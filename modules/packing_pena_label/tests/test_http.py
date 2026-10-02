# -*- coding: utf-8 -*-
"""HTTP 層の結合検証。実際にサーバーを立てて画面とAPIを叩く。"""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from modules.packing_pena_label import server
from modules.packing_pena_label.app.config import Config                        # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HttpTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cfg = Config()
        cfg.port = free_port()
        cfg.prefer_access = False
        # 状態とログをテスト用の一時領域へ隔離する
        cfg.__class__.local_dir = property(lambda self, d=cls.tmp: d)
        cls.cfg = cfg
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
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # --------------------------------------------------------
    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.read().decode("utf-8")

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))


class TestHealth(HttpTestCase):
    def test_health_identifies_app(self):
        """基盤仕様書 2.3: ポートだけでなくアプリIDを返すこと。"""
        status, body = self.get("/api/health")
        self.assertEqual(status, 200)
        j = json.loads(body)
        self.assertEqual(j["appId"], "PackingPenaLabel")
        self.assertTrue(j["ready"])
        self.assertEqual(j["pid"], os.getpid())
        self.assertIn("version", j)


class TestPages(HttpTestCase):
    def test_all_pages_render(self):
        for path in ("/", "/tare", "/list", "/breakdown", "/labels",
                     "/all-size", "/diag"):
            with self.subTest(path=path):
                status, html = self.get(path)
                self.assertEqual(status, 200)
                self.assertIn("<!DOCTYPE html>", html)
                self.assertIn("梱包ペナ", html)

    def test_static_served(self):
        for path in ("/static/css/app.css", "/static/js/app.js"):
            status, _ = self.get(path)
            self.assertEqual(status, 200)

    def test_unknown_page_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/does-not-exist")
        self.assertEqual(cm.exception.code, 404)

    def test_directory_traversal_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/static/../../server.py")
        self.assertIn(cm.exception.code, (403, 404))

    def test_unknown_api_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/nope", {})
        self.assertEqual(cm.exception.code, 404)


class TestFullFlow(HttpTestCase):
    def test_select_apply_calc(self):
        # 1) サイズ選択（丈1,2 同時）
        _, j = self.post("/api/select-checkbox",
                         {"cbIdx": 5, "current": {"selectedCb": 5,
                                                  "tip": "TIP1000"}})
        self.assertTrue(j["ok"])
        self.assertTrue(j["state"]["take1Visible"])
        self.assertTrue(j["state"]["take2Visible"])

        cur = {"selectedCb": 5, "tip": "TIP1000", "kensaNo": "w111111",
               "weight1": "10", "weight2": "12", "coilH": {}}

        # 2) 重量反映
        _, j = self.post("/api/apply-weight", {"current": cur})
        self.assertTrue(j["ok"], j["message"])
        self.assertEqual(j["state"]["lblKensaNo"], "W111111")
        self.assertEqual(len(j["written"]), 2)

        # 3) 風袋計算
        cur["coilH"] = {"1": "11", "2": "11", "3": "10", "4": "10"}
        _, j = self.post("/api/calc-tare", {"current": cur})
        self.assertTrue(j["ok"], j["message"])
        self.assertEqual(j["state"]["nw"]["1"], "110.0")
        self.assertEqual(j["state"]["gw"]["1"], "147.0")
        self.assertEqual(j["state"]["ta"]["1"], "910.0")

        # 4) 帳票が描画される
        status, html = self.get("/tare")
        self.assertEqual(status, 200)
        self.assertIn("丈1_1梱包目:11本", html)
        self.assertIn("147.0", html)

        # 5) 羅列計算
        _, j = self.post("/api/calc-list", {"current": cur})
        self.assertTrue(j["ok"], j["message"])
        self.assertEqual(len(j["list"]["rows"]), 50)
        status, html = self.get("/list")
        self.assertIn("羅列計算", html)

        # 6) 印刷対象
        _, j = self.post("/api/print-targets", {"current": cur})
        self.assertEqual(j["targets"], [5, 6])
        status, html = self.get("/labels/print?ob=5,6")
        self.assertEqual(status, 200)
        self.assertIn("-0101", html)
        self.assertIn("-0209", html)

    def test_invalid_kensa_no_returns_vba_message(self):
        _, j = self.post("/api/apply-weight", {"current": {
            "selectedCb": 5, "tip": "TIP1000", "kensaNo": "1111111",
            "weight1": "10", "weight2": "12", "coilH": {}}})
        self.assertFalse(j["ok"])
        self.assertIn("検査NOが7桁入力されていない", j["message"])

    def test_bad_json_rejected(self):
        req = urllib.request.Request(
            self.base + "/api/state", data=b"{not json",
            method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)


