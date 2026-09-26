# -*- coding: utf-8 -*-
"""VBA 互換層の検証。丸めがずれると帳票の数値が全部ずれるため最重要。"""

import os
import sys
import math
import unittest

from modules.packing_pena_label.app.services.vba_compat import (cdbl, cstr, cstr_single, fmt, is_numeric,
                                     single, to_narrow, val, ws_round,
                                     ws_roundup)


class TestRounding(unittest.TestCase):
    def test_ws_round_is_half_away_from_zero(self):
        # Excel の ROUND は 0 から遠い方へ。Python 組込 round（銀行家丸め）とは違う
        self.assertEqual(ws_round(2.5), 3.0)
        self.assertEqual(ws_round(3.5), 4.0)
        self.assertEqual(ws_round(-2.5), -3.0)
        self.assertEqual(ws_round(0.5), 1.0)
        self.assertEqual(round(2.5), 2)          # 参考: 組込はこうなる

    def test_ws_roundup(self):
        self.assertEqual(ws_roundup(1234, -1), 1240.0)
        self.assertEqual(ws_roundup(1230, -1), 1230.0)
        self.assertEqual(ws_roundup(901.6, -1), 910.0)
        self.assertEqual(ws_roundup(-901.6, -1), -910.0)

    def test_fmt(self):
        self.assertEqual(fmt(0.25, "0.0"), "0.3")
        self.assertEqual(fmt(1.0, "0.0"), "1.0")
        self.assertEqual(fmt(14.4, "0.00"), "14.40")
        self.assertEqual(fmt(1.4, "0.000"), "1.400")
        self.assertEqual(fmt(7, "00"), "07")
        self.assertEqual(fmt(11, "0"), "11")
        self.assertEqual(fmt("", "0.0"), "")

    def test_fmt_negative_zero(self):
        self.assertEqual(fmt(-0.001, "0.0"), "0.0")


class TestConversion(unittest.TestCase):
    def test_val(self):
        self.assertEqual(val("10kg"), 10.0)
        self.assertEqual(val("12.5"), 12.5)
        self.assertEqual(val("abc"), 0.0)
        self.assertEqual(val(""), 0.0)
        self.assertEqual(val(None), 0.0)

    def test_cdbl(self):
        self.assertEqual(cdbl("12.5"), 12.5)
        self.assertEqual(cdbl("１２"), 12.0)        # 全角も通す
        with self.assertRaises(ValueError):
            cdbl("")

    def test_is_numeric(self):
        self.assertTrue(is_numeric("1"))
        self.assertTrue(is_numeric("1.5"))
        self.assertFalse(is_numeric(""))
        self.assertFalse(is_numeric("W"))
        self.assertFalse(is_numeric(None))

    def test_to_narrow(self):
        self.assertEqual(to_narrow("Ｗ１１１"), "W111")


class TestSingle(unittest.TestCase):
    def test_single_precision(self):
        # MaterialWeights.TA は VBA で Single 宣言。エサフォーム判定に効く
        v = single(0.5969)
        self.assertNotEqual(v, 0.5969)                   # 単精度で丸まる
        self.assertAlmostEqual(v, 0.5969, places=6)

    def test_single_roundtrip_stable(self):
        self.assertEqual(single(single(1.5)), single(1.5))


class TestCStr(unittest.TestCase):
    """VBA ``CStr``（数値）。帳票の「計算式」欄は & 連結＝CStr で作られる。

    Python の ``str(float)`` は最短往復表現なので、そのまま埋めると
    ``0.5968999862670898`` のような値が並ぶ。VBA では ``0.5969`` だった。
    """

    def test_single_uses_seven_significant_digits(self):
        self.assertEqual(cstr_single(single(0.5969)), "0.5969")
        self.assertNotIn("0.59689", cstr_single(single(0.5969)))

    def test_double_uses_fifteen_significant_digits(self):
        self.assertEqual(cstr(1100 * math.pi / 1000), "3.45575191894877")

    def test_whole_numbers_have_no_decimal_point(self):
        """係数 1 は "1"。"1.0" になってはいけない。"""
        self.assertEqual(cstr(1.0), "1")
        self.assertEqual(cstr(2), "2")
        self.assertEqual(cstr(370.0), "370")
        self.assertEqual(cstr(0.0), "0")
        self.assertEqual(cstr(-3.0), "-3")

    def test_strings_pass_through(self):
        """マスタ値は Variant 文字列なので CStr は恒等。"""
        self.assertEqual(cstr("1"), "1")
        self.assertEqual(cstr("0.350"), "0.350")

    def test_typical_master_values(self):
        for v, want in ((0.35, "0.35"), (0.012, "0.012"), (7.5, "7.5"),
                        (1.2, "1.2"), (0.02, "0.02")):
            self.assertEqual(cstr(v), want)

    def test_floating_point_noise_is_absorbed(self):
        """8.399999999999999 は VBA では 8.4 と出る。"""
        self.assertEqual(cstr(12 * 0.7), "8.4")

    def test_none_and_bool(self):
        self.assertEqual(cstr(None), "")
        self.assertEqual(cstr(True), "True")


if __name__ == "__main__":
    unittest.main()
