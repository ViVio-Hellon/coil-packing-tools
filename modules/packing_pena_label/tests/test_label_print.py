# -*- coding: utf-8 -*-
"""ラベル印刷（実寸レイアウト）とバーコードの検証。

Excel 台紙はバーコードフォントと手調整の行高に依存していた。
移植版は寸法をデータ化し、mm 絶対配置＋自前バーコードで組む。
"""

import os
import re
import sys
import tempfile
import unittest

from html.parser import HTMLParser

from modules.packing_pena_label.app.services import barcode39 as BC
from modules.packing_pena_label.app.services import label_sheet as LS


class _Attrs(HTMLParser):
    """指定クラスの要素の属性を、**ブラウザーと同じ解釈で**取り出す。

    style 属性の中の ``&quot;`` は属性値なので実体参照が戻る。
    文字列検索だけで確かめると、属性が途中で切れていても気付けない
    （``style="font-family:"X""`` は style が空で終わっている）。
    """

    def __init__(self, want_class):
        super().__init__(convert_charrefs=True)
        self.want = want_class
        self.found = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if self.want in (d.get("class") or "").split():
            self.found.append(d)

    @classmethod
    def of(cls, html, want_class):
        p = cls(want_class)
        p.feed(html)
        return p.found


def _style_of(html, want_class):
    got = _Attrs.of(html, want_class)
    return got[0].get("style", "") if got else ""

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK = os.path.join(ROOT, "data", "label_stock.json")


class TestCode39(unittest.TestCase):
    def test_vba_strings_are_encodable(self):
        """VBA が作る 2 種類のバーコード文字列が Code 39 で表せる。"""
        for t in ("*BJB7606500QR*", "*W111111-0101 10*",
                  "*BJB7603900QR*", "*W111111-0132 12.5*"):
            with self.subTest(text=t):
                self.assertTrue(BC.encodable(t), BC.unsupported_chars(t))

    def test_asterisks_are_start_stop_not_data(self):
        """VBA の ``*`` はスタート/ストップ記号。データとして二重に入れない。"""
        self.assertEqual(BC.normalize("*ABC*"), "ABC")
        self.assertEqual(BC.normalize("ABC"), "ABC")
        # 前後を剥がしても剥がさなくても同じ本数になる
        self.assertEqual(BC.modules("*ABC*"), BC.modules("ABC"))

    def test_lowercase_is_upcased(self):
        self.assertEqual(BC.normalize("*w111111-0101*"), "W111111-0101")

    def test_space_and_hyphen_supported(self):
        """検番バーコードは ``W111111-0101 10`` の形。空白と - が必要。"""
        self.assertTrue(BC.encodable("*W111111-0101 10*"))

    def test_unsupported_reported(self):
        self.assertEqual(BC.unsupported_chars("*あ*"), ["あ"])
        with self.assertRaises(BC.Code39Error):
            BC.svg("*あ*", 50, 10)

    def test_svg_has_bars_and_fits_box(self):
        svg = BC.svg("*W111111-0101 10*", 90.0, 12.0)
        self.assertIn('width="90.0000mm"', svg)
        self.assertIn('height="12.0000mm"', svg)
        # 背景の白 rect を拾わないよう、黒バーだけに絞る
        bars = re.findall(
            r'<rect x="([\d.]+)" y="0" width="([\d.]+)" height="[\d.]+" fill="#000"',
            svg)
        self.assertGreater(len(bars), 20, "バーが描かれていない")
        last_x, last_w = float(bars[-1][0]), float(bars[-1][1])
        self.assertLessEqual(last_x + last_w, 90.0 + 0.01, "バーが枠を超えている")

    def test_quiet_zone_present(self):
        svg = BC.svg("*ABC*", 60.0, 10.0, quiet_mm=3.0)
        first_x = float(re.search(
            r'<rect x="([\d.]+)" y="0" width="[\d.]+" height="[\d.]+" fill="#000"',
            svg).group(1))
        self.assertGreaterEqual(first_x, 3.0 - 0.01, "静止帯が無い")

    def test_empty_returns_empty(self):
        self.assertEqual(BC.svg("", 50, 10), "")


class TestStockLoading(unittest.TestCase):
    def test_bundled_stock_loads(self):
        """実寸 105×29.7mm の 20 面（2列×10段）。"""
        s = LS.get_stock(STOCK)
        self.assertTrue(s.fields, "ラベルのフィールド定義が空")
        self.assertTrue(s.header_fields, "ヘッダーのフィールド定義が空")
        self.assertEqual((s.cols, s.rows), (2, 10))
        self.assertEqual((s.label_w, s.label_h), (105.0, 29.7))
        self.assertEqual((s.pitch_x, s.pitch_y), (105.0, 29.7))
        self.assertEqual((s.origin_x, s.origin_y), (0.0, 0.0))
        # 1段目ヘッダー + 8段ラベル = 16枚 / 10段目は余り
        self.assertEqual(s.header_rows, 1)
        self.assertEqual(s.label_rows, 8)
        self.assertEqual(s.per_page, 16)

    def test_divides_a4_exactly(self):
        """A4 を 2×10 に均等分割している。"""
        s = LS.get_stock(STOCK)
        self.assertAlmostEqual(s.label_w * s.cols, s.page_w, places=6)
        self.assertAlmostEqual(s.label_h * s.rows, s.page_h, places=6)

    def test_labels_start_below_header(self):
        s = LS.get_stock(STOCK)
        self.assertAlmostEqual(s.label_origin_y(),
                               s.origin_y + s.pitch_y * s.header_rows, places=6)

    def test_fits_a4(self):
        """台紙定義が A4 に収まっていること。"""
        s = LS.get_stock(STOCK)
        right = s.origin_x + s.pitch_x * (s.cols - 1) + s.label_w
        bottom = s.label_origin_y() + s.pitch_y * (s.label_rows - 1) + s.label_h
        self.assertLessEqual(right, s.page_w, "横がはみ出す")
        self.assertLessEqual(bottom, s.page_h, "縦がはみ出す")

    def test_positions(self):
        s = LS.get_stock(STOCK)
        y0 = s.label_origin_y()
        self.assertEqual(s.position(0), (0, s.origin_x, y0))
        self.assertEqual(s.position(1), (0, s.origin_x + s.pitch_x, y0))
        self.assertEqual(s.position(2), (0, s.origin_x, y0 + s.pitch_y))
        # 17 枚目は次シートの先頭
        self.assertEqual(s.position(16), (1, s.origin_x, y0))
        # シート数はラベル件数で決まるので上限は設けない
        self.assertEqual(s.position(32), (2, s.origin_x, y0))
        self.assertIsNone(s.position(-1))

    def test_broken_json_falls_back(self):
        p = os.path.join(tempfile.mkdtemp(), "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ not json")
        s = LS.get_stock(p)
        self.assertIsNotNone(s)
        self.assertGreater(s.label_w, 0)

    def test_measured_values_kept(self):
        """Excel 台紙の採寸値を参考として残してある。"""
        s = LS.get_stock(STOCK)
        samples = s.measured.get("excelPitchYSamplesMm")
        self.assertTrue(samples)
        self.assertNotEqual(min(samples), max(samples),
                            "Excel 台紙の間隔は一定でなかったはず")
        self.assertEqual(s.measured.get("nominalMm"), [105.0, 30.0])


