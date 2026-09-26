"""統合 Flask アプリの組み立て ── 3機能を1つのサーバに載せる

    /                      統合画面(3つのタブ。中身は各機能の画面を iframe で出す)
    /api/health            起動確認(統合アプリの身元。多重起動の判定・待機画面が見る)
    /api/alive             統合画面の心拍(自動終了の見張りへ)
    /api/shutdown          安全な停止(トークン必須)
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

import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from flask import Blueprint, Flask, current_app, jsonify, redirect, render_template, request

from common import app_config, boot_screen, idle_exit, modes, security
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
    security.install_common(app)
    app.register_blueprint(_shell_blueprint())

    loaded = []
    for module in (modules if modules is not None else all_modules()):
        module.register(app)
        loaded.append({
            "key": module.KEY, "label": module.LABEL, "prefix": module.PREFIX,
            "home": module.HOME, "display_name": module.display_name(),
            "version": module.version(),
        })
    app.config["MODULES"] = loaded

    @app.errorhandler(404)
    def _not_found(_e):                         # noqa: ANN202 - Flaskのフック
        return jsonify({"error": {"code": "not_found",
                                  "message": "ページが見つかりません"}}), 404

    log.info("create_app: port=%s version=%s 機能=%s", app.config["PORT"],
             app.config["VERSION"], ", ".join(m["key"] for m in loaded))
    return app


# ------------------------------------------------------------------
# 統合画面と、統合アプリ自身の経路
# ------------------------------------------------------------------
def _shell_blueprint() -> Blueprint:
    bp = Blueprint("shell", __name__)

    @bp.get("/")
    def index():
        """準備が終わるまでは起動待機画面、終わっていれば統合画面。"""
        conf = current_app.config
        if not conf["READY"]:
            return boot_screen.render(
                display_name=conf["DISPLAY_NAME"],
                version_label=app_config.version_label(),
                token=conf["TOKEN"],
                app_id=conf["APP_ID"],
                poll_ms=app_config.job_poll_ms(),
                home_url="/",
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
                         "home": m["home"]} for m in conf["MODULES"]],
        })

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
            log.info("統合画面の心拍: %s (%s)", reason, state or "-")
        watching = idle_exit.signal(client=client, leaving=leaving, hidden=hidden)
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

    return bp


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
