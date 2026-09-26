"""使ってよい画面は1つだけ ── タブを2枚開かせない

守りたいのは1行で言える。**プロセスに1つしかない作業状態
(`app/session.py`)を、2枚の画面に奪い合わせない。**

VBA のフォームは1枚しか開かなかったので、そこは考えなくてよかった。
Web はタブを何枚でも開けるので、ここで塞ぐ。
"""
from __future__ import annotations

import pytest

from modules.packing_material_calculation.app import screen
from modules.packing_material_calculation.tests._web import client, sandbox, seed   # noqa: F401


@pytest.fixture(autouse=True)
def 画面を戻す():
    screen.reset()
    yield
    screen.reset()


# ==================================================================
# 素の関門 (app/screen.py)
# ==================================================================
def test_1枚目は通る():
    assert screen.claim("A").ok is True
    assert screen.holder().screen == "A"


def test_2枚目は断られる():
    screen.claim("A")
    result = screen.claim("B")
    assert result.ok is False
    assert result.reason == screen.REFUSE_TAKEN
    # **相手がどれくらい前から開いているか**を返す。「さっき自分で
    # 開いたタブ」なのか「隣の人が使っている」のかの判断材料になる
    assert result.other_age >= 0
    # 断られても、持ち主は変わらない
    assert screen.holder().screen == "A"


def test_同じ画面の読み直しは通る():
    """画面を移る(全ページ読み込み)たびに申し出が飛ぶ。断ってはいけない。"""
    screen.claim("A")
    assert screen.claim("A").ok is True
    assert screen.holder().screen == "A"


def test_読み直しても開いた時刻は動かない():
    """動くと、断られた側に出る「◯分前から開いています」が嘘になる。"""
    screen.claim("A")
    first = screen.holder().since
    screen.claim("A")
    assert screen.holder().since == first


def test_押されたときだけ取り上げる():
    screen.claim("A")
    assert screen.claim("B", takeover=True).ok is True
    assert screen.holder().screen == "B"


def test_取り上げられた画面は心拍で気づく():
    """ここが**2枚とも動かないことの最後の砦**。"""
    screen.claim("A")
    assert screen.beat("A") is True
    screen.claim("B", takeover=True)
    assert screen.beat("A") is False
    assert screen.beat("B") is True


def test_閉じれば空く():
    screen.claim("A")
    screen.release("A")
    assert screen.holder() is None
    assert screen.claim("B").ok is True


def test_取り上げられた画面が閉じても新しい画面は巻き添えにならない():
    """古いタブを閉じた拍子に、使っているほうが締め出されない。"""
    screen.claim("A")
    screen.claim("B", takeover=True)
    screen.release("A")          # 古いタブを閉じた
    assert screen.holder().screen == "B"


def test_心拍が途切れたら空きとみなす(monkeypatch):
    """ブラウザが落ちた・端末ごと落ちた。**誰も使えないアプリにしない。**"""
    screen.claim("A")
    monkeypatch.setattr(screen, "STALE_SEC", -1.0)   # 即座に古い扱い
    assert screen.holder() is None
    assert screen.claim("B").ok is True


def test_途切れて戻ってきた画面は締め出さない():
    """通信が切れただけの画面が、自分の符牒で心拍を送り直した場合。"""
    screen.claim("A")
    screen.release("A")            # 空いた状態を作る
    assert screen.beat("A") is True
    assert screen.holder().screen == "A"


def test_符牒が無ければ断る():
    assert screen.claim("").ok is False
    assert screen.claim("   ").reason == screen.REFUSE_NO_SCREEN


def test_誰も開いていなければ通す():
    """`--no-browser` で立てておく使い方と試験を巻き添えにしない。"""
    assert screen.owns("") is True
    assert screen.owns("なんでも") is True


def test_誰かが開いていれば本人だけ通す():
    screen.claim("A")
    assert screen.owns("A") is True
    assert screen.owns("B") is False
    assert screen.owns("") is False


def test_古くなる秒数は心拍の間隔から導く():
    """秒数を直に書くと、片方だけ変えたときに黙ってずれる。"""
    from modules.packing_material_calculation.coil_tool import idle_exit
    assert screen.STALE_SEC == pytest.approx(
        screen.MISSED_BEATS * idle_exit.HEARTBEAT_MS / 1000.0)


# ==================================================================
# 受付 (/api/screen/*)
# ==================================================================
def test_申し出は1枚目だけ通る(client):
    assert client.post("/api/screen/claim", json={"screen": "A"}).status_code == 200

    r = client.post("/api/screen/claim", json={"screen": "B"})
    # 409。**入力は正しく、いまこの画面に資格が無い**という断り方
    assert r.status_code == 409
    assert r.get_json()["reason"] == screen.REFUSE_TAKEN


def test_符牒なしの申し出は400(client):
    """形が違うのと、断られたのを取り違えない。"""
    r = client.post("/api/screen/claim", json={})
    assert r.status_code == 400
    assert r.get_json()["reason"] == screen.REFUSE_NO_SCREEN


def test_取り上げは通る(client):
    client.post("/api/screen/claim", json={"screen": "A"})
    r = client.post("/api/screen/claim", json={"screen": "B", "takeover": True})
    assert r.status_code == 200
    assert screen.holder().screen == "B"