def _labels(n=32):
    return [{
        "kensaNo": "W111111", "coilNo": "-01%02d" % (i + 1),
        "kataban": "BJB7606500QR", "weight": "10",
        "barcodeKataban": "*BJB7606500QR*",
        "barcodeKensa": "*W111111-01%02d 10*" % (i + 1),
    } for i in range(n)]


class TestRender(unittest.TestCase):
    def setUp(self):
        self.stock = LS.get_stock(STOCK)

    def test_32_labels_make_2_pages(self):
        html = LS.render_pages(self.stock, _labels(32))
        self.assertEqual(html.count('class="sheet"'), 2)
        self.assertEqual(len(re.findall(r'class="label"', html)), 32)

    def test_page_size_is_stock_page(self):
        html = LS.render_pages(self.stock, _labels(1))
        m = re.search(r'class="sheet" style="width:([\d.]+)mm;height:([\d.]+)mm', html)
        self.assertEqual(float(m.group(1)), self.stock.page_w)
        self.assertEqual(float(m.group(2)), self.stock.page_h)

    def test_label_positions_follow_pitch(self):
        html = LS.render_pages(self.stock, _labels(4))
        pos = [(float(a), float(b)) for a, b in
               re.findall(r'class="label" style="left:([\d.]+)mm;top:([\d.]+)mm', html)]
        self.assertAlmostEqual(pos[1][0] - pos[0][0], self.stock.pitch_x, places=3)
        self.assertAlmostEqual(pos[2][1] - pos[0][1], self.stock.pitch_y, places=3)

    def test_barcode_caption_is_wrapped_in_asterisks(self):
        """バーの下の文字は ``*`` で挟む（VBA の文字列どおり。出荷先の求め）。"""
        html = LS.render_pages(self.stock, _labels(1))
        caps = re.findall(r'<text [^>]*>([^<]*)</text>', html)
        self.assertIn("*BJB7606500QR*", caps)
        self.assertIn("*W111111-0101 10*", caps)
        # 読取機が返す値（aria-label）は * を含まない中身
        self.assertIn('aria-label="BJB7606500QR"', html)

    def test_bars_start_and_end_with_the_asterisk_character(self):
        """バーの並びそのものが先頭・末尾とも Code 39 の * であること。"""
        els = BC._elements("BJB7606500QR", BC.DEFAULT_WIDE_RATIO)
        star = [BC.DEFAULT_WIDE_RATIO if k == "w" else 1.0
                for k in BC._PATTERNS["*"]]
        head = [w for _, w in els[:9]]
        tail = [w for _, w in els[-9:]]
        self.assertEqual(head, star)
        self.assertEqual(tail, star)

    def test_barcode_caption_is_readable_size(self):
        """バーコードの下の文字は 3.0mm（以前の 2.2mm は小さいと言われた）。"""
        html = LS.render_pages(self.stock, _labels(1))
        sizes = [float(v) for v in re.findall(
            r'<text [^>]*font-size="([\d.]+)"', html)]
        self.assertTrue(sizes, "バーコードの下に文字が出ていない")
        self.assertTrue(all(abs(v - 3.0) < 1e-6 for v in sizes), sizes)

    def test_barcode_caption_size_follows_stock_definition(self):
        """台紙定義の textSize で変えられること。"""
        import copy
        stock = copy.deepcopy(self.stock)
        for f in stock.fields:
            if f.get("type") == "barcode":
                f["textSize"] = 3.6
        html = LS.render_pages(stock, _labels(1))
        sizes = {float(v) for v in re.findall(
            r'<text [^>]*font-size="([\d.]+)"', html)}
        self.assertEqual(sizes, {3.6})

    def test_barcodes_drawn_not_font(self):
        """バーコードはフォントではなく SVG のバーで描く。"""
        html = LS.render_pages(self.stock, _labels(1))
        self.assertEqual(html.count('class="bc39"'), 2, "型番と検番の 2 本")
        self.assertIn('fill="#000"', html)

    def test_values_appear(self):
        html = LS.render_pages(self.stock, _labels(1))
        for v in ("W111111", "-0101", "BJB7606500QR", "10", "SEALING PLATE", "kg"):
            self.assertIn(v, html)

    def test_calibration_applies_transform(self):
        cal = LS.Calibration(offset_x_mm=1.5, offset_y_mm=-2.0, scale_pct=101.0)
        html = LS.render_pages(self.stock, _labels(1), cal)
        self.assertIn("translate(1.500mm,-2.000mm)", html)
        self.assertIn("scale(1.01000)", html)

    def test_no_transform_when_default(self):
        html = LS.render_pages(self.stock, _labels(1), LS.Calibration())
        self.assertNotIn("transform:", html)

    def test_empty_labels(self):
        self.assertIn("ありません", LS.render_pages(self.stock, []))

    def test_overflow_adds_pages(self):
        """1 シートに収まらなければシートを足す（枚数固定にしない）。"""
        html = LS.render_pages(self.stock, _labels(40))
        self.assertEqual(html.count('class="sheet"'), 3)


