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
        from common import versions
        html = self.client.get("/").data.decode()
        v = versions.all_versions()
        for key in ("details", "pena", "material"):
            self.assertIn(f"VER{v[key]}", html)

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
        from common import versions
        v = versions.all_versions()
        with _make_app(self).test_request_context("/"):
            self.assertIn(f"v={v['details']}-", url_for("details.static", filename="css/meisai.css"))
            self.assertIn(f"v={v['material']}-", url_for("material.static", filename="js/core.js"))
            self.assertIn(f"v={v['pena']}-", url_for("pena.static", filename="js/app.js"))
            self.assertIn(f"v={v['app']}-", url_for("static", filename="js/shell.js"))


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


class BootTest(unittest.TestCase):
    """準備中(起動待機画面が出ているあいだ)の入口。"""

    def setUp(self):
        self.app = _make_app(self)
        self.app.config["READY"] = False
        self.client = self.app.test_client()

    def test_待たずに使い始めるで統合画面に入れる(self):
        """移植元の梱包明細は取り込み中でも業務画面へ入れた。同じ画面に戻らないこと。"""
        import re
        html = self.client.get("/").get_data(as_text=True)
        (href,) = re.findall(r'id="skip" href="([^"]+)"', html)
        self.assertEqual(href, "/?go=1")
        shell = self.client.get(href).get_data(as_text=True)
        self.assertEqual(shell.count("<iframe"), 3, "統合画面が出ない")
        # 3機能の画面そのものは準備中でも開ける
        for path in ("/details/meisai", "/pena/", "/material/calc"):
            self.assertEqual(self.client.get(f"{path}?t={TOKEN}").status_code, 200, path)

    def test_準備中に機能の入口を開くと統合アプリの待機画面へ(self):
        """機能の入口で機能の待機画面を出すと、アプリIDが食い違って止まっていた。"""
        for path in ("/details/", "/details", "/material/", "/material"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 302, path)
            self.assertEqual(res.headers["Location"], "/", path)
        # 入口が業務画面そのもの(ペナラベル)は回さない(統合画面の枠が開くため)
        self.assertEqual(self.client.get(f"/pena/?t={TOKEN}").status_code, 200)

    def test_準備が終わったら機能の入口は元のまま(self):
        self.app.config["READY"] = True
        self.assertIn("/details/meisai", self.client.get("/details/").headers["Location"])
        self.assertIn("/material/calc", self.client.get(f"/material/?t={TOKEN}").headers["Location"])


class ModuleInfoTest(unittest.TestCase):
    """機能の画面に出る「どこで・どのポートで動いているか」は統合アプリのもの。"""

    def setUp(self):
        self.app = _make_app(self)
        self.client = self.app.test_client()

    def test_資材計算のこのアプリについて(self):
        from common import app_config
        body = self.client.get("/material/api/settings", headers={"X-App-Token": TOKEN}).get_json()
        about = (body.get("view") or body)["about"]
        self.assertEqual(about["port"], about["actual_port"], "繰り上がったように見える")
        self.assertEqual(about["app_root"], str(app_config.APP_ROOT))
        self.assertTrue(about["module_root"].endswith("packing_material_calculation"))

    def test_梱包明細のバージョン情報(self):
        from common import app_config
        body = self.client.get("/details/api/health").get_json()
        self.assertEqual(body["app_root"], str(app_config.APP_ROOT))
        self.assertTrue(body["module_root"].endswith("packing_details"))

    def test_ペナラベルの使用ポートは統合アプリが決める(self):
        """画面に出さない・保存しない・配布設定にも入れない。単体で動かす試験では従来どおり。"""
        import re
        from modules.packing_pena_label import server as pena_server
        from modules.packing_pena_label.app.services import distribution as D
        from modules.packing_pena_label.app.services import settings as S
        html = self.client.get("/pena/settings").get_data(as_text=True)
        self.assertIn('data-field="db_busy_timeout_ms"', html)          # 目印の書き方の確認
        self.assertNotIn('data-field="port"', html)
        self.assertNotIn('data-dist-item="port"', html)
        self.assertIn("config/app.json で決まります", html)
        cfg = pena_server.context().cfg
        self.assertTrue(S.integrated(cfg))
        r = S.save({"port": 9000}, cfg)
        self.assertTrue(r["ok"])
        self.assertNotIn("port", S.load_local_overrides(cfg))
        self.assertNotIn("port", [k for g in D.summary(cfg)["groups"] for k in
                                  (i["key"] for i in g["items"])])


