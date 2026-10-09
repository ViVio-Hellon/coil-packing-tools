# -*- coding: utf-8 -*-
"""風袋計算の検証。

期待値は VBA の式を手計算した値（docs/01_VBA解析書.md 8章）と突き合わせる。
資材単重は tests 内の固定マスタを使い、マスタ値の変更に影響されないようにする。
"""

import math
import os
import sys
import unittest

from modules.packing_pena_label.app.models import MaterialWeights
from modules.packing_pena_label.app.repositories.material_repo import MaterialTable
from modules.packing_pena_label.app.services import tare_calc as TC
from modules.packing_pena_label.app.services.vba_compat import fmt, single, ws_round, ws_roundup

FIELDS = ["管理番号", "梱包資材名", "単位質量", "係数"]
RECORDS = [
    ["1", "テスラピン", "0.35", "1"],
    ["2", "チップボール1000ф", "1.2", "1"],
    ["3", "ストレッチフィルム", "0.02", "1"],
    ["4", "エサフォーム", "0.05", "1"],
    ["5", "PETバンド", "0.012", "1"],
    ["6", "ポリシート", "0.09", "1"],
    ["7", "ハードボード", "0.65", "1"],
    ["8", "八角ハードボード", "1.1", "1"],
    ["9", "HB590*2000", "1.45", "1"],
    ["10", "HB530*2000", "1.3", "1"],
    # VBA は 羅列計算 で取得するが使用しない（構造として保持）
    ["11", "HB580*2000", "1.42", "1"],
    ["12", "HB500*2000", "1.25", "1"],
    ["19", "チップボール950ф", "1.05", "1"],
    ["20", "チップボール820ф", "0.85", "1"],
    ["21", "樹脂パレット", "7.5", "1"],
    ["22", "EX-DRY", "0.02", "1"],
]


def table():
    return MaterialTable(FIELDS, [list(r) for r in RECORDS], "test")


class TestDimensions(unittest.TestCase):
    def test_ta_and_kta(self):
        """TA = 本数×巾/1000 + (本数+1)×0.7/1000、KTA = TA*1000+300+4.7"""
        mat = MaterialWeights()
        kta = TC.set_coil_dimensions(mat, 11, 53.5, all_coil_empty=False)
        expected_tipt = single(12 * 0.7 / 1000)
        expected_ta = single(11 * 53.5 / 1000 + expected_tipt)
        self.assertAlmostEqual(mat.TA, expected_ta, places=6)
        self.assertAlmostEqual(mat.TA, 0.5969, places=4)
        self.assertAlmostEqual(kta, 901.6, places=1)
        self.assertEqual(fmt(ws_roundup(kta, -1), "0.0"), "910.0")

    def test_all_empty_returns_none(self):
        mat = MaterialWeights()
        self.assertIsNone(TC.set_coil_dimensions(mat, 11, 53.5, all_coil_empty=True))

    def test_diameter(self):
        for tip, diam, half in (("TIP1000", "1100", "550"),
                                ("TIP950", "1000", "500"),
                                ("TIP820", "900", "450")):
            mat = MaterialWeights()
            TC.set_diameter_info(mat, tip)
            self.assertEqual(mat.TempDiameter, diam)
            self.assertEqual(mat.HalfDiameter, half)
            self.assertAlmostEqual(mat.GAI, int(diam) * math.pi / 1000, places=9)


class TestHbFlag(unittest.TestCase):
    def test_cb_rules(self):
        cases = [(14, "CheckBox1"), (15, "CheckBox2"), (8, "CheckBox3"),
                 (7, "CheckBox3"), (7, "CheckBox4"), (6, "CheckBox4"),
                 (11, "CheckBox5"), (10, "CheckBox5"), (9, "CheckBox6"),
                 (8, "CheckBox6"), (11, "ck_08_53"), (10, "ck_08_53")]
        for n, cb in cases:
            with self.subTest(n=n, cb=cb):
                self.assertTrue(TC.judge_hb_flag(n, cb, 0))

    def test_ob_rules(self):
        for ob in (5, 6, 13, 14):
            self.assertTrue(TC.judge_hb_flag(11, "", ob))
            self.assertTrue(TC.judge_hb_flag(10, "", ob))
            self.assertFalse(TC.judge_hb_flag(9, "", ob))

    def test_no_match(self):
        self.assertFalse(TC.judge_hb_flag(9, "CheckBox5", 0))
        self.assertFalse(TC.judge_hb_flag(13, "CheckBox1", 0))
        self.assertFalse(TC.judge_hb_flag(11, "", 1))


