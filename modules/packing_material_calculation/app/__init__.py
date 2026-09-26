"""資材計算の Flask 部品の組み立て(統合版)

移植元は `create_app()` で Flask アプリを1つ組み立てていた。統合版では
**3機能が1つの Flask アプリに同居する**ので、ここは機能ぶんの
Blueprint(入れ子。`material` の下に `health` / `calc` / `checklist` /
`order` / `master` / `settings` / `screen`)を組み立て、統合アプリ
(`app.py`)に登録する形にした。

    register(app, url_prefix="/material")  統合アプリへ載せる
    create_app(token=...)                  この機能だけの Flask(試験・単体起動)

【移植元から動かしていないもの】
- 経路(`/calc` `/api/*` `/report/*`)は**機能の入口(`/material`)の下に
  そのまま**。画面のJSは入口を `window.APP_BASE` で受け取って付ける
- 起動トークン・準備前の 503・画面の持ち主の見張り(`_register_security`)
- 要求ごとのDB接続。断りの形(`ok` / `reason`)

【共通側に移したもの】
- Host 検証・同一オリジン・応答ヘッダ・控えの決まり → `common/security.py`
  (移植元には Host 検証と同一オリジンの確認が無かった。梱包明細のものを当てた)
- 自動終了の見張り → `common/idle_exit.py`(3機能で1つ)
- ログ → `common/logging_utils.py`(1つのファイル)

【守っている約束】(移植元のまま)
1. 業務判断はすべて Python。JS はビューモデル → DOM の変換だけ
2. API は更新後のビューモデル一式を返す(差分ではない)
3. 断りの種類は HTTP ステータスと `reason` 定数で運ぶ
4. 同じ事実を2か所に持たない
"""
from __future__ import annotations

import secrets
import time
from collections import ChainMap
from pathlib import Path
from typing import Optional

from flask import Blueprint, Flask, current_app, g, jsonify, request

from common import security
from modules.packing_material_calculation.coil_tool import app_config, db, modes
from modules.packing_material_calculation.coil_tool.logging_utils import get_logger

from . import screen

log = get_logger("app")

APP_DIR = Path(__file__).resolve().parent

# 統合アプリの中でのこの機能の名前と入口。**blueprint の名前は固定** ──
# テンプレートが `url_for('material.static', …)` で名指しする
KEY = "material"
DEFAULT_PREFIX = "/material"

# 起動トークンと同一オリジン確認を要求する経路(機能の入口を除いた経路)。
#
# **業務データを返す経路はここに入れる。** 帳票(`/report/*`)には LotNo・
# 用途名・寸法・数量が丸ごと入るので、`/api/*` と同じ扱いにする。
TOKEN_REQUIRED_PREFIXES = ("/api/", "/report/")

# `/api/*` のうち、起動トークンを要求しないもの。
#
# `/api/health` … まだトークンを知らない相手が正当に問い合わせる。
# `/api/alive` … 心拍。トークンを要求すると、**トークンが切れた画面が
#   黙って死んだ扱いになり**、開いているのに終了してしまう
# `/api/screen*` … 使ってよい画面の受付。関門そのものに関門を付けると、
#   断られた画面が「なぜ断られたか」も受け取れなくなる
TOKEN_EXEMPT_PATHS = frozenset({
    "/api/health", "/api/alive",
    "/api/screen", "/api/screen/claim", "/api/screen/release",
})

# 画面の取り合いを見ない経路。
#
# **見るのは書くほうだけ。** ここに並ぶのは、画面を持たない相手が正当に
# 叩くもの ── 心拍・起動確認・画面の受付、そして**停止**。
SCREEN_EXEMPT_PATHS = frozenset(TOKEN_EXEMPT_PATHS | {"/api/shutdown"})


def module_conf() -> dict:
    """この機能の固有値(表示名・版・アプリID・モード)。"""
    return {
        "MODE": modes.MAIN,
        "APP_ID": app_config.app_id(),
        "VERSION": app_config.version(),
        "DISPLAY_NAME": app_config.display_name(),
    }


def conf() -> ChainMap:
    """いまの要求から見た設定。機能の固有値 → 統合アプリの値の順に引く。"""
    return ChainMap(security.find_module_conf(KEY), current_app.config)


def base() -> str:
    """この機能の入口(`/material`)。単体で動かしているときは空文字。"""
    return security.module_prefix(KEY)


# ------------------------------------------------------------------
# 組み立て
# ------------------------------------------------------------------
def build_blueprint(url_prefix: str = DEFAULT_PREFIX) -> Blueprint:
    """この機能ぶんの Blueprint(入れ子)を1つ組み立てる。"""
    bp = Blueprint(KEY, __name__,
                   url_prefix=url_prefix or None,
                   template_folder=str(APP_DIR / "templates"),
                   static_folder=str(APP_DIR / "static"),
                   static_url_path="/static")
    bp.module_conf = module_conf()            # type: ignore[attr-defined]
    prefix = url_prefix or ""

    @bp.context_processor
    def _inject_base():                         # noqa: ANN202 - テンプレートに入口を渡す
        return {"base": prefix}

    _register_security(bp, prefix)
    _register_db(bp)
    _register_routes(bp)
    return bp


