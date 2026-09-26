"""計算Start の一連 ── パレット → 積数・台数 → リプラ員数"""
import pytest

from modules.packing_material_calculation.coil_tool import calc_service, models, pallet_service, vba
from modules.packing_material_calculation.coil_tool.models import CalcState, OrderInfo
from modules.packing_material_calculation.tests import _fixture


@pytest.fixture
def conn():
    c = _fixture.make_db()
    _fixture.standard_master(c)
    yield c
    c.close()


def order(**over) -> OrderInfo:
    base = dict(
        受注番号="12345678", 包装仕様NO="1C0001",
        受注板厚="1.000", 受注板幅="100.0", 製品単重="100.00",
        梱包単位_重量=OrderInfo.NO_DATA, 梱包単位_枚数=OrderInfo.NO_DATA,
        用途名="試験", 営業納期="04/01",
    )
    base.update(over)
    return OrderInfo(**base)


def state_for(conn, spec_no="1C0001", 検入数="10", 外径="900", **ov) -> CalcState:
    s = CalcState(LOT="A123456", 検入数=検入数, 外径=外径)
    s.order = order(包装仕様NO=spec_no, **ov)
    return s


# ==================================================================
def test_order_weight_takes_priority_and_rounds_down(conn):
    """1段目: オーダーの重量指定。切り捨て(本体は RoundDown)"""
    _fixture.add_spec(conn, "1C0001", 梱包単位_枚数="5", 枚数範囲="以下")
    s = state_for(conn, 検入数="10", 梱包単位_重量="350", 製品単重="100.00")
    r = calc_service.calculate(conn, s)
    assert r.ok
    # 350 / 100 = 3.5 → 切り捨て 3
    assert s.積数 == "3"
    # 10 / 3 = 3.33 → 切り上げ 4
    assert s.台数 == "4"
    assert models.FLAG_WEIGHT in s.flags


def test_order_count_beats_spec(conn):
    """2段目: オーダーの枚数指定。包装仕様より強い"""
    _fixture.add_spec(conn, "1C0001", 梱包単位_枚数="9", 枚数範囲="以下")
    s = state_for(conn, 検入数="10", 梱包単位_枚数="4")
    r = calc_service.calculate(conn, s)
    assert r.ok
    assert s.積数 == "4"
    assert s.台数 == "3"
    assert models.FLAG_COUNT in s.flags


def test_spec_fallback_is_shadowed_by_the_2023_guard(conn):
    """3〜5段目は**通らない**。2023.3.27 に足されたガードが先に断るため。

    `フォーム展開` はオーダーの梱包単位に「数値」か「データなし」しか
    入れない。どちらも数値でないなら必ず両方「データなし」になり、
    5段の手前のガード(引当データなし)で止まる。

    つまり **包装仕様マスタの梱包単位は、いまの経路では使われない。**
    VBA の意図とは食い違って見えるが、勝手に直すと台数が変わるので
    現行どおりにしてある(`docs/不明点.md` Q13)。
    """
    _fixture.add_spec(conn, "1C0001", 梱包単位_重量="300", 重量範囲="程度")
    s = state_for(conn, 検入数="10", 製品単重="100.00")
    r = calc_service.calculate(conn, s)
    assert not r.ok
    assert r.reason == "no_allocation_data"
    # 包装仕様には 300kg「程度」が入っているのに、積数は出ない
    assert s.積数 == ""


def test_spec_fallback_reachable_only_with_non_numeric_order_value(conn):
    """片方が「データなし」以外の非数値なら、3段目へ届く。

    実データでは起きにくいが、経路そのものは生きている。
    上のガードが外れたときに何が起きるかを、ここで押さえておく。
    """
    _fixture.add_spec(conn, "1C0001", 梱包単位_重量="300", 重量範囲="程度")
    s = state_for(conn, 検入数="10", 製品単重="100.00")
    s.order.梱包単位_枚数 = "指定なし"      # 「データなし」ではない非数値
    r = calc_service.calculate(conn, s)
    assert r.ok
    # 300 + 30 = 330 / 100 = 3.3 → 切り捨て 3
    assert s.積数 == "3"
    assert models.FLAG_WEIGHT in s.flags


def test_zero_stack_is_refused_instead_of_crashing(conn):
    """積数が 0 になったら断る。VBA はここでゼロ除算で落ちていた"""
    _fixture.add_spec(conn, "1C0001")
    s = state_for(conn, 検入数="10", 梱包単位_枚数="0")
    r = calc_service.calculate(conn, s)
    assert not r.ok
    assert r.reason == "zero_stack"


def test_no_allocation_data_is_refused(conn):
    """両方「データなし」なら計算できない"""
    _fixture.add_spec(conn, "1C0001")
    s = state_for(conn)
    r = calc_service.calculate(conn, s)
    assert not r.ok
    assert r.reason == "no_allocation_data"
    assert any("引当データなし" in m for m in s.messages)


def test_missing_inspection_count_is_refused(conn):
    _fixture.add_spec(conn, "1C0001")
    s = state_for(conn, 検入数="")
    r = calc_service.calculate(conn, s)
    assert not r.ok
    assert r.reason == "no_inspection_count"


