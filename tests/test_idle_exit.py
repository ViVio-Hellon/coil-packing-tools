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


class ScreensTest(unittest.TestCase):
    """画面ごとに数える(統合版)。

    ペナラベルの印刷ビューは別のタブで開く。1つだけで持つと、どちらかの
    タブを閉じた合図で、開いている他方ごと終わってしまう。
    """

    @staticmethod
    def _close(watch, client: str) -> None:
        watch.leaving(client)
        watch._screens[client].leaving_at = time.monotonic() - 9

    def test_外枠を閉じても別タブの画面が開いていれば終わらない(self):
        watch = _watch()
        watch.beat(hidden=False, client="shell-1")
        watch.beat(hidden=False, client="pena-1")
        self._close(watch, "shell-1")
        self.assertIsNone(watch.overdue())
        self.assertNotIn("shell-1", watch.screens(), "閉じた画面は忘れる")
        self.assertIn("pena-1", watch.screens())

    def test_全部閉じたら終わる(self):
        watch = _watch()
        watch.beat(hidden=False, client="shell-1")
        watch.beat(hidden=False, client="pena-1")
        self._close(watch, "shell-1")
        self.assertIsNone(watch.overdue())
        self._close(watch, "pena-1")
        self.assertEqual(watch.overdue(), "画面が閉じられました")

    def test_別タブを閉じても外枠は巻き添えにならない(self):
        watch = _watch()
        watch.beat(hidden=False, client="shell-1")
        watch.beat(hidden=False, client="pena-1")
        self._close(watch, "pena-1")
        self.assertIsNone(watch.overdue())

    def test_裏に回った画面は心拍が止まっても生きている(self):
        """印刷ビューを前に出すと、外枠は裏に回る。外枠を閉じずに放置しても終わらない。"""
        watch = _watch()
        watch.beat(hidden=True, client="shell-1")
        watch.beat(hidden=False, client="pena-1")
        watch._screens["shell-1"].seen = time.monotonic() - 8 * 3600
        self._close(watch, "pena-1")
        self.assertIsNone(watch.overdue())

    def test_心拍が途切れた画面だけ見送る(self):
        watch = _watch()
        watch.beat(hidden=False, client="shell-1")
        watch.beat(hidden=False, client="pena-1")
        watch._screens["pena-1"].seen = time.monotonic() - 91
        self.assertIsNone(watch.overdue())
        self.assertNotIn("pena-1", watch.screens())
        watch._screens["shell-1"].seen = time.monotonic() - 91
        self.assertIn("心拍がありません", watch.overdue() or "")

    def test_名乗らない画面と名乗った画面は別に数える(self):
        """梱包明細・資材計算の画面(名乗らない)を閉じても、外枠が開いていれば終わらない。"""
        watch = _watch()
        watch.beat(hidden=False)                     # 名乗らない(移植元の画面)
        watch.beat(hidden=False, client="shell-1")
        watch.leaving()
        watch._leaving_at = time.monotonic() - 9
        self.assertIsNone(watch.overdue())

    def test_知らない名前の閉じた合図は無視する(self):
        watch = _watch()
        watch.beat(hidden=False, client="shell-1")
        watch.leaving("pena-unknown")
        self.assertNotIn("pena-unknown", watch.screens())
        self.assertIsNone(watch.overdue())

    def test_スリープから戻ったらどの画面も数え直す(self):
        watch = _watch()
        watch.beat(hidden=False, client="shell-1")
        watch.beat(hidden=False, client="pena-1")
        for s in watch._screens.values():
            s.seen = time.monotonic() - 91
        watch.leaving("pena-1")
        mono = time.monotonic()
        watch.check_wake(wall=1000.0, mono=mono)
        self.assertGreater(watch.check_wake(wall=1000.0 + 600, mono=mono), 0)
        self.assertIsNone(watch.overdue())
        self.assertFalse(watch.screens()["pena-1"]["leaving"])

    def test_名乗りは長さと文字を絞る(self):
        watch = _watch()
        watch.beat(hidden=False, client="pena-<script>" + "x" * 200)
        (name,) = watch.screens()
        self.assertNotIn("<", name)
        self.assertLessEqual(len(name), 64)

    def test_覚える画面の数には上限がある(self):
        """閉じた合図が届かなかった画面が溜まり続けない。"""
        watch = _watch()
        for i in range(idle_exit.MAX_SCREENS + 10):
            watch.beat(hidden=True, client=f"pena-{i}")
        self.assertLessEqual(len(watch.screens()), idle_exit.MAX_SCREENS)
        self.assertIn(f"pena-{idle_exit.MAX_SCREENS + 9}", watch.screens(), "新しい画面を忘れた")

    def test_合図の取り次ぎ(self):
        idle_exit.reset()
        self.addCleanup(idle_exit.reset)
        self.assertFalse(idle_exit.signal(client="pena-1"), "見張りが無ければ False")
        watch = _watch()
        idle_exit._watch = watch
        self.assertTrue(idle_exit.signal(client="pena-1", hidden=False))
        self.assertTrue(idle_exit.signal(client="pena-1", leaving=True))
        self.assertTrue(watch.screens()["pena-1"]["leaving"])
        # 「裏に回った」は閉じた合図を取り消さない
        idle_exit.signal(client="pena-1", hidden=True)
        self.assertTrue(watch.screens()["pena-1"]["leaving"])


class DiscardedTabTest(unittest.TestCase):
    """ブラウザが裏のタブを捨てて、あとで読み直した(本ツール以外のタブが開いているとき)。

    捨てるときは何の合図も来ないので、前の名乗りは「裏に回ったまま」で残る。
    読み直した画面が前の名乗りを言ってくれば忘れる。言わないと全部閉じても終わらない。
    """

    def test_同じタブで開き直したら前の名乗りを忘れる(self):
        watch = _watch()
        watch.beat(hidden=False, client="shell-old")
        watch.beat(hidden=True, client="shell-old")        # 裏に回った → 捨てられた
        watch.beat(hidden=False, client="shell-new")        # 読み直した
        self.assertTrue(watch.forget("shell-old"))
        self.assertNotIn("shell-old", watch.screens())
        ScreensTest._close(watch, "shell-new")
        self.assertEqual(watch.overdue(), "画面が閉じられました")

    def test_忘れないと全部閉じても終わらない(self):
        """直す前の姿(裏の画面は心拍が途切れても数え続ける)。"""
        watch = _watch()
        watch.beat(hidden=True, client="shell-old")
        watch.beat(hidden=False, client="shell-new")
        ScreensTest._close(watch, "shell-new")
        self.assertIsNone(watch.overdue())

    def test_名乗らない画面は忘れない(self):
        watch = _watch()
        watch.beat(hidden=False)
        self.assertFalse(watch.forget(""))
        self.assertIn("", watch.screens())

    def test_合図に前の名乗りを添えられる(self):
        idle_exit.reset()
        self.addCleanup(idle_exit.reset)
        watch = idle_exit.install(lambda: None, lambda: False, tick_sec=3600)
        idle_exit.signal(client="shell-old", hidden=True)
        idle_exit.signal(client="shell-new", hidden=False, replaces="shell-old")
        self.assertEqual(set(watch.screens()), {"shell-new"})
        # 自分自身を言ってきても消さない
        idle_exit.signal(client="shell-new", hidden=False, replaces="shell-new")
        self.assertIn("shell-new", watch.screens())


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
