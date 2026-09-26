"""配布設定 ── ツールの直下の `配布設定\\` を、配った先の端末が読み込む

姉妹ツール python-web-tools の `tests/test_distribution.py` と同じことを
確かめる(配置図はこのツールに無いので除く)。

    1. 1台で設定して、設定画面の「配布設定」で書き出す
    2. `配布設定\\` が作られ、配下に必要なものが入る
    3. 配った先は `配布設定\\` があれば読み込む
    4. その端末にすでにある設定は読み込まない
"""
import json
from pathlib import Path

import pytest

from modules.packing_material_calculation.coil_tool import admin_password, config, distribution, user_settings
from modules.packing_material_calculation.tests._web import client, sandbox   # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
NEW_PW = "newpass123"


class Terminal:
    """1台ぶんの設定ファイル。`use()` でその端末に切り替える。"""

    def __init__(self, monkeypatch, path: Path) -> None:
        self.monkeypatch = monkeypatch
        self.path = path

    def use(self) -> None:
        self.monkeypatch.setattr(config, "USER_CONFIG_PATH", self.path)


@pytest.fixture
def terminals(sandbox, monkeypatch):   # noqa: F811
    return (Terminal(monkeypatch, sandbox / "source" / "user_config.json"),
            Terminal(monkeypatch, sandbox / "dest" / "user_config.json"))


def configure(source: Terminal) -> None:
    source.use()
    user_settings.save_many({
        config.KEY_MASTER_DB_DIR: r"\\srv\共有\マスタ",
        config.KEY_LOT_DB_DIR: r"\\srv\台帳",
        config.KEY_AUTO_IMPORT: False,
        config.KEY_STOCK_LENGTHS: [700, 800, 900],
        config.KEY_WORKER: "高村敏幸",
    })
    user_settings.set_line("NS1")
    assert admin_password.change(config.ADMIN_PASSWORD, NEW_PW, NEW_PW).ok


def export(items=None):
    items = [k for k, _, default in distribution.ITEMS if default] \
        if items is None else items
    return distribution.export(list(items))


def written() -> dict:
    return json.loads(distribution.settings_path().read_text(encoding="utf-8"))


# ==================================================================
# 書き出す
# ==================================================================
def test_配布設定フォルダが作られ配下に入る(terminals):
    configure(terminals[0])
    assert export().ok
    names = {p.relative_to(distribution.DIR).as_posix()
             for p in distribution.DIR.rglob("*") if p.is_file()}
    assert names == {"設定.json", "はじめに読む.txt"}
    assert not distribution.DIR.with_name("配布設定.作成中").exists()


def test_選んだものだけ_ラインは既定で入れない_担当者は入れられない(terminals):
    configure(terminals[0])
    export()
    settings = written()["settings"]
    assert settings[config.KEY_MASTER_DB_DIR] == r"\\srv\共有\マスタ"
    assert settings[config.KEY_AUTO_IMPORT] is False
    assert settings[config.KEY_STOCK_LENGTHS] == [700, 800, 900]
    assert config.KEY_LINE not in settings
    assert config.KEY_WORKER not in settings
    assert config.KEY_WORKER not in distribution.ITEM_KEYS
    # パスワードは撹拌した値だけ。平文はどこにも入らない
    assert admin_password.KEY in settings
    for path in distribution.DIR.rglob("*"):
        assert NEW_PW not in path.read_text(encoding="utf-8-sig"), path


def test_ラインは選べば入る(terminals):
    configure(terminals[0])
    export([config.KEY_LINE])
    assert written()["settings"] == {config.KEY_LINE: "NS1"}


def test_設定していない項目は入れない(terminals):
    """配った先の既定値を「空」で上書きしない。"""
    configure(terminals[0])
    result = export()
    assert config.KEY_EXPORT_DIR not in written()["settings"]
    assert "既定のまま" in result.message


def test_書き出せるものが1つも無ければ作らない(terminals):
    terminals[0].use()
    result = export([config.KEY_EXPORT_DIR])
    assert result.reason == distribution.REFUSE_BAD_INPUT
    assert not distribution.DIR.exists()


