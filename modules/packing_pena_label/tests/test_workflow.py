# -*- coding: utf-8 -*-
"""業務フローの結合検証（UserForm のボタン相当）。"""

import os
import shutil
import sys
import tempfile
import unittest

from modules.packing_pena_label.app.config import Config
from modules.packing_pena_label.app.models import FormState
from modules.packing_pena_label.app.repositories.material_repo import MaterialRepository
from modules.packing_pena_label.app.repositories.sqlite_store import Store
from modules.packing_pena_label.app.services import size_master as SM
from modules.packing_pena_label.app.services.size_master import SizeMaster
from modules.packing_pena_label.app.services.workflow import Workflow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class WorkflowTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cfg = Config()
        cfg.prefer_access = False
        self.cfg = cfg
        self.store = Store(os.path.join(self.tmp, "state.sqlite3"))
        self.master = SizeMaster(os.path.join(ROOT, "data", "size_master.json"))
        self.repo = MaterialRepository(
            "", os.path.join(ROOT, "data", "資材重量.csv"), prefer_access=False)
        self.wf = Workflow(self.store, self.master, self.repo, cfg)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cb_state(self, cb=5, **kw):
        st = FormState()
        self.wf.select_checkbox(st, cb)
        st.tip = "TIP1000"
        for k, v in kw.items():
            setattr(st, k, v)
        return st


class TestSelection(WorkflowTestBase):
    def test_ob_selection_is_exclusive_with_cb(self):
        st = FormState()
        self.wf.select_checkbox(st, 5)
        self.assertEqual(st.selected_cb, 5)
        self.wf.select_size(st, 1)
        self.assertEqual(st.selected_ob, 1)
        self.assertEqual(st.selected_cb, 0, "OB 選択で CB が解除されていない")

    def test_cb_selection_clears_ob(self):
        st = FormState()
        self.wf.select_size(st, 3)
        self.wf.select_checkbox(st, 4)
        self.assertEqual(st.selected_ob, 0)
        self.assertEqual(st.selected_cb, 4)

    def test_take_visibility_ob(self):
        st = FormState()
        self.wf.select_size(st, 5)          # 奇数 = 丈1
        self.assertTrue(st.take1_visible)
        self.assertFalse(st.take2_visible)
        self.wf.select_size(st, 6)          # 偶数 = 丈2
        self.assertFalse(st.take1_visible)
        self.assertTrue(st.take2_visible)

    def test_take_visibility_cb(self):
        st = self._cb_state(5)
        self.assertTrue(st.take1_visible)
        self.assertTrue(st.take2_visible)

    def test_ob5_clears_coil_info(self):
        """VBA: OB5/6 と lblSpec_08_53_1/2 のみ CoilH/NW/GW/TA をクリアする。"""
        st = FormState()
        st.coil_h = {1: "11", 2: "11", 3: "10", 4: "10"}
        st.nw = {1: "1", 2: "1", 3: "1", 4: "1"}
        self.wf.select_size(st, 5)
        self.assertEqual(st.coil_h[1], "")
        self.assertEqual(st.coil_h[2], "")
        self.assertEqual(st.coil_h[3], "10", "丈2 側まで消してはいけない")

    def test_ob1_does_not_clear_coil_info(self):
        st = FormState()
        st.coil_h = {1: "11", 2: "11", 3: "", 4: ""}
        self.wf.select_size(st, 1)          # OB1 は ClearCoilInfo を呼ばない
        self.assertEqual(st.coil_h[1], "11")


