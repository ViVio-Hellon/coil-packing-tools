# -*- coding: utf-8 -*-
"""マスタ管理。資材マスタの中身をこの画面から直す。

`ViVio-Hellon/python-web-tools` の「マスタ管理」を移植したもの。
考え方はそのままだが、**このツールには取り込み工程が無い**ので
そのぶん単純になっている。

    移植元: 画面 → 共有ファイルへ書く → その表だけ取り込み直す → 手元が追いつく
    こちら: 画面 → 共有ファイルへ書く → キャッシュを捨てる → 次の読みで反映

手元にマスタの写しを持たないため、「同じ事実が 2 か所にある」問題が
そもそも起きない。書き先は **共有の資材マスタただ 1 つ**。

引き継いだ考え方
    1. **行は取り込み元の言葉で指す。**
       管理番号は業務の列であって行の名前ではない（重複も欠番もありうる）。
       sqlite の暗黙の ``rowid`` を ``__行`` という名前
       （業務の列と衝突しない名前）で持ち回る。
       ここを踏むと「直したつもりが別の行だった」になる。
    2. **直せない表は隠さず、理由を出す。**
       隠すと画面が壊れて見える。
    3. **全部は出さない。** 出さなかった分は必ず数で言う。
       黙って切ると「これで全部だ」と読めてしまう。

このツール固有なのは :data:`MANAGED` と :data:`VIEW_ONLY_WHY` の 2 か所。
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: 一度に出す行数。**全部は出さない。**
ROW_LIMIT = 200

#: 空欄なら今の日時を入れる列。手で打たせる意味が無く、打ち間違いだけが増える
STAMP_COLUMNS = ("更新日時", "登録日時")

#: 行を指す隠しの列名。取り込み元の ``rowid`` をこの名前で持ち回る。
#: **業務の列と衝突しない名前**にする（全角を混ぜてあるのはそのため）
ROW_KEY = "__行"


# ---------------------------------------------------------------- 断り
REFUSE_BAD_VALUE = "bad_value"          # 入力の形が違う
REFUSE_NOT_ALLOWED = "not_allowed"      # 許されていない
REFUSE_NO_ROW = "no_row"                # 見ていた行がもう無い
REFUSE_NOT_EDITABLE = "not_editable"    # その表は直せない
REFUSE_NO_SOURCE = "no_source"          # 書き先が無い
REFUSE_WRITE_FAILED = "write_failed"    # 書けなかった
REFUSE_NOT_CREATABLE = "not_creatable"  # 行を増やせない表

#: 断りの種別を HTTP に写す
#:   400 … 入力の形が違う。**サーバーの状態は動いていない**
#:   403 … 許されていない
#:   409 … 先を越された（見ていた行がもう無い）
#:   422 … 業務としての断り
STATUS = {
    REFUSE_BAD_VALUE: 400,
    REFUSE_NOT_ALLOWED: 403,
    REFUSE_NO_ROW: 409,
    REFUSE_NOT_EDITABLE: 422,
    REFUSE_NO_SOURCE: 422,
    REFUSE_WRITE_FAILED: 422,
    REFUSE_NOT_CREATABLE: 422,
}


class Refused(Exception):
    """断り。種別と、人に見せる言葉を持つ。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message

    @property
    def status(self) -> int:
        return STATUS.get(self.kind, 422)


# ---------------------------------------------------------------- 対象
@dataclass(frozen=True)
class Managed:
    """直せる表 1 つ。"""
    table: str
    label: str
    mark: str
    note: str
    #: 直してよい列。空なら全列。
    #:
    #: **鍵になる列を触らせない**ために使う（管理番号＝台紙の番号など）。
    #: 指定があるとき、その表は **行数が決まっている** とみなし、
    #: 行の追加・削除も断る（台紙は 14 枚で固定、といった表）。
    editable: Tuple[str, ...] = ()


#: **このツールが使う表だけを出す。**
#:
#: 共有の資材マスタには班員名簿・アクセス権限・取引先名など
#: このツールが読まない表も入っている。それらは業務上ここで直すものでは
#: なく、個人情報も含むため、一覧にも出さない。
MANAGED: Tuple[Managed, ...] = (
    Managed("資材重量", "資材重量", "資", "梱包資材の単位質量と係数"),
    # 管理番号は台紙の番号（obIdx）そのもので、画面の丈1/丈2 と対応する。
    # ここを触ると別の台紙に紐付くので、直せるのはシート名と型番だけ。
    Managed("ラベル台紙一覧", "ラベル台紙一覧", "台",
            "台紙ごとのシート名と型番",
            editable=("シート名", "型番")),
)
BY_TABLE: Dict[str, Managed] = {m.table: m for m in MANAGED}

