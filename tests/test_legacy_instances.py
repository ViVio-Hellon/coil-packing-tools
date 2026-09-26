"""統合前の単体版が動いていたら、統合版は起動しない(同じ手元のデータを使うため)。

移植元の多重起動の印は機能ごとの領域にあり、統合版の印は統合アプリの領域にある。
移行の途中で単体版と統合版が両方動くと、同じ作業状態・手元DB・設定を書き換える
(移植漏れの点検で見つかった)。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

import launch_guard


class FindLegacyTest(unittest.TestCase):
    def setUp(self):
        self.locks = launch_guard._legacy_lock_files()
        self.assertEqual(set(self.locks), {"nlm.packing-details", "PackingPenaLabel",
                                           "nlm.coil-material-tool"})
        self.written = []

    def tearDown(self):
        for p in self.written:
            p.unlink(missing_ok=True)

    def _lock(self, app_id: str, **info) -> None:
        path = Path(self.locks[app_id])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info), encoding="utf-8")
        self.written.append(path)

    def _health(self, answers: dict):
        return lambda port, timeout=0: answers.get(port)

    def test_nothing_running(self):
        with mock.patch.object(launch_guard, "probe_health", self._health({})):
            self.assertEqual(launch_guard.find_legacy_instances(), [])

    def test_running_standalone_is_found_from_its_lock(self):
        self._lock("nlm.coil-material-tool", pid=4321, port=8734)
        with mock.patch.object(launch_guard, "is_process_alive", lambda pid: pid == 4321), \
             mock.patch.object(launch_guard, "probe_health",
                               self._health({8734: {"app_id": "nlm.coil-material-tool"}})):
            found = launch_guard.find_legacy_instances()
        self.assertEqual([(x.label, x.port, x.pid) for x in found], [("資材計算(単体版)", 8734, 4321)])

    def test_pena_answers_with_appId(self):
        """ペナラベルの単体版は `appId` で答える。記録が無くても既定のポートで見つける。"""
        with mock.patch.object(launch_guard, "probe_health",
                               self._health({8731: {"appId": "PackingPenaLabel"}})):
            found = launch_guard.find_legacy_instances()
        self.assertEqual([(x.label, x.port) for x in found], [("ペナラベル(単体版)", 8731)])

    def test_other_app_on_the_port_is_not_a_standalone(self):
        """同じポートで別のアプリ(統合版自身など)が答えても、単体版とはみなさない。"""
        self._lock("nlm.packing-details", pid=99, port=8733)
        with mock.patch.object(launch_guard, "is_process_alive", lambda pid: True), \
             mock.patch.object(launch_guard, "probe_health",
                               self._health({8733: {"app_id": "nlm.coil-packing-tools"}})):
            self.assertEqual(launch_guard.find_legacy_instances(), [])

    def test_dead_process_lock_is_ignored(self):
        self._lock("nlm.packing-details", pid=99, port=8735)
        with mock.patch.object(launch_guard, "is_process_alive", lambda pid: False), \
             mock.patch.object(launch_guard, "probe_health",
                               self._health({8735: {"app_id": "nlm.packing-details"}})):
            self.assertEqual(launch_guard.find_legacy_instances(), [])


class StartRefusesTest(unittest.TestCase):
    def test_start_stops_with_a_clear_reason(self):
        import start_app
        found = [launch_guard.LegacyInstance("梱包明細(単体版)", 8733, 1234)]
        with mock.patch.object(launch_guard, "find_legacy_instances", return_value=found), \
             mock.patch.object(launch_guard, "check_existing",
                               return_value=launch_guard.GuardResult(True, reason="ロックなし")), \
             mock.patch.object(launch_guard, "pick_port") as pick:
            with self.assertRaises(start_app.StartupError) as cm:
                start_app.start("main", open_browser=False)
        self.assertIn("単体版", str(cm.exception))
        self.assertIn("stop.bat", cm.exception.hint)
        pick.assert_not_called()


if __name__ == "__main__":
    unittest.main()
