"""使ってよい画面は1つだけ ── タブの取り合いを起こさせない

【なぜ要るのか】
`session.py` の作業状態は**プロセスに1つ**しかない。VBA の
`UF_Material` が1枚しか開かなかったのと同じ持ち方なので、そこは
そのままでよい。まずいのは**ブラウザのタブは何枚でも開ける**ことで、
2枚開くと2枚とも入力できてしまい、同じ1つの作業状態を奪い合う。

    タブA: LotNo 1234567 を打つ    → 作業状態 = 1234567
    タブB: LotNo 7654321 を打つ    → 作業状態 = 7654321
    タブA: 「計算Start」を押す      → **7654321 で計算される**

タブAの画面には 1234567 が出たままなので、**打った本人が気づけない。**
チェックリストへ積んだあとで気づくと、どの行が正しいのか分からない。

【プロセスの多重起動とは別の話】
`launch_guard.py` が見ているのは「Python の二重起動」で、これは
すでに塞いである。こちらは**1つのプロセスに画面が何枚ぶら下がるか**。
別の問題なので、別の関門で塞ぐ。

【どう塞ぐか】
タブごとの符牒(`screen`)を1つだけ通す。2枚目は**開いた時点で断る**
── 使えてしまってから「実はさっきの入力は消えました」と言うのが
いちばん悪い。

    1枚目 … 通す。以後、心拍のたびに「まだ自分のものか」を確かめる
    2枚目 … 断る。「この画面で使う」を押せば**取り上げて**入れ替わる
    取り上げられた側 … 次の心拍(20秒以内)で気づいて、使えなくなる

【取り上げを用意する理由】
ブラウザが落ちた・別の端末で画面を開いたまま鍵をかけた、のときに
**誰も使えないアプリ**になってしまう。取り上げは「2枚とも使える」に
はしない ── 入れ替わるだけなので、使ってよい画面はいつでも1つ。

【放っておかれた符牒】
心拍が `STALE_SEC` 途切れたら空きとみなす。タブを閉じたことは
`pagehide` の合図で即座に伝わるので、ふつうは待たずに空く。

**裏に回っている画面は、心拍が途切れても空きにしない。** ブラウザは
裏のタブのタイマーを間引くので心拍は途切れるが、開いている。空きに
すると、別のタブが黙って通り、戻ってきた元の画面が「使われなく
なりました」になる(2026-09-25 の現場の指摘と同じ理由)。
本当に落ちていた(ブラウザごと落ちた)ときは「この画面で使う」で
取り上げる。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from modules.packing_material_calculation.coil_tool import idle_exit
from modules.packing_material_calculation.coil_tool.logging_utils import get_logger

log = get_logger("app.screen")

# 心拍が何回途切れたら空きとみなすか。
# **間隔の出どころは `idle_exit.HEARTBEAT_MS` ただ1つ**にする ──
# ここに秒数を直に書くと、片方だけ変えたときに黙ってずれる
MISSED_BEATS = 3
STALE_SEC = MISSED_BEATS * idle_exit.HEARTBEAT_MS / 1000.0

# 断りの理由。画面は文言ではなくこれを見る
REFUSE_TAKEN = "screen_taken"
REFUSE_NO_SCREEN = "no_screen"


@dataclass(frozen=True)
class Claim:
    """いま使ってよい画面。"""
    screen: str
    since: float          # 最初に通した時刻 (monotonic)
    seen: float           # 最後に心拍が来た時刻 (monotonic)
    hidden: bool = False  # 裏に回っているか。裏の間は古くならない

    def age(self) -> float:
        """開いてから何秒たったか。"""
        return max(0.0, time.monotonic() - self.since)

    def silent(self) -> float:
        """心拍が途切れてから何秒たったか。"""
        return max(0.0, time.monotonic() - self.seen)


@dataclass(frozen=True)
class Result:
    ok: bool
    reason: str = ""
    message: str = ""
    # 断ったとき、**相手がどれくらい前から開いているか**。
    # 「さっき自分で開いたタブ」なのか「隣の人が使っている」のかは、
    # これが無いと画面の前の人に判断できない
    other_age: float = 0.0


_lock = threading.Lock()
_claim: Optional[Claim] = None


def _live(claim: Optional[Claim]) -> Optional[Claim]:
    """心拍が続いているものだけを返す。切れていれば空き扱い。"""
    if claim is None:
        return None
    if claim.hidden:
        return claim           # 裏に回っているだけ。閉じれば合図が来る
    if claim.silent() > STALE_SEC:
        return None
    return claim


def holder() -> Optional[Claim]:
    """いま使ってよい画面。誰も居なければ `None`。"""
    with _lock:
        return _live(_claim)


def claim(screen: str, *, takeover: bool = False) -> Result:
    """この画面で使わせてほしい、と申し出る。

    `takeover` は**人が押したときだけ**立てる。自動で取り上げると、
    断られた画面が勝手に奪い返して取り合いになる。
    """
    global _claim
    screen = (screen or "").strip()
    if not screen:
        return Result(False, REFUSE_NO_SCREEN, "画面の符牒がありません")

    with _lock:
        live = _live(_claim)

        if live is None:
            _claim = _new(screen)
            log.info("画面を通しました: %s", screen)
            return Result(True)

        if live.screen == screen:
            # 同じタブの読み直し。**通した時刻は動かさない** ──
            # 画面を移るたびに「開いてから何秒」が 0 に戻ると、
            # 断られた側に出る「◯分前から開いています」が嘘になる
            _claim = Claim(live.screen, live.since, time.monotonic())
            return Result(True)

        if takeover:
            log.info("画面を取り上げました: %s → %s", live.screen, screen)
            _claim = _new(screen)
            return Result(True)

        return Result(False, REFUSE_TAKEN,
                      "このアプリはすでに別の画面で開いています。",
                      other_age=live.age())


def beat(screen: str, *, hidden: Optional[bool] = None) -> bool:
    """心拍。**まだ自分のものなら `True`。**

    `False` が返ったら、その画面は取り上げられている。呼んだ側は
    そこで使えなくする ── 取り上げた側と2枚とも動くのを防ぐ最後の砦。
    """
    global _claim
    screen = (screen or "").strip()
    if not screen:
        return False
    with _lock:
        live = _live(_claim)
        if live is None:
            # 空いている。心拍を送ってきた画面がそのまま継ぐ ──
            # 通信が切れて戻ってきただけの画面を、締め出さない
            _claim = _new(screen)
            return True
        if live.screen != screen:
            return False
        _claim = Claim(live.screen, live.since, time.monotonic(),
                       live.hidden if hidden is None else bool(hidden))
        return True


def release(screen: str) -> None:
    """閉じた。**自分が持っているときだけ**手放す。

    条件を付けるのは、取り上げられた古い画面が閉じるときに、
    取り上げた新しい画面の符牒まで消してしまわないため。
    """
    global _claim
    screen = (screen or "").strip()
    with _lock:
        if _claim is not None and _claim.screen == screen:
            _claim = None
            log.info("画面を手放しました: %s", screen)


def owns(screen: str) -> bool:
    """この画面が、いま使ってよい画面か。**誰も居なければ通す。**

    誰も居ないときに通すのは、画面を持たない使い方
    (`--no-browser` で立てておく、試験から叩く)を巻き添えにしない
    ため。塞ぎたいのは「2枚目が入力できてしまう」であって、
    「1枚も開いていない」ではない。
    """
    live = holder()
    if live is None:
        return True
    return live.screen == (screen or "").strip()


def _new(screen: str) -> Claim:
    now = time.monotonic()
    return Claim(screen, now, now)


def reset() -> None:
    """試験用。誰も居ない状態へ戻す。"""
    global _claim
    with _lock:
        _claim = None
