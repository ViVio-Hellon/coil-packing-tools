# -*- coding: utf-8 -*-
"""印刷位置合わせ（試し刷りの実測 → 補正値）の検証。

現場の求めは「大きくずれる／ダイアログに倍率も余白も無い」。
ブラウザーが勝手に縮めて刷る場合でも、
**測った mm を入れるだけ**で元へ戻せることを確かめる。
"""

import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from modules.packing_pena_label import server
from modules.packing_pena_label.app.config import Config                          # noqa: E402
from modules.packing_pena_label.app.services import label_align as LA             # noqa: E402
from modules.packing_pena_label.app.services import label_sheet as LS             # noqa: E402


def printed(value_mm, offset, scale_pct, p, o):
    """紙に出る位置。``p`` はブラウザー側の倍率、``o`` は原点ズレ(mm)。"""
    return p * (offset + value_mm * scale_pct / 100.0) + o


class AlignMathTest(unittest.TestCase):
    """割り算を現場にさせないための計算。"""

    def test_exact_size_needs_no_change(self):
        r = LA.plan(0, 0, 100, span_x=100.0, span_y=100.0,
                    cross_x=20.0, cross_y=20.0)
        self.assertAlmostEqual(r.scale_pct, 100.0, places=2)
        self.assertAlmostEqual(r.offset_x_mm, 0.0, places=2)
        self.assertAlmostEqual(r.offset_y_mm, 0.0, places=2)

    def test_shrunk_print_is_scaled_back_up(self):
        """94mm しか出ていないなら倍率を上げる（100×100÷94）。"""
        r = LA.plan(0, 0, 100, span_x=94.0)
        self.assertAlmostEqual(r.scale_pct, 106.38, places=2)

    def test_one_round_of_measure_lands_within_a_tenth(self):
        """ブラウザーが 0.94 倍・右下へ 5mm ずらして刷る端末を模す。

        測って入れる → その値で刷ると、設計どおりの位置に戻ること。
        （ダイアログに倍率・余白が無くても吸収できる、の裏付け）
        """
        p, o = 0.94, 5.0
        sx = printed(LA.SPAN_MM + LA.CROSS_X_MM, 0, 100, p, o) \
            - printed(LA.CROSS_X_MM, 0, 100, p, o)
        cx = printed(LA.CROSS_X_MM, 0, 100, p, o)
        cy = printed(LA.CROSS_Y_MM, 0, 100, p, o)

        r = LA.plan(0, 0, 100, span_x=sx, span_y=sx, cross_x=cx, cross_y=cy)

        # 新しい補正で刷り直したときの、紙に出る位置
        again_cross = printed(LA.CROSS_X_MM, r.offset_x_mm, r.scale_pct, p, o)
        again_span = (printed(LA.SPAN_MM + LA.CROSS_X_MM, r.offset_x_mm,
                              r.scale_pct, p, o)
                      - printed(LA.CROSS_X_MM, r.offset_x_mm, r.scale_pct, p, o))
        self.assertAlmostEqual(again_cross, LA.CROSS_X_MM, places=1)
        self.assertAlmostEqual(again_span, LA.SPAN_MM, places=1)

        again_y = printed(LA.CROSS_Y_MM, r.offset_y_mm, r.scale_pct, p, o)
        self.assertAlmostEqual(again_y, LA.CROSS_Y_MM, places=1)

    def test_repeating_the_measure_converges(self):
        """1 回目が合わなくても、繰り返せば必ず近づくこと。"""
        p, o = 0.88, 7.5
        cal = (0.0, 0.0, 100.0)
        for _ in range(3):
            sx = (printed(LA.SPAN_MM + LA.CROSS_X_MM, cal[0], cal[2], p, o)
                  - printed(LA.CROSS_X_MM, cal[0], cal[2], p, o))
            cx = printed(LA.CROSS_X_MM, cal[0], cal[2], p, o)
            cy = printed(LA.CROSS_Y_MM, cal[1], cal[2], p, o)
            r = LA.plan(cal[0], cal[1], cal[2], span_x=sx, cross_x=cx, cross_y=cy)
            cal = (r.offset_x_mm, r.offset_y_mm, r.scale_pct)
        self.assertAlmostEqual(
            printed(LA.CROSS_X_MM, cal[0], cal[2], p, o), LA.CROSS_X_MM, places=1)

    def test_offset_only_moves_by_the_difference(self):
        """倍率を測らなければ、位置は差のぶんだけ動くこと。"""
        r = LA.plan(0, 0, 100, cross_x=22.5, cross_y=21.5)
        self.assertAlmostEqual(r.offset_x_mm, -2.5, places=2)
        self.assertAlmostEqual(r.offset_y_mm, -1.5, places=2)
        self.assertEqual(r.scale_pct, 100.0)

    def test_blank_fields_are_left_alone(self):
        r = LA.plan(1.5, -2.0, 101.0, span_x="", span_y=None, cross_x="21.0",
                    cross_y="")
        self.assertEqual(r.offset_y_mm, -2.0, "空欄の縦は触らない")
        self.assertEqual(r.scale_pct, 101.0, "空欄の倍率は触らない")
        self.assertNotEqual(r.offset_x_mm, 1.5, "入れた横は動くこと")

    def test_all_blank_is_refused(self):
        with self.assertRaises(LA.AlignError):
            LA.plan(0, 0, 100)

    def test_non_number_is_refused(self):
        with self.assertRaises(LA.AlignError):
            LA.plan(0, 0, 100, span_x="１００ミリ")

    def test_full_width_digits_are_accepted(self):
        """現場の入力は半角とは限らない。"""
        r = LA.plan(0, 0, 100, span_x="９６．０")
        self.assertAlmostEqual(r.scale_pct, 104.17, places=2)

    def test_wildly_wrong_measure_is_refused(self):
        """測る線を間違えた入力は、黙って受けずに断ること。"""
        with self.assertRaises(LA.AlignError):
            LA.plan(0, 0, 100, span_x=297.0)
        with self.assertRaises(LA.AlignError):
            LA.plan(0, 0, 100, cross_x=150.0)

    def test_axis_difference_is_reported_not_hidden(self):
        r = LA.plan(0, 0, 100, span_x=99.0, span_y=101.0)
        self.assertAlmostEqual(r.scale_pct, 100.0, places=2)
        self.assertTrue(any("違います" in m for m in r.messages),
                        "横と縦で違うことを黙らない")

    def test_values_stay_inside_the_settable_range(self):
        r = LA.plan(0, 0, 100, span_x=80.0)
        self.assertLessEqual(r.scale_pct, LA.SCALE_MAX)
        r2 = LA.nudge(49.0, 0, 100, dx=10)
        self.assertEqual(r2.offset_x_mm, LA.OFFSET_MAX)
        self.assertTrue(r2.clamped)
        self.assertTrue(r2.messages)

    def test_nudge_moves_by_the_asked_amount(self):
        r = LA.nudge(1.0, -1.0, 100.0, dx=0.5, dy=-0.5)
        self.assertEqual((r.offset_x_mm, r.offset_y_mm), (1.5, -1.5))


