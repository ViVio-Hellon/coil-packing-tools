"""設定画面 ── 置き場所を打って、確かめて、保存する"""
import json

from modules.packing_material_calculation.coil_tool import app_config, config, user_settings
from modules.packing_material_calculation.tests._web import client, sandbox, seed   # noqa: F401


def unlock(c):
    """置き場所を変えるための認証。

    **置き場所はパスワードが要る。** 間違えるとその端末が計算できなく
    なる(マスタを読めない)ので、担当者の選び直しとは重みが違う。
    """
    r = c.post("/api/master/auth", json={"password": config.ADMIN_PASSWORD})
    assert r.status_code == 200
    return r


def test_page_opens(client):
    r = client.get("/settings?t=test-token")
    assert r.status_code == 200
    assert "設定".encode() in r.data


def test_view_shows_defaults_and_resolved_paths(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    view = r.get_json()["view"]
    keys = {f["key"] for f in view["fields"]}
    assert config.KEY_MASTER_DB_DIR in keys
    assert config.KEY_LOT_DB_DIR in keys
    assert config.KEY_LOT_DB_DIR_2 in keys
    for f in view["fields"]:
        assert f["using_default"] is True
        # 打っていなくても「いま見に行く場所」が出る
        assert "resolved" in f


def test_save_path_and_it_takes_effect(client, sandbox):
    target = sandbox / "share" / "master"
    target.mkdir(parents=True)
    unlock(client)
    r = client.post("/api/settings",
                    json={config.KEY_MASTER_DB_DIR: str(target)})
    assert r.status_code == 200
    assert config.master_db_dir() == target
    # 保存されたものが画面にも返る
    view = r.get_json()["view"]
    master = next(f for f in view["fields"] if f["key"] == config.KEY_MASTER_DB_DIR)
    assert master["value"] == str(target)
    assert master["using_default"] is False


def test_relative_path_resolves_from_app_dir(client):
    unlock(client)
    r = client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: "data/src"})
    view = r.get_json()["view"]
    master = next(f for f in view["fields"] if f["key"] == config.KEY_MASTER_DB_DIR)
    assert master["relative"] is True
    # いまの作業フォルダではなく、アプリのフォルダから見る
    # 統合版: アプリのフォルダ = 統合アプリの根(Start.vbs がある所)。機能のフォルダではない
    assert master["resolved"] == str(config.APP_DIR / "data" / "src")
    assert config.APP_DIR != config.BASE_DIR


def test_probe_reports_missing_folder(client, sandbox):
    r = client.post("/api/settings/probe",
                    json={config.KEY_MASTER_DB_DIR: str(sandbox / "nope")})
    res = r.get_json()["results"][0]
    assert res["ok"] is False
    assert "ありません" in res["message"]


def test_probe_reports_empty_folder(client, sandbox):
    empty = sandbox / "empty"
    empty.mkdir()
    r = client.post("/api/settings/probe",
                    json={config.KEY_MASTER_DB_DIR: str(empty)})
    res = r.get_json()["results"][0]
    assert res["ok"] is False
    assert "見つかりません" in res["message"]


def test_probe_finds_sqlite_files(client, sandbox):
    import sqlite3
    folder = sandbox / "share"
    folder.mkdir()
    conn = sqlite3.connect(folder / "梱包資材マスタ.sqlite3")
    conn.execute("CREATE TABLE t (a)")
    conn.commit()
    conn.close()

    r = client.post("/api/settings/probe",
                    json={config.KEY_MASTER_DB_DIR: str(folder)})
    res = r.get_json()["results"][0]
    assert res["ok"] is True
    assert "梱包資材マスタ.sqlite3" in res["found"]


def test_probe_checks_writability_for_export(client, sandbox):
    folder = sandbox / "out"
    folder.mkdir()
    r = client.post("/api/settings/probe", json={config.KEY_EXPORT_DIR: str(folder)})
    res = r.get_json()["results"][0]
    assert res["ok"] is True
    assert "書けます" in res["message"]


def test_optional_spare_folder_may_stay_empty(client):
    r = client.post("/api/settings/probe", json={config.KEY_LOT_DB_DIR_2: ""})
    res = r.get_json()["results"][0]
    assert res["ok"] is True
    assert config.lot_db_dir_fallback() is None