class TestApplyWeight(WorkflowTestBase):
    def test_cb_writes_both_sheets(self):
        st = self._cb_state(5, kensa_no="w111111", weight1="10", weight2="12")
        r = self.wf.apply_weight(st)
        self.assertTrue(r.ok, r.message)
        names = [w["sheetName"] for w in r.data["written"]]
        self.assertEqual(names, ["1.0mm×53.5mm 　丈1", "1.0mm×53.5mm 　丈2"])
        self.assertEqual(st.lbl_weight1, "10kg")
        self.assertEqual(st.lbl_weight2, "12kg")
        self.assertEqual(st.lbl_kensa_no, "W111111")

    def test_ob_writes_one_sheet(self):
        st = FormState()
        self.wf.select_size(st, 5)
        st.kensa_no, st.weight1 = "W111111", "10"
        r = self.wf.apply_weight(st)
        self.assertTrue(r.ok)
        self.assertEqual(len(r.data["written"]), 1)
        self.assertEqual(r.data["written"][0]["takeType"], 1)

    def test_invalid_kensa_no_blocks(self):
        st = self._cb_state(5, kensa_no="1111111", weight1="10", weight2="12")
        r = self.wf.apply_weight(st)
        self.assertFalse(r.ok)
        self.assertIn("7桁目に数字", r.message)

    def test_missing_weight_blocks(self):
        st = self._cb_state(5, kensa_no="W111111", weight1="10", weight2="")
        r = self.wf.apply_weight(st)
        self.assertFalse(r.ok)
        self.assertEqual(r.message, "重量入力がありません　丈1　or　丈2")

    def test_cells_land_on_sheet(self):
        st = self._cb_state(5, kensa_no="W111111", weight1="10", weight2="12")
        self.wf.apply_weight(st)
        # 検査番号ヘッダー (3,22) と重量ヘッダー (3,37)
        self.assertEqual(self.store.get_cell(5, 3, SM.HDR_KEN_COL), "W111111")
        self.assertEqual(self.store.get_cell(5, 3, SM.HDR_WT1_COL), "10")
        # 丈2 側は重量 12
        self.assertEqual(self.store.get_cell(6, 3, SM.HDR_WT1_COL), "12")
        # ラベル1枚目のコイル番号
        self.assertEqual(self.store.get_cell(5, 8, SM.COL_COIL_L), "-0101")
        self.assertEqual(self.store.get_cell(6, 8, SM.COL_COIL_L), "-0201")


class TestCalcTare(WorkflowTestBase):
    def _ready(self):
        st = self._cb_state(5, kensa_no="W111111", weight1="10", weight2="12")
        self.wf.apply_weight(st)
        st.coil_h = {1: "11", 2: "11", 3: "10", 4: "10"}
        return st

    def test_full_cycle(self):
        st = self._ready()
        r = self.wf.calc_tare(st)
        self.assertTrue(r.ok, r.message)
        self.assertEqual(st.nw[1], "110.0")     # 11本 × 10kg
        self.assertEqual(st.nw[3], "120.0")     # 10本 × 12kg
        self.assertEqual(st.ta[1], "910.0")
        self.assertEqual(len(r.data["tare"]), 4)

    def test_results_transferred_to_sheets(self):
        st = self._ready()
        self.wf.calc_tare(st)
        # 丈1 は ob5、丈2 は ob6 へ転記される
        self.assertEqual(self.store.get_cell(5, 3, SM.COL_COILH), "11")
        self.assertEqual(self.store.get_cell(5, 3, SM.COL_NW), "110.0")
        self.assertEqual(self.store.get_cell(6, 3, SM.COL_COILH), "10")
        self.assertEqual(self.store.get_cell(6, 3, SM.COL_NW), "120.0")
        # ob5 は extraRow=92 なので複写される
        self.assertEqual(self.store.get_cell(5, 92, SM.COL_COILH), "11")

    def test_empty_coil_does_nothing(self):
        st = self._cb_state(5, kensa_no="W111111", weight1="10", weight2="12")
        r = self.wf.calc_tare(st)
        self.assertIn("本数が入力されていません", r.message)

    def test_no_tip_is_rejected(self):
        st = self._ready()
        st.tip = ""
        r = self.wf.calc_tare(st)
        self.assertFalse(r.ok)
        self.assertEqual(r.message, "チップボールチェックがありません")

    def test_no_size_is_rejected(self):
        st = self._ready()
        st.selected_cb = 0
        st.selected_ob = 0
        st.named_cb = False
        r = self.wf.calc_tare(st)
        self.assertFalse(r.ok)
        self.assertEqual(r.message, "コイルサイズが選択されていません")

    def test_tip_is_checked_before_size(self):
        """VBA の判定順（チップボール → 丈フレーム → サイズ）を保つ。"""
        st = self._ready()
        st.tip = ""
        st.selected_cb = 0
        st.selected_ob = 0
        st.named_cb = False
        r = self.wf.calc_tare(st)
        self.assertEqual(r.message, "チップボールチェックがありません")

    def test_clear_wipes_sheet_and_form(self):
        st = self._ready()
        self.wf.calc_tare(st)
        self.wf.clear_all(st)
        self.assertEqual(st.nw[1], "")
        self.assertEqual(st.ta[1], "")
        self.assertEqual(self.store.get_cell(5, 3, SM.COL_COILH), "")
        # ラベル本体（検番・型番）は消さない
        self.assertEqual(self.store.get_cell(5, 3, SM.HDR_KEN_COL), "W111111")
        self.assertEqual(self.store.get_cell(5, 8, SM.COL_COIL_L), "-0101")