class RulerSheetTest(unittest.TestCase):
    """試し刷りそのもの。"""

    def setUp(self):
        self.stock = LS.get_stock("", "")

    def test_ruler_is_printed_with_the_correction_applied(self):
        """目盛りも補正の中に入れること。

        補正の外に置くと、補正を変えても試し刷りが変わらないため
        「効いていない」と誤解する。
        """
        html = LS.render_ruler_page(self.stock, LS.Calibration(3.0, 2.0, 101.0))
        inner = html.split('class="sheet-inner"', 1)[1]
        head = inner[:400]
        self.assertIn("translate(3.000mm,2.000mm)", head)
        # 目盛り・測定線・基準十字が transform の内側にあること
        body = inner.split("</div>", 1)[0] + inner
        for cls in ("tick", "mline", "cross", "crosslab"):
            self.assertIn('class="%s' % cls, inner)
            self.assertNotIn('class="%s' % cls, html.split('class="sheet-inner"')[0])

    def test_measure_marks_use_the_same_numbers_as_the_form(self):
        """紙に刷る設計値と、画面が計算に使う設計値がずれないこと。"""
        html = LS.render_ruler_page(self.stock)
        self.assertIn("この 2 本の間が %.1fmm" % LA.SPAN_MM, html)
        self.assertIn("紙の左端から %.1fmm" % LA.CROSS_X_MM, html)
        self.assertIn("上端から %.1fmm" % LA.CROSS_Y_MM, html)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AlignHttpTest(unittest.TestCase):
    """画面から押したときに、実際に保存されて効くこと。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "梱包資材マスタ.sqlite3")
        c = sqlite3.connect(cls.db)
        c.execute("CREATE TABLE 資材重量 (管理番号 INTEGER, 梱包資材名 TEXT,"
                  " 単位質量 TEXT, 係数 TEXT)")
        c.execute("INSERT INTO 資材重量 VALUES (1,'テスラピン','0.5','1')")
        c.commit()
        c.close()
        cfg = Config()
        cfg.port = free_port()
        cfg.prefer_access = False
        cfg.material_db_file = cls.db
        cls.orig_local_dir = Config.__dict__["local_dir"]
        Config.local_dir = property(lambda self, d=cls.tmp: d)
        cls.httpd, cls.ctx = server.create_server(cfg)
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, kwargs={"poll_interval": 0.1},
            daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cfg.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.ctx.store.close()
        Config.local_dir = cls.orig_local_dir
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as r:
            return r.read().decode("utf-8")

    def tearDown(self):
        self.post("/api/label/align", {"mode": "reset"})

    def test_nudge_is_saved_and_shows_on_the_sheet(self):
        st, j = self.post("/api/label/align",
                          {"mode": "nudge", "dx": 2.5, "dy": 1.5})
        self.assertEqual(st, 200, j)
        self.assertTrue(j["ok"], j)
        self.assertEqual((j["offsetXMm"], j["offsetYMm"]), (2.5, 1.5))
        html = self.get("/labels/calibration")
        self.assertIn("translate(2.500mm,1.500mm)", html)

    def test_measure_is_saved(self):
        st, j = self.post("/api/label/align",
                          {"mode": "measure", "spanX": "96.0",
                           "crossX": "22.0", "crossY": "21.0"})
        self.assertEqual(st, 200, j)
        self.assertAlmostEqual(j["scalePct"], 104.17, places=2)
        html = self.get("/labels/calibration")
        self.assertIn("104.17", html)

    def test_bad_input_is_refused_without_changing_anything(self):
        self.post("/api/label/align", {"mode": "nudge", "dx": 1.0})
        st, j = self.post("/api/label/align",
                          {"mode": "measure", "spanX": "あ"})
        self.assertEqual(st, 200)
        self.assertFalse(j["ok"])
        html = self.get("/labels/calibration")
        self.assertIn("translate(1.000mm,0.000mm)", html,
                      "断ったのに値が変わってはいけない")

    def test_reset_returns_to_no_correction(self):
        self.post("/api/label/align", {"mode": "nudge", "dx": 3.0})
        st, j = self.post("/api/label/align", {"mode": "reset"})
        self.assertEqual((j["offsetXMm"], j["offsetYMm"], j["scalePct"]),
                         (0.0, 0.0, 100.0))

    def test_page_says_the_print_dialog_needs_no_change(self):
        """「毎回ダイアログを直すのはやってられない」への答え。

        印刷ページは既定のままで等倍・余白なしになる作りなので、
        画面は「何も変えずにそのまま印刷」と案内すること
        （以前は毎回「倍率100%・余白なし」を選ばせていた）。
        """
        html = self.get("/labels/calibration")
        self.assertIn("何も変えずに", html)
        self.assertNotIn("倍率 100%」「余白なし」を選んで", html)
        self.assertIn("刷れません", html)

    def test_current_correction_is_said_in_words(self):
        """「X +2.50mm」ではなく「右へ 2.5mm」と言うこと。"""
        self.post("/api/label/align", {"mode": "nudge", "dx": 2.5, "dy": -1.5})
        html = self.get("/labels/calibration")
        self.assertIn("右へ <b>2.5mm</b>・上へ <b>1.5mm</b>", html)

    def test_gap_direction_left_means_move_right(self):
        """「印刷が左へ 2.5mm ずれている」は右へ 2.5mm 動かすこと（画面の約束）。"""
        html = self.get("/labels/calibration")
        self.assertIn('(el("calDirX").value === "left") ? gx : -gx', html)
        self.assertIn('(el("calDirY").value === "up") ? gy : -gy', html)


    # ---------------- 合言葉（入れただけでは分からない、の対策）----------------
    def test_right_password_is_told_so(self):
        st, j = self.post("/api/settings/check-password", {"password": "nisk"})
        self.assertEqual(st, 200)
        self.assertTrue(j["valid"])

    def test_wrong_password_is_told_so(self):
        st, j = self.post("/api/settings/check-password", {"password": "x"})
        self.assertFalse(j["valid"])

    def test_empty_password_is_told_so(self):
        st, j = self.post("/api/settings/check-password", {"password": ""})
        self.assertFalse(j["valid"])

    def test_settings_page_has_the_button(self):
        html = self.get("/settings")
        self.assertIn('id="setPwCheck"', html)
        self.assertIn('id="setPwState"', html)


    # ---------------- 版が動いたことを画面で分かるように ----------------
    def test_health_carries_version_and_stamp(self):
        st, j = self.post("/api/health", {})
        self.assertEqual(st, 200)
        self.assertTrue(j["version"])
        self.assertTrue(j["build"])
        self.assertEqual(len(j["codeStamp"]), 8)

    def test_pages_show_version_build_and_stamp(self):
        html = self.get("/labels/calibration")
        from modules.packing_pena_label.app.config import APP_VERSION, APP_BUILD, code_stamp
        self.assertIn("v" + APP_VERSION, html)
        self.assertIn(APP_BUILD, html)
        self.assertIn(code_stamp(), html)


if __name__ == "__main__":
    unittest.main()
