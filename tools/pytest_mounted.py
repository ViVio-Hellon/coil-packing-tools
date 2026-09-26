"""3機能の Web の試験を、**統合アプリに載せた形**(入口+トークン)で流す pytest の差し込み。

    python -m pytest -p tools.pytest_mounted modules/packing_details/tests/test_web.py
    python -m pytest -p tools.pytest_mounted modules/packing_material_calculation/tests/test_web_flow.py
    python -m pytest -p tools.pytest_mounted modules/packing_pena_label/tests/test_http.py

【なぜ要るのか】
3機能の Web の試験は移植元のままで、**機能だけの Flask**(入口なし・ペナラベルは
トークンなし)を相手にしている。統合版で実際に動くのは、統合アプリに `/details`
`/pena` `/material` の入口で載った形で、統合アプリのトークン・待機画面への回し・
停止口の確認・別タブの心拍の差し込みが掛かる。その形では流していなかった
(移植漏れの点検で見つかった)。入口を付け忘れた URL や、統合アプリの取り次ぎで
変わる応答は、機能だけの試験では見つからない。

【何を差し替えるか】
試験が相手を作るところ(`create_app` / `create_server`)だけ。試験の中身は変えない。

- 梱包明細・資材計算: 統合アプリ(`app.create_app`)にその機能だけを載せて返す。
  試験のクライアントが叩く `/meisai` `/api/...` には入口を付けて送る
- ペナラベル: 統合アプリに、試験が用意した状態(`AppContext`)のままのペナラベルを
  `/pena` で載せ、本物の HTTP で待ち受ける。統合版では API にトークンが要るので、
  **画面の JS と同じく**トークンを付けて送る(付けないと断ることは根の試験が確かめる)

入口が付くこと・統合アプリが決めることそのものを確かめている試験は、統合版では
答えが違うのが正しい。`DIFFERENT_BY_DESIGN` に理由と、統合版の形を確かめている
根の試験を書いて飛ばす。

根の試験 `tests/test_modules_mounted.py` が、これを付けて3機能の Web の試験を流す。
"""
from __future__ import annotations

import os
import tempfile
import types
from unittest import mock

import pytest
from flask.testing import FlaskClient

# 統合アプリの手元の領域とログだけを使い捨ての場所へ(本物の領域を汚さない)。
# 3機能の置き場所は、それぞれの試験が自分で隔離している。`tests/_env.py` は使わない ──
# 機能の試験が見る環境変数まで変わり、機能だけで流したときと条件が変わる
# (そのためこの差し込みは `tests\` の外に置く。`tests\` を読むと `_env` が効く)
_DIR = tempfile.TemporaryDirectory(prefix="coil-packing-tools-mounted-")
os.environ.setdefault("COIL_PACKING_TOOLS_LOCAL_DIR", os.path.join(_DIR.name, "local"))
os.environ.setdefault("COIL_PACKING_TOOLS_LOG_DIR", os.path.join(_DIR.name, "local", "logs"))

#: 入口付きで送った要求の数(本当に載せた形で流れたかを、根の試験が見る)
SENT = {"details": 0, "material": 0, "pena": 0}

#: 統合版では答えが違うのが正しい試験: 理由(と、統合版の形を確かめている試験)
DIFFERENT_BY_DESIGN = {
    "modules/packing_details/tests/test_web.py::PrintTest::test_紙面に書き足しの欄と送り先が付く":
        "書き足しの送り先に入口 /details が付く(tests/test_app.py の帳票の試験が確かめる)",
    "modules/packing_material_calculation/tests/test_web_settings.py::test_帯のバッジから設定の詳しいところへ飛べる":
        "帯の版のバッジの行き先に入口 /material が付く(tests/test_modules_mounted.py が確かめる)",
    "modules/packing_pena_label/tests/test_http.py::TestSettingsScreen::test_invalid_value_rejected":
        "使用ポートは統合アプリが決め、ペナラベルの設定では扱わない"
        "(tests/test_app.py::ModuleInfoTest が確かめる)",
    "modules/packing_pena_label/tests/test_http.py::TestSettingsScreen::test_restart_required_is_reported":
        "同上(使用ポートを変えても再起動の案内は出ない)",
}


