# -*- coding: utf-8 -*-
"""ペナラベルを Flask に載せる取り次ぎ(統合版)

移植元は追加ライブラリを使えない前提で、標準ライブラリの
``http.server.ThreadingHTTPServer`` 上に画面(``PageRoutes``)と
API(``ApiRoutes``)を持っていた。統合版は3機能を**1つの Flask サーバ**で
動かすので、HTTP を喋る部分だけをここで Flask に置き換える。

    移植元 server.py の Handler.do_GET / do_POST  →  dispatch()(この中)
    ExclusiveHTTPServer                            →  統合アプリの server.py(waitress)
    AppContext                                     →  そのまま(ここに置く)

**画面と業務(``app/routes`` / ``app/services``)は触っていない。**
``PageRoutes.xxx()`` が HTML 文字列を、``ApiRoutes.handle()`` が
``(status, payload)`` を返す作りだったので、Flask から同じように呼ぶだけで済む。

【トークン】移植元の API にはトークンが無かった。統合版では同じプロセスに
梱包明細・資材計算(どちらもトークン必須)と同居するので、ペナラベルの
``/api/*`` にも同じトークンを要求する(``app.js`` がヘッダに載せる)。
心拍・画面の受付・起動確認は、他の2機能と同じく素通し。

【単体で動かす・試験】``create_server(cfg)`` は移植元と同じ形で
``(httpd, ctx)`` を返す(``werkzeug`` の開発サーバ。試験がそのまま使える)。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from flask import (Blueprint, Flask, Response, current_app, jsonify, request,
                   send_from_directory)

from common import security

from .app.config import load_config
from .app.repositories.material_repo import MaterialRepository
from .app.repositories.sqlite_store import Store
from .app.routes import ApiRoutes, PageRoutes
from .app.services.screen_guard import ScreenGuard
from .app.services.size_master import SizeMaster
from .app.services.workflow import Workflow

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent

#: 統合アプリの中でのこの機能の名前と入口。**blueprint の名前は固定**
KEY = "pena"
DEFAULT_PREFIX = "/pena"

#: 最大リクエストボディ(入力画面しかないので小さくてよい)
MAX_BODY = 1 * 1024 * 1024

#: 要求のログを DEBUG に落とす経路(数秒ごとに来る心拍・接続確認)
QUIET_PATHS = frozenset({
    "/api/health", "/api/screen/ping", "/api/screen/state", "/api/progress",
})

#: `/api/*` のうちトークンを要求しないもの。
#:   /api/health … まだトークンを知らない相手(起動確認)が叩く
#:   /api/screen/* … 画面の受付。`sendBeacon` はヘッダを付けられない
TOKEN_EXEMPT = frozenset({
    "/api/health", "/api/screen/claim", "/api/screen/ping",
    "/api/screen/release", "/api/screen/state",
})


class IntegratedApiRoutes(ApiRoutes):
    """設定を読み直しても、統合アプリが決めた値(ポート・入口)を戻さない。

    移植元の `_adopt` は `load_config()` の結果を丸ごと写す。統合版では
    ポートは統合アプリのもので、`config/app.json` の値ではない。
    """

    _KEEP = ("port", "host")

    def _adopt(self, new_cfg) -> None:
        kept = {name: getattr(self.cfg, name) for name in self._KEEP}
        super()._adopt(new_cfg)
        for name, value in kept.items():
            setattr(self.cfg, name, value)


class AppContext:
    """1 プロセス分の依存をまとめたもの(移植元 `server.AppContext`)。"""

    def __init__(self, cfg, *, load_materials: bool = True):
        self.cfg = cfg
        self.started_at = time.time()
        cfg.ensure_dirs()

        self.store = Store(cfg.db_path,
                           idle_close_sec=cfg.db_idle_close_sec,
                           busy_timeout_ms=cfg.db_busy_timeout_ms)
        self.master = SizeMaster(cfg.size_master_path)
        self.materials = MaterialRepository(
            accdb_dir=cfg.aim_ref_path,
            csv_path=cfg.material_csv_path,
            prefer_access=cfg.prefer_access,
            timeout_sec=cfg.access_timeout_sec,
            refresh_sec=cfg.material_refresh_sec,
            db_path=cfg.material_db_file)
        self.wf = Workflow(self.store, self.master, self.materials, cfg)
        #: 入力画面を 1 つに限る(タブを 2 枚開かせない)
        self.screens = ScreenGuard()
        self.pages = PageRoutes(self.wf, cfg, started_at=self.started_at,
                                screens=self.screens)
        self.api = IntegratedApiRoutes(self.wf, cfg, self.started_at, ctx=self)
        #: 止め方。統合アプリ(`start_app`)が差し込む。試験では werkzeug の shutdown
        self.shutdown_hook = None
        self.httpd = None

        # 基盤仕様書 2.8: まずアプリ自身へ正常終了を要求できるようにする
        self.wf.shutdown_hook = self.request_shutdown

        if load_materials:
            self.load_materials()

    def load_materials(self) -> None:
        """資材マスタを一度読んでおく(取得元を画面に出すため)。

        統合版では起動時の重い初期化(`initialize`)から呼ぶ ── 共有に
        届かない端末では Access の応答待ちに数十秒かかることがあり、
        Flask の組み立て(待機画面より前)でやると起動が遅く見える。
        """
        try:
            self.materials.load()
        except Exception as exc:
            log.warning("資材マスタの初回読込に失敗しました: %s", exc)

    def request_shutdown(self) -> None:
        if self.shutdown_hook is None:
            return
        log.info("停止要求を受け付けました。サーバーを停止します。")
        threading.Thread(target=self.shutdown_hook, daemon=True).start()

    def close(self) -> None:
        """状態DB を閉じる(移植元 `serve()` の `finally` と同じ)。何度呼んでもよい。"""
        try:
            self.store.close()
        except Exception as exc:                    # noqa: BLE001 - 止めるときは続ける
            log.warning("状態DB を閉じられませんでした: %s", exc)


# プロセスに1つ。統合アプリの入口(`__init__.py`)が `initialize` などで使う
_context: Optional[AppContext] = None


def context() -> Optional[AppContext]:
    return _context


def build_blueprint(url_prefix: str = DEFAULT_PREFIX, *,
                    ctx: Optional[AppContext] = None) -> Blueprint:
    """この機能ぶんの Blueprint を組み立てる。"""
    bp = Blueprint(KEY, __name__,
                   url_prefix=url_prefix or None,
                   static_folder=str(ROOT / "app" / "static"),
                   static_url_path="/static")
    prefix = url_prefix or ""
    if ctx is None:
        cfg = load_config()
        ctx = AppContext(cfg, load_materials=False)
    ctx.cfg.url_prefix = prefix
    bp.module_conf = {                          # type: ignore[attr-defined]
        "MODE": "main",
        "APP_ID": ctx.cfg.app_id,
        "VERSION": ctx.cfg.version,
        "DISPLAY_NAME": ctx.cfg.app_name,
    }
    bp.ctx = ctx                                # type: ignore[attr-defined]

    @bp.before_request
    def _guard():                               # noqa: ANN202 - Flaskのフック
        """トークンと同一オリジン(`/api/*` だけ)。断り方は移植元の形(`ok`/`message`)。"""
        path = security.relative_path(prefix)
        if not path.startswith("/api/"):
            return None
        if not security.same_origin_ok():
            return jsonify({"ok": False, "reason": "cross_origin",
                            "message": "別のページからは利用できません"}), 403
        if path in TOKEN_EXEMPT:
            return None
        if not current_app.config.get("PENA_REQUIRE_TOKEN", True):
            return None
        if not security.token_ok(current_app.config.get("TOKEN", "")):
            return jsonify({"ok": False, "reason": "bad_token",
                            "message": "この画面を開き直してください"}), 403
        return None

    @bp.route("/", defaults={"path": ""}, methods=["GET", "HEAD", "POST"])
    @bp.route("/<path:path>", methods=["GET", "HEAD", "POST"])
    def dispatch(path: str):
        # 統合アプリが決めたトークン・ポートを、画面へ渡す値へ写す
        ctx.cfg.app_token = current_app.config.get("TOKEN", "") or ""
        if current_app.config.get("BRIDGE"):
            ctx.cfg.port = 0                    # デスクトップ版はポートを使わない
        elif current_app.config.get("PORT"):
            ctx.cfg.port = int(current_app.config["PORT"])
        rel = "/" + path.rstrip("/") if path else "/"
        if request.method == "POST":
            # 移植元 do_POST: 本文を確かめてから、API でなければ JSON の 404
            # (以前は画面の経路へ POST すると画面をそのまま返していた)
            return _api(ctx, rel) if rel.startswith("/api/") else _post_elsewhere()
        if rel.startswith("/fonts/"):
            return _serve_font(rel)
        if rel.startswith("/api/"):
            return _api(ctx, rel)
        return _page(ctx, rel, prefix)

    @bp.errorhandler(404)
    def _missing(_e):                           # noqa: ANN202 - Flaskのフック
        """静的ファイルが無いとき。移植元と同じ HTML の案内(統合アプリの既定は JSON)。"""
        if request.method == "POST":
            return _post_elsewhere()
        return _error_page(404, "ファイルが見つかりません。", prefix)

    @bp.after_request
    def _log_request(response):                 # noqa: ANN202 - Flaskのフック
        """要求を1行ずつ残す(移植元 `Handler.log_message` と同じ)。

        画面の心拍・接続確認は数秒ごとに来るので DEBUG に落とす(`--diagnostic` で出る)。
        """
        rel = security.relative_path(prefix)
        level = logging.DEBUG if rel in QUIET_PATHS else logging.INFO
        log.log(level, "%s \"%s %s\" %s", request.remote_addr or "-", request.method,
                _logged_url(), response.status_code)
        return response

    return bp


def register(app: Flask, url_prefix: str = DEFAULT_PREFIX) -> Blueprint:
    """統合アプリへ載せる。配布設定があれば、この端末に無い項目だけ先に読む。"""
    global _context
    cfg = load_config()
    try:
        from .app.services import distribution
        dist = distribution.apply_on_start(cfg)
        if dist.applied:
            cfg = load_config()
            log.info("配布設定を読み込みました: %s", "、".join(dist.applied))
        elif not dist.ok:
            log.info("%s", dist.message)
    except Exception as exc:                       # 読めなくても起動は止めない
        log.warning("配布設定を読めませんでした: %s", exc)
    _context = AppContext(cfg, load_materials=False)
    bp = build_blueprint(url_prefix, ctx=_context)
    app.register_blueprint(bp)
    log.info("ペナラベルを載せました: %s (版 %s)", url_prefix or "/", cfg.version)
    return bp


# ------------------------------------------------------------------
# 経路(移植元 Handler.do_GET / do_POST)
# ------------------------------------------------------------------
def _logged_url() -> str:
    """ログに残す URL。**トークン(`t=`)は伏せる**(ログは端末に残り、人に渡すこともある)。"""
    args = [(k, "***" if k == "t" else v) for k, v in request.args.items(multi=True)]
    return request.path + ("?" + urlencode(args, safe="*") if args else "")


def _html(html: str, status: int = 200) -> Response:
    return Response(html, status=status, mimetype="text/html")


def record_incident(method: str, path: str, exc: BaseException) -> str:
    """エラーの記録(`common/incidents.py`)を作って番号を返す(統合版 1.5.8)。

    ペナラベルは移植元のまま自分で例外を受け止めて画面へ返すので、統合アプリの
    エラーの受け口まで届かない。ここで記録して、**画面の文言に番号を付ける**。
    """
    from flask import g
    from common import incidents
    eid = incidents.new_id()
    incidents.record("server", f"ペナラベル {method} {path}: {type(exc).__name__}: {exc}"[:300],
                     shown=f"処理に失敗しました(エラー番号 {eid})", exc=exc, eid=eid)
    try:
        g.error_id = eid                           # 統合アプリの「断り・失敗」の行と二重にしない
    except RuntimeError:
        pass
    return eid


def _error_page(status: int, message: str, prefix: str) -> Response:
    # 文言に例外の文字が入る(送られた値が混ざりうる)ので、**必ず伏せ字にしてから**埋める
    from html import escape
    return _html(
        "<!doctype html><meta charset='utf-8'>"
        "<title>エラー</title>"
        "<body style=\"font-family:Meiryo,sans-serif;padding:24px\">"
        f"<h1>{status}</h1><p>{escape(message)}</p>"
        f"<p><a href='{prefix}/'>メイン画面へ戻る</a> / <a href='{prefix}/diag'>診断</a></p>",
        status)


def _serve_font(rel: str) -> Response:
    """バーコードフォントを配信する(assets/fonts 配下のみ)。"""
    name = rel[len("/fonts/"):]
    if not name.lower().endswith((".ttf", ".otf")) or "/" in name or "\\" in name:
        return _error_page(404, "フォントが見つかりません。",
                           security.module_prefix(KEY))
    base = ROOT / "assets" / "fonts"
    if not (base / name).is_file():
        return _error_page(404, "フォントが見つかりません。",
                           security.module_prefix(KEY))
    return send_from_directory(str(base), name, mimetype="font/ttf")


def _read_body():
    """POST の本文(移植元 do_POST と同じ確かめ方)。`(本文, 断りの応答)` のどちらか。

    **長さを名乗らない送り方(chunked)でも上限を守る。** `Content-Length` だけを
    見ていると、名乗らない要求は上限なしで読んでいた(移植元の `http.server` は
    名乗らない本文を読まなかった)。上限より1バイトだけ多く読んで確かめる。
    """
    too_big = jsonify({"ok": False, "message": "リクエストが大きすぎます"}), 413
    if (request.content_length or 0) > MAX_BODY:
        return None, too_big
    raw = request.stream.read(MAX_BODY + 1) or b""
    if len(raw) > MAX_BODY:
        return None, too_big
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, (jsonify({"ok": False, "message": "リクエストを解釈できません"}), 400)
    if not isinstance(body, dict):
        return None, (jsonify({"ok": False, "message": "リクエスト形式が不正です"}), 400)
    return body, None


def _post_elsewhere():
    """API 以外への POST(移植元と同じ JSON の 404)。本文の確かめは先に行う。"""
    _, refused = _read_body()
    if refused is not None:
        return refused
    return jsonify({"ok": False, "message": "不明なエンドポイントです"}), 404


def _api(ctx: AppContext, rel: str) -> Response:
    method = "POST" if request.method == "POST" else "GET"
    body: dict = {}
    if method == "POST":
        body, refused = _read_body()
        if refused is not None:
            return refused
    status, payload = ctx.api.handle(rel, method, body)
    return jsonify(payload), status


def _ob_list(ctx: AppContext, query) -> list:
    raw = (query.get("ob") or [""])[0]
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            n = int(part)
            if ctx.master.get(n):
                out.append(n)
    return out


def _page(ctx: AppContext, rel: str, prefix: str) -> Response:
    p = ctx.pages
    query = request.args.to_dict(flat=False)
    # ?pane=1 … 起動ページの中にタブとして差し込むので中身だけ返す
    pane = (request.args.get("pane", "") == "1")
    p.pane_mode(pane)
    try:
        if rel == "/":
            return _html(p.index())
        if rel == "/tare":
            return _html(p.tare())
        if rel == "/tare/print":
            return _html(p.tare(print_view=True))
        if rel == "/list":
            return _html(p.list_page())
        if rel == "/list/print":
            return _html(p.list_page(print_view=True))
        if rel == "/breakdown":
            return _html(p.breakdown())
        if rel == "/labels":
            return _html(p.labels(_ob_list(ctx, query)))
        if rel == "/labels/print":
            obs = _ob_list(ctx, query)
            if not obs:
                return _error_page(400, "印刷対象が指定されていません。", prefix)
            guides = (query.get("guides") or [""])[0] == "1"
            return _html(p.label_print(obs, guides=guides))
        if rel == "/labels/sheet":
            obs = _ob_list(ctx, query)
            if not obs:
                return _error_page(400, "対象が指定されていません。", prefix)
            return _html(p.labels(obs, print_view=True))
        if rel == "/labels/calibration":
            return _html(p.label_calibration())
        if rel == "/all-size":
            return _html(p.all_size(query))
        if rel == "/all-size/print":
            combo = (query.get("combo") or [""])[0]
            return _html(p.all_size_print(combo))
        if rel == "/settings":
            return _html(p.settings())
        if rel == "/diag":
            return _html(p.diag())
        if rel == "/favicon.ico":
            return Response(b"", status=204, mimetype="image/x-icon")
        return _error_page(404, "ページが見つかりません。", prefix)
    except Exception as exc:                       # noqa: BLE001 - 画面に理由を出す
        log.exception("GET 失敗: %s", request.path)
        eid = record_incident("GET", request.path, exc)
        return _error_page(500, "画面の生成に失敗しました: %s(エラー番号 %s)" % (exc, eid),
                           prefix)
    finally:
        # スレッドは使い回されるので、必ず戻す
        p.pane_mode(False)


# ------------------------------------------------------------------
# 単体で動かす・試験(移植元と同じ `create_server(cfg)` の形)
# ------------------------------------------------------------------
def create_server(cfg=None, *, require_token: bool = False):
    """サーバーとコンテキストを作る(起動はしない)。

    移植元と同じ ``(httpd, ctx)`` を返す。``httpd`` は
    ``serve_forever(poll_interval)`` / ``shutdown()`` / ``server_close()`` を持つ
    (``werkzeug`` の開発サーバ。**配布先では使わない** ── 本番は統合アプリの
    ``server.py``(waitress)が受け持つ)。

    すでに起動していれば ``OSError`` になる。bind に失敗したら、ここで作った
    資源(状態DB の接続)を残さない。
    """
    cfg = cfg or load_config()
    ctx = AppContext(cfg)
    app = Flask("pena_standalone", static_folder=None, template_folder=None)
    app.config.update(
        TOKEN="", PORT=cfg.port, READY=True,
        # 試験・単体起動ではトークンを要求しない(移植元の試験がそのまま通る)。
        # 統合アプリでは要求する(`PENA_REQUIRE_TOKEN` の既定は真)
        PENA_REQUIRE_TOKEN=require_token,
    )
    security.install_common(app)
    app.register_blueprint(build_blueprint(cfg.url_prefix or "", ctx=ctx))

    from werkzeug.serving import make_server
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    try:
        httpd = make_server(cfg.host, cfg.port, app, threaded=True)
    except OSError:
        try:
            ctx.store.close()
        except Exception:
            pass
        raise
    ctx.httpd = httpd
    ctx.shutdown_hook = httpd.shutdown
    return httpd, ctx


def serve(cfg=None) -> int:
    """ブロッキングで待ち受ける(単体での確認用)。"""
    cfg = cfg or load_config()
    httpd, ctx = create_server(cfg)
    log.info("起動しました: http://%s:%s/ (pid=%s)", cfg.host, cfg.port, os.getpid())
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        log.info("中断されました")
    finally:
        httpd.server_close()
        ctx.store.close()
        log.info("停止しました")
    return 0
