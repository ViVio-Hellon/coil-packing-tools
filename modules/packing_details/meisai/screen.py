"""画面は1つだけ (同じアプリを2枚開かせない)

【なぜ要るか】
`session.py` の作業状態は**プロセスに1つ**しかない。VBA の
`frmCoilPacking` が1枚しか開かなかったのと同じ持ち方で、そこは
変えていない。ところが Web版はブラウザのタブを何枚でも開けるので、
2枚目を開くと**同じ盤面を2つの画面が奪い合う。**

実際に確かめるとこうなる。

    タブA でロット L7110P0 を開く
    タブB でロット L5160Z0 を開く
    タブA で1回操作する
      → **タブAの画面が L5160Z0 に化ける**

自分が開いたはずのロットが、触った瞬間に別のロットへ変わる。
現物と紙が食い違う事故になるので、**2枚目は開かせない。**

【プロセスの多重起動とは別の話】
`launch_guard` が見ているのは「Pythonが2つ走っていないか」で、
これはこれで必要だが、**タブを2枚開くことは止められない**
(プロセスは1つのままなので)。別の仕掛けが要る。

【どこで見分けるか】
**サーバだけでは再読込と2枚目を区別できない。** ブラウザは再読込の
とき、古いページを畳む前に次の要求を送るからで、器(HTML)を出す
ところで断ると F5 のたびに断ることになる(実際そうなった)。

見分けはブラウザ側で行い(`app/static/js/screen.js`)、サーバは
**名乗り**を受けて断る。

    sessionStorage      タブごとに違い、再読込では消えない名前。
                        同じ名前の名乗り直しは通す＝F5 は素通り
    BroadcastChannel    いま他のタブが居ないかを直接きく。タブの複製は
                        名前ごと写されるので、名前だけでは見分けられない

**器は誰にでも返してよい。** 業務データは名乗ってからでないと触れない。

【引き継ぎは残す】
断るだけだと、タブが落ちた・強制終了したときに誰も入れなくなる。

- 心拍が `GRACE_SEC` 途切れたら、その画面は居ないものとして扱う
  ── **ただし「裏に回った」と言ってきた画面は除く**(下記)

【裏に回った画面は、心拍が止まっても居るものとして扱う】
ブラウザは裏に回ったタブのタイマーを間引く。Chrome は5分を過ぎると
**1分に1回**まで、Edge のスリープタブは**完全に止める**。すると心拍が
`GRACE_SEC` を超えて途切れ、戻ってきた持ち主が自分の画面から締め出さ
れていた(実測: 他のタブが無いのに `409 screen_taken`)。

- 画面は裏に回るとき合図を送る(`hidden`)。その間は空きとみなさない
- **持ち主の心拍は、どれだけ遅れても受ける。** 断ってよいのは、その
  あいだに**別の画面が取った**ときだけ。遅れたこと自体は締め出す理由に
  ならない
- スリープから戻ったら(`note_wake`)、止まっていた時間を数えない
- 明示的に引き継げる口も置く(同じ端末・同じ人しか触れないので、
  奪い合いにはならない。**移すだけで、2枚にはしない**)

引き継がれた側は、次に操作した時点で断られる ── 画面に出ている値は
もう嘘なので、そのまま触らせてはいけない。
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .logging_utils import get_logger

log = get_logger("screen")

# 心拍がこれだけ途切れたら、その画面は居ないものとして扱う。
#
# 画面の心拍は20秒ごと。**1回の取りこぼしでは奪わない** ── 通信が
# 一瞬詰まっただけで別のタブに持っていかれると、作業中の画面が
# 突然使えなくなる。2回ぶん待つ。
GRACE_SEC = 50.0

_lock = threading.RLock()
_holder: Optional[str] = None
_last_seen: float = 0.0
# 持ち主の画面が裏に回っているか(タブ切替・最小化・画面ロック)。
# 裏では心拍が間引かれるので、途切れても空きとみなさない
_hidden: bool = False


@dataclass(frozen=True)
class Holder:
    """いま画面を持っているもの。持ち主が居なければ `screen_id` は空。"""

    screen_id: str
    last_seen: float

    @property
    def idle_sec(self) -> float:
        return max(0.0, time.time() - self.last_seen)


def new_id() -> str:
    """画面1枚ぶんの名前。**タブごとに違う値**になる。"""
    return secrets.token_urlsafe(12)


def _alive(now: float) -> bool:
    """持ち主が居るか。呼ぶ側で必ず `_lock` を取っていること。

    **裏に回っている持ち主は居るものとする。** 心拍が止まっているのは
    ブラウザが間引いているからで、画面は閉じられていない。
    """
    if not _holder:
        return False
    return _hidden or (now - _last_seen) <= GRACE_SEC


def holder() -> Optional[Holder]:
    """いまの持ち主。居ない(または心拍が途切れた)なら None。"""
    with _lock:
        if not _alive(time.time()):
            return None
        return Holder(_holder or "", _last_seen)


def claim(screen_id: str) -> bool:
    """画面を取る。**取れたかどうか**を返す。

    すでに生きている別の画面が持っていれば取れない(False)。

    **同じ名前なら取り直せる。** 再読込(F5)がこれにあたる ── タブの
    名前は `sessionStorage` に残るので、同じ名前で名乗り直してくる。
    ここで断ると、F5 のたびに「すでに開いています」になる。
    """
    global _holder, _last_seen
    with _lock:
        now = time.time()
        if _alive(now) and _holder != screen_id:
            return False
        if _holder != screen_id:
            log.info("画面を開いた: %s", screen_id)
        _holder, _last_seen = screen_id, now
        _set_hidden(False)          # 開いた(名乗った)ところは前に出ている
        return True


def hand_over() -> str:
    """前の画面に手放させる。**2枚にするのではなく、移すための1段目。**

    ここでは空けるだけで、新しい持ち主は決めない ── 決めてしまうと、
    そのあと画面を開き直したときに**自分自身に断られる**(開き直しは
    新しい名前で来るため)。空けておけば、次に開いた画面が普通に取る。

    手放させた画面の名前を返す(誰も居なければ空文字)。
    """
    global _holder, _last_seen
    with _lock:
        old = _holder or ""
        if old:
            log.info("画面を手放させた: %s", old)
        _holder, _last_seen = None, 0.0
        _set_hidden(False)
        return old


def beat(screen_id: str, *, hidden: Optional[bool] = None) -> bool:
    """心拍。持ち主なら更新して True、そうでなければ False。

    **持ち主の心拍は、どれだけ遅れても受ける。** 以前は猶予を過ぎた
    心拍を断っていたので、裏に回ってタイマーを間引かれた画面が、前に
    戻った瞬間に自分の画面から締め出されていた(他のタブが無いのに)。
    断ってよいのは、そのあいだに**別の画面が取った・手放させた**とき
    だけ ── そのときは `_holder` が変わっている。

    **持っていない画面の心拍で延命しない。** 延命すると、引き継がれた
    はずの古いタブが裏で生き続け、いつまでも猶予が切れなくなる。

    `hidden` は画面が今どちらに居るか(裏 True / 前 False)。None なら
    変えない ── 業務の要求は裏表を名乗らないので、ここで決めつけない。
    """
    global _last_seen
    with _lock:
        if not _holder or _holder != screen_id:
            return False
        _last_seen = time.time()
        if hidden is not None:
            _set_hidden(hidden)
        return True


def _set_hidden(value: bool) -> None:
    """呼ぶ側で必ず `_lock` を取っていること。"""
    global _hidden
    if _hidden != value:
        log.info("画面が%sに回った", "裏" if value else "前")
    _hidden = value


def is_hidden() -> bool:
    """持ち主の画面が裏に回っているか。"""
    with _lock:
        return bool(_holder) and _hidden


def note_wake(gap_sec: float = 0.0) -> None:
    """スリープから戻った。**止まっていた時間を数えない。**

    戻った直後は、画面の心拍より先に「別のタブが名乗る」ことがありうる。
    止まっていた時間を空きの猶予に数えると、持ち主がまだ戻っていない
    だけなのに空きとみなされる。猶予を数え直す。
    """
    global _last_seen
    with _lock:
        if _holder:
            _last_seen = time.time()
            log.info("スリープから戻ったので空きの猶予を数え直す(約%.0f秒止まっていた)",
                     gap_sec)


def release(screen_id: str) -> None:
    """タブを閉じた。**すぐ空ける** ── 開き直すのに猶予を待たせない。"""
    global _holder, _last_seen
    with _lock:
        if _holder == screen_id:
            log.info("画面を閉じた: %s", screen_id)
            _holder, _last_seen = None, 0.0
            _set_hidden(False)


def reset() -> None:
    """試験用。持ち主を消す。"""
    global _holder, _last_seen, _hidden
    with _lock:
        _holder, _last_seen, _hidden = None, 0.0, False