if __name__ == "__main__":
    unittest.main()


class TestSettingsScreen(HttpTestCase):
    """設定画面（ファイルパスの変更）の結合検証。"""

    def _local_path(self):
        return self.ctx.cfg.local_config_path

    def tearDown(self):
        # 端末設定を消して次のテストへ持ち越さない
        self.post("/api/settings/reset", {})

    def test_generated_script_has_no_broken_string(self):
        """画面へ埋め込む JS が途中で切れていないこと。

        Python の """ + '"""' + """ の中に \\n を書くと **本物の改行**になり、
        JS の文字列が行の途中で切れて構文エラーになる。
        その script 全体（＝設定画面の動作すべて）が黙って死ぬ。
        """
        import re
        _, html = self.get("/settings")
        for body in re.findall(r"<script>(.*?)</script>", html, re.S):
            for n, line in enumerate(body.split("\n"), 1):
                code = re.sub(r"\\.", "", line)          # \" などを外す
                if code.lstrip().startswith("//"):
                    continue
                with self.subTest(line=n):
                    self.assertEqual(
                        code.count('"') % 2, 0,
                        "%d 行目で文字列が閉じていない: %s" % (n, line.strip()[:70]))

    def test_password_is_outside_the_subpanes(self):
        """合言葉はどちらの面でも使えること。

        パス設定の面の中に置くと、マスタ管理の面を開いている間は
        隠れてしまい、**マスタを直すときに入力できない**。
        """
        _, html = self.get("/settings")
        head = html.split('<div class="subnav"', 1)[0]
        self.assertIn("setPassword", head,
                      "合言葉がサブタブより下（面の中）にある")

    def test_save_button_is_near_the_top_of_the_paths_pane(self):
        """設定カード 9 枚の下に埋もれないこと。"""
        _, html = self.get("/settings")
        pane = html.split('<div class="subpane" data-sub="paths">', 1)[1]
        self.assertLess(pane.index("setSave"), 900,
                        "保存ボタンが設定カードの下に埋もれている")

    def test_page_renders(self):
        status, html = self.get("/settings")
        self.assertEqual(status, 200)
        self.assertIn("設定の保存先", html)
        self.assertIn("資材マスタの参照先フォルダー", html)

    def test_save_and_live_apply(self):
        """マスタCSVを差し替えると、再起動せずにその内容が読まれる。"""
        import csv
        import os
        import tempfile

        d = tempfile.mkdtemp()
        path = os.path.join(d, "別の資材重量.csv")
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["管理番号", "梱包資材名", "単位質量", "係数"])
            w.writerow(["1", "テスラピン", "9.99", "1"])

        _, j = self.post("/api/settings/save", {
            "values": {"material_csv_file": path, "prefer_access": False},
            "password": "nisk"})          # パス変更には合言葉が要る
        self.assertTrue(j["ok"], j)
        self.assertIn("資材マスタの参照先", j.get("applied", []))

        _, m = self.post("/api/materials", {})
        self.assertEqual(m["path"], path)
        pin = [r["mass"] for r in m["rows"] if r["name"] == "テスラピン"]
        self.assertEqual(pin, ["9.99"], "差し替えたマスタが読まれていない")

    def test_invalid_value_rejected(self):
        _, j = self.post("/api/settings/save",
                         {"values": {"port": "99999"}})
        self.assertFalse(j["ok"])
        self.assertIn("port", j["errors"])

    def test_unreachable_path_saves_with_warning(self):
        _, j = self.post("/api/settings/save",
                         {"values": {"aim_ref_path": "/no/such/share"},
                          "password": "nisk"})
        self.assertTrue(j["ok"], "共有が落ちていても保存はできるべき")
        self.assertTrue(j["warnings"])

    # ---- パス変更の合言葉 ----
    def test_path_change_needs_the_password(self):
        _, j = self.post("/api/settings/save",
                         {"values": {"aim_ref_path": "/some/other/share"}})
        self.assertFalse(j["ok"])
        self.assertTrue(j["needPassword"])
        self.assertIn("aim_ref_path", j["pathKeys"])
        self.assertIn("合言葉", j["message"])

    def test_wrong_password_is_refused(self):
        _, j = self.post("/api/settings/save",
                         {"values": {"aim_ref_path": "/another/share"},
                          "password": "ちがう"})
        self.assertFalse(j["ok"])
        self.assertTrue(j["needPassword"])

    def test_non_path_settings_need_no_password(self):
        _, j = self.post("/api/settings/save",
                         {"values": {"material_refresh_sec": 123}})
        self.assertTrue(j["ok"], j)

    def test_unchanged_path_needs_no_password(self):
        """保存ボタンを押しただけ（値が同じ）なら求めない。"""
        _, j = self.post("/api/settings/save",
                         {"values": {"aim_ref_path": self.cfg.aim_ref_path}})
        self.assertTrue(j["ok"], j)

    def test_restart_required_is_reported(self):
        _, j = self.post("/api/settings/save",
                         {"values": {"port": self.cfg.port + 1}})
        self.assertTrue(j["ok"])
        self.assertTrue(j["restart"])

    def test_test_master_endpoint(self):
        _, j = self.post("/api/settings/test-master", {"values": {
            "aim_ref_path": "", "material_csv_file": "",
            "prefer_access": False, "access_timeout_sec": 60}})
        self.assertTrue(j["ok"], j)
        self.assertIn("detail", j)

    def test_reset_restores_defaults(self):
        import os
        self.post("/api/settings/save", {"values": {"material_refresh_sec": 42}})
        self.assertTrue(os.path.exists(self._local_path()))
        _, j = self.post("/api/settings/reset", {})
        self.assertTrue(j["ok"])
        self.assertFalse(os.path.exists(self._local_path()))


