"""統合 Flask アプリの組み立て ── 3機能を1つのサーバに載せる

    /                      統合画面(3つのタブ。中身は各機能の画面を iframe で出す)
    /api/health            起動確認(統合アプリの身元。多重起動の判定・待機画面が見る)
    /api/alive             統合画面の心拍(自動終了の見張りへ)
    /api/shutdown          安全な停止(トークン必須)
    /log                   ログとエラーの記録(上の帯の「ログ」。出力先の設定・エラーの一覧)
    /api/client-log        画面(ブラウザ)で起きたエラーの報告(`static/js/error_report.js`)
    /api/log/...           ログの様子・設定・エラーの記録の一覧と中身(トークン必須)
    /details/...           梱包明細   (modules/packing_details)
    /pena/...              ペナラベル (modules/packing_pena_label)
    /material/...          資材計算   (modules/packing_material_calculation)

【なぜ機能ごとに入口(URLの接頭辞)を付けるのか】
3つの移植元はどれも `/` `/api/health` `/api/settings` `/api/master/*`
`/static/...` を自分の根に持っていた。同じ経路に3つは置けないので、
機能ごとに入口を切り、その下に**移植元の経路をそのまま**置く。
画面のJSは入口を `window.APP.base` 等で受け取って付ける。

【なぜタブの中身が iframe なのか】
3つの画面は CSS(`.card` `.btn` `.toast` …の同名クラス)も JS
(`window.APP` の使い方・心拍・画面の見張り)も別々に作られている。
1つの文書に3つを流し込むと、名前の衝突を全部ほどく=3画面の書き直しに
なる。iframe なら各画面はそれぞれの文書のまま動き、**タブを切り替えても
消えない**(iframe は隠すだけで外さない)ので、入力の途中の値も、
心拍も、そのまま続く。

【プロセスに1つのもの】
起動トークン・準備完了(`READY`)・自動終了の見張り・停止の仕方は
統合アプリが1つ持ち、3機能はそれを見る(`ChainMap`)。
表示名・版・アプリIDは機能ごとにそれぞれの `config/app.json` のまま。
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from datetime import date
from pathlib import Path
from typing import Optional

from flask import (Blueprint, Flask, current_app, g, jsonify, redirect, render_template, request,
                   url_for)
from markupsafe import escape
from werkzeug.exceptions import HTTPException

from common import (app_config, boot_screen, idle_exit, incidents, local_settings,
                    logging_utils, modes, security, versions)
from common.logging_utils import get_logger

log = get_logger("coil_packing_tools", "app")

APP_ROOT = Path(__file__).resolve().parent


def all_modules() -> tuple:
    """載せる機能。**この並びがタブの並び。**"""
    from modules import (packing_details, packing_material_calculation,
                         packing_pena_label)
    return (packing_details, packing_pena_label, packing_material_calculation)


# 停止要求を受けてから実際に落とすまでの猶予(秒)。
# 応答を返しきる前にサーバを止めると「押したのに何も起きなかった」に見える
SHUTDOWN_DELAY_SEC = 0.4

_shutdown_hook = None


def set_shutdown_hook(func) -> None:
    """サーバの止め方を登録する(`server.py` が差し込む)。統合画面の `/api/shutdown` が使う。"""
    global _shutdown_hook
    _shutdown_hook = func


def create_app(mode: str = modes.MAIN, *, token: Optional[str] = None,
               port: Optional[int] = None, modules=None) -> Flask:
    """統合アプリを1つ組み立てる。

    `token` を省略すると起動ごとに新しく作る。試験からは固定値を渡せる。
    `modules` を省略すると3機能すべてを載せる(試験は一部だけ載せられる)。
    """
    app = Flask(__name__,
                template_folder=str(APP_ROOT / "templates"),
                static_folder=str(APP_ROOT / "static"),
                static_url_path="/static")
    app.config.update(
        MODE=modes.normalize(mode),
        # 起動ごとの合言葉。同じPC上の別プロセスや、利用者が偶然開いた
        # 外部のWebページから叩かれないようにする。**3機能で同じ値**
        TOKEN=token or secrets.token_urlsafe(32),
        PORT=port or app_config.port(modes.MAIN),
        APP_ID=app_config.app_id(),
        VERSION=app_config.version(),
        DISPLAY_NAME=app_config.display_name(),
        STARTED_AT=time.time(),
        # 起動直後は準備中。3機能の重い初期化が終わってから True にする。
        # 起動待機画面はこれを見て切り替える
        READY=False,
        STAGE="アプリを準備中",
        STAGE_KEY="prepare",
        STARTUP_ERROR="",
        SECRET_KEY=secrets.token_hex(16),
        # ペナラベルの API にもトークンを要求する(移植元には無かった)
        PENA_REQUIRE_TOKEN=True,
        MODULES=[],
    )
    # **最初に差し込む**(要求の印を付けてから、ほかの関門が断る ── 断った行にも印が付く)
    _install_error_trail(app)
    security.install_common(app)
    app.register_blueprint(_shell_blueprint())

    loaded = []
    for module in (modules if modules is not None else all_modules()):
        module.register(app)
        loaded.append({
            "key": module.KEY, "label": module.LABEL, "prefix": module.PREFIX,
            "home": module.HOME, "display_name": module.display_name(),
            "version": module.version(),
            "ported_from": dict(getattr(module, "PORTED_FROM", {}) or {}),
        })
    app.config["MODULES"] = loaded
    # 版は**統合ツールの版と3機能の版を分けて**持つ(common/versions.py)。
    # このプロセスが読み込んだ中身の版。起動時の入れ替え判定はこの組で比べる
    app.config["VERSIONS"] = {versions.APP_KEY: app.config["VERSION"],
                              **{m["key"]: m["version"] for m in loaded}}
    app.config["VERSION_SET"] = versions.version_set(app.config["VERSIONS"])
    _install_tab_alive(app, [m["prefix"] for m in loaded])
    _install_error_report(app, [m["prefix"] for m in loaded])
    _install_boot_redirect(app, loaded)
    _install_shutdown_guard(app, loaded)

    @app.errorhandler(404)
    def _not_found(_e):                         # noqa: ANN202 - Flaskのフック
        return jsonify({"error": {"code": "not_found",
                                  "message": "ページが見つかりません"}}), 404

    log.info("create_app: port=%s 版=%s", app.config["PORT"],
             versions.describe(app.config["VERSIONS"]))
    return app


# ------------------------------------------------------------------
# 別のタブで開いた画面の心拍(帳票・印刷ビュー)
# ------------------------------------------------------------------
#: 差し込んだ印。2度差し込まない
TAB_ALIVE_MARK = "js/tab_alive.js"


def inject_tab_alive(html: str, tag: str) -> str:
    """HTML の末尾(最後の `</body>` の前)に心拍のスクリプトを1つ差し込む。

    `</body>` が無ければ最後に足す。すでに入っていれば何もしない。
    """
    if TAB_ALIVE_MARK in html:
        return html
    at = html.lower().rfind("</body>")
    if at < 0:
        return html + tag
    return html[:at] + tag + html[at:]


def _install_tab_alive(app: Flask, prefixes: list) -> None:
    """3機能の画面(HTML)に `static/js/tab_alive.js` を差し込む。

    **3機能とも印刷は別のタブで開く**(梱包明細の帳票・ペナラベルの印刷ビュー・
    資材計算のチェックリストと発注票)。統合画面のタブを閉じても、開いたままの
    帳票があるあいだはサーバを止めない ── 帳票は紙面で書き足した値を保存し、
    発注票はサーバが覚えている中身から組むため。

    スクリプトは**別のタブ(一番上の窓)で開いたときだけ**心拍を送り、統合画面の
    中(iframe)では何もしない。画面にも紙面にも何も描かないので、印刷の見た目は
    変わらない。各機能のコードは触らずに、ここで差し込む。
    """
    roots = tuple(p.rstrip("/") for p in prefixes if p)

    @app.after_request
    def _tab_alive(response):                   # noqa: ANN202 - Flaskのフック
        if (request.method != "GET" or response.status_code != 200
                or response.mimetype != "text/html"
                or response.direct_passthrough or not response.is_sequence):
            return response
        path = request.path
        if not any(path == r or path.startswith(r + "/") for r in roots):
            return response
        # ペナラベルの「中身だけ返す」要求(統合画面の中で差し替える断片)
        if request.args.get("pane") == "1":
            return response
        key = next(r for r in roots if path == r or path.startswith(r + "/")).lstrip("/")
        tag = ('<script src="%s" data-key="%s" data-alive-ms="%d" defer></script>'
               % (url_for("static", filename=TAB_ALIVE_MARK), key, idle_exit.HEARTBEAT_MS))
        html = response.get_data(as_text=True)
        injected = inject_tab_alive(html, tag)
        if injected is not html:
            response.set_data(injected)
        return response


# ------------------------------------------------------------------
# エラーの後追い(統合 1.0.12。現場の指摘: エラー等の後追いが現状できない。
# ログを残し、なぜなぜで分析できるようにしておいてほしい)
# ------------------------------------------------------------------
#: 数秒ごとに来る要求(心拍・接続確認・進み具合)。行は DEBUG にする(INFO だと埋もれる)
QUIET_TAILS = ("/api/health", "/api/alive", "/api/progress", "/api/client-log")
QUIET_PARTS = ("/api/screen/", "/static/")

#: 画面のエラーを受ける上限(1分あたり・1件の大きさ)。壊れた画面が送り続けても溢れない
CLIENT_LOG_PER_MIN = 60
CLIENT_LOG_MAX_BYTES = 16 * 1024

ERROR_REPORT_MARK = "js/error_report.js"


def _quiet(path: str) -> bool:
    return path.endswith(QUIET_TAILS) or any(p in path for p in QUIET_PARTS)


def _shown_message(response) -> str:
    """断り・失敗の応答で、画面に出す文言(JSON の message / error.message)。"""
    if response.mimetype != "application/json" or response.direct_passthrough:
        return ""
    try:
        body = response.get_json(silent=True)
    except Exception:                               # noqa: BLE001
        return ""
    if not isinstance(body, dict):
        return ""
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    return str(body.get("message") or err.get("message") or "")[:300]


def _wants_json() -> bool:
    accept = request.accept_mimetypes
    return ("/api/" in request.path or request.method != "GET"
            or (accept.accept_json and not accept.accept_html))


def _install_error_trail(app: Flask) -> None:
    """要求ごとの印・行・断りの文言、止まった処理のエラーの記録。

    - **要求の印**(`R-xxxxxx`)を付ける。この要求の中の行には `[要求 R-…]` が付く
      (`logging_utils._ContextFilter`)。1つの操作の流れを、並んで来る要求から拾える
    - 要求を1行ずつ残す(何を・どれだけ掛かって・どう答えたか)。ペナラベルは移植元が
      自分で残すので、ここでは残さない(二重にしない)
    - **断り・失敗(400 番台・500 番台)は、画面に出した文言も残す** ── 「画面に何と
      出ていたか」は、後から追うとき最初に要る
    - 止まった処理(予期しない例外)は、**エラーの記録**を作り(`common/incidents.py`)、
      画面へエラー番号を返す。以前は Flask の既定の 500(英語の1行)で、画面には
      「通信に失敗しました (HTTP 500)」としか出ず、ログのどこを見ればよいか分からなかった
    """
    @app.before_request
    def _mark():                                    # noqa: ANN202 - Flaskのフック
        g.request_id = "R-" + secrets.token_hex(3).upper()
        g.started = time.monotonic()
        return None

    @app.after_request
    def _trail(response):                           # noqa: ANN202 - Flaskのフック
        path = request.path
        ms = round((time.monotonic() - getattr(g, "started", time.monotonic())) * 1000)
        where = incidents.mask_url(request.full_path.rstrip("?"))
        if not (path == "/pena" or path.startswith("/pena/")):
            log.log(logging.DEBUG if _quiet(path) else logging.INFO,
                    '"%s %s" %s %sms', request.method, where, response.status_code, ms)
        if response.status_code >= 400 and not getattr(g, "error_id", ""):
            shown = _shown_message(response)
            if shown or response.status_code >= 500:
                log.warning("断り・失敗 %s %s %s: %s", response.status_code, request.method,
                            where, shown or "(文言なし)")
        return response

    @app.errorhandler(Exception)
    def _unexpected(exc):                           # noqa: ANN202 - Flaskのフック
        if isinstance(exc, HTTPException):
            return exc
        label = incidents._module_label(request.path)
        title = f"{label} {request.method} {incidents.mask_url(request.path)}: " \
                f"{type(exc).__name__}: {exc}"
        eid = incidents.new_id()
        message = server_error_message(eid)
        incidents.record("server", title[:300], shown=message, exc=exc, eid=eid)
        g.error_id = eid
        if _wants_json():
            return jsonify({"ok": False, "reason": "internal_error", "message": message,
                            "error": {"code": "internal_error", "message": message},
                            "error_id": eid}), 500
        page = ("<!doctype html><meta charset='utf-8'><title>エラー</title>"
                "<div style='font-family:Meiryo UI,sans-serif;padding:24px;line-height:1.8'>"
                "<h2 style='margin:0 0 8px'>画面を作る途中でエラーが起きました</h2>"
                f"<p>{escape(message)}</p>"
                "<p><a href='javascript:location.reload()'>読み込み直す</a></p></div>")
        return current_app.response_class(page, status=500, mimetype="text/html")


def server_error_message(eid: str) -> str:
    """止まった処理の、画面に出す文言。**エラー番号を必ず付ける**(現場の人が伝えられるように)。"""
    if not eid:
        return "処理中にエラーが起きました。"
    return (f"処理中にエラーが起きました(エラー番号 {eid})。同じ操作をもう一度しても"
            "だめなときは、この番号を伝えてください(上の帯の「ログ」で中身を見られます)。")


def _install_error_report(app: Flask, prefixes: list) -> None:
    """3機能の画面(HTML)に `static/js/error_report.js` を差し込む。

    画面の JavaScript が止まると、**押しても何も起きない**だけで、どこにも残らなかった。
    スクリプトは画面のエラー(例外・読み込めないファイル・届かなかった通信)を
    `/api/client-log` へ送り、エラーの記録の番号を画面の隅に出す。各機能のコードは
    触らずに、ここで差し込む(`tab_alive.js` と同じ)。統合画面の中(iframe)でも動く。
    """
    roots = tuple(p.rstrip("/") for p in prefixes if p)

    @app.after_request
    def _error_report(response):                    # noqa: ANN202 - Flaskのフック
        if (request.method != "GET" or response.status_code != 200
                or response.mimetype != "text/html"
                or response.direct_passthrough or not response.is_sequence):
            return response
        path = request.path
        if not any(path == r or path.startswith(r + "/") for r in roots):
            return response
        if request.args.get("pane") == "1":
            return response
        html = response.get_data(as_text=True)
        if ERROR_REPORT_MARK in html:
            return response
        key = next(r for r in roots if path == r or path.startswith(r + "/")).lstrip("/")
        tag = ('<script src="%s" data-key="%s" data-token="%s" defer></script>'
               % (url_for("static", filename=ERROR_REPORT_MARK), key,
                  escape(current_app.config["TOKEN"])))
        at = html.lower().rfind("</body>")
        response.set_data(html + tag if at < 0 else html[:at] + tag + html[at:])
        return response


# ------------------------------------------------------------------
# 準備中の入口
# ------------------------------------------------------------------
#: 待機画面の「待たずに使い始める」の行き先(準備中でも統合画面を出す)
SKIP_WAIT_URL = "/?go=1"


def _install_boot_redirect(app: Flask, modules: list) -> None:
    """準備中に機能の入口(`/details/` など)を開いたら、統合アプリの待機画面へ回す。

    移植元の各機能は入口で自分の待機画面を出していた。統合版でそのまま出すと、
    待機画面が問い合わせる `/api/health` は統合アプリのもので、アプリの ID が
    食い違い「別のアプリが同じポートを使っています」と出て止まっていた(統合版で直した)。
    待機画面は統合アプリの1つにまとめる。準備が終わったあとは各機能の入口のまま。

    **入口が業務画面そのもの**の機能(ペナラベルの `/pena/`)は回さない ──
    「待たずに使い始める」で入った統合画面の枠がそこを開くため。
    """
    roots = set()
    for m in modules:
        prefix = (m.get("prefix") or "").rstrip("/")
        if prefix and (m.get("home") or "").rstrip("/") != prefix:
            roots.update({prefix, prefix + "/"})

    @app.before_request
    def _to_boot_screen():                      # noqa: ANN202 - Flaskのフック
        if request.path in roots and not current_app.config.get("READY"):
            return redirect("/")
        return None


# ------------------------------------------------------------------
# 機能の停止口
# ------------------------------------------------------------------
def _install_shutdown_guard(app: Flask, modules: list) -> None:
    """機能の停止口(`/material/api/shutdown` など)にも、取り込み中の確認を掛ける。

    機能の停止口は移植元のままで、呼ばれると**プロセスごと**止める ── 統合版では
    3機能とも終わる。統合アプリの停止口(`/api/shutdown`)は、資材計算の取り込みの
    途中なら 409 で断るが、機能の停止口はその確認を飛ばしていた(資材計算の帯の
    「終了」がこれを呼ぶ。移植漏れの点検で見つかった)。取り込みの途中で止めると、
    DBが中途半端な状態で残る。`{"force": true}` なら中断してでも止める(同じ決まり)。

    トークンの確認は機能の側に任せる(ここではトークンが正しいときだけ断る。
    知らない相手に「何が走っているか」を返さない)。
    """
    paths = {(m.get("prefix") or "").rstrip("/") + "/api/shutdown"
             for m in modules if m.get("prefix")}

    @app.before_request
    def _guard_module_shutdown():               # noqa: ANN202 - Flaskのフック
        if request.method != "POST" or request.path not in paths:
            return None
        if not security.token_ok(current_app.config["TOKEN"]):
            return None
        body = request.get_json(silent=True) or {}
        running = _busy_labels()
        if running and not body.get("force"):
            log.info("機能の停止口を断りました(実行中: %s)", ", ".join(running))
            return jsonify({"ok": False, "stopped": False, "reason": "busy", "running": running,
                            "message": "実行中の処理があります: " + "、".join(running)
                                       + "。終わってから終了してください"}), 409
        return None


# ------------------------------------------------------------------
# 統合画面と、統合アプリ自身の経路
# ------------------------------------------------------------------
def _shell_blueprint() -> Blueprint:
    bp = Blueprint("shell", __name__)

    @bp.get("/")
    def index():
        """準備が終わるまでは起動待機画面、終わっていれば統合画面。

        **`?go=1` なら準備中でも統合画面を出す**(待機画面の「待たずに使い始める」)。
        移植元の梱包明細は、取り込み中でも業務画面へ入れた(共有が遅い端末で
        最大 150 秒待たせないため)。統合画面は準備完了を待つので、印が無いと
        押しても同じ待機画面に戻っていた(統合版で直した)。3機能の画面そのものは
        準備完了を見ないので、そのまま使える。
        """
        conf = current_app.config
        if not conf["READY"] and request.args.get("go") != "1":
            return boot_screen.render(
                display_name=conf["DISPLAY_NAME"],
                version_label=app_config.version_label(),
                token=conf["TOKEN"],
                app_id=conf["APP_ID"],
                poll_ms=app_config.job_poll_ms(),
                home_url=SKIP_WAIT_URL,
            )
        return render_template(
            "index.html",
            display_name=conf["DISPLAY_NAME"],
            version=conf["VERSION"],
            version_label=app_config.version_label(),
            token=conf["TOKEN"],
            tabs=conf["MODULES"],
            server_pid=os.getpid(),
            alive_poll_ms=idle_exit.HEARTBEAT_MS,
            health_poll_ms=app_config.health_poll_seconds() * 1000,
        )

    @bp.get("/api/health")
    def health():
        """起動確認と生存監視。**`app_id` を返すのが要点**(基盤仕様書 2.3)。

        業務データは含めないので、トークン無しで答えてよい。
        3機能の身元(表示名・版)も並べる ── 「入れ替えたのに古いまま」を
        調べるとき、どの機能のどの版が動いているかが1回で分かる。
        """
        conf = current_app.config
        return jsonify({
            "app_id": conf["APP_ID"],
            "display_name": conf["DISPLAY_NAME"],
            "version": conf["VERSION"],
            # 統合ツールの版と3機能の版(分けて持つ)。入れ替え判定はこの組で比べる
            "versions": conf["VERSIONS"],
            "version_set": conf["VERSION_SET"],
            "version_problem": app_config.version_problem(),
            "app_root": str(app_config.APP_ROOT),
            "mode": conf["MODE"],
            "port": conf["PORT"],
            "pid": os.getpid(),
            "ready": bool(conf["READY"]),
            "stage": conf["STAGE"],
            "stage_key": conf.get("STAGE_KEY", "prepare"),
            "startup_error": conf["STARTUP_ERROR"],
            "uptime_sec": round(time.time() - conf["STARTED_AT"], 1),
            # 待機画面が読む鍵。統合アプリ自身に長時間処理は無い
            "job": None,
            "modules": [{"key": m["key"], "label": m["label"],
                         "display_name": m["display_name"], "version": m["version"],
                         "ported_from": m["ported_from"],
                         "home": m["home"]} for m in conf["MODULES"]],
        })

    @bp.get("/favicon.ico")
    def favicon():
        """ブラウザが勝手に取りに来るアイコン。**中身なし(204)で答える。**

        ペナラベルの移植元が根でこう答えていた。統合版ではペナラベルが `/pena` の
        下に移ったので、根で答えないと、アイコンを指定していない画面(印刷ビュー・
        帳票)を別のタブで開くたびに 404 がコンソールに出る。
        """
        return current_app.response_class(b"", status=204, mimetype="image/x-icon")

    @bp.post("/api/alive")
    def alive():
        """統合画面の心拍(基盤仕様書 2.8「自動終了」)。**トークンは要らない。**

        3機能の画面もそれぞれ自分の心拍を同じ見張りへ送る。ここは統合画面の
        外枠のぶん ── 中の画面が1つも動いていなくても、外枠が開いている
        あいだは終わらない。`leaving=true` はタブを閉じた合図(`sendBeacon`)。
        `state` は前(`visible`)か裏(`hidden`)か。裏では心拍が間引かれるので、
        裏だと言ってきたら心拍の途切れで終わらない。
        `client` は画面の名乗り(開くたびに新しい名前)。見張りは名乗りごとに
        数えるので、別のタブ(ペナラベルの印刷ビュー等)が開いていれば終わらない。
        """
        body = request.get_json(silent=True) or {}
        leaving = bool(body.get("leaving"))
        state = str(body.get("state", ""))
        hidden = {"hidden": True, "visible": False}.get(state)
        reason = str(body.get("reason", ""))[:20]
        # 画面の名乗り。見張りは画面ごとに生き死にを持つ(別タブを巻き添えにしない)
        client = str(body.get("client", ""))
        if reason and reason not in ("timer", "focus"):
            who = "統合画面" if not client or client.startswith("shell-") else f"別タブ {client}"
            log.info("%sの心拍: %s (%s)", who, reason, state or "-")
        # 同じタブの前の名乗り(タブが捨てられて読み直されたとき。`IdleWatch.forget`)
        replaces = str(body.get("replaces", ""))
        watching = idle_exit.signal(client=client, leaving=leaving, hidden=hidden,
                                    replaces=replaces)
        return jsonify({"ok": True, "pid": os.getpid(), "watching": watching})

    @bp.post("/api/shutdown")
    def shutdown():
        """安全な停止(基盤仕様書 2.8)。トークン必須。

        資材計算の取り込みが走っている間は、既定では止めない(409)。
        `{"force": true}` で中断してでも止める(`stop.bat --force`)。
        """
        if not security.token_ok(current_app.config["TOKEN"]):
            return security.error_json("bad_token", "この画面は無効になりました", 401)
        body = request.get_json(silent=True) or {}
        running = _busy_labels()
        if running and not body.get("force"):
            return jsonify({"stopped": False, "reason": "busy", "running": running,
                            "message": "実行中の処理があります: " + ", ".join(running)}), 409
        if _shutdown_hook is None:
            return jsonify({"stopped": False, "reason": "no_hook",
                            "message": "このプロセスは停止操作に対応していません"}), 501
        log.info("停止要求を受け付けました")
        threading.Timer(SHUTDOWN_DELAY_SEC, _shutdown_hook).start()
        return jsonify({"stopped": True, "message": "終了します"})

    _log_routes(bp)
    return bp


# ------------------------------------------------------------------
# ログとエラーの記録(上の帯の「ログ」)
# ------------------------------------------------------------------
_client_log_times: list = []
_client_log_lock = threading.Lock()


def _need_token():
    if not security.token_ok(current_app.config["TOKEN"]):
        return security.error_json("bad_token", "この画面を開き直してください", 403)
    return None


def _log_routes(bp: Blueprint) -> None:
    @bp.get("/log")
    def log_page():
        """ログとエラーの記録。統合画面の上の帯の「ログ」から開く(中に埋め込む)。"""
        conf = current_app.config
        return render_template("log.html", display_name=conf["DISPLAY_NAME"],
                               token=conf["TOKEN"], embed=request.args.get("embed") == "1")

    @bp.post("/api/client-log")
    def client_log():
        """画面(ブラウザ)で起きたエラー(`static/js/error_report.js`)。

        例外・読み込めないファイル → **エラーの記録**を作って番号を返す(同じものは1件に
        まとめる)。届かなかった通信(サーバが止まっていた)→ ログに1行(つながったあとで届く)。
        """
        deny = _need_token()
        if deny is not None:
            return deny
        if (request.content_length or 0) > CLIENT_LOG_MAX_BYTES:
            return security.error_json("too_large", "大きすぎます", 413)
        now = time.monotonic()
        with _client_log_lock:
            _client_log_times[:] = [t for t in _client_log_times if now - t < 60]
            if len(_client_log_times) >= CLIENT_LOG_PER_MIN:
                return security.error_json("too_many", "しばらく受けません", 429)
            _client_log_times.append(now)
        body = request.get_json(silent=True) or {}
        kind = str(body.get("kind", "error"))[:20]
        message = str(body.get("message", ""))[:500]
        source = incidents.mask_url(str(body.get("source", "")))[:300]
        page = incidents.mask_url(str(body.get("page", "")))[:300]
        module = {"details": "梱包明細", "pena": "ペナラベル", "material": "資材計算",
                  "shell": "統合画面"}.get(str(body.get("module", "")), "")
        where = f"{source}:{body.get('line', '')}:{body.get('col', '')}" if source else ""
        if kind == "offline":
            log.warning("画面から: サーバに届かなかった通信 %s(%s 回。%s)", source or page,
                        body.get("count", 1), message)
            return jsonify({"ok": True})
        facts = {"機能": module, "画面": page, "操作": str(body.get("action", ""))[:200],
                 "例外": message, "場所": where, "スタック": str(body.get("stack", ""))[:4000],
                 "ブラウザ": request.headers.get("User-Agent", "")[:200],
                 "要求の印": getattr(g, "request_id", "")}
        title = f"{module or '画面'}: {message}"[:300]
        shown = "画面でエラーが起きました(記録しました)"
        eid = incidents.record("screen", title, shown=shown, facts=facts,
                               dedup_key=f"{kind}|{module}|{message}|{source}")
        g.error_id = eid
        return jsonify({"ok": True, "error_id": eid})

    @bp.get("/api/log/status")
    def log_status():
        deny = _need_token()
        if deny is not None:
            return deny
        return jsonify({"ok": True, **logging_utils.status()})

    @bp.post("/api/log/settings")
    def log_settings():
        """出力先・残す日数。`action` = check(確かめるだけ)/ save / reset(既定へ)。

        **このPCに保存**(`common/local_settings.py`)。保存したらすぐ切り替える(再起動不要)。
        """
        deny = _need_token()
        if deny is not None:
            return deny
        body = request.get_json(silent=True) or {}
        action = str(body.get("action", "save"))
        text = str(body.get("log_dir", "") or "").strip()
        if action == "reset":
            text = ""
        if len(text) > 400:
            return security.error_json("bad_input", "長すぎます", 400)
        target = logging_utils.target_for(text) if text else logging_utils.default_log_dir()
        problem = logging_utils.writable_problem(target)
        if action == "check":
            return jsonify({"ok": not problem, "target": str(target),
                            "message": problem or f"書けます。ここへ出します: {target}"})
        if problem:
            return jsonify({"ok": False, "target": str(target),
                            "message": problem + "。保存しませんでした"}), 400
        keep = body.get("keep_days")
        try:
            if keep not in (None, ""):
                keep = int(keep)
                if not (local_settings.LOG_KEEP_DAYS_MIN <= keep
                        <= local_settings.LOG_KEEP_DAYS_MAX):
                    return security.error_json(
                        "bad_input", f"残す日数は {local_settings.LOG_KEEP_DAYS_MIN}〜"
                        f"{local_settings.LOG_KEEP_DAYS_MAX} 日です", 400)
            local_settings.save(local_settings.KEY_LOG_DIR, text)
            if keep not in (None, ""):
                local_settings.save(local_settings.KEY_LOG_KEEP_DAYS,
                                    None if keep == local_settings.LOG_KEEP_DAYS_DEFAULT
                                    else keep)
        except ValueError:
            return security.error_json("bad_input", "残す日数は数字で入れてください", 400)
        except OSError as exc:
            return jsonify({"ok": False, "message": f"設定を書けませんでした: {exc}"}), 500
        state = logging_utils.apply_settings()
        log.info("ログの設定を保存しました: 出力先=%s 残す日数=%s", text or "(このPCの既定)",
                 state["keep_days"])
        return jsonify({"ok": True, "message": f"保存しました。ここへ出します: {state['dir']}",
                        **state})

    @bp.get("/api/log/incidents")
    def log_incidents():
        deny = _need_token()
        if deny is not None:
            return deny
        return jsonify({"ok": True, "folder": str(incidents.folder()),
                        "items": incidents.list_recent(200)})

    @bp.get("/api/log/incidents/<eid>")
    def log_incident(eid):
        deny = _need_token()
        if deny is not None:
            return deny
        text = incidents.read(eid)
        if text is None:
            return security.error_json("not_found", "その番号の記録はありません", 404)
        return jsonify({"ok": True, "id": eid, "path": str(incidents.folder() / f"{eid}.md"),
                        "text": text})

    @bp.get("/api/log/recent")
    def log_recent():
        """今日のログの終わりのほう。`only=problems` なら警告とエラーだけ。"""
        deny = _need_token()
        if deny is not None:
            return deny
        only = request.args.get("only", "")
        try:
            limit = max(50, min(int(request.args.get("limit", "300")), 2000))
        except ValueError:
            limit = 300
        path = Path(logging_utils.log_path_for(date.today()))
        lines = _tail(path, 512 * 1024)
        if only == "problems":
            lines = [l for l in lines if " | WARNING | " in l or " | ERROR | " in l
                     or " | CRITICAL | " in l]
        return jsonify({"ok": True, "path": str(path),
                        "lines": [incidents.mask_url(l) for l in lines[-limit:]]})


def _tail(path: Path, max_bytes: int) -> list:
    """ファイルの終わりの `max_bytes` を行で(大きなログを全部は読まない)。"""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[1:] if size > max_bytes else lines


def _busy_labels() -> list[str]:
    """いま走っている、途中で止めると困る処理の名前。"""
    labels: list[str] = []
    for module in all_modules():
        try:
            if module.busy():
                names = getattr(module, "busy_labels", None)
                labels.extend(names() if names else [module.LABEL])
        except Exception:                         # noqa: BLE001 - 判定で止めない
            labels.append(module.LABEL)
    return labels


def busy() -> bool:
    return bool(_busy_labels())
