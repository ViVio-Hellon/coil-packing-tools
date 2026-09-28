"""マスタ管理(見るだけ) ── 共有フォルダの sqlite3 の中身を見る

梱包資材総合ツールのマスタ管理のうち、**「中身を見る」ところだけ**を移した
(総合ツールの `packaging_tool/master_browse.py`)。直すところ(行を足す・
書き換える・消す・表を作る)は移していない ── 梱包資材マスタを直すのは
総合ツールの役目で、誰が直せるか(アクセス権限・管理者パスワード)の決まりも
あちらにある。ここから書けると、その決まりを通らずに共有のマスタが変わる。

見るファイル(どちらも共有フォルダ `shared_settings.shared_dir()` にある):

    梱包資材マスタ.sqlite3  … 総合ツールが直す。このアプリは表「梱包明細打ち出し」
                              (紙面の右上の文字)を読む
    梱包明細履歴.sqlite3    … このアプリが書く(明細の履歴、全ライン・3年)。
                              総合ツールのマスタ管理では開けない(あちらが開くのは
                              梱包資材マスタとパレット閾値マスタの2つだけ)。
                              **ファイルや表が無ければ、開いたときに作る**
                              (`slip_history.ensure_shared`)

【総合ツールと同じ決まり】
- **表は全部出す。** 中身を確かめられることと、書き換えられることは別の話
- 行は `ROW_LIMIT` 件ずつ。**出さなかった分は必ず数で言う**(黙って切ると
  「これで全部だ」と読めてしまう)
- 絞り込みは**どの列でも**(どの列に何が入っているかを覚えていなくても引ける)
- 見出しで並べ替え。列名は**実在する列だけ**を許す(`ORDER BY` に組み込むため)
- 取り込み元の `rowid` を `__行` という名前で持ち回る

【このアプリで足したこと】
- **待つのは `READ_WAIT_SEC` まで。** 共有が応えないとき、画面を待たせ続けない。
  応えないまま残っている読み込みがあれば、重ねずに断る
- 履歴は**新しいものを上**に出す(3年ぶん溜まるので、古い順では目当てが出ない)。
  明細履歴は出力日時の新しい順(`DEFAULT_SORT`)
- 絞り込みの `%` `_` は文字どおりに探す(総合ツールでは「何でも」の印になる)
- CSV に書き出す(`export_csv`)。**ブラウザのダウンロードはしない。こちらから
  Excel なども開かない**(現場の指定)── 書き出し先に書いて、場所を出すだけ
"""
from __future__ import annotations

import csv
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import config, shared_settings, slip_history, source_db
from .logging_utils import get_logger

log = get_logger("master_browse")

# 一度に出す行数。**全部は出さない**(総合ツールと同じ)
ROW_LIMIT = 200

# 行を指す隠しの列名。取り込み元の `rowid` をこの名前で持ち回る。
# **業務の列と衝突しない名前**にする(総合ツールと同じ)
ROW_KEY = "__行"

# 共有の応えを待つ上限。応えないときに画面を待たせ続けない
READ_WAIT_SEC = 15.0

# Excel で開ける行数(見出しの1行を除く)。これより多い分は書かずに数で言う
EXCEL_MAX_ROWS = 1_048_575

MASTER = "master"
HISTORY = "history"


class BrowseError(RuntimeError):
    """読めない・書き出せない。画面にそのまま出せる文言を持つ。"""


# 置き場所を直せば読める断り(届かない・ファイルが無い)に添える、直し方
FIX_HINT = ("置き場所は「設定」→「置き場所・取り込み」の「梱包資材マスタのフォルダ（共有）」"
            "で変えられます（変えるとすぐ、ここもその場所を見ます）。")


class PlaceError(BrowseError):
    """置き場所の話(共有フォルダに届かない・ファイルが無い)。**直し方を添える。**"""

    def __init__(self, message: str) -> None:
        super().__init__(f"{message}。{FIX_HINT}")


