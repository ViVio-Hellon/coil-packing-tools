"""裏に回ったタブ・スリープからの戻り (自動終了と画面の空き)

ブラウザは裏に回ったタブのタイマーを間引く。Chrome は5分を過ぎると
1分に1回まで、Edge のスリープタブは完全に止める。PCのスリープでは
サーバごと止まる。直す前は、実機でこうなっていた(時間は本番の1/10):

    裏で心拍が空きの猶予を超えて途切れる → 前に戻ると 409 screen_taken
    裏で心拍が自動終了の時間を超えて途切れる → プロセスが終わる
    スリープから戻る                       → 戻った直後に終わる

**守ること**
- 裏に回った画面の心拍が止まっても、終わらない・空きにしない
- 持ち主の心拍は、どれだけ遅れても受ける(締め出さない)
- スリープから戻ったら、止まっていた時間を数えない
- **今までどおり終わるべきときは終わる**
  (前に居るのに心拍が途切れた＝ブラウザが落ちた / タブを閉じた)
"""
from __future__ import annotations

import time
import unittest

from modules.packing_details.meisai import idle_exit, screen

from .test_web import HEADERS, SCREEN, _client, _seed


def _watch(**kw) -> idle_exit.IdleWatch:
    """見張り。**スレッドは立てない**(判断だけを確かめる)。"""
    return idle_exit.IdleWatch(lambda: None, lambda: False,
                               idle_sec=90, grace_sec=8, tick_sec=2, **kw)


def _age(watch: idle_exit.IdleWatch, seconds: float) -> None:
    """最後の心拍を `seconds` 秒前にする。"""
    watch._seen = time.monotonic() - seconds


class IdleWatchTest(unittest.TestCase):
    """自動終了の判断。"""

    def test_前に居て心拍が途切れたら終わる(self):
        """**今までどおり。** ブラウザが落ちたときはこれで終わる。"""
        watch = _watch()
        watch.beat(hidden=False)
        _age(watch, 91)
        self.assertIn("心拍がありません", watch.overdue() or "")

    def test_裏に回っていれば心拍が止まっても終わらない(self):
        """Edge のスリープタブはタイマーを完全に止める。何時間でも待つ。"""
        watch = _watch()
        watch.beat(hidden=True)
        _age(watch, 8 * 60 * 60)
        self.assertIsNone(watch.overdue())

    def test_前に戻ったら今までどおり数える(self):
        watch = _watch()
        watch.beat(hidden=True)
        watch.beat(hidden=False)
        _age(watch, 91)
        self.assertIsNotNone(watch.overdue())

    def test_裏表を名乗らない心拍は裏表を変えない(self):
        watch = _watch()
        watch.beat(hidden=True)
        watch.beat()
        self.assertTrue(watch.hidden)

    def test_裏のまま閉じても終わる(self):
        """**閉じた合図は裏でも効く。**"""
        watch = _watch()
        watch.beat(hidden=True)
        watch.leaving()
        watch._leaving_at = time.monotonic() - 9
        self.assertIn("閉じられました", watch.overdue() or "")

    def test_閉じた合図のあとに裏に回った合図が来ても終わる(self):
        """タブを閉じるとブラウザは「裏に回った」と「閉じた」を両方送る。
        **届く順は決まっていない。** 後から来た「裏に回った」で取り消すと
        閉じたのに終わらない(実機の試験で見つかった)。
        """
        watch = _watch()
        watch.beat(hidden=False)
        watch.leaving()
        watch.beat(hidden=True, keep_leaving=True)     # 遅れて届いた「裏に回った」
        watch._leaving_at = time.monotonic() - 9
        self.assertIn("閉じられました", watch.overdue() or "")

    def test_再読込なら閉じた合図は取り消される(self):
        """再読込でも閉じた合図は飛ぶ。新しい画面の心拍で取り消す。"""
        watch = _watch()
        watch.beat(hidden=False)
        watch.leaving()
        self.assertIsNotNone(watch._leaving_at)
        watch.beat(hidden=False)                                # 新しい画面の心拍
        self.assertIsNone(watch._leaving_at)
        self.assertIsNone(watch.overdue())

    def test_1度も繋がっていなければ終わらない(self):
        """`--no-browser` で立てておく使い方を巻き添えにしない(今までどおり)。"""
        self.assertIsNone(_watch().overdue())


class WakeTest(unittest.TestCase):
    """スリープからの戻り。**止まっていた時間を「心拍なし」と数えない。**"""

    def test_刻みが大きく飛んだらスリープから戻ったとみなす(self):
        woke = []
        watch = _watch(on_wake=woke.append)
        watch.beat(hidden=False)
        watch.check_wake(wall=1000.0, mono=500.0)
        gap = watch.check_wake(wall=1600.0, mono=1100.0)        # 10分止まっていた
        self.assertGreater(gap, 500)
        self.assertEqual(len(woke), 1)

    def test_戻った直後は終わらない(self):
        """直す前は、戻った直後に「心拍なし」で終わっていた(実測)。"""
        watch = _watch()
        watch.beat(hidden=False)
        _age(watch, 600)                                        # 止まっていた10分
        now = time.monotonic()
        watch.check_wake(wall=time.time() - 600, mono=now - 600)
        watch.check_wake()                                      # 戻った最初の刻み
        self.assertIsNone(watch.overdue())

    def test_単調時計だけが飛んでも気づく(self):
        """Windows の単調時計はスリープ中も進むことがある。"""
        watch = _watch()
        watch.check_wake(wall=1000.0, mono=500.0)
        self.assertGreater(watch.check_wake(wall=1002.0, mono=560.0), 0)

    def test_壁時計だけが飛んでも気づく(self):
        """Linux の単調時計はスリープ中に進まない。"""
        watch = _watch()
        watch.check_wake(wall=1000.0, mono=500.0)
        self.assertGreater(watch.check_wake(wall=1060.0, mono=502.0), 0)

    def test_ふつうの刻みでは騒がない(self):
        woke = []
        watch = _watch(on_wake=woke.append)
        watch.check_wake(wall=1000.0, mono=500.0)
        self.assertEqual(watch.check_wake(wall=1002.1, mono=502.1), 0.0)
        self.assertEqual(woke, [])

    def test_戻った後始末が失敗しても見張りは続く(self):
        def boom(_gap):
            raise RuntimeError("後始末に失敗")
        watch = _watch(on_wake=boom)
        watch.check_wake(wall=1000.0, mono=500.0)
        watch.check_wake(wall=1600.0, mono=1100.0)             # 例外が漏れない