class TestHeaderBand(unittest.TestCase):
    """1 段目のヘッダー帯（サイズ名 / 検番 / 重量 / 本数・高さ・NW・GW）。"""

    HEAD = {
        "sizeName": "0.8mm\u00d753.5mm\u3000\u4e082",
        "kensaNo": "Z987654",
        "weightLabel": "\u91cd\u91cf\u4e082",
        "weight": "22.0",
        "coilH1": "11", "ta1": "910.0", "nw1": "242.0", "gw1": "279.0",
        "coilH2": "11", "ta2": "910.0", "nw2": "242.0", "gw2": "279.0",
    }

    def setUp(self):
        self.stock = LS.get_stock(STOCK)

    def _blocks(self, n, head=None):
        return [{"header": head if head is not None else self.HEAD,
                 "labels": _labels(n)}]

    def test_band_spans_full_sheet_width(self):
        html = LS.render_blocks(self.stock, self._blocks(1))
        m = re.search(r'class="header" style="left:([\d.]+)mm;top:([\d.]+)mm;'
                      r'width:([\d.]+)mm;height:([\d.]+)mm', html)
        self.assertIsNotNone(m, "ヘッダー帯が描かれていない")
        self.assertAlmostEqual(float(m.group(1)), self.stock.origin_x, places=3)
        self.assertAlmostEqual(float(m.group(2)), self.stock.origin_y, places=3)
        self.assertAlmostEqual(float(m.group(3)),
                               self.stock.pitch_x * self.stock.cols, places=3)
        self.assertAlmostEqual(float(m.group(4)),
                               self.stock.pitch_y * self.stock.header_rows,
                               places=3)

    def test_header_values_appear(self):
        html = LS.render_blocks(self.stock, self._blocks(1))
        for v in ("Z987654", "22.0", "910.0", "242.0", "279.0",
                  "\u691c\u756a", "\u672c\u6570", "NW", "GW",
                  "1\u68b1\u5305\u76ee", "2\u68b1\u5305\u76ee"):
            self.assertIn(v, html)

    def test_labels_start_below_band(self):
        html = LS.render_blocks(self.stock, self._blocks(1))
        top = float(re.search(r'class="label" style="left:[\d.]+mm;'
                              r'top:([\d.]+)mm', html).group(1))
        self.assertAlmostEqual(top, self.stock.label_origin_y(), places=3)
        self.assertGreater(top, self.stock.origin_y, "帯の下に置かれていない")

    def test_band_repeats_on_every_sheet_of_a_block(self):
        """1 コイルで 2 シートになっても、帯は両方に出す。"""
        n = self.stock.per_page + 1
        html = LS.render_blocks(self.stock, self._blocks(n))
        self.assertEqual(html.count('class="sheet"'), 2)
        self.assertEqual(html.count('class="header"'), 2)

    def test_blocks_do_not_share_a_sheet(self):
        """別コイルは別シート（帯が混ざらない）。"""
        blocks = [{"header": self.HEAD, "labels": _labels(2)},
                  {"header": dict(self.HEAD, kensaNo="Z111111"),
                   "labels": _labels(2)}]
        html = LS.render_blocks(self.stock, blocks)
        self.assertEqual(html.count('class="sheet"'), 2)
        self.assertIn("Z987654", html)
        self.assertIn("Z111111", html)

    def test_block_without_labels_is_skipped(self):
        html = LS.render_blocks(self.stock, [{"header": self.HEAD,
                                              "labels": []}])
        self.assertIn("ありません", html)

    def test_guides_mark_the_band(self):
        html = LS.render_blocks(self.stock, self._blocks(1),
                                show_guides=True)
        self.assertIn('class="header guide"', html)

    def test_calibration_applies_to_blocks(self):
        cal = LS.Calibration(offset_x_mm=1.5, offset_y_mm=-2.0,
                             scale_pct=101.0)
        html = LS.render_blocks(self.stock, self._blocks(1), cal)
        self.assertIn("translate(1.500mm,-2.000mm)", html)
        self.assertIn("scale(1.01000)", html)

    def test_band_is_optional(self):
        """headerRows = 0 の台紙なら帯は出さず、ラベルが 1 段目から始まる。"""
        stock = LS.get_stock(STOCK)
        stock.header_rows = 0
        stock.label_rows = stock.rows
        html = LS.render_blocks(stock, self._blocks(1))
        self.assertNotIn('class="header"', html)
        top = float(re.search(r'class="label" style="left:[\d.]+mm;'
                              r'top:([\d.]+)mm', html).group(1))
        self.assertAlmostEqual(top, stock.origin_y, places=3)


