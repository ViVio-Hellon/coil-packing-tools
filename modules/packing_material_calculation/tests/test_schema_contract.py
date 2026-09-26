"""スキーマと Python 側の名前が食い違っていないか

【なぜこの検査が要るか】
Python は識別子を NFKC で正規化する。半角カナの属性名
`ｺｲﾙ間は間紙入` は、書いた瞬間に `コイル間は間紙入` になる。
全角の `Ｗ` は `W` になる。**エラーにならず静かにずれる**ので、
DB の列名と合わなくなっても気づけない。

取り込み元の列名(元 Access の半角カナ)は、識別子ではなく
**文字列リテラル**として `import_specs` だけが持つ。ここを混ぜないこと。
"""
import sqlite3
import unicodedata

import pytest

from modules.packing_material_calculation.coil_tool import db, import_specs, models
from modules.packing_material_calculation.tests import _fixture


def test_dataclass_fields_are_nfkc_stable():
    """属性名が正規化で変わらないこと。変わるものは DB 列と合わなくなる"""
    for cls in (models.OrderInfo, models.PackagingSpec,
                models.PalletRow, models.CalcState, models.ChecklistRow):
        for name in cls.__dataclass_fields__:
            assert unicodedata.normalize("NFKC", name) == name, (
                f"{cls.__name__}.{name} は正規化で "
                f"{unicodedata.normalize('NFKC', name)!r} に変わる")


def test_packaging_spec_fields_match_table_columns():
    """`PackagingSpec` の属性がそのまま包装仕様テーブルの列であること"""
    conn = _fixture.make_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(包装仕様)")}
    conn.close()
    assert set(models.PackagingSpec.__dataclass_fields__) <= cols


@pytest.mark.parametrize("table", ["包装仕様", "パレット", "リプラサイズ",
                                   "仕掛引当", "仕掛受注"])
def test_import_destinations_exist_in_schema(table):
    """取り込み先の列が `schema.sql` に実在すること。

    `import_specs` と `schema.sql` は対で直す約束なので、
    片方だけ直したらここで落ちる。
    """
    conn = _fixture.make_db()
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    conn.close()
    specs = (import_specs.MASTER_IMPORT_SPECS.get(table)
             or import_specs.LOT_IMPORT_SPECS[table])
    missing = [dest for dest, _, _ in specs if dest not in cols]
    assert not missing, f"{table} に列がありません: {missing}"


def test_convert_row_drops_rows_without_key():
    """鍵が空の行は写さない"""
    assert import_specs.convert_row("包装仕様", {"包装仕様NO": ""}) is None
    assert import_specs.convert_row("包装仕様", {"包装仕様NO": "1C0001"}) is not None


def test_convert_row_fills_missing_optional_columns():
    """取り込み元に無い任意列は既定値で埋めて、残りは取り込む"""
    row = import_specs.convert_row("仕掛引当", {"ﾛｯﾄ番号": "A123456",
                                                "受注番号": "12345678"})
    assert row is not None
    assert row["出荷日"] == ""


def test_missing_required_column_is_reported():
    """必須列が無ければ、取り込みは断る"""
    missing = import_specs.missing_columns("仕掛引当", {"ﾛｯﾄ番号"})
    assert "受注番号" in missing
    assert "出荷日" not in missing      # 任意なので数えない
