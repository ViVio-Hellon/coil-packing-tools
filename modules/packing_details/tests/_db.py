"""試験用の手元DBと、取り込み元の作り方

**取り込み元は本物と同じ形で作る。** 実データ(`SIKALOT.sqlite3` 等)は
全列 TEXT で、`ｵｰﾀﾞｰ板丈` の実値は `'0'` ではなく `'0.0'`、
`LS4LOT.sqlite3` は**型宣言すら無い**。この差が取り込みの正しさを
決めるので、試験でも同じ形を作る ── きれいな型で作った偽物を相手に
すると、本物で落ちる不具合が試験では通ってしまう。
"""
from __future__ import annotations

import shutil
import sqlite3
import unittest
from pathlib import Path
from typing import Any, Optional, Sequence

from modules.packing_details.meisai import db


def memory_db() -> sqlite3.Connection:
    """スキーマを当てた手元のDB。1件の試験のあいだだけ生きる。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.apply_schema(conn)
    return conn


def case_db(case: unittest.TestCase) -> sqlite3.Connection:
    """`memory_db()` を開いて、片付けまで `case` に積む。"""
    conn = memory_db()
    case.addCleanup(conn.close)
    return conn


def file_db(case: unittest.TestCase) -> tuple[sqlite3.Connection, Path]:
    """**ファイルの**DBを1つ。ロックの試験に使う。

    `:memory:` は接続1本ぶんしかないので、別の接続から握れない ──
    ロックに当たったときの振る舞いを確かめられない。
    """
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="meisai_test_"))
    path = directory / "t.db"
    conn = db.get_connection(path)
    db.apply_schema(conn)

    def cleanup() -> None:
        conn.close()
        shutil.rmtree(directory, ignore_errors=True)

    case.addCleanup(cleanup)
    return conn, path


# ------------------------------------------------------------------
# 取り込み元のにせもの (本物と同じ形)
# ------------------------------------------------------------------
def make_source(path: Path, columns: Sequence[str],
                rows: Sequence[dict[str, Any]], *,
                declare_text: bool = True,
                created_at: Optional[str] = None) -> Path:
    """取り込み元の sqlite3 を1つ作る。

    `declare_text` が偽だと**型宣言なし**で作る(`LS4LOT.sqlite3` と
    同じ形)。`created_at` を渡すと `_更新情報` 表も作る
    (`SIKALOT` 等が持っていて、LS4LOT は持っていない)。
    """
    conn = sqlite3.connect(str(path))
    decl = ", ".join(f'"{c}" TEXT' if declare_text else f'"{c}"' for c in columns)
    conn.execute(f'CREATE TABLE "仕掛" ({decl})')
    marks = ", ".join("?" for _ in columns)
    cols = ", ".join(f'"{c}"' for c in columns)
    with conn:
        for row in rows:
            conn.execute(f'INSERT INTO "仕掛" ({cols}) VALUES ({marks})',
                         [row.get(c) for c in columns])
        if created_at is not None:
            conn.execute('CREATE TABLE "_更新情報" ("項目" TEXT, "値" TEXT)')
            conn.execute('INSERT INTO "_更新情報" VALUES (?, ?)',
                         ("作成日時", created_at))
            conn.execute('INSERT INTO "_更新情報" VALUES (?, ?)',
                         ("件数", str(len(rows))))
    conn.close()
    return path


# 実データから写した列の並び(使う分だけ)
LOT_COLUMNS = ("ﾛｯﾄ番号", "用途ｺｰﾄﾞ", "用途名", "製造材質", "製造調質",
               "製造板厚", "製造板幅", "ｵｰﾀﾞｰ板厚", "ｵｰﾀﾞｰ板幅", "ｵｰﾀﾞｰ板丈",
               "設計_設備ｺｰｽ", "実績_設備ｺｰｽ")
HIKI_COLUMNS = ("ﾛｯﾄ番号", "引当番号", "受注番号")
ODR_COLUMNS = ("受注番号", "包装仕様NO", "取引先名称", "納入先名称")
TOKOTEI_COLUMNS = ("ﾛｯﾄ番号", "当工程設計_設備名", "当工程設計_横割数",
                   "当工程設計_縦割数", "当工程設計_枚本数",
                   "製造板厚", "製造板幅", "製造板丈")


def lot_row(lot_no: str, **over: Any) -> dict[str, Any]:
    """仕掛ロット1行。**板丈は実データと同じ `'0.0'`。**"""
    row = {
        "ﾛｯﾄ番号": lot_no, "用途ｺｰﾄﾞ": "R192", "用途名": "ｱﾝｾﾞﾝﾀｲｻﾝｺｰ",
        "製造材質": "52S", "製造調質": "H34",
        "製造板厚": "1.985", "製造板幅": "104.0",
        "ｵｰﾀﾞｰ板厚": "2.0", "ｵｰﾀﾞｰ板幅": "104.0", "ｵｰﾀﾞｰ板丈": "0.0",
        "設計_設備ｺｰｽ": "HOT L-1 LS4", "実績_設備ｺｰｽ": "HOT L-1",
    }
    row.update(over)
    return row


def tokotei_row(lot_no: str, tate: str = "2", yoko: str = "12",
                **over: Any) -> dict[str, Any]:
    """仕掛当工程1行。縦割・横割は実データと同じ**文字列**で入れる。"""
    row = {
        "ﾛｯﾄ番号": lot_no, "当工程設計_設備名": "LS4",
        "当工程設計_横割数": yoko, "当工程設計_縦割数": tate,
        "当工程設計_枚本数": "24",
        "製造板厚": "1.985", "製造板幅": "104.0", "製造板丈": "0.0",
    }
    row.update(over)
    return row


def settings_file(case: unittest.TestCase) -> Path:
    """この試験だけの設定ファイル(まだ無い状態から)と、共有フォルダ。

    設定は `config.USER_CONFIG_PATH` を読み書きのたびに開くので、
    ここを差し替えれば、この試験の中だけで変わる。

    **共有フォルダも使い捨てにする。** 右上の文字と管理者パスワードは
    全ラインで共有(`shared_settings`)なので、本物の共有を指したまま
    試験すると**全ラインの紙が変わる。** 共有フォルダはまだ作らず、
    その上のフォルダだけ作っておく(入れたばかりの現場と同じ形)。
    """
    import tempfile
    from modules.packing_details.meisai import config, shared_settings

    folder = tempfile.TemporaryDirectory(prefix="packing-details-settings-")
    case.addCleanup(folder.cleanup)
    root = Path(folder.name)
    for name in ("USER_CONFIG_PATH", "SHARED_DIR", "EXPORT_DIR"):
        case.addCleanup(setattr, config, name, getattr(config, name))
    config.EXPORT_DIR = root / "export"
    config.USER_CONFIG_PATH = root / "pc1" / "user_config.json"
    (root / "share").mkdir()
    # 本番は梱包資材マスタのフォルダ(`【■】_参照用ファイル`)。試験では
    # まだ作らない(JSONだけで使う・最初に変えたとき作る形も確かめるため)。
    # マスタを置く試験は `master_file` がフォルダごと作る
    config.SHARED_DIR = root / "share" / "【■】_参照用ファイル"
    shared_settings.reset_for_tests()
    case.addCleanup(shared_settings.reset_for_tests)
    from modules.packing_details.meisai import master_browse, slip_history
    slip_history.reset_for_tests()
    case.addCleanup(slip_history.reset_for_tests)
    master_browse.reset_for_tests()
    case.addCleanup(master_browse.reset_for_tests)
    return config.USER_CONFIG_PATH


def other_pc(case: unittest.TestCase, name: str = "pc2") -> Path:
    """**別のラインPC**になる。共有フォルダはそのまま、端末ごとの設定だけ替える。

    戻すときは `this_pc` ── 同じ試験の中で2台を行き来できる。
    """
    from modules.packing_details.meisai import config
    path = config.USER_CONFIG_PATH.parent.parent / name / "user_config.json"
    config.USER_CONFIG_PATH = path
    return path


def this_pc(case: unittest.TestCase) -> Path:
    return other_pc(case, "pc1")


# 現場が足した表の形。**アップロードされた梱包資材マスタのとおり**
# (Access で作って、総合ツールの「表を持ってくる」で足したもの。列に型が無い)
MASTER_DDL = 'CREATE TABLE "梱包明細打ち出し" ("ID", "設定文字列")'
REGISTRY_DDL = ('CREATE TABLE "ツールで足した表" '
                '(表 TEXT PRIMARY KEY, 足した日時 TEXT, 元のファイル TEXT)')


def master_file(case: unittest.TestCase, rows=((1, "NLM.NAGOYA.QA"),), *,
                table: bool = True, name: str = "梱包資材マスタ.sqlite3") -> Path:
    """共有フォルダに梱包資材マスタを置く(`settings_file` のあとで呼ぶ)。

    ほかの表も1つ入れておく ── 右上の文字を書いても、**ほかの表に触らない**
    ことを確かめるため。
    """
    from modules.packing_details.meisai import config
    folder = config.SHARED_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute('CREATE TABLE "PalletMaster" ("ID" INTEGER, "名前" TEXT)')
        conn.execute("INSERT INTO PalletMaster VALUES (1, 'P-1'), (2, 'P-2')")
        if table:
            conn.execute(MASTER_DDL)
            conn.executemany('INSERT INTO "梱包明細打ち出し" VALUES (?, ?)', list(rows))
            conn.execute(REGISTRY_DDL)
            conn.execute("INSERT INTO ツールで足した表 VALUES "
                         "('梱包明細打ち出し', '2026-09-25 16:06:59', 'C:\\梱包資材マスタ.accdb')")
        conn.commit()
    finally:
        conn.close()
    return path


def master_rows(path: Path) -> list[tuple]:
    """マスタの表の中身(rowid の順)。"""
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute('SELECT "ID", "設定文字列" FROM "梱包明細打ち出し"'
                            " ORDER BY rowid").fetchall()
    finally:
        conn.close()