@dataclass(frozen=True)
class Source:
    """見るファイル1つ。"""

    key: str
    label: str
    note: str
    # 既定の並びを新しいもの(あとから入ったもの)が上にするか
    newest_first: bool


SOURCES: tuple[Source, ...] = (
    Source(MASTER, "梱包資材マスタ",
           "梱包資材総合ツールのマスタ管理で直す表です。ここでは中身を見るだけです。"
           "紙面の右上の文字（表「梱包明細打ち出し」）は、このアプリの「設定」から変えます。",
           newest_first=False),
    Source(HISTORY, "梱包明細履歴",
           "このアプリが書く明細の履歴です（全ライン・3年）。ここでは中身を見るだけです。"
           "紙を探して紙面を作り直すのは「履歴」からです。",
           newest_first=True),
)
BY_KEY: dict[str, Source] = {s.key: s for s in SOURCES}

# 表の説明。**押す前に、その先に何があるかを示す。** 並びもこの順を先にする
TABLE_NOTES: dict[tuple[str, str], str] = {
    (MASTER, shared_settings.MASTER_TABLE):
        "紙面の右上の文字（ID=1 の行）。このアプリが読みます",
    (HISTORY, "明細履歴"): "1行 = 紙1枚",
    (HISTORY, "明細履歴_副番"): "1行 = コイル1本（送信ID で紙とつながります）",
    (HISTORY, "整理記録"): f"{config.HISTORY_KEEP_YEARS}年より古い行を最後に整理した日",
}


# 既定の並びに使う列(その表で「新しさ」を表す列)。無い表は rowid(共有に入った順)。
# 明細履歴は何台ものラインPCがまとめて送るので、入った順は出した順と揃わない
DEFAULT_SORT: dict[tuple[str, str], str] = {
    (HISTORY, "明細履歴"): "出力日時",
}


@dataclass
class TableInfo:
    """一覧に出す表1つ。"""

    table: str
    rows: int                       # -1 = 数えられなかった
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "rows": self.rows, "note": self.note}


@dataclass
class View:
    """画面に返す、まるごとの状態。"""

    source: str
    label: str
    source_note: str
    folder: str = ""
    path: str = ""
    # ファイルとして読めない(見つからない・届かない・応えない)
    error: str = ""
    # 断りが置き場所の話なら "share"(画面は設定のその欄へ飛べるボタンを出す)
    fix: str = ""
    tables: list[TableInfo] = field(default_factory=list)
    table: str = ""
    table_note: str = ""
    query: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    # 出しきれなかった分(**黙って落とさない**)
    note: str = ""
    # いま並び替えている列。空なら既定の並び(`order_label`)
    sort: str = ""
    sort_dir: str = "asc"
    order_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "label": self.label,
                "source_note": self.source_note,
                "sources": [{"key": s.key, "label": s.label} for s in SOURCES],
                "folder": self.folder, "path": self.path, "error": self.error,
                "fix": self.fix,
                "tables": [t.to_dict() for t in self.tables],
                "table": self.table, "table_note": self.table_note,
                "query": self.query, "columns": self.columns, "rows": self.rows,
                "total": self.total, "shown": len(self.rows), "note": self.note,
                "sort": self.sort, "sort_dir": self.sort_dir,
                "order_label": self.order_label,
                "row_key": ROW_KEY, "limit": ROW_LIMIT}


def source_of(key: str) -> Source:
    """見るファイル。知らない名前なら先頭(梱包資材マスタ)。"""
    return BY_KEY.get(key or "", SOURCES[0])


# ==================================================================
# 共有を待つのは決めた時間まで
# ==================================================================
_guard = threading.Lock()
_running: list[tuple[threading.Thread, float]] = []


