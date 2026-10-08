"""業務ツール統合ランチャーとの連携(統合 1.2.5。業務ツール統合ツール all-tools 1.2.0 と同じ作り)

起動ファイル(Start.vbs・start.bat・stop.bat)の中身は 1.2.3 のまま。ランチャー専用の口は置かない。
ランチャーから見た動きを all-tools にそろえる:

    デスクトップ版  窓の × (ランチャーの「窓を閉じる」)= 統合画面の「終了」と同じ流れ。
                    確認が出るのは、保存していない入力・途中の処理があるときだけ
    ブラウザ版      stop.bat → process_manager.py → /api/shutdown。入口は開いている統合画面に
                    「閉じる前の頼み」を出し、保存していない入力を確かめてもらってから止まる
    戻り値          process_manager.py は 0 止めた / 2 止めなかった(途中の処理・「閉じない」・確かめ中)/
                    1 止められなかった(デスクトップ版が動いている など)

守ること:
- 画面が無ければ待たずに止まる(画面を閉じたあと)
- 画面が「閉じてよい」→ 止まる /「閉じない」→ 409 refused / 答えない → 409 asking。
  asking のあとで「閉じてよい」が来たら、入口が自分で止まる
- 統合画面の「終了」は自分で確かめてから頼む(`screens_ready`)。もう一度は訊かない
- 途中の処理があれば、画面が「閉じてよい」と言っても止めない(`force` 以外)
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

import process_manager
from common import close_ask
from tests.test_app import TOKEN, _make_app

H = {"X-Tool-Token": TOKEN}


class CloseAskTest(unittest.TestCase):
    def test_ask_answer_and_verdict(self):
        asks = close_ask.CloseAsk()
        asks.seen("a")
        asks.seen("b")
        self.assertEqual(asks.pending("a"), 0, "頼む前は何も出ていない")
        seq = asks.ask()
        self.assertEqual(asks.pending("a"), seq)
        self.assertTrue(asks.answer("a", seq, "ok"))
        self.assertEqual(asks.verdict(["a", "b"]), "working", "b がまだ")
        self.assertEqual(asks.pending("a"), 0, "答えた画面には、もう出さない")
        asks.answer("b", seq, "ok")
        self.assertEqual(asks.verdict(["a", "b"]), "ok")

    def test_refused_is_asked_again_next_time(self):
        """「閉じない」を選んだ人は、入力を片付けてからもう一度止めにくる。前の答えで断り続けない。"""
        asks = close_ask.CloseAsk()
        asks.seen("a")
        first = asks.ask()
        asks.answer("a", first, "refused")
        self.assertEqual(asks.verdict(["a"]), "refused")
        second = asks.ask()
        self.assertNotEqual(first, second)
        self.assertEqual(asks.verdict(["a"]), "working")
        self.assertFalse(asks.answer("a", first, "ok"), "古い頼みへの答えは数えない")

    def test_expired_request_is_withdrawn(self):
        asks = close_ask.CloseAsk()
        asks.seen("a")
        seq = asks.ask()
        with mock.patch.object(close_ask.CloseAsk, "EXPIRE_SEC", -1):
            self.assertEqual(asks.pending("a"), 0)
            self.assertFalse(asks.answer("a", seq, "ok"))

    def test_closed_page_is_not_waited_for(self):
        asks = close_ask.CloseAsk()
        asks.seen("a")
        asks.gone("a")
        asks.seen("a")                     # 閉じる間際の問い合わせが後から着いた
        self.assertEqual(asks.live(), [])

    def test_quiet_page_is_not_alive(self):
        asks = close_ask.CloseAsk()
        asks.seen("a", now=time.monotonic() - close_ask.CloseAsk.PAGE_ALIVE_SEC - 1)
        self.assertEqual(asks.live(), [])


class ShutdownAsksScreensTest(unittest.TestCase):
    """入口(app.py)の /api/shutdown・/api/close-ask・/api/close-answer。"""

    def setUp(self):
        import app as app_module

        self.app_module = app_module
        self.app = _make_app(self)
        self.client = self.app.test_client()
        self.stopped = threading.Event()
        app_module.set_shutdown_hook(self.stopped.set)
        self.addCleanup(app_module.set_shutdown_hook, None)
        patcher = mock.patch.object(close_ask.CloseAsk, "WAIT_SEC", 0.5)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_page(self, page="shell-1"):
        self.client.post("/api/alive", json={"client": page, "page": page, "state": "visible"})
        return page

    def shutdown_in_background(self, body=None):
        result = {}

        def run():
            client = self.app.test_client()
            res = client.post("/api/shutdown", json=body or {}, headers=H)
            result.update(status=res.status_code, body=res.get_json())

        thread = threading.Thread(target=run)
        thread.start()
        return thread, result

    def poll(self, page):
        res = self.client.get(f"/api/close-ask?page={page}", headers=H)
        self.assertEqual(res.status_code, 200)
        return res.get_json()["seq"]

    def wait_seq(self, page, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            seq = self.poll(page)
            if seq:
                return seq
            time.sleep(0.02)
        self.fail("頼みが出ませんでした")

    def answer(self, page, seq, state):
        return self.client.post("/api/close-answer", json={"page": page, "seq": seq, "state": state},
                                headers=H)

    def test_no_screen_stops_at_once(self):
        res = self.client.post("/api/shutdown", json={}, headers=H)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(self.stopped.wait(3))

    def test_screen_says_ok_then_it_stops(self):
        page = self.open_page()
        thread, result = self.shutdown_in_background()
        seq = self.wait_seq(page)
        self.answer(page, seq, "working")
        self.answer(page, seq, "ok")
        thread.join(5)
        self.assertEqual(result["status"], 200, result)
        self.assertTrue(self.stopped.wait(3))

    def test_screen_refuses_then_it_does_not_stop(self):
        page = self.open_page()
        thread, result = self.shutdown_in_background()
        seq = self.wait_seq(page)
        self.answer(page, seq, "refused")
        thread.join(5)
        self.assertEqual((result["status"], result["body"]["reason"]), (409, "refused"))
        self.assertFalse(self.stopped.wait(0.8))

    def test_still_asking_then_stops_itself_when_ok_arrives(self):
        """確かめている途中(本人が確認を読んでいる)で 409 asking。済んだら入口が自分で止まる。"""
        page = self.open_page()
        thread, result = self.shutdown_in_background()
        seq = self.wait_seq(page)
        self.answer(page, seq, "working")
        thread.join(5)
        self.assertEqual((result["status"], result["body"]["reason"]), (409, "asking"))
        self.assertFalse(self.stopped.is_set())
        res = self.answer(page, seq, "ok")
        self.assertTrue(res.get_json()["stopping"])
        self.assertTrue(self.stopped.wait(3))

    def test_quit_button_has_already_checked(self):
        self.open_page()
        res = self.client.post("/api/shutdown", json={"screens_ready": True}, headers=H)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(self.stopped.wait(3))

    def test_closed_screen_is_not_asked(self):
        page = self.open_page()
        self.client.post("/api/alive", json={"client": page, "page": page, "leaving": True})
        res = self.client.post("/api/shutdown", json={}, headers=H)
        self.assertEqual(res.status_code, 200)

    def test_busy_is_never_stopped_without_force(self):
        page = self.open_page()
        with mock.patch.object(self.app_module, "_busy_labels", return_value=["資材計算: 取り込み"]):
            res = self.client.post("/api/shutdown", json={}, headers=H)
            self.assertEqual((res.status_code, res.get_json()["reason"]), (409, "busy"))
            self.assertEqual(self.poll(page), 0, "途中の処理があるなら画面に頼まない")
            res = self.client.post("/api/shutdown", json={"force": True}, headers=H)
            self.assertEqual(res.status_code, 200)
        self.assertTrue(self.stopped.wait(3))

    def test_close_routes_need_the_token(self):
        self.assertEqual(self.client.get("/api/close-ask?page=x").status_code, 403)
        self.assertEqual(self.client.post("/api/close-answer",
                                          json={"page": "x", "seq": 1, "state": "ok"}).status_code, 403)


class ProcessManagerTest(unittest.TestCase):
    """stop.bat(process_manager.py)の戻り値 0 / 2 / 1。"""

    def setUp(self):
        info = mock.Mock(port=8740, token="t", pid=12345, app_root="")
        for target, value in (("read_lock", info), ("probe_health", {"app_id": "x"}),
                              ("is_our_app", True), ("remove_lock", None),
                              ("desktop_running", False)):
            patcher = mock.patch(f"launch_guard.{target}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, asked, *, gone=True):
        with mock.patch.object(process_manager, "_request_shutdown", return_value=asked), \
                mock.patch.object(process_manager, "_wait_gone", return_value=gone), \
                mock.patch.object(process_manager, "ASKING_WAIT_SEC", 0.1):
            return process_manager.main([])

    def test_stopped_is_0(self):
        self.assertEqual(self.run_main({"ok": True}), 0)

    def test_asking_waits_then_0_or_2(self):
        asking = {"ok": False, "reason": "asking", "message": "確かめています"}
        self.assertEqual(self.run_main(asking, gone=True), 0)
        self.assertEqual(self.run_main(asking, gone=False), 2)

    def test_refused_and_busy_are_2(self):
        self.assertEqual(self.run_main({"ok": False, "reason": "refused"}), 2)
        self.assertEqual(self.run_main({"ok": False, "busy": True, "reason": "busy",
                                        "running": ["資材計算: 取り込み"]}), 2)

    def test_reason_comes_first_for_the_launcher(self):
        """ランチャー 1.7.1 は stop.bat の出力のはじめの行を理由として見せる。"""
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            self.run_main({"ok": False, "reason": "refused", "message": "画面で「閉じない」が選ばれました"})
        self.assertTrue(out.getvalue().startswith("止めませんでした: 画面で「閉じない」"), out.getvalue())

    def test_old_launcher_stop_bat_still_stops(self):
        """1.2.4 の launcher_stop.bat(`--any` を付ける)が残っていても、断らずに止める。"""
        with mock.patch.object(process_manager, "_request_shutdown", return_value={"ok": True}), \
                mock.patch.object(process_manager, "_wait_gone", return_value=True):
            self.assertEqual(process_manager.main(["--any"]), 0)

    def test_desktop_is_not_stopped_and_is_1(self):
        with mock.patch("launch_guard.desktop_running", return_value=True), \
                mock.patch.object(process_manager, "bring_desktop_to_front") as front, \
                mock.patch.object(process_manager, "stop") as stop:
            self.assertEqual(process_manager.main([]), 1)
        front.assert_called_once()
        stop.assert_not_called()


    def test_force_does_not_relaunch_the_desktop_window(self):
        """ランチャーの強制終了(窓を閉じた直後の stop.bat --force)で exe を起こし直さない。"""
        with mock.patch("launch_guard.desktop_running", return_value=True), \
                mock.patch.object(process_manager, "bring_desktop_to_front") as front:
            self.assertEqual(process_manager.main(["--force"]), 1)
        front.assert_not_called()

if __name__ == "__main__":
    unittest.main()
