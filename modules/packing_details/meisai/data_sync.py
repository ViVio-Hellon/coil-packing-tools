"""取り込み元 (共有フォルダの sqlite3) → 手元のSQLite

移植元 `python-web-tools` の `data_sync.py` と同じ4段で動く。

    1. 置き場所を引く      config.lot_db_dir() / konpo_db_dir()
    2. ファイルを探す      find_sources()
    3. 対応表で総入れ替え   import_table()
    4. 手元を引く          lot_repo(以降はここだけを見る)

【共有フォルダへ触るのは取り込みのときだけ】
画面も業務ロジックも手元のテーブルしか見ない。共有への往復が
1回の操作ごとに起きると、ネットワークの調子がそのまま操作の
待ち時間になる。

【取り込みは総入れ替え】
取り込み元はキーを持たないスナップショットで、差分を出す手掛かりが
無い。1テーブルずつ `DELETE` してから入れ直す。**1テーブルが失敗
しても他は続ける** ── 現場では「全部止まる」より「入るものは入る」
ほうが役に立つ。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import config, import_specs, source_db
from .logging_utils import get_logger

log = get_logger("data_sync")

# 進捗の通知先。`(パーセント, 文言, 成功したか)`
Progress = Callable[..., None]


def _noop_progress(_pct: int, _msg: str, ok: bool = True) -> None:
    return None


class SyncError(RuntimeError):
    """取り込みに失敗した。"""


# ==================================================================
# 探す
# ==================================================================
def find_sources(lot_dir: Optional[Path] = None,
                 konpo_dir: Optional[Path] = None) -> dict[str, Path]:
    """4ファイルを探す。**見つかったものだけ**返す。

    仕掛台帳(3ファイル)と梱包課共有(1ファイル)は別のフォルダにある。
    共有に届かない端末もあるので、どちらもアプリの起動フォルダに
    置かれた写しを次点で探す。

    見つからないファイルを例外にしないのは、**届いた分だけは最新に
    したい**から。LS4LOTだけ届かない端末でも、ロット情報は引ける
    (前工程実績数を手で入れれば作業は進む)。
    """
    found: dict[str, Path] = {}
    pairs = (
        (config.LOT_DB_FILES, lot_dir if lot_dir is not None else config.lot_db_dir()),
        (config.KONPO_DB_FILES, konpo_dir if konpo_dir is not None else config.konpo_db_dir()),
    )
    for files, primary in pairs:
        for table, filename in files.items():
            # 次点はアプリのフォルダ(統合アプリの根)。共有に届かない端末は
            # そこに写しを置く(統合版で直した: 機能のフォルダを見ていた)
            for base in (primary, config.APP_DIR):
                path = source_db.find(base, filename)
                if path is not None:
                    found[table] = path
                    break
    return found


def missing_sources(found: dict[str, Path]) -> dict[str, str]:
    """見つからなかったファイル。`{手元の表名: ファイル名}`。"""
    return {table: name for table, name in config.ALL_SOURCE_FILES.items()
            if table not in found}


# ==================================================================
# 結果
# ==================================================================
@dataclass
class ImportResult:
    """1回の取り込みの結果。画面に出すためのまとめ。"""

    imported: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.imported.values())

    @property
    def ok(self) -> bool:
        return not self.errors

    def merge(self, other: "ImportResult") -> "ImportResult":
        self.imported.update(other.imported)
        self.skipped.update(other.skipped)
        self.errors.extend(other.errors)
        return self

    def summary(self) -> str:
        """一行で言う。"""
        if not self.imported and self.errors:
            return "取り込めませんでした"
        parts = [f"{t} {n:,}件" for t, n in self.imported.items()]
        text = " / ".join(parts) if parts else "0件"
        if self.errors:
            text += f"（{len(self.errors)}件の問題あり）"
        return text


# ==================================================================
# 読む
# ==================================================================
def read_table(path: Path, table: str = import_specs.SOURCE_TABLE) -> list[dict[str, Any]]:
    """取り込み元の1表を丸ごと読む。**読み取り専用で開く。**"""
    try:
        return source_db.read_table(path, table)
    except source_db.SourceError as exc:
        raise SyncError(str(exc)) from exc


def source_created_at(path: Path) -> str:
    """取り込み元の `_更新情報` にある作成日時。

    `SIKALOT` / `SIKAHIKI` / `SIKAODR` はこの表を持っていて、
    `作成日時` `RNE` `件数` が入っている。画面に「いつ時点の台帳か」を
    出すために使う。**LS4LOT には無い**ので、その場合は空を返す
    (呼び出し側はファイルの更新時刻へ落とす)。
    """
    try:
        rows = source_db.read_table(path, "_更新情報")
    except source_db.SourceError:
        return ""
    for row in rows:
        if str(row.get("項目", "")).strip() == "作成日時":
            return str(row.get("値", "") or "").strip()
    return ""


# ==================================================================
# 取り込む
# ==================================================================
def import_table(conn: sqlite3.Connection, table: str, source_path: Path, *,
                 result: Optional[ImportResult] = None) -> ImportResult:
    """1つの表を総入れ替えで取り込む。

    **元に無い列があれば先に言う。** `row.get()` は無い列を None に
    するので、上流が列名を変えても取り込み自体は「成功」してしまい、
    中身だけが空になる。鍵の列が無いなら**入れない** ── 空の鍵で
    総入れ替えすると、それまで使えていたデータまで消える。
    """
    result = result or ImportResult()
    spec = import_specs.IMPORT_SPECS[table]

    try:
        rows = read_table(source_path)
    except SyncError as exc:
        log.warning("%s: 読み取り失敗 %s", table, exc)
        result.errors.append(f"{table}: {exc}")
        return result

    if not rows:
        result.imported[table] = 0
        return result

    present = set(rows[0].keys())
    missing = [src for _col, src, _conv in spec if src not in present]
    if missing:
        keys = {src for col, src, _conv in spec
                if col in import_specs.REQUIRED_KEY_COLUMNS.get(table, ())}
        lost = [m for m in missing if m in keys]
        note = f"{table}: 元のファイルに無い列があります: {', '.join(missing)}"
        if lost:
            result.errors.append(
                note + f" ── {', '.join(lost)} が無いので取り込みません")
            log.warning("%s: 鍵の列が無いため取り込みを見送りました: %s", table, lost)
            return result
        result.errors.append(note + " ── その列は空で取り込みます")
        log.warning("%s: 元に無い列: %s", table, missing)

    columns = [c[0] for c in spec]
    col_list = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    required = import_specs.REQUIRED_KEY_COLUMNS.get(table, ())
    blanks = import_specs.BLANK_IS_MISSING.get(table, ())
    fallbacks = import_specs.NULL_FALLBACKS

    imported = skipped = 0
    try:
        with conn:
            conn.execute(f'DELETE FROM "{table}"')
            for row in rows:
                values = {col: conv(row.get(src)) for col, src, conv in spec}
                if any(values[k] is None for k in required):
                    skipped += 1
                    continue
                if any(str(values[k] or "").strip() == "" for k in blanks):
                    skipped += 1
                    continue
                conn.execute(
                    f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})',
                    [values[col] if values[col] is not None else fallbacks.get(conv)
                     for col, _src, conv in spec])
                imported += 1
    except sqlite3.Error as exc:
        log.exception("%s: 取り込み中にエラー", table)
        result.errors.append(f"{table}: {exc}")
        return result

    result.imported[table] = imported
    if skipped:
        result.skipped[table] = skipped
    _note_imported(conn, table, source_path, imported)
    log.info("%s: %s件取り込み(%s件スキップ)", table, imported, skipped)
    return result


def import_all(conn: sqlite3.Connection, *,
               lot_dir: Optional[Path] = None,
               konpo_dir: Optional[Path] = None,
               progress: Optional[Progress] = None,
               force: bool = False,
               only: Optional[Iterable[str]] = None) -> ImportResult:
    """4ファイルまとめて取り込む。画面の「取り込み」ボタン用。

    `force` が偽なら、**元が前回から変わっていないファイルは飛ばす**。
    仕掛台帳は日々更新されるので結果的にほぼ毎回読み、変わっていない
    ものだけが省かれる。

    `only` は取り込む表(手元の表名)。設定画面で**置き場所を変えた欄の分だけ**
    取り込むときに使う(仕掛台帳の3つ / 梱包課共有の LS4LOT)。
    """
    result = ImportResult()
    notify = progress or _noop_progress
    wanted = set(only) if only is not None else set(config.ALL_SOURCE_FILES)
    found = {t: p for t, p in find_sources(lot_dir, konpo_dir).items() if t in wanted}

    if not found:
        places = []
        if wanted & set(config.LOT_DB_FILES):
            places.append(f"{config.lot_db_dir()} に "
                          f"{', '.join(n for t, n in config.LOT_DB_FILES.items() if t in wanted)} を")
        if wanted & set(config.KONPO_DB_FILES):
            places.append(f"{config.konpo_db_dir()} に "
                          f"{', '.join(n for t, n in config.KONPO_DB_FILES.items() if t in wanted)} を")
        result.errors.append(f"取り込み元が見つかりません。{'、'.join(places)}置いてください。")
        notify(100, "取り込み元が見つかりません", False)
        return result

    items = list(found.items())
    for index, (table, path) in enumerate(items):
        pct = 100 * index // len(items)
        notify(pct, f"{table} を読み込み中...（{index + 1}/{len(items)}）")
        if not force and not needs_import(conn, path):
            log.info("%s: 元が変わっていないので飛ばします (%s)", table, path.name)
            continue
        import_table(conn, table, path, result=result)
        if result.errors and table in [e.split(":")[0] for e in result.errors]:
            notify(pct, f"{table} を読み込み中...", False)

    for table, filename in missing_sources(found).items():
        if table in wanted:
            result.errors.append(f"{filename} が見つからないため {table} は更新していません")

    notify(100, result.summary(), result.ok)
    return result


# ==================================================================
# 更新の検知
# ==================================================================
def needs_import(conn: sqlite3.Connection, path: Path) -> bool:
    """元ファイルが前回の取り込み以降に更新されているか。

    **一度も取り込んでいなければ「要る」と答える。** 分からないのに
    「変わっていない」と答えると、古い台帳を使い続けることになる。

    ファイルを**開かずに**判定する(更新時刻だけ見る)ので、共有への
    往復は1回で済む。
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False                       # 届いていない。見に行けない
    row = conn.execute("SELECT 更新時刻 FROM 取り込み記録 WHERE ファイル = ?",
                       (str(path),)).fetchone()
    # 1秒の余裕。共有フォルダは秒未満の精度が環境によって落ちる
    return row is None or mtime > float(row[0]) + 1.0