class TestWidthSelection(WorkflowTestBase):
    def test_ob_overrides_cb(self):
        """VBA は CB の Select Case のあとに OB の Select Case を評価する。"""
        st = FormState()
        st.selected_cb = 5          # 53.5
        st.selected_ob = 1          # 73（後勝ち）
        self.assertEqual(self.wf._selected_width(st), 73.0)

    def test_named_cb_width(self):
        st = FormState()
        st.named_cb = True
        self.assertEqual(self.wf._selected_width(st), 53.5)

    def test_list_width_named_cb_is_hardcoded(self):
        st = FormState()
        st.named_cb = True
        self.assertEqual(self.wf._list_width(st), 53.5)


class TestPrintTargets(WorkflowTestBase):
    def test_ob_single_target(self):
        st = FormState()
        self.wf.select_size(st, 5)
        r = self.wf.print_targets(st)
        self.assertEqual(r.data["targets"], [5])

    def test_cb_two_targets(self):
        st = self._cb_state(5)
        r = self.wf.print_targets(st)
        self.assertEqual(r.data["targets"], [5, 6])

    def test_named_cb_targets(self):
        st = FormState()
        self.wf.select_checkbox(st, 0, named=True)
        r = self.wf.print_targets(st)
        self.assertEqual(r.data["targets"], [13, 14])

    def test_nothing_selected(self):
        r = self.wf.print_targets(FormState())
        self.assertFalse(r.ok)