# ==================================================================
def test_pallet_size_picks_smallest_that_fits(conn):
    """外径が収まるうち、いちばん小さいパレット"""
    _fixture.add_spec(conn, "1C0001", 梱包単位_枚数="2", 枚数範囲="以下")
    s = state_for(conn, 外径="850")
    calc_service.calculate(conn, s)
    assert s.パレット種類 == "スカシ"
    assert s.パレットサイズ == "900"
    assert s.パレット名称 == "P09"


def test_no_pallet_fits_is_reported(conn):
    _fixture.add_spec(conn, "1C0001", 梱包単位_枚数="2", 枚数範囲="以下")
    s = state_for(conn, 外径="9000")
    calc_service.calculate(conn, s)
    assert any("収まる" in m for m in s.messages)



# ==================================================================
# 不明点 Q24(回答済み): 負の積数・台数は出さずに断る
# ==================================================================
def test_ﾎｲｰﾙで収まるパレットが無いときは負の台数を出さずに断る(conn):
    """VBA は「収まるパレットがDB上にありません」と知らせたあと計算を
    続け、積数-1・台数-5・HB枚数-10 を出した(2026-09-23、疑似ロット Q1201A0)。

    マスタのﾎｲｰﾙのパレット(H1)は巾の上下限が空欄で、どの外径も収まらない。
    **負の台数をチェックリストへ積めてしまうほうが危ない**ので、移植は断る。
    """
    _fixture.add_pallet(conn, "ﾎｲｰﾙ", 720, "H1", 巾下限=0, 巾上限=0, 丈下限=0, 丈上限=0)
    _fixture.add_spec(conn, "1C1201", パレット="ﾎｲｰﾙ", リプラサイズ="",
                      コイル間スペーサー="外装紙", 最下部スペーサー="外装紙",
                      下本数="", 梱包単位_重量="500", 重量範囲="max",
                      梱包総高さ="1000", 高さ範囲="以下")
    s = state_for(conn, spec_no="1C1201", 検入数="5", 外径="900",
                  梱包単位_重量="1000", 製品単重="150.00",
                  受注板厚="0.100", 受注板幅="400.0")
    r = calc_service.calculate(conn, s)

    assert not r.ok
    # **積数が0以下になったからではなく、パレットが無いから断る。**
    # この値だと高さには余裕があり、VBA も移植も積数は正になる
    assert r.reason == pallet_service.REASON_NO_PALLET
    assert any("収まる" in m for m in s.messages)       # パレットが無いと言う
    for value in (s.積数, s.台数, s.HB枚数, s.Re_台数):
        assert not value.startswith("-"), f"負の値を出しています: {value}"
    assert s.台数 == ""                                  # 台数を出さない


def test_どの計算でも積数と台数は負にならない(conn):
    """断るべきところで断っていれば、負の値は画面に出ない。
    積数が0以下になる道(重量・高さ)は、どちらも断る。"""
    for 単重 in ("100.00", "5000.00"):
        for 重量 in ("350", "10"):
            _fixture.add_spec(conn, "1C0001", 梱包総高さ="300", 高さ範囲="以下")
            s = state_for(conn, 検入数="7", 梱包単位_重量=重量, 製品単重=単重)
            calc_service.calculate(conn, s)
            for value in (s.積数, s.台数, s.Re_積数, s.Re_台数):
                assert not str(value).startswith("-"), (単重, 重量, value)



def test_ﾎｲｰﾙでもパレットが収まれば計算する(conn):
    """ﾎｲｰﾙはＷだけで見る(VBA `PalletSize` の Else の枝)。H1 は W720 なので
    外径720以下なら収まる。断るのは収まらないときだけ。"""
    _fixture.add_pallet(conn, "ﾎｲｰﾙ", 720, "H1", 巾下限=0, 巾上限=0, 丈下限=0, 丈上限=0)
    _fixture.add_spec(conn, "1C1201", パレット="ﾎｲｰﾙ", リプラサイズ="",
                      コイル間スペーサー="外装紙", 最下部スペーサー="外装紙", 下本数="")
    s = state_for(conn, spec_no="1C1201", 検入数="5", 外径="700",
                  梱包単位_重量="1000", 製品単重="150.00")
    r = calc_service.calculate(conn, s)
    assert r.ok, s.messages
    assert s.パレット名称 == "H1"


def test_積数を手で直したときもﾎｲｰﾙでパレットが無ければ断る(conn):
    _fixture.add_pallet(conn, "ﾎｲｰﾙ", 720, "H1")
    _fixture.add_spec(conn, "1C1201", パレット="ﾎｲｰﾙ", リプラサイズ="",
                      コイル間スペーサー="外装紙", 最下部スペーサー="外装紙", 下本数="")
    s = state_for(conn, spec_no="1C1201", 検入数="5", 外径="900")
    s.積数 = "2"
    r = calc_service.recalculate_from_stack(conn, s)
    assert not r.ok
    assert r.reason == pallet_service.REASON_NO_PALLET
    assert s.台数 == ""
