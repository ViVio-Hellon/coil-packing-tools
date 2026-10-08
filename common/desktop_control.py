"""デスクトップ版を外から止める・起動完了を外から確かめる(ランチャー連携。統合 1.2.4)

デスクトップ版はポートを持たない(窓と Python は標準入出力でつながる)。ブラウザ版のように
`/api/health` や `/api/shutdown` を外から呼べないので、**このPCのローカル領域のファイル**で
やりとりする:

    runtime/desktop-state.json   デスクトップ版の Python が1秒ごとに書く
                                 {"app_id", "pid", "ready", "stage", "time"}
    runtime/stop-request.json    止めてほしい側が置く {"id", "force"}
    runtime/stop-result.json     デスクトップ版が答える {"id", "stopped", "running", "message"}

止め方は統合画面の「終了」と同じ(取り込みなどの最中は、`force` が無ければ止めない)。
ただし「終了します。よろしいですか」の確かめは出さない(止めるよう頼んだ側が決めている)。
止めると決めたら外枠(exe)へ「終了してよい」を知らせ、exe も Python も終わる。

**置き場所は Python どうしで同じに見える。** Microsoft Store の Python はローカル領域の
ファイルを自分だけの場所に置き換えるが、頼む側(`process_manager.py`)も同じ Python なので
同じファイルを見る(exe の錠 `desktop.lock` を使わないのはそのため)。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import app_config
from .logging_utils import get_logger

log = get_logger("coil_packing_tools", "desktop_control")

STATE_NAME = "desktop-state.json"
REQUEST_NAME = "stop-request.json"
RESULT_NAME = "stop-result.json"

#: 見張りの間隔(秒)
TICK_SEC = 1.0
#: これより古い状態は、デスクトップ版が答えていないとみなす(秒)
STATE_FRESH_SEC = 5.0


def _path(name: str) -> Path:
    return app_config.local_dir("runtime") / name


def _write_json(path: Path, data: dict) -> None:
    """書きかけを読まれないよう、別名で書いてから置き換える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# ------------------------------------------------------------------
# デスクトップ版の Python の側
# ------------------------------------------------------------------
class Watch:
    """状態を書き、止める頼みに答える(デスクトップ版の Python の中で1つ)。"""

    def __init__(self, *, app_id: str, state: Callable[[], dict],
                 busy: Callable[[], list], stop: Callable[[], None],
                 tick_sec: float = TICK_SEC) -> None:
        self.app_id = app_id
        self._state = state
        self._busy = busy
        self._stop = stop
        self.tick_sec = tick_sec
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 起動より前に置かれた頼み(前の回の残り)には答えない
        _remove(_path(REQUEST_NAME))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="desktop-control", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._halt.set()
        _remove(_path(STATE_NAME))

    def tick(self) -> None:
        """1回ぶん(試験からも呼ぶ)。"""
        try:
            st = self._state() or {}
        except Exception:                             # noqa: BLE001 - 状態が取れなくても止まらない
            st = {}
        try:
            _write_json(_path(STATE_NAME), {
                "app_id": self.app_id, "pid": os.getpid(),
                "ready": bool(st.get("ready")), "stage": str(st.get("stage") or ""),
                "time": time.time()})
        except OSError as exc:
            log.debug("状態を書けませんでした: %s", exc)
        request = _read_json(_path(REQUEST_NAME))
        if request is None:
            return
        _remove(_path(REQUEST_NAME))
        self._answer(request)

    def _answer(self, request: dict) -> None:
        force = bool(request.get("force"))
        try:
            running = list(self._busy() or [])
        except Exception:                             # noqa: BLE001 - 判定できなければ止めない側
            running = ["(確かめられません)"]
        stopped = force or not running
        message = ("終了します" if stopped
                   else "実行中の処理があります: " + ", ".join(running))
        try:
            _write_json(_path(RESULT_NAME), {
                "id": str(request.get("id", "")), "stopped": stopped,
                "running": running, "message": message})
        except OSError as exc:
            log.warning("止める頼みに答えを書けませんでした: %s", exc)
        if stopped:
            log.info("外から止めるよう頼まれたので終了します%s", "(中断してでも)" if force else "")
            self._halt.set()
            _remove(_path(STATE_NAME))
            self._stop()
        else:
            log.info("外から止めるよう頼まれましたが、実行中の処理があるので止めません: %s",
                     ", ".join(running))

    def _loop(self) -> None:
        while not self._halt.is_set():
            self.tick()
            self._halt.wait(self.tick_sec)


# ------------------------------------------------------------------
# 頼む側(process_manager.py)
# ------------------------------------------------------------------
def read_state(*, fresh_sec: float = STATE_FRESH_SEC) -> Optional[dict]:
    """デスクトップ版の状態。書かれていない・古いときは None。"""
    state = _read_json(_path(STATE_NAME))
    if state is None:
        return None
    try:
        age = time.time() - float(state.get("time", 0))
    except (TypeError, ValueError):
        return None
    return state if age <= fresh_sec else None


def request_stop(*, force: bool = False, answer_sec: float = 10.0) -> Optional[dict]:
    """止めるよう頼み、答えを待つ。答えが無ければ None。"""
    ident = secrets.token_hex(8)
    _remove(_path(RESULT_NAME))
    _write_json(_path(REQUEST_NAME), {"id": ident, "force": force, "time": time.time()})
    end = time.monotonic() + answer_sec
    while time.monotonic() < end:
        result = _read_json(_path(RESULT_NAME))
        if result is not None and result.get("id") == ident:
            _remove(_path(RESULT_NAME))
            return result
        time.sleep(0.2)
    _remove(_path(REQUEST_NAME))
    return None