class TestBarcodeFontMode(unittest.TestCase):
    """バーコードをフォントで出す方式（現行 Excel と同じ字形）。"""

    FAMILY = "K's BarCodeFont Code39"

    def setUp(self):
        self.stock = LS.get_stock(STOCK)

    def _blocks(self):
        return [{"header": {}, "labels": _labels(1)}]

    def test_font_mode_emits_text_not_bars(self):
        html = LS.render_blocks(self.stock, self._blocks(),
                                barcode_mode="font",
                                barcode_family=self.FAMILY)
        self.assertNotIn('class="bc39"', html, "SVG で描かれている")
        self.assertEqual(html.count('class="bcfont"'), 2, "型番と検番の 2 本")

    def test_font_mode_style_attribute_is_intact(self):
        html = LS.render_blocks(self.stock, self._blocks(),
                                barcode_mode="font",
                                barcode_family=self.FAMILY)
        for attrs in _Attrs.of(html, "bcfont"):
            st = attrs.get("style", "")
            self.assertIn('font-family:"%s"' % self.FAMILY, st)
            self.assertIn("line-height", st, "style 属性が途中で切れている")

    def test_font_mode_keeps_start_stop_asterisks(self):
        """フォント方式では * を付けたまま渡す（Excel と同じ文字列）。"""
        html = BC.font_html("*W111111-0101 10*", 60.0, 10.0, self.FAMILY)
        self.assertIn("*W111111-0101 10*", html)

    def test_svg_mode_strips_asterisks(self):
        """SVG 方式は * をスタート/ストップ記号として符号化する。"""
        html = LS.render_blocks(self.stock, self._blocks(),
                                barcode_mode="svg")
        self.assertIn('class="bc39"', html)
        self.assertNotIn('class="bcfont"', html)

    def test_family_survives_html_attribute_parsing(self):
        """style 属性が途中で切れず、font-family が最後まで残ること。

        CSS のフォント名は ``"`` で囲む必要があるが、style 属性も ``"`` で
        囲まれている。エスケープしないと**属性がそこで終わり**、
        font-family が効かないまま `*...*` の文字列が印刷される。
        """
        html = BC.font_html("*ABC*", 40.0, 10.0, self.FAMILY)
        style = _style_of(html, "bcfont")
        self.assertIn('font-family:"%s", monospace' % self.FAMILY, style)
        self.assertIn("line-height", style, "style 属性が途中で切れている")

    def test_family_injection_is_neutralised(self):
        html = BC.font_html("*ABC*", 40.0, 10.0, 'Ev"il;}body{display:none')
        self.assertNotIn('"il', html)
        self.assertIn("monospace", html)

    def test_blank_family_falls_back(self):
        html = BC.font_html("*ABC*", 40.0, 10.0, "")
        self.assertIn("font-family:monospace", html)

    def test_empty_text_renders_nothing(self):
        self.assertEqual(BC.font_html("", 40.0, 10.0, self.FAMILY), "")

    def test_human_readable_line_present(self):
        html = BC.font_html("*ABC-1*", 40.0, 10.0, self.FAMILY)
        self.assertIn('class="bctext"', html)
        self.assertIn("ABC-1", html)


class TestFontFileSelection(unittest.TestCase):
    """設定のフォント名と、置かれたファイルの突き合わせ。

    ファイル名とフォント名は一致しない（``KsBarCodeCode39.ttf`` の中身は
    ``K's BarCodeFont Code39``）。ファイル名順の先頭を使うと、
    **名前と違うフォントで印刷してしまう**。
    """

    FONTS = os.path.join(ROOT, "assets", "fonts")

    def test_reads_family_from_ttf(self):
        if not os.path.isdir(self.FONTS):
            self.skipTest("フォント未配置")
        found = dict(BC.list_fonts(self.FONTS))
        if not found:
            self.skipTest("フォント未配置")
        for fn, fam in found.items():
            self.assertTrue(fam, "%s のフォント名が読めない" % fn)
            self.assertNotEqual(fam, os.path.splitext(fn)[0],
                                "ファイル名を返しているだけ")

    def test_picks_by_family_not_by_filename_order(self):
        fonts = [("Code39Barcode.ttf", "Code39"),
                 ("KsBarCodeCode39.ttf", "K's BarCodeFont Code39")]
        self.assertEqual(BC.pick_font_file(fonts, "K's BarCodeFont Code39"),
                         "KsBarCodeCode39.ttf")
        self.assertEqual(BC.pick_font_file(fonts, "Code39"),
                         "Code39Barcode.ttf")

    def test_tolerates_case_and_punctuation(self):
        fonts = [("KsBarCodeCode39.ttf", "K's BarCodeFont Code39")]
        for name in ("k's barcodefont code39", "KsBarCodeFontCode39",
                     "K'S BARCODEFONT CODE39"):
            self.assertEqual(BC.pick_font_file(fonts, name),
                             "KsBarCodeCode39.ttf", name)

    def test_unknown_family_is_not_silently_substituted(self):
        fonts = [("Code39Barcode.ttf", "Code39")]
        self.assertIsNone(BC.pick_font_file(fonts, "K's BarCodeFont Code39"))

    def test_no_fonts_at_all(self):
        self.assertIsNone(BC.pick_font_file([], "Code39"))
        self.assertIsNone(BC.pick_font_file([("a.ttf", "X")], ""))

    def test_missing_dir_is_empty_not_error(self):
        self.assertEqual(BC.list_fonts(os.path.join(ROOT, "no-such-dir")), [])

    def test_broken_file_is_ignored(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "broken.ttf"), "wb") as f:
            f.write(b"not a font")
        self.assertEqual(BC.list_fonts(d), [("broken.ttf", "")])
        self.assertIsNone(BC.pick_font_file(BC.list_fonts(d), "Code39"))


class TestFontNameAgreement(unittest.TestCase):
    """@font-face で宣言する名前と、要素が要求する名前が一致すること。

    片側だけ HTML エスケープすると ``K&#x27;s BarCodeFont Code39`` になるが、
    ``<style>`` の中では実体参照が戻らないため名前が食い違い、
    **フォントが当たらないまま `*...*` の文字列が印刷される**。
    """

    FAMILY = "K's BarCodeFont Code39"

    def test_apostrophe_survives(self):
        self.assertEqual(BC.css_family_name(self.FAMILY), self.FAMILY)
        self.assertNotIn("&#", BC.css_family_name(self.FAMILY))

    def test_declared_name_matches_requested_name(self):
        """@font-face が宣言する名前と、要素が要求する名前が一致する。"""
        declared = BC.css_family_name(self.FAMILY)           # <style> 側
        style = _style_of(BC.font_html("*ABC*", 40.0, 10.0, self.FAMILY),
                          "bcfont")                          # style 属性側
        m = re.search(r'font-family:"([^"]*)"', style)
        self.assertIsNotNone(m, "font-family が読み取れない")
        self.assertEqual(m.group(1), declared, "宣言名と要求名が違う")

    def test_quote_and_backslash_removed(self):
        for bad in ('A"B', "A\\B"):
            self.assertNotIn('"', BC.css_family_name(bad))
            self.assertNotIn("\\", BC.css_family_name(bad))

    def test_style_tag_cannot_be_closed(self):
        """``</style>`` を入れられても style 要素を閉じさせない。"""
        out = BC.css_family_name("a</style><script>alert(1)</script>")
        self.assertNotIn("<", out)
        self.assertNotIn(">", out)
        html = BC.font_html("*ABC*", 40.0, 10.0,
                            "a</style><script>alert(1)</script>")
        self.assertNotIn("<script", html)

    def test_newlines_removed(self):
        self.assertNotIn("\n", BC.css_family_name("A\nB"))

    def test_url_path_is_neutralised(self):
        self.assertEqual(BC.css_url_path('a"b).ttf'), "ab.ttf")


