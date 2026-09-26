"""3機能に共通する守り ── Host 検証・同一オリジン・起動トークン・応答ヘッダ

移植元の3つは守り方が違っていた。

    梱包明細   Host 検証あり / Sec-Fetch-Site あり / トークン `X-Tool-Token`(401)
               / 画面の持ち主 `X-Tool-Screen`(409)
    資材計算   Host 検証なし / Sec-Fetch-Site なし / トークン `X-App-Token`(403)
               / 画面の持ち主 `X-App-Screen`(409、書く要求だけ)
    ペナラベル トークンなし / 画面の持ち主は本文の `screenId`(409)

【統合版で採ったもの】
- **Host 検証と同一オリジンの確認は、梱包明細のものを全機能に当てる**
  (`install_common`)。どちらも「127.0.0.1 のこのアプリ以外からは触らせない」
  ためのもので、業務の結果には触れない。資材計算・ペナラベルが持っていなかった
  のは移植の時期の差で、無くてよい理由があったわけではない
- **起動トークンはプロセスに1つ。** 3機能とも同じ値を照合する。ヘッダの名前は
  移植元のまま2つとも受ける(`X-Tool-Token` / `X-App-Token`)── 画面のJSを
  そろえて直す理由が無い。ペナラベルにも同じトークンを要求する(同じプロセスに
  同居するので、1つだけ素通しにしておく理由が無い)
- **断り方(401 か 403 か・本文の形)は各機能のまま。** 画面のJSがそれを
  見て振る舞うので、そろえると画面側も直すことになる。ここは照合の
  「道具」だけを持ち、断りの応答は各機能の `before_request` が作る
- **画面の持ち主の見張りは各機能のまま。** 守る相手(作業状態)が機能ごとに
  別で、画面のJSとの約束(ヘッダ名・本文・応答)も違う。1つにしても
  守れるものは増えない

【静的ファイルの控え】梱包明細の決まりを全機能に当てる。URLに版が入っている
静的ファイルは長く控えてよく、それ以外(HTML・API)は控えない。
"""
from __future__ import annotations

import secrets
from typing import Optional

from flask import Flask, Response, current_app, jsonify, request

# 静的ファイルを控えておいてよい期間(秒)。URLに版が入っているので、
# 入れ替えれば URL が変わり、必ず取り直される
STATIC_MAX_AGE = 7 * 24 * 60 * 60

ALLOWED_HOSTS = ("127.0.0.1", "localhost")

TOKEN_HEADERS = ("X-Tool-Token", "X-App-Token")


def host_ok(req=None) -> bool:
    """`Host` がこのPCの自分自身か(DNSリバインディング対策)。"""
    req = req or request
    host = (req.host or "").split(":")[0]
    return host in ALLOWED_HOSTS


def same_origin_ok(req=None) -> bool:
    """ブラウザが付ける Fetch Metadata が「別のページから」でないか。

    付いていない場合(古いクライアント・curl)は素通しし、トークンで守る。
    """
    req = req or request
    fetch_site = req.headers.get("Sec-Fetch-Site")
    return not fetch_site or fetch_site in ("same-origin", "none")


def supplied_token(req=None) -> str:
    """要求に付いてきたトークン。ヘッダ(2種)か `?t=`。"""
    req = req or request
    for name in TOKEN_HEADERS:
        value = req.headers.get(name)
        if value:
            return value
    return req.args.get("t", "")


def token_ok(expected: str, req=None) -> bool:
    """トークンが合っているか。

    バイト列で比べる。`compare_digest` に str を渡すと非ASCIIで
    TypeError になり、**500 を返してしまう**(送られた値は誰にでも
    決められるので、素直に断らなければならない)。
    """
    given = supplied_token(req)
    return secrets.compare_digest(given.encode("utf-8"), (expected or "").encode("utf-8"))


def relative_path(prefix: str, req=None) -> str:
    """機能の入口(`/details` など)を除いた、機能の中での経路。

    移植元の `before_request` は `request.path` を `/api/` などと比べていた。
    統合版では入口が付くので、比べる前にここで外す。
    """
    req = req or request
    path = req.path
    if prefix and path.startswith(prefix):
        rest = path[len(prefix):]
        return rest or "/"
    return path


def error_json(code: str, message: str, status: int) -> Response:
    """統合アプリ自身の断り(機能の外の経路)。形は梱包明細に合わせる。"""
    response = jsonify({"error": {"code": code, "message": message}})
    response.status_code = status
    return response


