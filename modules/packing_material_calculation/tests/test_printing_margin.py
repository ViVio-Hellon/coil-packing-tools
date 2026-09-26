"""印刷の字・罫線は、紙の端から 5mm 以上内側に置く。

プリンタは紙の縁から約4mmには刷れない。ブラウザの印刷プレビューは紙の
端まで描くので、画面で収まって見えても紙では欠ける(現場の話、2026-09-25)。

距離は `@page` の余白ではなく紙の中(`.sheet` の padding)で取る。
`@page` の余白は印刷ダイアログの「余白: なし」で消えてしまうため
(Chromium で PDF にして測った: `@page` で取っていたときは端から 0mm)。
"""
import pytest

from modules.packing_material_calculation.coil_tool import printing, reports
from modules.packing_material_calculation.tests.test_checklist_and_order import _sheet


def _all_reports():
    return {
        "チェックリスト": reports.build_checklist_report([], line="機側"),
        "発注票": reports.build_order_sheet_report([_sheet("30×40"), _sheet("40×60")]),
    }


def test_端から5mmより近くには置けない():
    with pytest.raises(ValueError):
        printing.PageSetup(margin_mm=4.9)
    printing.PageSetup(margin_mm=printing.SAFE_MARGIN_MM)     # ちょうどは可


@pytest.mark.parametrize("name", list(_all_reports()))
def test_どの帳票も紙の中で5mm以上空ける(name):
    rep = _all_reports()[name]
    assert rep.setup.margin_mm >= printing.SAFE_MARGIN_MM
    html = printing.render_html(rep)
    # ダイアログの余白に左右されないよう @page は 0、距離は紙の中で取る
    assert "@page { size: A4 landscape; margin: 0; }" in html
    assert html.count("@page") == 1
    assert f".sheet {{ padding: {rep.setup.margin_mm:g}mm;" in html
    # 紙をまたいでも次の紙の上端で同じだけ空ける
    assert "box-decoration-break: clone" in html


def test_チェックリストは縮めて1枚に収める():
    """VBA `FitToPagesTall = 1`。30行(15行 × 2段)でも1枚。
    縮めるのは中身だけで、紙の中の余白(端からの距離)は縮めない。"""
    html = printing.render_html(reports.build_checklist_report([], line="機側"))
    assert '<div class="sheet sheet--fit"><div class="fit">' in html
    # 刷れる高さ = 210 - 2 × (余白6 + 「最小」を選ばれたときのプリンタの縁4)
    assert "var avail = 190 * 96 / 25.4" in html


def test_発注票は縮めない():
    """1票は必ず左半分に収まる寸法で組んである。縮めると原紙と違う大きさになる。"""
    html = printing.render_html(reports.build_order_sheet_report([_sheet("30×40")]))
    assert "sheet--fit" not in html.split("<body>")[1]
    assert "var avail" not in html