def test_spare_folder_is_added_to_search_order(client, sandbox):
    spare = sandbox / "spare"
    spare.mkdir()
    unlock(client)
    client.post("/api/settings", json={config.KEY_LOT_DB_DIR_2: str(spare)})
    dirs = config.lot_db_dirs()
    assert len(dirs) == 2
    assert dirs[1] == spare


def test_line_and_worker_are_saved(client):
    r = client.post("/api/settings", json={"line": "NS1", "worker": config.WORKERS[1]})
    assert r.status_code == 200
    assert user_settings.get_line() == "NS1"
    assert user_settings.get_worker() == config.WORKERS[1]


def test_unknown_line_is_refused(client):
    r = client.post("/api/settings", json={"line": "XX9"})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "bad_line"


def test_unknown_worker_is_refused(client):
    r = client.post("/api/settings", json={"worker": "知らない人"})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "bad_worker"


def test_reset_returns_to_default(client, sandbox):
    unlock(client)
    client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: str(sandbox)})
    assert config.master_db_dir() == sandbox
    r = client.post("/api/settings/reset", json={"key": config.KEY_MASTER_DB_DIR})
    assert r.status_code == 200
    assert config.master_db_dir() == config.DEFAULT_MASTER_DB_DIR


def test_reset_unknown_key_is_refused(client):
    r = client.post("/api/settings/reset", json={"key": "nope"})
    assert r.status_code == 400


def test_import_reports_missing_source(client):
    """置き場所に届かなくても、理由を返して落ちない"""
    r = client.post("/api/settings/import", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is False
    assert all(not t["ok"] for t in body["tables"])
    assert any("見つかりません" in t["error"] for t in body["tables"])


def test_token_is_required(sandbox):
    from modules.packing_material_calculation.app import create_app
    from modules.packing_material_calculation.coil_tool import db
    with db.connect() as conn:
        db.apply_schema(conn)
    app = create_app(token="test-token")
    app.config["READY"] = True
    with app.test_client() as c:
        assert c.get("/api/settings").status_code == 403
        assert c.get("/api/health").status_code == 200   # 素通し


# ==================================================================
# 版を見せる
#
# 現場の端末はフォルダごと写して配るので、「いまどれが入っているか」を
# 答えられないと、直したはずの不具合の話が噛み合わない。**番号は
# `config/app.json` の1か所が出どころ**で、帯のバッジ・`/api/health`・
# 設定画面が同じものを読む ── ここが崩れると、画面とAPIで違う版を
# 名乗ることになる。
# ==================================================================
def test_版は帯にも設定にも同じものが出る(client):
    page = client.get("/settings?t=test-token").data.decode()
    label = app_config.version_label()
    # 帯のバッジ
    assert 'class="ver"' in page
    assert label in page

    about = client.get("/api/settings").get_json()["view"]["about"]
    assert about["version_label"] == label
    assert about["version"] == app_config.version()

    health = client.get("/api/health").get_json()
    assert health["version"] == about["version"]


def test_このアプリについてが素性を全部出す(client):
    about = client.get("/api/settings").get_json()["view"]["about"]
    for key in ("display_name", "version", "version_label", "app_id",
                "mode", "port", "actual_port", "python", "python_exe",
                "app_root", "app_config_path", "local_root"):
        assert about[key] != "" and about[key] is not None, key
    # 正常なら警告は空。**空でないことを「異常あり」の印にしている**
    assert about["version_problem"] == ""


def test_帯のバッジから設定の詳しいところへ飛べる(client):
    """どの画面からでも押せる ── 版を聞かれるのは計算中が多い。"""
    for path in ("/calc", "/checklist", "/order", "/settings"):
        page = client.get(f"{path}?t=test-token").data.decode()
        assert 'class="ver" href="/settings?t=test-token#about"' in page, path


def test_版の書き方が壊れていたら設定画面で言う(client, monkeypatch):
    """起動は止めない。**止めると版を直す画面にも入れない。**"""
    monkeypatch.setattr(app_config, "version", lambda: "0.1")
    about = client.get("/api/settings").get_json()["view"]["about"]
    assert about["version_problem"] != ""
    assert "0.1" in about["version_problem"]


# ==================================================================
# 置き場所を変えるにはパスワードが要る
#
# 置き場所を間違えると、**その端末が計算できなくなる**(マスタを
# 読めない)。担当者を間違えても出てくる票の名前が違うだけなので、
# 関門は置き場所にだけ置く。
# ==================================================================
def test_認証なしでは置き場所を変えられない(client, sandbox):
    target = sandbox / "share"
    target.mkdir(parents=True)
    r = client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: str(target)})
    assert r.status_code == 403
    from modules.packing_material_calculation.app.routes import settings as settings_routes
    assert r.get_json()["reason"] == settings_routes.REFUSE_NEEDS_PASSWORD
    # **断ったら1つも書かない。** 半分だけ変わるのがいちばん困る
    assert config.master_db_dir() == config.DEFAULT_MASTER_DB_DIR


