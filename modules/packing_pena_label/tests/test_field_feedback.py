# -*- coding: utf-8 -*-
"""現場の指摘で直したところ(ペナラベル 1.5.4)。

- 重量計算_DB: 重いのはいいが進み具合を出す(`/api/progress`・画面の棒)
- 参照先(外部ファイル): 欄ごとに保存できる(「この欄を保存」)
- 合言葉: 聞き直すときも伏せ字(`prompt()` は伏せ字にできないので使わない)
"""

import os
import re
import unittest

from modules.packing_pena_label.app.services.workflow import Progress

from .test_workflow import WorkflowTestBase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class TestProgress(unittest.TestCase):
    def test_steps(self):
        p = Progress()
        self.assertFalse(p.snapshot()["active"])
        p.begin("重量計算", 3)
        p.step("資材マスタを確かめています")
        s = p.snapshot()
        self.assertTrue(s["active"])
        self.assertEqual((s["step"], s["total"], s["text"]), (1, 3, "資材マスタを確かめています"))
        for _ in range(5):                       # 数えすぎても total で止まる
            p.step("x")
        self.assertEqual(p.snapshot()["step"], 3)
        p.end()
        self.assertFalse(p.snapshot()["active"])


class TestCalcTareProgress(WorkflowTestBase):
    def _ready(self):
        st = self._cb_state(5, kensa_no="W111111", weight1="10", weight2="12")
        self.wf.apply_weight(st)
        st.coil_h = {1: "11", 2: "11", 3: "10", 4: "10"}
        return st

    def test_each_stage_is_reported(self):
        seen = []
        real_step = self.wf.progress.step
        self.wf.progress.step = lambda text: (seen.append(text), real_step(text))
        r = self.wf.calc_tare(self._ready())
        self.assertTrue(r.ok, r.message)
        # 資材マスタ → 4梱包 → ラベルへ写す → 保存
        self.assertEqual(seen[0], "資材マスタを確かめています")
        self.assertEqual(len([t for t in seen if "梱包目" in t]), 4)
        self.assertEqual(seen[-2:], ["ラベルへ写しています", "保存しています"])
        snap = self.wf.progress.snapshot()
        self.assertFalse(snap["active"])
        self.assertEqual(snap["step"], snap["total"])

    def test_progress_ends_even_when_refused(self):
        st = self._cb_state(5, kensa_no="W111111", weight1="10", weight2="12")
        st.coil_h = {1: "11"}
        st.selected_cb = 0                       # 選択なし → 断られる
        st.selected_ob = 0
        self.wf.calc_tare(st)
        self.assertFalse(self.wf.progress.snapshot()["active"])


class TestScreens(unittest.TestCase):
    def test_calc_tare_shows_progress(self):
        js = _read("app", "static", "js", "app.js")
        body = js[js.index("calcTare: function"):js.index("clearAll: function")]
        self.assertIn("progressBar(", body)
        self.assertIn('url("/api/progress")', js)

    def test_password_is_never_asked_in_plain_text(self):
        pages = _read("app", "routes", "pages.py")
        self.assertNotRegex(pages, r"window\.prompt\([^)]*合言葉")
        self.assertIn("pplAskPassword", pages)
        js = _read("app", "static", "js", "app.js")
        self.assertRegex(js, r'box\.type = "password"')

    def test_each_path_row_has_its_own_save(self):
        """フォルダー・ファイルの欄には「この欄を保存」。その欄だけを送る。"""
        pages = _read("app", "routes", "pages.py")
        self.assertIn('data-save-one="{esc(key)}"', pages)
        self.assertRegex(pages, r'it\["kind"\] in \("dir", "file"\)')
        self.assertIn("values[only] = all[only]", pages)


if __name__ == "__main__":
    unittest.main()
