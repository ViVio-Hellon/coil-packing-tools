"""外から止める前に、開いている統合画面へ「閉じる前の頼み」を出す(統合 1.2.5)

統合画面の「終了」(と窓の ×)は、止める前に3機能の画面へ「まだ保存していない入力」を
訊く(`static/js/shell.js` の `prepareFrames`)。ところが**外から**(ランチャー・`stop.bat`)
`/api/shutdown` を頼まれると、画面を通らずに止めていた ── 開いたままの画面の入力は、
訊かれずに消えた。

外からの頼みは、開いている画面(毎秒たずねに来る画面)に「閉じる前の頼み」を出し、
返事を少しだけ待つ:

    全部の画面が「閉じてよい」(`ok`)       → そのまま止める
    どれかが「閉じない」(`refused`)        → 止めない(409 refused。画面で本人が選んだ)
    まだ確かめている・答えない(`working`)  → 409 asking。あとで `ok` が来たら入口が自分で止まる

画面が1枚も居なければ、すぐ止める(画面を閉じたあと)。

業務ツール統合ツール(ViVio-Hellon/all-tools 1.2.0)の `portal/web.py` の `CloseAsk` と同じ決まり
(ランチャーから見た動きをそろえる)。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

ASKING_MESSAGE = ("開いているコイル梱包ツールの画面に、保存していない入力を確かめてから閉じるよう"
                  "頼みました。確かめが済めば自分で終わります(画面に確認が出ていれば答えてください)")
REFUSED_MESSAGE = "コイル梱包ツールの画面で「閉じない」が選ばれました(保存していない入力があります)"


class CloseAsk:
    """外からの停止の前の頼みと、画面の返事。"""

    PAGE_ALIVE_SEC = 75.0   # この間に声のあった画面は居るとみなす(裏のタブは1分に1回まで間引かれる)
    WAIT_SEC = 3.0          # 停止要求の中で返事を待つ(ランチャーは停止口を 5 秒で見切る)
    EXPIRE_SEC = 120.0      # これより遅い「閉じてよい」では止めない(頼んだ人はもう待っていない)

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._pages: dict[str, float] = {}
        self._left: set[str] = set()     # 閉じた画面(閉じる間際の問い合わせが後から着いても戻さない)
        self._seq = 0
        self._asked_at = 0.0
        self._answers: dict[str, str] = {}
        self._waiting = 0               # いま返事を待っている停止要求の数

    # --- 画面の声 ---
    def seen(self, page: str, now: Optional[float] = None) -> None:
        if not page:
            return
        with self._cond:
            if page[:64] in self._left:
                return
            self._pages[page[:64]] = time.monotonic() if now is None else now

    def gone(self, page: str) -> None:
        if not page:
            return
        with self._cond:
            self._pages.pop(page[:64], None)
            self._answers.pop(page[:64], None)
            if len(self._left) > 200:
                self._left.clear()
            self._left.add(page[:64])
            self._cond.notify_all()

    def live(self, now: Optional[float] = None) -> list[str]:
        now = time.monotonic() if now is None else now
        with self._cond:
            return [p for p, at in self._pages.items() if now - at <= self.PAGE_ALIVE_SEC]

    # --- 頼む・答える ---
    def pending(self, page: str) -> int:
        """その画面へ出ている頼み(番号)。もう答えた・期限切れなら 0。"""
        with self._cond:
            if not self._seq or not self._asked_at \
                    or time.monotonic() - self._asked_at > self.EXPIRE_SEC:
                return 0
            return 0 if self._answers.get(page[:64]) in ("ok", "refused") else self._seq

    def ask(self) -> int:
        """頼みを出す。まだ期限内の頼みがあれば、それを使い回す(続けて頼まれても1つ)。

        「閉じない」と答えた画面があれば新しく頼む ── 本人は入力を片付けてから、
        もう一度止めにくる。同じ頼みのままだと、前の「閉じない」で断り続ける。
        """
        with self._cond:
            now = time.monotonic()
            refused = any(v == "refused" for v in self._answers.values())
            if not self._asked_at or refused or now - self._asked_at > self.EXPIRE_SEC:
                self._seq += 1
                self._asked_at = now
                self._answers = {}
            return self._seq

    def answer(self, page: str, seq: int, state: str) -> bool:
        """画面の返事。その頼みがまだ生きていれば真。"""
        with self._cond:
            if not self._asked_at or seq != self._seq \
                    or time.monotonic() - self._asked_at > self.EXPIRE_SEC:
                return False
            self._answers[page[:64]] = state
            self._cond.notify_all()
            return True

    def verdict(self, pages: list[str]) -> str:
        """`ok` / `refused` / `working`。"""
        with self._cond:
            return self._verdict(pages)

    def _verdict(self, pages: list[str]) -> str:
        states = [self._answers.get(p, "") for p in pages if p in self._pages]
        if any(s == "refused" for s in states):
            return "refused"
        return "ok" if all(s == "ok" for s in states) else "working"

    def wait(self, pages: list[str], timeout: float) -> str:
        deadline = time.monotonic() + timeout
        with self._cond:
            self._waiting += 1
            try:
                while True:
                    verdict = self._verdict(pages)
                    left = deadline - time.monotonic()
                    if verdict != "working" or left <= 0:
                        return verdict
                    self._cond.wait(left)
            finally:
                self._waiting -= 1

    def someone_waiting(self) -> bool:
        with self._cond:
            return self._waiting > 0

    def done(self) -> None:
        """止めると決めた(同じ頼みで2度止めない)。"""
        with self._cond:
            self._seq += 1
            self._asked_at = 0.0
            self._answers = {}
