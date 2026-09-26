# -*- coding: utf-8 -*-
"""入力画面を 1 つに限ることの検証（画面の多重起動）。

プロセスの二重起動（test_launch_guard.py）とは別の問題。
プロセスが 1 つでも、ブラウザーのタブを 2 枚開けば起きる。
"""

import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from modules.packing_pena_label import server
from modules.packing_pena_label.app.config import Config                        # noqa: E402
from modules.packing_pena_label.app.services.screen_guard import ScreenGuard    # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ScreenGuardUnitTest(unittest.TestCase):
    def setUp(self):
        self.g = ScreenGuard(ttl_sec=0.5)

    def test_first_screen_gets_it(self):
        self.assertTrue(self.g.claim("A")["granted"])

    def test_second_screen_is_refused(self):
        self.g.claim("A")
        r = self.g.claim("B")
        self.assertFalse(r["granted"])
        self.assertIn("別の画面", r["reason"])

    def test_same_screen_may_reclaim(self):
        """再読込しても同じタブなら使い続けられること。"""
        self.g.claim("A")
        self.assertTrue(self.g.claim("A")["granted"])

    def test_force_takes_it_over(self):
        self.g.claim("A")
        self.assertTrue(self.g.claim("B", force=True)["granted"])
        self.assertFalse(self.g.heartbeat("A"), "前の画面が生き残っている")
        self.assertTrue(self.g.heartbeat("B"))

    def test_release_frees_it(self):
        self.g.claim("A")
        self.g.release("A")
        self.assertTrue(self.g.claim("B")["granted"])

    def test_release_by_someone_else_does_nothing(self):
        self.g.claim("A")
        self.g.release("B")
        self.assertFalse(self.g.claim("B")["granted"])

    def test_expires_when_the_screen_stops_answering(self):
        """タブが固まった・ブラウザーが落ちた場合に次へ譲ること。"""
        self.g.claim("A")
        self.assertFalse(self.g.claim("B")["granted"])
        time.sleep(0.6)
        self.assertTrue(self.g.claim("B")["granted"], "期限切れにならない")

    def test_heartbeat_keeps_it(self):
        self.g.claim("A")
        for _ in range(4):
            time.sleep(0.2)
            self.assertTrue(self.g.heartbeat("A"))
        self.assertFalse(self.g.claim("B")["granted"], "生きているのに奪われた")

    def test_empty_id_is_refused(self):
        self.assertFalse(self.g.claim("")["granted"])

    def test_is_active_is_open_when_nobody_holds_it(self):
        self.assertTrue(self.g.is_active("誰でも"))
        self.g.claim("A")
        self.assertTrue(self.g.is_active("A"))
        self.assertFalse(self.g.is_active("B"))


class FakeClock:
    """進むだけの時計の代わり。何時間でも一瞬で進められる。"""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += sec