def _bounded(fn: Callable[[], Any], timeout: float) -> Any:
    """`fn()` を `timeout` 秒まで待つ。

    **応えないまま残っている読み込みがあれば、重ねずに断る。** 押すたびに
    応えない共有へ読み込みを積むと、裏で待つものが増えるだけになる。
    ふつうに重なっただけ(すぐ終わるもの)は断らない。
    """
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:                    # noqa: BLE001
            box["error"] = exc

    now = time.monotonic()
    with _guard:
        _running[:] = [(t, s) for t, s in _running if t.is_alive()]
        if any(now - started >= timeout for _, started in _running):
            raise BrowseError("前の読み込みが、まだ共有の応えを待っています。"
                              "少し待ってから、もう一度押してください。")
        thread = threading.Thread(target=run, name="master-browse", daemon=True)
        _running.append((thread, now))
        thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise BrowseError(f"共有フォルダが{timeout:g}秒以内に応えません。"
                          "ネットワークを確かめてから、もう一度押してください。")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ==================================================================
# 読む
# ==================================================================
def _find(source: Source, folder: Path) -> Path:
    """見るファイルの場所。**無ければ理由を言う**(届かないのか、無いのか)。"""
    if source.key == HISTORY:
        # **無ければ作る**(ファイル・表・足りない列)。「まだ無い」で止めない
        try:
            slip_history.ensure_shared()
        except slip_history.HistoryError as exc:
            if not folder.is_dir():
                raise PlaceError(f"共有フォルダに届きません: {folder}") from exc
            raise BrowseError(str(exc)) from exc
        except (shared_settings.SharedError, sqlite3.Error, OSError) as exc:
            raise BrowseError(f"共有の履歴を作れません: {exc}") from exc
        return folder / config.HISTORY_DB_NAME
    found = source_db.find(folder, config.MASTER_DB_NAME)
    if found is not None:
        return found
    if not folder.is_dir():
        raise PlaceError(f"共有フォルダに届きません: {folder}")
    raise PlaceError(f"{config.MASTER_DB_NAME} が見つかりません: {folder}")


def _open(path: Path) -> sqlite3.Connection:
    try:
        return source_db.open_read_only(path)
    except source_db.SourceError as exc:
        raise BrowseError(str(exc)) from exc


def _table_names(conn: sqlite3.Connection) -> list[str]:
    with source_db.identifiers_as_utf8(conn):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [str(r[0]) for r in rows]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    with source_db.identifiers_as_utf8(conn):
        rows = conn.execute(
            f"PRAGMA table_info({source_db.quote_identifier(table)})").fetchall()
    return [str(r[1]) for r in rows]


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {source_db.quote_identifier(table)}").fetchone()
        return int(row[0])
    except sqlite3.Error:
        # 1つ数えられなくても残りは出す(見えないより見えるほうがよい)
        return -1


def _ordered(source: Source, names: list[str]) -> list[str]:
    """表の並び。説明のある表(このアプリが使う表)が先、残りは名前順。"""
    known = [t for (key, t) in TABLE_NOTES if key == source.key and t in names]
    return known + [n for n in names if n not in known]


def _filter(names: list[str], query: str) -> tuple[str, dict[str, Any]]:
    """絞り込みの条件。**どの列でもいい**ので、全部の列を見る。

    `%` と `_` は文字どおりに探す(打った文字が「何でも」の印に化けない)。
    """
    text = (query or "").strip()
    if not text or not names:
        return "", {}
    pattern = "%" + re.sub(r"([\\%_])", r"\\\1", text) + "%"
    conds = " OR ".join(
        f"CAST({source_db.quote_identifier(n)} AS TEXT) LIKE :q ESCAPE '\\'"
        for n in names)
    return f" WHERE ({conds})", {"q": pattern}


