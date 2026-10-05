#!/usr/bin/env python3
"""Python側の起動開始点 (基盤仕様書 2.5) ── 統合アプリ

アプリ本体を読む前に実行環境を整え、多重起動を判定し、サーバを立てて
ブラウザを開く。**業務機能はここに書かない。**

    Start.vbs (通常) / start.bat (診断)
        └─ start_app.py            ← ここ(`run.py` も同じ入口)
             ├─ 実行環境の確認      (Python版数・必須パッケージ・書込権限)
             ├─ launch_guard        (多重起動の判定・順番待ち・ポート選び)
             ├─ server              (waitress + 待機画面 → 統合 Flask アプリ)
             ├─ ブラウザを開く
             └─ 3機能の重い初期化   (modules/<機能>/__init__.py の initialize)

移植元(梱包明細・資材計算)の `start_app.py` はほぼ同じ作りだった。統合版は
資材計算のもの(起動の順番待ち `startup_gate` を持つ)を基にし、初期化を
「3機能を順に呼ぶ」形に変えた。ペナラベルの移植元(`http.server` 用)から
持ってきたのは、Windows での排他 bind(`server.bind_exclusive`)。

使い方:

    python start_app.py                  ふつうに開く
    python start_app.py --no-browser     ブラウザを開かない(検証用)
    python start_app.py --check          環境の確認だけして終わる(診断用)
    python start_app.py --diagnostic     3機能とも細かいログ(DEBUG)まで残す(診断用)
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import types
import webbrowser
from pathlib import Path
from typing import Optional

APP_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_ROOT))

# 起動の起点。**待機画面が出るまでの時間**をログに残すために持つ
_BOOT_AT = time.monotonic()

# 必要なPython。3機能とも 3.9 以上を掲げていた(ペナラベルは 3.8 以上)
MIN_PYTHON = (3, 9)

# `requirements.txt` に対応する import 名
REQUIRED_PACKAGES = (("flask", "Flask"), ("waitress", "waitress"))

# デスクトップ版(`bridge.py`)は待ち受けないので waitress は要らない
BRIDGE_PACKAGES = (("flask", "Flask"),)

# ブラウザを開いたあと、待ち受けが始まるのを待つ上限(秒)
LISTEN_TIMEOUT_SEC = 15

# 3機能の初期化(取り込み)が終わるのを待つ上限(秒)。過ぎたら準備完了に
# して、取り込みは背景で続けさせる(待たせきりにしない)
INIT_WAIT_LIMIT_SEC = 150

# `--mode` の既定。モードは1つしかない(`common/modes.py`)
AUTO = "auto"


class StartupError(RuntimeError):
    """利用者に見せる、次の行動が分かる形のエラー。"""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


# ------------------------------------------------------------------
# 1. 実行環境の確認
# ------------------------------------------------------------------
def check_python_version() -> None:
    if sys.version_info < MIN_PYTHON:
        raise StartupError(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 以上が必要です"
            f"(いまは {sys.version.split()[0]})",
            "https://www.python.org/downloads/ から新しいPythonを入れてください。")


def check_packages(packages=None) -> None:
    """必須パッケージの有無。**入れ方まで示す**(基盤仕様書 ステップ5)。

    既定(`None`)はブラウザ版の一覧。デスクトップ版は `BRIDGE_PACKAGES`。
    """
    import importlib.util

    if packages is None:
        packages = REQUIRED_PACKAGES
    missing = [pip_name for module, pip_name in packages
               if importlib.util.find_spec(module) is None]
    if missing:
        raise StartupError(
            f"必要なパッケージが入っていません: {', '.join(missing)}",
            f"コマンドプロンプトで次を実行してください:\n"
            f"    {console_python()} -m pip install -r requirements.txt")


def console_python() -> str:
    """`pip` を実行するときに使うPythonの名前。

    `Start.vbs` は画面を出さないために **pythonw.exe** で起動する。
    そのまま `sys.executable` を案内すると `pythonw.exe -m pip install ...`
    と出るが、pythonw には画面が無いので**実行しても何も表示されない**。
    案内するときは必ずコンソール側の `python.exe` に読み替える。
    """
    name = sys.executable.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().startswith("pythonw"):
        return "python" + name[len("pythonw"):]
    return name


def should_abort(check) -> bool:
    """起動確認が取れなかったとき、起動そのものを中止するか。

    中止するのは **TCPでも繋がらないとき**だけ。TCPが通っているなら
    サーバ自身は待ち受けており、応答を取れないのはこちら側の確認経路の
    都合(プロキシ・セキュリティ製品)であることが多い。
    """
    return not check.ok and not check.tcp_ok


def describe_loopback() -> str:
    """自分自身への通信まわりの状態(診断用)。"""
    import launch_guard

    lines = ["", "--- 自分自身への通信 ---"]
    proxies = launch_guard.proxy_settings()
    if proxies:
        lines.append("プロキシ設定  : "
                     + ", ".join(f"{k}={v}" for k, v in sorted(proxies.items())))
        lines.append("  ※このアプリはプロキシを経由せずに 127.0.0.1 へ接続します。")
        lines.append("    ブラウザ側でも 127.0.0.1 / localhost が除外されているか"
                     "確認してください。")
    else:
        lines.append("プロキシ設定  : なし")
    return "\n".join(lines)


def check_writable() -> Path:
    """ローカル領域を作れるか(基盤仕様書 2.7)。"""
    from common import app_config

    try:
        root = app_config.ensure_local_dirs()
    except OSError as exc:
        raise StartupError(
            f"作業用フォルダを作れません: {exc}",
            "書き込みの権限があるか、ディスクの空きがあるか確認してください。") from None

    probe = root / "runtime" / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise StartupError(
            f"作業用フォルダに書き込めません: {root}",
            f"{exc}") from None
    return root


def redirect_pycache(root: Path) -> None:
    """`__pycache__` をローカル領域の `pycache\\` へ向ける(ペナラベルの移植元と同じ)。

    **アプリのフォルダに `__pycache__` を作らせない**(基盤仕様書 2.7)。アプリ一式は
    共有フォルダに置かれることがあり、そこへ端末ごと・Python の版ごとの `__pycache__`
    が書かれると配布フォルダが汚れ、書けない端末では毎回コンパイルし直す。
    統合版では、この処理が移植元のペナラベルにしか無かったので抜けていた。
    書き先が決まるまでは書かない(`main` の最初で止めておく)。

    環境変数 `PYTHONPYCACHEPREFIX` で向け先を決めてあればそれに従い、
    `PYTHONDONTWRITEBYTECODE`(`-B`)で書かないと決めてあれば書かないまま。
    """
    if not sys.pycache_prefix:
        target = root / "pycache"
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            return                                  # 書かないまま動く(遅くなるだけ)
        sys.pycache_prefix = str(target)
    sys.dont_write_bytecode = bool(sys.flags.dont_write_bytecode)


def _module_areas() -> list:
    """3機能の手元の領域: (呼び名, 作って根を返す関数)。

    統合版も3機能の手元DB・作業状態・設定を、**統合前と同じ各機能の領域**
    (`%LOCALAPPDATA%\\PackingDetails` など)に置いたまま使う(引き継ぐため)。
    """
    def details() -> Path:
        from modules.packing_details.meisai import app_config
        return app_config.ensure_local_dirs()

    def material() -> Path:
        from modules.packing_material_calculation.coil_tool import app_config
        return app_config.ensure_local_dirs()

    def pena() -> Path:
        from modules.packing_pena_label.app.config import load_config
        cfg = load_config()
        cfg.ensure_dirs()
        return Path(cfg.local_dir)

    return [("梱包明細", details), ("ペナラベル", pena), ("資材計算", material)]


def check_module_areas() -> None:
    """3機能の手元の領域を作れて、書けるか。

    移植元の3つの `start_app` は、それぞれ自分の領域をここで確かめていた。
    統合版は統合アプリの領域しか見ていなかったので、機能の領域に書けない端末では
    画面は出ても保存のたびに失敗していた(移植漏れの点検で見つかった)。起動の前に言う。
    """
    for label, make in _module_areas():
        root = None
        try:
            root = Path(make())
            probe = root / "runtime" / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise StartupError(
                f"{label}の作業用フォルダに書き込めません: {root or exc}",
                f"{exc}\n書き込みの権限があるか、ディスクの空きがあるか確認してください。") from None


def check_config() -> None:
    """アプリ固有値が読めているか。読めなくても既定値で動くが、記録は残す。"""
    from common import app_config

    error = app_config.load_error()
    if error:
        log().warning("%s — 既定値で起動します", error)
    for problem in app_config.port_range_conflicts():
        log().warning("%s", problem)


def run_environment_checks(*, bridge: bool = False) -> Path:
    """順に確認する。落ちたところで理由が分かるように分けてある。"""
    check_python_version()
    check_packages(BRIDGE_PACKAGES if bridge else None)
    root = check_writable()
    redirect_pycache(root)
    check_module_areas()
    check_config()
    return root


# ------------------------------------------------------------------
# ログ
# ------------------------------------------------------------------
_log = None


def log():
    """起動入口のログ(基盤仕様書 2.6 の `launcher.log` にあたる)。"""
    global _log
    if _log is None:
        from common.logging_utils import get_logger
        _log = get_logger("coil_packing_tools", "launcher")
    return _log


def apply_common_distribution():
    """配布設定(共通: ログの出力先・残す日数)を読み込む(統合 1.0.14)。

    **起動のログの1行目より前に呼ぶ** ── 配られた出力先へ、起動の記録から出す。
    このPCですでに設定してある項目は読まない。起動は止めない(結果は呼ぶ側がログへ)。
    """
    try:
        from common import log_distribution, logging_utils
        result = log_distribution.apply_on_start()
        if result.applied:
            logging_utils.apply_settings()          # 読み込んだ出力先へ切り替える
        return result
    except Exception as exc:                        # noqa: BLE001 - 起動は止めない
        # 部品そのものを読めなかったときも同じ形で返す(呼ぶ側は applied・kept・message を見る)
        return types.SimpleNamespace(ok=False, applied=[], kept=[],
                                     message=f"配布設定(共通)を読めませんでした: {exc}")


def log_environment(mode: str) -> None:
    """起動のたびに残す1枚(基盤仕様書 2.6)。"""
    from common import app_config

    loaded = apply_common_distribution()
    log().info("=" * 60)
    log().info("起動: mode=%s pid=%s", mode, os.getpid())
    log().info("Python: %s (%s)", sys.version.split()[0], sys.executable)
    log().info("アプリ本体: %s", APP_ROOT)
    try:
        from common import versions
        # 統合ツールの版と3機能の版(分けて持つ)。「どの版が動いていたか」を後から追う
        log().info("版: %s", versions.describe())
    except Exception as exc:                        # noqa: BLE001 - 起動は止めない
        log().warning("版を読めませんでした: %s", exc)
    log().info("ローカル領域: %s", app_config.local_root())
    if loaded.applied:
        log().info("配布設定(共通)を読み込みました: %s(すでにあったので読まなかったもの: %s)",
                   "・".join(loaded.applied), "・".join(loaded.kept) or "なし")
    if loaded.message:
        log().warning("配布設定(共通): %s", loaded.message)
    log_module_environment()
    tidy_logs()


def tidy_logs() -> None:
    """ログの置き場所を残し、残す日数を過ぎたログとエラーの記録を消す(統合 1.0.12)。

    **起動を止めない。** 消せなくても(共有が遅い・権限が無い)次の起動でまた試す。
    """
    try:
        from common import incidents, logging_utils
        state = logging_utils.status()
        log().info("ログ: %s(PC %s・残す日数 %s 日)", state["dir"], state["pc_name"],
                   state["keep_days"])
        if state["problem"]:
            log().warning("ログの出力先: %s", state["problem"])
        removed = logging_utils.cleanup_old() + incidents.cleanup_old()
        if removed:
            log().info("古いログとエラーの記録を %d 件消しました(%s 日より前)",
                       len(removed), state["keep_days"])
    except Exception as exc:                        # noqa: BLE001 - 起動は止めない
        log().warning("古いログを片付けられませんでした: %s", exc)


#: 統合版では効かない、移植元のログの置き場所の環境変数(ログは統合アプリの1か所)
LEGACY_LOG_ENV = ("PACKING_DETAILS_LOG_DIR", "COIL_TOOL_LOG_DIR")


def log_module_environment() -> None:
    """3機能の設定と置き場所(移植元の各 `start_app` が起動のたびに残していたもの)。

    **置き場所がまずければ起動時に言う**(梱包明細の移植元)。止めはしない ──
    1台で使っているうちは動くので、ここで落とすと「昨日まで動いていたのに」になる。
    """
    from common import logging_utils

    try:
        from modules.packing_details.meisai import app_config as d_cfg
        from modules.packing_details.meisai import config as d_paths
        error = d_cfg.load_error()
        if error:
            log().warning("梱包明細: %s — 既定値で起動します", error)
        problem = d_paths.db_path_problem()
        if problem:
            log().warning("梱包明細: %s", problem)
        log().info("梱包明細: 手元の領域 %s / 手元DB %s", d_cfg.local_root(), d_paths.DB_PATH)
    except Exception as exc:                        # noqa: BLE001 - 起動は止めない
        log().warning("梱包明細の設定を確かめられませんでした: %s", exc)
    try:
        from modules.packing_material_calculation.coil_tool import app_config as m_cfg
        from modules.packing_material_calculation.coil_tool import config as m_paths
        error = m_cfg.load_error()
        if error:
            log().warning("資材計算: %s — 既定値で起動します", error)
        log().info("資材計算: 手元の領域 %s / 手元DB %s", m_cfg.local_root(), m_paths.DB_PATH)
    except Exception as exc:                        # noqa: BLE001
        log().warning("資材計算の設定を確かめられませんでした: %s", exc)
    try:
        from modules.packing_pena_label.app.config import load_config
        cfg = load_config()
        log().info("ペナラベル: 手元の領域 %s / 状態DB %s", cfg.local_dir, cfg.db_path)
    except Exception as exc:                        # noqa: BLE001
        log().warning("ペナラベルの設定を確かめられませんでした: %s", exc)
    # 移植元でログの置き場所を変えていた環境変数は、統合版では効かない。
    # 黙って無視すると「設定したのにログが無い」になるので、残っていれば言う
    for name in LEGACY_LOG_ENV:
        if os.environ.get(name, "").strip():
            log().warning("環境変数 %s は統合版では使いません。ログは %s に出ます"
                          "(変えるときは COIL_PACKING_TOOLS_LOG_DIR)",
                          name, logging_utils.log_dir())


# ------------------------------------------------------------------
# 2. 起動
# ------------------------------------------------------------------
def resolve_mode(requested: str) -> str:
    """開くモードを決める。モードは1つしかないので、何を渡されても既定へ寄せる。"""
    from common import modes

    return modes.normalize("" if requested == AUTO else requested)


def start(mode: str, *, open_browser: bool = True) -> int:
    """戻り値はプロセスの終了コード。

    【順番が要点】待機画面より前に置くものを、できるだけ減らしてある。

        ポートを決める → 待ち受け開始(待機画面だけ)→ ブラウザ
            → 本体(統合アプリ+3機能)を組み立てて差し替え → 3機能の重い初期化
    """
    import launch_guard
    import server as server_module
    from common import app_config

    mode = resolve_mode(mode)
    log_environment(mode)

    # **ここから「ロックを書く」までを1つずつしか通さない。**
    # 判定からロックまでは1秒弱かかるので、その間に2つ目が起動すると
    # どちらも「ロックなし」と判断して両方が立ち上がる(資材計算で実測)。
    with launch_guard.startup_gate(mode):
        guard = launch_guard.check_existing(mode)
        if not guard.should_start:
            log().info("既存のインスタンスに合流します: %s", guard.url)
            print(f"すでに起動しています。ブラウザを開きます: {guard.url}")
            if open_browser:
                webbrowser.open(guard.url)
            return 0
        log().info("多重起動の判定: %s", guard.reason)

        # --- 統合前の単体版が動いていないか(同じ手元のデータを使う) ---
        legacy = launch_guard.find_legacy_instances()
        if legacy:
            names = "、".join(x.describe() for x in legacy)
            log().warning("統合前の単体版が動いています: %s", names)
            raise StartupError(
                f"統合前の単体版が動いています: {names}",
                "単体版と統合版は同じデータ(作業状態・手元DB・設定)を使うので、"
                "同時には動かせません。単体版の画面を閉じるか、単体版のフォルダの "
                "stop.bat で止めてから、もう一度起動してください。"
                "単体版の Start.vbs やショートカットは消しておいてください。")

        # --- デスクトップ版(exe)が動いていないか(同じ手元のデータを使う) ---
        if launch_guard.desktop_running():
            log().warning("デスクトップ版が動いているので、ブラウザ版は起動しません")
            raise StartupError(
                "デスクトップ版のコイル梱包ツールが動いています",
                "デスクトップ版(コイル梱包ツール.exe)と同じデータを使うので、同時には動かせません。"
                "デスクトップ版の窓を使うか、窓を閉じてからもう一度起動してください。")

        # --- ポート選び ---
        port = launch_guard.pick_port(mode)
        if port is None:
            candidates = app_config.port_candidates(mode)
            raise StartupError(
                f"使えるポートがありません(試した番号: {candidates})",
                "他のアプリが使っている可能性があります。"
                "config/app.json の port を変えるか、そのアプリを終了してください。")

        # --- 待ち受けを始める(この時点ではまだ待機画面だけ) ---
        srv = server_module.AppServer(mode, port)
        thread = server_module.run_in_background(srv)

        check = server_module.diagnose_listening(port, timeout=LISTEN_TIMEOUT_SEC)
        if should_abort(check):
            raise StartupError(
                "サーバを起動できませんでした",
                f"{check.hint}\n\nログ: {app_config.local_dir('logs')}")
        if not check.ok:
            log().warning("起動確認の応答を取れませんでしたが、待ち受けは"
                          "できているので続行します:\n%s", check.hint)
            print("[注意] 起動の確認応答を取れませんでした。"
                  "画面が出ない場合は次を確認してください:")
            print(check.hint)

        # 待ち受けが始まってからロックを書く。先に書くと、起動に失敗した
        # ロックが残って次回の判定を惑わせる
        launch_guard.write_lock(
            launch_guard.build_lock_info(mode, port, srv.token))
    # ここで順番待ちを解く。あとは自分のプロセスの中の話

    try:
        # --- ブラウザを開く ---
        if open_browser:
            log().info("ブラウザを開きます: %s", srv.url)
            webbrowser.open(srv.url)
        else:
            print(f"起動しました: {srv.url}")
        log().info("待機画面まで %.2f秒", time.monotonic() - _BOOT_AT)

        # --- 本体を組み立てて差し替える ---
        try:
            srv.build()
        except Exception as exc:                  # noqa: BLE001 - 画面に出して継続
            log().exception("アプリを組み立てられませんでした")
            srv.boot.mark_error(f"アプリを組み立てられませんでした: {exc}")
            _hold_until_stopped(srv, thread)
            return 1

        _initialize(srv)

        # --- 待ち受けが終わるまでここで止まる ---
        _hold_until_stopped(srv, thread)
        return 0
    finally:
        _close_modules()
        launch_guard.remove_lock(mode)
        log().info("終了しました: mode=%s", mode)


#: 止めるときの片付けを待つ上限(秒)。**片付けで止まらない**ための上限
CLOSE_WAIT_SEC = 3.0


def _close_modules() -> None:
    """3機能の止めるときの片付け(`close` を持つ機能だけ。ペナラベルの状態DB)。

    移植元のペナラベルは止めるときに状態DB を閉じていた(`serve()` の `finally`)。
    統合版では閉じずにプロセスを終えていた(移植漏れの点検で見つかった)。

    **待つのは上限まで。** 状態DB を閉じるには DB の鍵を取る。応答の途中で固まった
    要求が鍵を持ったままだと待ち続け、「確実に落とす」(`_hard_exit`)が効かなくなる。
    間に合わなければ閉じずに終える(開いたままでもファイルは壊れない。移植元の
    強制終了と同じ)。
    """
    if "app" not in sys.modules:
        return                                      # 本体を組み立てる前に止まった

    def work() -> None:
        for module in sys.modules["app"].all_modules():
            close = getattr(module, "close", None)
            if close is None:
                continue
            try:
                close()
            except Exception as exc:                # noqa: BLE001 - 止めるのは続ける
                log().warning("%s の片付けに失敗しました: %s", module.LABEL, exc)

    worker = threading.Thread(target=work, name="close-modules", daemon=True)
    worker.start()
    worker.join(timeout=CLOSE_WAIT_SEC)
    if worker.is_alive():
        log().warning("止めるときの片付けが %.0f秒 で終わらないので、そのまま終えます",
                      CLOSE_WAIT_SEC)


# 停止を頼んでから、受付の輪が終わるのを待つ上限(秒)。
# ここを過ぎたら**確実に落とす** ── 残ったプロセスは次回の起動で
# 「すでに起動しています」と判定され、入れ替えた新しい版が動かない
EXIT_WAIT_SEC = 6.0


def _hold_until_stopped(srv, thread) -> None:
    """待ち受けが終わるまで止まる。**終わらなくても必ず抜ける。**"""
    while thread.is_alive():
        thread.join(timeout=0.5)
        if not srv.stop_requested:
            continue
        thread.join(timeout=EXIT_WAIT_SEC)
        if thread.is_alive():
            log().warning("待ち受けが %.0f秒 で終わらないので、"
                          "プロセスを終了します", EXIT_WAIT_SEC)
            _hard_exit()
        return


def _hard_exit() -> None:
    """後始末をしてから、確実に落とす。"""
    import logging

    _close_modules()
    logging.shutdown()
    os._exit(0)


class _Stages:
    """3機能の段を、まとめて待機画面に出す。

    機能ごとに最新の段を持ち、画面には**まだ終わっていない機能の段を並べて**出す
    (`梱包明細: 取り込み中 / 資材計算: 取り込み中`)。以前は最後に知らせた機能の段で
    上書きしていたので、梱包明細の取り込みが続いていても「資材計算: 取り込み中」だけが
    出たり、段の目印が「取り込み」から「準備」へ戻ったりした(移植漏れの点検で
    見つかった。移植元はどれも自分の段だけを出していた)。
    """

    #: 段の目印の順(待機画面の `order` と同じ)。並べるときは進んでいるほうを出す
    ORDER = ("env", "prepare", "import", "done")

    def __init__(self, srv) -> None:
        self._srv = srv
        self._lock = threading.Lock()
        self._now: dict = {}                      # 呼び名 → (段の文, 目印)

    def report(self, label: str) -> "_Report":
        return _Report(self, label)

    def set(self, label: str, text: str, key: str) -> None:
        log().info("段: %s", text)
        with self._lock:
            self._now[label] = (text, key)
            self._show()

    def finish(self, label: str) -> None:
        with self._lock:
            if self._now.pop(label, None) is not None:
                self._show()

    def error(self, text: str) -> None:
        self._srv.mark_error(text)

    def _show(self) -> None:
        if not self._now:
            return
        texts = [text for text, _ in self._now.values()]
        key = max((k for _, k in self._now.values()),
                  key=lambda k: self.ORDER.index(k) if k in self.ORDER else 0)
        self._srv.mark_stage(" / ".join(texts), key)


class _Report:
    """1つの機能の初期化が、待機画面へ段を知らせるための口。"""

    def __init__(self, stages: _Stages, label: str) -> None:
        self._stages = stages
        self._label = label

    def stage(self, text: str, key: str = "prepare") -> None:
        self._stages.set(self._label, text, key)

    def error(self, text: str) -> None:
        self._stages.error(text)


#: 背景の取り込みが終わったかを見る間隔(秒)
READY_POLL_SEC = 0.2


def start_bridge(mode: str, *, token: str = "", server_factory) -> int:
    """デスクトップ版の起動(`bridge.py` から)。**ポートを使わない。**

    窓・多重起動の防止・終了の確認は外枠(Rust/Tauri)が持つ。ここでするのは
    ブラウザ版と同じ「待機画面 → 本体を組み立てる → 3機能の重い初期化」だけで、
    その中身(`_initialize`)は共有する ── 2本持つと片方だけ直すことになる。

    **ブラウザ版と同時には動かさない**(同じ手元のDB・作業状態を使う)。ブラウザ版が
    動いていれば断る。統合前の単体版も同じ。
    """
    import secrets

    import launch_guard
    import server as server_module

    mode = resolve_mode(mode)
    log_environment(mode)
    with launch_guard.startup_gate(mode):
        guard = launch_guard.check_existing(mode)
        if not guard.should_start:
            raise StartupError(
                "ブラウザ版のコイル梱包ツールが動いています",
                "ブラウザ版と同じデータを使うので、同時には動かせません。ブラウザの画面で"
                "「終了」を押すか stop.bat で止めてから、もう一度開いてください。")
        legacy = launch_guard.find_legacy_instances()
        if legacy:
            names = "、".join(x.describe() for x in legacy)
            raise StartupError(
                f"統合前の単体版が動いています: {names}",
                "単体版の画面を閉じるか、単体版のフォルダの stop.bat で止めてから、"
                "もう一度開いてください。")
    srv = server_factory(mode, token or secrets.token_urlsafe(24))
    thread = server_module.run_in_background(srv)
    log().info("待機画面まで %.2f秒(デスクトップ版)", time.monotonic() - _BOOT_AT)
    try:
        try:
            srv.build()
        except Exception as exc:                  # noqa: BLE001 - 画面に出して継続
            log().exception("アプリを組み立てられませんでした")
            srv.boot.mark_error(f"アプリを組み立てられませんでした: {exc}")
            _hold_until_stopped(srv, thread)
            return 1
        # 窓を閉じたら外枠が終わらせるので、心拍による自動終了は使わない
        _initialize(srv, watch_idle=False)
        _hold_until_stopped(srv, thread)
        return 0
    finally:
        _close_modules()
        log().info("終了しました: mode=%s(デスクトップ版)", mode)


def _initialize(srv, *, watch_idle: bool = True) -> None:
    """3機能の重い初期化。サーバが立ってから行う。

    機能ごとに `initialize(report)` を呼ぶ。1つが失敗しても他は続ける
    (失敗は待機画面に出す)。背景の取り込みが終わるまで(上限つき)待ってから
    準備完了にする ── 準備が一瞬で終わると、待機画面が出る前に業務画面へ入り、
    マスタが空のまま「未取り込み」を読むことになる。
    """
    import app as app_module
    from common import idle_exit

    # 画面が居なくなったら終わる(基盤仕様書 2.8)。**見張りはプロセスに1つ。**
    # 3機能の画面と統合画面の外枠が、同じ見張りへ心拍を送る。
    # 処理中(資材計算の取り込み)は落とさない。スリープから戻ったら、
    # 梱包明細の画面の空きの猶予も数え直す
    if watch_idle:
        on_wake = getattr(app_module.all_modules()[0], "on_wake", None)
        idle_exit.install(srv.stop, app_module.busy, on_wake=on_wake)

    run_initializers(srv, app_module.all_modules())


def run_initializers(srv, modules, *, limit_sec: Optional[float] = None) -> Optional[threading.Thread]:
    """機能を順に初期化し、背景の取り込みが終わったら準備完了にする。

    背景で待つときは、その見張りのスレッドを返す(試験が待てるように)。
    """
    stages = _Stages(srv)
    limit = INIT_WAIT_LIMIT_SEC if limit_sec is None else limit_sec

    pending: list[tuple[str, threading.Event]] = []
    for module in modules:
        try:
            event = module.initialize(stages.report(module.LABEL))
        except Exception as exc:                  # noqa: BLE001 - ほかの機能は続ける
            log().exception("%s の初期化に失敗しました", module.LABEL)
            stages.error(f"{module.LABEL}: 初期化に失敗しました: {exc}")
            stages.finish(module.LABEL)
            continue
        if event is None:
            stages.finish(module.LABEL)          # 背景の仕事が無ければ、この機能は済み
        else:
            pending.append((module.LABEL, event))

    if not pending:
        srv.mark_ready(True)
        return None

    def wait_all() -> None:
        deadline = time.monotonic() + limit
        waiting = list(pending)
        while waiting:
            for item in list(waiting):
                if item[1].is_set():
                    waiting.remove(item)
                    stages.finish(item[0])
            if not waiting:
                break
            if time.monotonic() >= deadline:
                for label, _ in waiting:
                    log().warning("%s の取り込みが %s秒 で終わらないので、先に画面を開きます",
                                  label, limit)
                break
            time.sleep(READY_POLL_SEC)
        srv.mark_ready(True)

    thread = threading.Thread(target=wait_all, name="ready-watch", daemon=True)
    thread.start()
    return thread


# ------------------------------------------------------------------
# 3. 失敗の伝え方
# ------------------------------------------------------------------
def report_failure(error: StartupError, *, open_browser: bool) -> None:
    """コンソールとブラウザの両方に出す。

    `Start.vbs`(コンソール非表示)で起動された場合、標準出力は誰にも
    見えない。HTMLを書いてブラウザで開く(基盤仕様書 2.2)。
    """
    print(f"\n[エラー] {error}", file=sys.stderr)
    if error.hint:
        print(error.hint, file=sys.stderr)

    try:
        from common import app_config
        log_dir = str(app_config.local_dir("logs"))
    except Exception:                             # noqa: BLE001 - 失敗の報告で失敗しない
        log_dir = "(ローカル領域を特定できませんでした)"

    try:
        log().error("起動に失敗: %s / %s", error, error.hint)
    except Exception:                             # noqa: BLE001
        pass

    if not open_browser:
        return
    try:
        path = _write_error_page(str(error), error.hint, log_dir)
        webbrowser.open(path.as_uri())
    except Exception as exc:                      # noqa: BLE001
        print(f"(エラー画面を出せませんでした: {exc})", file=sys.stderr)


def _write_error_page(message: str, hint: str, log_dir: str) -> Path:
    """起動に失敗したことを伝えるHTMLを一時領域に書く。"""
    import html
    import tempfile

    try:
        from common import app_config
        target = app_config.local_dir("work") / "起動エラー.html"
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception:                             # noqa: BLE001
        target = Path(tempfile.gettempdir()) / "coil_packing_tools_起動エラー.html"

    target.write_text(f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>起動できませんでした</title>
<style>
 body{{margin:0;min-height:100vh;display:grid;place-items:center;
       background:#eef1f5;color:#101720;
       font-family:system-ui,"Yu Gothic UI","Meiryo UI",sans-serif;line-height:1.7}}
 .box{{width:min(560px,calc(100vw - 48px));background:#fff;border:1px solid #c9d2dc;
       border-radius:4px;padding:32px;box-shadow:0 6px 20px rgba(16,23,32,.08)}}
 h1{{margin:0 0 12px;font-size:19px;color:#b4232a}}
 .hint{{margin-top:16px;padding:14px;background:#fdeaea;border-left:4px solid #b4232a;
        border-radius:0 3px 3px 0;white-space:pre-wrap}}
 code{{font-family:ui-monospace,Consolas,monospace;font-size:13px;
       background:rgba(0,0,0,.06);padding:2px 5px;border-radius:2px;word-break:break-all}}
 dt{{color:#556171;font-size:13px;margin-top:12px}}
</style></head>
<body><main class="box">
<h1>起動できませんでした</h1>
<p>{html.escape(message)}</p>
{f'<div class="hint">{html.escape(hint)}</div>' if hint else ''}
<dt>ログの場所</dt>
<p><code>{html.escape(log_dir)}</code></p>
<dt>診断</dt>
<p>コンソールで詳しく見るには <code>start.bat</code> を実行してください。</p>
</main></body></html>
""", encoding="utf-8")
    return target


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    # `__pycache__` は書き先(ローカル領域)が決まるまで書かない(`redirect_pycache`)
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description="コイル梱包ツールを起動する")
    parser.add_argument("--mode", default=AUTO, help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true",
                        help="ブラウザを開かない(検証用)")
    parser.add_argument("--check", action="store_true",
                        help="実行環境の確認だけして終わる(診断用)")
    parser.add_argument("--diagnostic", action="store_true",
                        help="3機能とも細かいログ(DEBUG)まで残す(ペナラベルの移植元と同じ)")
    args = parser.parse_args(argv)
    if args.diagnostic:
        from common import logging_utils
        logging_utils.set_diagnostic(True)

    open_browser = not args.no_browser

    try:
        run_environment_checks()
    except StartupError as exc:
        report_failure(exc, open_browser=open_browser)
        return 1

    if args.check:
        from common import app_config
        print("実行環境の確認: 問題ありません\n")
        print(app_config.describe())
        print(describe_loopback())
        return 0

    try:
        return start(args.mode, open_browser=open_browser)
    except StartupError as exc:
        report_failure(exc, open_browser=open_browser)
        return 1
    except KeyboardInterrupt:
        print("\n中断しました")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
