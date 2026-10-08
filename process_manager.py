#!/usr/bin/env python3
"""対象プロセスの確認と安全な停止 (基盤仕様書 2.8)

**このアプリだけを止める。** 同じPCで動く別のPythonアプリを巻き添えに
しないことが、このモジュールの唯一の目的。

順序は仕様書のとおり:

    1. まずアプリ自身へ正常終了を要求する (`POST /api/shutdown`)
    2. 応答しないときだけ、記録したPIDを使う
    3. 落とす前に、そのPIDが**本当にこのアプリか**を確かめる
    4. `python.exe` をプロセス名だけで一括終了することはしない

実行中の長時間処理があるときは、既定では止めずに知らせる。
中断してよいかは利用者が決める(`--force` で中断する)。

**開いている統合画面に、保存していない入力を確かめてから止める**(統合 1.2.5)。
入口は画面に「閉じる前の頼み」を出し、確かめ中なら 409 asking を返す(済めば入口が
自分で終わる)ので、ここでは少しだけ終わるのを待つ。

**デスクトップ版(コイル梱包ツール.exe の窓)は止めない。** 窓の × か「終了」で閉じる
(閉じるときに保存していない入力・途中の処理を確かめるため)。動いていれば窓を前に出す。

戻り値(業務ツール統合ツール all-tools の stop.bat と同じ):

    0  止めた(動いていなかった)
    2  止めなかった ── 途中の処理がある・画面で「閉じない」が選ばれた・画面で確かめ中
    1  止められなかった ── デスクトップ版が動いている・応答しない など

使い方:

    python process_manager.py                  統合アプリを止める
    python process_manager.py --mode material  資材モードを止める
    python process_manager.py --all            両方止める
    python process_manager.py --force          実行中の処理を中断してでも止める
    python process_manager.py --status         状態を見るだけ
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT))
# アプリのフォルダに `__pycache__` を作らない(start_app.redirect_pycache と同じ理由。
# 止めるだけの短い処理なので、書き先を向けずに書かないでおく)
sys.dont_write_bytecode = True

from common import app_config, modes  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402

log = get_logger("coil_packing_tools", "process_manager")

# 正常終了を要求したあと、実際に落ちるのを待つ上限(秒)
GRACEFUL_WAIT_SEC = 8.0
# PIDで落としたあと、消えるのを待つ上限(秒)
FORCE_WAIT_SEC = 5.0
# 画面で確かめてもらうのを待つ上限(秒)。ランチャーは stop.bat を 20 秒で見切る
ASKING_WAIT_SEC = 15.0

#: 戻り値
EXIT_STOPPED = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2

DESKTOP_MESSAGE = ("デスクトップ版(コイル梱包ツール.exe の窓)が動いています。"
                   "窓の × か「終了」で閉じてください(stop.bat では止めません)。")


class StopResult:
    """止められたか、どうやって止めたか。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.stopped = False
        self.method = ""
        self.message = ""
        self.busy_jobs: list[str] = []
        # 止めなかった(途中の処理・画面で「閉じない」・画面で確かめ中)。戻り値 2
        self.refused = False

    def __str__(self) -> str:
        mark = "済" if self.stopped else "--"
        return f"[{mark}] {self.mode}: {self.message}"


# ------------------------------------------------------------------
# 状態
# ------------------------------------------------------------------
def status(mode: str) -> Optional[dict]:
    """そのmodeが動いているか。動いていれば `/api/health` の中身。"""
    import launch_guard

    info = launch_guard.read_lock(mode)
    if info is None:
        return None
    health = launch_guard.probe_health(info.port)
    if not launch_guard.is_our_app(health, mode):
        return None
    health["_lock"] = {"pid": info.pid, "port": info.port,
                       "started": info.started_text, "app_root": info.app_root}
    return health