class TestHbValue(unittest.TestCase):
    def test_53_5_with_11_uses_hb590(self):
        hb = TC.calc_hb(table(), 11, 53.5, "TIP1000", "CheckBox5", 0)
        self.assertTrue(hb.HBFlag)
        self.assertAlmostEqual(hb.HBValue, 2 * 1.45 + 2 * 1.1, places=9)
        self.assertIn("HB590*2000", hb.HBFormula)

    def test_53_5_with_10_uses_hb530(self):
        hb = TC.calc_hb(table(), 10, 53.5, "TIP1000", "CheckBox5", 0)
        self.assertAlmostEqual(hb.HBValue, 2 * 1.3 + 2 * 1.1, places=9)
        self.assertIn("HB530*2000", hb.HBFormula)

    def test_flag_true_but_other_width_uses_calc_formula(self):
        """40mm×14本 は Flag=True だが式は Flag=False と同じ（VBA の構造）。"""
        on = TC.calc_hb(table(), 14, 40.0, "TIP1000", "CheckBox1", 0)
        off = TC.calc_hb(table(), 13, 40.0, "TIP1000", "CheckBox1", 0)
        self.assertTrue(on.HBFlag)
        self.assertFalse(off.HBFlag)
        self.assertIn("外周+700mm", on.HBFormula)
        self.assertIn("外周+700mm", off.HBFormula)

    def test_hb_ta_matches_the_shared_formula(self):
        """DB_HB の TA は SetCoilDimensions と同じ共通関数から来る（2026.09 統一）。"""
        hb = TC.calc_hb(table(), 11, 73.0, "TIP1000", "CheckBox3", 0)
        self.assertAlmostEqual(hb.TA, TC.get_coil_ta(11, 73.0), places=12)
        self.assertAlmostEqual(hb.TA, 11 * 73.0 / 1000 + 12 * 0.7 / 1000,
                               places=12)


class TestEsaThreshold(unittest.TestCase):
    def test_one_wrap_below_threshold(self):
        mat = MaterialWeights()
        TC.set_coil_dimensions(mat, 11, 53.5, False)     # TA = 0.5969
        TC.set_diameter_info(mat, "TIP1000")
        v = TC._calc_esa(table(), mat, [])
        self.assertAlmostEqual(v, mat.GAI * 0.05, places=9)
        self.assertIn("1周", mat.formulas["ESAsiki"])

    def test_two_wraps_above_threshold(self):
        mat = MaterialWeights()
        TC.set_coil_dimensions(mat, 16, 53.5, False)     # TA = 0.8679 > 0.82
        TC.set_diameter_info(mat, "TIP1000")
        self.assertGreater(mat.TA, 0.82)
        v = TC._calc_esa(table(), mat, [])
        self.assertAlmostEqual(v, mat.GAI * 2 * 0.05, places=9)
        self.assertIn("2周", mat.formulas["ESAsiki"])


class TestNw(unittest.TestCase):
    def test_nw_is_rounded_to_one_decimal(self):
        """VBA は Format(...,"0.0") の文字列を Double へ代入している。"""
        v = TC.calc_nw(3, 1, 10.05, 0, True, False)
        self.assertEqual(v, 30.2)          # 30.15 -> "30.2" -> 30.2
        self.assertNotAlmostEqual(v, 30.15)

    def test_val_reads_the_product_as_vba_cstr_does(self):
        """VBA の Val は文字列を受ける: 掛けた Double を 15 桁で文字列にしてから読む(統合 1.2.6)。

        3 × 101.35 は 2進では 304.04999999999995。VBA は "304.05" → 304.1。
        以前は 304.0 になり、保存・ラベルの NW が VBA と 0.1 違った。
        """
        self.assertEqual(TC.calc_nw(3, 1, 101.35, 0, True, False), 304.1)
        self.assertEqual(TC.calc_nw(3, 3, 0, 20.15, False, True), 60.5)
        self.assertEqual(TC.calc_nw(2, 1, 101.35, 0, True, False), 202.7)

    def test_hidden_take_gives_zero(self):
        self.assertEqual(TC.calc_nw(11, 1, 10, 0, False, False), 0.0)
        self.assertEqual(TC.calc_nw(11, 3, 0, 12, False, False), 0.0)

    def test_take2_uses_t2(self):
        self.assertEqual(TC.calc_nw(10, 3, 10, 12, False, True), 120.0)

    def test_parse_label_weight(self):
        self.assertEqual(TC.parse_label_weight("10kg"), 10.0)
        self.assertEqual(TC.parse_label_weight("kg"), 0.0)
        self.assertEqual(TC.parse_label_weight(""), 0.0)