def register(app: Flask, url_prefix: str = DEFAULT_PREFIX) -> Blueprint:
    """統合アプリへ載せる。"""
    bp = build_blueprint(url_prefix)
    app.register_blueprint(bp)
    log.info("資材計算を載せました: %s (版 %s)", url_prefix or "/", app_config.version())
    return bp


def create_app(mode: str = modes.MAIN, *,
               token: Optional[str] = None,
               port: Optional[int] = None) -> Flask:
    """この機能だけの Flask を1つ組み立てる(試験・単体起動用)。

    経路は入口なし(`/calc` `/api/*`)で、移植元の `create_app` と同じ形。
    `token` を省略すると起動ごとに新しく作る。テストからは固定値を渡せる。
    """
    requested = modes.normalize(mode) or modes.MAIN
    if requested not in modes.KEYS:
        raise ValueError(
            f"未知のモード: {mode!r} (使えるのは {', '.join(modes.KEYS)})")

    app = Flask(__name__, static_folder=None, template_folder=None)
    app.config.update(
        MODE=requested,
        TOKEN=token or secrets.token_urlsafe(32),
        PORT=port or app_config.port(requested),
        APP_ID=app_config.app_id(),
        VERSION=app_config.version(),
        DISPLAY_NAME=app_config.display_name(),
        STARTED_AT=time.time(),
        READY=False,
        STAGE="アプリを準備中",
        STAGE_KEY="prepare",
        STARTUP_ERROR="",
        SECRET_KEY=secrets.token_hex(16),
    )
    security.install_common(app)
    register(app, url_prefix="")
    log.info("create_app: mode=%s port=%s", requested, app.config["PORT"])
    return app


# ------------------------------------------------------------------
# DB ── 要求ごとに取り直す
# ------------------------------------------------------------------
def get_db():
    """この要求ぶんの DB 接続。

    `sqlite3` の接続はスレッドをまたげないので、**要求ごとに取り直す**。
    画面を開いたまま放置されても、ファイルハンドルもロックも残らない。
    """
    if "db" not in g:
        g.db = db.get_connection()
    return g.db


def _register_db(bp: Blueprint) -> None:
    @bp.teardown_request
    def _close_db(_exc) -> None:
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()


# ------------------------------------------------------------------
# 起動トークンと画面の持ち主
# ------------------------------------------------------------------
def _register_security(bp: Blueprint, prefix: str) -> None:
    @bp.before_request
    def _check_token():
        path = security.relative_path(prefix)
        if path in TOKEN_EXEMPT_PATHS:
            return None
        if not path.startswith(TOKEN_REQUIRED_PREFIXES):
            return None

        # 同一オリジンの確認(梱包明細と同じ。統合版で足した)
        if not security.same_origin_ok():
            return jsonify(ok=False, reason="cross_origin",
                           message="別のページからは利用できません"), 403

        if not security.token_ok(current_app.config["TOKEN"]):
            # 401 ではなく 403。相手が誰かは分かっていて、**この経路を
            # 使う資格が無い**という断り方
            return jsonify(ok=False, reason="bad_token",
                           message="この画面を開き直してください"), 403

        # 起動していないうちの業務要求は 503。404 にすると「無い」に見える
        if not current_app.config["READY"] and path.startswith("/api/") \
                and path not in TOKEN_EXEMPT_PATHS:
            return jsonify(ok=False, reason="starting",
                           message="起動しています"), 503
        return None

    @bp.before_request
    def _check_screen():
        """**使ってよい画面からの書き込みだけ通す。**

        画面側の覆い(`app.js`)が最初の関門で、ここは最後の砦。
        覆いは次の心拍(最大20秒)で掛かるので、取り上げられた直後の
        画面がその隙に書けてしまう。同じ作業状態を2枚で奪い合わせない
        ためには、**サーバ側でも断る**必要がある。

        誰も画面を持っていなければ通す ── 塞ぎたいのは「2枚目が
        入力できてしまう」であって、「1枚も開いていない」ではない
        (`--no-browser` で立てておく使い方と試験を巻き添えにしない)。
        """
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        path = security.relative_path(prefix)
        if path in SCREEN_EXEMPT_PATHS or not path.startswith("/api/"):
            return None
        if screen.owns(request.headers.get("X-App-Screen", "")):
            return None
        # 409。**入力は正しく、いまこの画面に資格が無い**という断り方
        return jsonify(ok=False, reason=screen.REFUSE_TAKEN,
                       message="この画面は使われていません。"
                               "別の画面で開いているので、"
                               "そちらを使うか、この画面を開き直してください"), 409


# ------------------------------------------------------------------
# 経路
# ------------------------------------------------------------------
def _register_routes(bp: Blueprint) -> None:
    from .routes import (calc, checklist, health, master, order,
                         screen as screen_routes, settings)

    bp.register_blueprint(health.bp)
    bp.register_blueprint(calc.bp)
    bp.register_blueprint(checklist.bp)
    bp.register_blueprint(order.bp)
    bp.register_blueprint(master.bp)
    bp.register_blueprint(settings.bp)
    bp.register_blueprint(screen_routes.bp)
