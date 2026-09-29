"""ペナラベルを統合アプリに載せた形で、移植元の HTTP の約束を守ること。

移植元は標準ライブラリの `http.server`(`Handler.do_GET` / `do_POST`)で動いていた。
統合版で Flask へ載せ替えたとき、次の約束が抜けていた(移植漏れの点検で見つかった):

- 画面の経路への POST は、画面ではなく JSON の 404(「不明なエンドポイントです」)
- 本文の上限 1MB は、長さを名乗らない送り方(chunked)でも守る
- 静的ファイルが無いときは、案内つきの HTML の 404
- 要求を1行ずつログに残す(心拍・接続確認は DEBUG)。ふだんのログは INFO まで
  (`--diagnostic` のときだけ DEBUG)
- 止めるときに状態DB を閉じる
"""
from __future__ import annotations

import io
import json
import logging
import types
import unittest
from unittest import mock

from .test_app import TOKEN, _make_app

H = {"X-Tool-Token": TOKEN}


class PenaHttpTest(unittest.TestCase):
    def setUp(self):
        self.client = _make_app(self).test_client()

    def test_post_to_a_page_is_a_json_404(self):
        for path in ("/pena/tare", "/pena/", "/pena/labels/print"):
            res = self.client.post(path, json={}, headers=H)
            self.assertEqual(res.status_code, 404, path)
            self.assertEqual(res.get_json(), {"ok": False, "message": "不明なエンドポイントです"}, path)

    def test_post_to_a_page_still_checks_the_body(self):
        res = self.client.post("/pena/tare", data=b"{not json", headers=H,
                               content_type="application/json")
        self.assertEqual(res.status_code, 400)

    def test_missing_static_file_is_an_html_page(self):
        res = self.client.get("/pena/static/nope.css")
        self.assertEqual(res.status_code, 404)
        self.assertIn("text/html", res.content_type)
        self.assertIn("ファイルが見つかりません", res.get_data(as_text=True))
        self.assertIn("href='/pena/'", res.get_data(as_text=True))

    def test_pages_still_render(self):
        self.assertEqual(self.client.get("/pena/tare").status_code, 200)

    def _chunked(self, size: int):
        return self.client.post(
            "/pena/api/state", input_stream=io.BytesIO(json.dumps({"x": "a" * size}).encode()),
            headers={**H, "Content-Type": "application/json", "Transfer-Encoding": "chunked"},
            environ_overrides={"wsgi.input_terminated": True})

    def test_chunked_body_over_the_limit_is_refused(self):
        from modules.packing_pena_label import server
        res = self._chunked(server.MAX_BODY + 10)
        self.assertEqual(res.status_code, 413)
        self.assertEqual(res.get_json()["message"], "リクエストが大きすぎます")

    def test_chunked_body_under_the_limit_is_read(self):
        res = self._chunked(100)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])

    def test_declared_length_over_the_limit_is_refused(self):
        from modules.packing_pena_label import server
        body = json.dumps({"x": "a" * (server.MAX_BODY + 10)}).encode()
        res = self.client.post("/pena/api/state", data=body, headers=H,
                               content_type="application/json")
        self.assertEqual(res.status_code, 413)

    def test_each_request_is_logged(self):
        with self.assertLogs("modules.packing_pena_label.server", level="DEBUG") as cm:
            self.client.get("/pena/tare?pane=1")
            self.client.get("/pena/api/health")
        lines = cm.output
        self.assertTrue(any(l.startswith("INFO:") and '"GET /pena/tare?pane=1" 200' in l
                            for l in lines), lines)
        # 心拍・接続確認は数秒ごとに来るので DEBUG
        self.assertTrue(any(l.startswith("DEBUG:") and "/pena/api/health" in l for l in lines),
                        lines)

    def test_the_token_is_not_logged(self):
        with self.assertLogs("modules.packing_pena_label.server", level="INFO") as cm:
            self.client.get(f"/pena/?t={TOKEN}&pane=1")
        text = "\n".join(cm.output)
        self.assertNotIn(TOKEN, text)
        self.assertIn('"GET /pena/?t=***&pane=1" 200', text)


class PenaFieldFeedbackTest(unittest.TestCase):
    """現場の指摘で直したところ(ペナラベル 1.5.4)を、統合アプリに載せた形で。"""

    def setUp(self):
        self.client = _make_app(self).test_client()

    def test_every_path_row_has_its_own_save_button(self):
        import re
        from modules.packing_pena_label.app.config import load_config
        from modules.packing_pena_label.app.services import settings as S
        html = self.client.get("/pena/settings").get_data(as_text=True)
        paths = [it["key"] for it in S.describe(load_config()) if it["kind"] in ("dir", "file")]
        self.assertTrue(paths)
        for key in paths:
            self.assertRegex(html, r'data-save-one="%s"' % re.escape(key), key)

    def test_progress_is_readable_during_and_after_calc(self):
        res = self.client.get("/pena/api/progress", headers=H)
        self.assertEqual(res.status_code, 200)
        self.assertIn("active", res.get_json())
        self.assertEqual(self.client.get("/pena/api/progress").status_code, 403, "トークンが要る")