def _note_imported(conn: sqlite3.Connection, table: str, path: Path,
                   count: int) -> None:
    """取り込んだ、と覚える。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    with conn:
        conn.execute(
            "INSERT INTO 取り込み記録"
            " (テーブル, ファイル, 更新時刻, 取り込み日時, 元作成日時, 件数)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(テーブル) DO UPDATE SET"
            " ファイル=excluded.ファイル, 更新時刻=excluded.更新時刻,"
            " 取り込み日時=excluded.取り込み日時,"
            " 元作成日時=excluded.元作成日時, 件数=excluded.件数",
            (table, str(path), mtime, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             source_created_at(path), count))


@dataclass(frozen=True)
class SourceStamp:
    """取り込み元1つの鮮度。画面に出す。"""

    table: str
    filename: str
    found: Optional[bool]       # None = 見に行っていない(`stamps(check=False)`)
    created_at: str = ""        # 元の `_更新情報` にある作成日時
    imported_at: str = ""       # こちらが取り込んだ日時
    count: int = 0

    @property
    def imported(self) -> bool:
        """一度でも取り込めているか。**いま元が届くかとは別。**"""
        return bool(self.imported_at)

    @property
    def as_of(self) -> str:
        """「いつ時点の台帳か」。元が名乗る作成日時を優先する。"""
        if not self.imported:
            return "未取込"
        return self.created_at or self.imported_at

    @property
    def as_of_short(self) -> str:
        """上の帯に出す短い形。`2026-09-14T09:00:26` → `2026-09-14 09:00`。

        **年は落とさない。** 秒と `T` は読むときに要らないが、年まで
        削ると去年の台帳が今朝のものに見える。4つ並べても折り返さない
        だけの幅は、これで足りる。
        """
        v = self.as_of
        if len(v) >= 16 and v[4] == "-" and v[10] in "T ":
            return v[:10] + " " + v[11:16]
        return v

    def to_dict(self) -> dict[str, Any]:
        """画面へ渡す形。**導き出した値もここに入れる。**

        `__dict__` をそのまま渡すと `as_of` と `imported` が落ちて、
        受け取った側が `found` から組み立て直すことになる ── そして
        「届かない」と「取り込んでいない」を取り違える(実際そうなって
        いた)。判断はここ1か所に置く。
        """
        return {"table": self.table, "filename": self.filename,
                "found": self.found, "created_at": self.created_at,
                "imported_at": self.imported_at, "count": self.count,
                "imported": self.imported, "as_of": self.as_of,
                "as_of_short": self.as_of_short}


def stamps(conn: sqlite3.Connection, *, check: bool = True,
           found: Optional[dict[str, Path]] = None) -> list[SourceStamp]:
    """取り込み元それぞれの鮮度。設定画面と画面上部の断り書きに使う。

    `check` が偽なら**取り込み元を見に行かない**(`found` は None)。いつ時点の
    ものが入っているかは手元の記録だけで分かる ── 画面を開くたびに共有へ
    問い合わせると、届かない・遅い共有のぶんだけ画面が出るのが遅れる。
    `found` を渡せば、見に行った結果としてそれを使う(時間を区切って探した結果)。
    """
    if found is None and check:
        found = find_sources()
    out: list[SourceStamp] = []
    for table, filename in config.ALL_SOURCE_FILES.items():
        # **取り込んだ実績はテーブル名で引く。** 置き場所(パス)で引くと、
        # 共有に届かない端末では「取り込んであるのに未取込と出る」。
        # いま元が届くか(`found`)と、いつ時点のものが入っているか
        # (`imported_at`)は別の話なので、別々に持つ
        row = conn.execute(
            "SELECT 取り込み日時, 元作成日時, 件数 FROM 取り込み記録"
            " WHERE テーブル = ?", (table,)).fetchone()
        out.append(SourceStamp(
            table, filename, found=(table in found) if found is not None else None,
            created_at=str(row["元作成日時"]) if row else "",
            imported_at=str(row["取り込み日時"]) if row else "",
            count=int(row["件数"]) if row else 0))
    return out


def has_lot_data(conn: sqlite3.Connection) -> bool:
    """仕掛台帳が取り込まれているか。未取り込みなら画面で案内を出す。"""
    row = conn.execute("SELECT COUNT(*) AS c FROM 仕掛ロット").fetchone()
    return bool(row and row["c"])