def _prefixed_client(key: str, prefix: str):
    """試験が叩く経路(`/meisai` `/api/...`)に入口を付けて送るクライアント。"""

    class Client(FlaskClient):
        def open(self, *args, **kwargs):
            path = args[0] if args else kwargs.get("path")
            if isinstance(path, str) and path.startswith("/") and not path.startswith(prefix + "/"):
                path = prefix + path
                if args:
                    args = (path,) + args[1:]
                else:
                    kwargs["path"] = path
                SENT[key] += 1
            return super().open(*args, **kwargs)

    return Client


def _mounted_app(module, token):
    import app as integrated
    flask_app = integrated.create_app(token=token, modules=[module])
    flask_app.test_client_class = _prefixed_client(module.KEY, module.PREFIX)
    return flask_app


def details_create_app(mode=None, *, token=None, port=None):
    from modules import packing_details
    return _mounted_app(packing_details, token)


def material_create_app(mode=None, *, token=None, port=None):
    from modules import packing_material_calculation
    return _mounted_app(packing_material_calculation, token)


#: ペナラベルの試験で使うトークン(画面の JS が付けるのと同じ役)
PENA_TOKEN = "mounted-pena-token"


def pena_create_server(cfg=None, *, require_token=False):
    """`server.create_server` と同じ `(httpd, ctx)` を、統合アプリに載せた形で返す。"""
    import app as integrated
    from werkzeug.serving import make_server

    from modules import packing_pena_label as pena
    from modules.packing_pena_label import server as pena_server

    cfg = cfg or pena_server.load_config()
    ctx = pena_server.AppContext(cfg)
    # 試験が用意した状態のまま載せる(`pena.register` は端末の設定から作り直すため)
    module = types.SimpleNamespace(
        KEY=pena.KEY, LABEL=pena.LABEL, PREFIX=pena.PREFIX, HOME=pena.HOME,
        PORTED_FROM=pena.PORTED_FROM, display_name=pena.display_name, version=pena.version,
        register=lambda flask_app: flask_app.register_blueprint(
            pena_server.build_blueprint(pena.PREFIX, ctx=ctx)))
    flask_app = integrated.create_app(token=PENA_TOKEN, modules=[module])
    inner = flask_app.wsgi_app

    def as_the_page_sends(environ, start_response):
        """試験は機能の根(`/api/...`)を叩く。入口を付け、画面の JS と同じくトークンを付ける。"""
        path = environ.get("PATH_INFO", "")
        if not (path == pena.PREFIX or path.startswith(pena.PREFIX + "/")):
            environ["PATH_INFO"] = pena.PREFIX + path
            SENT["pena"] += 1
        environ.setdefault("HTTP_X_TOOL_TOKEN", PENA_TOKEN)
        return inner(environ, start_response)

    flask_app.wsgi_app = as_the_page_sends
    try:
        httpd = make_server(cfg.host, cfg.port, flask_app, threaded=True)
    except OSError:
        ctx.store.close()
        raise
    ctx.httpd = httpd
    ctx.shutdown_hook = httpd.shutdown
    return httpd, ctx


def pytest_collection_modifyitems(config, items):
    for item in items:
        why = DIFFERENT_BY_DESIGN.get(item.nodeid)
        if why:
            item.add_marker(pytest.mark.skip(reason="統合版では違うのが正しい: " + why))


@pytest.fixture(scope="session", autouse=True)
def _mount_in_the_integrated_app():
    from modules.packing_details import app as details_app
    from modules.packing_material_calculation import app as material_app
    from modules.packing_pena_label import server as pena_server
    with mock.patch.object(details_app, "create_app", details_create_app), \
         mock.patch.object(material_app, "create_app", material_create_app), \
         mock.patch.object(pena_server, "create_server", pena_create_server):
        yield


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(
        "入口付きで送った要求: " + " ".join(f"{k}={v}" for k, v in SENT.items()))