class TestHbFormulaGating(WorkflowTestBase):
    """HB 行の計算式は、他の資材と同じように**必ず出す**（A-5 / 2026.09）。

    VBA では `WriteSheetFormulas` が HB 行だけ書かず、`WriteHBToSheet` が
    **シートのセルへ直接書いて**いた。その条件が `CoilH1 <> ""` だったため、
    丈1の1梱包目が空だと計算式の欄だけ空になる、という副作用があった。

    移植版は画面をデータから描くので **セルへ書く処理そのものが無い**。
    計算式は `mat.formulas["HBsiki"]` に必ず入っているため、
    出す・出さないの条件を持つ理由が無い。
    **数値には影響しない**（表示欄だけ）。
    """

    def _run(self, coil_h):
        st = self._cb_state(5, kensa_no="W111111", weight1="10", weight2="12")
        self.wf.apply_weight(st)
        st.coil_h = coil_h
        return self.wf.calc_tare(st)

    def _hb_row(self, payload):
        return next(r for r in payload["rows"] if r["name"] == "ﾊｰﾄﾞﾎﾞｰﾄﾞ")

    def test_formula_present_when_coil1_filled(self):
        r = self._run({1: "11", 2: "11", 3: "", 4: ""})
        hb = self._hb_row(r.data["tare"]["1"])
        self.assertNotEqual(hb["value"], "")
        self.assertIn("ハードボード", hb["formula"])

    def test_formula_present_when_coil1_empty(self):
        """CoilH1 が空でも計算式が出る（旧 VBA は空だった）。"""
        r = self._run({1: "", 2: "11", 3: "", 4: ""})
        hb = self._hb_row(r.data["tare"]["2"])
        self.assertNotEqual(hb["value"], "", "HB の重量")
        self.assertIn("ハードボード", hb["formula"], "HB の計算式が空のまま")

    def test_formula_present_for_every_filled_coil(self):
        """丈1・丈2、1梱包目・2梱包目のどれでも出る。"""
        r = self._run({1: "11", 2: "12", 3: "13", 4: "14"})
        for coil in ("1", "2", "3", "4"):
            hb = self._hb_row(r.data["tare"][coil])
            self.assertIn("ハードボード", hb["formula"],
                          "梱包 %s の計算式が空" % coil)

    def test_empty_coils_produce_no_block(self):
        """本数が空の梱包はそもそも計算されない（VBA と同じ）。"""
        r = self._run({1: "", 2: "11", 3: "", 4: ""})
        self.assertNotIn("1", r.data["tare"])
        self.assertIn("2", r.data["tare"])

    def test_totals_unaffected(self):
        """計算式の扱いを変えても風袋・GW の数値は変わらない。"""
        r = self._run({1: "", 2: "11", 3: "", 4: ""})
        p = r.data["tare"]["2"]
        hu = next(x for x in p["rows"] if x["name"] == "風袋")["value"]
        self.assertNotEqual(hu, "")
        self.assertNotEqual(hu, "0.0")

    def test_every_material_row_has_a_formula(self):
        """HB だけが特別扱いされていないこと。"""
        r = self._run({1: "11", 2: "", 3: "", 4: ""})
        rows = r.data["tare"]["1"]["rows"]
        for row in rows:
            self.assertTrue(row["formula"].strip(),
                            "%s の計算式が空" % row["name"])

    def test_numbers_match_regardless_of_which_coil_is_filled(self):
        """同じ本数なら、1梱包目に入れても2梱包目に入れても数値は同じ。"""
        a = self._run({1: "11", 2: "", 3: "", 4: ""}).data["tare"]["1"]
        b = self._run({1: "", 2: "11", 3: "", 4: ""}).data["tare"]["2"]
        for key in ("NW", "HU", "GW"):
            self.assertEqual(a["display"][key], b["display"][key], key)