class TestFullCalculation(unittest.TestCase):
    """1.0mm×53.5mm / 11本 / TIP1000 / 1条10kg の一式。"""

    def setUp(self):
        self.mat = TC.calculate_materials(
            table(), 11, 1, 53.5, "TIP1000", "CheckBox5", 0,
            t1=10.0, t2=12.0, take1_visible=True, take2_visible=True)

    def test_individual_materials(self):
        m = self.mat
        gai = 1100 * math.pi / 1000
        self.assertAlmostEqual(m.PIN, 4 * 0.35, places=9)
        self.assertAlmostEqual(m.TIP, 12 * 1.2, places=9)
        self.assertAlmostEqual(m.SUT, gai * 5 * 0.02, places=9)
        self.assertAlmostEqual(m.ESA, gai * 0.05, places=9)
        self.assertAlmostEqual(m.PAR, 7.5 * 2, places=9)
        self.assertAlmostEqual(m.DRY, 0.02, places=9)
        self.assertAlmostEqual(m.HB, 2 * 1.45 + 2 * 1.1, places=9)
        self.assertAlmostEqual(m.NW, 110.0, places=9)

    def test_hu_has_nine_items(self):
        m = self.mat
        expected = (m.PIN + m.TIP + m.SUT + m.ESA + m.POR
                    + m.HB + m.PET + m.PAR + m.DRY)
        self.assertAlmostEqual(m.HU, expected, places=12)

    def test_gw(self):
        self.assertAlmostEqual(self.mat.GW, self.mat.HU + self.mat.NW, places=12)

    def test_display_rounding(self):
        d = TC.display_values(self.mat)
        self.assertEqual(d["NW"], "110.0")
        self.assertEqual(d["HU"], fmt(ws_round(self.mat.HU, 0), "0.0"))
        self.assertEqual(d["GW"], fmt(ws_round(self.mat.GW, 0), "0.0"))
        self.assertEqual(d["TIP"], "14.40")     # チップボールだけ小数2桁

    def test_missing_material_is_zero_and_recorded(self):
        """マスタに無い資材は VBA と同じく 0。ただし記録は残す。"""
        t = MaterialTable(FIELDS, [r for r in RECORDS
                                   if r[1] != "テスラピン"], "test")
        mat = TC.calculate_materials(t, 11, 1, 53.5, "TIP1000", "CheckBox5", 0,
                                     10.0, 12.0, True, True)
        self.assertEqual(mat.PIN, 0.0)
        self.assertIn("テスラピン", mat.missing)