class TestPrintableArea(unittest.TestCase):
    """プレビューで収まっていても、刷ると欠ける所に置かない。

    現場の報告: ヘッダー帯の表（GW 列）と、左列ラベルの情報欄が
    **刷ると欠けた**。プリンターは紙の縁 約 4mm に刷れないため。
    旧レイアウトは GW 列が 207mm まで、情報欄が 2mm から始まっていた。
    """

    def setUp(self):
        self.stock = LS.get_stock(STOCK)

    def test_nothing_is_printed_in_the_unprintable_border(self):
        self.assertGreaterEqual(self.stock.safe_margin, 5.0)
        self.assertEqual(LS.ink_outside_safe_area(self.stock), [])

    def test_checker_catches_an_edge_field(self):
        """確かめる仕組み自体が働くこと（端へ寄せると見つける）。"""
        import copy
        st = copy.deepcopy(self.stock)
        st.header_fields[-1]["x"] = 200.0
        self.assertTrue(LS.ink_outside_safe_area(st))

    def test_info_block_matches_excel_print(self):
        """情報欄は Excel の印刷（縮小 89%）と同じ大きさ 23.5×12.24mm。

        旧 29×12.85mm は、Excel の印刷と透かして比べて大きいと言われた。
        """
        keys = ("pnLabel", "pnValue", "lcLabel", "kensaNo", "coilNo",
                "pLabel", "kataban", "qLabel", "weight", "unit")
        fs = [f for f in self.stock.fields if f["key"] in keys]
        left = min(f["x"] for f in fs)
        right = max(f["x"] + f["w"] for f in fs)
        top = min(f["y"] for f in fs)
        bottom = max(f["y"] + f["h"] for f in fs)
        self.assertAlmostEqual(right - left, 23.5, places=2)
        self.assertAlmostEqual(bottom - top, 4 * 9.75 * 25.4 / 72 * 0.89, places=1)
        # やや中央寄り: ラベル端から 7mm（旧 2mm）
        self.assertAlmostEqual(self.stock.padding + left, 7.0, places=2)
        # 型番バーコードとは重ならない
        bc = [f for f in self.stock.fields if f["key"] == "barcodeKataban"][0]
        self.assertLessEqual(right, bc["x"])

    def test_barcodes_did_not_move(self):
        """バーコードは Excel ときれいに重なっていた（現場確認）ので動かさない。"""
        want = {"barcodeKataban": (29.5, 0.0, 71.5, 12.85),
                "barcodeKensa": (0.0, 12.85, 101.0, 12.85)}
        for f in self.stock.fields:
            if f["key"] in want:
                self.assertEqual((f["x"], f["y"], f["w"], f["h"]), want[f["key"]])


class TestNothingIsClipped(unittest.TestCase):
    """枠からはみ出した文字は ``overflow:hidden`` で**黙って切れる**。

    検査番号や型番が途中で切れたラベルは現場で使えないので、
    (1) 実データが既定の文字サイズで枠に収まること
    (2) それでも入らない値は縮めて全部見せること
    の両方を押さえる。
    """

    #: 実データの最大長（型番は master の全件が 12 桁、検番は 7 桁固定）
    REAL = {
        "kensaNo": "Z987654",
        "coilNo": "-0216",
        "kataban": "BJB7610400QR",
        "weight": "1234.5",
    }

    def setUp(self):
        self.stock = LS.get_stock(STOCK)

    def _spec(self, key, fields):
        for f in fields:
            if f.get("key") == key:
                return f
        self.fail("フィールド %s が無い" % key)

    def _inner_w(self, spec):
        """描画と同じく、枠内の左右の余白を引いた幅。"""
        pad = float(spec.get("pad", self.stock.cell_pad))
        return float(spec["w"]) - pad * 2

    def test_real_values_fit_without_shrinking(self):
        for key, text in self.REAL.items():
            spec = self._spec(key, self.stock.fields)
            size = float(spec["size"])
            got = LS.fit_font_mm(text, size, self._inner_w(spec))
            self.assertAlmostEqual(
                got, size, places=6,
                msg="%s=%r が枠 %.2fmm に収まらず %.2fmm へ縮んだ"
                    % (key, text, float(spec["w"]), got))

    def test_static_labels_fit(self):
        for f in self.stock.fields:
            if f.get("type") != "static":
                continue
            size = float(f.get("size", 2.2))
            got = LS.fit_font_mm(f.get("text", ""), size, self._inner_w(f))
            self.assertAlmostEqual(got, size, places=6,
                                   msg="%r が収まらない" % f.get("text"))

    def test_header_values_fit(self):
        head = {"sizeName": "0.8mm\u00d753.5mm\u3000\u4e082",
                "kensaNo": "Z987654", "weightLabel": "\u91cd\u91cf\u4e082",
                "weight": "1234.5", "coilH1": "11", "ta1": "1910.0",
                "nw1": "1242.0", "gw1": "1279.0"}
        for f in self.stock.header_fields:
            text = (f.get("text", "") if f.get("type") == "static"
                    else head.get(f.get("source", ""), ""))
            size = float(f.get("size", 2.2))
            got = LS.fit_font_mm(text, size, float(f["w"]))
            self.assertAlmostEqual(got, size, places=6,
                                   msg="ヘッダー %r が収まらない" % text)

    def test_too_long_value_shrinks_instead_of_clipping(self):
        spec = self._spec("kataban", self.stock.fields)
        long = "BJB7610400QRXYZ0123"
        got = LS.fit_font_mm(long, float(spec["size"]), float(spec["w"]))
        self.assertLess(got, float(spec["size"]), "縮んでいない＝切れる")
        self.assertLessEqual(LS._text_em(long) * got,
                             float(spec["w"]) + 1e-9, "縮めても入っていない")

    def test_shrink_has_a_floor(self):
        got = LS.fit_font_mm("X" * 500, 2.9, 10.0)
        self.assertGreaterEqual(got, LS._MIN_FONT_MM)

    def test_wide_chars_counted_wider(self):
        self.assertGreater(LS._text_em("\u4e08\u4e08"), LS._text_em("AA"))

    def test_rendered_font_size_reflects_shrink(self):
        blocks = [{"header": {}, "labels": [{
            "kensaNo": "Z987654", "coilNo": "-0201",
            "kataban": "BJB7610400QRXYZ0123", "weight": "22.0",
            "barcodeKataban": "*BJB7610400QR*",
            "barcodeKensa": "*Z987654-0201 22.0*"}]}]
        html = LS.render_blocks(self.stock, blocks)
        sizes = [float(m) for m in
                 re.findall(r'font-size:([\d.]+)mm', html)]
        self.assertTrue(any(s < 2.9 for s in sizes),
                        "長い型番が縮んでいない")

    def test_fields_stay_inside_the_label(self):
        inner_w = self.stock.label_w - self.stock.padding * 2
        inner_h = self.stock.label_h - self.stock.padding * 2
        for f in self.stock.fields:
            self.assertLessEqual(f["x"] + f["w"], inner_w + 1e-6,
                                 "%s が右へはみ出す" % f.get("key"))
            self.assertLessEqual(f["y"] + f["h"], inner_h + 1e-6,
                                 "%s が下へはみ出す" % f.get("key"))