def test_空きを見に行っても取りに行かない(client):
    """断られた画面が5秒ごとに見る先。ここで取ってしまうと取り合いになる。"""
    client.post("/api/screen/claim", json={"screen": "A"})
    r = client.get("/api/screen?screen=B").get_json()
    assert r["busy"] is True
    assert r["mine"] is False
    # 見ただけ。持ち主は変わっていない
    assert screen.holder().screen == "A"


def test_空いていれば空いていると答える(client):
    r = client.get("/api/screen?screen=B").get_json()
    assert r["busy"] is False


def test_受付はトークンを要らない(sandbox):
    """関門そのものに関門を付けると、断られた画面が理由も受け取れない。"""
    from modules.packing_material_calculation.app import create_app, session
    from modules.packing_material_calculation.coil_tool import db
    session.reset()
    screen.reset()
    with db.connect() as conn:
        db.apply_schema(conn)
    app = create_app(token="test-token")
    app.config["READY"] = True
    with app.test_client() as c:       # トークンを積まない
        assert c.post("/api/screen/claim", json={"screen": "A"}).status_code == 200


# ==================================================================
# 心拍 (/api/alive)
# ==================================================================
def test_心拍が取り上げを知らせる(client):
    client.post("/api/screen/claim", json={"screen": "A"})
    assert client.post("/api/alive", json={"screen": "A"}).get_json()["own"] is True

    client.post("/api/screen/claim", json={"screen": "B", "takeover": True})
    # ここで A は使えなくなったと知る
    assert client.post("/api/alive", json={"screen": "A"}).get_json()["own"] is False


def test_符牒を名乗らない心拍は素通し(client):
    """古い画面・試験・`--no-browser`。締め出すとアプリが終わってしまう。"""
    client.post("/api/screen/claim", json={"screen": "A"})
    assert client.post("/api/alive", json={}).get_json()["own"] is True
    # 名乗らない相手は持ち主を横取りしない
    assert screen.holder().screen == "A"


def test_閉じた合図で符牒を手放す(client):
    """閉じてすぐ開き直したときに、待たずに通るため。"""
    client.post("/api/screen/claim", json={"screen": "A"})
    client.post("/api/alive", json={"screen": "A", "closing": True})
    assert screen.holder() is None


def test_取り上げられた画面が閉じても持ち主は残る(client):
    client.post("/api/screen/claim", json={"screen": "A"})
    client.post("/api/screen/claim", json={"screen": "B", "takeover": True})
    client.post("/api/alive", json={"screen": "A", "closing": True})
    assert screen.holder().screen == "B"


# ==================================================================
# 書き込みの関門
#
# 画面の覆いは次の心拍(最大20秒)まで掛からない。その隙に取り上げ
# られた画面が書けてしまうと、結局2枚で奪い合うことになる。
# ==================================================================
def test_使っていない画面からの書き込みは断る(client, sandbox):
    client.post("/api/screen/claim", json={"screen": "A"})
    r = client.post("/api/settings", json={"line": "NS1"},
                    headers={"X-App-Screen": "B"})
    assert r.status_code == 409
    assert r.get_json()["reason"] == screen.REFUSE_TAKEN


def test_使ってよい画面からの書き込みは通る(client, sandbox):
    client.post("/api/screen/claim", json={"screen": "A"})
    r = client.post("/api/settings", json={"line": "NS1"},
                    headers={"X-App-Screen": "A"})
    assert r.status_code == 200


def test_誰も開いていなければ書き込みは通る(client, sandbox):
    """試験と `--no-browser` を巻き添えにしない。"""
    assert client.post("/api/settings", json={"line": "NS1"}).status_code == 200


def test_読むのは断らない(client):
    """塞ぐのは書くほうだけ。読むのを塞いでも守るものが無い。"""
    client.post("/api/screen/claim", json={"screen": "A"})
    r = client.get("/api/settings", headers={"X-App-Screen": "B"})
    assert r.status_code == 200


def test_停止は使っていない画面からでも通る(client):
    """取り上げられた画面からアプリを終われないと、閉じ方が無くなる。"""
    client.post("/api/screen/claim", json={"screen": "A"})
    r = client.post("/api/shutdown", json={}, headers={"X-App-Screen": "B"})
    # 止め方が登録されていないので 500 だが、**409 ではない**
    assert r.status_code != 409


def test_心拍は使っていない画面からでも通る(client):
    """ここを断ると、心拍が止まってアプリが終わってしまう。"""
    client.post("/api/screen/claim", json={"screen": "A"})
    assert client.post("/api/alive", json={"screen": "B"}).status_code == 200


# ==================================================================
# 画面に覆いが載っていること
# ==================================================================
def test_どの画面にも覆いが載っている(client):
    """**隠しておいて後から掛けるのではない。** 掛かるまでの一瞬に
    打てては意味が無いので、通るまで覆ったまま出す。"""
    for path in ("/calc", "/checklist", "/order", "/settings"):
        page = client.get(f"{path}?t=test-token").data.decode()
        # `hidden` が付いていたら、掛かるまでの隙ができる
        assert '<div class="gate" id="gate"' in page, path


def test_通るまで帯も本体も出さない(client):
    """覆うだけでは、Tab キーで後ろの欄に入れてしまう。

    断られている画面から打ててしまっては覆いの意味が無いので、
    **出さない**ほうで塞ぐ(`app.js` が通ったときに外す)。
    """
    for path in ("/calc", "/checklist", "/order", "/settings"):
        page = client.get(f"{path}?t=test-token").data.decode()
        assert '<header class="ribbon" hidden>' in page, path
        assert '<div class="shell" hidden>' in page, path
