"""画面が居なくなったら終わる (基盤仕様書 2.8「自動終了」)

【統合版で採ったもの】
梱包明細と資材計算の両方に同名のモジュールがあった。中身はほぼ同じで、
違いは「PCのスリープから戻ったこと」の見つけ方だけ:

    資材計算 … 見張りの刻み(単調時計)が `SUSPEND_SEC` 以上飛んだら
    梱包明細 … 壁時計と単調時計の**両方**で測り、どちらかが飛んだら。
              戻ったことを外へ知らせる口(`on_wake`)も持つ

単調時計は OS によってスリープ中に進んだり進まなかったりする(Linux は
進まない、Windows は進むことがある)ので、両方で測る梱包明細のほうを採る。
資材計算の呼び名(`_notice_suspend` / `SUSPEND_SEC`)は別名で残してある。

**見張りはプロセスに1つ。** 3機能の画面はどれも同じ見張りへ心拍を送る
(統合画面の外枠も送る)。1つでも前に出ている画面があれば終わらない。
ペナラベルは移植元に自動終了が無かったが、同じプロセスに同居するので
統合版ではこの見張りに従う(タブを全部閉じれば終わる)。

【なぜ要るのか】
このアプリに窓はありません。見えているのはブラウザのタブだけなので、
**タブを閉じたら終わったつもりになります。** ところが Python は動いた
ままで、次に起動すると多重起動の判定が「すでに起動しています」と答え、
古いプロセスのブラウザが開きます ── 入れ替えたはずの新しい版が、
いつまでも動きません。

【どう決めるか】
画面が一定の間隔で心拍(`POST /api/alive`)を送ります。**途切れたら
誰も見ていない**と判断して終わります。タブを閉じたことは
`sendBeacon` で即座に伝わるので、たいていは待たずに終わります。

【間違って落とさないための3つ】
1. **猶予を置く。** 画面の作り直し(再読込・画面遷移)でも心拍は
   一瞬途切れます。閉じた合図が来ても `GRACE_SEC` は待ち、そのあいだに
   心拍が戻れば取り消します
2. **処理中は落とさない。** 取り込みの途中で終わると、DBが中途半端な
   状態で残ります(`/api/shutdown` と同じ判断を使う)
3. **1度も繋がっていなければ落とさない。** `--no-browser` で立てて
   おく使い方(検証・並行運用)を巻き添えにしません
4. **裏に回った画面の心拍は待たない。** ブラウザは裏に回ったタブの
   タイマーを間引く(Chrome は5分を過ぎると1分に1回、Edge のスリープ
   タブは完全に止める)。画面は裏に回るとき合図(`hidden`)を送るので、
   前に戻る合図が来るまで「心拍が無い」ことを理由に落とさない。
   **閉じた合図(`leaving`)は裏でも効く** ── 裏のまま閉じても終わる
5. **スリープから戻った直後に落とさない。** 止まっていた時間をまとめて
   「心拍なし」と数えると、画面の心拍が届く前に終わってしまう(実測)。
   見張りの刻みが大きく飛んだら、スリープから戻ったとみなして数え直す

【裏のまま二度と戻らなかったら】
裏に回ったままブラウザが落ちると、閉じた合図が来ないので終わらない。
それでも困らない: 次に `Start.vbs` を押すと、多重起動の判定が生きている
このプロセスを見つけて、そのままブラウザを開く(`launch_guard`)。
止めたいときは `stop.bat`。**裏で誤って終わるより、ずっと害が小さい。**
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from .logging_utils import get_logger

log = get_logger("coil_packing_tools", "idle_exit")

# 心拍が途切れてから終わるまで(秒)。画面は `HEARTBEAT_MS` ごとに送るので、
# 数回落としても持ちこたえる長さにする
IDLE_SEC = 90.0

# 「閉じました」を受けてから終わるまで(秒)。
# 再読込でも閉じた合図は飛ぶので、戻ってくるぶんを待つ
GRACE_SEC = 8.0

# 画面が心拍を送る間隔(ミリ秒)。画面へ渡す値の出どころはここ1つ
HEARTBEAT_MS = 20_000

# 見張る間隔(秒)
TICK_SEC = 2.0

# 見張りの刻みがこれ以上飛んだら、**スリープ(または長い停止)から戻った**
# とみなす。刻みは2秒なので、15秒も飛ぶのは止まっていたときだけ
WAKE_GAP_SEC = 15.0
# 資材計算での呼び名。値は同じもの
SUSPEND_SEC = WAKE_GAP_SEC


class IdleWatch:
    """画面の生き死にを見て、居なくなったら止める。"""

    def __init__(self, stop: Callable[[], None],
                 busy: Callable[[], bool],
                 *, idle_sec: float = IDLE_SEC,
                 grace_sec: float = GRACE_SEC,
                 tick_sec: float = TICK_SEC,
                 wake_gap_sec: float = WAKE_GAP_SEC,
                 on_wake: Optional[Callable[[float], None]] = None) -> None:
        self._stop = stop
        self._busy = busy
        self.idle_sec = idle_sec
        self.grace_sec = grace_sec
        self.tick_sec = tick_sec
        self.wake_gap_sec = wake_gap_sec
        self._on_wake = on_wake

        self._lock = threading.Lock()
        self._seen: Optional[float] = None     # 最後の心拍。None = まだ1度も
        self._leaving_at: Optional[float] = None
        # 画面が裏に回っているか。裏では心拍が間引かれるので待たない
        self._hidden = False
        # 見張りの前回の刻み(壁時計・単調時計)。飛びを測ってスリープを知る
        self._last_wall: Optional[float] = None
        self._last_mono: Optional[float] = None
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- 画面から ------------------------------------------------------
    def beat(self, *, hidden: Optional[bool] = None,
             keep_leaving: bool = False) -> None:
        """画面が生きている。**閉じた合図も取り消す**(再読込のとき)。

        `hidden` は画面が今どちらに居るか(裏 True / 前 False)。None なら
        変えない。

        `keep_leaving` なら閉じた合図を取り消さない。**「裏に回った」と
        いう合図は、閉じた合図を取り消してはいけない。** タブを閉じると
        ブラウザは「裏に回った」と「閉じた」を両方送り、届く順は決まって
        いない。後から来た「裏に回った」で取り消すと、閉じたのに終わらない
        (試験で見つかった)。
        """
        with self._lock:
            self._seen = time.monotonic()
            if not keep_leaving:
                self._leaving_at = None
            if hidden is not None and hidden != self._hidden:
                self._hidden = hidden
                log.info("画面が%sに回りました%s", "裏" if hidden else "前",
                         "(心拍が止まっても終了しません)" if hidden else "")

    @property
    def hidden(self) -> bool:
        with self._lock:
            return self._hidden

    def leaving(self) -> None:
        """画面が閉じた(`sendBeacon`)。猶予のあとで落とす。

        裏に回っていたかどうかに関わらず効く(裏のまま閉じることもある)。
        """
        with self._lock:
            if self._seen is None:
                return                          # 1度も繋がっていない
            self._leaving_at = time.monotonic()
        log.info("画面が閉じました。%.0f秒 待って終了します", self.grace_sec)

    # -- 見張り --------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="idle-watch",
                                        daemon=True)
        self._thread.start()
        log.info("自動終了の見張りを始めました(無通信 %.0f秒 / 閉じたら %.0f秒)",
                 self.idle_sec, self.grace_sec)

    def cancel(self) -> None:
        self._done.set()

    def _loop(self) -> None:
        while not self._done.wait(self.tick_sec):
            # **先にスリープを疑う。** 止まっていた時間を「心拍なし」と
            # 数えてから気づいても遅い
            self.check_wake()
            why = self.overdue()
            if why is None:
                continue
            if self._busy():
                # **処理中は落とさない。** 取り込みの途中で終わると、
                # DBが中途半端な状態で残る。終わればまた見に来る
                log.info("誰も見ていませんが、処理中なので待ちます")
                continue
            log.info("誰も見ていないので終了します(%s)", why)
            self._done.set()
            self._stop()
            return

    def check_wake(self, *, wall: Optional[float] = None,
                   mono: Optional[float] = None) -> float:
        """見張りの刻みが飛んでいたら、スリープから戻ったとみなす。

        飛んだ秒数を返す(飛んでいなければ 0)。

        **壁時計と単調時計の両方で測る。** 単調時計は OS によって
        スリープ中に進んだり進まなかったりする(Linux は進まない、
        Windows は進むことがある)。壁時計は必ず進む。どちらかが大きく
        飛べば止まっていた。時計を手で進めたときも飛んで見えるが、
        心拍を待つ時間が1回ぶん延びるだけで害は無い。
        """
        wall = time.time() if wall is None else wall
        mono = time.monotonic() if mono is None else mono
        with self._lock:
            last_wall, last_mono = self._last_wall, self._last_mono
            self._last_wall, self._last_mono = wall, mono
            if last_wall is None or last_mono is None:
                return 0.0
            gap = max(wall - last_wall, mono - last_mono) - self.tick_sec
            if gap < self.wake_gap_sec:
                return 0.0
            # 止まっていた時間は数えない。**ここから数え直す**
            if self._seen is not None:
                self._seen = mono
            self._leaving_at = None
        log.info("スリープ(または長い停止)から戻りました(約%.0f秒)。"
                 "心拍を待つ時間を数え直します", gap)
        if self._on_wake is not None:
            try:
                self._on_wake(gap)
            except Exception:                   # noqa: BLE001 - 見張りは止めない
                log.exception("スリープからの戻りの後始末に失敗しました")
        return gap

    # 資材計算での呼び名(試験が使う)。中身は `check_wake`
    def _notice_suspend(self) -> float:
        return self.check_wake()

    def overdue(self) -> Optional[str]:
        """終わってよいか。よければ理由、まだなら `None`。"""
        with self._lock:
            seen, leaving, hidden = self._seen, self._leaving_at, self._hidden
        if seen is None:
            # 1度も繋がっていない。`--no-browser` で立てておく使い方を
            # 巻き添えにしない
            return None
        now = time.monotonic()
        # **閉じた合図は裏でも効く。** 裏のままタブを閉じても終わる
        if leaving is not None and now - leaving >= self.grace_sec:
            return "画面が閉じられました"
        if hidden:
            # 裏に回っている。心拍が止まっているのはブラウザの間引きで、
            # 画面は閉じられていない。前に戻る合図を待つ
            return None
        if now - seen >= self.idle_sec:
            return f"{self.idle_sec:.0f}秒 心拍がありません"
        return None


# ------------------------------------------------------------------
# プロセスに1つ
# ------------------------------------------------------------------
_watch: Optional[IdleWatch] = None
_lock = threading.Lock()


def install(stop: Callable[[], None], busy: Callable[[], bool],
            **kwargs) -> IdleWatch:
    """見張りを1つ立てる。2度呼んでも1つ。"""
    global _watch
    with _lock:
        if _watch is None:
            _watch = IdleWatch(stop, busy, **kwargs)
            _watch.start()
        return _watch


def get() -> Optional[IdleWatch]:
    return _watch


def reset() -> None:
    """テスト用。"""
    global _watch
    with _lock:
        if _watch is not None:
            _watch.cancel()
        _watch = None
