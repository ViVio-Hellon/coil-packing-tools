# -*- coding: utf-8 -*-
"""印刷の字・罫線・バー・目盛りは、紙の端から 5mm 以上内側に置く(統合版で直した)。

プリンターは紙の縁 約 4mm に刷れない。ブラウザの印刷プレビューは紙の端まで描くので、
画面で収まって見えても紙では欠ける(現場の話: 「印刷しても見切れる」)。

【見切れていた理由】
- 風袋計算・羅列・全サイズ: `app.css` に印刷用の `@page` が 2 つあり、後ろにある
  ラベル台紙用の `margin:0` が前の `margin:8mm` を打ち消していた。中身が紙の端から
  刷られていた(移植元のまま)。印刷ダイアログの「余白なし」でも同じことが起きる
- 位置合わせの試し刷り: 目盛り・段の見出し・枠の番号・説明文が紙の縁に置かれていた

距離は `@page` の余白ではなく**紙の中**で取る(梱包明細・資材計算と同じ決まり)。
統合版の本物の紙面を PDF にして測った結果は `tools/print_edge_check.py`。
"""

import os
import re
import unittest

from modules.packing_pena_label.app.services import label_sheet as LS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK = os.path.join(ROOT, "data", "label_stock.json")
CSS = os.path.join(ROOT, "app", "static", "css", "app.css")
SAFE = 5.0


def _css() -> str:
    with open(CSS, encoding="utf-8") as f:
        return f.read()


def _rule(css: str, selector: str) -> str:
    """セレクタの中身(最初に見つかったもの)。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def _mm(body: str, prop: str) -> float:
    m = re.search(r"(?:^|[;\s])" + re.escape(prop) + r"\s*:\s*([\d.]+)mm", body)
    assert m, prop
    return float(m.group(1))


class TestReportViews(unittest.TestCase):
    """風袋計算・羅列・全サイズ(と画面からの「このまま印刷」)。"""

    def test_every_page_rule_has_zero_margin(self):
        """`@page` の余白に頼らない。**どれか 1 つでも 0 だと全部 0 になる**(同じファイル)。"""
        rules = re.findall(r"@page\s*\{([^}]*)\}", _css())
        self.assertTrue(rules)
        for body in rules:
            self.assertRegex(body, r"margin\s*:\s*0\b", body)

    def test_distance_is_taken_inside_the_paper(self):
        css = _css()
        start = css.index("/* ---------- 印刷 ---------- */")
        block = css[start:css.index("}\n", css.index("@page{size:A4; margin:0}", start))]
        body = _rule(block, ".page")
        self.assertGreaterEqual(_mm(body, "padding"), SAFE)
        # 2 枚目以降の紙の上端・下端も同じだけ空ける
        self.assertIn("box-decoration-break:clone", body)
        self.assertIn("-webkit-box-decoration-break:clone", body)

    def test_label_sheets_are_not_shifted(self):
        """ラベル台紙・較正シートは mm の実寸で紙に置く。中で空けるとずれる。"""
        self.assertIn(".page:has(.sheet){padding:0}", _css())


class TestCalibrationSheet(unittest.TestCase):
    """位置合わせの試し刷り。

    台紙の切れ目を示す点線の枠・段の赤い線・紙の端から十字までの道しるべは、
    紙の端と重なる位置そのものが意味を持つ目印なので、位置を変えない(縁の分は刷れない)。
    **文字・目盛りは紙の端から 5mm 以上内側。**
    """

    def setUp(self):
        self.stock = LS.get_stock(STOCK)
        self.html = LS.render_ruler_page(self.stock)

    def _positions(self, cls: str, prop: str):
        pat = r'<div class="%s[^"]*" style="[^"]*%s:([\d.]+)mm' % (re.escape(cls), prop)
        return [float(v) for v in re.findall(pat, self.html)]

    def test_ticks_are_inside(self):
        m = self.stock.safe_margin
        xs = self._positions("tick h", "left")
        ys = self._positions("tick v", "top")
        self.assertTrue(xs and ys)
        for x in xs:
            self.assertTrue(m <= x <= self.stock.page_w - m, x)
        for y in ys:
            self.assertTrue(m <= y <= self.stock.page_h - m, y)
        # 目盛りの線は紙の端から 5mm の所から引く(位置 = 端から何 mm は変えない)
        css = _css()
        self.assertGreaterEqual(_mm(_rule(css, ".sheet.ruler .tick.h"), "top"), SAFE)
        self.assertGreaterEqual(_mm(_rule(css, ".sheet.ruler .tick.v"), "left"), SAFE)

    def test_tick_labels_are_inside(self):
        for v in self._positions("tlab", "left") + self._positions("tlab", "top"):
            self.assertGreaterEqual(v, self.stock.safe_margin)

    def test_row_labels_are_inside(self):
        tops = [float(v) for v in re.findall(r'<div class="rowlab" style="top:([\d.]+)mm', self.html)]
        self.assertEqual(len(tops), self.stock.rows + 1)
        m = self.stock.safe_margin
        self.assertGreaterEqual(min(tops), m + LS.TICK_BAND, "1段目の見出しが目盛りに重なる")
        self.assertLessEqual(max(tops) + LS.ROWLAB_H, self.stock.page_h - m,
                             "最後の段の見出しが紙の下の縁に入る")
        self.assertGreaterEqual(_mm(_rule(_css(), ".rowlab"), "right"), SAFE)

    def test_frame_numbers_and_info_are_inside(self):
        css = _css()
        self.assertGreaterEqual(_mm(_rule(css, ".sheet.ruler .gno"), "left"), SAFE)
        gxy = _rule(css, ".sheet.ruler .gxy")
        self.assertGreaterEqual(_mm(gxy, "right"), SAFE)
        self.assertGreaterEqual(_mm(gxy, "bottom"), SAFE)
        info = _rule(css, ".calinfo")
        for prop in ("left", "right", "bottom"):
            self.assertGreaterEqual(_mm(info, prop), SAFE, prop)

    def test_info_last_line_is_short(self):
        """説明文の最後の行は台紙の名前だけ(右下の「N段ぶん」と重ならない)。"""
        info = self.html[self.html.index('<div class="calinfo">'):]
        self.assertRegex(info, r"<br>台紙 [^<]*</div>")

    def test_labels_themselves_still_pass(self):
        """ラベルそのもの(文字・罫線・バー)の決まりは元から守られている。"""
        self.assertEqual(LS.ink_outside_safe_area(self.stock), [])


if __name__ == "__main__":
    unittest.main()
