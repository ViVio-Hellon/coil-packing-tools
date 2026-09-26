"""1本積み不可の端数処理と、高さ制限による再計算"""
import pytest

from modules.packing_material_calculation.coil_tool import calc_service, models
from modules.packing_material_calculation.coil_tool.models import CalcState, OrderInfo
from modules.packing_material_calculation.tests import _fixture


@pytest.fixture
def conn():
    c = _fixture.make_db()
    _fixture.standard_master(c)
    yield c
    c.close()


def run(conn, *, 検入数, 枚数, 枚数範囲="1不可", 単重="100.00", 外径="900",
        spec_no="1C0001", **spec_over):
    _fixture.add_spec(conn, spec_no, 枚数範囲=枚数範囲, **spec_over)
    s = CalcState(検入数=str(検入数), 外径=外径)
    s.order = OrderInfo(包装仕様NO=spec_no, 受注板厚="1.000", 受注板幅="100.0",
                        製品単重=単重, 梱包単位_重量=OrderInfo.NO_DATA,
                        梱包単位_枚数=str(枚数))
    return s, calc_service.calculate(conn, s)


# ==================================================================
# 1本積み不可
# ==================================================================
def test_no_single_flag_is_raised(conn):
    s, r = run(conn, 検入数=10, 枚数=5)
    assert r.ok
    assert models.FLAG_NO_SINGLE in s.flags


def test_split_into_two_when_only_two_units(conn):
    """x = 1 のとき: 2台へ均等に分ける (2026.1.16)

    検入数 5、積数 4 → Round(5/4) = 1、5 - 1*4 = 1 で1本余る。
    2台に均等(2.5 → 2)で分ける。
    """
    s, r = run(conn, 検入数=5, 枚数=4)
    assert r.ok
    assert s.台数 == "2"
    assert s.積数 == "2"          # 5 / 2 = 2.5 → int_text で 2
    assert s.Re_台数 == ""        # 別枠は使わない
    assert models.FLAG_SINGLE_SPLIT in s.flags


def test_split_uses_spare_when_more_than_two_units(conn):
    """x >= 2 のとき: 1台戻して、その分を2台に割る

    検入数 13、積数 4 → Round(13/4) = 3(偶数丸め)、13 - 3*4 = 1。
    仮台数 3 から1台戻して 2台、残り 13 - 2*4 = 5 を2台で分ける。
    """
    s, r = run(conn, 検入数=13, 枚数=4)
    assert r.ok
    assert s.台数 == "2"
    assert s.積数 == "4"
    assert s.Re_積数 == "2"       # (13 - 8) / 2 = 2.5 → 2
    assert s.Re_台数 == "2"


def test_no_split_when_remainder_is_not_one(conn):
    """余りが1本でなければ、ふつうに切り上げ"""
    s, r = run(conn, 検入数=14, 枚数=4)
    assert r.ok
    assert s.台数 == "4"          # 切り上げ(14/4) = 4
    assert s.Re_台数 == ""
    assert models.FLAG_SINGLE_SPLIT not in s.flags


def test_no_split_without_the_flag(conn):
    """1本不可の指定が無ければ、余り1本でもそのまま"""
    s, r = run(conn, 検入数=5, 枚数=4, 枚数範囲="以下")
    assert r.ok
    assert s.台数 == "2"
    assert models.FLAG_SINGLE_SPLIT not in s.flags


# ==================================================================
# 高さ制限
# ==================================================================
def test_height_limit_reduces_stack(conn):
    """重量で決めた積数が指定高さを超えたら、高さから積数を取り直す

    重量 1000kg / 単重 100kg → 積数 10。
    リプラ 30 + 幅 100 = 130mm。10段で 1300mm。
    梱包総高さ 800「以下」→ 上限 800 - 210 = 590 → 590/130 = 4.53 → 4段。
    """
    _fixture.add_spec(conn, "1C0002", リプラサイズ="30",
                      梱包総高さ="800", 高さ範囲="以下")
    s = CalcState(検入数="20", 外径="900")
    s.order = OrderInfo(包装仕様NO="1C0002", 受注板厚="1.000", 受注板幅="100.0",
                        製品単重="100.00", 梱包単位_重量="1000",
                        梱包単位_枚数=OrderInfo.NO_DATA)
    r = calc_service.calculate(conn, s)
    assert r.ok
    assert s.積数 == "4"
    assert s.台数 == "5"          # 切り上げ(20/4)
    assert models.FLAG_HEIGHT in s.flags
    assert s.総高さ == "730"      # 4 * 130 + 210


def test_height_limit_skipped_when_count_specified(conn):
    """枚数の指定があれば高さ判定は丸ごと飛ばす"""
    _fixture.add_spec(conn, "1C0002", リプラサイズ="30",
                      梱包総高さ="400", 高さ範囲="以下")
    s = CalcState(検入数="20", 外径="900")
    s.order = OrderInfo(包装仕様NO="1C0002", 受注板厚="1.000", 受注板幅="100.0",
                        製品単重="100.00", 梱包単位_重量=OrderInfo.NO_DATA,
                        梱包単位_枚数="10")
    r = calc_service.calculate(conn, s)
    assert r.ok
    assert s.積数 == "10"         # 高さで減らされない
    assert models.FLAG_HEIGHT not in s.flags


def test_lid_excluded_subtracts_only_eighty(conn):
    """「蓋除き」はパレット上分 80mm だけ引く"""
    _fixture.add_spec(conn, "1C0003", リプラサイズ="30",
                      梱包総高さ="800", 高さ範囲="蓋除き")
    s = CalcState(検入数="20", 外径="900")
    s.order = OrderInfo(包装仕様NO="1C0003", 受注板厚="1.000", 受注板幅="100.0",
                        製品単重="100.00", 梱包単位_重量="1000",
                        梱包単位_枚数=OrderInfo.NO_DATA)
    r = calc_service.calculate(conn, s)
    assert r.ok
    # 上限 800 - 80 = 720 → 720/130 = 5.53 → 5
    assert s.積数 == "5"


def test_pallet_width_caps_stack_when_weight_only(conn):
    """高さ指定が無く重量だけのとき、パレット幅から1本分引いた高さが上限

    パレットサイズ 900、リプラ 30 + 幅 100 = 130 → 限度高 = 770。
    重量 2000/100 = 20段 → 20*130 = 2600 > 770 → 770/130 = 5.9 → 5段。
    """
    _fixture.add_spec(conn, "1C0004", リプラサイズ="30")
    s = CalcState(検入数="20", 外径="900")
    s.order = OrderInfo(包装仕様NO="1C0004", 受注板厚="1.000", 受注板幅="100.0",
                        製品単重="100.00", 梱包単位_重量="2000",
                        梱包単位_枚数=OrderInfo.NO_DATA)
    r = calc_service.calculate(conn, s)
    assert r.ok
    assert s.積数 == "5"
    assert models.FLAG_HEIGHT in s.flags