class TestStaleCalcGuard(WorkflowTestBase):
    """入力を変えて「計算」せずに刷ると値が混ざる件の検出。

    ラベルの **本数・高さ・NW・GW** は「計算」を押したときにしか
    書き換わらない（VBA も同じ）。一方 **検番・重量** は「重量反映」で
    書き換わる。そのため入力を変えて反映だけして印刷すると、
    **新しい検番と古い NW/GW が混ざったラベル**が出てしまう。

    値は VBA どおりのままにし、**食い違いを知らせる**だけにしてある。
    """

    def _ob_state(self, ken="Z987654", w2="22.0", n="11"):
        st = FormState()
        self.wf.select_size(st, 14)          # 0.8mm×53.5mm 丈2
        st.tip = "TIP1000"
        st.kensa_no = ken
        st.weight1 = ""
        st.weight2 = w2
        st.coil_h = {1: "", 2: "", 3: n, 4: n}
        return st

    def _run(self, st, calc=True):
        self.wf.apply_weight(st)
        if calc:
            self.wf.calc_tare(st)
        self.wf.save_state(st)

    def test_not_stale_right_after_calculating(self):
        self._run(self._ob_state())
        self.assertFalse(self.wf.calc_is_stale())
        self.assertFalse(self.wf.label_view(14)["stale"])

    def test_stale_when_inputs_changed_without_recalculating(self):
        self._run(self._ob_state("Z987654", "22.0", "11"))
        self._run(self._ob_state("Z111111", "33.0", "20"), calc=False)
        self.assertTrue(self.wf.calc_is_stale(), "食い違いを検出できていない")
        self.assertTrue(self.wf.label_view(14)["stale"])

    def test_the_mismatch_is_real(self):
        """警告が出ている状態では、実際に検番と NW が食い違っている。"""
        self._run(self._ob_state("Z987654", "22.0", "11"))
        before = self.wf.label_view(14)["printHeader"]
        self._run(self._ob_state("Z111111", "33.0", "20"), calc=False)
        after = self.wf.label_view(14)["printHeader"]
        self.assertEqual(after["kensaNo"], "Z111111", "検番は新しくなる")
        self.assertEqual(after["nw1"], before["nw1"], "NW は古いまま（VBA と同じ）")
        self.assertEqual(after["coilH1"], before["coilH1"], "本数も古いまま")

    def test_recalculating_clears_the_warning_and_fixes_values(self):
        self._run(self._ob_state("Z987654", "22.0", "11"))
        self._run(self._ob_state("Z111111", "33.0", "20"), calc=False)
        self.assertTrue(self.wf.calc_is_stale())

        self._run(self._ob_state("Z111111", "33.0", "20"))
        self.assertFalse(self.wf.calc_is_stale())
        h = self.wf.label_view(14)["printHeader"]
        self.assertEqual(h["coilH1"], "20")
        self.assertEqual(h["nw1"], "660.0", "20本 × 33.0kg")

    def test_no_warning_before_the_first_calculation(self):
        """一度も計算していなければ古い値は載っていない。"""
        self.assertFalse(self.wf.calc_is_stale())
        self._run(self._ob_state(), calc=False)
        self.assertFalse(self.wf.calc_is_stale())

    def test_clear_resets_the_guard(self):
        st = self._ob_state()
        self._run(st)
        self.assertFalse(self.wf.calc_is_stale())
        self.wf.clear_all(st)
        self.assertFalse(self.wf.calc_is_stale(), "クリア後に警告が残っている")

    def test_every_input_that_moves_the_numbers_is_watched(self):
        """本数・高さ・NW・GW を動かす入力は、どれを変えても検出する。"""
        base = dict(ken="Z987654", w2="22.0", n="11")
        for field, value in (("ken", "Z111111"), ("w2", "33.0"), ("n", "20")):
            self._run(self._ob_state(**base))
            self.assertFalse(self.wf.calc_is_stale(), field)
            changed = dict(base); changed[field] = value
            self._run(self._ob_state(**changed), calc=False)
            self.assertTrue(self.wf.calc_is_stale(),
                            "%s を変えても検出していない" % field)

    def test_size_and_tip_changes_are_watched(self):
        st = self._ob_state()
        self._run(st)
        st2 = self._ob_state()
        self.wf.select_size(st2, 13)          # 丈1 側へ
        self.wf.apply_weight(st2)
        self.wf.save_state(st2)
        self.assertTrue(self.wf.calc_is_stale(), "サイズ変更を検出していない")

        st3 = self._ob_state()
        self._run(st3)
        st3.tip = "TIP820"
        self.wf.save_state(st3)
        self.assertTrue(self.wf.calc_is_stale(), "チップボール変更を検出していない")

    def test_reopening_the_same_inputs_is_not_stale(self):
        """同じ入力のまま画面を開き直しただけでは警告を出さない。"""
        self._run(self._ob_state())
        for _ in range(3):
            self.assertFalse(self.wf.calc_is_stale())
            self.assertFalse(self.wf.label_view(14)["stale"])


