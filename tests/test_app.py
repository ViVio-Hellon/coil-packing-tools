"""統合アプリ ── 3機能が1つの Flask に載り、入口ごとに独立して動くこと

**守ること**
- 統合画面(`/`)に3つのタブと iframe が、業務の順(明細 → ラベル → 資材)で出る
- 各機能の画面・API・静的ファイルは、その機能の入口(`/details` `/pena` `/material`)の下に
  移植元の経路のまま並ぶ。画面のリンクと JS の経路も入口が付く
- 起動トークンはプロセスに1つ。3機能とも同じ値を照合し、断り方は各機能のまま
  (梱包明細 401 / 資材計算 403 / ペナラベル 403)
- Host 検証・同一オリジンの確認は3機能すべてに効く
- 資材計算のテンプレート(`material_base.html`)と統合画面の `base.html` が混ざらない
- 心拍(`/api/alive`)は統合画面の外枠からも届き、裏に回ったと言えば終わらない
- 停止(`/api/shutdown`)はトークン必須。取り込み中は 409
"""
from __future__ import annotations

import threading
import time
import unittest

from common import idle_exit

TOKEN = "test-token-abc123"


def _make_app(case: unittest.TestCase, **kw):
    import app as app_module
    from modules.packing_details.meisai import db as details_db
    from modules.packing_details.meisai import screen as details_screen
    from modules.packing_material_calculation.app import screen as material_screen
    from modules.packing_material_calculation.coil_tool import db as material_db

    with details_db.connect() as conn:
        details_db.apply_schema(conn)
    with material_db.connect() as conn:
        material_db.apply_schema(conn)
    details_screen.reset()
    material_screen.reset()
    case.addCleanup(details_screen.reset)
    case.addCleanup(material_screen.reset)
    flask_app = app_module.create_app(token=TOKEN, **kw)
    flask_app.config["READY"] = True
    return flask_app


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.app = _make_app(self)
        self.client = self.app.test_client()

    def test_準備中は待機画面_終わったら統合画面(self):
        self.app.config["READY"] = False
        html = self.client.get("/").data.decode()
        self.assertIn("起動中", html)
        self.assertIn("nlm.coil-packing-tools", html)
        self.app.config["READY"] = True
        html = self.client.get("/").data.decode()
        self.assertIn('class="tabs"', html)
        self.assertIn("コイル梱包ツール", html)

    def test_タブと_iframe_は業務の順で3つ(self):
        html = self.client.get("/").data.decode()
        for key in ("details", "pena", "material"):
            self.assertIn(f'id="tab-{key}"', html)
            self.assertIn(f'id="pane-{key}"', html)
        srcs = [f'src="/details/meisai?t={TOKEN}"', f'src="/pena/?t={TOKEN}"',
                f'src="/material/calc?t={TOKEN}"']
        positions = [html.index(s) for s in srcs]
        self.assertEqual(positions, sorted(positions), "並びは 明細 → ラベル → 資材")
        # 最初のタブだけ見せる(あとは hidden)。iframe は3つとも置く(外さない)
        self.assertEqual(html.count("<iframe"), 3)
        self.assertEqual(html.count('role="tabpanel"'), 3)

    def test_統合画面はタブの版も出す(self):
        html = self.client.get("/").data.decode()
        for version in ("0.13.1", "1.5.0", "0.2.0"):
            self.assertIn(f"VER{version}", html)

    def test_健康確認は統合アプリの身元と3機能の身元(self):
        body = self.client.get("/api/health").get_json()
        self.assertEqual(body["app_id"], "nlm.coil-packing-tools")
        self.assertTrue(body["ready"])
        self.assertEqual([m["key"] for m in body["modules"]], ["details", "pena", "material"])
        self.assertEqual(body["modules"][0]["display_name"], "コイル梱包明細打ち出しシステム")
        self.assertIsNone(body["job"])

    def test_機能の健康確認はその機能の身元を名乗る(self):
        self.assertEqual(self.client.get("/details/api/health").get_json()["app_id"],
                         "nlm.packing-details")
        self.assertEqual(self.client.get("/material/api/health").get_json()["app_id"],
                         "nlm.coil-material-tool")
        self.assertEqual(self.client.get("/pena/api/health").get_json()["appId"],
                         "PackingPenaLabel")

    def test_知らない経路はJSONの404(self):
        # ペナラベルの `/api/*` はトークンの照合が先(知らない経路でも 403 で断る)
        for path in ("/nope", "/details/api/nope", "/material/nope", "/pena/api/nope"):
            with self.subTest(path=path):
                res = self.client.get(path, headers={"X-Tool-Token": TOKEN})
                self.assertEqual(res.status_code, 404)
                self.assertTrue(res.is_json, path)

    def test_Host検証は全機能に効く(self):
        for path in ("/", "/api/health", "/details/api/health", "/pena/api/health",
                     "/material/api/health"):
            with self.subTest(path=path):
                res = self.client.get(path, headers={"Host": "example.com"})
                self.assertEqual(res.status_code, 400)

    def test_一部の機能だけ載せられる(self):
        from modules import packing_details
        flask_app = _make_app(self, modules=(packing_details,))
        c = flask_app.test_client()
        self.assertEqual(c.get("/details/api/health").status_code, 200)
        self.assertEqual(c.get("/pena/api/health").status_code, 404)
        self.assertEqual([m["key"] for m in c.get("/api/health").get_json()["modules"]],
                         ["details"])