# ------------------------------------------------------------------
# 停止
# ------------------------------------------------------------------
def stop(mode: str, *, force: bool = False) -> StopResult:
    import launch_guard

    result = StopResult(mode)
    info = launch_guard.read_lock(mode)
    if info is None:
        result.stopped = True
        result.method = "none"
        result.message = "起動していません"
        return result

    health = launch_guard.probe_health(info.port)
    if not launch_guard.is_our_app(health, mode):
        # 応答しない、または別のアプリ。ロックだけ残っている状態
        if launch_guard.is_process_alive(info.pid):
            return _stop_by_pid(mode, info, result, force=force)
        launch_guard.remove_lock(mode)
        result.stopped = True
        result.method = "stale-lock"
        result.message = "動いていませんでした(残っていたロックを片付けました)"
        return result

    # --- 1. 正常終了を要求する ---
    asked = _request_shutdown(info.port, info.token, force=force)
    reason = asked.get("reason")
    if reason == "asking":
        # 開いている画面に、保存していない入力を確かめてから閉じるよう頼んだ。
        # 済めば入口が自分で終わる。少しだけ待つ
        print(asked.get("message") or "画面に確かめてもらっています")
        if _wait_gone(info.port, ASKING_WAIT_SEC):
            launch_guard.remove_lock(mode)
            result.stopped = True
            result.method = "graceful"
            result.message = "画面で確かめてもらい、終了しました"
            return result
        result.refused = True
        result.message = "まだ画面で確認中です。画面を見てください(済めば自分で終わります)"
        return result
    if reason == "refused":
        result.refused = True
        result.message = str(asked.get("message") or "画面で「閉じない」が選ばれました")
        return result
    if asked.get("busy"):
        result.refused = True
        result.busy_jobs = asked.get("running", [])
        result.message = (f"実行中の処理があります: {', '.join(result.busy_jobs)}\n"
                          f"    中断して止めるには --force を付けてください")
        return result

    if asked.get("ok") and _wait_gone(info.port, GRACEFUL_WAIT_SEC):
        launch_guard.remove_lock(mode)
        result.stopped = True
        result.method = "graceful"
        result.message = "正常に終了しました"
        return result

    # --- 2. 応答しないときだけPIDを使う ---
    return _stop_by_pid(mode, info, result, force=force)


