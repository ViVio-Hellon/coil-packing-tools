"""VBA の数値の振る舞いが移植できているか"""
from modules.packing_material_calculation.coil_tool import vba


def test_val_reads_leading_number():
    assert vba.val("12abc") == 12
    assert vba.val("") == 0
    assert vba.val(None) == 0
    assert vba.val("1050") == 1050
    assert vba.val("可変") == 0


def test_is_numeric_treats_no_data_as_not_numeric():
    # 「データなし」は数値でない = 指定なし。台数計算の優先順位がこれに乗る
    assert not vba.is_numeric("データなし")
    assert vba.is_numeric("500")
    assert not vba.is_numeric("")


def test_round_half_even_is_bankers_rounding():
    # VBA Round(x, 0) は偶数丸め。四捨五入にすると台数が動く
    assert vba.round_half_even(2.5) == 2
    assert vba.round_half_even(3.5) == 4
    assert vba.round_half_even(2.4) == 2


def test_round_up_and_down():
    assert vba.round_up(2.1) == 3
    assert vba.round_up(2.0) == 2
    assert vba.round_down(2.9) == 2


def test_num_equal_compares_at_single_precision():
    # 0.8 は2進で割り切れない。倍精度のままだと規則が当たらなくなる
    assert vba.num_equal(0.8, 0.8)
    assert vba.num_equal("0.800", 0.8)
    assert vba.num_equal(43.7, 43.7)
    assert vba.num_equal(107.8, 107.8)
    assert not vba.num_equal(0.8, 0.9)
