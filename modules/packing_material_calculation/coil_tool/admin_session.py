"""マスタを直せる状態かどうか ── プロセスに1つ

パスワードを通したことを覚えておく場所です。**1サーバ = 1セッション**で、
複数のタブは同じ状態を共有します(このツールは1台の端末を1人が使う
前提で、他の作業状態も同じ持ち方にしてあります)。

保存はしません。アプリを開き直せば掛け直しです。

【放っておくと自分で閉まります】
現場の端末は誰でも触れます。1度通したらアプリを閉じるまで開いたままだと、
席を離れたあいだに共有のマスタを書き換えられます。
最後に使ってから `config.ADMIN_SESSION_IDLE_SEC` が過ぎると閉まります。

**「使った」に数えるのは、直せるかどうかを実際に確かめた時**です
(`is_open()` が呼ばれた時)。画面を開いているだけでは伸びません ──
伸ばしてしまうと、タブを開きっぱなしにするだけで永久に開いたままに
なり、時間切れを置いた意味がなくなります。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import admin_password, config
from .logging_utils import get_logger

log = get_logger("admin_session")

_lock = threading.Lock()
# 最後に使った時刻(`time.monotonic`)。None なら閉まっている
_opened_at: Optional[float] = None


@dataclass(frozen=True)
class State:
    """画面に出す状態。**パスワードそのものは出しません。**"""
    open: bool = False
    # 閉まるまでの残り秒。閉まっているときは 0
    remains: int = 0
    # この端末でパスワードを変えてあるか(値は出さない)
    custom: bool = False


def open_with(password: str) -> bool:
    """パスワードを確かめて、合っていれば開ける。"""
    global _opened_at
    if not admin_password.verify(password):
        # **何が違うのかは言わない。** 総当たりの手がかりを与えない
        log.warning("マスタ編集の認証に失敗しました")
        return False
    with _lock:
        _opened_at = time.monotonic()
    log.info("マスタ編集の認証を通しました")
    return True


def close() -> None:
    """閉める。席を離れるときに押す。"""
    global _opened_at
    with _lock:
        was = _opened_at is not None
        _opened_at = None
    if was:
        log.info("マスタ編集の認証を閉じました")


def is_open() -> bool:
    """いま直せるか。**確かめるたびに時間切れを数え直します。**"""
    global _opened_at
    with _lock:
        if _opened_at is None:
            return False
        now = time.monotonic()
        if now - _opened_at > config.ADMIN_SESSION_IDLE_SEC:
            _opened_at = None
            log.info("マスタ編集の認証が時間切れで閉じました")
            return False
        # 使ったので数え直す
        _opened_at = now
        return True


def peek() -> bool:
    """開いているかを**見るだけ**。時間切れは数えるが、延ばさない。

    画面の表示用。`is_open()` を表示のたびに呼ぶと、開いている画面を
    眺めているだけで時間切れが来なくなります。
    """
    global _opened_at
    with _lock:
        if _opened_at is None:
            return False
        if time.monotonic() - _opened_at > config.ADMIN_SESSION_IDLE_SEC:
            _opened_at = None
            return False
        return True


def state() -> State:
    """画面に出す状態一式。"""
    with _lock:
        at = _opened_at
        now = time.monotonic()
    if at is None:
        return State(open=False, remains=0, custom=admin_password.is_custom())
    left = config.ADMIN_SESSION_IDLE_SEC - (now - at)
    if left <= 0:
        close()
        return State(open=False, remains=0, custom=admin_password.is_custom())
    return State(open=True, remains=int(left), custom=admin_password.is_custom())


def reset() -> None:
    """テスト用。"""
    close()