class BackgroundTabTest(unittest.TestCase):
    """裏に回ったタブの心拍が止まっても、作業中の画面を失わないこと。

    別ツールで起きた事故: ブラウザーが裏のタブのタイマーを間引き
    （Chrome / Edge は 5 分ほどで 1 分に 1 回まで。眠ったタブ・スリープでは停止）、
    心拍が途切れてセッションが誤って終了した。
    このツールでは「入力画面の使用権」が心拍で決まるため、同じことが起きうる。
    """

    def setUp(self):
        self.clock = FakeClock()
        self.g = ScreenGuard(ttl_sec=20, background_ttl_sec=12 * 3600,
                             clock=self.clock)
        self.assertTrue(self.g.claim("A", visible=True)["granted"])

    def test_without_the_signal_a_silent_tab_is_freed(self):
        """（これまでどおり）表のまま応答が消えた画面は 20 秒で譲る。
        ブラウザーが落ちたときに次が使えるようにするため。"""
        self.clock.advance(21)
        self.assertTrue(self.g.claim("B")["granted"])

    def test_background_tab_keeps_the_screen_through_throttling(self):
        """裏に回ると知らせた画面は、心拍が 2 時間止まっても空きにしない。"""
        self.g.set_visibility("A", False, "hidden")
        self.clock.advance(2 * 3600)
        r = self.g.claim("B")
        self.assertFalse(r["granted"], "裏に回しただけで使用権を奪われた")
        self.assertTrue(r["holderHidden"])
        self.assertIn("hiddenSince", r)
        self.assertTrue(self.g.has_holder())
        self.assertFalse(self.g.is_active("B"), "裏の間に別の画面が書き換えられる")

    def test_returning_tab_keeps_working(self):
        self.g.set_visibility("A", False, "hidden")
        self.clock.advance(3 * 3600)
        r = self.g.set_visibility("A", True, "visible")
        self.assertTrue(r["granted"])
        self.assertTrue(self.g.heartbeat("A"))
        # 表に戻ったら通常の期限に戻る
        self.clock.advance(21)
        self.assertTrue(self.g.claim("B")["granted"])

    def test_hidden_flag_also_rides_on_the_heartbeat(self):
        """裏に回る知らせが届かなくても、裏での心拍に載せた表裏で分かる。"""
        self.g.heartbeat_info("A", visible=False)
        self.clock.advance(3600)
        self.assertFalse(self.g.claim("B")["granted"])

    def test_background_grace_is_not_forever(self):
        """裏に回ったまま本当に閉じられた場合は、いずれ空きに戻る。"""
        self.g.set_visibility("A", False, "hidden")
        self.clock.advance(12 * 3600 + 1)
        self.assertTrue(self.g.claim("B")["granted"])

    def test_force_still_takes_over_a_background_tab(self):
        """裏に残ったタブが見つからないときは「この画面で使う」で移せる。"""
        self.g.set_visibility("A", False, "hidden")
        self.assertTrue(self.g.claim("B", force=True)["granted"])
        self.assertFalse(self.g.heartbeat("A"))

    def test_only_the_holder_can_say_it_went_away(self):
        self.g.set_visibility("B", False, "hidden")
        self.clock.advance(21)
        self.assertTrue(self.g.claim("B")["granted"], "他人の知らせで延命された")

    def test_sleep_resume_in_foreground_is_regained_by_the_same_tab(self):
        """表のまま PC がスリープし、時計が大きく進んでも、
        戻ってきた同じ画面の心拍で使い続けられる。"""
        self.clock.advance(8 * 3600)
        self.assertTrue(self.g.set_visibility("A", True, "sleep")["granted"])
        self.assertFalse(self.g.claim("B")["granted"])

    def test_release_clears_the_background_state(self):
        self.g.set_visibility("A", False, "hidden")
        self.g.release("A")
        self.assertTrue(self.g.claim("B")["granted"])
        self.clock.advance(21)
        self.assertTrue(self.g.claim("C")["granted"], "裏の状態が次の画面に残った")

    def test_info_says_it_is_in_the_background(self):
        self.g.set_visibility("A", False, "hidden")
        i = self.g.info()
        self.assertTrue(i["hidden"])
        self.assertEqual(i["ttlSec"], 12 * 3600)


