# -*- coding: utf-8 -*-
"""サイズ構成マスタの検証。VBA GetSizeConfig と 1 対 1 で一致すること。"""

import os
import sys
import unittest

from modules.packing_pena_label.app.services.size_master import (
    SizeMaster, get_size_master, normalize_size_name)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: VBA GetSizeConfig の全内容（**VBA の文字列そのまま**）
#:
#: サイズ名だけは表記を統一して出す（normalize_size_name / 2026.09 決定）ので、
#: 比較時に正規化を挟む。この表そのものは VBA の記録として書き換えない。
VBA_TABLE = {
    (1, 2): ("1.0mm×73mm", "BJB7604000QR", "c", 3, 0),
    (3, 4): ("0.6mm×82.5mm", "BJB7604200QR", "e", 3, 0),
    (5, 6): ("1.0mm×53.5mm", "BJB7606500QR", "d", 3, 92),
    (7, 8): ("1.0mm×40.0mm", "BJB7604300QR", "b", 3, 92),
    (9, 10): ("1.0mm×33.0mm", "BJB7603900QR", "a", 3, 92),
    (11, 12): ("1.0mm×63.0mm", "BJB7605900QR", "f", 92, 0),
    (13, 14): ("0.8mm×53.5mm", "BJB7610400QR", "g", 3, 92),
}
#: VBA GetCBtoPair
VBA_CB_PAIRS = {1: (7, 8), 2: (9, 10), 3: (1, 2),
                4: (3, 4), 5: (5, 6), 6: (11, 12)}


class _Base:
    master = None

    def test_matches_vba_get_size_config(self):
        for obs, (base, kata, sym, drow, erow) in VBA_TABLE.items():
            for ob in obs:
                with self.subTest(ob=ob):
                    c = self.master.get(ob)
                    self.assertIsNotNone(c)
                    self.assertEqual(c.base_name, normalize_size_name(base))
                    self.assertEqual(c.kataban, kata)
                    self.assertEqual(c.symbol, sym)
                    self.assertEqual(c.data_row, drow)
                    self.assertEqual(c.extra_row, erow)

    def test_take_type_is_odd_even(self):
        for ob in self.master.ob_indexes():
            expected = 1 if ob % 2 == 1 else 2
            self.assertEqual(self.master.take_type(ob), expected)
            self.assertEqual(self.master.get(ob).take_type, expected)

    def test_sheet_name(self):
        self.assertEqual(self.master.sheet_name(5), "1.0mm×53.5mm 　丈1")
        self.assertEqual(self.master.sheet_name(6), "1.0mm×53.5mm 　丈2")
        self.assertEqual(self.master.sheet_name(11), "1.0mm×63.0mm 　丈1")

    def test_cb_pairs(self):
        for cb, pair in VBA_CB_PAIRS.items():
            self.assertEqual(self.master.cb_to_pair(cb), pair)
        self.assertEqual(self.master.cb_to_pair(9), (0, 0))
        self.assertEqual(self.master.pair_for_named_cb(), (13, 14))

    def test_widths(self):
        expected = {1: 73.0, 3: 82.5, 5: 53.5, 7: 40.0,
                    9: 33.0, 11: 63.0, 13: 53.5}
        for ob, w in expected.items():
            self.assertEqual(self.master.width_of(ob), w)
            self.assertEqual(self.master.width_of(ob + 1), w)

    def test_width_from_sheet_name(self):
        # GetSelectedCoilWidth はシート名から抽出する
        self.assertEqual(self.master.width_from_sheet_name(5), 53.5)
        self.assertEqual(self.master.width_from_sheet_name(1), 73.0)
        self.assertEqual(self.master.width_from_sheet_name(3), 82.5)

    def test_unknown_ob(self):
        self.assertIsNone(self.master.get(99))
        self.assertEqual(self.master.display_name(99), "")


class TestBuiltin(_Base, unittest.TestCase):
    """JSON が無い場合の内蔵定義。"""
    def setUp(self):
        self.master = SizeMaster(None)


