"""実データの仕掛台帳を相手にした回帰試験

`tests/golden/lot_cases.json` は、現場の写し(`SIKALOT` / `SIKAHIKI` /
`SIKAODR` / `LS4LOT`)から `scripts/make_golden.py` が作った期待値。
**台帳側の事実は、VBAの出力紙が無くてもここで固定できる。**

    ロット番号を入れると何が引けるか
    前工程実績数(= 縦割数 × 横割数)がいくつになるか
    その丈数で条数がどう配られるか
    表示書式(0.000 / 0.0)がどう出るか

【この試験が守るもの】
上流の写しが新しくなったら `make_golden.py` を回し直す。差分が出たら
**「変わったのはデータか、こちらの計算か」**を切り分けられる ──
期待値を手で並べていると、書き写した時点の間違いがそのまま正解に
なってしまう。

【取り込み元そのものは置いていない】
写しは業務データなのでリポジトリに入れない。期待値(数字と文字列)
だけを置き、計算はここで組み直す。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from modules.packing_details.meisai import config, strand_service

GOLDEN = Path(__file__).resolve().parent / "golden" / "lot_cases.json"


def load() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


class GoldenFileTest(unittest.TestCase):
    """期待値そのものの形。"""

    def test_期待値がある(self):
        self.assertTrue(GOLDEN.exists(),
                        f"{GOLDEN} がありません。"
                        "scripts/make_golden.py で作り直してください。")

    def test_条件がばらけている(self):
        """**全部が同じ形だと、配分の枝を一度も踏まない。**"""
        cases = load()["cases"]
        self.assertGreaterEqual(len(cases), 10)
        tate = {c["tate_wari"] for c in cases}
        self.assertGreaterEqual(len(tate), 3, f"縦割数が偏っています: {tate}")


class DistributionTest(unittest.TestCase):
    """RULE-03: 実データの前工程実績数で条数を配り直す。"""

    def test_配分が期待値と一致する(self):
        for case in load()["cases"]:
            if not case["jou_su"]:
                continue
            with self.subTest(lot=case["lot_no"]):
                self.assertEqual(
                    strand_service.distribute(case["zen_kotei"], case["jou_su"]),
                    case["strands"])

    def test_配分の合計が前工程実績数と一致する(self):
        for case in load()["cases"]:
            with self.subTest(lot=case["lot_no"]):
                self.assertEqual(sum(case["strands"]), case["zen_kotei"])

    def test_自動セットのままなら必ず割り切れる(self):
        """**これは実データの性質。**

        前工程実績数 = 縦割数 × 横割数、丈数 = 縦割数 なので、
        自動セットのままだと余りが出ない。RULE-03 の余りの枝を
        踏むのは、作業者が手で直したときだけ ── その形は
        `manual_overrides` で別に押さえる。
        """
        for case in load()["cases"]:
            if not case["jou_su"]:
                continue
            with self.subTest(lot=case["lot_no"]):
                self.assertEqual(len(set(case["strands"])), 1,
                                 f"{case['lot_no']} で余りが出ました")

    def test_手で直したときの配分(self):
        """余りは丈番号の大きいほうから配られる。"""
        overrides = load()["manual_overrides"]
        self.assertTrue(overrides, "手修正の例が1件もありません")
        for case in overrides:
            with self.subTest(lot=case["lot_no"], jou=case["jou_su"]):
                got = strand_service.distribute(case["zen_kotei"], case["jou_su"])
                self.assertEqual(got, case["strands"])
                # 余りは後ろに付く = 前が小さく、後ろが大きい
                self.assertEqual(got, sorted(got))


class WarisuTest(unittest.TestCase):
    """RULE-01 / RULE-02: 縦割数・横割数から決まるもの。"""

    def test_前工程実績数は縦割かける横割(self):
        for case in load()["cases"]:
            with self.subTest(lot=case["lot_no"]):
                self.assertEqual(case["zen_kotei"],
                                 case["tate_wari"] * case["yoko_wari"])

    def test_丈数は縦割数(self):
        for case in load()["cases"]:
            with self.subTest(lot=case["lot_no"]):
                if 1 <= case["tate_wari"] <= config.JOUSU_MAX:
                    self.assertEqual(case["jou_su"], case["tate_wari"])
                else:
                    self.assertEqual(case["jou_su"], 0)

    def test_実データの縦割数は上限に収まっている(self):
        """上限(20)を超えるロットが現れたら、手入力の案内が要る。"""
        for case in load()["cases"]:
            with self.subTest(lot=case["lot_no"]):
                self.assertLessEqual(case["tate_wari"], config.JOUSU_MAX)


class KeyTest(unittest.TestCase):
    """RULE-04: 条番号の並び。"""

    def test_丈1の条番号が期待値と一致する(self):
        for case in load()["cases"]:
            if not case["strands"]:
                continue
            with self.subTest(lot=case["lot_no"]):
                self.assertEqual(
                    strand_service.keys_for_jou(1, case["strands"][0]),
                    case["keys_jou1"])

    def test_左端が最大で右端が1(self):
        for case in load()["cases"]:
            keys = case["keys_jou1"]
            if not keys:
                continue
            with self.subTest(lot=case["lot_no"]):
                self.assertEqual(keys[0], f"1-{case['strands'][0]}")
                self.assertEqual(keys[-1], "1-1")


class FormatTest(unittest.TestCase):
    """表示書式。VBA の `Format(..., "0.000")` / `"0.0"` と同じ文字列か。"""

    def test_板厚は小数3桁(self):
        for case in load()["cases"]:
            with self.subTest(lot=case["lot_no"]):
                for key in ("seizou_thickness", "odr_thickness"):
                    self.assertRegex(case[key], r"^-?\d+\.\d{3}$")

    def test_板幅は小数1桁(self):
        for case in load()["cases"]:
            with self.subTest(lot=case["lot_no"]):
                for key in ("seizou_width", "odr_width"):
                    self.assertRegex(case[key], r"^-?\d+\.\d$")


class OrderTest(unittest.TestCase):
    """受注番号と包装仕様NO。"""

    def test_受注番号は重複しない(self):
        for case in load()["cases"]:
            nos = [o["order_no"] for o in case["orders"]]
            with self.subTest(lot=case["lot_no"]):
                self.assertEqual(len(nos), len(set(nos)))

    def test_包装仕様NOは見つからなくても空で返る(self):
        """引当側の受注番号の19%は仕掛受注に無い(実データ)。

        **エラーにしない。** VBA も空のまま返し、画面は空欄にして
        リンクを張らない。
        """
        for case in load()["cases"]:
            for order in case["orders"]:
                with self.subTest(lot=case["lot_no"], order=order["order_no"]):
                    self.assertIsInstance(order["spec_no"], str)


if __name__ == "__main__":
    unittest.main()
