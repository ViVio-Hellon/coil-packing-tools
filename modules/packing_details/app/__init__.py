"""梱包明細の Flask 部品の組み立て(統合版)

移植元は `create_app()` で Flask アプリを1つ組み立てていた。統合版では
**3機能が1つの Flask アプリに同居する**ので、ここは機能ぶんの
Blueprint(入れ子。`details` の下に `health` / `meisai` / `settings` /
`history` / `master`)を組み立て、統合アプリ(`app.py`)に登録する形にした。

    register(app, url_prefix="/details")   統合アプリへ載せる
    create_app(token=...)                  この機能だけの Flask(試験・単体起動)

【移植元から動かしていないもの】
- 経路(`/meisai` `/api/*` `/report/*`)は**機能の入口(`/details`)の下に
  そのまま**。画面のJSは入口を `window.APP.base` で受け取って付ける
- 起動トークン・同一オリジン・画面の持ち主の見張り(`_register_security`)
- 書く要求を1つずつ通す順番待ち(`_WRITE_LOCK`)と、要求ごとのDB接続
- 断りの形(`error_body`)と、書けなかったときの 503

【共通側に移したもの】
- Host 検証・応答ヘッダ・控えの決まり・静的ファイルの版 → `common/security.py`
- 自動終了の見張り → `common/idle_exit.py`(3機能で1つ)
- ログ → `common/logging_utils.py`(1つのファイル)

社内「汎用Webアプリ作成 基盤仕様書」に沿った構成。監視レベルは**1**
(通常の操作アプリ) ── 長時間処理も自動実行も無く、出力は即時で、
ブラウザを閉じたあとに続ける処理が無いため。
"""
from __future__ import annotations

import secrets
import threading
import time
from collections import ChainMap
from pathlib import Path
from typing import Optional

from flask import Blueprint, Flask, current_app, g, jsonify, request

from common import security
from modules.packing_details.meisai import app_config, db, modes, screen
from modules.packing_details.meisai.logging_utils import get_logger

log = get_logger("app")

APP_DIR = Path(__file__).resolve().parent

# 統合アプリの中でのこの機能の名前と入口。**blueprint の名前は固定** ──
# テンプレートが `url_for('details.static', …)` で名指しする
KEY = "details"
DEFAULT_PREFIX = "/details"

# 起動トークンと同一オリジン確認を要求する経路(機能の入口を除いた経路)。
#
# **業務データを返す経路はここに入れる。** 帳票(`/report/*`)を
# 忘れると、Lot番号・品名・寸法・副番が丸ごと素通しになる
# (移植元がそれを踏んでいる)。画面のHTML(`/meisai`)を入れないのは、
# ブラウザのアドレス欄から開く経路だから ── 中身は空の器で、
# 業務データは `/api/*` から取る。
TOKEN_REQUIRED_PREFIXES = ("/api/", "/report/")

# `/api/*` のうち、起動トークンを要求しないもの。
#
# `/api/health` を素通しにするのは、**まだトークンを知らない相手**が
# 正当に問い合わせる場面があるため(多重起動の判定・起動待機画面)。
# 返すのは識別情報と状態だけで、業務データは含まない。
# `/api/alive`(心拍)も同じ ── トークンを要求すると、切れた画面が
# 黙って死んだ扱いになり、開いているのに終了してしまう。
TOKEN_EXEMPT_PATHS = frozenset({"/api/health", "/api/alive"})

# 画面の持ち主であることまで要求する経路。
#
# **作業状態はプロセスに1つしかない**(`session.py`)ので、タブを2枚
# 開くと同じ盤面を奪い合う。開くところで断ってはいるが、断りを
# すり抜けた画面(引き継がれた古いタブ)がそのまま操作できると、
# 画面に出ている値と実際の盤面が食い違う。**ここが本当の歯止め。**
#
# 除くのは2つだけ。
#
#   `/api/screen/*`  持ち主を変える口そのもの。ここで断ると
#                    引き継げなくなる
#   `/report/*`      **帳票は別のタブで開く。** 印刷は
#                    `window.open` で新しいタブに出すので、その
#                    タブは画面を持っていない。ここで断ると
#                    印刷そのものができなくなる。盤面は触らず、
#                    DBから引いて組むだけ(トークンは要る)。
#                    紙面で書き足したサイズ・LOTNOもここへ返って
#                    くる(`/report/<lot>/edits`)。触るのは出力済みの
#                    明細の書き足し欄だけで、盤面には触らない
SCREEN_EXEMPT_PATHS = frozenset({
    "/api/screen/claim", "/api/screen/take-over", "/api/screen/release",
})
SCREEN_EXEMPT_PREFIXES = ("/report/",)


