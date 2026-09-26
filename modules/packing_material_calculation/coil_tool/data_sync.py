"""取り込み ── 共有フォルダのマスタを手元へ写す

VBA は計算のたびに共有フォルダの .accdb を開いて読んでいた
(`GetRecordsArr(filePath, tableName, SQLfilter)`)。1回の計算で
同じファイルを何度も開くので、共有が重い日は目に見えて遅かった。

ここでは**一度写して、手元の SQLite を引く**。

    共有フォルダ (読むだけ)  ──取り込み──▶  手元の作業用DB  ──▶ 画面
       source_db(mode=ro)                     db.py

【取り込み中も画面を止めない】
1テーブルぶんを「DELETE してから全行 INSERT」する。既定の
rollback journal だとその間ずっと読み手が止まるので、WAL が使えるなら
使う(`sqlite_toolkit.enable_wal`)。共有フォルダでは WAL を張れない
ことがあるが、**手元の DB はローカル**なので効く。

【自動では取り込み直さない】
起動時に1回だけ(設定で切れる)。日付が変わっても、マスタが更新されても、
黙って読み直すことはしない ── 計算の途中で足元の値が変わるほうが困る。
代わりに「いつの写しか」を画面に出して、古ければ人が押せるようにする。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config, db, import_specs, source_db
from .logging_utils import get_logger

log = get_logger("data_sync")


@dataclass
class TableResult:
    """1テーブルの取り込み結果。"""
    table: str
    ok: bool
    rows: int = 0
    source: str = ""
    error: str = ""
    # 取り込み元に無かった列(既定値で埋めたもの)。画面に出して気づけるように
    filled: list[str] = field(default_factory=list)
    # 鍵が重なっていて飛ばした行の鍵。**黙って捨てない** ──
    # 元のマスタに同じ番号の行が2つある、ということなので画面に出す
    duplicates: list[str] = field(default_factory=list)


@dataclass
class SyncResult:
    """取り込み全体の結果。"""
    tables: list[TableResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.tables) and all(t.ok for t in self.tables)

    @property
    def total_rows(self) -> int:
        return sum(t.rows for t in self.tables)

    @property
    def failures(self) -> list[TableResult]:
        return [t for t in self.tables if not t.ok]


# ==================================================================
# 取り込み元を探す
# ==================================================================
def find_master_db() -> Optional[Path]:
    """梱包資材マスタのファイルを探す。

    名前で見つからなければフォルダの中の sqlite3 を拾う。ただし
    **仕掛台帳(`SIKA` で始まる)は除く** ── 同じフォルダに置かれて
    いることがあり、名前の緩い一致で拾うと取り違える。
    """
    directory = config.master_db_dir()
    found = source_db.find(directory, config.MATERIAL_DB_NAME)
    if found is not None:
        return found
    try:
        candidates = source_db.list_source_files(directory)
    except OSError as exc:
        log.warning("マスタのフォルダを見られませんでした: %s (%s)", directory, exc)
        return None
    for path in candidates:
        if path.stem.upper().startswith("SIKA"):
            continue
        return path
    return None


def find_lot_db(table: str) -> Optional[Path]:
    """仕掛台帳の1ファイルを探す。本命 → 予備の順(VBA の2系統)。"""
    name = config.LOT_DB_FILES.get(table)
    if not name:
        return None
    for directory in config.lot_db_dirs():
        found = source_db.find(directory, name)
        if found is not None:
            return found
    return None


# ==================================================================
# 1テーブルを写す
# ==================================================================
def _import_table(
    conn: Any, *, dest_table: str, source_path: Path, source_table: str,
) -> TableResult:
    """取り込み元の1テーブルを、手元の1テーブルへ写す。

    **全入れ替え**(DELETE → INSERT)。差分更新にしないのは、上流が
    行を消したときに手元へ残り続けるのを避けるため。
    """
    result = TableResult(table=dest_table, ok=False, source=str(source_path))

    try:
        available = set(source_db.columns(source_path, source_table))
    except source_db.SourceError as exc:
        result.error = f"取り込み元を読めません: {exc}"
        return result

    if not available:
        result.error = f"取り込み元にテーブル「{source_table}」がありません"
        return result

    missing = import_specs.missing_columns(dest_table, available)
    if missing:
        result.error = "取り込み元に列がありません: " + ", ".join(missing)
        return result

    specs = (import_specs.MASTER_IMPORT_SPECS.get(dest_table)
             or import_specs.LOT_IMPORT_SPECS[dest_table])
    result.filled = [src for _, src, _ in specs if src not in available]

    try:
        rows = source_db.read_table(source_path, source_table)
    except source_db.SourceError as exc:
        result.error = f"取り込み元を読めません: {exc}"
        return result

    converted = [r for r in (import_specs.convert_row(dest_table, row) for row in rows)
                 if r is not None]

    # 鍵が重なる行は**先に出てきたほうを残す**(VBA は鍵で引いて
    # 最初の1件しか見ない)。あとの行で上書きすると、VBA と違う行で
    # 計算することになる
    converted, result.duplicates = import_specs.drop_duplicates(
        dest_table, converted)
    if result.duplicates:
        log.warning("取り込み %s: 鍵が重なる行を %d 件飛ばしました"
                    "(先の行を残しました): %s", dest_table,
                    len(result.duplicates), ", ".join(result.duplicates[:5]))

    columns = [dest for dest, _, _ in specs]
    placeholders = ", ".join("?" for _ in columns)
    quoted = ", ".join(source_db.quote_identifier(c) for c in columns)
    sql = (f"INSERT OR REPLACE INTO {source_db.quote_identifier(dest_table)}"
           f" ({quoted}) VALUES ({placeholders})")

    # 1テーブルを1トランザクションで入れ替える。途中で落ちたら丸ごと戻す
    # (半分だけ新しい写し、が残らない)
    try:
        with conn:
            conn.execute(f"DELETE FROM {source_db.quote_identifier(dest_table)}")
            conn.executemany(sql, [[row[c] for c in columns] for row in converted])
    except Exception as exc:  # noqa: BLE001 - 理由を残して次のテーブルへ進む
        result.error = f"手元のDBへ書けませんでした: {exc}"
        log.warning("取り込み失敗 %s: %s", dest_table, exc)
        return result

    result.ok = True
    result.rows = len(converted)
    _record_import(conn, dest_table, source_path, len(converted))
    log.info("取り込み %s: %d 件 (%s)", dest_table, len(converted), source_path.name)
    return result


def _record_import(conn: Any, table: str, source_path: Path, rows: int) -> None:
    """「いつ・どこから・何件」を残す。画面に出して古さを判断させる。"""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO 取り込み履歴"
            " (テーブル名, 取り込み日時, 元ファイル, 件数) VALUES (?, ?, ?, ?)",
            (table, db.now_db_string(), str(source_path), rows))


# ==================================================================
# まとめて取り込む
# ==================================================================
def import_master(conn: Any) -> list[TableResult]:
    """梱包資材マスタの3テーブルを写す。"""
    path = find_master_db()
    if path is None:
        directory = config.master_db_dir()
        return [TableResult(table=t, ok=False,
                            error=f"梱包資材マスタが見つかりません: {directory}")
                for t in import_specs.MASTER_IMPORT_SPECS]
    return [_import_table(conn, dest_table=t, source_path=path, source_table=t)
            for t in import_specs.MASTER_IMPORT_SPECS]


def import_lot(conn: Any) -> list[TableResult]:
    """仕掛台帳の2テーブルを写す。"""
    results: list[TableResult] = []
    for table in import_specs.LOT_IMPORT_SPECS:
        path = find_lot_db(table)
        if path is None:
            dirs = " / ".join(str(d) for d in config.lot_db_dirs())
            results.append(TableResult(
                table=table, ok=False,
                error=f"{config.LOT_DB_FILES[table]} が見つかりません: {dirs}"))
            continue
        results.append(_import_table(
            conn, dest_table=table, source_path=path,
            source_table=import_specs.LOT_SOURCE_TABLE))
    return results


def import_all(conn: Optional[Any] = None) -> SyncResult:
    """マスタと仕掛台帳をまとめて写す。

    **1つ失敗しても残りは続ける。** 仕掛台帳に届かない端末でも、
    マスタさえ読めれば包装仕様の閲覧はできる。
    """
    result = SyncResult(started_at=db.now_db_string())
    if conn is None:
        with db.connect() as own:
            db.apply_schema(own)
            result.tables = import_master(own) + import_lot(own)
    else:
        result.tables = import_master(conn) + import_lot(conn)
    result.finished_at = db.now_db_string()

    if result.ok:
        log.info("取り込み完了: %d 件", result.total_rows)
    else:
        for failure in result.failures:
            log.warning("取り込めませんでした %s: %s", failure.table, failure.error)
    return result


# ==================================================================
# 写しの古さ
# ==================================================================
def last_import(conn: Any) -> dict[str, dict[str, Any]]:
    """テーブルごとの「いつの写しか」。画面に出す。"""
    rows = db.fetch_all(
        conn, "SELECT テーブル名, 取り込み日時, 元ファイル, 件数 FROM 取り込み履歴") or []
    return {r["テーブル名"]: dict(r) for r in rows}


def imported_at(conn: Any) -> str:
    """いちばん古い取り込み日時。1つでも未取り込みなら空文字。

    **いちばん古いほうを出す。** 「マスタは今朝、仕掛は3日前」の
    ときに新しいほうを出すと、古さに気づけない。
    """
    expected = set(import_specs.MASTER_IMPORT_SPECS) | set(import_specs.LOT_IMPORT_SPECS)
    records = last_import(conn)
    if not expected.issubset(records):
        return ""
    return min(records[t]["取り込み日時"] for t in expected)


# 「古い」の理由。画面は文言ではなくこれを見る
STALE_NONE = ""
STALE_NOT_IMPORTED = "not_imported"
STALE_DATE_CHANGED = "date_changed"

STALE_MESSAGES = {
    STALE_NOT_IMPORTED: "まだ取り込んでいません",
    STALE_DATE_CHANGED: "取り込んだ日から日付が変わりました",
}


def stale_reason(conn: Any) -> str:
    """写しが古いか、その理由。古くなければ空文字。

    **取り込んだ日と今日が違えば古い。**

    以前は「取り込みから24時間経ったか」で見ていた。仕掛台帳は日々の
    台帳なので、日付が変われば上流の中身も変わる。ところが経過時間で
    見ていると、23:50 に取り込んで翌 00:10 に使ったとき経過は20分
    なので古くならず、**前日の写しのまま計算できてしまった**。
    本人は「さっき取り込んだ」と思っているので気づけない。

    日付で見ると、そういう取りこぼしが無い ── 同じ日の中は最長でも
    24時間未満なので、**経過時間で見るより甘くなることはない**。
    """
    stamp = imported_at(conn)
    if not stamp:
        return STALE_NOT_IMPORTED
    try:
        when = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # 読めない日時は、古いものとして扱う(黙って新しい扱いにしない)
        return STALE_NOT_IMPORTED
    if when.date() != datetime.now().date():
        return STALE_DATE_CHANGED
    return STALE_NONE


def stale_message(conn: Any) -> str:
    """「古い」の理由を人の言葉で。古くなければ空文字。

    **なぜ古いのかを出す。** 20分前に取り込んだ人が日付をまたいだ
    だけで「古い」と言われると、壊れたように見える。
    """
    return STALE_MESSAGES.get(stale_reason(conn), "")


def is_stale(conn: Any) -> bool:
    """写しが古いか。未取り込みも「古い」に含める。"""
    return stale_reason(conn) != STALE_NONE


# ==================================================================
# 起動時の自動取り込み
# ==================================================================
def find_lot_dbs() -> dict[str, Path]:
    """仕掛台帳のファイルを、見つかったぶんだけ返す。診断とログに使う。"""
    found: dict[str, Path] = {}
    for table, name in config.LOT_DB_FILES.items():
        path = find_lot_db(table)
        if path is not None:
            found[name] = path
    return found


def _source_stamp(path: Path) -> str:
    """元ファイルの更新時刻。取り込み済みのものと比べる。"""
    try:
        return str(int(path.stat().st_mtime))
    except OSError:
        return ""


def auto_import(conn: Any, *, progress=None) -> SyncResult:
    """**更新されたものだけ**取り込む(起動時に走る)。

    毎回すべて読み直すと、共有が重い日は起動が数十秒になる。
    元ファイルの更新時刻を覚えておいて、変わっていないものは飛ばす。

    `progress` は `(done, total, label)` で呼ばれる。起動待機画面が
    これを読んで「いま何をしているか」を出す。
    """
    db.apply_schema(conn)

    plan: list[tuple[str, Path, str]] = []
    master = find_master_db()
    if master is not None:
        for table in import_specs.MASTER_IMPORT_SPECS:
            plan.append((table, master, table))
    for table in import_specs.LOT_IMPORT_SPECS:
        path = find_lot_db(table)
        if path is not None:
            plan.append((table, path, import_specs.LOT_SOURCE_TABLE))

    known = last_import(conn)
    result = SyncResult(started_at=db.now_db_string())
    total = len(plan)

    for i, (dest, path, source_table) in enumerate(plan, start=1):
        if progress is not None:
            progress(i - 1, total, f"{dest} を取り込み中")

        # 元が変わっていなければ飛ばす
        record = known.get(dest)
        stamp = _source_stamp(path)
        if record and stamp and record.get("元ファイル") == str(path):
            imported_when = record.get("取り込み日時", "")
            try:
                when = datetime.strptime(imported_when, "%Y-%m-%d %H:%M:%S")
                if path.stat().st_mtime <= when.timestamp():
                    result.tables.append(TableResult(
                        table=dest, ok=True, rows=int(record.get("件数", 0)),
                        source=str(path)))
                    continue
            except (ValueError, OSError):
                pass

        result.tables.append(_import_table(
            conn, dest_table=dest, source_path=path, source_table=source_table))

    if not plan:
        log.warning("取り込み元が1つも見つかりません: マスタ=%s 台帳=%s",
                    config.master_db_dir(), config.lot_db_dir())

    if progress is not None:
        progress(total, total, "取り込み完了")
    result.finished_at = db.now_db_string()
    return result


# ==================================================================
# 1表だけ取り込み直す
# ==================================================================
def reimport_table(conn: Any, table: str, *,
                   path: Optional[Path] = None) -> TableResult:
    """その表だけを取り込み直して、手元を取り込み元に追いつかせる。

    マスタ管理が取り込み元へ書いたあとに呼ぶ(`master_admin._follow`)。
    ここを飛ばすと、**取り込み元は直っているのに画面の動きは変わらない**
    ── 直したのに効かない、がいちばんたちが悪い。

    まとめて取り込むのと違い、**触った1表だけ**にする。他の表まで
    読み直すと、1行直すたびに共有フォルダを5回叩くことになる。
    """
    if table in import_specs.MASTER_IMPORT_SPECS:
        found = path or find_master_db()
        source_table = table
    elif table in import_specs.LOT_IMPORT_SPECS:
        found = path or find_lot_db(table)
        source_table = import_specs.LOT_SOURCE_TABLE
    else:
        return TableResult(table=table, ok=False,
                           error=f"取り込みの定義がありません: {table}")

    if found is None:
        return TableResult(table=table, ok=False,
                           error="取り込み元が見つかりません")

    db.apply_schema(conn)
    return _import_table(conn, dest_table=table, source_path=found,
                         source_table=source_table)