class TwoTabsHttpTest(unittest.TestCase):
    """実際にサーバーを立て、2 枚目のタブを想定して叩く。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cfg = Config()
        cfg.port = free_port()
        cfg.prefer_access = False
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

    def setUp(self):
        self.ctx.screens.release(self.ctx.screens.info().get("screenId", ""))

    # --------------------------------------------------------
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
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.read().decode("utf-8")

    def _state(self, screen_id):
        return self.post("/api/state", {"screenId": screen_id})[1]["state"]

    # --------------------------------------------------------
    def test_second_tab_is_refused(self):
        self.assertTrue(self.post("/api/screen/claim", {"screenId": "A"})[1]["granted"])
        r = self.post("/api/screen/claim", {"screenId": "B"})[1]
        self.assertFalse(r["granted"])

    def test_the_refused_tab_cannot_change_anything(self):
        self.post("/api/screen/claim", {"screenId": "A"})
        st = self._state("B")
        status, body = self.post("/api/select-size",
                                 {"obIdx": 5, "screenId": "B", "current": st})
        self.assertEqual(status, 409)
        self.assertTrue(body.get("screenTaken"))
        self.assertIn("別の画面", body["message"])

    def test_the_holder_can_change_things(self):
        self.post("/api/screen/claim", {"screenId": "A"})
        st = self._state("A")
        status, body = self.post("/api/select-size",
                                 {"obIdx": 5, "screenId": "A", "current": st})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_two_tabs_can_no_longer_swap_the_kensa_no(self):
        """これが直したかった事故そのもの。

        直す前は、タブAの画面に A111111 が出ているのに、
        タブBの計算でサーバーの状態が B999999 に置き換わり、
        タブAで印刷すると B999999 が刷られていた。
        """
        self.post("/api/screen/claim", {"screenId": "A"})

        a = self._state("A")
        a["selectedOb"] = 5
        a["kensaNo"] = "A111111"
        a["weight1"] = "20.0"
        a["coilH"] = {"1": "11"}
        a = self.post("/api/apply-weight", {"screenId": "A", "current": a})[1]["state"]
        a = self.post("/api/calc-tare", {"screenId": "A", "current": a})[1]["state"]
        self.assertEqual(a["lblKensaNo"], "A111111")

        # タブBが割り込もうとする
        b = dict(a, kensaNo="B999999", weight1="33.0", coilH={"1": "20"})
        status, _ = self.post("/api/apply-weight", {"screenId": "B", "current": b})
        self.assertEqual(status, 409)
        status, _ = self.post("/api/calc-tare", {"screenId": "B", "current": b})
        self.assertEqual(status, 409)

        # サーバーの作業状態はタブAのまま
        self.assertEqual(self._state("A")["lblKensaNo"], "A111111")

    # -------------------------------------------------------- 裏に回ったタブ
    def _with_fake_clock(self):
        clock = FakeClock()
        clock.t = time.monotonic()
        guard = self.ctx.screens
        old = guard._clock
        guard._clock = clock
        self.addCleanup(setattr, guard, "_clock", old)
        return clock

    def test_background_tab_is_not_robbed_while_its_heartbeat_is_throttled(self):
        """裏のタブの心拍が止まった間に、次に開いたタブへ使用権が移らないこと。

        直す前: 20 秒途切れると空き扱い → 次のタブ（Start.vbs の起動でも開く）が
        使用権を取り、戻った作業中の画面は「使用中ではなくなりました」になった。
        """
        clock = self._with_fake_clock()
        self.post("/api/screen/claim", {"screenId": "A", "visible": True})
        a = self._state("A")
        a.update(selectedOb=5, kensaNo="A222222", weight1="20.0", coilH={"1": "11"})
        self.post("/api/apply-weight", {"screenId": "A", "current": a})

        st, r = self.post("/api/screen/state",
                          {"screenId": "A", "visible": False, "reason": "hidden"})
        self.assertEqual(st, 200)
        self.assertTrue(r["granted"])

        clock.advance(2 * 3600)                      # 心拍なしで 2 時間
        r = self.post("/api/screen/claim", {"screenId": "B", "visible": True})[1]
        self.assertFalse(r["granted"])
        self.assertTrue(r["holderHidden"])
        b = dict(self._state("B"), kensaNo="B888888")
        self.assertEqual(self.post("/api/apply-weight",
                                   {"screenId": "B", "current": b})[0], 409)

        # 作業中の画面が表に戻る → そのまま使える・作業状態もそのまま
        r = self.post("/api/screen/state",
                      {"screenId": "A", "visible": True, "reason": "visible"})[1]
        self.assertTrue(r["granted"])
        self.assertEqual(self._state("A")["lblKensaNo"], "A222222")

    def test_server_keeps_running_when_heartbeats_stop(self):
        """心拍が何時間止まっても、サーバーは止まらず作業状態も残る。"""
        clock = self._with_fake_clock()
        self.post("/api/screen/claim", {"screenId": "A"})
        a = self._state("A")
        a.update(selectedOb=5, kensaNo="A333333", weight1="20.0", coilH={"1": "11"})
        self.post("/api/apply-weight", {"screenId": "A", "current": a})
        clock.advance(13 * 3600)
        st, h = self.post("/api/health", {})
        self.assertEqual(st, 200)
        self.assertTrue(h["ready"])
        self.assertEqual(self._state("A")["lblKensaNo"], "A333333")
        # 戻ってきた同じ画面はそのまま取り直せる
        self.assertTrue(self.post("/api/screen/claim", {"screenId": "A"})[1]["granted"])

    def test_state_signal_needs_visible(self):
        self.post("/api/screen/claim", {"screenId": "A"})
        st, _ = self.post("/api/screen/state", {"screenId": "A"})
        self.assertEqual(st, 400)

    def test_refusal_page_can_say_the_other_screen_is_in_the_background(self):
        _, html = self.get("/")
        self.assertIn('id="sgBg"', html)

    def test_taking_over_kicks_the_previous_tab(self):
        self.post("/api/screen/claim", {"screenId": "A"})
        self.assertTrue(
            self.post("/api/screen/claim",
                      {"screenId": "B", "force": True})[1]["granted"])
        self.assertFalse(self.post("/api/screen/ping", {"screenId": "A"})[1]["granted"])
        self.assertTrue(self.post("/api/screen/ping", {"screenId": "B"})[1]["granted"])

    def test_closing_a_tab_frees_the_screen(self):
        self.post("/api/screen/claim", {"screenId": "A"})
        self.post("/api/screen/release", {"screenId": "A"})
        self.assertTrue(self.post("/api/screen/claim", {"screenId": "B"})[1]["granted"])

    def test_callers_without_a_screen_id_are_not_blocked(self):
        """停止スクリプトや自動テストは画面ではないので素通しすること。"""
        self.post("/api/screen/claim", {"screenId": "A"})
        st = self.post("/api/state", {})[1]["state"]
        status, _ = self.post("/api/select-size", {"obIdx": 5, "current": st})
        self.assertEqual(status, 200)

    # --------------------------------------------------------
    def test_input_screens_carry_the_guard(self):
        for path in ("/", "/all-size", "/settings"):
            _, html = self.get(path)
            self.assertIn('data-guard="1"', html, path)

    def test_view_only_screens_do_not(self):
        """表示・印刷だけの画面は何枚開いてもよい（実際に別タブで開く）。"""
        for path in ("/tare", "/list", "/labels", "/breakdown",
                     "/tare/print", "/list/print"):
            _, html = self.get(path)
            self.assertNotIn('data-guard="1"', html, path)

    def test_the_refusal_explains_what_goes_wrong(self):
        _, html = self.get("/")
        self.assertIn("この画面は開けません", html)
        self.assertIn("刷られる検査番号", html)
        self.assertIn("この画面で使う", html)


class HiddenAttributeMustWorkTest(unittest.TestCase):
    """``el.hidden = true`` が見た目にも効くこと。

    ブラウザー既定の ``[hidden]{display:none}`` は、
    作者スタイルの ``.foo{display:flex}`` に負ける。
    覆い（.screenguard）がまさにそれで、使用権が取れて
    ``hidden`` を立てても**画面に出たまま**になり、
    アプリ全体が操作できなくなっていた。
    """

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "app", "static", "css", "app.css"),
                  encoding="utf-8") as f:
            self.css = f.read()

    def test_hidden_is_forced(self):
        body = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        rule = re.search(r"\[hidden\]\s*\{([^}]*)\}", body)
        self.assertIsNotNone(rule, "[hidden] の規則が無い")
        decl = rule.group(1).replace(" ", "").lower()
        self.assertIn("display:none", decl)
        self.assertIn("!important", decl,
                      "!important が無いと display:flex 等に負ける")

    def test_the_overlay_sets_display(self):
        """この規則が必要な理由（覆いが display を持つ）を明示しておく。"""
        self.assertRegex(self.css, r"\.screenguard\s*\{[^}]*display\s*:\s*flex")


if __name__ == "__main__":
    unittest.main()