def test_書き出し直すと前の中身は置き換わる(terminals):
    configure(terminals[0])
    export()
    export([config.KEY_LOT_DB_DIR])
    assert written()["settings"] == {config.KEY_LOT_DB_DIR: r"\\srv\台帳"}


def test_画面にパスワードの値を出さない(terminals):
    configure(terminals[0])
    export()
    text = json.dumps(distribution.summary(), ensure_ascii=False)
    assert user_settings.get(admin_password.KEY) not in text
    assert "(設定済み)" in text


def test_知らない項目と空は断る(terminals):
    configure(terminals[0])
    assert export(["謎"]).reason == distribution.REFUSE_BAD_INPUT
    assert export([]).reason == distribution.REFUSE_BAD_INPUT


def test_消してもこの端末の設定はそのまま(terminals):
    configure(terminals[0])
    export()
    assert distribution.remove().ok
    assert not distribution.DIR.exists()
    assert user_settings.get(config.KEY_LOT_DB_DIR) == r"\\srv\台帳"


# ==================================================================
# 読み込む(配られた側)
# ==================================================================
def distributed(terminals, items=None):
    configure(terminals[0])
    assert export(items).ok
    terminals[1].use()


def test_配布設定フォルダがあれば読み込む(terminals):
    distributed(terminals)
    result = distribution.apply_on_start()
    assert "梱包資材マスタのフォルダ" in result.applied
    assert user_settings.get(config.KEY_MASTER_DB_DIR) == r"\\srv\共有\マスタ"
    assert user_settings.get(config.KEY_AUTO_IMPORT) is False
    assert admin_password.verify(NEW_PW)
    assert user_settings.get(config.KEY_LINE) is None
    assert user_settings.get(config.KEY_WORKER) is None
    # 置き場所は**その場で効く**(次の取り込みがそこを見る)
    assert config.master_db_dir() == config.resolve_dir(r"\\srv\共有\マスタ")


def test_既存データがある項目は読み込まない(terminals):
    distributed(terminals)
    user_settings.save(config.KEY_LOT_DB_DIR, r"\\この端末\台帳")
    result = distribution.apply_on_start()
    assert user_settings.get(config.KEY_LOT_DB_DIR) == r"\\この端末\台帳"
    assert "仕掛台帳のフォルダ" in result.kept
    # 無い項目は埋める
    assert user_settings.get(config.KEY_MASTER_DB_DIR) == r"\\srv\共有\マスタ"


def test_起動のたびに見ても端末で直した値は戻さない(terminals):
    distributed(terminals)
    distribution.apply_on_start()
    user_settings.save(config.KEY_AUTO_IMPORT, True)     # 端末で直した
    assert distribution.apply_on_start().applied == []
    assert user_settings.get(config.KEY_AUTO_IMPORT) is True


def test_読み込み直しは上書きする(terminals):
    distributed(terminals)
    user_settings.save(config.KEY_LOT_DB_DIR, r"\\この端末\台帳")
    result = distribution.reapply()
    assert result.ok
    assert user_settings.get(config.KEY_LOT_DB_DIR) == r"\\srv\台帳"


def test_置かれていなければ読み込み直せない(terminals):
    terminals[1].use()
    assert distribution.reapply().reason == distribution.REFUSE_BAD_INPUT


def test_無い_壊れた_形が違うなら何もしない(terminals):
    terminals[1].use()
    assert distribution.apply_on_start().applied == []
    distribution.DIR.mkdir(parents=True)
    distribution.settings_path().write_text("{壊れ", encoding="utf-8")
    assert distribution.apply_on_start().applied == []
    distribution.settings_path().write_text(json.dumps({"format": 99, "settings": {
        config.KEY_LOT_DB_DIR: "x"}}), encoding="utf-8")
    assert distribution.apply_on_start().applied == []
    assert user_settings.get(config.KEY_LOT_DB_DIR) is None


def test_知らない鍵は入れない(terminals):
    """担当者の鍵を手で書き足されても入れない(全端末が同じ人になる)。"""
    terminals[1].use()
    distribution.DIR.mkdir(parents=True)
    distribution.settings_path().write_text(json.dumps({"format": 1, "settings": {
        "謎の鍵": 1, config.KEY_WORKER: "誰か", config.KEY_LOT_DB_DIR: r"\\x"}}),
        encoding="utf-8")
    distribution.apply_on_start()
    assert "謎の鍵" not in user_settings.load_all()
    assert config.KEY_WORKER not in user_settings.load_all()
    assert user_settings.get(config.KEY_LOT_DB_DIR) == r"\\x"


