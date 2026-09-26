"""自動終了の見張り(統合版: `common/idle_exit.py`)

梱包明細の実装を採った。資材計算の移植元の試験(`_notice_suspend`)も
そのまま通ることで、資材計算から見た振る舞いが変わっていないことを確かめる。
"""
from __future__ import annotations

import time
import unittest

from common import idle_exit


def _watch(**kw) -> idle_exit.IdleWatch:
    return idle_exit.IdleWatch(lambda: None, lambda: False,
                               idle_sec=90, grace_sec=8, tick_sec=2, **kw)


def _age(watch, seconds: float) -> None:
    watch._seen = time.monotonic() - seconds


class IdleWatchTest(unittest.TestCase):
    def test_前に居て心拍が途切れたら終わる(self):
        watch = _watch()
        watch.beat(hidden=False)
        _age(watch, 91)
        self.assertIn("心拍がありません", watch.overdue() or "")

    def test_裏に回っていれば心拍が止まっても終わらない(self):
        watch = _watch()
        watch.beat(hidden=True)
        _age(watch, 8 * 3600)
        self.assertIsNone(watch.overdue())

    def test_裏でも閉じた合図は効く(self):
        watch = _watch()
        watch.beat(hidden=True)
        watch.leaving()
        watch._leaving_at = time.monotonic() - 9
        self.assertEqual(watch.overdue(), "画面が閉じられました")

    def test_裏に回った合図は閉じた合図を取り消さない(self):
        watch = _watch()
        watch.beat(hidden=False)
        watch.leaving()
        watch.beat(hidden=True, keep_leaving=True)
        self.assertIsNotNone(watch._leaving_at)
        watch.beat(hidden=False)                  # ふつうの心拍は取り消す(再読込)
        self.assertIsNone(watch._leaving_at)

    def test_一度も繋がっていなければ終わらない(self):
        self.assertIsNone(_watch().overdue())

    def test_スリープから戻ったら数え直す_壁時計(self):
        """壁時計だけが飛ぶ(Linux では単調時計はスリープ中に進まない)。"""
        watch = _watch()
        watch.beat(hidden=False)
        mono = time.monotonic()
        watch.check_wake(wall=1000.0, mono=mono)
        gap = watch.check_wake(wall=1000.0 + 7200, mono=mono + 2.0)
        self.assertGreater(gap, 0)
        self.assertIsNone(watch.overdue())

    def test_スリープから戻ったら数え直す_単調時計(self):
        """資材計算の移植元の呼び名(`_notice_suspend`)でも同じ。"""
        watch = _watch()
        watch.beat(hidden=False)
        mono = time.monotonic()
        watch.check_wake(wall=1000.0, mono=mono - 7200)
        _age(watch, 7200)
        gap = watch.check_wake(wall=1002.0, mono=mono)
        self.assertGreater(gap, 0)
        self.assertIsNone(watch.overdue())
        self.assertIs(watch._notice_suspend.__func__, idle_exit.IdleWatch._notice_suspend)

    def test_ふつうの刻みは眠っていたことにしない(self):
        watch = _watch()
        watch.beat(hidden=False)
        wall, mono = 1000.0, time.monotonic()
        for _ in range(50):
            self.assertEqual(watch.check_wake(wall=wall, mono=mono), 0.0)
            wall += 2; mono += 2
        _age(watch, 100)
        self.assertIsNotNone(watch.overdue())

    def test_戻ったことを外へ知らせる(self):
        got = []
        watch = _watch(on_wake=got.append)
        watch.beat(hidden=False)
        mono = time.monotonic()
        watch.check_wake(wall=1000.0, mono=mono)
        watch.check_wake(wall=1000.0 + 300, mono=mono + 300)
        self.assertEqual(len(got), 1)
        self.assertGreater(got[0], 200)

    def test_資材計算の別名(self):
        self.assertEqual(idle_exit.SUSPEND_SEC, idle_exit.WAKE_GAP_SEC)


class SingletonTest(unittest.TestCase):
    """見張りはプロセスに1つ。3機能の `idle_exit` は同じもの。"""

    def setUp(self):
        idle_exit.reset()
        self.addCleanup(idle_exit.reset)

    def test_機能の取り次ぎは共通側そのもの(self):
        from modules.packing_details.meisai import idle_exit as a
        from modules.packing_material_calculation.coil_tool import idle_exit as b
        self.assertIs(a, idle_exit)
        self.assertIs(b, idle_exit)

    def test_2度立てても1つ(self):
        first = idle_exit.install(lambda: None, lambda: False, tick_sec=3600)
        second = idle_exit.install(lambda: None, lambda: False, tick_sec=3600)
        self.assertIs(first, second)
        self.assertIs(idle_exit.get(), first)
