# -*- coding: utf-8 -*-
"""ラベル転記の検証。

実台紙 ``1.0mm×53.5mm 丈1.xlsx``（検番 W111111 / 重量 10 / 型番 BJB7606500QR）
から読み取った実セル値と突き合わせる。
"""

import os
import sys
import unittest

from modules.packing_pena_label.app.services.label_layout import (build_coil_info_cells, build_sheet_cells,
                                       cellmap_to_a1, clear_coil_info_cells,
                                       coil_numbers, col_letter)
from modules.packing_pena_label.app.services.size_master import LABEL_BASE_ROWS
from modules.packing_pena_label.app.services import label_layout as LL

#: 実 xlsx から読み取った期待値（A1 表記）
REAL_SHEET = {
    "V3": "W111111", "V92": "W111111",          # 検査番号ヘッダー
    "AK3": "10", "AK92": "10",                   # 重量ヘッダー
    "I7": "*BJB7606500QR*",                      # バーコード(型番) 左
    "AO7": "*BJB7606500QR*",                     # 同 右
    "D8": "W111111", "AJ8": "W111111",           # 台紙数式が出す検査番号
    "G8": "-0101", "AM8": "-0109",               # コイル番号 左/右
    "D9": "BJB7606500QR", "AJ9": "BJB7606500QR",  # 型番
    "D10": "10", "AJ10": "10",                   # 重量
    "G10": "kg", "AM10": "kg",                   # 単位
    "B11": "*W111111-0101 10*",                  # バーコード(検番) 左
    "AH11": "*W111111-0109 10*",                 # 同 右
    # 2 ブロック目
    "D97": "W111111", "G97": "-0117", "AM97": "-0125",
    "B100": "*W111111-0117 10*",
    "G106": "-0118", "AM106": "-0126",
    "G151": "-0123", "AM151": "-0131",
    "G160": "-0124", "AM160": "-0132",
    "B163": "*W111111-0124 10*",
}


class TestColLetter(unittest.TestCase):
    def test_col_letter(self):
        self.assertEqual(col_letter(1), "A")
        self.assertEqual(col_letter(7), "G")
        self.assertEqual(col_letter(22), "V")
        self.assertEqual(col_letter(37), "AK")
        self.assertEqual(col_letter(59), "BG")


class TestCoilNumbers(unittest.TestCase):
    def test_take1_sequence(self):
        nums = coil_numbers(1)
        self.assertEqual(len(nums), 16)
        # 左面: 01-08 -> 17-24 / 右面: 09-16 -> 25-32
        self.assertEqual(nums[0], ("-0101", "-0109"))
        self.assertEqual(nums[7], ("-0108", "-0116"))
        self.assertEqual(nums[8], ("-0117", "-0125"))
        self.assertEqual(nums[15], ("-0124", "-0132"))

    def test_take2_prefix(self):
        nums = coil_numbers(2)
        self.assertEqual(nums[0], ("-0201", "-0209"))
        self.assertEqual(nums[15], ("-0224", "-0232"))

    def test_no_duplicates(self):
        flat = [n for pair in coil_numbers(1) for n in pair]
        self.assertEqual(len(flat), len(set(flat)), "コイル番号が重複している")


class TestAgainstRealSheet(unittest.TestCase):
    def setUp(self):
        self.a1 = cellmap_to_a1(
            build_sheet_cells("W111111", "10", "BJB7606500QR", 1))

    def test_matches_real_xlsx(self):
        for cell, expected in REAL_SHEET.items():
            with self.subTest(cell=cell):
                self.assertEqual(self.a1.get(cell), expected,
                                 "セル %s が実台紙と一致しない" % cell)

    def test_label_count(self):
        # 16 行 × 左右 2 面 = 32 枚。コイル番号セルの数で確認
        coil_cells = [k for k in self.a1
                      if k.startswith("G") or k.startswith("AM")]
        base_rows = set(LABEL_BASE_ROWS)
        coil_no_cells = [k for k in coil_cells
                         if int(k.lstrip("GAM")) in base_rows]
        self.assertEqual(len(coil_no_cells), 32)


class TestCoilInfo(unittest.TestCase):
    def test_writes_two_rows(self):
        cells = build_coil_info_cells(3, 0, "11", "11", "910.0", "910.0",
                                      "110.0", "110.0", "147.0", "147.0")
        a1 = cellmap_to_a1(cells)
        self.assertEqual(a1["AQ3"], "11")     # COL_COILH = 43
        self.assertEqual(a1["AV3"], "910.0")  # COL_TA    = 48
        self.assertEqual(a1["BA3"], "110.0")  # COL_NW    = 53
        self.assertEqual(a1["BG3"], "147.0")  # COL_GW    = 59
        self.assertEqual(a1["AQ4"], "11")
        self.assertNotIn("AQ92", a1)          # extraRow=0 なので複写しない

    def test_extra_row_is_copied(self):
        cells = build_coil_info_cells(3, 92, "11", "11", "910.0", "910.0",
                                      "110.0", "110.0", "147.0", "147.0")
        a1 = cellmap_to_a1(cells)
        self.assertEqual(a1["AQ92"], "11")
        self.assertEqual(a1["AQ93"], "11")
        self.assertEqual(a1["BG92"], "147.0")

    def test_clear_targets(self):
        self.assertEqual(len(clear_coil_info_cells(3, 0)), 8)
        self.assertEqual(len(clear_coil_info_cells(3, 92)), 16)


if __name__ == "__main__":
    unittest.main()


class TestPrintedWeight(unittest.TestCase):
    """ラベルに刷る重量は台紙のセル書式 ``0.0_ `` と同じ小数1桁(統合 1.2.6)。

    重量器は小数2桁まで量れない。台紙のセル(保存する値)とバーコードは打ったまま。
    """

    def test_weight_text_is_one_decimal(self):
        for typed, want in (("20", "20.0"), ("20.15", "20.2"), ("20.14", "20.1"),
                            ("110.5", "110.5"), ("", ""), ("abc", "abc")):
            with self.subTest(typed=typed):
                self.assertEqual(LL.weight_text(typed), want)

    def test_rows_print_one_decimal_but_keep_barcode_and_cells(self):
        rows = LL.build_label_rows("W111111", "20.15", "K1", 1)
        self.assertEqual(rows[0]["weight"], "20.2")
        self.assertTrue(rows[0]["barcodeKensa"].endswith(" 20.15*"), rows[0]["barcodeKensa"])
        cells = LL.build_label_cells("W111111", "20.15", "K1", 1)
        self.assertIn("20.15", cells.values())
        self.assertNotIn("20.2", cells.values())
