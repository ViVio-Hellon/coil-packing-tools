"""リプラの長さと本数"""
import pytest

from modules.packing_material_calculation.coil_tool import calc_service, models, ripla_service, special_rules
from modules.packing_material_calculation.coil_tool.models import CalcState, OrderInfo
from modules.packing_material_calculation.tests import _fixture


@pytest.fixture
def conn():
    c = _fixture.make_db()
    _fixture.standard_master(c)
    yield c
    c.close()


def run(conn, *, 検入数, 枚数, spec_no="1C0001", 外径="900", **spec_over):
    _fixture.add_spec(conn, spec_no, **spec_over)
    s = CalcState(検入数=str(検入数), 外径=外径)
    s.order = OrderInfo(包装仕様NO=spec_no, 受注板厚="1.000", 受注板幅="100.0",
                        製品単重="100.00", 梱包単位_重量=OrderInfo.NO_DATA,
                        梱包単位_枚数=str(枚数))
    return s, calc_service.calculate(conn, s)


# ==================================================================
# 長さ
# ==================================================================
def test_sukashi_uses_largest_stock_length(conn):
    """スカシ: 最下部は定尺のうちパレットサイズ以下で最大、コイル間は -100"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下", パレット="スカシ", 外径="900")
    assert r.ok
    assert s.パレットサイズ == "900"
    assert s.最下部長さ == "900"     # 定尺に 900 がある
    assert s.リプラ長さ == "800"     # 900 - 100


def test_zenmen_uses_pallet_size_directly(conn):
    """全面: 最下部はパレットサイズそのまま"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下", パレット="全面", 外径="1000")
    assert r.ok
    assert s.最下部長さ == "1000"
    assert s.リプラ長さ == "900"     # 1000 - 100


def test_exps_shortens_by_350_and_counts_differ(conn):
    """EXPS: コイル間は -350、最下部は長い1本 + 短い2本"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下", パレット="EXPS",
               外径="1000", 下本数="3")
    assert r.ok
    assert s.最下部長さ == "1000"
    assert s.リプラ長さ == "650"     # 1000 - 350
    # 台数 2 → 長い 2*1 = 2、短い 2*2 = 4
    assert s.台数 == "2"
    assert s.長い本数 == "2"
    assert s.短い本数 == "4"


def test_coil_length_over_800_is_reselected_from_stock(conn):
    """コイル間が 800mm 以上なら定尺に寄せる(倉庫でのカットを避ける)

    全面・パレット 1200 → 1200 - 100 = 1100。定尺に 1100 があるのでそのまま。
    """
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下", パレット="全面", 外径="1150")
    assert r.ok
    assert s.パレットサイズ == "1200"
    assert s.リプラ長さ == "1100"


# ==================================================================
# 本数
# ==================================================================
def test_divisible_count(conn):
    """割り切れるとき: コイル間 = (積数-1)*2 × 台数

    検入数 10、積数 5 → 台数 2。1台あたり (5-1)*2 = 8 本 → 16 本。
    最下部は 下本数 3 × 台数 2 = 6 本。合計 22。
    """
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下", 下本数="3")
    assert r.ok
    assert s.台数 == "2"
    assert s.本数 == "16"
    assert s.長い本数 == "6"
    assert s.短い本数 == "0"
    assert s.TotalC == "22"
    assert r.total_ripla == 22


def test_remainder_counts_the_last_unit_separately(conn):
    """割り切れないとき: 満載の台 + 端数の台を別に数える

    検入数 11、積数 5 → 台数 3(切り上げ)。
    仮台数 = 2、端数コイル = 11 - 2*5 - 1 = 0 → 8*2 + 0*2 = 16 本。
    最下部 3 × 3 = 9。合計 25。
    """
    s, r = run(conn, 検入数=11, 枚数=5, 枚数範囲="以下", 下本数="3")
    assert r.ok
    assert s.台数 == "3"
    assert s.本数 == "16"
    assert s.TotalC == "25"


def test_hb_sheets_equal_unit_count(conn):
    """緩衝材の枚数は台数ぶん"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下")
    assert s.HB枚数 == "2"


