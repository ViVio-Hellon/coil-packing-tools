# -*- coding: utf-8 -*-
"""入力画面を 1 つに限る（画面の多重起動の防止）。

プロセスの二重起動（``launch_guard``）とは**別の問題**。
こちらはプロセスが 1 つでも起きる。

    作業状態（検査番号・重量・本数・計算結果）は
    **サーバー側に 1 組しか無い**（状態DB の ``form_state``）。

そのためブラウザーのタブを 2 枚開くと、両方が同じ 1 組を書き換える。

    タブA: 検番 A111111 / 11本 で計算  → 画面には A111111
    タブB: 検番 B999999 / 20本 で計算  → サーバーの状態は B999999
    タブA で印刷                       → **B999999 の内容が出る**

画面には A が出ているのに B が刷られる。コイルに別の検査番号のラベルが
貼られるということなので、**開くこと自体を断る**。

対象は入力できる画面だけ
    指定サイズ `/` ・全サイズ `/all-size` ・設定 `/settings`

表示と印刷だけの画面（`/tare` `/list` `/labels` `/breakdown` `*/print`）は
状態を書き換えないので、何枚開いてもよい。実際、印刷ビューは
別タブで開く作りになっている。

閉じ忘れ対策
    画面は数秒ごとに生存を知らせる。``TTL_SEC`` の間それが途切れたら
    「閉じられた」とみなして次の画面に譲る。ブラウザーが落ちても
    数秒で次が使えるようにするための仕組み。

裏に回った画面（バックグラウンド）
    ブラウザーは **裏のタブのタイマーを間引く**（Chrome / Edge は 5 分ほど
    裏にあると 1 分に 1 回まで。眠ったタブ・PC のスリープでは止まる）。
    生存通知だけで判断すると、**裏に回しただけの作業中の画面を
    「閉じられた」と誤判定**し、次に開いたタブへ使用権を渡してしまう。
    戻ってきた作業中の画面は「使用中ではなくなりました」になる。

    そこで画面は裏に回る瞬間に **「裏に回る」と知らせる**（``set_visibility``）。
    知らせを受けた画面は ``BACKGROUND_TTL_SEC``（12 時間）の間、生存通知が
    途切れても**空きとみなさない**。表に戻れば通常の ``TTL_SEC`` に戻る。
    知らせが届かなかった場合に備え、生存通知にも表か裏かを載せる。

    裏に回ったまま本当に閉じられた（ブラウザーが落ちた）場合は、
    次の画面に「裏に回っている画面がある」と出し、「この画面で使う」で移せる。

    **サーバーのプロセスは生存通知が途切れても止まらない**
    （止めるのは stop.bat / 終了の操作だけ。自動で止める仕組みは持たない）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

#: 生存通知が途切れてから、その画面を手放したとみなすまでの秒数
TTL_SEC = 20.0

#: 「裏に回る」と知らせた画面を、生存通知なしで持たせておく秒数。
#: 1 勤務（〜12 時間）裏に置いても空き扱いにしない。
BACKGROUND_TTL_SEC = 12 * 3600.0


class ScreenGuard:
    """入力画面の使用権。**同時に持てるのは 1 つだけ**。"""

    def __init__(self, ttl_sec: float = TTL_SEC,
                 background_ttl_sec: float = BACKGROUND_TTL_SEC,
                 clock: Optional[Callable[[], float]] = None):
        self.ttl_sec = float(ttl_sec)
        self.background_ttl_sec = max(float(background_ttl_sec), self.ttl_sec)
        #: 進むだけの時計（検証では差し替えて何時間も進める）
        self._clock = clock or time.monotonic
        self._id: str = ""
        #: 使用中の画面が裏に回っているか（本人が知らせてきた状態）
        self._hidden: bool = False
        self._hidden_at: float = 0.0        # monotonic
        self._hidden_wall: float = 0.0      # 表示用
        #: 経過時間の判定は **進むだけの時計** で行う。
        #: time.time() は NTP 補正や手動変更で飛ぶため、
        #: 1 時間進んだだけで「誰も使っていない」と誤判定し、
        #: 作業中の画面を別のタブに奪われることがあった。
        self._since: float = 0.0            # monotonic
        self._seen: float = 0.0             # monotonic
        self._since_wall: float = 0.0       # 表示用（人が読む時刻）
        self._lock = threading.Lock()

    # ------------------------------------------------------------
    def _ttl(self) -> float:
        return self.background_ttl_sec if self._hidden else self.ttl_sec

    def _expired(self, now: float) -> bool:
        return (not self._id) or (now - self._seen > self._ttl())

    def _take(self, screen_id: str, now: float) -> None:
        self._id = screen_id
        self._since = now
        self._seen = now
        self._since_wall = time.time()
        self._hidden = False
        self._hidden_at = 0.0
        self._hidden_wall = 0.0

    def _note_visibility(self, visible: Optional[bool], now: float,
                         reason: str = "") -> None:
        """使用中の画面の表・裏を記録する（呼ぶ側でロック済み）。"""
        if visible is None:
            return
        if not visible and not self._hidden:
            self._hidden = True
            self._hidden_at = now
            self._hidden_wall = time.time()
            log.debug("入力画面が裏に回りました: %s (%s)", self._id, reason)
        elif visible and self._hidden:
            away = now - self._hidden_at
            self._hidden = False
            # タブを切り替えるたびには残さない。通常の期限を超えて
            # 裏にあった（＝以前なら空き扱いにされていた）ときだけ残す。
            if away > self.ttl_sec:
                log.info("入力画面が表に戻りました: %s（裏にあった時間 %.0f 秒, %s）",
                         self._id, away, reason or "-")

    # ------------------------------------------------------------
    def claim(self, screen_id: str, force: bool = False,
              visible: Optional[bool] = None) -> dict:
        """使用権を求める。

        ``force`` は利用者が「この画面で使う」を押した場合。
        前の画面は次の生存通知で弾かれ、操作できなくなる。
        ``visible`` はその画面が表に出ているか（分からなければ None）。
        """
        screen_id = (screen_id or "").strip()
        if not screen_id:
            return {"granted": False, "reason": "画面IDがありません"}

        now = self._clock()
        with self._lock:
            if self._id == screen_id:
                self._seen = now
                self._note_visibility(visible, now, "claim")
                return self._granted(now)

            if self._expired(now) or force:
                prev = self._id
                self._take(screen_id, now)
                self._note_visibility(visible, now, "claim")
                if prev and prev != screen_id:
                    log.info("入力画面を切り替えました: %s -> %s", prev, screen_id)
                return self._granted(now)

            out = {
                "granted": False,
                "reason": "別の画面で開いています",
                "heldSince": time.strftime("%H:%M:%S",
                                           time.localtime(self._since_wall)),
                "idleSec": round(now - self._seen, 1),
                "holderHidden": self._hidden,
            }
            if self._hidden:
                out["hiddenSince"] = time.strftime(
                    "%H:%M:%S", time.localtime(self._hidden_wall))
            return out

    def heartbeat(self, screen_id: str) -> bool:
        """生存通知。使用権を持っていれば True。"""
        return self.heartbeat_info(screen_id)["granted"]

    def set_visibility(self, screen_id: str, visible: bool,
                       reason: str = "") -> dict:
        """画面が裏に回る・表に戻るときの知らせ。

        裏に回る瞬間は ``sendBeacon`` で送られてくる（タブを閉じる途中や
        凍結の直前でも届く）。使用中の画面からの知らせだけを受け付ける。
        """
        now = self._clock()
        with self._lock:
            mine = (screen_id or "").strip()
            if not self._id or self._id != mine:
                return {"granted": False,
                        "heldByOther": bool(self._id) and not self._expired(now)}
            self._seen = now
            self._note_visibility(bool(visible), now, reason)
            return {"granted": True, "heldByOther": False,
                    "hidden": self._hidden}

    def heartbeat_info(self, screen_id: str,
                       visible: Optional[bool] = None) -> dict:
        """生存通知の詳細。

        使用権を失っている理由を分けて返す。

            heldByOther=True  … 別の画面が「この画面で使う」を押した。
                                こちらは操作できない旨を出す。
            heldByOther=False … **誰も使っていない**。
                                放置で期限切れになっただけなので、
                                黙って取り直せばよい（警告を出さない）。
        """
        now = self._clock()
        with self._lock:
            mine = (screen_id or "").strip()
            if self._id and self._id == mine:
                self._seen = now
                self._note_visibility(visible, now, "ping")
                return {"granted": True, "heldByOther": False}
            return {"granted": False,
                    "heldByOther": bool(self._id) and not self._expired(now)}

    def release(self, screen_id: str) -> None:
        """画面を閉じたときに手放す。"""
        with self._lock:
            if self._id and self._id == (screen_id or "").strip():
                self._id = ""
                self._since = 0.0
                self._seen = 0.0
                self._hidden = False

    # ------------------------------------------------------------
    def is_active(self, screen_id: str) -> bool:
        """``screen_id`` が現在の入力画面か。"""
        now = self._clock()
        with self._lock:
            if self._expired(now):
                return True             # 誰も使っていないなら誰でもよい
            return self._id == (screen_id or "").strip()

    def has_holder(self) -> bool:
        with self._lock:
            return not self._expired(self._clock())

    def info(self) -> dict:
        """診断画面用。"""
        now = self._clock()
        with self._lock:
            if self._expired(now):
                return {"held": False}
            out = {
                "held": True,
                "screenId": self._id,
                "since": time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime(self._since_wall)),
                "idleSec": round(now - self._seen, 1),
                "ttlSec": self._ttl(),
                "hidden": self._hidden,
            }
            if self._hidden:
                out["hiddenSince"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(self._hidden_wall))
            return out

    # ------------------------------------------------------------
    def _granted(self, now: float) -> dict:
        return {
            "granted": True,
            "screenId": self._id,
            "heldSince": time.strftime("%H:%M:%S",
                                       time.localtime(self._since_wall)),
            "ttlSec": self.ttl_sec,
            "backgroundTtlSec": self.background_ttl_sec,
        }