def _order(names: list[str], sort: str, sort_dir: str, *,
           newest_first: bool, rowid: bool = True, default: str = "") -> tuple[str, str]:
    """見出しクリックの並び替え。

    `sort` が実在の列でなければ、押していないのと同じ(既定の並び)へ静かに
    戻す ── 列は取り込み元の都合で増減するので、もう無い列を指した並び替えを
    断ると「さっきまで押せたのに」が起きる(総合ツールと同じ)。
    既定の並びは `default` の列(あれば)、それから rowid。
    """
    if sort and sort in names:
        direction = "DESC" if sort_dir == "desc" else "ASC"
        # 同値が並ぶと表示順が揺れるので、rowid で確定させる
        tail = ", rowid ASC" if rowid else ""
        return f" ORDER BY {source_db.quote_identifier(sort)} {direction}{tail}", sort
    direction = "DESC" if newest_first else "ASC"
    keys = []
    if default and default in names:
        keys.append(f"{source_db.quote_identifier(default)} {direction}")
    if rowid:
        keys.append(f"rowid {direction}")
    return (" ORDER BY " + ", ".join(keys) if keys else ""), ""


def _cell(value: Any) -> Any:
    """画面へ返す1マス。JSON に載らない値(バイナリ・無限大)は文字にする。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"（バイナリ {len(bytes(value))} バイト）"
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _select(conn: sqlite3.Connection, table: str, where: str,
            order: tuple[str, str], params: dict[str, Any],
            limit: Optional[int]) -> tuple[sqlite3.Cursor, bool]:
    """行を引く。`order` は (rowid を使う並び, rowid を使わない並び)。

    **rowid の無い表**(WITHOUT ROWID)は、rowid を使わない並びで引き直す。
    """
    quoted = source_db.quote_identifier(table)
    tail = " LIMIT :limit" if limit is not None else ""
    args = {**params, **({"limit": limit} if limit is not None else {})}
    try:
        return conn.execute(f'SELECT rowid AS "{ROW_KEY}", * FROM {quoted}{where}'
                            f"{order[0]}{tail}", args), True
    except sqlite3.OperationalError as exc:
        if "rowid" not in str(exc):
            raise
    return conn.execute(f"SELECT * FROM {quoted}{where}{order[1]}{tail}", args), False


def _orders(names: list[str], sort: str, sort_dir: str, *, newest_first: bool,
            default: str) -> tuple[tuple[str, str], str]:
    """(rowid を使う並び, rowid を使わない並び) と、並べ替えに使った列。"""
    with_rowid, used = _order(names, sort, sort_dir, newest_first=newest_first,
                              default=default)
    plain, _used = _order(names, sort, sort_dir, newest_first=newest_first,
                          rowid=False, default=default)
    return (with_rowid, plain), used


def _order_label(source: Source, sort: str, sort_dir: str, default: str = "") -> str:
    if sort:
        return f"{sort} の{'大きい' if sort_dir == 'desc' else '小さい'}順"
    if default:
        return f"{default} の{'新しい' if source.newest_first else '古い'}順"
    return "入った順（新しいものが上）" if source.newest_first else "入った順"


def browse(source_key: str = "", table: str = "", *, query: str = "",
           sort: str = "", sort_dir: str = "asc", limit: int = ROW_LIMIT,
           timeout: float = READ_WAIT_SEC) -> View:
    """見るファイルの表の一覧と、選んだ表の1ページ。**共有から直に読む。**

    写しではなく元を読むのは、このアプリが書いた履歴や、総合ツールで直した
    マスタが**本当に入ったか**をここで確かめられるようにするため。
    """
    source = source_of(source_key)
    folder = shared_settings.shared_dir()

    def blank() -> View:
        return View(source=source.key, label=source.label, source_note=source.note,
                    folder=str(folder), query=(query or "").strip(),
                    sort_dir="desc" if sort_dir == "desc" else "asc")

    def read() -> View:
        # **読み込みは自分の View に書く。** 待ちきれずに帰ったあとも裏で読み続けるので、
        # 画面へ返すものと同じものを触らせない
        view = blank()
        path = _find(source, folder)
        view.path = str(path)
        conn = _open(path)
        try:
            _fill(conn, source, view, table, sort, max(1, int(limit)), _stamp(path))
        finally:
            conn.close()
        return view

    started = time.monotonic()
    try:
        view = _bounded(read, timeout)
    except BrowseError as exc:
        view = blank()
        view.error = str(exc)
        view.fix = "share" if isinstance(exc, PlaceError) else ""
    except (sqlite3.Error, OSError) as exc:
        view = blank()
        view.error = f"{source.label}を読めません: {exc}"
    if view.error:
        log.warning("マスタ管理: %s を読めません: %s", source.label, view.error)
    else:
        log.info("マスタ管理: %s / %s を見ました(%s行中 %s行・%.1f秒)", source.label,
                 view.table, view.total, len(view.rows), time.monotonic() - started)
    return view


# 表ごとの行数の控え。**ファイルが変わっていなければ数え直さない**(並べ替え・
# 絞り込みのたびに全部の表を数えると、共有の上ではそのぶん待たせる)。
# 変わったかどうかは更新時刻と大きさで見る(書かれれば必ずどちらかが変わる)
_counts_lock = threading.Lock()
_counts_cache: dict[str, tuple[tuple[int, int], dict[str, int]]] = {}


def _stamp(path: Path) -> Optional[tuple[int, int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _counts(conn: sqlite3.Connection, path: str, stamp: Optional[tuple[int, int]],
            names: list[str]) -> dict[str, int]:
    with _counts_lock:
        cached = _counts_cache.get(path)
    if stamp is not None and cached is not None and cached[0] == stamp \
            and set(cached[1]) == set(names):
        return dict(cached[1])
    counts = {n: _count(conn, n) for n in names}
    if stamp is not None:
        with _counts_lock:
            _counts_cache[path] = (stamp, dict(counts))
    return counts


def _fill(conn: sqlite3.Connection, source: Source, view: View, wanted: str,
          sort: str, limit: int, stamp: Optional[tuple[int, int]] = None) -> None:
    names = _ordered(source, _table_names(conn))
    counts = _counts(conn, view.path, stamp, names)
    view.tables = [TableInfo(n, counts[n], TABLE_NOTES.get((source.key, n), ""))
                   for n in names]
    # 指定が無い・もう無い表なら**先頭**を開く(空の面を出して「選べ」とするより早い)
    view.table = wanted if wanted in names else (names[0] if names else "")
    if not view.table:
        return
    view.table_note = TABLE_NOTES.get((source.key, view.table), "")
    view.columns = _columns(conn, view.table)

    where, params = _filter(view.columns, view.query)
    default = DEFAULT_SORT.get((source.key, view.table), "")
    default = default if default in view.columns else ""
    order, view.sort = _orders(view.columns, sort, view.sort_dir,
                               newest_first=source.newest_first, default=default)
    if where:
        row = conn.execute(f"SELECT COUNT(*) FROM "
                           f"{source_db.quote_identifier(view.table)}{where}",
                           params).fetchone()
        view.total = int(row[0])
    else:
        # **見ている表は数え直す**(一覧の数は控えでよいが、この数は「ほか N行」に使う)
        fresh = _count(conn, view.table)
        view.total = max(0, fresh)
        for info in view.tables:
            if info.table == view.table:
                info.rows = fresh
    cursor, has_rowid = _select(conn, view.table, where, order, params, limit)
    names_out = [d[0] for d in cursor.description or []]
    for index, raw in enumerate(cursor.fetchall(), start=1):
        record = {k: _cell(v) for k, v in zip(names_out, raw)}
        if not has_rowid:
            record[ROW_KEY] = index
        view.rows.append(record)
    if not has_rowid and not view.sort and not default:
        view.order_label = "並びは決まっていません（rowid の無い表）"
    else:
        view.order_label = _order_label(source, view.sort, view.sort_dir, default)

    hidden = view.total - len(view.rows)
    if hidden > 0:
        view.note = (f"{view.total:,}行のうち {len(view.rows):,}行を出しています"
                     f"（ほか {hidden:,}行）。絞り込むと目当ての行が出ます。")


# ==================================================================
# CSV に書き出す
# ==================================================================
@dataclass
class ExportResult:
    file: str = ""
    folder: str = ""
    rows: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "folder": self.folder, "rows": self.rows,
                "note": self.note}


_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\s]+')


def _csv_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"（バイナリ {len(bytes(value))} バイト）"
    return value


def export_csv(source_key: str, table: str, *, query: str = "", sort: str = "",
               sort_dir: str = "asc", folder: Optional[Path] = None) -> ExportResult:
    """表を CSV に書き出す。**いま画面で絞り込み・並べ替えている形のまま、全行。**

    UTF-8 の BOM 付き・改行 CRLF(Excel でそのまま読める)。副番 `1-10` が
    日付に化けないように、履歴の CSV と同じ守り(`slip_history.excel_cell`)を
    掛ける。**書くだけ。** ファイルを開くのは使う人がすること。
    """
    source = source_of(source_key)
    shared = shared_settings.shared_dir()
    path = _find(source, shared)
    out_dir = folder or config.export_dir()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BrowseError(f"書き出し先のフォルダを作れません: {out_dir} ({exc})") from exc

    conn = _open(path)
    result = ExportResult(folder=str(out_dir))
    target: Optional[Path] = None
    try:
        names = _table_names(conn)
        if table not in names:
            raise BrowseError(f"{source.label}に表「{table}」がありません。")
        columns = _columns(conn, table)
        where, params = _filter(columns, query)
        order, _sorted = _orders(columns, sort, "desc" if sort_dir == "desc" else "asc",
                                 newest_first=source.newest_first,
                                 default=DEFAULT_SORT.get((source.key, table), ""))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = out_dir / f"{source.label}_{_UNSAFE_NAME.sub('_', table)}_{stamp}.csv"
        cursor, has_rowid = _select(conn, table, where, order, params, None)
        names_out = [d[0] for d in cursor.description or []]
        skip = 1 if has_rowid else 0             # `__行` は書かない(取り込み元の列ではない)
        cut = 0
        with open(target, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(names_out[skip:])
            for raw in cursor:
                if result.rows >= EXCEL_MAX_ROWS:
                    cut += 1
                    continue
                writer.writerow([slip_history.excel_cell(c, _csv_value(v))
                                 for c, v in zip(names_out[skip:], raw[skip:])])
                result.rows += 1
    except sqlite3.Error as exc:
        _discard(target)
        raise BrowseError(f"{source.label}を読めません: {exc}") from exc
    except OSError as exc:
        _discard(target)
        raise BrowseError(f"CSV を書けません: {exc}") from exc
    except BaseException:
        _discard(target)
        raise
    finally:
        conn.close()

    result.file = target.name
    notes = []
    if (query or "").strip():
        notes.append(f"絞り込み「{query.strip()}」に合う行だけです。")
    if cut:
        notes.append(f"Excel で開ける{EXCEL_MAX_ROWS:,}行までで止めました"
                     f"（ほか {cut:,}行は書いていません。絞り込んでください）。")
    if not result.rows:
        notes.append("合う行はありませんでした（見出しだけのファイルです）。")
    result.note = "".join(notes)
    log.info("マスタ管理: %s / %s を書き出しました: %s(%s行)", source.label, table,
             target, result.rows)
    return result


def _discard(target: Optional[Path]) -> None:
    """書きかけのファイルを残さない(途中までのファイルを全部だと思わせない)。"""
    if target is None:
        return
    try:
        target.unlink()
    except OSError:
        pass


def reset_for_tests() -> None:
    """試験用。"""
    with _guard:
        _running.clear()
    with _counts_lock:
        _counts_cache.clear()