# ==================================================================
# 起動と置き場所
# ==================================================================
def test_取り込みより先に読む():
    """置き場所が入っているので、読む前に取り込むと既定の場所を見る。
    移し替え(`migrate_local`)のあとに読む ── 先に読むと、移す前の
    設定ファイルを「無い」と見て配布の値で埋めてしまう。"""
    # 統合版では、起動時の初期化は機能の入口(`modules/<機能>/__init__.py` の
    # `initialize`)にある。順番の約束は移植元の `start_app._initialize` と同じ
    text = (ROOT / "__init__.py").read_text(encoding="utf-8")
    body = text[text.index("def initialize"):]
    at = body.index("distribution.apply_on_start()")
    assert body.index("config.migrate_local()") < at
    assert at < body.index("_diagnose_sources(")
    assert at < body.index("_start_auto_import(")


def test_配布設定は追跡しない():
    # `.gitignore` は統合リポジトリの根に1つ
    assert "配布設定/" in (ROOT.parent.parent / ".gitignore").read_text(encoding="utf-8")


def test_置き場所は統合アプリの直下():
    """統合版では統合アプリのフォルダの直下 `配布設定\\packing_material_calculation\\`。
    機能ごとに分けるのは、設定の鍵(項目)が機能ごとに違うため。"""
    assert distribution.DIR == (config.BASE_DIR.parent.parent / "配布設定"
                                / "packing_material_calculation")


# ==================================================================
# 画面と API
# ==================================================================
def unlock(c):
    assert c.post("/api/master/auth",
                  json={"password": config.ADMIN_PASSWORD}).status_code == 200


def test_認証していなければ403で_何も作らない(client):
    for path, body in (("/api/settings/distribution/export",
                        {"items": [config.KEY_LOT_DB_DIR]}),
                       ("/api/settings/distribution/reapply", {}),
                       ("/api/settings/distribution/remove", {})):
        r = client.post(path, json=body)
        assert r.status_code == 403, path
        assert r.get_json()["reason"] == "needs_password"
    assert not distribution.DIR.exists()


def test_書き出すと状態に出る(client):
    unlock(client)
    user_settings.save(config.KEY_LOT_DB_DIR, r"\\srv\台帳")
    r = client.post("/api/settings/distribution/export",
                    json={"items": [config.KEY_LOT_DB_DIR, config.KEY_EXPORT_DIR]})
    assert r.status_code == 200, r.get_json()
    dist = r.get_json()["view"]["distribution"]
    assert dist["exists"]
    assert dist["contents"] == [{"label": "仕掛台帳のフォルダ", "value": r"\\srv\台帳"}]
    assert "書き出し先" in r.get_json()["message"]      # 既定のままで入れなかった


def test_形が違えば400(client):
    unlock(client)
    r = client.post("/api/settings/distribution/export", json={"items": "lot"})
    assert r.status_code == 400


def test_読み込み直すと画面の置き場所も変わる(client):
    unlock(client)
    user_settings.save(config.KEY_LOT_DB_DIR, r"\\srv\台帳")
    client.post("/api/settings/distribution/export", json={"items": [config.KEY_LOT_DB_DIR]})
    user_settings.save(config.KEY_LOT_DB_DIR, r"\\この端末\台帳")
    r = client.post("/api/settings/distribution/reapply", json={})
    assert r.status_code == 200, r.get_json()
    field = next(f for f in r.get_json()["view"]["fields"]
                 if f["key"] == config.KEY_LOT_DB_DIR)
    assert field["value"] == r"\\srv\台帳"


def test_画面に面がある(client):
    html = client.get("/settings?t=test-token").get_data(as_text=True)
    assert 'id="tab-distribution"' in html
    assert 'id="panel-distribution"' in html
    assert 'id="distExport"' in html
    # パスワード欄は面の中に置かない(いちばん上の認証を使う)
    panel = html[html.index('id="panel-distribution"'):html.index("/panel-distribution")]
    assert 'type="password"' not in panel