def test_non_ripla_spacer_stops_early(conn):
    """コイル間がリプラ / LVS 以外なら、緩衝材だけ数えて終わり"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下",
               コイル間スペーサー="ザラ板", 最下部スペーサー="ザラ板")
    assert r.ok
    assert r.total_ripla == 0
    assert s.HB枚数 == "4"          # 台数 2 × 2
    assert s.リプラ長さ == ""


def test_size_80_is_fixed_length(conn):
    """角サイズ 80 は長さを持たない。積数 × 2 × 台数"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下", リプラサイズ="80")
    assert r.ok
    assert r.total_ripla == 20      # 5 * 2 * 2
    assert s.HB枚数 == "2"
    assert s.リプラ長さ == ""


def test_spacer_kinds_differ_is_flagged(conn):
    """間リプラと最下部リプラの種類が違えば印を立てる (2026.02.25)"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下",
               コイル間スペーサー="リプラ", 最下部スペーサー="LVS")
    assert models.FLAG_SPACER_DIFFERS in s.flags


# ==================================================================
# 特殊品
# ==================================================================
def test_kyohou_interleaf_drops_coil_spacers(conn):
    """コイル間は間紙入「〇」: コイル間のリプラを出さず、緩衝材は外装紙"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下",
               下本数="3", コイル間は間紙入="〇")
    assert r.ok
    assert s.リプラ長さ == ""
    assert s.本数 == ""
    assert s.緩衝材 == "外装紙"
    assert r.total_ripla == 6       # 下本数 3 × 台数 2


def test_間紙入はVBAが止まる先も書いてあるとおり計算する(conn):
    """不明点 Q23(回答済み): 移植は止めずに続きを計算する。

    VBA は `.本数 = ""` の直後の `中間本数 = .本数`(Long)で
    「型が一致しません」になり、HB枚数とトータル本数を出さない。
    移植は、そこから先に書いてあるとおりに出す。
    """
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下",
               下本数="3", コイル間は間紙入="〇")
    assert r.ok
    assert s.本数 == ""                 # VBA が空にした欄はそのまま空
    assert s.HB枚数 == s.台数           # HB数 = 台数(ReFlag なし)
    assert s.TotalC == "6"              # 下本数 3 × 台数 2
    assert r.total_ripla == 6


def test_imi_cover_uses_three_lengths(conn):
    """1C1282 ｱｲｴﾑｱｲｶﾊﾞｰ: リプラ3種、中間本数を総数へ加える"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下",
               spec_no="1C1282", 下本数="3")
    assert r.ok
    assert s.特殊Flag == special_rules.IMI_COVER_FLAG
    assert s.リプラ長さ == "800"
    assert s.最下部長さ_短 == "600"
    assert s.IMI中間長さ == "950"
    assert s.IMI中間本数 == "4"     # 台数 2 × 2
    assert s.短い本数 == "4"
    # 本数 16 + 長い 6 + 短い 4 + 中間 4 = 30
    assert r.total_ripla == 30


def test_kotobuki_sets_flag(conn):
    """1C1297 ｺﾄﾌﾞｷｾｲﾐﾂ: 帳票を2行に分ける印だけ立つ"""
    s, r = run(conn, 検入数=10, 枚数=5, 枚数範囲="以下", spec_no="1C1297")
    assert s.特殊Flag == special_rules.KOTOBUKI_FLAG


# ==================================================================
# 図形
# ==================================================================
@pytest.mark.parametrize("kind,count,bars", [
    ("EXPS", 3, (True, True, True)),
    ("EXPS", 2, (True, False, True)),     # 真ん中が抜ける
    ("EXPS", 0, (False, False, False)),
    ("全面", 3, (True, True, True)),
])
def test_shape_view(kind, count, bars):
    view = ripla_service.shape_view(kind, count)
    assert view.kind == kind
    assert view.bars == bars


def test_shape_hidden_for_other_pallet_types():
    assert ripla_service.shape_view("スカシ", 3).kind == ""


def test_ripla_size_label():
    assert ripla_service.ripla_size_label("30") == "30×40"
    assert ripla_service.ripla_size_label("40") == "40×60"
    assert ripla_service.ripla_size_label("80") == "80×80"