class TestPrintBlockedWhenStale(HttpTestCase):
    """入力が変わっている間は**印刷させない**（A-6 / 2026.09 決定）。

    ボタンを消すだけでは Ctrl+P やメニューから刷れてしまうため、
    印刷そのもの（`@media print`）でもシートを出さない。
    """

    def _cur(self, ken="Z987654", w2="22.0", n="11"):
        return {"selectedOb": 14, "selectedCb": 0, "namedCb": False,
                "tip": "TIP1000", "kensaNo": ken, "weight1": "", "weight2": w2,
                "coilH": {"1": "", "2": "", "3": n, "4": n}}

    def _apply(self, cur, calc=True):
        self.post("/api/apply-weight", {"current": cur})
        if calc:
            self.post("/api/calc-tare", {"current": cur})

    def setUp(self):
        self.post("/api/clear", {"current": self._cur()})

    def test_print_is_available_after_calculating(self):
        self._apply(self._cur())
        _, html = self.get("/labels/print?ob=14")
        self.assertIn('onclick="window.print()"', html, "印刷ボタンが無い")
        self.assertNotIn("印刷する（停止中）", html)
        self.assertNotIn("stopprint", html)

    def test_print_button_is_disabled_when_stale(self):
        self._apply(self._cur())
        self._apply(self._cur("Z111111", "33.0", "20"), calc=False)
        _, html = self.get("/labels/print?ob=14")
        self.assertIn("印刷する（停止中）", html)
        self.assertIn("<button class=\"btn\" disabled", html)
        self.assertNotIn('onclick="window.print()"', html,
                         "押せる印刷ボタンが残っている")

    def test_print_output_itself_is_suppressed(self):
        """Ctrl+P で刷られても、シートは紙に出ない。"""
        self._apply(self._cur())
        self._apply(self._cur("Z111111", "33.0", "20"), calc=False)
        _, html = self.get("/labels/print?ob=14")
        self.assertIn("@media print{.sheet{display:none !important}", html)
        self.assertIn('class="stopprint"', html)
        self.assertIn("印刷できません", html)

    def test_recalculating_restores_printing(self):
        self._apply(self._cur())
        self._apply(self._cur("Z111111", "33.0", "20"), calc=False)
        _, html = self.get("/labels/print?ob=14")
        self.assertIn("印刷する（停止中）", html)

        self._apply(self._cur("Z111111", "33.0", "20"))
        _, html = self.get("/labels/print?ob=14")
        self.assertIn('onclick="window.print()"', html, "印刷が戻っていない")
        self.assertNotIn("stopprint", html)
        self.assertIn("660.0", html, "新しい NW（20本 × 33.0kg）が出ていない")

    def test_label_list_hides_the_print_link_when_stale(self):
        self._apply(self._cur())
        _, html = self.get("/labels")
        self.assertIn("/labels/print?ob=14", html)

        self._apply(self._cur("Z111111", "33.0", "20"), calc=False)
        _, html = self.get("/labels")
        self.assertIn("印刷（停止中）", html)
        self.assertNotIn("/labels/print?ob=14", html,
                         "一覧から印刷へ飛べてしまう")

    def test_the_warning_explains_what_to_do(self):
        self._apply(self._cur())
        self._apply(self._cur("Z111111", "33.0", "20"), calc=False)
        _, html = self.get("/labels/print?ob=14")
        self.assertIn("計算", html)
        self.assertIn("混ざ", html, "何が起きるのかが書かれていない")

    def test_clear_does_not_block_printing(self):
        """クリア直後は古い値が無いので、止める理由も無い。"""
        self._apply(self._cur())
        self.post("/api/clear", {"current": self._cur()})
        self._apply(self._cur("Z111111", "33.0", "20"), calc=False)
        _, html = self.get("/labels/print?ob=14")
        self.assertNotIn("印刷する（停止中）", html)


