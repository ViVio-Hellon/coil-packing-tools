"""開いている間は終わらない ── 裏に回ったタブ・PCのスリープ

【なぜ要るのか】
現場から「見ていないからといって、開いているものを勝手に閉じられては
困る。開いていたら維持してほしい」(2026-09-25)。記録には
`誰も見ていないので終了します(90秒 心拍がありません)` とあった。

ブラウザは**裏に回ったタブのタイマーを間引く**(Chrome/Edge は5分後
から1分に1回まで。Edge の「スリープ中のタブ」なら止まる。最小化も
裏扱い)。心拍が届かなくなり、90秒で「誰も見ていない」と判断していた。
PCのスリープから戻ったときも、眠っていたぶんが経過に数えられていた。
"""
from __future__ import annotations

import pytest

from modules.packing_material_calculation.coil_tool import idle_exit
from modules.packing_material_calculation.app import screen
from modules.packing_material_calculation.tests._web import client, sandbox  # noqa: F401


class 時計:
    """`time.monotonic` の代わり。進めたいだけ進める。"""
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = 時計()
    monkeypatch.setattr(idle_exit.time, "monotonic", c)
    return c


@pytest.fixture
def watch(clock):
    stopped = []
    w = idle_exit.IdleWatch(lambda: stopped.append(1), lambda: False,
                            idle_sec=90, grace_sec=8, tick_sec=2)
    w.stopped = stopped
    return w


# ==================================================================
# 裏に回ったタブ
# ==================================================================
def test_見えている画面の心拍が途切れたら終わる(watch, clock):
    """いままでどおり。ブラウザが落ちたときのための見張り。"""
    watch.beat(hidden=False)
    clock.now += 91
    assert watch.overdue() is not None


def test_裏に回った画面は心拍が途切れても終わらない(watch, clock):
    """**ここが今回の直し。** 何時間裏にあっても、開いている。"""
    watch.beat(hidden=True)
    clock.now += 8 * 3600
    assert watch.overdue() is None


def test_戻ってきたらまた心拍を見る(watch, clock):
    watch.beat(hidden=True)
    clock.now += 3600
    watch.beat(hidden=False)
    assert watch.overdue() is None
    clock.now += 91
    assert watch.overdue() is not None


def test_裏に回っていても閉じたら終わる(watch, clock):
    """閉じたことは pagehide の合図で分かる。**閉じたものは閉じる。**"""
    watch.beat(hidden=True)
    watch.leaving()
    clock.now += 9
    assert watch.overdue() == "画面が閉じられました"


def test_裏かどうかを言わない心拍は前のまま(watch, clock):
    """古い画面・試験の心拍は `hidden` を言わない。それで表に戻さない。"""
    watch.beat(hidden=True)
    watch.beat()
    clock.now += 3600
    assert watch.overdue() is None


# ==================================================================
# PCのスリープ
# ==================================================================
def test_眠っていたら待ち直す(watch, clock):
    """起きた瞬間は「最後の心拍から何分も経った」ように見えるが、
    誰も居なくなったわけではない。"""
    watch.beat(hidden=False)
    watch._notice_suspend()                 # 見張りの1回目
    clock.now += 2 * 3600                   # PC が2時間スリープ
    watch._notice_suspend()                 # 起きて最初の見張り
    assert watch.overdue() is None
    clock.now += 91                         # 起きてからも心拍が来なければ
    assert watch.overdue() is not None      # そこで初めて終わる


def test_ふつうの間隔なら眠っていたことにしない(watch, clock):
    watch.beat(hidden=False)
    for _ in range(50):
        watch._notice_suspend()
        clock.now += 2
    assert watch.overdue() is not None      # 100秒ぶん途切れている


# ==================================================================
# 使える画面は1枚 ── 裏の間に取られない
# ==================================================================
@pytest.fixture
def screens(clock, monkeypatch):
    monkeypatch.setattr(screen.time, "monotonic", clock)
    screen.reset()
    yield
    screen.reset()


def test_裏に回った画面は空きにならない(screens, clock):
    """空きにすると、別のタブが黙って通り、戻ってきた元の画面が
    「使われなくなりました」になる。"""
    assert screen.claim("A").ok
    assert screen.beat("A", hidden=True)
    clock.now += 3600
    other = screen.claim("B")
    assert not other.ok and other.reason == screen.REFUSE_TAKEN
    assert screen.beat("A", hidden=False)   # 戻っても自分のまま


def test_見えている画面は途切れたら空きになる(screens, clock):
    """いままでどおり(ブラウザが落ちたとき)。"""
    assert screen.claim("A").ok
    assert screen.beat("A", hidden=False)
    clock.now += screen.STALE_SEC + 1
    assert screen.claim("B").ok


def test_裏に回っていても取り上げはできる(screens, clock):
    """ブラウザごと落ちて裏のまま残った画面は、人が取り上げる。"""
    assert screen.claim("A").ok
    screen.beat("A", hidden=True)
    assert screen.claim("B", takeover=True).ok


# ==================================================================
# 画面ごし
# ==================================================================
def test_裏に回った合図が届く(client, monkeypatch):
    from modules.packing_material_calculation.tests._web import client as _  # noqa: F401
    w = idle_exit.IdleWatch(lambda: None, lambda: False)
    monkeypatch.setattr(idle_exit, "_watch", w)
    client.post("/api/alive", json={"hidden": True})
    assert w._hidden is True
    client.post("/api/alive", json={"hidden": False})
    assert w._hidden is False
    client.post("/api/alive", json={})       # 言わなければ前のまま
    assert w._hidden is False