def _decode_svg(svg_text):
    """描いた SVG の**バーだけを見て**読み戻す（読取機のまねごと）。

    符号表を使って組み立てた結果を、同じ符号表で読み直すのでは
    循環した検算にしかならない。ここでは SVG の矩形の座標と幅、
    つまり**紙に出る形そのもの**から読み直す。
    """
    bars = [(float(x), float(w)) for x, w in
            re.findall(r'<rect x="([\d.]+)" y="0" width="([\d.]+)"[^/]*fill="#000"',
                       svg_text)]
    if not bars:
        raise ValueError("バーが無い")

    elems = []
    for i, (x, w) in enumerate(bars):
        elems.append(w)
        if i + 1 < len(bars):
            elems.append(bars[i + 1][0] - (x + w))      # 空白の幅

    unit = min(elems)
    kinds = ["w" if (e / unit) >= 2.0 else "n" for e in elems]
    rev = {v: k for k, v in BC._PATTERNS.items()}

    out = []
    i = 0
    while i + 9 <= len(kinds):
        pat = "".join(kinds[i:i + 9])
        if pat not in rev:
            raise ValueError("未知のパターン %s（位置 %d）" % (pat, i))
        out.append(rev[pat])
        i += 9
        if i < len(kinds):
            i += 1                                      # 文字間ギャップ
    if i != len(kinds):
        raise ValueError("要素が余った（%d / %d）" % (i, len(kinds)))
    return "".join(out)


class TestBarcodeRoundTrip(unittest.TestCase):
    """描いたバーコードが元の文字列に読み戻せること（E-5 の机上確認）。

    実機の読取機での確認はまだできていないが、少なくとも
    **バーの並びとして正しい Code 39 になっている**ことは確かめられる。
    """

    #: VBA が実際に作る 2 種類 + 文字集合の端
    CASES = [
        "*BJB7610400QR*",
        "*Z987654-0201 22.0*",
        "*W111111-0101 10*",
        "*A*",
        "*0123456789*",
        "*ABCDEFGHIJKLMNOPQRSTUVWXYZ*",
        "*-. $/+%*",
    ]
    #: 実際にラベルで使う枠幅と、極端に狭い場合
    WIDTHS = [71.5, 101.0, 30.0]

    def test_decodes_back_to_the_same_string(self):
        for text in self.CASES:
            want = "*" + BC.normalize(text) + "*"
            for w in self.WIDTHS:
                got = _decode_svg(BC.svg(text, w, 12.85))
                self.assertEqual(got, want,
                                 "枠 %.1fmm で %r が %r になった"
                                 % (w, want, got))

    def test_start_stop_are_present_in_the_bars(self):
        got = _decode_svg(BC.svg("*ABC*", 60.0, 10.0))
        self.assertTrue(got.startswith("*") and got.endswith("*"))
        self.assertEqual(got.count("*"), 2, "スタート/ストップは 2 つだけ")

    def test_asterisk_is_not_encoded_as_data(self):
        """``*`` は記号であってデータではない。"""
        self.assertEqual(_decode_svg(BC.svg("*A*", 60.0, 10.0)), "*A*")
        self.assertEqual(_decode_svg(BC.svg("A", 60.0, 10.0)), "*A*")

    def test_wide_narrow_ratio_is_preserved(self):
        """幅を変えても太細の比は変わらない（読取機が見るのは比）。"""
        for w in (25.0, 101.0):
            svg = BC.svg("*BJB7610400QR*", w, 12.85)
            bars = [float(b) for b in
                    re.findall(r'<rect x="[\d.]+" y="0" width="([\d.]+)"'
                               r'[^/]*fill="#000"', svg)]
            ratio = max(bars) / min(bars)
            self.assertAlmostEqual(ratio, BC.DEFAULT_WIDE_RATIO, places=3)