class PrefixTest(unittest.TestCase):
    """機能ごとの入口の下に、移植元の経路がそのまま並ぶ。"""

    def setUp(self):
        self.client = _make_app(self).test_client()

    def test_各機能の入口は自分の画面へ送る(self):
        res = self.client.get("/details/")
        self.assertEqual(res.status_code, 302)
        self.assertTrue(res.headers["Location"].endswith("/details/meisai"))
        res = self.client.get("/material/")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/material/calc?t=", res.headers["Location"])

    def test_梱包明細の画面は入口を知っている(self):
        html = self.client.get(f"/details/meisai?t={TOKEN}").data.decode()
        self.assertIn('base: "/details"', html)
        self.assertIn("/details/static/css/meisai.css", html)
        self.assertIn('href="/details/docs"', html)
        self.assertIn("コイル梱包明細打ち出しシステム", html)
        self.assertNotIn('href="/docs"', html)

    def test_資材計算の画面は入口を知っている(self):
        html = self.client.get(f"/material/calc?t={TOKEN}").data.decode()
        self.assertIn('window.APP_BASE = "/material"', html)
        self.assertIn("/material/static/css/tokens.css", html)
        self.assertIn(f'href="/material/checklist?t={TOKEN}"', html)
        self.assertIn(f'href="/material/settings?t={TOKEN}#about"', html)
        # 資材計算の外枠(帯・レール)であって、統合画面の外枠ではない
        self.assertIn('class="ribbon"', html)
        self.assertNotIn('class="topbar"', html)
        self.assertNotIn("<iframe", html)

    def test_ペナラベルの画面は入口とトークンを知っている(self):
        html = self.client.get("/pena/").data.decode()
        self.assertIn('data-base="/pena"', html)
        self.assertIn(f'data-token="{TOKEN}"', html)
        self.assertIn('href="/pena/static/css/app.css"', html)
        self.assertIn('href="/pena/tare"', html)
        self.assertIn('src="/pena/static/js/app.js"', html)
        self.assertNotIn('href="/tare"', html)

    def test_ペナラベルの面だけ返す(self):
        html = self.client.get("/pena/tare?pane=1").data.decode()
        self.assertNotIn("<html", html)
        self.assertIn("風袋計算", html)
        # 面の中のリンクにも入口が付く
        self.assertNotIn('href="/tare/print"', html)

    def test_静的ファイルは機能の版が付いて長く控えられる(self):
        for path in ("/details/static/css/meisai.css", "/pena/static/js/app.js",
                     "/material/static/js/core.js", "/static/js/shell.js"):
            with self.subTest(path=path):
                res = self.client.get(path + "?v=1")
                self.assertEqual(res.status_code, 200)
                self.assertIn("immutable", res.headers["Cache-Control"])
                res = self.client.get(path)
                self.assertEqual(res.headers["Cache-Control"], "no-cache")
        # 画面と API は控えない
        self.assertEqual(self.client.get("/api/health").headers["Cache-Control"], "no-store")

    def test_版の印は機能ごと(self):
        from flask import url_for
        with _make_app(self).test_request_context("/"):
            self.assertIn("v=0.13.1", url_for("details.static", filename="css/meisai.css"))
            self.assertIn("v=0.2.0", url_for("material.static", filename="js/core.js"))
            self.assertIn("v=1.5.0", url_for("pena.static", filename="js/app.js"))
            self.assertIn("v=1.0.0", url_for("static", filename="js/shell.js"))