class TestFromJson(_Base, unittest.TestCase):
    """data/size_master.json から読んだ場合。"""
    def setUp(self):
        self.master = SizeMaster(os.path.join(ROOT, "data", "size_master.json"))

    def test_size_cell_label_is_unified(self):
        """印刷ヘッダーに出る文字は 14 件とも同じ形（2026.09 決定）。

        VBA では当時のシート名がそのまま入っていて、
        ``1.0mm×53.5mm`` だけ全角スペースの後に半角スペースが 1 個多く、
        ``1.0mm×73mm`` だけ小数点が無い、という揺れがあった。
        Excel 側はシート名を色々な設定の基準にしてしまっていて直せないが、
        このツールはシート名に依存していないので表記だけ揃える。
        """
        self.assertEqual(self.master.get(5).size_cell_label,
                         "1.0mm×53.5mm 　丈1")
        self.assertEqual(self.master.get(1).size_cell_label,
                         "1.0mm×73.0mm 　丈1")

    def test_override_still_works_if_ever_needed(self):
        """実シートの字間に合わせたくなったら JSON で上書きできること。"""
        import json
        import tempfile
        with open(os.path.join(ROOT, "data", "size_master.json"),
                  encoding="utf-8") as f:
            data = json.load(f)
        for row in data["sizes"]:
            if row["baseName"] == "1.0mm×73mm":
                row["sizeCellLabel"] = {"1": "なんでも良い"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            path = f.name
        try:
            m = SizeMaster(path)
            self.assertEqual(m.get(1).size_cell_label, "なんでも良い")
            # 指定しなかった方は統一形のまま
            self.assertEqual(m.get(2).size_cell_label, "1.0mm×73.0mm 　丈2")
        finally:
            os.unlink(path)

    def test_json_matches_builtin(self):
        builtin = SizeMaster(None)
        for ob in builtin.ob_indexes():
            a, b = builtin.get(ob), self.master.get(ob)
            self.assertEqual((a.base_name, a.kataban, a.symbol,
                              a.data_row, a.extra_row, a.width_mm),
                             (b.base_name, b.kataban, b.symbol,
                              b.data_row, b.extra_row, b.width_mm))


class TestBadJsonFallsBack(unittest.TestCase):
    def test_invalid_json_uses_builtin(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        m = SizeMaster(p)
        self.assertEqual(len(m.all()), 14)
        self.assertEqual(m.get(5).kataban, "BJB7606500QR")




class TestSizeNameIsUnified(unittest.TestCase):
    """サイズ名の表記が 14 件すべて同じ形であること（2026.09 決定）。"""

    def setUp(self):
        self.master = SizeMaster(os.path.join(ROOT, "data", "size_master.json"))

    def test_every_name_has_one_decimal_or_more(self):
        import re
        for ob in self.master.ob_indexes():
            base = self.master.get(ob).base_name
            with self.subTest(ob=ob, base=base):
                nums = re.findall(r"(\d+(?:\.\d+)?)mm", base)
                self.assertEqual(len(nums), 2, "厚み×巾 の 2 か所であること")
                for n in nums:
                    self.assertIn(".", n, "小数点が無い表記が残っている")

    def test_printed_label_is_sheet_name_form(self):
        """印刷ヘッダーの文字 = サイズ名 + 全角スペース + 丈N（例外なし）。"""
        for ob in self.master.ob_indexes():
            cfg = self.master.get(ob)
            with self.subTest(ob=ob):
                self.assertEqual(cfg.size_cell_label, cfg.sheet_name)

    def test_normalize_never_rounds(self):
        """桁を削らない。整数に .0 を足すだけ。"""
        self.assertEqual(normalize_size_name("1.25mm×40mm"), "1.25mm×40.0mm")
        self.assertEqual(normalize_size_name("0.6mm×82.5mm"), "0.6mm×82.5mm")
        self.assertEqual(normalize_size_name("1.0mm×73mm"), "1.0mm×73.0mm")

    def test_width_is_unchanged_by_the_rename(self):
        """巾の取り出しは表記を変えても同じ値（計算に影響しない）。"""
        for ob in self.master.ob_indexes():
            with self.subTest(ob=ob):
                self.assertEqual(self.master.width_from_sheet_name(ob),
                                 self.master.width_of(ob))


if __name__ == "__main__":
    unittest.main()