def module_conf() -> dict:
    """この機能の固有値(表示名・版・アプリID・モード)。

    統合アプリの `app.config` には統合アプリ自身の値が入っているので、
    機能の中の経路はこちらを見る。**プロセスに1つのもの**(トークン・
    準備完了・段・ポート)は統合アプリの値がそのまま見える(`ChainMap`)。
    """
    return {
        "MODE": modes.MEISAI,
        "APP_ID": app_config.app_id(),
        "VERSION": app_config.version(),
        "DISPLAY_NAME": app_config.display_name(),
    }


def conf() -> ChainMap:
    """いまの要求から見た設定。機能の固有値 → 統合アプリの値の順に引く。"""
    return ChainMap(security.find_module_conf(KEY), current_app.config)


def base() -> str:
    """この機能の入口(`/details`)。単体で動かしているときは空文字。"""
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
    _register_db(bp, prefix)
    _register_routes(bp)
    return bp


def register(app: Flask, url_prefix: str = DEFAULT_PREFIX) -> Blueprint:
    """統合アプリへ載せる。"""
    bp = build_blueprint(url_prefix)
    app.register_blueprint(bp)
    log.info("梱包明細を載せました: %s (版 %s)", url_prefix or "/", app_config.version())
    return bp


def create_app(mode: str = modes.MEISAI, *,
               token: Optional[str] = None,
               port: Optional[int] = None) -> Flask:
    """この機能だけの Flask を1つ組み立てる(試験・単体起動用)。

    経路は入口なし(`/meisai` `/api/*`)で、移植元の `create_app` と同じ形。
    `token` を省略すると起動ごとに新しく作る。試験からは固定値を渡せる。
    """
    app = Flask(__name__, static_folder=None, template_folder=None)
    app.config.update(
        MODE=modes.normalize(mode),
        TOKEN=token or secrets.token_urlsafe(32),
        PORT=port or app_config.port(modes.MEISAI),
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

    @app.errorhandler(404)
    def _not_found(_e):                         # noqa: ANN202 - Flaskのフック
        return jsonify(error_body("not_found", "ページが見つかりません")), 404

    log.info("create_app: port=%s version=%s", app.config["PORT"],
             app.config["VERSION"])
    return app


# ------------------------------------------------------------------
# セキュリティ
# ------------------------------------------------------------------
def _register_security(bp: Blueprint, prefix: str) -> None:
    @bp.before_request
    def _check_request():                       # noqa: ANN202 - Flaskのフック
        # Host 検証は統合アプリ側(`common.security.install_common`)が
        # 全経路に当てている
        path = security.relative_path(prefix)
        if not any(path.startswith(p) for p in TOKEN_REQUIRED_PREFIXES):
            return None

        # --- 同一オリジンの確認 ---
        if not security.same_origin_ok():
            log.warning("別オリジンからの要求を拒否: %s %s",
                        request.headers.get("Sec-Fetch-Site"), request.path)
            return jsonify(error_body("cross_origin",
                                      "別のページからは利用できません")), 403

        # --- 起動トークン ---
        if path in TOKEN_EXEMPT_PATHS:
            return None
        if not security.token_ok(current_app.config["TOKEN"]):
            log.warning("トークン不一致で拒否: %s", request.path)
            return jsonify(error_body(
                "bad_token",
                "この画面は無効になりました。アプリを開き直してください")), 401

        # --- 画面の持ち主 ---
        # **持っていない画面には触らせない。** 引き継がれた古いタブが
        # 操作を続けると、そのタブに出ている値(ロット番号・条番号)は
        # もう別のロットのものになっている
        if (path in SCREEN_EXEMPT_PATHS
                or any(path.startswith(p) for p in SCREEN_EXEMPT_PREFIXES)):
            return None
        screen_id = request.headers.get("X-Tool-Screen", "")
        if not screen.beat(screen_id):
            log.warning("画面を持っていない要求を拒否: %s", request.path)
            return jsonify(error_body(
                "screen_taken",
                "この画面はもう使われていません。"
                "同じアプリを2枚開いたときは、"
                "あとから引き継いだほうだけが使えます")), 409
        return None


def error_body(code: str, message: str, field: str = "") -> dict:
    """エラー応答の形。**文言はサーバが持つ。**"""
    body = {"code": code, "message": message}
    if field:
        body["field"] = field
    return {"error": body}


# ------------------------------------------------------------------
# DB接続
# ------------------------------------------------------------------
# **書く要求は1つずつ通す。**
#
# waitress はスレッドプールで動くので要求は同時に走る。読むだけなら
# WAL があるので困らないが、書くほうが重なると
#   ・連番を2枚に同じ番号で付けてしまう
#   ・同じ行を2か所から書いて、後から書いたほうが黙って勝つ
#   ・sqlite3 の書き込みロックに当たって `database is locked`
# が起きる。この道具は1台のPCを1人が使う前提なので、書く要求が
# 重なるのは押し間違いか二重送信で、待たせても誰も困らない。
#
# **この順番待ちは梱包明細の中だけ。** 資材計算・ペナラベルの書き込みは
# 別のDBなので、ここで待たせる理由が無い(待たせると、梱包明細の出力の
# あいだ資材計算が固まる)。
_WRITE_LOCK = threading.RLock()

# 直列化しないもの。**待たせてはいけない**種類の POST。
#   /api/alive    … 心拍。待たせると自動終了が誤る
#   /api/history/ … 明細の履歴。共有を待つことがあり、そのあいだ出力を
#                   止めてしまう。手元のDBは自分の接続で SQLite のロックを取る
#   /api/master/  … マスタ管理(見るだけ)。同じく共有を待つことがある。
#                   手元のDBには書かない(履歴を送るのは履歴の送り手)
_NO_LOCK_PREFIXES = ("/api/alive", "/api/history/", "/api/master/")


def _is_write(req, prefix: str = "") -> bool:
    """その要求は「書く」か。**方法だけで決める** ── 経路ごとの表を
    持つと、画面を1つ足すたびに更新が要り、忘れたぶんだけ穴が開く。
    """
    if req.method in ("GET", "HEAD", "OPTIONS"):
        return False
    return not security.relative_path(prefix, req).startswith(_NO_LOCK_PREFIXES)


def _register_db(bp: Blueprint, prefix: str) -> None:
    """リクエストごとに1本開いて、終わったら閉じる。

    waitress はスレッドプールで動くため、`sqlite3` の接続をプロセス
    全体で共有できない(既定で `check_same_thread=True`)。常駐接続を
    持たないので、長時間放置してもハンドルやロックが滞留しない。
    """

    @bp.before_request
    def _take_write_lock():                     # noqa: ANN202 - Flaskのフック
        if _is_write(request, prefix):
            _WRITE_LOCK.acquire()
            g.holds_write_lock = True

    # `teardown_request` は登録の逆順に呼ばれる。**接続を閉じるより先に
    # 放す**ために、閉じるほうを先に登録する
    @bp.teardown_request
    def _close_db(_exc):                        # noqa: ANN202 - Flaskのフック
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @bp.teardown_request
    def _release_write_lock(_exc):              # noqa: ANN202 - Flaskのフック
        if g.pop("holds_write_lock", False):
            _WRITE_LOCK.release()


def get_db():
    """このリクエスト用のDB接続。`app` の外からは呼ばない。"""
    if "db" not in g:
        g.db = db.get_connection()
    return g.db


# ------------------------------------------------------------------
# ルーティング
# ------------------------------------------------------------------
def _register_routes(bp: Blueprint) -> None:
    from .routes import health, history, master, meisai, settings

    bp.register_blueprint(health.bp)
    bp.register_blueprint(meisai.bp)
    bp.register_blueprint(settings.bp)
    bp.register_blueprint(history.bp)
    bp.register_blueprint(master.bp)

    @bp.errorhandler(db.WriteError)
    def _write_failed(exc):                     # noqa: ANN202 - Flaskのフック
        """書き込めなかった。**500 にしない。**

        「処理できませんでした」だけ出ると、何を直せばよいか分からない。
        原因(ほかのプログラムが使っている)と、次にすること(少し待って
        もう一度)を文言で渡す。**書けていないことは確かなので、
        画面は「できた」と思ってはいけない。**
        """
        log.warning("書き込みに失敗: %s %s", request.path, exc)
        return jsonify(error_body("write_failed", str(exc))), 503
