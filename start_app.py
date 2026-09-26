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
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
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


def check_packages() -> None:
    """必須パッケージの有無。**入れ方まで示す**(基盤仕様書 ステップ5)。"""
    import importlib.util

    missing = [pip_name for module, pip_name in REQUIRED_PACKAGES
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


def check_config() -> None:
    """アプリ固有値が読めているか。読めなくても既定値で動くが、記録は残す。"""
    from common import app_config

    error = app_config.load_error()
    if error:
        log().warning("%s — 既定値で起動します", error)
    for problem in app_config.port_range_conflicts():
        log().warning("%s", problem)


def run_environment_checks() -> Path:
    """順に確認する。落ちたところで理由が分かるように分けてある。"""
    check_python_version()
    check_packages()
    root = check_writable()
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


def log_environment(mode: str) -> None:
    """起動のたびに残す1枚(基盤仕様書 2.6)。"""
    from common import app_config

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
        launch_guard.remove_lock(mode)
        log().info("終了しました: mode=%s", mode)


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

    logging.shutdown()
    os._exit(0)


class _Report:
    """3機能の初期化が待機画面へ段を知らせるための口。"""

    def __init__(self, srv) -> None:
        self._srv = srv

    def stage(self, text: str, key: str = "prepare") -> None:
        log().info("段: %s", text)
        self._srv.mark_stage(text, key)

    def error(self, text: str) -> None:
        self._srv.mark_error(text)


def _initialize(srv) -> None:
    """3機能の重い初期化。サーバが立ってから行う。

    機能ごとに `initialize(report)` を呼ぶ。1つが失敗しても他は続ける
    (失敗は待機画面に出す)。背景の取り込みが終わるまで(上限つき)待ってから
    準備完了にする ── 準備が一瞬で終わると、待機画面が出る前に業務画面へ入り、
    マスタが空のまま「未取り込み」を読むことになる。
    """
    import app as app_module
    from common import idle_exit

    report = _Report(srv)

    # 画面が居なくなったら終わる(基盤仕様書 2.8)。**見張りはプロセスに1つ。**
    # 3機能の画面と統合画面の外枠が、同じ見張りへ心拍を送る。
    # 処理中(資材計算の取り込み)は落とさない。スリープから戻ったら、
    # 梱包明細の画面の空きの猶予も数え直す
    on_wake = getattr(app_module.all_modules()[0], "on_wake", None)
    idle_exit.install(srv.stop, app_module.busy, on_wake=on_wake)

    pending: list[tuple[str, threading.Event]] = []
    for module in app_module.all_modules():
        try:
            event = module.initialize(report)
        except Exception as exc:                  # noqa: BLE001 - ほかの機能は続ける
            log().exception("%s の初期化に失敗しました", module.LABEL)
            report.error(f"{module.LABEL}: 初期化に失敗しました: {exc}")
            continue
        if event is not None:
            pending.append((module.LABEL, event))

    if not pending:
        srv.mark_ready(True)
        return

    def wait_all() -> None:
        deadline = time.monotonic() + INIT_WAIT_LIMIT_SEC
        for label, event in pending:
            left = max(0.0, deadline - time.monotonic())
            if not event.wait(left):
                log().warning("%s の取り込みが %s秒 で終わらないので、先に画面を開きます",
                              label, INIT_WAIT_LIMIT_SEC)
        srv.mark_ready(True)

    threading.Thread(target=wait_all, name="ready-watch", daemon=True).start()


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
    parser = argparse.ArgumentParser(description="コイル梱包ツールを起動する")
    parser.add_argument("--mode", default=AUTO, help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true",
                        help="ブラウザを開かない(検証用)")
    parser.add_argument("--check", action="store_true",
                        help="実行環境の確認だけして終わる(診断用)")
    args = parser.parse_args(argv)

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