def _request_shutdown(port: int, token: str, *, force: bool) -> dict:
    """`POST /api/shutdown` で正常終了を要求する(基盤仕様書 2.8 の1段目)。

    トークンはロックファイルから読む。ロックは利用者ごとのローカル領域に
    あるので、読める相手はそもそもプロセスを直接落とせる。
    """
    import launch_guard

    url = f"http://127.0.0.1:{port}/api/shutdown"
    # 入口が画面へ頼んで返事を待つ(3秒)ぶん、待ち時間を長めにとる
    body = json.dumps({"force": force}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Tool-Token"] = token
    try:
        # プロキシを経由しない送信口を使う(`launch_guard` の注釈を参照)。
        # 社内PCのプロキシ設定に 127.0.0.1 の除外が無いと、自分自身への
        # 停止要求までプロキシへ送られて届かない
        with launch_guard.local_request(url, timeout=10, data=body,
                                        method="POST", headers=headers) as res:
            payload = json.loads(res.read().decode("utf-8"))
            return {"ok": bool(payload.get("stopped"))}
    except urllib.error.HTTPError as exc:
        if exc.code == 409:                        # 実行中の処理・画面で確かめ中・「閉じない」
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:                      # noqa: BLE001
                payload = {}
            reason = str(payload.get("reason") or "busy")
            return {"ok": False, "busy": reason == "busy", "reason": reason,
                    "message": payload.get("message", ""),
                    "running": payload.get("running", ["(不明)"])}
        log.info("停止要求は %s で拒否されました。PIDで止めます", exc.code)
        return {"ok": False}
    except (urllib.error.URLError, OSError) as exc:
        log.info("停止要求を送れませんでした (%s)。PIDで止めます", exc)
        return {"ok": False}


def _stop_by_pid(mode: str, info, result: StopResult, *, force: bool) -> StopResult:
    """PIDで止める。**落とす前に本当にこのアプリかを確かめる。**"""
    import launch_guard

    if not launch_guard.is_process_alive(info.pid):
        launch_guard.remove_lock(mode)
        result.stopped = True
        result.method = "already-gone"
        result.message = "すでに終了していました"
        return result

    if not _looks_like_our_process(info):
        result.message = (
            f"pid {info.pid} はこのアプリではないようなので止めません。\n"
            f"    手動で確認してください(ロック: {launch_guard.lock_path(mode)})")
        log.warning("pid=%s の照合に失敗したため停止しません", info.pid)
        return result

    log.info("pid=%s を停止します", info.pid)
    try:
        os.kill(info.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        result.message = f"停止できませんでした: {exc}"
        return result

    if _wait_pid_gone(info.pid, FORCE_WAIT_SEC):
        launch_guard.remove_lock(mode)
        result.stopped = True
        result.method = "sigterm"
        result.message = "終了しました"
        return result

    if not force:
        result.message = ("終了要求に応じません。"
                          "強制的に止めるには --force を付けてください")
        return result

    try:
        os.kill(info.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError as exc:
        result.message = f"強制終了できませんでした: {exc}"
        return result
    launch_guard.remove_lock(mode)
    result.stopped = True
    result.method = "sigkill"
    result.message = "強制終了しました"
    return result


def _looks_like_our_process(info) -> bool:
    """そのPIDが本当にこのアプリか。

    コマンドラインに**このアプリの置き場所**が入っていることを確かめる。
    別のフォルダにある同じツールや、無関係なPythonを巻き添えにしない。
    コマンドラインが取れない環境では False にして、止めずに知らせる
    (誤って別のアプリを落とすより、止まらないほうが害が小さい)。
    """
    import launch_guard

    cmdline = launch_guard.process_command_line(info.pid)
    if not cmdline:
        log.warning("pid=%s のコマンドラインを取得できませんでした", info.pid)
        return False

    root = (info.app_root or str(app_config.APP_ROOT)).replace("\\", "/")
    normalized = cmdline.replace("\\", "/")
    if root and root in normalized:
        return True
    # 起動スクリプト名でも照合する(相対パスで起動された場合)
    return "start_app.py" in normalized


def _wait_gone(port: int, timeout: float) -> bool:
    """そのポートが応答しなくなるまで待つ。"""
    import launch_guard

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if launch_guard.probe_health(port, timeout=0.3) is None:
            return True
        time.sleep(0.2)
    return False


def _wait_pid_gone(pid: int, timeout: float) -> bool:
    import launch_guard

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not launch_guard.is_process_alive(pid):
            return True
        time.sleep(0.2)
    return False


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="コイル梱包ツールを安全に停止する")
    parser.add_argument("--mode", default=modes.DEFAULT,
                        choices=list(modes.KEYS) + list(modes.LEGACY_NAMES))
    parser.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true",
                        help="実行中の処理を中断してでも止める")
    parser.add_argument("--status", action="store_true", help="状態を見るだけ")
    args = parser.parse_args(argv)

    targets = list(modes.KEYS) if args.all else [modes.normalize(args.mode)]

    if args.status:
        for mode in targets:
            health = status(mode)
            if health is None:
                print(f"[--] {mode}: 起動していません")
            else:
                lock = health["_lock"]
                print(f"[稼働] {mode}: pid={lock['pid']} port={lock['port']} "
                      f"ready={health['ready']} 起動={lock['started']}")
        return 0

    import launch_guard

    if launch_guard.desktop_running():
        print(DESKTOP_MESSAGE)
        bring_desktop_to_front()
        return EXIT_FAILED

    codes = []
    for mode in targets:
        result = stop(mode, force=args.force)
        print(result)
        if not result.stopped:
            codes.append(EXIT_REFUSED if result.refused else EXIT_FAILED)
    if EXIT_FAILED in codes:
        return EXIT_FAILED
    return EXIT_REFUSED if codes else EXIT_STOPPED


def bring_desktop_to_front() -> bool:
    """デスクトップ版の窓を前に出す。

    exe をもう一度起動すると、2つ目は開かずに1つ目の窓を前に出して終わる
    (`tauri-plugin-single-instance`)。それを使う(all-tools と同じ)。
    """
    exe = desktop_exe()
    if exe is None or os.name != "nt":
        return False
    import subprocess
    try:
        subprocess.Popen([str(exe)], cwd=str(APP_ROOT), close_fds=True)
    except OSError:
        return False
    return True


def desktop_exe() -> Optional[Path]:
    """アプリのフォルダにあるデスクトップ版の exe(名前は配布で変わることがある)。"""
    try:
        name = json.loads((APP_ROOT / "src-tauri" / "tauri.conf.json")
                          .read_text(encoding="utf-8")).get("productName") or ""
    except (OSError, ValueError):
        name = ""
    for candidate in [APP_ROOT / "コイル梱包ツール.exe"] + ([APP_ROOT / f"{name}.exe"] if name else []):
        if candidate.is_file():
            return candidate
    return None


if __name__ == "__main__":
    raise SystemExit(main())