#: 直せない表と、その理由。**出さないのではなく、理由を出す。**
VIEW_ONLY_WHY: Dict[str, str] = {}
DEFAULT_VIEW_ONLY = "このツールが直す表ではありません。中身の確認だけできます。"


def editable_columns(table: str, cols: List[str]) -> List[str]:
    """その表で直してよい列。指定が無ければ全列。"""
    m = BY_TABLE.get(table)
    if not m or not m.editable:
        return list(cols)
    return [c for c in cols if c in m.editable]


def fixed_rows(table: str) -> bool:
    """行数が決まっている表か（追加・削除を断る）。"""
    m = BY_TABLE.get(table)
    return bool(m and m.editable)


def view_only_why(table: str) -> str:
    """その表を直せない理由。直せる表なら空。"""
    if table in BY_TABLE:
        return ""
    return VIEW_ONLY_WHY.get(table, DEFAULT_VIEW_ONLY)


# ---------------------------------------------------------------- 書き先
def source_path(repo) -> str:
    """書き先（共有の資材マスタ）。SQLite のときだけ直せる。"""
    return str(getattr(repo, "db_path", "") or "")


def editable_why_not(repo) -> str:
    """直せない理由。直せるなら空文字。"""
    path = source_path(repo)
    if not path:
        return ("資材マスタが SQLite(.sqlite3) に設定されていません。"
                "設定の「パス設定」で資材マスタ(SQLite)を指定してください。")
    if not os.path.exists(path):
        return "資材マスタが見つかりません: %s" % path
    if not os.access(path, os.W_OK):
        return ("資材マスタが読み取り専用です: %s\n"
                "共有フォルダーの権限を確認してください。" % path)
    return ""


def can_edit(repo) -> bool:
    return not editable_why_not(repo)


