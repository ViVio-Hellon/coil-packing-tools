"""作業の記録 (VBA の隠しシート「副番履歴」の移植)

    VBA                              -> Python
    ---------------------------------------------------------------
    mSheet.RegisterFuban               -> register_fuban()
    mSheet.IsDuplicate                  -> is_duplicate()
    mSheet.ClearFubanList                -> clear_fuban()
    mSheet.SaveSnapshot                   -> save_snapshot()
    mSheet.LoadSnapshot                    -> load_snapshot()
    mSheet.DeleteSnapshot                   -> delete_snapshot()
    mSheet.ClearAllSnapshots                 -> clear_snapshots()
    mSheet.Get/SetCurrentLotNo                -> current_lot() / set_current_lot()
    mSheet.Get/Set/IncrementSeq                -> current_seq() / set_seq() / next_seq()
    mSheet.IsPrinted / SetPrinted               -> is_printed() / set_printed()

【なぜ都度DBへ書くのか】
VBA は作業の途中経過をシートへ書いていたので、ブックを閉じても
残った。Web版は**ブラウザのタブを閉じるとプロセスが終わる**
(`idle_exit`)ので、メモリに置くと消える。VBA が
`SaveCurrentSnapshot` を呼んでいた3つの契機 ── 条番号表示・廃棄の
増減・出力 ── で、同じようにここへ書く。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from . import config, db
from .logging_utils import get_logger

log = get_logger("history_repo")


# ==================================================================
# 副番履歴 (RULE-07)
# ==================================================================
def fuban_key(lot_no: str, strand_key: str) -> str:
    """副番キー。VBA `lotNo & "-" & slotKeys(i)`。

        N3250Q0 + "2-15" -> "N3250Q0-2-15"
    """
    return f"{lot_no}-{strand_key}"


def is_duplicate(conn: sqlite3.Connection, key: str) -> bool:
    """その副番はもう出力済みか (VBA `IsDuplicate`)。"""
    row = db.fetch_one(conn, "SELECT 1 FROM 副番履歴 WHERE 副番キー = ? LIMIT 1",
                       (key,), caller_name="history_repo.is_duplicate")
    return row is not None


def duplicates(conn: sqlite3.Connection, lot_no: str,
               strand_keys: list[str]) -> list[str]:
    """これから出す副番のうち、すでに出力済みのもの。

    VBA `cmdKettei_Click` は1件ずつ `IsDuplicate` を呼んで警告文を
    組み立てていた。まとめて1回で引く。**並びは積み上げ順のまま**
    (警告に出る順序が画面と一致する)。
    """
    if not strand_keys:
        return []
    keys = [fuban_key(lot_no, k) for k in strand_keys]
    marks = ", ".join("?" for _ in keys)
    rows = db.fetch_all(conn,
                        f"SELECT 副番キー FROM 副番履歴 WHERE 副番キー IN ({marks})",
                        tuple(keys), caller_name="history_repo.duplicates") or []
    found = {r["副番キー"] for r in rows}
    return [k for k, full in zip(strand_keys, keys) if full in found]


def register_fuban(conn: sqlite3.Connection, key: str) -> None:
    """副番を履歴へ足す (VBA `RegisterFuban`)。

    VBA は `If Not IsDuplicate Then RegisterFuban` と呼んでいるので、
    **すでにあるものは足さない。** ここでは確かめずに書き、呼び出し側
    (`meisai_service`)が同じ判断をする ── VBAの呼び出し形をそのまま
    残したいため。
    """
    db.write(conn, "INSERT INTO 副番履歴 (副番キー, 登録日時) VALUES (?, ?)",
             (key, db.now_db_string()), name="副番の登録")


def clear_fuban(conn: sqlite3.Connection) -> None:
    """副番履歴を全件消す (VBA `ClearFubanList`)。

    **連番はリセットしない。** VBA も「ロット切替時は ResetSeq を
    明示的に呼ぶ」とコメントしている。印刷済フラグだけ1に戻す。
    """
    with db.transaction(conn, name="副番履歴のクリア"):
        db.write(conn, "DELETE FROM 副番履歴", name="副番履歴のクリア")
        db.set_state(conn, 印刷済=1)
    log.info("clear_fuban: 副番履歴をクリアしました")


def fuban_count(conn: sqlite3.Connection) -> int:
    row = db.fetch_one(conn, "SELECT COUNT(*) AS c FROM 副番履歴",
                       caller_name="history_repo.fuban_count")
    return int(row["c"]) if row else 0


# ==================================================================
# スナップショット
# ==================================================================
@dataclass
class Snapshot:
    """作業の途中経過 (VBA 副番履歴シートの G〜K列)。"""

    lot_no: str
    zen_kotei: int
    jou_su: int
    weights: list[int]
    strands: list[int]
    used: list[str]
    discarded: list[str]


def _join_ints(values: list[int]) -> str:
    return ",".join(str(v) for v in values)


def _split_ints(text: str) -> list[int]:
    """カンマ区切りを整数の並びに戻す。**読めない要素は0にする。**

    VBA は `CLng(Val(Trim(...)))` で読んでいて、空や数字以外は0に
    なる。例外にすると、途中まで書けたスナップショットを開いただけで
    画面が出なくなる。
    """
    out: list[int] = []
    for part in (text or "").split(","):
        part = part.strip()
        if part == "":
            continue
        try:
            out.append(int(float(part)))
        except ValueError:
            out.append(0)
    return out


def _split_keys(text: str) -> list[str]:
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def save_snapshot(conn: sqlite3.Connection, snap: Snapshot) -> None:
    """作業の途中経過を書く (VBA `SaveSnapshot`)。

    同じロットがあれば上書き、無ければ足す。**直近5ロットだけ**残す
    (VBA はシートのG〜K列という物理的な制約から5件だった。同じ数に
    そろえてある ── 古い作業が延々と復元候補に残らないほうが、
    現場の見え方としてもVBAと一致する)。
    """
    # **1つのまとまりにする。** 別々に書くと、片方だけ通って
    # 「5件に収まっていない」「書けたのに載っていない」が起きうる
    with db.transaction(conn, name="途中経過の保存"):
        db.write(
            conn,
            "INSERT INTO 明細スナップショット"
            " (ロット番号, 前工程実績数, 丈数, 重量, 条数, 使用済, 廃棄, 更新日時)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(ロット番号) DO UPDATE SET"
            " 前工程実績数=excluded.前工程実績数, 丈数=excluded.丈数,"
            " 重量=excluded.重量, 条数=excluded.条数, 使用済=excluded.使用済,"
            " 廃棄=excluded.廃棄, 更新日時=excluded.更新日時",
            (snap.lot_no, snap.zen_kotei, snap.jou_su,
             _join_ints(snap.weights), _join_ints(snap.strands),
             ",".join(snap.used), ",".join(snap.discarded), db.now_db_string()),
            name="途中経過の保存")
        _trim_snapshots(conn)
    log.debug("save_snapshot: %s 丈数=%s 使用済=%s 廃棄=%s",
              snap.lot_no, snap.jou_su, len(snap.used), len(snap.discarded))


def _trim_snapshots(conn: sqlite3.Connection) -> None:
    """古いものから落として `SNAPSHOT_KEEP` 件に収める。"""
    db.write(
        conn,
        "DELETE FROM 明細スナップショット WHERE ロット番号 NOT IN ("
        " SELECT ロット番号 FROM 明細スナップショット"
        " ORDER BY 更新日時 DESC, ロット番号 DESC LIMIT ?)",
        (config.SNAPSHOT_KEEP,), name="古い途中経過の整理")


def load_snapshot(conn: sqlite3.Connection, lot_no: str) -> Optional[Snapshot]:
    """そのロットの途中経過 (VBA `LoadSnapshot`)。無ければ None。"""
    row = db.fetch_one(conn,
                       "SELECT * FROM 明細スナップショット WHERE ロット番号 = ?",
                       (lot_no,), caller_name="history_repo.load_snapshot")
    if row is None:
        log.debug("load_snapshot: スナップショットなし %s", lot_no)
        return None
    return Snapshot(
        lot_no=lot_no,
        zen_kotei=int(row["前工程実績数"] or 0),
        jou_su=int(row["丈数"] or 0),
        weights=_split_ints(row["重量"]),
        strands=_split_ints(row["条数"]),
        used=_split_keys(row["使用済"]),
        discarded=_split_keys(row["廃棄"]),
    )


def delete_snapshot(conn: sqlite3.Connection, lot_no: str) -> None:
    """1ロット分を消す (VBA `DeleteSnapshot`)。"""
    db.write(conn, "DELETE FROM 明細スナップショット WHERE ロット番号 = ?",
             (lot_no,), name="途中経過の削除")


def clear_snapshots(conn: sqlite3.Connection) -> None:
    """全消去 (VBA `ClearAllSnapshots`)。副番履歴クリアと連動する。"""
    db.write(conn, "DELETE FROM 明細スナップショット",
             name="途中経過の全消去")


def snapshot_lots(conn: sqlite3.Connection) -> list[str]:
    """途中経過が残っているロット。新しい順。"""
    rows = db.fetch_all(
        conn, "SELECT ロット番号 FROM 明細スナップショット"
              " ORDER BY 更新日時 DESC, ロット番号 DESC",
        caller_name="history_repo.snapshot_lots") or []
    return [r["ロット番号"] for r in rows]


# ==================================================================
# 出力状態 (RULE-08)
# ==================================================================
def current_lot(conn: sqlite3.Connection) -> str:
    """いま開いているロット (VBA `GetCurrentLotNo`)。"""
    return str(db.state_row(conn)["現在ロット番号"] or "")


def set_current_lot(conn: sqlite3.Connection, lot_no: str) -> None:
    db.set_state(conn, 現在ロット番号=lot_no)


def current_seq(conn: sqlite3.Connection) -> int:
    """いまの連番 (VBA `GetCurrentSeq`)。"""
    return int(db.state_row(conn)["現在連番"] or 0)


def set_seq(conn: sqlite3.Connection, value: int) -> None:
    db.set_state(conn, 現在連番=int(value))


def next_seq(conn: sqlite3.Connection) -> int:
    """連番を1つ進めて返す (VBA `IncrementSeq` → `GetCurrentSeq`)。

    **読んで書くまでを1つにする。** 同じプロセス内でも要求が重なると
    同じ番号を2枚に付けてしまう。`UPDATE ... RETURNING` が使える
    SQLite なら1文で済むが、古い版でも動くよう条件付き更新にする。
    """
    while True:
        now = current_seq(conn)
        # `db.write` は書けなければ例外。**黙って番号を飛ばさない**
        if db.write(
                conn,
                "UPDATE 明細出力状態 SET 現在連番 = ? WHERE ID = 1 AND 現在連番 = ?",
                (now + 1, now), name="連番の採番") == 1:
            return now + 1
        # 誰かが先に採った。読み直してもう一度
        log.info("next_seq: 連番が競合したので採り直します (%s)", now)


def reset_seq(conn: sqlite3.Connection) -> None:
    """連番を0に戻す (VBA `ResetSeq`)。**ロット切替時だけ**呼ぶ。"""
    db.set_state(conn, 現在連番=0)
    log.info("reset_seq: 連番をリセットしました")


def is_printed(conn: sqlite3.Connection) -> bool:
    """直近の出力は印刷済みか (VBA `IsPrinted`)。"""
    return int(db.state_row(conn)["印刷済"] or 0) == 1


def set_printed(conn: sqlite3.Connection, printed: bool) -> None:
    db.set_state(conn, 印刷済=1 if printed else 0)