class TokenTest(unittest.TestCase):
    """起動トークンはプロセスに1つ。断り方は各機能のまま。"""

    def setUp(self):
        self.client = _make_app(self).test_client()

    def test_梱包明細は401(self):
        res = self.client.post("/details/api/lot", json={"lot_no": "L5160Z0"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"]["code"], "bad_token")

    def test_資材計算は403(self):
        res = self.client.get("/material/api/calc")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json()["reason"], "bad_token")

    def test_ペナラベルも403_移植元には無かった守り(self):
        res = self.client.post("/pena/api/state", json={})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json()["reason"], "bad_token")
        res = self.client.post("/pena/api/state", json={}, headers={"X-Tool-Token": TOKEN})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["ok"])

    def test_ヘッダの名前は2つとも受ける(self):
        for header in ("X-Tool-Token", "X-App-Token"):
            with self.subTest(header=header):
                self.assertEqual(self.client.get("/material/api/calc",
                                                 headers={header: TOKEN}).status_code, 200)
                res = self.client.post("/details/api/lot", json={"lot_no": "L5160Z0"},
                                       headers={header: TOKEN, "X-Tool-Screen": "s1"})
                # 画面を持っていないので 409(トークンは通っている)
                self.assertEqual(res.status_code, 409)

    def test_URLのtでも通る(self):
        self.assertEqual(self.client.get(f"/material/api/calc?t={TOKEN}").status_code, 200)

    def test_心拍と健康確認と画面の受付はトークン不要(self):
        for path, method in (("/api/alive", "POST"), ("/details/api/alive", "POST"),
                             ("/material/api/alive", "POST"), ("/pena/api/screen/ping", "POST"),
                             ("/api/health", "GET"), ("/details/api/health", "GET"),
                             ("/pena/api/health", "GET"), ("/material/api/health", "GET")):
            with self.subTest(path=path):
                res = self.client.open(path, method=method, json={})
                self.assertEqual(res.status_code, 200)

    def test_別オリジンからは全機能が断る(self):
        for path in ("/details/api/lot", "/material/api/calc/lot", "/pena/api/state"):
            with self.subTest(path=path):
                res = self.client.post(path, json={}, headers={
                    "X-Tool-Token": TOKEN, "Sec-Fetch-Site": "cross-site"})
                self.assertEqual(res.status_code, 403)