class PrintTabTest(unittest.TestCase):
    """印刷用のタブは印刷専用(ペナラベル 1.5.5。現場の指摘)。

    上部のメニュー(指定サイズ・風袋計算…)から業務の画面へ進めると、ツールが2つ
    開いたのと同じになり、タブが増える一方だった。メニューを出さず、「印刷」
    「このタブを閉じる」だけの帯にする。受付(この画面で使う)もしない。
    """

    PRINT_PAGES = ("/pena/labels/print?ob=5", "/pena/tare/print", "/pena/list/print",
                   "/pena/labels/sheet?ob=5", "/pena/labels/calibration",
                   "/pena/all-size/print?combo=x", "/pena/all-size/print")

    def setUp(self):
        self.client = _make_app(self).test_client()

    def test_print_pages_are_print_only(self):
        for path in self.PRINT_PAGES:
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertIn('<body class="print-only"', html)
                self.assertIn('class="printbar"', html)
                self.assertIn("data-close-tab", html)
                self.assertIn('data-guard=""', html, "印刷用のタブは受付しない")

    def test_business_pages_keep_the_menu(self):
        for path in ("/pena/", "/pena/tare", "/pena/all-size", "/pena/settings"):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertIn('<body class=""', html)
                self.assertNotIn('class="printbar"', html)

    def test_no_links_to_business_pages_in_print_tabs(self):
        import re
        for path in ("/pena/labels/calibration", "/pena/labels/print?ob=5"):
            html = self.client.get(path).get_data(as_text=True)
            main = html[html.index("<main"):html.index("</main>")]
            hrefs = re.findall(r'href="([^"]+)"', main)
            for href in hrefs:
                self.assertTrue(href.startswith("/pena/labels/"), (path, href))


class OtherTabsTest(unittest.TestCase):
    """本ツール以外のタブ(別のサイト・同じ PC の別のアプリ)からの送信は断る(1.0.7)。

    以前は統合画面と資材計算の心拍の受け口が、別のページからの送信も受けていた。
    """

    def setUp(self):
        self.client = _make_app(self).test_client()

    def test_writes_from_other_pages_are_refused(self):
        for site in ("cross-site", "same-site"):
            for path, body in (("/api/alive", {"client": "evil"}),
                               ("/material/api/alive", {"closing": True}),
                               ("/details/api/alive", {"leaving": True}),
                               ("/pena/api/screen/release", {"screenId": "x"})):
                with self.subTest(site=site, path=path):
                    res = self.client.post(path, json=body, headers={"Sec-Fetch-Site": site})
                    self.assertEqual(res.status_code, 403)

    def test_own_pages_still_pass(self):
        for site in ("same-origin", "none", None):
            headers = {"Sec-Fetch-Site": site} if site else {}
            with self.subTest(site=site):
                res = self.client.post("/api/alive", json={"client": "shell-1"}, headers=headers)
                self.assertEqual(res.status_code, 200)

    def test_reading_is_not_affected(self):
        res = self.client.get("/api/health", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(res.status_code, 200)


class PenaLogLevelTest(unittest.TestCase):
    def tearDown(self):
        from common import logging_utils
        logging_utils.set_diagnostic(False)

    def test_pena_keeps_info_unless_diagnostic(self):
        from common import logging_utils
        logging_utils.set_diagnostic(False)
        pena = logging.getLogger("modules.packing_pena_label")
        self.assertEqual(pena.level, logging.INFO)
        self.assertEqual(logging.getLogger("meisai").level, logging.DEBUG)
        self.assertEqual(logging.getLogger("coil_tool").level, logging.DEBUG)
        logging_utils.set_diagnostic(True)
        self.assertEqual(pena.level, logging.DEBUG)

    def test_start_app_takes_the_diagnostic_flag(self):
        import sys
        import start_app
        from common import logging_utils
        self.addCleanup(setattr, sys, "dont_write_bytecode", sys.dont_write_bytecode)
        with mock.patch.object(start_app, "run_environment_checks",
                               side_effect=start_app.StartupError("試験")), \
             mock.patch.object(start_app, "report_failure"):
            start_app.main(["--diagnostic", "--no-browser"])
        self.assertEqual(logging.getLogger("modules.packing_pena_label").level, logging.DEBUG)
        logging_utils.set_diagnostic(False)


class CloseOnStopTest(unittest.TestCase):
    def test_pena_closes_its_state_db(self):
        from modules import packing_pena_label as pena
        from modules.packing_pena_label import server
        store = mock.Mock()
        fake = types.SimpleNamespace(store=store)
        fake.close = lambda: server.AppContext.close(fake)
        with mock.patch.object(server, "_context", fake):
            pena.close()
            pena.close()                            # 何度呼んでもよい
        self.assertEqual(store.close.call_count, 2)

    def test_start_app_closes_every_feature_that_can(self):
        import start_app
        closed = []
        mods = (types.SimpleNamespace(LABEL="梱包明細"),
                types.SimpleNamespace(LABEL="ペナラベル", close=lambda: closed.append("pena")),
                types.SimpleNamespace(LABEL="資材計算",
                                      close=mock.Mock(side_effect=OSError("こわれた"))))
        fake_app = types.SimpleNamespace(all_modules=lambda: mods)
        with mock.patch.dict("sys.modules", {"app": fake_app}):
            with self.assertLogs("coil_packing_tools", level="WARNING") as cm:
                start_app._close_modules()
        self.assertEqual(closed, ["pena"])
        self.assertIn("資材計算 の片付けに失敗しました", "\n".join(cm.output))

    def test_a_stuck_close_does_not_keep_the_process(self):
        """固まった要求が DB の鍵を持っていても、片付けで止まらない(確実に落とす)。"""
        import threading
        import time
        import start_app
        stuck = threading.Event()
        mods = (types.SimpleNamespace(LABEL="ペナラベル", close=lambda: stuck.wait(30)),)
        fake_app = types.SimpleNamespace(all_modules=lambda: mods)
        with mock.patch.dict("sys.modules", {"app": fake_app}), \
             mock.patch.object(start_app, "CLOSE_WAIT_SEC", 0.3):
            began = time.monotonic()
            with self.assertLogs("coil_packing_tools", level="WARNING") as cm:
                start_app._close_modules()
            self.assertLess(time.monotonic() - began, 3)
        stuck.set()
        self.assertIn("片付けが", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