class TestHeightsAgree(WorkflowTestBase):
    """積み高さと胴巻きハードボードの高さが一致していること（2026.09 統一）。

    かつては `SetCoilDimensions` と `GetHBCoilDimensions` が別々に計算していて
    値が違っていた。共通関数 `get_coil_ta` へ集約して解消（解析書 A-1）。

    締結代（`HB_BAND_CLEARANCE_M`）は実測と合わなかったため 0。
    将来値を入れた場合に差が出るので、画面はその受け皿を持っている。
    """

    def _payload(self, ob=14, n="11"):
        st = FormState()
        self.wf.select_size(st, ob)
        st.tip = "TIP1000"
        st.kensa_no = "Z987654"
        st.weight1 = ""
        st.weight2 = "22.0"
        st.coil_h = {1: "", 2: "", 3: n, 4: n}
        self.wf.apply_weight(st)
        r = self.wf.calc_tare(st)
        tare = r.data["tare"]
        return tare[sorted(tare)[0]]

    def test_both_heights_are_reported(self):
        p = self._payload()
        self.assertTrue(p["coilHeightMm"])
        self.assertTrue(p["coilHeightHbMm"])

    def test_there_is_no_difference_while_clearance_is_zero(self):
        from modules.packing_pena_label.app.services import tare_calc as TC

        self.assertEqual(TC.HB_BAND_CLEARANCE_M, 0.0)
        for n in ("6", "11", "20"):
            p = self._payload(n=n)
            self.assertEqual(p["coilHeightDiffMm"], "0.0",
                             "%s本で差が出ている" % n)
            self.assertEqual(p["coilHeightHbMm"], p["coilHeightMm"], n)

    def test_the_chipboard_is_included_in_both(self):
        """統一前はハードボード側だけチップボール分が消えていた。"""
        p = self._payload(n="11")
        # 11本 × 53.5mm + 12枚 × 0.7mm = 596.9mm
        self.assertEqual(p["coilHeightMm"], "596.9")
        self.assertEqual(p["coilHeightHbMm"], "596.9")

    def test_numbers_are_unchanged_by_reporting_them(self):
        p = self._payload()
        self.assertEqual(p["taDisplay"], "910.0")
        self.assertEqual(p["display"]["NW"], "242.0")


class TestEmptyWeightShowsNothing(WorkflowTestBase):
    """重量が未入力のとき ``"kg"`` だけを表示しない（B-3）。

    VBA は `Cells(dataRow, 37) & "kg"` としていたため、空セルでも "kg" に
    なっていた。`テスラ全サイズ` 側には
    `If Label17 = "kg" Then Label17 = ""` という後始末があるのに、
    `テスラ指定サイズ` 側に無いだけの取りこぼし。

    **計算には影響しない** ―― `parse_label_weight` は "" も "kg" も 0.0。
    """

    def test_selecting_a_fresh_size_shows_no_unit(self):
        st = FormState()
        self.wf.select_size(st, 13)          # まだ何も書いていない台紙
        self.assertEqual(st.lbl_weight1, "", "重量が空なのに単位だけ出ている")
        self.assertEqual(st.lbl_weight2, "")

    def test_selecting_a_take2_size_shows_no_unit(self):
        st = FormState()
        self.wf.select_size(st, 14)
        self.assertEqual(st.lbl_weight2, "")

    def test_checkbox_mode_shows_no_unit(self):
        st = FormState()
        self.wf.select_checkbox(st, 5)
        self.assertEqual(st.lbl_weight1, "")
        self.assertEqual(st.lbl_weight2, "")

    def test_unit_appears_once_a_weight_is_written(self):
        st = FormState()
        self.wf.select_size(st, 14)
        st.tip = "TIP1000"
        st.kensa_no = "Z987654"
        st.weight2 = "22.0"
        self.wf.apply_weight(st)
        self.assertTrue(st.lbl_weight2.endswith("kg"), st.lbl_weight2)
        self.assertIn("22.0", st.lbl_weight2)

    def test_numbers_are_unchanged(self):
        """"kg" を出さなくしても風袋・GW は変わらない。"""
        from modules.packing_pena_label.app.services import tare_calc as TC

        self.assertEqual(TC.parse_label_weight(""),
                         TC.parse_label_weight("kg"))

        st = FormState()
        self.wf.select_size(st, 14)
        st.tip = "TIP1000"
        st.kensa_no = "Z987654"
        st.weight2 = "22.0"
        st.coil_h = {1: "", 2: "", 3: "11", 4: "11"}
        self.wf.apply_weight(st)
        r = self.wf.calc_tare(st)
        p = r.data["tare"]["3"]
        self.assertEqual(p["display"]["HU"], "37.0")
        self.assertEqual(p["display"]["GW"], "279.0")

if __name__ == "__main__":
    unittest.main()
