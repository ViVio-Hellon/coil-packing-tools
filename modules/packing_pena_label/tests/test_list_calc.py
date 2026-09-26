# -*- coding: utf-8 -*-
"""羅列計算（50まで）の検証。

方針変更（2026.09）: 一覧表は参考値であり、``CalculateMaterials`` を正とする。
VBA の ``羅列計算`` が独自に持っていた 3 つの差異は解消し、
**風袋計算画面と必ず一致する**ことをここで固定する。
"""

import os
import sys
import unittest

from modules.packing_pena_label.app.services import list_calc as LC
from modules.packing_pena_label.app.services import tare_calc as TC
from modules.packing_pena_label.app.services.vba_compat import fmt, ws_round, ws_roundup
from modules.packing_pena_label.tests.test_tare_calc import table


class TestListShape(unittest.TestCase):
    def setUp(self):
        self.data = LC.build_list(table(), 53.5, "TIP1000", 10.0, 12.0,
                                  True, True, cb_key="CheckBox5")

    def test_fifty_rows(self):
        self.assertEqual(len(self.data["rows"]), 50)
        self.assertEqual(self.data["rows"][0]["count"], "1")
        self.assertEqual(self.data["rows"][49]["count"], "50")

    def test_columns_consistent(self):
        self.assertEqual(len(self.data["headers"]), len(self.data["columnKeys"]))
        for row in self.data["rows"]:
            for key in self.data["columnKeys"]:
                self.assertIn(key, row)

    def test_ex_dry_column_exists(self):
        """風袋に EX-DRY を含めるようにしたので、列としても出す。"""
        self.assertIn("dry", self.data["columnKeys"])
        self.assertTrue(any("EX-DRY" in h for h in self.data["headers"]))


class TestMatchesTareCalc(unittest.TestCase):
    """一覧の各行が calculate_materials と一致すること。"""

    WIDTH = 53.5
    TIP = "TIP1000"
    CB = "CheckBox5"

    def _row(self, q, **kw):
        opts = dict(t1=10.0, t2=0.0, take1_visible=True, take2_visible=False)
        opts.update(kw)
        data = LC.build_list(table(), self.WIDTH, self.TIP,
                             opts["t1"], opts["t2"],
                             opts["take1_visible"], opts["take2_visible"],
                             cb_key=self.CB)
        return data["rows"][q - 1]

    def _mat(self, q, coil_no=1, **kw):
        opts = dict(t1=10.0, t2=0.0, take1_visible=True, take2_visible=False)
        opts.update(kw)
        return TC.calculate_materials(
            table(), q, coil_no, self.WIDTH, self.TIP, self.CB, 0,
            opts["t1"], opts["t2"],
            opts["take1_visible"], opts["take2_visible"])

    def test_every_row_matches(self):
        for q in (1, 5, 9, 10, 11, 16, 30, 50):
            with self.subTest(count=q):
                row = self._row(q)
                mat = self._mat(q)
                disp = TC.display_values(mat)
                self.assertEqual(row["pin"], disp["PI"])
                self.assertEqual(row["tip"], disp["TIP"])
                self.assertEqual(row["sut"], disp["SUT"])
                self.assertEqual(row["esa"], disp["ESA"])
                self.assertEqual(row["por"], disp["POR"])
                self.assertEqual(row["hb"], disp["HB"])
                self.assertEqual(row["par"], disp["PAR"])
                self.assertEqual(row["pet"], disp["PET"])
                self.assertEqual(row["dry"], disp["DRY"])
                self.assertEqual(row["nw"], disp["NW"])
                self.assertEqual(row["hu"], disp["HU"])
                self.assertEqual(row["gw"], disp["GW"])

    def test_hu_includes_ex_dry(self):
        """旧 VBA は 8 項目だった。今は 9 項目（EX-DRY 込み）。"""
        q = 11
        mat = self._mat(q)
        nine = (mat.PIN + mat.TIP + mat.SUT + mat.ESA + mat.POR
                + mat.HB + mat.PET + mat.PAR + mat.DRY)
        self.assertAlmostEqual(mat.HU, nine, places=12)
        self.assertEqual(self._row(q)["hu"], fmt(ws_round(nine, 0), "0.0"))
        self.assertNotEqual(mat.DRY, 0.0, "EX-DRY が 0 では検証にならない")

    def test_pack_height_includes_hardboard(self):
        """旧 VBA は 2.35×2 を加えていなかった。今は加える。"""
        q = 11
        mat = self._mat(q)
        expected = ws_roundup(mat.KTA, -1)
        self.assertEqual(self._row(q)["packHeight"], fmt(expected, "0.0"))
        # パレット 300mm のみの旧式より必ず大きいか等しい
        old = ws_roundup(mat.TA * 1000 + 300, -1)
        self.assertGreaterEqual(expected, old)

    def test_hb_uses_judge_flag(self):
        """HB は DB_HB と同じ判定（HBFlag）を通る。"""
        # 53.5mm × 11本 は Flag=True -> HB590 固定
        hb = TC.calc_hb(table(), 11, 53.5, "TIP1000", "CheckBox5", 0)
        self.assertTrue(hb.HBFlag)
        self.assertEqual(self._row(11)["hb"], fmt(hb.HBValue, "0.000"))
        # 53.5mm × 10本 は HB530 固定
        hb10 = TC.calc_hb(table(), 10, 53.5, "TIP1000", "CheckBox5", 0)
        self.assertEqual(self._row(10)["hb"], fmt(hb10.HBValue, "0.000"))
        self.assertNotEqual(self._row(10)["hb"], self._row(11)["hb"])

    def test_heights(self):
        q = 11
        mat = self._mat(q)
        row = self._row(q)
        self.assertEqual(row["coilHeight"], fmt(mat.TA * 1000, "0"))
        self.assertEqual(row["tipHeight"], fmt(mat.TIPT * 1000, "0"))


class TestListNw(unittest.TestCase):
    """NW の丈の選び方は VBA のまま。"""

    def _first(self, t1, t2, v1, v2):
        return LC.build_list(table(), 53.5, "TIP1000", t1, t2, v1, v2,
                             cb_key="CheckBox5")["rows"][0]["nw"]

    def test_both_visible_uses_t1(self):
        self.assertEqual(self._first(10.0, 99.0, True, True), "10.0")

    def test_take1_only_uses_t1(self):
        self.assertEqual(self._first(10.0, 12.0, True, False), "10.0")

    def test_take2_only_uses_t2(self):
        self.assertEqual(self._first(10.0, 12.0, False, True), "12.0")

    def test_neither_visible_is_zero(self):
        self.assertEqual(self._first(10.0, 12.0, False, False), "0.0")


class TestQuickHeight(unittest.TestCase):
    """積み高さクイック計算は「1条分の参考表示」のため現行どおり。"""

    def test_uses_one_coil_not_count(self):
        q = LC.quick_height(10, 53.5)
        tipt = 11 * 0.7 / 1000
        ta = 53.5 / 1000 + tipt
        self.assertAlmostEqual(float(q["one"]), ta * 1000, places=3)
        self.assertAlmostEqual(float(q["tip"]), tipt * 1000, places=3)

    def test_pallet_excludes_hardboard(self):
        q = LC.quick_height(10, 53.5)
        ta = 53.5 / 1000 + 11 * 0.7 / 1000
        self.assertAlmostEqual(float(q["palletRoundUp"]),
                               ws_roundup(ta * 1000 + 300, -1), places=6)


if __name__ == "__main__":
    unittest.main()