def test_認証なしでも担当者とラインは変えられる(client):
    """使う人ごとの持ち物。毎回パスワードを聞かれては仕事にならない。"""
    r = client.post("/api/settings", json={"line": "NS1", "worker": config.WORKERS[1]})
    assert r.status_code == 200
    assert user_settings.get_worker() == config.WORKERS[1]


def test_認証なしでは既定に戻せない(client, sandbox):
    """戻すのも「置き場所を変えること」。同じ関門を通す。"""
    unlock(client)
    client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: str(sandbox)})
    client.post("/api/master/auth", json={"close": True})

    r = client.post("/api/settings/reset", json={"key": config.KEY_MASTER_DB_DIR})
    assert r.status_code == 403
    assert config.master_db_dir() == sandbox      # 変わっていない


def test_確かめるだけならパスワードは要らない(client, sandbox):
    """**打つ前に届くか見る**のは、まだ何も変えていない。"""
    r = client.post("/api/settings/probe",
                    json={config.KEY_MASTER_DB_DIR: str(sandbox)})
    assert r.status_code == 200


def test_画面は押す前に関門の状態を知れる(client):
    """押してから断られるのは手戻り。先に出す。"""
    assert client.get("/api/settings").get_json()["view"]["can_edit_paths"] is False
    unlock(client)
    assert client.get("/api/settings").get_json()["view"]["can_edit_paths"] is True


def test_欄ごとに1つだけ保存できる(client, sandbox):
    """一番下の1つで全部保存すると、直すつもりのない欄まで書いてしまう。"""
    a, b = sandbox / "a", sandbox / "b"
    a.mkdir(); b.mkdir()
    unlock(client)
    client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: str(a),
                                       config.KEY_LOT_DB_DIR: str(b)})
    # 片方だけ送り直す
    c = sandbox / "c"; c.mkdir()
    r = client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: str(c)})
    assert r.status_code == 200
    assert config.master_db_dir() == c
    assert config.lot_db_dir() == b        # 送っていない欄はそのまま


# ==================================================================
# 画面の形
# ==================================================================
def test_設定は分類ごとの面に分かれている(client):
    """縦に長いと、下にあるものは「無いもの」として扱われる。"""
    page = client.get("/settings?t=test-token").data.decode()
    for key, label in (("source", "取り込み"), ("master", "マスタ管理"),
                       ("team", "ラインと担当者"), ("about", "このアプリ")):
        assert f'id="tab-{key}"' in page, key
        assert f'id="panel-{key}"' in page, key
        assert label in page


def test_最初に出ている面は1つだけ(client):
    """全部 `hidden` だと空に見え、全部出ていると分けた意味が無い。"""
    page = client.get("/settings?t=test-token").data.decode()
    shown = [k for k in ("source", "master", "team", "about")
             if f'id="panel-{k}" role="tabpanel"' in page
             and f'id="panel-{k}" role="tabpanel" aria-labelledby="tab-{k}" hidden'
             not in page]
    assert shown == ["source"], shown


def test_1行を開く窓は面の外にある(client):
    """隠れている面の中にあると、開こうとしても出てこない

    (祖先が `display:none` になるため)。
    """
    page = client.get("/settings?t=test-token").data.decode()
    assert page.index("<!-- /tabs -->") < page.index('<dialog id="mEdit"')


def test_まとめて保存するボタンは置かない(client):
    """1つで全部書くと、直すつもりのない欄まで上書きする。"""
    page = client.get("/settings?t=test-token").data.decode()
    assert 'id="save"' not in page
    assert 'id="probe"' not in page