class TestHardboardHeightIsUnified(unittest.TestCase):
    """コイル高さ TA は 1 つの共通関数から来る（2026.09 統一 / 解析書 A-1）。

    かつては ``SetCoilDimensions`` と ``GetHBCoilDimensions`` が別々に
    計算しており、後者は ``/10^3`` の位置が誤っていて括弧の中で mm と m を
    足していた。そのためチップボール分（11本で 8.4mm）が実質消え、
    ハードボードの重量が小さく出ていた。

    「HB は積み高さより低い」という話もあったが、実測と突き合わせた結果
    **一定の締結代という概念が成立しなかった**ため見送り
    （``HB_BAND_CLEARANCE_M = 0``）。
    """

    def setUp(self):
        self.table = table()

    def _mat(self, n, width, ob=14):
        return TC.calculate_materials(
            self.table, n, 1, width, "TIP1000", "", ob, 22.0, 22.0,
            True, True, all_coil_empty=False)

    def test_both_heights_come_from_the_shared_function(self):
        for n in (1, 6, 11, 20, 50):
            for w in (33.0, 53.5, 73.0):
                m = self._mat(n, w)
                hb = TC.calc_hb(self.table, n, w, "TIP1000", "", 14)
                self.assertAlmostEqual(hb.TA, TC.get_coil_ta(n, w), places=12,
                                       msg="%d本 巾%.1f" % (n, w))
                # mat.TA は VBA の宣言が Single なので float32 ぶんだけ違う
                self.assertAlmostEqual(m.TA, hb.TA, places=5,
                                       msg="%d本 巾%.1f" % (n, w))

    def test_the_stack_includes_the_chipboard(self):
        """TA = 本数×巾 + チップボール (本数+1) 枚。"""
        for n in (6, 11, 20, 50):
            for w in (33.0, 53.5, 73.0):
                expect = (n * w + (n + TC.CHIP_EXTRA_SHEETS)
                          * TC.CHIP_THICKNESS_MM) / 1000
                self.assertAlmostEqual(TC.get_coil_ta(n, w), expect, places=12,
                                       msg="%d本 巾%.1f" % (n, w))

    def test_hb_no_longer_loses_the_chipboard(self):
        """旧式はチップボール分が 1/1000 になって消えていた。"""
        n, w = 11, 53.5
        old = TC.val((n * w) + TC.val((n + 1) * 0.7 / 10 ** 3)) / 10 ** 3
        new = TC.get_coil_ta(n, w)
        self.assertGreater(new, old)
        self.assertAlmostEqual((new - old) * 1000, 12 * 0.7, delta=0.01,
                               msg="差はチップボール 12 枚ぶんのはず")

    def test_band_height_equals_the_stack_while_clearance_is_zero(self):
        self.assertEqual(TC.HB_BAND_CLEARANCE_M, 0.0)
        for ta in (0.1, 0.5969, 2.7107):
            self.assertEqual(TC.get_hb_band_height(ta), ta)

    def test_band_height_subtracts_the_clearance_when_set(self):
        """将来 締結代を入れる場合の受け皿（定数 1 つで効く）。"""
        orig = TC.HB_BAND_CLEARANCE_M
        try:
            TC.HB_BAND_CLEARANCE_M = 0.02
            self.assertAlmostEqual(TC.get_hb_band_height(0.5969), 0.5769,
                                   places=9)
        finally:
            TC.HB_BAND_CLEARANCE_M = orig

    def test_band_height_never_goes_below_the_floor(self):
        """補正後が下限を割る場合は補正しない。"""
        orig = TC.HB_BAND_CLEARANCE_M
        try:
            TC.HB_BAND_CLEARANCE_M = 0.5
            self.assertEqual(TC.get_hb_band_height(0.3), 0.3,
                             "下限を割ったのに補正している")
            self.assertAlmostEqual(TC.get_hb_band_height(2.0), 1.5, places=9)
        finally:
            TC.HB_BAND_CLEARANCE_M = orig

    def test_the_hb_row_uses_the_band_height(self):
        hb = TC.calc_hb(self.table, 11, 53.5, "TIP1000", "", 1)
        self.assertIn(TC.fmt(TC.get_hb_band_height(hb.TA), "0.000"),
                      hb.HBFormula)

class TestHbDisplay(unittest.TestCase):
    """VBA ``SetHBDisplayForm`` 相当（表記用フォームへの反映）。

    ```vba
    Public Sub SetHBDisplayForm(HB As hbData)
      With 表記用
        .HBsiki = HB.HBFormula
        .HB = Format(HB.HBValue, "0.000")
      End With
    End Sub
    ```

    移植先は `/breakdown`（計算内容）画面。
    値は `display_values()["HB"]`、計算式は `formulas["HBsiki"]`。
    """

    def _mat(self, n=11, col=53.5, cb="CheckBox5"):
        return TC.calculate_materials(
            table(), n, 1, col, "TIP1000", cb, 0, 10.0, 12.0, True, True)

    def test_value_uses_three_decimals(self):
        """``Format(HB.HBValue, "0.000")`` と同じ書式。"""
        m = self._mat()
        self.assertEqual(TC.display_values(m)["HB"], fmt(m.HB, "0.000"))
        self.assertEqual(TC.display_values(m)["HB"], "5.100")

    def test_formula_is_carried_over(self):
        """``HB.HBFormula`` がそのまま入る。"""
        m = self._mat()
        hb = TC.calc_hb(table(), 11, 53.5, "TIP1000", "CheckBox5", 0)
        self.assertEqual(m.formulas["HBsiki"], hb.HBFormula)
        self.assertTrue(m.formulas["HBsiki"].startswith("【ハードボード】"))

    def test_formula_is_never_empty(self):
        """どの分岐でも計算式が入る（固定物 2 種 / 標準 HB）。"""
        for n, col, cb in ((11, 53.5, "CheckBox5"),    # HB590
                           (10, 53.5, "CheckBox5"),    # HB530
                           (13, 40.0, "CheckBox1"),    # 標準
                           (14, 40.0, "CheckBox1")):   # Flag=True だが標準式
            m = self._mat(n, col, cb)
            self.assertTrue(m.formulas.get("HBsiki", "").strip(),
                            "%d本 巾%.1f の計算式が空" % (n, col))

    def test_value_and_formula_agree(self):
        """計算式の末尾の数値が表示値と一致する。"""
        for n, col, cb in ((11, 53.5, "CheckBox5"), (13, 40.0, "CheckBox1")):
            m = self._mat(n, col, cb)
            shown = TC.display_values(m)["HB"]
            self.assertTrue(m.formulas["HBsiki"].rstrip().endswith(shown),
                            "%r の末尾が %r でない"
                            % (m.formulas["HBsiki"][-40:], shown))

    def test_every_material_has_both(self):
        """HB だけでなく、表記用へ出す資材はすべて値と式を持つ。"""
        m = self._mat()
        d = TC.display_values(m)
        for key, siki in (("PI", "PIsiki"), ("TIP", "TIPsiki"),
                          ("SUT", "SUTsiki"), ("ESA", "ESAsiki"),
                          ("POR", "PORsiki"), ("PAR", "PARsiki"),
                          ("PET", "PETsiki"), ("DRY", "DRYsiki"),
                          ("HB", "HBsiki")):
            self.assertTrue(d[key].strip(), "%s の値が空" % key)
            self.assertTrue(m.formulas.get(siki, "").strip(),
                            "%s が空" % siki)


