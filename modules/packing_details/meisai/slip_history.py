"""明細の履歴 (全ライン・3年)

【何のためか】(現場の指定)
- あとから追う … 「このコイル(ロット・副番)はどの紙に載ったか」
- 数える     … 日ごと・PCごとに何枚・何本出したか

`明細出力` はロットを切り替えると消える(VBA の `DeleteLotSheets` と
同じ)。そこで出力と**同じまとまり**で、消えない履歴にも書く。

【どこに置くか】
    手元のDB(このPC)         明細履歴 / 明細履歴_副番   … 先に書く
      ↓ 送る(裏で。届かなければ残して、あとで送り直す)
    共有の 梱包明細履歴.sqlite3  明細履歴 / 明細履歴_副番   … 全ライン
      (梱包資材マスタと同じフォルダ。**このアプリが作る**)

**共有へ直接書かない。** 複数のラインPCが同じ SQLite に同時に書くと、
ネットワーク越しではロックが正しく効かず壊れることがある。手元に先に
書いておけば、ネットワークが切れても失わない。送るときは共有に鍵
(`梱包明細履歴.sqlite3.lock`)を置いて**1台ずつ**書く。

**二重にならない。** 1枚ごとに一意の番号(`送信ID`)を付け、共有では
その番号を主キーにする。送り直しても同じ行が増えない。送ったあとに
手元で変わる値(書き足し・紙面を開いた・作り直し)は、送り直すと共有の
値を書き換える。

【いつまで残すか】
`config.HISTORY_KEEP_YEARS`(3年)。それより古い行は、共有も手元も
**1日1回**整理する。`VACUUM` はしない(共有の上でファイルを丸ごと
作り直すことになる)── 消した分の場所は、次に書く行が使う。

【何を残すか】
    明細履歴       1行 = 紙1枚。ロット・No・出力日時・ロットの属性・
                   書き足し・右上の文字・紙面を開いた回数・状態・PC
    明細履歴_副番  1行 = コイル1本。送信ID・積んだ順・副番・丈・条・重量

【見る】
- 探す(`search`)… 期間・ロット番号・副番で紙を探して画面に並べる
- 紙面を作り直す(`load_slips` → `report.render_history`)… 履歴に残した
  中身のとおりに明細票を組み直す(読むだけ。履歴は書き換えない)
- CSV に書き出す(`export_csv`)… **ブラウザのダウンロードはしない**(現場の
  指定)── `config.export_dir()` にファイルを書き、その場所を出すだけ。
  **Excel などをこちらから開くこともしない**(開くのは使う人がすること。
  現場の指定)。UTF-8 の BOM 付きで、副番 `1-10` が日付に化けないように
  してある

共有に届かないときは、探す・作り直すは**この PC の分だけ**で行い、画面に
そう出す(書き出しは共有の全ライン分なので断る)。
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from . import app_config, config, db, shared_settings, source_db, strand_service
from .logging_utils import get_logger

log = get_logger("slip_history")

# 共有と手元で同じ列(この順で書く)
HEADER_COLUMNS: tuple[str, ...] = (
    "送信ID", "ロット番号", "No", "出力日時", "本数", "重量合計",
    "用途名", "製造材質", "製造調質", "製造板厚", "製造板幅",
    "設計_設備コース", "実績_設備コース", "受注番号", "包装仕様NO",
    "手入力サイズ", "手入力ロット番号", "右上の文字",
    "状態", "作り直し日時", "紙面を開いた回数", "最後に紙面を開いた日時",
    "出力PC", "ログインID", "版", "更新日時",
)
DETAIL_COLUMNS: tuple[str, ...] = ("送信ID", "順", "ロット番号", "副番", "丈", "条", "重量")
# 手元だけの列(送ったか・何回変わったか)
LOCAL_ONLY: tuple[str, ...] = ("要送信", "変更回数", "送信日時")
# 送ったあとに手元で変わりうる列。送り直したら共有の値を書き換える
MUTABLE: tuple[str, ...] = (
    "手入力サイズ", "手入力ロット番号", "右上の文字", "状態", "作り直し日時",
    "紙面を開いた回数", "最後に紙面を開いた日時", "更新日時",
)

STATE_VALID = "有効"
STATE_REPLACED = "作り直し"     # No指定で同じNoを出し直した(上書きされた)

LOCK_NAME = config.HISTORY_DB_NAME + ".lock"
SEND_BATCH = 500                # 1回に送る枚数の上限(溜まっていても共有を長く掴まない)
SHARED_BUSY_SEC = 10.0          # 共有の SQLite のロックを待つ上限

# 共有の表。手元の表(`schema.sql`)と**列を揃える**(試験で確かめる)
SHARED_DDL = """
CREATE TABLE IF NOT EXISTS 明細履歴 (
    送信ID            TEXT    PRIMARY KEY,
    ロット番号         TEXT    NOT NULL,
    No                INTEGER NOT NULL,
    出力日時           TEXT    NOT NULL,
    本数              INTEGER NOT NULL DEFAULT 0,
    重量合計           REAL    NOT NULL DEFAULT 0,
    用途名            TEXT    NOT NULL DEFAULT '',
    製造材質           TEXT    NOT NULL DEFAULT '',
    製造調質           TEXT    NOT NULL DEFAULT '',
    製造板厚           REAL    NOT NULL DEFAULT 0,
    製造板幅           REAL    NOT NULL DEFAULT 0,
    設計_設備コース     TEXT    NOT NULL DEFAULT '',
    実績_設備コース     TEXT    NOT NULL DEFAULT '',
    受注番号           TEXT    NOT NULL DEFAULT '',
    包装仕様NO         TEXT    NOT NULL DEFAULT '',
    手入力サイズ        TEXT    NOT NULL DEFAULT '',
    手入力ロット番号     TEXT    NOT NULL DEFAULT '',
    右上の文字          TEXT    NOT NULL DEFAULT '',
    状態              TEXT    NOT NULL DEFAULT '有効',
    作り直し日時        TEXT    NOT NULL DEFAULT '',
    紙面を開いた回数     INTEGER NOT NULL DEFAULT 0,
    最後に紙面を開いた日時 TEXT   NOT NULL DEFAULT '',
    出力PC            TEXT    NOT NULL DEFAULT '',
    ログインID         TEXT    NOT NULL DEFAULT '',
    版                TEXT    NOT NULL DEFAULT '',
    更新日時           TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rireki_lot ON 明細履歴(ロット番号);
CREATE INDEX IF NOT EXISTS idx_rireki_date ON 明細履歴(出力日時);
CREATE TABLE IF NOT EXISTS 明細履歴_副番 (
    送信ID     TEXT    NOT NULL,
    順         INTEGER NOT NULL,
    ロット番号  TEXT    NOT NULL,
    副番       TEXT    NOT NULL,
    丈         INTEGER NOT NULL DEFAULT 0,
    条         INTEGER NOT NULL DEFAULT 0,
    重量       REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (送信ID, 順)
);
CREATE INDEX IF NOT EXISTS idx_rireki_fuban ON 明細履歴_副番(ロット番号, 副番);
CREATE TABLE IF NOT EXISTS 整理記録 (
    項目  TEXT PRIMARY KEY,
    値    TEXT NOT NULL DEFAULT ''
);
"""


class HistoryError(RuntimeError):
    """履歴を読めない・書き出せない。画面にそのまま出せる文言を持つ。"""


def _now() -> str:
    return db.now_db_string()


def cutoff(today: Optional[date] = None) -> str:
    """これより前(未満)の出力日時は整理する。`HISTORY_KEEP_YEARS` 年前の今日。"""
    today = today or date.today()
    years = config.HISTORY_KEEP_YEARS
    try:
        start = today.replace(year=today.year - years)
    except ValueError:                          # 2月29日
        start = today.replace(year=today.year - years, day=28)
    return start.strftime("%Y-%m-%d 00:00:00")


def history_path() -> Path:
    """共有の履歴ファイル。梱包資材マスタと同じフォルダ。"""
    return shared_settings.shared_dir() / config.HISTORY_DB_NAME


# ==================================================================
# 手元に書く(出力・書き足し・紙面を開いた・作り直し)
# ==================================================================
def _coils(keys: Sequence[str], weights: Sequence[int]) -> list[tuple[int, str, int, int, float]]:
    """(順, 副番, 丈, 条, 重量)。読めない副番は丈・条・重量を0にして残す
    (紙の行と同じ ── VBA `WriteData` は読めないキーでも行を進める)。"""
    out = []
    for index, key in enumerate(keys, start=1):
        try:
            jou, strand = strand_service.parse_key(key)
        except ValueError:
            out.append((index, key, 0, 0, 0.0))
            continue
        weight = float(weights[jou - 1]) if 1 <= jou <= len(weights) else 0.0
        out.append((index, key, jou, strand, weight))
    return out


def record_output(conn: sqlite3.Connection, *, lot_no: str, seq_no: int,
                  keys: Sequence[str], weights: Sequence[int],
                  lot: Any = None, orders: Iterable[Any] = ()) -> str:
    """出力した1枚を手元の履歴に書く。**出力と同じまとまりの中で呼ぶこと。**

    `lot` / `orders` は画面に出ているロット情報(`lot_repo.LotInfo` /
    `OrderInfo`)。無ければ空で残す(試験・復元直後など)。
    戻り値は送信ID(`明細出力.履歴ID` に入れる)。
    """
    history_id = uuid.uuid4().hex
    coils = _coils(keys, weights)
    orders = list(orders or ())
    now = _now()

    def attr(name: str, default: Any = "") -> Any:
        return getattr(lot, name, default) if lot is not None else default

    values = {
        "送信ID": history_id, "ロット番号": lot_no, "No": int(seq_no), "出力日時": now,
        "本数": len(coils), "重量合計": sum(c[4] for c in coils),
        "用途名": attr("yoto_name"), "製造材質": attr("zaishitsu"),
        "製造調質": attr("choshitsu"),
        "製造板厚": float(attr("seizou_thickness", 0.0) or 0.0),
        "製造板幅": float(attr("seizou_width", 0.0) or 0.0),
        "設計_設備コース": attr("sekkei_course"), "実績_設備コース": attr("jisseki_course"),
        "受注番号": ",".join(o.order_no for o in orders if getattr(o, "order_no", "")),
        "包装仕様NO": ",".join(o.spec_no for o in orders if getattr(o, "spec_no", "")),
        "出力PC": shared_settings.pc_name(), "ログインID": shared_settings.login_id(),
        "版": app_config.version(), "更新日時": now,
    }
    cols = list(values)
    db.write(conn,
             f"INSERT INTO 明細履歴 ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
             [values[c] for c in cols], name="明細の履歴")
    for order, key, jou, strand, weight in coils:
        db.write(conn,
                 "INSERT INTO 明細履歴_副番 (送信ID, 順, ロット番号, 副番, 丈, 条, 重量)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (history_id, order, lot_no, key, jou, strand, weight), name="明細の履歴")
    return history_id


def _touch(conn: sqlite3.Connection, ids: Sequence[str], sets: str,
           params: Sequence[Any], *, name: str) -> int:
    """手元の履歴を書き換えて、**送り直す印**を付ける。"""
    ids = [i for i in ids if i]
    if not ids:
        return 0
    marks = ", ".join("?" for _ in ids)
    return db.write(
        conn,
        f"UPDATE 明細履歴 SET {sets}, 更新日時 = ?, 要送信 = 1,"
        f" 変更回数 = 変更回数 + 1 WHERE 送信ID IN ({marks})",
        [*params, _now(), *ids], name=name)


def mark_replaced(conn: sqlite3.Connection, history_id: str) -> None:
    """No指定で同じNoを出し直した。**古い紙の履歴は消さず、印を付ける。**"""
    _touch(conn, [history_id], "状態 = ?, 作り直し日時 = ?",
           (STATE_REPLACED, _now()), name="明細の履歴(作り直し)")


def update_hand(conn: sqlite3.Connection, history_id: str, size: str, lotno: str) -> None:
    """紙面で書き足したサイズ・LOTNO。"""
    _touch(conn, [history_id], "手入力サイズ = ?, 手入力ロット番号 = ?",
           (size, lotno), name="明細の履歴(書き足し)")


def note_opened(conn: sqlite3.Connection, history_ids: Sequence[str], qa: str) -> None:
    """紙面を開いた(刷るために開いた)。**そのとき刷られる右上の文字も残す。**"""
    _touch(conn, list(history_ids),
           "紙面を開いた回数 = 紙面を開いた回数 + 1, 最後に紙面を開いた日時 = ?,"
           " 右上の文字 = ?", (_now(), qa), name="明細の履歴(紙面)")


# ==================================================================
# 共有へ送る
# ==================================================================
@dataclass
class SendResult:
    sent: int = 0                   # 今回送った枚数
    pending: int = 0                # まだ送れていない枚数(送ったあとの残り)
    error: str = ""                 # 送れなかった理由
    purged_shared: int = 0          # 共有で整理した枚数(3年より前)
    purged_local: int = 0           # 手元で整理した枚数
    busy: bool = False              # ほかの送りが走っていた

    @property
    def ok(self) -> bool:
        return not self.error


_send_lock = threading.Lock()
_status: dict[str, str] = {"last_sent_at": "", "last_try_at": "", "last_error": ""}
_local_purged_on: Optional[str] = None
# 今日もう共有を整理したか。**送るものが無く整理も済んでいれば共有に触らない**
# (5分ごとに全ラインが共有の鍵を取りに行くのは無駄)
_shared_purged_on: Optional[str] = None


def _pending(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT {', '.join(HEADER_COLUMNS)}, 変更回数 FROM 明細履歴"
        " WHERE 要送信 = 1 ORDER BY 出力日時, 送信ID LIMIT ?", (limit,)).fetchall()


def pending_count(conn: sqlite3.Connection) -> int:
    """この PC にまだ送っていない紙の枚数(手元のDBだけを見る。共有には触らない)。"""
    return int(conn.execute("SELECT COUNT(*) FROM 明細履歴 WHERE 要送信 = 1").fetchone()[0])


def _open_shared(path: Path) -> sqlite3.Connection:
    """共有の履歴を書けるように開く。**無ければ作る**(表もここで作る)。

    DELETE で置く(WAL だと共有の上では読めない・書けない)。表があっても
    列が足りなければ足す(`_repair_shape`)。
    """
    conn = sqlite3.connect(str(path), timeout=SHARED_BUSY_SEC)
    try:
        conn.row_factory = sqlite3.Row
        mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(mode).lower() != "delete":
            raise sqlite3.OperationalError(f"journal_mode が {mode} のままです")
        # 表 → 足りない列 → 索引の順。**索引を先に作ると**、列の足りない表
        # (手で作った表)で「その列が無い」と落ちて、足す前に止まる
        statements = [x.strip() for x in SHARED_DDL.split(";") if x.strip()]
        for statement in statements:
            if not statement.startswith("CREATE INDEX"):
                conn.execute(statement)
        _repair_shape(conn)
        for statement in statements:
            if statement.startswith("CREATE INDEX"):
                conn.execute(statement)
        conn.commit()
        return conn
    except BaseException:
        conn.close()
        raise


# ---- 表の形をそろえる --------------------------------------------------
# 共有の表と、1行を決める列。**この列の組で同じ行を増やさない**(送り直し)
SHARED_KEYS: dict[str, tuple[str, ...]] = {
    "明細履歴": ("送信ID",),
    "明細履歴_副番": ("送信ID", "順"),
    "整理記録": ("項目",),
}
_shape_cache: dict[str, list[tuple]] = {}


def _shared_shape() -> dict[str, list[tuple]]:
    """`SHARED_DDL` の表ごとの列(`PRAGMA table_info` の形)。**DDL が唯一の出どころ。**"""
    if not _shape_cache:
        mem = sqlite3.connect(":memory:")
        try:
            mem.executescript(SHARED_DDL)
            for table in SHARED_KEYS:
                _shape_cache[table] = mem.execute(f"PRAGMA table_info({table})").fetchall()
        finally:
            mem.close()
    return _shape_cache


def _has_unique(hconn: sqlite3.Connection, table: str, key: Sequence[str]) -> bool:
    """`key` の列の組が一意になっているか(主キー・一意の索引)。"""
    for index in hconn.execute(f"PRAGMA index_list({table})").fetchall():
        unique = bool(index[2])
        partial = len(index) > 4 and bool(index[4])
        if not unique or partial:
            continue
        cols = {r[2] for r in hconn.execute(
            f"PRAGMA index_info({source_db.quote_identifier(index[1])})")}
        if cols == set(key):
            return True
    return False


def _shape_problems(hconn: sqlite3.Connection) -> list[str]:
    """足りないもの(表・列・一意)。そろっていれば空。**読むだけ。**"""
    problems = []
    for table, spec in _shared_shape().items():
        have = {r[1] for r in hconn.execute(f"PRAGMA table_info({table})")}
        if not have:
            problems.append(f"表 {table}")
            continue
        problems += [f"{table}.{c[1]}" for c in spec if c[1] not in have]
        if not _has_unique(hconn, table, SHARED_KEYS[table]):
            problems.append(f"{table} の一意({'・'.join(SHARED_KEYS[table])})")
    return problems


def _repair_shape(hconn: sqlite3.Connection) -> list[str]:
    """表はあるのに列が足りない(手で作った表・古い版が作った表)なら足す。

    **足せない列(主キー・既定値の無い必須の列)が無いときは断る。** 形の違う
    表へ黙って書くと、送り直しで同じ紙が増えたり、読めない行が混ざる。
    主キーが違う表には一意の索引を足す(送り直しで同じ紙を増やさない)。
    """
    fixed: list[str] = []
    for table, spec in _shared_shape().items():
        have = {r[1] for r in hconn.execute(f"PRAGMA table_info({table})")}
        for _cid, name, ctype, notnull, default, pk in spec:
            if name in have:
                continue
            if default is None and (notnull or pk):
                raise HistoryError(
                    f"共有の {config.HISTORY_DB_NAME} の表「{table}」に列「{name}」が"
                    "ありません。この列は後から足せないので、この表の名前を変えるか"
                    "消してください（次に送るときに、正しい形で作り直します）。")
            clause = f"ALTER TABLE {table} ADD COLUMN {name} {ctype}"
            if default is not None:
                clause += f"{' NOT NULL' if notnull else ''} DEFAULT {default}"
            hconn.execute(clause)
            fixed.append(f"{table}.{name}")
        key = SHARED_KEYS[table]
        if not _has_unique(hconn, table, key):
            try:
                hconn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}"
                              f" ON {table}({', '.join(key)})")
            except sqlite3.IntegrityError as exc:
                raise HistoryError(
                    f"共有の表「{table}」に同じ{'・'.join(key)}の行が複数あります"
                    f"（{exc}）。この表の名前を変えるか消してください。") from exc
            fixed.append(f"{table} の一意({'・'.join(key)})")
    if fixed:
        hconn.commit()
        log.warning("共有の履歴の表に足りないものを足しました: %s", ", ".join(fixed))
    return fixed


# 形がそろっていると確かめたときのファイルの様子(更新時刻・大きさ)。**変わって
# いなければ開いて確かめ直さない**(探す・様子を見るたびに共有を開くぶん待たせない)
_shape_ok: dict[str, tuple[int, int]] = {}


def _file_stamp(path: Path) -> Optional[tuple[int, int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def ensure_shared() -> bool:
    """共有の履歴(ファイル・表・列)が**無ければ作る。** 作った・足したら True。

    そろっていれば読むだけで帰る(鍵も取らない)。作るときは送りと同じ鍵を
    取って**1台ずつ**作る。探す・書き出す・マスタ管理で見る前に呼ぶ ──
    「まだ無い」で止めずに、その場で作る(現場の指定)。
    """
    folder = shared_settings.shared_dir()
    path = folder / config.HISTORY_DB_NAME
    stamp = _file_stamp(path)
    if stamp is not None:
        if _shape_ok.get(str(path)) == stamp:
            return False
        try:
            hconn = _read_only(path)
            try:
                if not _shape_problems(hconn):
                    _shape_ok[str(path)] = stamp
                    return False
            finally:
                hconn.close()
        except sqlite3.Error:
            pass                    # 開けない・読めない → 下で開き直して確かめる
    elif not folder.is_dir():
        raise HistoryError(f"共有フォルダに届きません: {folder}")
    created = not path.exists()
    with shared_settings.folder_lock(folder, LOCK_NAME):
        hconn = _open_shared(path)
        hconn.close()
    log.info("共有の履歴を%sました: %s", "作り" if created else "そろえ", path)
    return True


def _read_shared(read: Callable[[sqlite3.Connection], Any]) -> Any:
    """共有の履歴を読む。**ファイル・表・列が無ければ作ってから読む**(`ensure_shared`)。

    確かめた控え(`_shape_ok`)が古くて表・列が無かったら、控えを捨てて
    作り直してから**1度だけ**読み直す。
    """
    for attempt in (1, 2):
        ensure_shared()
        hconn = _read_only(history_path())
        try:
            return read(hconn)
        except sqlite3.OperationalError as exc:
            if attempt == 2 or not re.search(r"no such (table|column)", str(exc)):
                raise
            _shape_ok.pop(str(history_path()), None)
        finally:
            hconn.close()
    raise AssertionError("unreachable")                 # pragma: no cover


def _purge_shared(hconn: sqlite3.Connection, today: str) -> int:
    """共有を整理する(3年より前)。**1日1回**(`整理記録` に日付を残す)。"""
    row = hconn.execute("SELECT 値 FROM 整理記録 WHERE 項目 = '最後に整理した日'").fetchone()
    if row is not None and row[0] == today:
        return 0
    limit = cutoff()
    old = "SELECT 送信ID FROM 明細履歴 WHERE 出力日時 < ?"
    hconn.execute(f"DELETE FROM 明細履歴_副番 WHERE 送信ID IN ({old})", (limit,))
    count = hconn.execute("DELETE FROM 明細履歴 WHERE 出力日時 < ?", (limit,)).rowcount
    hconn.execute("INSERT OR REPLACE INTO 整理記録 (項目, 値) VALUES ('最後に整理した日', ?)",
                  (today,))
    if count:
        log.info("共有の履歴を整理しました(%s より前): %s枚", limit[:10], count)
    return count


def _purge_local(conn: sqlite3.Connection, today: str) -> int:
    """手元を整理する(3年より前)。送れていない古い行も落とす(共有でも整理される)。"""
    global _local_purged_on
    if _local_purged_on == today:
        return 0
    limit = cutoff()
    with db.transaction(conn, name="手元の履歴の整理"):
        stale = conn.execute("SELECT COUNT(*) FROM 明細履歴 WHERE 出力日時 < ? AND 要送信 = 1",
                             (limit,)).fetchone()[0]
        db.write(conn, "DELETE FROM 明細履歴_副番 WHERE 送信ID IN"
                       " (SELECT 送信ID FROM 明細履歴 WHERE 出力日時 < ?)", (limit,),
                 name="手元の履歴の整理")
        count = db.write(conn, "DELETE FROM 明細履歴 WHERE 出力日時 < ?", (limit,),
                         name="手元の履歴の整理")
    if stale:
        log.warning("%s年より前で送れていなかった履歴を%s枚整理しました",
                    config.HISTORY_KEEP_YEARS, stale)
    _local_purged_on = today
    return count


def send_pending(conn: Optional[sqlite3.Connection] = None, *,
                 limit: int = SEND_BATCH) -> SendResult:
    """手元の送れていない履歴を共有へ送る。**例外にしない**(理由は結果に)。

    1プロセスで同時に走るのは1本だけ(走っていれば `busy` で帰る)。
    """
    if not _send_lock.acquire(blocking=False):
        return SendResult(busy=True)
    try:
        if conn is None:
            with db.connect() as own:
                return _send(own, limit)
        return _send(conn, limit)
    finally:
        _send_lock.release()


def _send(conn: sqlite3.Connection, limit: int) -> SendResult:
    global _shared_purged_on
    result = SendResult()
    today = date.today().isoformat()
    rows = _pending(conn, limit)
    if not rows and _shared_purged_on == today:
        return result
    _status["last_try_at"] = _now()
    try:
        folder = shared_settings.shared_dir()
        if not folder.is_dir():
            raise HistoryError(f"共有フォルダに届きません: {folder}")
        ids = [r["送信ID"] for r in rows]
        details: list[sqlite3.Row] = []
        if ids:
            marks = ", ".join("?" for _ in ids)
            details = conn.execute(
                f"SELECT {', '.join(DETAIL_COLUMNS)} FROM 明細履歴_副番"
                f" WHERE 送信ID IN ({marks})", ids).fetchall()
        with shared_settings.folder_lock(folder, LOCK_NAME):
            hconn = _open_shared(folder / config.HISTORY_DB_NAME)
            try:
                hconn.execute("BEGIN IMMEDIATE")
                _write_rows(hconn, rows, details)
                result.purged_shared = _purge_shared(hconn, today)
                hconn.commit()
            except BaseException:
                hconn.rollback()
                raise
            finally:
                hconn.close()
    except (HistoryError, shared_settings.SharedError, sqlite3.Error, OSError) as exc:
        result.error = str(exc)
        result.pending = pending_count(conn)
        if _status["last_error"] != result.error:
            log.warning("明細の履歴を共有へ送れませんでした(あとで送り直します): %s", exc)
        _status["last_error"] = result.error
        return result

    # **送ったあとに変わった行は、送れたことにしない。** 読んでから送るまでの
    # あいだに書き足しがあると、共有には古い値が入っている(次に送り直す)
    with db.transaction(conn, name="履歴を送った印"):
        for row in rows:
            db.write(conn, "UPDATE 明細履歴 SET 要送信 = 0, 送信日時 = ?"
                           " WHERE 送信ID = ? AND 変更回数 = ?",
                     (_now(), row["送信ID"], row["変更回数"]), name="履歴を送った印")
    result.sent = len(rows)
    _shared_purged_on = today
    result.purged_local = _purge_local(conn, today)
    result.pending = pending_count(conn)
    if result.sent:
        log.info("明細の履歴を共有へ送りました: %s枚(残り%s枚)", result.sent, result.pending)
    if _status["last_error"]:
        log.info("明細の履歴を共有へ送れるようになりました")
    _status.update(last_error="", last_sent_at=_now())
    return result


def _write_rows(hconn: sqlite3.Connection, rows: Sequence[sqlite3.Row],
                details: Sequence[sqlite3.Row]) -> None:
    """共有へ書く。**同じ送信IDは増やさない**(あれば変わる列だけ書き換える)。

    UPSERT(`ON CONFLICT`)を使わないのは、ラインPCの Python に入っている
    SQLite が古いと通らないため。「無ければ足す」→「変わる列を書き換える」
    の2文にする。
    """
    cols = ", ".join(HEADER_COLUMNS)
    marks = ", ".join("?" for _ in HEADER_COLUMNS)
    sets = ", ".join(f"{c} = ?" for c in MUTABLE)
    for row in rows:
        hconn.execute(f"INSERT OR IGNORE INTO 明細履歴 ({cols}) VALUES ({marks})",
                      [row[c] for c in HEADER_COLUMNS])
        hconn.execute(f"UPDATE 明細履歴 SET {sets} WHERE 送信ID = ?",
                      [*(row[c] for c in MUTABLE), row["送信ID"]])
    dcols = ", ".join(DETAIL_COLUMNS)
    dmarks = ", ".join("?" for _ in DETAIL_COLUMNS)
    for d in details:
        hconn.execute(f"INSERT OR IGNORE INTO 明細履歴_副番 ({dcols}) VALUES ({dmarks})",
                      [d[c] for c in DETAIL_COLUMNS])


# ---- 裏で送る ---------------------------------------------------------
_wake = threading.Event()
_worker: Optional[threading.Thread] = None
_stop = threading.Event()


def start_background(interval: float = config.HISTORY_SEND_INTERVAL_SEC) -> None:
    """裏で送り続ける。**出力したら `kick` ですぐ送る**、届かなければ `interval` ごとに。"""
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    _stop.clear()

    def loop() -> None:
        while not _stop.is_set():
            try:
                send_pending()
            except Exception:                           # noqa: BLE001
                log.exception("明細の履歴の送りで思わぬ失敗(続けます)")
            _wake.wait(interval)
            _wake.clear()

    _worker = threading.Thread(target=loop, name="history-send", daemon=True)
    _worker.start()


def kick() -> None:
    """いま送る(裏で)。裏の送り手が居なければ何もしない(試験・検証用の起動)。"""
    _wake.set()


def stop_background() -> None:
    """試験用。"""
    global _worker
    _stop.set()
    _wake.set()
    if _worker is not None:
        _worker.join(5)
    _worker = None
    _wake.clear()


# ==================================================================
# 様子(画面の「履歴」に出す)
# ==================================================================
def _read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(source_db.to_uri(path) + "?mode=ro", uri=True,
                           timeout=SHARED_BUSY_SEC)
    conn.row_factory = sqlite3.Row
    return conn


def _bounded(fn, timeout: float):
    """`fn()` を `timeout` 秒まで待つ(共有が応えないとき画面を待たせない)。"""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:                    # noqa: BLE001
            box["error"] = exc

    thread = threading.Thread(target=run, name="history-read", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise HistoryError(f"共有フォルダが{timeout:g}秒以内に応えません")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def status(conn: sqlite3.Connection, *, timeout: float = 3.0) -> dict[str, Any]:
    """手元の送り残しと、共有の履歴の件数・期間。**パスは画面に出す。**"""
    path = history_path()
    out: dict[str, Any] = {
        "pending": pending_count(conn),
        "local_count": int(conn.execute("SELECT COUNT(*) FROM 明細履歴").fetchone()[0]),
        "last_sent_at": _status["last_sent_at"],
        "last_try_at": _status["last_try_at"],
        "last_error": _status["last_error"],
        "shared_path": str(path),
        "keep_years": config.HISTORY_KEEP_YEARS,
        "cutoff": cutoff()[:10],
        "export_dir": str(config.export_dir()),
        "shared_count": None, "shared_coils": None,
        "shared_oldest": "", "shared_newest": "", "shared_problem": "",
    }

    def read(hconn: sqlite3.Connection) -> dict[str, Any]:
        row = hconn.execute("SELECT COUNT(*), MIN(出力日時), MAX(出力日時)"
                            " FROM 明細履歴").fetchone()
        coils = hconn.execute("SELECT COUNT(*) FROM 明細履歴_副番").fetchone()[0]
        return {"shared_count": int(row[0]), "shared_coils": int(coils),
                "shared_oldest": row[1] or "", "shared_newest": row[2] or ""}

    try:
        out.update(_bounded(lambda: _read_shared(read), timeout))
    except (HistoryError, shared_settings.SharedError, sqlite3.Error, OSError) as exc:
        out["shared_problem"] = str(exc)
    return out


# ==================================================================
# 探す・紙面を作り直すために読む
# ==================================================================
SEARCH_LIMIT = 200              # 画面に並べる上限。**超えた分は数で言う**
LOAD_LIMIT = 20                 # 一度に作り直す紙の上限

# 探すときに画面へ返す列
SEARCH_COLUMNS: tuple[str, ...] = (
    "送信ID", "ロット番号", "No", "出力日時", "本数", "重量合計", "状態",
    "作り直し日時", "手入力サイズ", "手入力ロット番号", "紙面を開いた回数", "出力PC",
)


@dataclass
class SearchResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0                  # 条件に合う枚数(並べたのは `SEARCH_LIMIT` まで)
    source: str = "shared"          # shared = 全ライン / local = この PC の分だけ
    note: str = ""


def _conditions(date_from: str, date_to: str, lot_no: str,
                fuban: str) -> tuple[str, list[Any]]:
    start = _check_date(date_from, "はじめ")
    end = _check_date(date_to, "おわり")
    if start > end:
        raise HistoryError("はじめの日付が、おわりの日付より後になっています。")
    where = ["h.出力日時 >= ?", "h.出力日時 < ?"]
    params: list[Any] = [f"{start:%Y-%m-%d} 00:00:00",
                         f"{end + timedelta(days=1):%Y-%m-%d} 00:00:00"]
    lot_no = str(lot_no or "").strip().upper()
    if lot_no:
        where.append("h.ロット番号 LIKE ? ESCAPE '\\'")
        params.append(lot_no.replace("\\", "\\\\").replace("%", "\\%")
                      .replace("_", "\\_") + "%")
    fuban = re.sub(r"\s+", "", str(fuban or ""))
    if fuban:
        where.append("EXISTS (SELECT 1 FROM 明細履歴_副番 d"
                     " WHERE d.送信ID = h.送信ID AND d.副番 = ?)")
        params.append(fuban)
    return " AND ".join(where), params


def _search_in(hconn: sqlite3.Connection, where: str, params: list[Any],
               limit: int) -> tuple[list[dict[str, Any]], int]:
    total = int(hconn.execute(f"SELECT COUNT(*) FROM 明細履歴 h WHERE {where}",
                              params).fetchone()[0])
    cols = ", ".join(f"h.{c}" for c in SEARCH_COLUMNS)
    rows = hconn.execute(
        f"SELECT {cols}, (SELECT group_concat(副番, ' ') FROM"
        " (SELECT 副番 FROM 明細履歴_副番 d WHERE d.送信ID = h.送信ID ORDER BY d.順))"
        f" AS 副番の並び FROM 明細履歴 h WHERE {where}"
        " ORDER BY h.出力日時 DESC, h.ロット番号, h.No LIMIT ?", [*params, limit]).fetchall()
    return [dict(r) for r in rows], total


def search(conn: sqlite3.Connection, date_from: str, date_to: str, lot_no: str = "",
           fuban: str = "", *, limit: int = SEARCH_LIMIT,
           timeout: float = 5.0) -> SearchResult:
    """紙を探す(新しい順)。**共有(全ライン)から。** 届かなければこの PC の分から。

    `fuban` は副番(例 1-10)。**このコイルが載った紙**を探すときに使う。
    """
    where, params = _conditions(date_from, date_to, lot_no, fuban)

    def read_shared():
        return _read_shared(lambda hconn: _search_in(hconn, where, params, limit))

    result = SearchResult()
    try:
        result.rows, result.total = _bounded(read_shared, timeout)
    except (HistoryError, shared_settings.SharedError, sqlite3.Error, OSError) as exc:
        send_pending(conn)                  # 手元の分は最新にしておく(届かないなら何もしない)
        result.rows, result.total = _search_in(conn, where, params, limit)
        result.source = "local"
        result.note = (f"共有に届かないため、この PC の分だけを探しました({exc})。")
    if result.total > len(result.rows):
        result.note = (result.note + f"合う紙は{result.total}枚あります。新しい"
                       f"{len(result.rows)}枚だけ並べています(期間・ロット番号で絞ってください)。")
    return result


def load_slips(conn: sqlite3.Connection, ids: Sequence[str], *,
               timeout: float = 5.0) -> tuple[list[dict[str, Any]], str]:
    """紙面を作り直すために、履歴の紙を読む。`(紙, 出どころ)`。

    紙は `{"head": {...}, "coils": [{順, 副番, 丈, 条, 重量}, ...]}` で、
    `ids` の順に並べる。**見つからない紙があれば HistoryError**(黙って
    抜かすと、頼んだ枚数と刷った枚数が食い違う)。
    """
    ids = [i for i in dict.fromkeys(str(i).strip() for i in ids) if i]
    if not ids:
        raise HistoryError("作り直す紙を選んでください。")
    if len(ids) > LOAD_LIMIT:
        raise HistoryError(f"一度に作り直せるのは{LOAD_LIMIT}枚までです(いま{len(ids)}枚)。")
    marks = ", ".join("?" for _ in ids)

    def read_from(hconn: sqlite3.Connection) -> list[dict[str, Any]]:
        heads = {r["送信ID"]: dict(r) for r in hconn.execute(
            f"SELECT {', '.join(HEADER_COLUMNS)} FROM 明細履歴 WHERE 送信ID IN ({marks})",
            ids)}
        coils: dict[str, list[dict[str, Any]]] = {}
        for r in hconn.execute(
                f"SELECT {', '.join(DETAIL_COLUMNS)} FROM 明細履歴_副番"
                f" WHERE 送信ID IN ({marks}) ORDER BY 送信ID, 順", ids):
            coils.setdefault(r["送信ID"], []).append(dict(r))
        return [{"head": heads[i], "coils": coils.get(i, [])} for i in ids if i in heads]

    source = "shared"
    try:
        slips = _bounded(lambda: _read_shared(read_from), timeout)
    except (HistoryError, shared_settings.SharedError, sqlite3.Error, OSError):
        slips, source = [], "local"
    if len(slips) < len(ids):
        # 共有に無い(まだ送れていない・届かない)分は、この PC の手元から
        have = {s["head"]["送信ID"] for s in slips}
        local = {s["head"]["送信ID"]: s for s in read_from(conn)}
        slips = [next((s for s in slips if s["head"]["送信ID"] == i), None) or local.get(i)
                 for i in ids]
        if any(s is None for s in slips):
            raise HistoryError("履歴に見つからない紙があります(3年より前で整理されたか、"
                               "ほかの PC の分で共有に届かない)。")
        if have != set(ids):
            source = "local" if not have else "mixed"
    return slips, source


# ==================================================================
# CSV に書き出す
# ==================================================================
# Excel に**勝手に読み替えさせない**文字列:
#   1-10 / 1/10     … 日付になる(副番が「1月10日」に化ける)
#   0123            … 先頭の0が消える
#   = + - @ で始まる … 式として動く(打ち込まれた文字で、意図しない式を
#                     走らせない)
# こういう値は `="…"`(文字列を返す式)で書く。Excel では元の文字のまま出る
_EXCEL_MISREAD = re.compile(r"^(\d+\s*[-/]\s*\d+|0\d+|[=+\-@])")

# 日時の列は Excel に日時として読ませたい(絞り込み・並べ替えのため)
_DATETIME_COLUMNS = {"出力日時", "作り直し日時", "最後に紙面を開いた日時", "更新日時"}

DETAIL_CSV_COLUMNS: tuple[str, ...] = (
    "出力日時", "ロット番号", "No", "順", "副番", "丈", "条", "重量",
    "状態", "本数", "重量合計", "用途名", "製造材質", "製造調質", "製造板厚", "製造板幅",
    "設計_設備コース", "実績_設備コース", "受注番号", "包装仕様NO",
    "手入力サイズ", "手入力ロット番号", "右上の文字",
    "紙面を開いた回数", "最後に紙面を開いた日時", "作り直し日時",
    "出力PC", "ログインID", "版", "送信ID",
)
SUMMARY_CSV_COLUMNS: tuple[str, ...] = (
    "出力日", "出力PC", "枚数", "本数", "重量合計", "作り直した枚数",
)


def excel_cell(column: str, value: Any) -> Any:
    """CSV の1マス。**Excel で開いたとき元の文字のまま出る**ようにする。"""
    if value is None:
        return ""
    if not isinstance(value, str) or column in _DATETIME_COLUMNS:
        return value
    if _EXCEL_MISREAD.match(value):
        return '="' + value.replace('"', '""') + '"'
    return value


@dataclass
class ExportResult:
    detail_file: str = ""
    summary_file: str = ""
    detail_rows: int = 0            # コイルの行数
    slips: int = 0                  # 紙の枚数(作り直しを含む)
    summary_rows: int = 0
    folder: str = ""
    note: str = ""


def _check_date(text: str, name: str) -> date:
    try:
        return datetime.strptime(str(text or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HistoryError(f"{name}の日付が読めません(例: 2026-09-01)。") from None


def export_csv(date_from: str, date_to: str, lot_no: str = "", fuban: str = "", *,
               folder: Optional[Path] = None) -> ExportResult:
    """共有の履歴(全ライン)を CSV に書き出す。**2つのファイル**:

        明細 … 1行 = コイル1本(紙の情報つき)。「このコイルはどの紙か」
        集計 … 1行 = 出力日 × PC。枚数・本数・重量合計(作り直しは数えない)

    期間は出力日で `date_from` 〜 `date_to`(両端を含む)。`lot_no` は
    ロット番号の前方一致、`fuban` はその副番が載った紙だけ(空ならすべて)。
    **書くだけ。** ファイルを開くのは使う人がすること(こちらからは開かない)。
    """
    where_sql, params = _conditions(date_from, date_to, lot_no, fuban)
    start = _check_date(date_from, "はじめ")
    end = _check_date(date_to, "おわり")
    path = history_path()
    try:
        ensure_shared()                 # 無ければ作る(見出しだけのファイルになる)
    except (shared_settings.SharedError, sqlite3.Error, OSError) as exc:
        raise HistoryError(f"共有の履歴を開けません: {exc}") from exc

    folder = folder or config.export_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HistoryError(f"書き出し先のフォルダを作れません: {folder} ({exc})") from exc
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    span = f"{start:%Y%m%d}-{end:%Y%m%d}"
    detail_path = folder / f"梱包明細履歴_明細_{span}_{stamp}.csv"
    summary_path = folder / f"梱包明細履歴_集計_{span}_{stamp}.csv"

    head_cols = [c for c in DETAIL_CSV_COLUMNS if c not in DETAIL_COLUMNS or c in ("ロット番号", "送信ID")]
    select = ", ".join(
        [f"h.{c}" for c in head_cols] + ["d.順", "d.副番", "d.丈", "d.条", "d.重量"])
    sql = (f"SELECT {select} FROM 明細履歴 h JOIN 明細履歴_副番 d ON d.送信ID = h.送信ID"
           f" WHERE {where_sql} ORDER BY h.出力日時, h.ロット番号, h.No, d.順")

    result = ExportResult(folder=str(folder))
    summary: dict[tuple[str, str], dict[str, Any]] = {}
    slips: set[str] = set()
    try:
        hconn = _read_only(path)
    except sqlite3.Error as exc:
        raise HistoryError(f"共有の履歴を開けません: {exc}") from exc
    try:
        with open(detail_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(DETAIL_CSV_COLUMNS)
            for row in hconn.execute(sql, params):
                record = dict(row)
                writer.writerow([excel_cell(c, record.get(c)) for c in DETAIL_CSV_COLUMNS])
                result.detail_rows += 1
                sid = record["送信ID"]
                key = (str(record["出力日時"])[:10], str(record["出力PC"]))
                bucket = summary.setdefault(key, {"slips": set(), "replaced": set(),
                                                  "coils": 0, "weight": 0.0})
                if record["状態"] == STATE_REPLACED:
                    bucket["replaced"].add(sid)
                else:
                    if sid not in bucket["slips"]:
                        bucket["slips"].add(sid)
                    bucket["coils"] += 1
                    bucket["weight"] += float(record["重量"] or 0)
                slips.add(sid)
    except sqlite3.Error as exc:
        raise HistoryError(f"共有の履歴を読めません: {exc}") from exc
    except OSError as exc:
        raise HistoryError(f"CSV を書けません: {exc}") from exc
    finally:
        hconn.close()

    try:
        with open(summary_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(SUMMARY_CSV_COLUMNS)
            for (day, pc), b in sorted(summary.items()):
                writer.writerow([day, excel_cell("出力PC", pc), len(b["slips"]), b["coils"],
                                 round(b["weight"], 1), len(b["replaced"])])
                result.summary_rows += 1
    except OSError as exc:
        raise HistoryError(f"CSV を書けません: {exc}") from exc

    result.detail_file = detail_path.name
    result.summary_file = summary_path.name
    result.slips = len(slips)
    if not result.detail_rows:
        result.note = "その期間の履歴はありませんでした(見出しだけのファイルです)。"
    log.info("明細の履歴を書き出しました: %s(%s行) / %s", detail_path, result.detail_rows,
             summary_path.name)
    return result


def reset_for_tests() -> None:
    """試験用。"""
    global _local_purged_on, _shared_purged_on
    _local_purged_on = None
    _shared_purged_on = None
    _shape_ok.clear()
    _status.update(last_sent_at="", last_try_at="", last_error="")