class ScreenBackgroundTest(unittest.TestCase):
    """画面の空き判定。"""

    def setUp(self):
        screen.reset()
        self.addCleanup(screen.reset)
        screen.claim("A")

    def _age(self, seconds: float) -> None:
        screen._last_seen = time.time() - seconds

    def test_持ち主の心拍はどれだけ遅れても受ける(self):
        """**締め出さない。** 直す前は、裏から戻った持ち主が
        `409 screen_taken` で自分の画面から締め出されていた。
        """
        self._age(screen.GRACE_SEC * 10)
        self.assertTrue(screen.beat("A"))

    def test_裏に回った持ち主は空きにしない(self):
        """心拍が止まっているのはブラウザの間引き。画面は閉じられていない。"""
        screen.beat("A", hidden=True)
        self._age(screen.GRACE_SEC * 100)
        self.assertIsNotNone(screen.holder())
        self.assertFalse(screen.claim("B"))                     # 2枚目は断る

    def test_前に居て心拍が途切れたら空く(self):
        """**今までどおり。** タブが落ちたら誰も入れない、にはしない。"""
        screen.beat("A", hidden=False)
        self._age(screen.GRACE_SEC + 1)
        self.assertTrue(screen.claim("B"))
        self.assertFalse(screen.beat("A"))                      # 取られたら断る

    def test_別の画面が取ったら持ち主の心拍は断る(self):
        screen.hand_over()
        screen.claim("B")
        self.assertFalse(screen.beat("A", hidden=False))

    def test_業務の要求は裏表を変えない(self):
        screen.beat("A", hidden=True)
        screen.beat("A")                                        # 名乗らない
        self.assertTrue(screen.is_hidden())

    def test_スリープから戻ったら空きの猶予を数え直す(self):
        screen.beat("A", hidden=False)
        self._age(screen.GRACE_SEC * 10)
        screen.note_wake(600)
        self.assertFalse(screen.claim("B"))

    def test_閉じたら裏の印も消える(self):
        screen.beat("A", hidden=True)
        screen.release("A")
        self.assertFalse(screen.is_hidden())
        self.assertTrue(screen.claim("B"))


class AliveEndpointTest(unittest.TestCase):
    """`/api/alive` が裏表を受け取って、見張りと空き判定へ渡す。"""

    def setUp(self):
        self.client, self.conn = _client(self)
        idle_exit.reset()
        self.watch = idle_exit.install(lambda: None, lambda: False, tick_sec=3600)
        self.addCleanup(idle_exit.reset)

    def _alive(self, **body):
        return self.client.post("/api/alive", json={"screen": SCREEN, **body})

    def test_裏に回った合図で見張りが待つ(self):
        self._alive(state="hidden", reason="hidden")
        self.assertTrue(self.watch.hidden)
        self.assertTrue(screen.is_hidden())

    def test_前に戻った合図で数え直す(self):
        self._alive(state="hidden")
        self._alive(state="visible", reason="foreground")
        self.assertFalse(self.watch.hidden)
        self.assertFalse(screen.is_hidden())

    def test_持ち主でない画面の裏表では見張りを揺らさない(self):
        """引き継がれた古いタブが裏で何を言っても、判断を変えない。"""
        self.client.post("/api/alive", json={"screen": "古いタブ", "state": "hidden"})
        self.assertFalse(self.watch.hidden)

    def test_閉じた合図のあとの裏に回った合図で取り消さない(self):
        self._alive(state="visible", reason="open")             # 繋がっていた画面
        self._alive(leaving=True, reason="close")
        self._alive(state="hidden", reason="hidden")            # 遅れて届いた
        self.assertIsNotNone(self.watch._leaving_at)

    def test_心拍はプロセスの番号を返す(self):
        """前に戻った画面が「裏で開き直されていないか」を確かめるため。"""
        import os
        self.assertEqual(self._alive(state="visible").get_json()["pid"], os.getpid())

    def test_裏から戻った持ち主はそのまま操作できる(self):
        """直す前の実測: 空きの猶予を過ぎて戻ると `409 screen_taken`。"""
        _seed(self.conn)
        self._alive(state="hidden")
        screen._last_seen = time.time() - screen.GRACE_SEC * 10
        self._alive(state="visible", reason="foreground")
        res = self.client.post("/api/lot", json={"lot_no": "L5160Z0"}, headers=HEADERS)
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))

    def test_画面に心拍の間隔とプロセスの番号を渡す(self):
        """心拍の間隔の出どころは `idle_exit.HEARTBEAT_MS` の1か所。"""
        import os
        screen.reset()
        html = self.client.get("/meisai").get_data(as_text=True)
        self.assertIn(f"alivePollMs: {idle_exit.HEARTBEAT_MS},", html)
        self.assertIn(f"serverPid: {os.getpid()},", html)


if __name__ == "__main__":
    unittest.main()