# ------------------------------------------------------------------
# アプリ全体に当てるもの
# ------------------------------------------------------------------
def install_common(app: Flask) -> None:
    """Host 検証・応答ヘッダ・控えの決まり・静的ファイルの版。"""

    @app.before_request
    def _check_host():                          # noqa: ANN202 - Flaskのフック
        if not host_ok():
            current_app.logger.warning("Host不一致で拒否: %s", request.host)
            return error_json("bad_host", "このアドレスからは利用できません", 400)
        return None

    @app.after_request
    def _headers(response):                     # noqa: ANN202 - Flaskのフック
        # CORS ヘッダは**一切返さない**(返さないことが対策)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        apply_cache_policy(response)
        return response

    @app.url_defaults
    def _stamp_static(endpoint, values):        # noqa: ANN202 - Flaskのフック
        """CSS/JS の URL に版を付ける(`?v=`)。

        **入れ替えたら必ず取り直させる。** 配布はフォルダごとコピーなので
        ファイル名は版が上がっても変わらない。機能ごとの静的ファイル
        (`details.static` など)にはその機能の版を、統合画面のものには
        統合アプリの版を付ける。
        """
        if not (endpoint == "static" or endpoint.endswith(".static")) or "v" in values:
            return
        values["v"] = static_version(endpoint, values.get("filename"))


def static_version(endpoint: str, filename: Optional[str] = None) -> str:
    """その静的ファイルの版。機能の blueprint なら機能の版、統合画面のものなら統合ツールの版。

    **後ろに中身の指紋を付ける**(`0.13.1-3f2a9c1b`)。版の付いた静的ファイルは
    長く控えてよい(`immutable`)ので、版を上げ忘れたまま JS や CSS を直すと、
    一度開いた端末では古いものが使われ続ける。統合ツールの版と機能の版を
    分けたので、どちらを上げるか迷っても、中身が変われば必ず URL が変わるようにする。
    """
    version = ""
    folder = current_app.static_folder
    if "." in endpoint:
        name = endpoint.split(".", 1)[0]
        bp = current_app.blueprints.get(name)
        conf = getattr(bp, "module_conf", None) or {}
        if conf.get("VERSION"):
            version = str(conf["VERSION"])
        folder = getattr(bp, "static_folder", None) or folder
    version = version or str(current_app.config.get("VERSION", ""))
    stamp = _fingerprint(folder, filename) if folder and filename else ""
    return f"{version}-{stamp}" if stamp else version


#: 指紋の控え: 置き場所 → (更新時刻, 大きさ, 指紋)。毎回読み直さない
_FINGERPRINTS: dict = {}


def _fingerprint(folder: str, filename: str) -> str:
    """静的ファイルの中身の指紋(先頭8文字)。読めなければ空。"""
    import hashlib
    import os

    path = os.path.normpath(os.path.join(folder, filename))
    if not path.startswith(os.path.normpath(folder) + os.sep):
        return ""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    cached = _FINGERPRINTS.get(path)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    try:
        with open(path, "rb") as f:
            stamp = hashlib.sha1(f.read()).hexdigest()[:8]
    except OSError:
        return ""
    _FINGERPRINTS[path] = (st.st_mtime_ns, st.st_size, stamp)
    return stamp


def apply_cache_policy(response: Response) -> None:
    """何を控えてよくて、何を控えてはいけないか(梱包明細の決まり)。

    - **版がURLに入っているもの(静的ファイル)は長く控えてよい。**
    - **それ以外は控えない。** 画面のHTMLもAPIの応答も、いま作ったものを毎回渡す
    - 版の付いていない静的ファイル(`import` される共有モジュール)は
      `no-cache`(使う前に必ず確かめる)
    """
    if "/static/" in request.path or "/fonts/" in request.path:
        if request.args.get("v"):
            response.headers["Cache-Control"] = (
                f"public, max-age={STATIC_MAX_AGE}, immutable")
        else:
            response.headers["Cache-Control"] = "no-cache"
        return
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def module_prefix(bp_name: str) -> str:
    """登録済みの機能の入口(`/details` など)。標準の Flask には無いので blueprint から引く。"""
    bp = current_app.blueprints.get(bp_name)
    return (getattr(bp, "url_prefix", None) or "") if bp is not None else ""


def find_module_conf(name: Optional[str] = None) -> dict:
    """いまの要求が属する機能の固有値。機能の外なら空。"""
    name = name or (request.blueprint or "").split(".", 1)[0]
    bp = current_app.blueprints.get(name)
    return getattr(bp, "module_conf", None) or {}