class AliveAndShutdownTest(unittest.TestCase):
    def setUp(self):
        self.app = _make_app(self)
        self.client = self.app.test_client()
        idle_exit.reset()
        self.addCleanup(idle_exit.reset)
        # 見張りは立てるが、スレッドは動かさない(判断だけを確かめる)
        self.watch = idle_exit.IdleWatch(lambda: None, lambda: False,
                                         idle_sec=90, grace_sec=8, tick_sec=2)
        idle_exit._watch = self.watch

    def test_見張りが無ければそう言う(self):
        idle_exit.reset()
        body = self.client.post("/api/alive", json={}).get_json()
        self.assertFalse(body["watching"])

    def test_外枠の心拍が見張りへ届く(self):
        body = self.client.post("/api/alive", json={"state": "visible", "reason": "open"}).get_json()
        self.assertTrue(body["watching"])
        self.assertIsNotNone(self.watch._seen)
        self.assertFalse(self.watch.hidden)

    def test_裏に回ったと言えば心拍が止まっても終わらない(self):
        self.client.post("/api/alive", json={"state": "visible"})
        self.client.post("/api/alive", json={"state": "hidden", "reason": "hidden"})
        self.assertTrue(self.watch.hidden)
        self.watch._seen = time.monotonic() - 3600
        self.assertIsNone(self.watch.overdue())
        self.client.post("/api/alive", json={"state": "visible", "reason": "foreground"})
        self.assertFalse(self.watch.hidden)

    def test_閉じた合図は猶予のあとで終わる_裏に回った合図は取り消さない(self):
        self.client.post("/api/alive", json={"state": "visible"})
        self.client.post("/api/alive", json={"leaving": True, "reason": "close"})
        self.assertIsNotNone(self.watch._leaving_at)
        # タブを閉じると「裏に回った」と「閉じた」が両方届く。順は決まっていない
        self.client.post("/api/alive", json={"state": "hidden", "reason": "hidden"})
        self.assertIsNotNone(self.watch._leaving_at, "裏に回った合図で閉じた合図を取り消した")
        self.watch._leaving_at = time.monotonic() - 9
        self.assertEqual(self.watch.overdue(), "画面が閉じられました")

    def test_3機能の心拍は同じ見張りへ届く(self):
        from modules.packing_details.meisai import idle_exit as details_idle
        from modules.packing_material_calculation.coil_tool import idle_exit as material_idle
        self.assertIs(details_idle, idle_exit)
        self.assertIs(material_idle, idle_exit)
        self.client.post("/details/api/alive", json={"state": "visible", "reason": "open"})
        self.client.post("/material/api/alive", json={"hidden": False})
        self.assertIsNotNone(self.watch._seen)

    def test_停止はトークン必須(self):
        self.assertEqual(self.client.post("/api/shutdown", json={}).status_code, 401)

    def test_止め方が無ければ501(self):
        import app as app_module
        app_module.set_shutdown_hook(None)
        res = self.client.post("/api/shutdown", json={}, headers={"X-Tool-Token": TOKEN})
        self.assertEqual(res.status_code, 501)

    def test_止め方があれば少し待ってから呼ぶ(self):
        import app as app_module
        called = threading.Event()
        app_module.set_shutdown_hook(called.set)
        self.addCleanup(app_module.set_shutdown_hook, None)
        res = self.client.post("/api/shutdown", json={}, headers={"X-Tool-Token": TOKEN})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["stopped"])
        self.assertTrue(called.wait(3.0))

    def test_取り込み中は止めない_forceなら止める(self):
        import app as app_module
        from modules import packing_material_calculation as material
        called = threading.Event()
        app_module.set_shutdown_hook(called.set)
        self.addCleanup(app_module.set_shutdown_hook, None)
        original = (material.busy, material.busy_labels)
        material.busy = lambda: True
        material.busy_labels = lambda: ["起動時の自動取り込み"]
        self.addCleanup(setattr, material, "busy", original[0])
        self.addCleanup(setattr, material, "busy_labels", original[1])
        res = self.client.post("/api/shutdown", json={}, headers={"X-Tool-Token": TOKEN})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["running"], ["起動時の自動取り込み"])
        res = self.client.post("/api/shutdown", json={"force": True},
                               headers={"X-Tool-Token": TOKEN})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(called.wait(3.0))


class ScreenIsolationTest(unittest.TestCase):
    """画面の持ち主の見張りは機能ごとに別。片方が2枚目でも、もう片方は使える。"""

    def setUp(self):
        self.client = _make_app(self).test_client()

    def test_梱包明細と資材計算の画面は別々に持てる(self):
        res = self.client.post("/details/api/screen/claim", json={"screen": "A"},
                               headers={"X-Tool-Token": TOKEN})
        self.assertEqual(res.status_code, 200)
        res = self.client.post("/details/api/screen/claim", json={"screen": "B"},
                               headers={"X-Tool-Token": TOKEN})
        self.assertEqual(res.status_code, 409)
        # 資材計算はまだ誰も持っていないので、別のタブでも通る
        res = self.client.post("/material/api/screen/claim", json={"screen": "B"})
        self.assertEqual(res.status_code, 200)
        # ペナラベルも
        res = self.client.post("/pena/api/screen/claim", json={"screenId": "B"})
        self.assertTrue(res.get_json()["granted"])