class TestEveryPrintTabHasThePrintButton(HttpTestCase):
    """印刷用のタブには、どれも上の帯に「印刷する」(1.5.11。現場の指摘:
    すべての印刷プレビュー画面に「印刷する」があるか)。帯は紙に出ない(.appbar)。"""

    BUTTON = '<button type="button" class="btn btn-primary" data-print-now>印刷する</button>'

    def setUp(self):
        cur = {"selectedCb": 5, "tip": "TIP1000", "kensaNo": "w111111",
               "weight1": "10", "weight2": "12", "coilH": {}}
        self.post("/api/clear", {"current": cur})
        self.post("/api/select-checkbox", {"cbIdx": 5, "current": cur})
        self.post("/api/apply-weight", {"current": cur})
        cur["coilH"] = {"1": "11", "2": "11", "3": "10", "4": "10"}
        self.post("/api/calc-tare", {"current": cur})
        self.post("/api/calc-list", {"current": cur})
        self.cur = cur

    def test_every_print_tab(self):
        for path in ("/tare/print", "/list/print", "/labels/print?ob=5,6",
                     "/labels/sheet?ob=5,6", "/labels/calibration",
                     "/all-size/print?combo=x"):
            with self.subTest(path=path):
                status, html = self.get(path)
                self.assertEqual(status, 200)
                self.assertEqual(html.count(self.BUTTON), 1, "帯の「印刷する」")
                self.assertIn('<body class="print-only"', html)
                self.assertNotIn(">印刷</button>", html, "名前は「印刷する」にそろえる")

    def test_the_bar_cannot_print_stale_labels(self):
        """入力が変わったまま(計算していない)のラベルは、帯からも刷れない。"""
        changed = dict(self.cur, kensaNo="w222222", weight1="20", weight2="22")
        self.post("/api/apply-weight", {"current": changed})
        _, html = self.get("/labels/print?ob=5,6")
        self.assertNotIn("data-print-now", html)
        self.assertEqual(html.count("印刷する（停止中）"), 2, "帯と案内の2つとも止める")
        # 計算し直せば戻る
        self.post("/api/calc-tare", {"current": dict(changed, coilH=self.cur["coilH"])})
        _, html = self.get("/labels/print?ob=5,6")
        self.assertEqual(html.count(self.BUTTON), 1)


class TestStaleMasterIsVisible(HttpTestCase):
    """取得元へ到達できず直前の内容で動いているとき、画面で分かること。

    診断画面だけに出していると、ライン作業中は誰も気付かない（解析書 D-8）。
    """

    def tearDown(self):
        self.ctx.wf.materials.serving_stale = False

    def test_header_is_normal_while_the_master_is_fresh(self):
        _, html = self.get("/")
        self.assertNotIn("（古い内容）", html)
        self.assertIn("資材マスタの取得元", html)

    def test_header_says_so_on_every_screen(self):
        self.ctx.wf.materials.serving_stale = True
        for path in ("/", "/all-size", "/tare", "/list", "/labels"):
            _, html = self.get(path)
            self.assertIn("（古い内容）", html, path)
            self.assertIn("直前に読んだ内容", html, path)

    def test_diag_still_reports_it(self):
        self.ctx.wf.materials.serving_stale = True
        _, html = self.get("/diag")
        self.assertIn("直前に読んだ内容", html)