# ---------------------------------------------------------------- 読み
def _connect(path: str, write: bool = False) -> sqlite3.Connection:
    if write:
        conn = sqlite3.connect(path, timeout=15)
    else:
        uri = "file:%s?mode=ro" % path.replace("?", "%3f").replace("#", "%23")
        conn = sqlite3.connect(uri, uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _quote(name: str) -> str:
    """識別子を安全に囲む。表名・列名は必ずこれを通す。"""
    return '"%s"' % str(name).replace('"', '""')


def _check_table(table: str) -> str:
    t = str(table or "").strip()
    if t not in BY_TABLE and t not in VIEW_ONLY_WHY:
        raise Refused(REFUSE_NOT_EDITABLE, "扱わない表です: %s" % (t or "(未指定)"))
    return t


def columns(conn: sqlite3.Connection, table: str) -> List[str]:
    rows = conn.execute("PRAGMA table_info(%s)" % _quote(table)).fetchall()
    if not rows:
        raise Refused(REFUSE_NO_SOURCE, "表が見つかりません: %s" % table)
    return [r["name"] for r in rows]


def tables(repo) -> List[dict]:
    """扱う表の一覧。直せない表も理由付きで出す。"""
    path = source_path(repo)
    out = []
    exists = set()
    if path and os.path.exists(path):
        try:
            conn = _connect(path)
            try:
                exists = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                conn.close()
        except sqlite3.Error as exc:
            log.warning("資材マスタの表一覧を取れません: %s", exc)
    for m in MANAGED:
        out.append({"table": m.table, "label": m.label, "mark": m.mark,
                    "note": m.note, "viewOnlyWhy": "",
                    "missing": bool(exists) and m.table not in exists})
    for t, why in VIEW_ONLY_WHY.items():
        out.append({"table": t, "label": t, "mark": "参", "note": "",
                    "viewOnlyWhy": why,
                    "missing": bool(exists) and t not in exists})
    return out


def _order(cols: List[str], sort: str, sort_dir: str) -> Tuple[str, str]:
    """見出しクリックの並び替え(梱包明細・資材計算のマスタ管理と同じ決まり)。

    **列名は実在する列だけ**を許す ── 列名をそのまま ``ORDER BY`` に組み込むため。
    実在しない列(表を切り替えた・列が消えた)を指したら、押していないのと同じ
    (取り込み元の並び = rowid)へ静かに戻す。同じ値が並ぶと表示順が揺れるので、
    rowid で確定させる。

    **数字は数の大きさで並べる**(``common/sql_sort.py``)。資材マスタは数字も文字で
    持っていることがあり、そのままだと 10 が 9 より前へ来る。空はいちばん後ろ。
    """
    from common import sql_sort
    if sort and sort in cols:
        direction = "DESC" if sort_dir == "desc" else "ASC"
        return (" ORDER BY %s, rowid ASC" % sql_sort.order_terms(_quote(sort), direction),
                sort)
    return " ORDER BY rowid", ""


def page(repo, table: str, keyword: str = "",
         limit: int = ROW_LIMIT, sort: str = "", sort_dir: str = "asc") -> dict:
    """表の中身。**全部は出さず、出さなかった分は数で言う。**

    ``sort`` を渡すと、その列で並べ替える(見出しを押したとき。``sort_dir`` は
    ``asc`` / ``desc``)。全部を並べ替えてから先頭 ``limit`` 件を出すので、
    出していない行も含めた並びになる。
    """
    table = _check_table(table)
    path = source_path(repo)
    if not path or not os.path.exists(path):
        raise Refused(REFUSE_NO_SOURCE, editable_why_not(repo)
                      or "資材マスタが見つかりません")

    conn = _connect(path)
    try:
        cols = columns(conn, table)
        sel = ", ".join([("rowid AS %s" % _quote(ROW_KEY))]
                        + [_quote(c) for c in cols])
        total = conn.execute(
            "SELECT COUNT(*) FROM %s" % _quote(table)).fetchone()[0]

        kw = str(keyword or "").strip()
        args: List[Any] = []
        where = ""
        if kw:
            # どの列でもよいので当たれば出す（現場は列を意識しない）
            parts = ["CAST(%s AS TEXT) LIKE ?" % _quote(c) for c in cols]
            where = " WHERE " + " OR ".join(parts)
            args = ["%%%s%%" % kw] * len(cols)

        order, used = _order(cols, str(sort or ""), str(sort_dir or "asc"))
        shown = conn.execute(
            "SELECT %s FROM %s%s%s LIMIT ?"
            % (sel, _quote(table), where, order), args + [int(limit)]).fetchall()
        matched = total
        if kw:
            matched = conn.execute(
                "SELECT COUNT(*) FROM %s%s" % (_quote(table), where),
                args).fetchone()[0]
    finally:
        conn.close()

    rows = [{k: ("" if r[k] is None else str(r[k])) for k in r.keys()}
            for r in shown]
    note = ""
    if matched > len(rows):
        note = ("%d 件のうち %d 件を出しています。"
                "絞り込むと残りが見られます。" % (matched, len(rows)))
    return {
        "table": table,
        "columns": cols,
        "editableColumns": editable_columns(table, cols),
        "fixedRows": fixed_rows(table),
        "rowKey": ROW_KEY,
        "rows": rows,
        "sort": used,
        "sortDir": ("desc" if sort_dir == "desc" else "asc") if used else "asc",
        "total": total,
        "matched": matched,
        "note": note,
        "viewOnlyWhy": view_only_why(table),
        "editable": can_edit(repo) and not view_only_why(table),
        "whyNot": editable_why_not(repo),
        "sourcePath": path,
    }


# ---------------------------------------------------------------- 書き
def _backup(path: str) -> str:
    """書く前に控えを取る。共有のマスタを壊したら戻せないため。"""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = "%s.%s.bak" % (path, stamp)
    try:
        shutil.copy2(path, dst)
        return dst
    except OSError as exc:
        log.warning("控えを取れません: %s", exc)
        return ""


def _stamped(values: Dict[str, Any], cols: List[str]) -> Dict[str, Any]:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    out = dict(values)
    for c in STAMP_COLUMNS:
        if c in cols and not str(out.get(c, "") or "").strip():
            out[c] = now
    return out


def _require_editable(repo, table: str) -> str:
    why = editable_why_not(repo)
    if why:
        raise Refused(REFUSE_NOT_ALLOWED, why)
    if view_only_why(table):
        raise Refused(REFUSE_NOT_EDITABLE, view_only_why(table))
    return source_path(repo)


def save_row(repo, table: str, row_id: Optional[int],
             values: Dict[str, Any]) -> dict:
    """1 行を直す（``row_id`` が無ければ新しく足す）。

    **書き先は共有マスタただ 1 つ。** 書いたらキャッシュを捨て、
    次の読みで画面と計算に反映される。
    """
    table = _check_table(table)
    path = _require_editable(repo, table)
    if not isinstance(values, dict) or not values:
        raise Refused(REFUSE_BAD_VALUE, "入力がありません")

    if row_id is None and fixed_rows(table):
        raise Refused(REFUSE_NOT_CREATABLE,
                      "この表は行を増やせません（並びが決まっています）。"
                      "ある行の中身だけ直せます。")

    conn = _connect(path, write=True)
    backup = ""
    try:
        cols = columns(conn, table)
        unknown = [k for k in values if k not in cols and k != ROW_KEY]
        if unknown:
            raise Refused(REFUSE_BAD_VALUE,
                          "この表に無い列です: %s" % "、".join(unknown))
        allowed = set(editable_columns(table, cols))
        locked = [k for k in values if k in cols and k not in allowed]
        if locked:
            raise Refused(REFUSE_NOT_ALLOWED,
                          "この列は直せません: %s" % "、".join(locked))
        data = _stamped({k: v for k, v in values.items()
                         if k in cols and k in allowed}, cols)
        if not data:
            raise Refused(REFUSE_BAD_VALUE, "書き込む内容がありません")

        backup = _backup(path)
        if row_id is None:
            names = list(data)
            sql = ("INSERT INTO %s (%s) VALUES (%s)"
                   % (_quote(table), ", ".join(_quote(c) for c in names),
                      ", ".join("?" * len(names))))
            cur = conn.execute(sql, [data[c] for c in names])
            new_id = cur.lastrowid
            action = "追加"
        else:
            hit = conn.execute("SELECT rowid FROM %s WHERE rowid=?"
                               % _quote(table), (int(row_id),)).fetchone()
            if hit is None:
                raise Refused(REFUSE_NO_ROW,
                              "その行はもうありません（誰かが消した可能性があります）。"
                              "読み直してください。")
            names = list(data)
            sql = ("UPDATE %s SET %s WHERE rowid=?"
                   % (_quote(table),
                      ", ".join("%s=?" % _quote(c) for c in names)))
            conn.execute(sql, [data[c] for c in names] + [int(row_id)])
            new_id = int(row_id)
            action = "更新"
        conn.commit()
    except Refused:
        conn.rollback()
        conn.close()
        raise
    except sqlite3.Error as exc:
        conn.rollback()
        conn.close()
        raise Refused(REFUSE_WRITE_FAILED, "書き込めませんでした: %s" % exc)
    else:
        conn.close()

    _refresh(repo)
    log.info("資材マスタを%sしました: %s rowid=%s", action, table, new_id)
    return {"ok": True, "action": action, "rowId": new_id,
            "backup": backup,
            "message": "%s しました。次の計算から反映されます。" % action}


def delete_row(repo, table: str, row_id: int) -> dict:
    table = _check_table(table)
    path = _require_editable(repo, table)
    if fixed_rows(table):
        raise Refused(REFUSE_NOT_CREATABLE,
                      "この表は行を消せません（並びが決まっています）。")
    conn = _connect(path, write=True)
    try:
        hit = conn.execute("SELECT rowid FROM %s WHERE rowid=?"
                           % _quote(table), (int(row_id),)).fetchone()
        if hit is None:
            raise Refused(REFUSE_NO_ROW, "その行はもうありません。読み直してください。")
        backup = _backup(path)
        conn.execute("DELETE FROM %s WHERE rowid=?" % _quote(table),
                     (int(row_id),))
        conn.commit()
    except Refused:
        conn.rollback()
        conn.close()
        raise
    except sqlite3.Error as exc:
        conn.rollback()
        conn.close()
        raise Refused(REFUSE_WRITE_FAILED, "消せませんでした: %s" % exc)
    else:
        conn.close()

    _refresh(repo)
    log.info("資材マスタから 1 行消しました: %s rowid=%s", table, row_id)
    return {"ok": True, "action": "削除", "backup": backup,
            "message": "削除しました。次の計算から反映されます。"}


def _refresh(repo) -> None:
    """書いたらキャッシュを捨てる。次の読みで取り直す。

    移植元の「その表だけ取り込み直す」に当たる工程。
    こちらは手元に写しを持たないので、捨てるだけでよい。
    """
    try:
        repo.clear_cache()
    except Exception as exc:                          # noqa: BLE001
        log.warning("資材マスタのキャッシュを捨てられません: %s", exc)