class TestQuietZone(unittest.TestCase):
    """静止帯（クワイエットゾーン）。

    Code 39 は「細バーの 10 倍 以上、かつ 2.54mm 以上」を要求する。
    静止帯不足は読み取れない原因として最も多いものの一つ。
    """

    #: SVG の座標は小数 4 桁で書き出すため、読み戻すと数ミクロンずれる。
    #: 600dpi でも 1 ドット 42 ミクロンなので、この差は紙の上には出ない。
    TOL_MM = 0.002

    def _measure(self, svg_text, width_mm):
        bars = [(float(x), float(w)) for x, w in
                re.findall(r'<rect x="([\d.]+)" y="0" width="([\d.]+)"'
                           r'[^/]*fill="#000"', svg_text)]
        left = bars[0][0]
        right = width_mm - (bars[-1][0] + bars[-1][1])
        unit = min(w for _, w in bars)
        return left, right, unit

    def test_meets_the_spec_at_label_widths(self):
        stock = LS.get_stock(STOCK)
        fields = {f["key"]: f for f in stock.fields}
        for key, text in [("barcodeKataban", "*BJB7610400QR*"),
                          ("barcodeKensa", "*Z987654-0250 1234.5*")]:
            w = float(fields[key]["w"])
            left, right, unit = self._measure(BC.svg(text, w, 12.85), w)
            need = max(unit * BC.QUIET_RATIO, BC.MIN_QUIET_MM)
            self.assertGreaterEqual(left + self.TOL_MM, need,
                                    "%s の左静止帯 %.3f < 必要 %.3f"
                                    % (key, left, need))
            self.assertGreaterEqual(right + self.TOL_MM, need,
                                    "%s の右静止帯 %.3f < 必要 %.3f"
                                    % (key, right, need))

    def test_never_below_the_absolute_floor(self):
        for w in (20.0, 40.0, 71.5, 101.0, 180.0):
            left, right, _ = self._measure(
                BC.svg("*ABC123*", w, 10.0), w)
            self.assertGreaterEqual(left + self.TOL_MM, BC.MIN_QUIET_MM,
                                    "枠 %.1f" % w)
            self.assertGreaterEqual(right + self.TOL_MM, BC.MIN_QUIET_MM,
                                    "枠 %.1f" % w)

    def test_explicit_value_still_honoured(self):
        left, right, _ = self._measure(
            BC.svg("*ABC*", 60.0, 10.0, quiet_mm=5.0), 60.0)
        self.assertAlmostEqual(left, 5.0, places=3)
        self.assertAlmostEqual(right, 5.0, places=3)

    def test_bars_stay_thick_enough_to_scan(self):
        """細バーは 0.19mm（7.5mil）を下回らないこと。"""
        stock = LS.get_stock(STOCK)
        fields = {f["key"]: f for f in stock.fields}
        for key, text in [("barcodeKataban", "*BJB7610400QR*"),
                          ("barcodeKensa", "*Z987654-0250 1234.5*")]:
            w = float(fields[key]["w"])
            _, _, unit = self._measure(BC.svg(text, w, 12.85), w)
            self.assertGreaterEqual(unit, 0.19,
                                    "%s の細バーが %.3fmm しかない" % (key, unit))

    def test_quiet_zone_formula_is_self_consistent(self):
        for w, mods in [(71.5, 202.0), (101.0, 274.5), (30.0, 100.0)]:
            q = BC.quiet_zone_mm(w, mods)
            unit = (w - 2 * q) / mods
            self.assertGreaterEqual(q + 1e-9,
                                    min(unit * BC.QUIET_RATIO, q),
                                    "循環が解けていない")
            self.assertGreaterEqual(q, BC.MIN_QUIET_MM)


class TestFontMetrics(unittest.TestCase):
    """フォント方式で枠幅いっぱいへ伸ばすための送り幅の読み取り。"""

    FONTS = os.path.join(ROOT, "assets", "fonts")
    FAMILY = "K's BarCodeFont Code39"

    def _path(self):
        if not os.path.isdir(self.FONTS):
            self.skipTest("フォント未配置")
        f = BC.pick_font_file(BC.list_fonts(self.FONTS), self.FAMILY)
        if not f:
            self.skipTest("フォント未配置")
        return os.path.join(self.FONTS, f)

    def test_reads_a_plausible_width(self):
        em = BC.text_width_em(self._path(), "*BJB7610400QR*")
        self.assertIsNotNone(em)
        # 14 文字なので 1 文字 0.1〜2em の範囲には収まるはず
        self.assertGreater(em, 14 * 0.1)
        self.assertLess(em, 14 * 2.0)

    def test_longer_text_is_wider(self):
        p = self._path()
        self.assertGreater(BC.text_width_em(p, "*ABCDEF*"),
                           BC.text_width_em(p, "*ABC*"))

    def test_unknown_char_gives_up_rather_than_guessing(self):
        self.assertIsNone(BC.text_width_em(self._path(), "*\u3042*"))

    def test_not_a_font_returns_none(self):
        self.assertIsNone(BC.text_width_em(
            os.path.join(ROOT, "README.md"), "*A*"))
        self.assertIsNone(BC.text_width_em(
            os.path.join(ROOT, "no-such-file.ttf"), "*A*"))

    def test_stretches_to_fill_the_box(self):
        html = BC.font_html("*BJB7610400QR*", 71.5, 12.85, self.FAMILY,
                            font_path=self._path())
        m = re.search(r'scaleX\(([\d.]+)\)', html)
        self.assertIsNotNone(m, "横へ伸ばしていない")
        self.assertGreater(float(m.group(1)), 1.0)
        self.assertLessEqual(float(m.group(1)), BC.MAX_FONT_STRETCH)

    def test_no_stretch_without_metrics(self):
        """フォントを読めないときは伸ばさない（当て推量をしない）。"""
        self.assertNotIn("scaleX",
                         BC.font_html("*ABC*", 71.5, 12.85, self.FAMILY))

    def test_stretch_leaves_a_quiet_zone(self):
        w = 71.5
        html = BC.font_html("*BJB7610400QR*", w, 12.85, self.FAMILY,
                            font_path=self._path())
        stretch = float(re.search(r'scaleX\(([\d.]+)\)', html).group(1))
        size = float(re.search(r'font-size:([\d.]+)mm', html).group(1))
        em = BC.text_width_em(self._path(), "*BJB7610400QR*")
        drawn = em * size * stretch
        quiet = (w - drawn) / 2
        self.assertGreaterEqual(quiet, BC.MIN_QUIET_MM,
                                "静止帯が %.2fmm しかない" % quiet)