class TabAliveInjectionTest(unittest.TestCase):
    """別のタブで開く画面(帳票・印刷ビュー)に心拍のスクリプトが入る。

    3機能とも印刷は別のタブで開く。統合アプリが HTML の末尾に差し込み、
    各機能のコードと紙面の中身は変えない。
    """

    def setUp(self):
        self.app = _make_app(self)
        self.client = self.app.test_client()

    def _get(self, path: str) -> str:
        res = self.client.get(path)
        self.assertEqual(res.status_code, 200, path)
        return res.get_data(as_text=True)

    def _tags(self, html: str) -> list:
        import re
        return re.findall(r'<script src="/static/js/tab_alive\.js[^"]*"[^>]*></script>', html)

    def test_3機能の印刷ページに1つずつ入る(self):
        for path, key in (("/pena/tare/print", "pena"),
                          ("/pena/list/print", "pena"),
                          (f"/material/report/checklist?t={TOKEN}", "material"),
                          (f"/details/meisai?t={TOKEN}", "details")):
            html = self._get(path)
            tags = self._tags(html)
            self.assertEqual(len(tags), 1, path)
            self.assertIn(f'data-key="{key}"', tags[0])
            self.assertIn(f'data-alive-ms="{idle_exit.HEARTBEAT_MS}"', tags[0])
            # 最後の </body> の直前(紙面の中身の後ろ)
            self.assertLess(html.rindex(tags[0]), html.lower().rindex("</body>"), path)

    def test_梱包明細の帳票にも入る_書き足しの保存はそのまま(self):
        """帳票は刷る前に紙面で書き足した値をサーバへ保存する。開いている間は止めない。"""
        from unittest import mock
        from modules.packing_details.app.routes import meisai as details_routes
        from modules.packing_details.meisai.meisai_service import Output
        out = Output(lot_no="L5160Z0", seq_no=1, keys=["1-10", "2-8"], weights=[250, 248])
        with mock.patch.object(details_routes.meisai_service, "find_output", return_value=out):
            html = self._get(f"/details/report/L5160Z0/1?t={TOKEN}")
        tags = self._tags(html)
        self.assertEqual(len(tags), 1)
        self.assertIn('data-key="details"', tags[0])
        self.assertIn("/details/report/L5160Z0/edits", html, "書き足しの送り先が変わった")
        self.assertLess(html.rindex("/report/qa-mark"), html.rindex(tags[0]),
                        "帳票のスクリプトより後ろに入る")

    def test_紙面の中身は変えない(self):
        """差し込むのはスクリプト1つだけ。紙面の中身には触らない。"""
        import app as app_module
        html = self._get("/pena/list/print")
        (tag,) = self._tags(html)
        self.assertEqual(app_module.inject_tab_alive("<p>x</p></body>", tag),
                         "<p>x</p>" + tag + "</body>")
        self.assertEqual(html.lower().count("</body>"), 1)

    def test_統合画面の中へ差し替える断片には入れない(self):
        html = self._get("/pena/tare?pane=1")
        self.assertEqual(self._tags(html), [])

    def test_統合画面そのものとAPIには入れない(self):
        self.assertEqual(self._tags(self._get(f"/?t={TOKEN}")), [])
        body = self.client.get("/pena/api/health").get_data(as_text=True)
        self.assertNotIn("tab_alive", body)

    def test_スクリプトそのものが配られる(self):
        res = self.client.get("/static/js/tab_alive.js")
        self.assertEqual(res.status_code, 200)
        text = res.get_data(as_text=True)
        self.assertIn("window.top !== window", text, "統合画面の中では送らない")
        self.assertIn("leaving: true", text)

    def test_アイコンは根で中身なしに答える(self):
        """ペナラベルの移植元と同じ。印刷ビューを開くたびに 404 を出さない。"""
        res = self.client.get("/favicon.ico")
        self.assertEqual(res.status_code, 204)

    def test_差し込みの決まり(self):
        import app as app_module
        tag = '<script src="/static/js/tab_alive.js"></script>'
        self.assertEqual(app_module.inject_tab_alive("<b>a</b></BODY></html>", tag),
                         "<b>a</b>" + tag + "</BODY></html>")
        self.assertEqual(app_module.inject_tab_alive("<b>a</b>", tag), "<b>a</b>" + tag)
        once = app_module.inject_tab_alive("<b>a</b></body>", tag)
        self.assertEqual(app_module.inject_tab_alive(once, tag), once, "2度入れない")


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

    def test_外枠を閉じても印刷の別タブが開いていれば終わらない(self):
        """3機能とも印刷は別のタブで開く。帳票が開いているあいだは終わらない。"""
        self.client.post("/api/alive", json={"state": "visible", "client": "shell-a"})
        # 別タブの帳票(tab_alive.js)。sendBeacon で届くのでトークンは付かない
        res = self.client.post("/api/alive", json={"state": "visible", "client": "tab-details-a",
                                                   "reason": "open"})
        self.assertTrue(res.get_json()["watching"])
        self.client.post("/api/alive", json={"leaving": True, "client": "shell-a"})
        self.watch._screens["shell-a"].leaving_at = time.monotonic() - 9
        self.assertIsNone(self.watch.overdue(), "開いている帳票ごと終わった")
        # 帳票も閉じたら終わる
        self.client.post("/api/alive", json={"leaving": True, "client": "tab-details-a"})
        self.watch._screens["tab-details-a"].leaving_at = time.monotonic() - 9
        self.assertEqual(self.watch.overdue(), "画面が閉じられました")

    def test_資材計算の画面も裏に回った合図で閉じた合図を取り消さない(self):
        """統合画面を閉じると、中の資材計算から「閉じた」「裏に回った」が順不同で届く。"""
        self.client.post("/material/api/alive", json={"hidden": False})
        self.client.post("/material/api/alive", json={"closing": True, "screen": "m-1"})
        self.client.post("/material/api/alive", json={"hidden": True})
        self.assertIsNotNone(self.watch._leaving_at, "裏に回った合図で閉じた合図を取り消した")
        self.watch._leaving_at = time.monotonic() - 9
        self.assertEqual(self.watch.overdue(), "画面が閉じられました")

    def test_資材計算の画面はふつうの心拍なら閉じた合図を取り消す(self):
        """再読込: 閉じた合図のあとに前の画面の心拍が来たら、終わらない。"""
        self.client.post("/material/api/alive", json={"hidden": False})
        self.client.post("/material/api/alive", json={"closing": True, "screen": "m-1"})
        self.client.post("/material/api/alive", json={"hidden": False})
        self.assertIsNone(self.watch._leaving_at)

    def test_機能の停止口も取り込み中は止めない(self):
        """機能の停止口も、止めるのはプロセスごと(3機能とも)。統合アプリの停止口と同じく断る。"""
        import app as app_module
        from modules import packing_material_calculation as material
        from modules import packing_details as details, packing_pena_label as pena
        called = []
        for m in (material, details, pena):
            m.set_shutdown_hook(lambda: called.append("stop"))
        self.addCleanup(lambda: [m.set_shutdown_hook(None) for m in (material, details, pena)])
        original = (material.busy, material.busy_labels)
        material.busy = lambda: True
        material.busy_labels = lambda: ["起動時の自動取り込み"]
        self.addCleanup(setattr, material, "busy", original[0])
        self.addCleanup(setattr, material, "busy_labels", original[1])
        h = {"X-Tool-Token": TOKEN, "X-App-Token": TOKEN}
        for path in ("/material/api/shutdown", "/pena/api/shutdown", "/details/api/shutdown"):
            res = self.client.post(path, json={}, headers=h)
            self.assertEqual(res.status_code, 409, path)
            self.assertEqual(res.get_json()["running"], ["起動時の自動取り込み"], path)
        # トークンが無ければ機能の側が断る(何が走っているかは返さない)
        res = self.client.post("/material/api/shutdown", json={})
        self.assertNotEqual(res.status_code, 200)
        self.assertNotIn("running", res.get_json() or {})
        time.sleep(0.6)
        self.assertEqual(called, [], "取り込み中なのに止めた")
        # force なら止める(統合アプリの停止口と同じ決まり)
        res = self.client.post("/material/api/shutdown", json={"force": True}, headers=h)
        self.assertEqual(res.status_code, 200)
        # 止める処理は少し遅れて走る。片付け(停止の仕方を外す)より先に済ませる
        time.sleep(0.8)
        self.assertEqual(called, ["stop"])

    def test_機能の停止口は取り込みが無ければ従来どおり(self):
        from modules import packing_material_calculation as material
        called = []
        material.set_shutdown_hook(lambda: called.append("stop"))
        self.addCleanup(material.set_shutdown_hook, None)
        res = self.client.post("/material/api/shutdown", json={}, headers={"X-App-Token": TOKEN})
        self.assertEqual(res.status_code, 200)
        time.sleep(0.8)
        self.assertEqual(called, ["stop"])

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