class TestSheetFormulaText(unittest.TestCase):
    """風袋計算の「計算式」欄（VBA ``WriteSheetFormulas``）の文字列。

    VBA は ``&`` で連結する＝ ``CStr`` が効くので、
    マスタ値は Variant 文字列のまま、計算値は Single 7 桁 / Double 15 桁。
    素の float を埋めると ``0.5968999862670898`` のような値が並んでしまう。
    """

    def setUp(self):
        self.mat = TC.calculate_materials(
            table(), 11, 1, 53.5, "TIP1000", "CheckBox5", 0,
            10.0, 12.0, True, True)
        self.f = TC.sheet_formulas(self.mat, 11, 1, 10.0, 12.0)

    def test_coefficient_one_is_not_one_point_zero(self):
        """係数 "1" はマスタの文字列のまま出す。"""
        self.assertEqual(self.f["PIN"], "4*0.35*1")
        self.assertNotIn("*1.0", self.f["PIN"])

    def test_no_float_noise_anywhere(self):
        """どの式にも 6 桁を超える小数が出ない（外周の 15 桁は除く）。"""
        import re
        gai = TC.cstr(self.mat.GAI)
        for key, text in self.f.items():
            cleaned = text.replace(gai, "")
            long_ = re.findall(r"\d+\.\d{6,}", cleaned)
            self.assertFalse(long_, "%s に %r" % (key, long_))

    def test_height_uses_single_precision(self):
        """TA は Single なので 0.5969。"""
        self.assertIn("0.5969", self.f["POR"])
        self.assertIn("0.5969", self.f["PET"])
        self.assertNotIn("0.59689", self.f["POR"])

    def test_perimeter_uses_double_precision(self):
        """GAI は Double なので 15 桁（VBA と同じ）。"""
        self.assertIn("3.45575191894877", self.f["SUT"])
        self.assertNotIn("3.4557519189487724", self.f["SUT"])

    def test_nw_uses_cstr(self):
        self.assertEqual(self.f["NW"], "10*11")

    def test_constants_appear_in_the_text(self):
        """定数化しても見た目は変わらない。"""
        self.assertIn("[約%d周計算]" % TC.STRETCH_WRAPS, self.f["SUT"])
        self.assertIn("*%d" % TC.PALLET_QTY, self.f["PAR"])
        self.assertIn("%dmm" % int(TC.BAND_SLACK_M * 1000), self.f["PET"])
        self.assertIn("(11+%d)" % TC.CHIP_EXTRA_SHEETS, self.f["TIP"])

    def test_esa_tag_follows_the_threshold(self):
        one = TC.sheet_formulas(self.mat, 11, 1, 10.0, 12.0)
        self.assertIn("[1周計算]", one["ESA"])
        big = TC.calculate_materials(
            table(), 16, 1, 53.5, "TIP1000", "CheckBox5", 0,
            10.0, 12.0, True, True)
        self.assertGreater(big.TA, TC.ESA_DOUBLE_LIMIT_M)
        self.assertIn("[2周計算]", TC.sheet_formulas(big, 16, 1, 10.0, 12.0)["ESA"])

    def test_every_row_has_text(self):
        for key, text in self.f.items():
            self.assertTrue(text.strip(), "%s が空" % key)


if __name__ == "__main__":
    unittest.main()
