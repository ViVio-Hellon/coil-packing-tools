# -*- coding: utf-8 -*-
"""検査番号・重量チェックの検証（VBA の判定順・文言をそのまま保つ）。"""

import os
import sys
import unittest

from modules.packing_pena_label.app.models import FormState
from modules.packing_pena_label.app.services.validation import (check_weight_inputs, convert_fullwidth,
                                     filter_alnum_upper, inp_check,
                                     normalize_kensa_no, num_extract,
                                     validate_kensa_no)


class TestKensaNo(unittest.TestCase):
    def test_valid(self):
        # 実台紙のサンプル W111111 が通ること
        self.assertIsNone(validate_kensa_no("W111111"))

    def test_lowercase_is_upcased(self):
        self.assertEqual(normalize_kensa_no("w111111"), "W111111")
        self.assertIsNone(validate_kensa_no(normalize_kensa_no("w111111")))

    def test_fullwidth_is_narrowed(self):
        self.assertEqual(normalize_kensa_no("Ｗ１１１１１１"), "W111111")

    def test_all_digits_rejected(self):
        msg = validate_kensa_no("1111111")
        self.assertIn("7桁目に数字が入力されています", msg)

    def test_second_char_alpha_rejected(self):
        msg = validate_kensa_no("WW11111")
        self.assertIn("6桁目にアルファベット", msg)

    def test_too_short_rejected(self):
        msg = validate_kensa_no("W11111")
        self.assertIn("1桁目にアルファベット", msg)

    def test_third_char_is_checked(self):
        # VBA は Mid(ken,3,1) を検査していなかった（穴）。
        # 2026.09 の判断で塞いだ（解析書 B-2）。
        msg = validate_kensa_no("W1X1111")
        self.assertIsNotNone(msg, "3 文字目の穴が開いている")
        self.assertIn("5桁目にアルファベット", msg)


class TestInpCheck(unittest.TestCase):
    def _state(self, **kw):
        st = FormState()
        for k, v in kw.items():
            setattr(st, k, v)
        return st

    def test_ob_take1_requires_weight1(self):
        st = self._state(selected_ob=5, weight1="")
        self.assertTrue(inp_check(st, 1))
        st.weight1 = "10"
        self.assertFalse(inp_check(st, 1))

    def test_ob_take2_requires_weight2(self):
        st = self._state(selected_ob=6, weight2="")
        self.assertTrue(inp_check(st, 2))

    def test_named_ob13_requires_weight1(self):
        st = self._state(selected_ob=13, weight1="")
        self.assertTrue(inp_check(st, 1))

    def test_cb_requires_both(self):
        st = self._state(selected_cb=5, weight1="10", weight2="")
        self.assertTrue(inp_check(st, 3))
        st.weight2 = "12"
        self.assertFalse(inp_check(st, 3))

    def test_named_cb_requires_both(self):
        st = self._state(named_cb=True, weight1="", weight2="12")
        self.assertTrue(inp_check(st, 3))

    def test_messages_match_vba(self):
        st = self._state(selected_ob=5, weight1="")
        self.assertEqual(check_weight_inputs(st), "重量入力がありません　丈1")
        st = self._state(selected_ob=6, weight2="")
        self.assertEqual(check_weight_inputs(st), "重量入力がありません　丈2")
        st = self._state(selected_cb=5, weight1="", weight2="")
        self.assertEqual(check_weight_inputs(st), "重量入力がありません　丈1　or　丈2")


class TestFilters(unittest.TestCase):
    def test_alnum_upper(self):
        self.assertEqual(filter_alnum_upper("w11-11 1x"), "W11111X")

    def test_convert_fullwidth(self):
        # VBA CommandButton8 の置換表どおり
        self.assertEqual(convert_fullwidth("1.0x53.5"), "1.0Ｘ53.5")
        self.assertEqual(convert_fullwidth("１０"), "10")
        self.assertEqual(convert_fullwidth("（A）"), "(A)")

    def test_num_extract(self):
        self.assertEqual(num_extract("1.0/2M"), "1.0")
        self.assertEqual(num_extract("abc12.5kg"), "12.5")



class TestKensaNoGaps(unittest.TestCase):
    """検査番号のチェック（B-2）。

    VBA は 1 / 2 / 4〜7 文字目しか見ておらず、**3 文字目に穴があった**
    （``W1A1111`` が通り、そのままラベルとバーコードに載っていた）。
    2026.09 の判断で塞いだ。

    メッセージの「N桁目」は **右から数えた桁**。文言は変えていない。
    """

    def _v(self, k):
        return validate_kensa_no(normalize_kensa_no(k))

    def test_the_third_character_is_checked(self):
        """3 文字目が英字なら弾く（塞いだ穴）。"""
        self.assertIsNotNone(self._v("W1A1111"), "3 文字目の穴が開いている")
        self.assertIn("5桁目", self._v("W1A1111"), "右から 5 桁目のはず")

    def test_closing_the_hole_did_not_change_existing_messages(self):
        """これまで弾かれていた入力の文言は 1 つも変わらない。

        3 文字目の判定を**最後**に置いているため、
        先に該当する条件があればそちらのメッセージが出る。
        """
        cases = {
            "WA11111": "6桁目にアルファベット",   # 2文字目
            "W11111A": "1桁目にアルファベット",   # 7文字目
            "W11A111": "1～4桁目にアルファベット",  # 4文字目
            "1111111": "7桁目に数字",             # 1文字目
            # 2 文字目と 3 文字目の両方が英字 -> 2 文字目のメッセージのまま
            "WAA1111": "6桁目にアルファベット",
        }
        for ken, expect in cases.items():
            self.assertIn(expect, self._v(ken) or "", ken)

    def test_valid_numbers_still_pass(self):
        for k in ("W111111", "Z987654", "a123456", "W000000"):
            self.assertIsNone(self._v(k), k)

    def test_the_positions_that_are_checked(self):
        self.assertIsNotNone(self._v("1111111"), "1文字目が数字なら NG")
        self.assertIsNotNone(self._v("WA11111"), "2文字目が英字なら NG")
        self.assertIsNotNone(self._v("W11A111"), "4文字目が英字なら NG")
        self.assertIsNotNone(self._v("W111A11"), "5文字目が英字なら NG")
        self.assertIsNotNone(self._v("W1111A1"), "6文字目が英字なら NG")
        self.assertIsNotNone(self._v("W11111A"), "7文字目が英字なら NG")

    def test_messages_count_from_the_right(self):
        """「N桁目」は右から数えた桁。7 桁なので 左N ↔ 右(8-N)。"""
        self.assertIn("6桁目", self._v("WA11111"))     # 左2 ↔ 右6
        self.assertIn("1桁目", self._v("W11111A"))     # 左7 ↔ 右1
        self.assertIn("7桁目", self._v("1111111"))     # 左1 ↔ 右7
        self.assertIn("1～4桁目", self._v("W11A111"))  # 左4〜7 ↔ 右1〜4

    def test_a_valid_number_passes(self):
        for k in ("W111111", "Z987654", "a123456"):
            self.assertIsNone(self._v(k), k)

    def test_short_input_is_rejected(self):
        for k in ("", "W11", "W1"):
            self.assertIsNotNone(self._v(k), repr(k))

if __name__ == "__main__":
    unittest.main()
