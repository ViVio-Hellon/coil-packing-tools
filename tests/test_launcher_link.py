"""業務ツール統合ランチャーとの連携(統合 1.2.4)

ランチャーは各ツールの中身を知らずに、ツールが用意した口だけを使う:

    起動      コイル梱包ツール.exe(デスクトップ版)/ Start.vbs(ブラウザ版。引数をそのまま渡す)
    起動完了  launcher_status.bat … 戻り値 0=使える 2=起動中 1=動いていない
              (ブラウザ版は /api/health でも分かる。デスクトップ版はポートが無いのでこちら)
    終了      launcher_stop.bat   … 動いているほうを止める。処理中は止めずに 1(--force で中断)

デスクトップ版はポートを持たないので、ローカル領域のファイルで頼む(`common/desktop_control.py`)。
本物の bridge.py を相手にした試験は `tests/test_bridge.py` の
`test_launcher_can_see_ready_and_stop_it_without_a_port`。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import launch_guard
import process_manager
from common import desktop_control


class _Local(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="cpt-launcher-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        patcher = mock.patch.dict(os.environ, {"COIL_PACKING_TOOLS_LOCAL_DIR": str(tmp)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.stopped = threading.Event()
        self.busy: list = []
        self.ready = False

    def watch(self, *, start=True):
        """見張り。状態だけを見る試験は動かさずに `tick()` を呼ぶ(書き手を1つにする)。"""
        w = desktop_control.Watch(app_id="nlm.coil-packing-tools",
                                  state=lambda: {"ready": self.ready, "stage": "取り込み中"},
                                  busy=lambda: self.busy, stop=self.stopped.set, tick_sec=0.05)
        if start:
            w.start()
        self.addCleanup(w.cancel)
        return w


class DesktopControlTest(_Local):
    def test_state_tells_ready_and_stage(self):
        w = self.watch(start=False)
        w.tick()
        state = desktop_control.read_state()
        self.assertEqual((state["app_id"], state["ready"], state["stage"]),
                         ("nlm.coil-packing-tools", False, "取り込み中"))
        self.ready = True
        w.tick()
        self.assertTrue(desktop_control.read_state()["ready"])

    def test_old_state_means_not_answering(self):
        self.watch(start=False).tick()
        self.assertIsNone(desktop_control.read_state(fresh_sec=-1))

    def test_stops_when_not_busy(self):
        self.watch()
        answer = desktop_control.request_stop(answer_sec=5)
        self.assertTrue(answer["stopped"])
        self.assertTrue(self.stopped.wait(2), "外枠へ「終了してよい」を知らせる")

    def test_does_not_stop_while_busy_unless_forced(self):
        """統合画面の「終了」と同じ。取り込みの最中は止めない(--force なら止める)。"""
        self.busy = ["資材計算: 取り込み"]
        self.watch()
        answer = desktop_control.request_stop(answer_sec=5)
        self.assertFalse(answer["stopped"])
        self.assertEqual(answer["running"], ["資材計算: 取り込み"])
        self.assertFalse(self.stopped.is_set())
        answer = desktop_control.request_stop(force=True, answer_sec=5)
        self.assertTrue(answer["stopped"])
        self.assertTrue(self.stopped.wait(2))

    def test_no_answer_is_none(self):
        self.assertIsNone(desktop_control.request_stop(answer_sec=0.3))

    def test_a_request_left_from_before_is_not_answered(self):
        """前の回に置かれたままの頼みで、起動した直後に止まらない。"""
        desktop_control._write_json(desktop_control._path(desktop_control.REQUEST_NAME),
                                    {"id": "old", "force": True})
        w = desktop_control.Watch(app_id="x", state=dict, busy=list, stop=self.stopped.set)
        w.tick()
        self.assertFalse(self.stopped.is_set())


class ProcessManagerAnyTest(_Local):
    def test_status_codes(self):
        with mock.patch.object(process_manager, "status", return_value=None), \
                mock.patch.object(launch_guard, "desktop_running", return_value=False):
            self.assertEqual(process_manager.any_status()[0], process_manager.STATUS_STOPPED)
        w = self.watch(start=False)
        w.tick()
        with mock.patch.object(process_manager, "status", return_value=None), \
                mock.patch.object(launch_guard, "desktop_running", return_value=True):
            self.assertEqual(process_manager.any_status()[0], process_manager.STATUS_STARTING)
            self.ready = True
            w.tick()
            self.assertEqual(process_manager.any_status()[0], process_manager.STATUS_READY)
        browser = {"ready": True, "_lock": {"port": 8740}}
        with mock.patch.object(process_manager, "status", return_value=browser):
            code, text = process_manager.any_status()
        self.assertEqual(code, process_manager.STATUS_READY)
        self.assertIn("ブラウザ版", text)

    def test_stop_desktop_waits_until_it_is_gone(self):
        self.watch()
        with mock.patch.object(launch_guard, "desktop_running",
                               side_effect=lambda: not self.stopped.is_set()):
            result = process_manager.stop_desktop()
        self.assertTrue(result.stopped, result.message)

    def test_stop_desktop_reports_busy(self):
        self.busy = ["梱包明細: 取り込み"]
        self.watch()
        with mock.patch.object(launch_guard, "desktop_running", return_value=True):
            result = process_manager.stop_desktop()
        self.assertFalse(result.stopped)
        self.assertEqual(result.busy_jobs, ["梱包明細: 取り込み"])

    def test_cli_any_returns_1_when_something_stays(self):
        busy = process_manager.StopResult("デスクトップ版")
        ok = process_manager.StopResult("main")
        ok.stopped = True
        with mock.patch.object(process_manager, "stop", return_value=ok), \
                mock.patch.object(process_manager, "stop_desktop", return_value=busy):
            self.assertEqual(process_manager.main(["--any"]), 1)
        busy.stopped = True
        with mock.patch.object(process_manager, "stop", return_value=ok), \
                mock.patch.object(process_manager, "stop_desktop", return_value=busy):
            self.assertEqual(process_manager.main(["--any"]), 0)


if __name__ == "__main__":
    unittest.main()