class TestRules(unittest.TestCase):
    """罫線。ラベル本体とヘッダー帯の両方に引く。

    セルごとに枠線を付けると隣り合う辺が二重になって太さが変わるため、
    **線分**（x1,y1,x2,y2）で持っている。
    """

    def setUp(self):
        self.stock = LS.get_stock(STOCK)

    HEAD = {"sizeName": "0.8mm\u00d753.5mm\u3000\u4e082",
            "kensaNo": "Z987654", "weightLabel": "\u91cd\u91cf\u4e082",
            "weight": "22.0",
            "coilH1": "11", "ta1": "910.0", "nw1": "242.0", "gw1": "279.0",
            "coilH2": "11", "ta2": "910.0", "nw2": "242.0", "gw2": "279.0"}

    def _blocks(self, n=1):
        return [{"header": self.HEAD, "labels": _labels(n)}]

    def test_label_has_rules(self):
        self.assertTrue(self.stock.rules, "ラベルの罫線が定義されていない")
        html = LS.render_blocks(self.stock, self._blocks())
        self.assertEqual(html.count('class="rl"'),
                         (len(self.stock.rules) + len(self.stock.header_rules)))

    def test_header_has_no_enclosing_rules(self):
        """ヘッダー帯の範囲囲いの罫線は消す（現場の求め。Excel にも無い）。"""
        self.assertEqual(self.stock.header_rules, [])
        html = LS.render_blocks(self.stock, self._blocks())
        head = html.split('class="header', 1)[1].split('class="label', 1)[0]
        self.assertNotIn('class="rl"', head)

    def test_rules_are_horizontal_or_vertical(self):
        for r in self.stock.rules + self.stock.header_rules:
            self.assertTrue(r["x1"] == r["x2"] or r["y1"] == r["y2"],
                            "斜めの罫線がある: %r" % r)

    def test_rules_stay_inside(self):
        inner_w = self.stock.label_w - self.stock.padding * 2
        inner_h = self.stock.label_h - self.stock.padding * 2
        for r in self.stock.rules:
            for x in (r["x1"], r["x2"]):
                self.assertLessEqual(x, inner_w + 1e-6, r)
            for y in (r["y1"], r["y2"]):
                self.assertLessEqual(y, inner_h + 1e-6, r)

    def test_header_rules_stay_inside(self):
        inner_w = self.stock.pitch_x * self.stock.cols - self.stock.header_padding * 2
        inner_h = (self.stock.pitch_y * self.stock.header_rows
                   - self.stock.header_padding * 2)
        for r in self.stock.header_rules:
            for x in (r["x1"], r["x2"]):
                self.assertLessEqual(x, inner_w + 1e-6, r)
            for y in (r["y1"], r["y2"]):
                self.assertLessEqual(y, inner_h + 1e-6, r)

    def test_header_table_does_not_cross_the_middle_cut(self):
        """1 段目は 105mm のシール 2 枚。表は右のシールの中に収める。"""
        cut = self.stock.pitch_x
        hp = self.stock.header_padding
        for f in self.stock.header_fields:
            x0 = self.stock.origin_x + hp + float(f["x"])
            x1 = x0 + float(f["w"])
            self.assertFalse(x0 < cut < x1,
                             "%s が真ん中の切れ目をまたいでいる (%.1f〜%.1f)"
                             % (f["key"], x0, x1))

    def test_lines_are_centred_on_the_coordinate(self):
        """線の太さぶん、座標の中心に来る（片側にずれない）。"""
        html = LS.render_blocks(self.stock, self._blocks())
        w = self.stock.rule_mm
        tops = [float(t) for t in re.findall(
            r'class="rl" style="left:[\d.]+mm;top:(-?[\d.]+)mm', html)]
        self.assertIn(round(-w / 2, 3), [round(t, 3) for t in tops],
                      "y=0 の横罫が中心に置かれていない")

    def test_zero_length_rules_are_skipped(self):
        self.assertEqual(LS._rules_html([{"x1": 1, "y1": 1, "x2": 1, "y2": 1}],
                                        0.2), "")

    def test_no_rules_renders_nothing(self):
        self.assertEqual(LS._rules_html([], 0.2), "")
        self.assertEqual(LS._rules_html(self.stock.rules, 0), "")

    def test_diagonal_rule_is_refused(self):
        self.assertEqual(LS._rules_html(
            [{"x1": 0, "y1": 0, "x2": 5, "y2": 5}], 0.2), "")

    def test_text_does_not_touch_the_lines(self):
        """セル内の余白があること（罫線に文字がくっつかない）。"""
        self.assertGreater(self.stock.cell_pad, 0)
        html = LS.render_blocks(self.stock, self._blocks())
        self.assertIn("padding:0 %.3fmm;" % self.stock.cell_pad, html)


class TestRulerPage(unittest.TestCase):
    def test_ruler_has_ticks_and_frames(self):
        stock = LS.get_stock(STOCK)
        html = LS.render_ruler_page(stock)
        self.assertIn('class="sheet ruler"', html)
        self.assertGreater(html.count('class="tick'), 40, "目盛りが少ない")
        self.assertEqual(len(re.findall(r'class="label guide"', html)),
                         stock.per_page)
        self.assertIn("100", html, "100mm の目盛り表示が無い")

    def test_every_row_boundary_is_drawn(self):
        """段の境目を全段ぶん引く。

        1 段だけでは 29.7mm と 30mm の違い（0.3mm）は測れない。
        **10 段の合計**なら 297mm と 300mm で 3mm 差になり、目で分かる。
        """
        stock = LS.get_stock(STOCK)
        html = LS.render_ruler_page(stock)
        tops = [float(t) for t in
                re.findall(r'class="rowline" style="top:([\d.]+)mm', html)]
        self.assertEqual(len(tops), stock.rows + 1,
                         "段の境目が全部引かれていない")
        self.assertAlmostEqual(tops[-1],
                               stock.origin_y + stock.pitch_y * stock.rows,
                               places=3)

    def test_total_height_is_stated(self):
        stock = LS.get_stock(STOCK)
        html = LS.render_ruler_page(stock)
        total = stock.pitch_y * stock.rows
        self.assertIn("%.1fmm" % total, html, "合計が書かれていない")
        self.assertIn("段ピッチの確認", html)

    def test_header_band_is_marked(self):
        stock = LS.get_stock(STOCK)
        html = LS.render_ruler_page(stock)
        self.assertIn('class="label guide hdr"', html)

    def test_frames_start_below_the_header(self):
        stock = LS.get_stock(STOCK)
        html = LS.render_ruler_page(stock)
        tops = [float(t) for t in re.findall(
            r'class="label guide" style="left:[\d.]+mm;top:([\d.]+)mm', html)]
        self.assertAlmostEqual(min(tops), stock.label_origin_y(), places=3)


if __name__ == "__main__":
    unittest.main()
