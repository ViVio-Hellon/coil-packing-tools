"""包装仕様No別の業務規則 ── 境界も含めて1件ずつ"""
import pytest

from modules.packing_material_calculation.coil_tool import calc_service, models, special_rules
from modules.packing_material_calculation.coil_tool.models import CalcState, OrderInfo
from modules.packing_material_calculation.tests import _fixture


@pytest.fixture
def conn():
    c = _fixture.make_db()
    _fixture.standard_master(c)
    yield c
    c.close()


def run(conn, spec_no, *, 板厚, 板幅, 検入数="20", 単重="100.00", 外径="900",
        枚数=None, 重量=None):
    _fixture.add_spec(conn, spec_no)
    s = CalcState(検入数=検入数, 外径=外径)
    s.order = OrderInfo(
        包装仕様NO=spec_no, 受注板厚=板厚, 受注板幅=板幅, 製品単重=単重,
        梱包単位_重量=重量 if 重量 is not None else OrderInfo.NO_DATA,
        梱包単位_枚数=枚数 if 枚数 is not None else OrderInfo.NO_DATA)
    # 規則を通すにはガードを越える必要がある(→ test_calc_chain の Q13)
    if 重量 is None and 枚数 is None:
        s.order.梱包単位_枚数 = "1"
    return s, calc_service.calculate(conn, s)


# ==================================================================
# 積数の上書き
# ==================================================================
@pytest.mark.parametrize("spec,atu,haba,expected", [
    ("1C1188", "0.800", "260.0", "2"),    # ｼﾏﾉ
    ("1C1188", "2.000", "180.0", "3"),
    ("1C1178", "0.250", "43.7", "4"),     # 積水化学工業
    ("1C1178", "0.350", "62.3", "3"),
    ("1C1178", "0.500", "97.2", "3"),
    ("1C1178", "1.200", "194.0", "2"),
    ("1C1271", "0.800", "72.0", "4"),     # ﾆｼﾊﾗﾘｺｳ
    ("1C1271", "2.000", "140.0", "3"),
    ("1C1295", "4.500", "132.0", "2"),    # ｶ)ﾃｲﾗﾄﾞ
    ("1C1295", "4.500", "214.0", "1"),
    ("1C1244", "0.160", "315.0", "1"),    # ﾋﾀﾁｷﾝｿﾞｸﾈｵﾏﾃﾘｱﾙ
    ("1C1244", "0.360", "50.0", "2"),
    ("1C1277", "1.000", "100.0", "3"),    # ｶ)ﾊﾂﾀﾞｲ 幅<=100
    ("1C1277", "1.000", "100.1", "2"),    # 幅>100
    ("1C1250", "1.800", "108.0", "2"),    # ロジメイト
    ("1C1250", "1.800", "107.8", "4"),
    ("1C1250", "1.500", "87.0", "5"),
    ("1C1250", "1.300", "80.0", "5"),
    ("1C1263", "0.300", "34.0", "8"),     # ＦＤＫ鳥取
    ("1C1263", "0.250", "50.0", "5"),
    ("1C1263", "0.500", "65.0", "4"),
])
def test_stack_override(conn, spec, atu, haba, expected):
    s, r = run(conn, spec, 板厚=atu, 板幅=haba)
    assert r.ok, r.reason
    assert s.積数 == expected
    assert models.FLAG_SPEC in s.flags


def test_shimano_kanshou_uses_weight_limit(conn):
    """1C1254 シマノ金商: 上限重量 +10% を単重で割って切り捨て"""
    # 0.6 × 34 → 上限 300kg。単重 40kg なら 330/40 = 8.25 → 8
    s, r = run(conn, "1C1254", 板厚="0.600", 板幅="34.0", 単重="40.00")
    assert r.ok
    assert s.積数 == "8"
    # 1.0 × 96 → 上限 500kg。550/40 = 13.75 → 13
    s, r = run(conn, "1C1254", 板厚="1.000", 板幅="96.0", 単重="40.00")
    assert s.積数 == "13"


def test_rule_that_does_not_match_still_sets_spec_flag(conn):
    """当たらなくても「規則が動いた」印は立つ。高さ判定をスキップするため"""
    s, r = run(conn, "1C1188", 板厚="9.999", 板幅="999.9", 枚数="5")
    assert r.ok
    assert models.FLAG_SPEC in s.flags
    assert s.積数 == "5"     # 上書きされず、オーダーの枚数のまま


# ==================================================================
# パレット種類 (1C1103 ｲｽﾞﾐﾒﾀﾙ)
# ==================================================================
@pytest.mark.parametrize("outer,expected", [
    (700.0, "全面"),
    (779.0, "全面"),
    (780.0, "全面"),     # 境界が重なっており、先勝ちで「全面」
    (799.0, "全面"),
    (800.0, "強度UP"),
    (890.0, "強度UP"),
    (990.0, "強度UP"),
])
def test_izumi_pallet_type(outer, expected):
    assert special_rules.pallet_type_override("1C1103", outer, "スカシ") == expected


def test_izumi_above_990_keeps_master_value():
    """990 超は上書きされない。マスタの値のまま(VBA どおり)"""
    assert special_rules.pallet_type_override("1C1103", 991.0, "スカシ") == "スカシ"


# ==================================================================
# パレットサイズ (1C1258 外径指定)
# ==================================================================
@pytest.mark.parametrize("outer,expected", [
    (700.0, 800.0), (780.0, 800.0),
    (781.0, 900.0), (880.0, 900.0),
    (1249.0, 1300.0), (1250.0, 1350.0), (1330.0, 1350.0),
])
def test_outer_designation_steps(outer, expected):
    assert special_rules.pallet_width_by_outer(outer) == expected


@pytest.mark.parametrize("outer", [699.0, 780.5, 1331.0])
def test_outer_designation_has_gaps(outer):
    """段の隙間と範囲外は「該当なし」。VBA も 0 のまま進む"""
    assert special_rules.pallet_width_by_outer(outer) == 0.0
