"""マスタ管理 ── 取り込み元を見て、直す

【ここで確かめている思想】
1. 書き先は取り込み元ただ1つ。手元は総入れ替えなので、書いたあとに
   その表だけ取り込み直して手元を追いつかせる
2. 行は取り込み元の言葉(`rowid`)で指す。手元の `id` は取り込みの
   ときに振り直されるので、取り込み元の行を指さない
3. 直せない表は隠さず、理由を出す
"""
import sqlite3

import pytest

from modules.packing_material_calculation.coil_tool import config, db, import_specs, master_admin
from modules.packing_material_calculation.tests._web import client, sandbox   # noqa: F401


# ==================================================================
# 取り込み元のマスタを作る
# ==================================================================
SPEC_COLS = [src for _d, src, _c in import_specs.MASTER_IMPORT_SPECS["包装仕様"]]
PALLET_COLS = [src for _d, src, _c in import_specs.MASTER_IMPORT_SPECS["パレット"]]
RIPLA_COLS = [src for _d, src, _c in import_specs.MASTER_IMPORT_SPECS["リプラサイズ"]]


def _create(conn, table, cols):
    quoted = ", ".join(f'"{c}" TEXT' for c in cols)
    conn.execute(f'CREATE TABLE "{table}" ({quoted})')


def make_source(sandbox, *, spec_rows=2, extra_table=True, spec_cols=None):
    """共有フォルダに見立てた梱包資材マスタを1つ作る。"""
    folder = sandbox / "master"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / config.MATERIAL_DB_NAME
    conn = sqlite3.connect(path)

    _create(conn, "包装仕様", spec_cols or SPEC_COLS)
    for i in range(spec_rows):
        values = {c: "" for c in (spec_cols or SPEC_COLS)}
        if "包装仕様NO" in values:
            values["包装仕様NO"] = f"1C00{i:02d}"
            values["パレット"] = "スカシ"
            values["納入先名称"] = f"取引先{i}"
            values["リプラサイズ"] = "30"
        cols = ", ".join(f'"{c}"' for c in values)
        marks = ", ".join("?" for _ in values)
        conn.execute(f'INSERT INTO "包装仕様" ({cols}) VALUES ({marks})',
                     list(values.values()))

    _create(conn, "パレット", PALLET_COLS)
    conn.execute(
        'INSERT INTO "パレット" ("種類","巾下限","巾上限","丈下限","丈上限",'
        '"新記号","Ｗ") VALUES (?,?,?,?,?,?,?)',
        ("スカシ", "0", "900", "0", "900", "P09", "900"))

    _create(conn, "リプラサイズ", RIPLA_COLS)
    conn.execute('INSERT INTO "リプラサイズ" ("リプラ長さ","長さ") VALUES (?,?)',
                 ("900", "900"))

    if extra_table:
        # 姉妹ツールの表。**同じファイルに同居する**
        _create(conn, "PalletMaster", ["幅", "丈"])
        conn.execute('INSERT INTO "PalletMaster" ("幅","丈") VALUES ("1100","1100")')

    conn.commit()
    conn.close()
    return path


def unlock(c, password=config.ADMIN_PASSWORD):
    """マスタ編集の認証を通す。"""
    return c.post("/api/master/auth", json={"password": password})


@pytest.fixture
def looking(client, sandbox):
    """取り込み元があり、設定がそこを指している。**認証はまだ**。"""
    make_source(sandbox)
    client.post("/api/settings",
                json={config.KEY_MASTER_DB_DIR: str(sandbox / "master")})
    return client


@pytest.fixture
def ready(looking):
    """認証まで通した状態。直すテストはこちらを使う。"""
    unlock(looking)
    return looking


def browse(c, **params):
    from urllib.parse import urlencode
    return c.get("/api/master/browse?" + urlencode(params)).get_json()


def first_key(c, table="包装仕様"):
    """取り込み元の1行目を指す鍵(`rowid`)。"""
    body = browse(c, table=table)
    return body["page"]["rows"][0][body["row_key"]]


# ==================================================================
# 見る
# ==================================================================
def test_表と中身が返る(looking):
    body = browse(looking)
    assert body["loaded"] is True
    names = [t["table"] for t in body["tables"]]
    # 直せる3表が先頭。並びは触る頻度の順
    assert names[:3] == ["包装仕様", "パレット", "リプラサイズ"]
    assert body["table"] == "包装仕様"
    assert body["page"]["total"] == 2


def test_表を指定して引ける(looking):
    body = browse(looking, table="パレット")
    assert body["table"] == "パレット"
    assert body["page"]["total"] == 1
    assert "新記号" in body["page"]["columns"]


def test_絞り込める(looking):
    body = browse(looking, table="包装仕様", q="1C0001")
    assert body["page"]["total"] == 1


def test_絞り込みはどの列でも効く(looking):
    """どの列に何が入っているか覚えていなくても引ける"""
    body = browse(looking, table="包装仕様", q="取引先0")
    assert body["page"]["total"] == 1


def test_知らない表を指定したら先頭に戻す(looking):
    body = browse(looking, table="そんな表はない")
    assert body["table"] == "包装仕様"


def test_姉妹ツールの表は見るだけで理由が出る(ready):
    """直せない表は**隠さず**、理由を出す(思想3)"""
    body = browse(ready, table="PalletMaster")
    assert body["page"]["editable"] is False
    assert "梱包資材総合ツール" in body["page"]["why"]
    # 一覧にも出る
    info = next(t for t in body["tables"] if t["table"] == "PalletMaster")
    assert info["editable"] is False and info["why"]


def test_並び替えられる(looking):
    body = browse(looking, table="包装仕様", sort="包装仕様NO", sort_dir="desc")
    assert body["page"]["sort"] == "包装仕様NO"
    assert body["page"]["sort_dir"] == "desc"
    assert body["page"]["rows"][0]["包装仕様NO"] == "1C0001"


def test_数字の列は数の大きさで並ぶ(looking, sandbox):
    """取り込み元は数字も文字(TEXT)で持つ。文字の並びだと 800 が 1350 の後ろへ来る(統合版で直した)。

    空はいちばん後ろ(昇順でも降順でも)。
    """
    conn = sqlite3.connect(sandbox / "master" / config.MATERIAL_DB_NAME)
    conn.executemany('INSERT INTO "リプラサイズ" ("リプラ長さ","長さ") VALUES (?,?)',
                     [("1350", "1350"), ("800", "800"), ("", ""), ("1000", "1000")])
    conn.commit()
    conn.close()
    up = browse(looking, table="リプラサイズ", sort="長さ", sort_dir="asc")
    assert [r["長さ"] for r in up["page"]["rows"]] == ["800", "900", "1000", "1350", ""]
    down = browse(looking, table="リプラサイズ", sort="長さ", sort_dir="desc")
    assert [r["長さ"] for r in down["page"]["rows"]] == ["1350", "1000", "900", "800", ""]


def test_知らない列での並び替えは既定へ静かに戻す(looking):
    """もう無い列を指した並び替えを断ると「さっきまで押せたのに」になる"""
    body = browse(looking, table="包装仕様", sort="そんな列はない")
    assert body["page"]["sort"] == ""


def test_行は取り込み元のrowidで指す(looking):
    """手元の id ではなく取り込み元の rowid(思想2)"""
    body = browse(looking, table="包装仕様")
    assert body["row_key"] == master_admin.ROW_KEY
    assert body["page"]["rows"][0][master_admin.ROW_KEY] == 1


def test_取り込み元が無ければ理由を出す(client, sandbox):
    body = browse(client)
    assert body["tables"] == []
    assert body["page"]["error"]
    assert "見つかりません" in body["page"]["error"]


def test_打ち込める列は取り込み元にある列だけ(looking):
    body = browse(looking, table="包装仕様")
    names = [c["name"] for c in body["columns"]]
    assert "包装仕様NO" in names
    # 型の言い方はサーバが持つ
    assert body["columns"][0]["kind_label"] in ("整数", "小数", "文字")


def test_鍵の列は必須になる(looking):
    """手元のスキーマは既定値つきだが、取り込みが鍵と見なす列は空にできない"""
    body = browse(looking, table="包装仕様")
    spec_no = next(c for c in body["columns"] if c["name"] == "包装仕様NO")
    assert spec_no["required"] is True
    assert "捨てられます" in spec_no["note"]


# ==================================================================
# 直す
# ==================================================================
def test_直すと画面ぜんぶが返る(ready):
    key = first_key(ready)
    r = ready.post("/api/master/row/save",
                   json={"table": "包装仕様", "key": key,
                         "values": {"パレット": "全面"}})
    assert r.status_code == 200
    body = r.get_json()
    # 「保存しました」だけでなく、いまの状態がまるごと入っている
    assert body["page"]["rows"][0]["パレット"] == "全面"
    assert body["message"]


def test_直すと取り込み元が変わる(ready, sandbox):
    """書き先は取り込み元ただ1つ(思想1)"""
    key = first_key(ready)
    ready.post("/api/master/row/save",
               json={"table": "包装仕様", "key": key,
                     "values": {"パレット": "EXPS"}})
    conn = sqlite3.connect(sandbox / "master" / config.MATERIAL_DB_NAME)
    got = conn.execute('SELECT "パレット" FROM "包装仕様" WHERE rowid = ?',
                       (key,)).fetchone()
    conn.close()
    assert got[0] == "EXPS"


def test_直すと手元も追いつく(ready):
    """書いたあと、その表だけ取り込み直す(思想1)"""
    key = first_key(ready)
    r = ready.post("/api/master/row/save",
                   json={"table": "包装仕様", "key": key,
                         "values": {"パレット": "ﾎｲｰﾙ"}})
    assert "手元も" in r.get_json()["message"]
    with db.connect() as conn:
        row = conn.execute(
            "SELECT パレット FROM 包装仕様 WHERE 包装仕様NO = '1C0000'").fetchone()
    assert row["パレット"] == "ﾎｲｰﾙ"


def test_直したら計算をやり直すよう言う(ready):
    """開いたままの計算結果は、直した時点でもう古い"""
    key = first_key(ready)
    r = ready.post("/api/master/row/save",
                   json={"table": "包装仕様", "key": key,
                         "values": {"パレット": "全面"}})
    assert "やり直す" in r.get_json()["message"]


def test_足せる(ready):
    r = ready.post("/api/master/row/add",
                   json={"table": "包装仕様",
                         "values": {"包装仕様NO": "1C9999", "パレット": "スカシ"}})
    assert r.status_code == 200
    assert r.get_json()["page"]["total"] == 3


def test_消せる(ready):
    key = first_key(ready)
    r = ready.post("/api/master/row/delete",
                   json={"table": "包装仕様", "key": key})
    assert r.status_code == 200
    assert r.get_json()["page"]["total"] == 1


def test_絞り込みと並び替えは書いたあとも残る(ready):
    key = first_key(ready)
    r = ready.post("/api/master/row/save",
                   json={"table": "包装仕様", "key": key, "q": "1C0000",
                         "sort": "包装仕様NO", "sort_dir": "desc",
                         "values": {"パレット": "全面"}})
    body = r.get_json()
    assert body["query"] == "1C0000"
    assert body["page"]["sort"] == "包装仕様NO"
    assert body["page"]["sort_dir"] == "desc"


def test_書き換えは送った列だけ触る(ready):
    """送っていない列を消さない"""
    key = first_key(ready)
    ready.post("/api/master/row/save",
               json={"table": "包装仕様", "key": key,
                     "values": {"パレット": "全面"}})
    body = browse(ready, table="包装仕様")
    assert body["page"]["rows"][0]["納入先名称"] == "取引先0"


# ==================================================================
# 断り方
# ==================================================================
def test_入力の形が違えば400(ready):
    key = first_key(ready, "パレット")
    r = ready.post("/api/master/row/save",
                   json={"table": "パレット", "key": key,
                         "values": {"Ｗ": "あいうえお"}})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == master_admin.REFUSE_BAD_VALUE


def test_鍵を空にしようとすれば400(ready):
    r = ready.post("/api/master/row/add",
                   json={"table": "包装仕様", "values": {"パレット": "スカシ"}})
    assert r.status_code == 400
    assert "空にできません" in r.get_json()["error"]["message"]


def test_先を越されていれば409(ready):
    r = ready.post("/api/master/row/save",
                   json={"table": "包装仕様", "key": 9999,
                         "values": {"パレット": "全面"}})
    assert r.status_code == 409
    assert r.get_json()["error"]["code"] == master_admin.REFUSE_NO_ROW


def test_直せない表は422(ready):
    key = first_key(ready, "PalletMaster")
    r = ready.post("/api/master/row/save",
                   json={"table": "PalletMaster", "key": key,
                         "values": {"幅": "1200"}})
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == master_admin.REFUSE_NOT_EDITABLE


def test_取り込み元に届かなければ422(client, sandbox):
    unlock(client)
    r = client.post("/api/master/row/add",
                    json={"table": "包装仕様", "values": {"包装仕様NO": "1C0001"}})
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == master_admin.REFUSE_NO_SOURCE


def test_断ったときも画面ぜんぶが入っている(ready):
    r = ready.post("/api/master/row/save",
                   json={"table": "包装仕様", "key": 9999,
                         "values": {"パレット": "全面"}})
    body = r.get_json()
    assert body["error"]["code"]
    # 「断られた」と「画面が古い」を同時に起こさない
    assert body["page"]["total"] == 2
    assert body["tables"]


def test_断ったときは何も動いていない(ready, sandbox):
    key = first_key(ready, "パレット")
    ready.post("/api/master/row/save",
               json={"table": "パレット", "key": key,
                     "values": {"Ｗ": "あいうえお"}})
    conn = sqlite3.connect(sandbox / "master" / config.MATERIAL_DB_NAME)
    got = conn.execute('SELECT "Ｗ" FROM "パレット" WHERE rowid = ?', (key,)).fetchone()
    conn.close()
    assert got[0] == "900"


# ==================================================================
# 取り込み元に表が無い / 列名が違う
# ==================================================================
def test_無い表も一覧に出て理由が付く(client, sandbox):
    """「無い表は画面にも無い」にしない(思想3)"""
    folder = sandbox / "master"
    folder.mkdir(parents=True)
    conn = sqlite3.connect(folder / config.MATERIAL_DB_NAME)
    _create(conn, "パレット", PALLET_COLS)
    conn.commit()
    conn.close()
    client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: str(folder)})

    body = browse(client, table="包装仕様")
    assert body["page"]["missing"] is True
    assert body["page"]["editable"] is False
    assert "取り込み元にありません" in body["page"]["why"]
    info = next(t for t in body["tables"] if t["table"] == "包装仕様")
    assert info["missing"] is True


def test_無い表へ足そうとすると422(client, sandbox):
    unlock(client)
    folder = sandbox / "master"
    folder.mkdir(parents=True)
    conn = sqlite3.connect(folder / config.MATERIAL_DB_NAME)
    _create(conn, "パレット", PALLET_COLS)
    conn.commit()
    conn.close()
    client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: str(folder)})

    r = client.post("/api/master/row/add",
                    json={"table": "包装仕様", "values": {"包装仕様NO": "1C0001"}})
    assert r.status_code == 422
    assert r.get_json()["error"]["code"] == master_admin.REFUSE_NOT_CREATABLE


def test_列名が違えば打ち込める欄が無いことを言葉にする(client, sandbox):
    """表はある。列名が想定と違うので1つも打ち込めない"""
    folder = sandbox / "master"
    folder.mkdir(parents=True)
    conn = sqlite3.connect(folder / config.MATERIAL_DB_NAME)
    _create(conn, "包装仕様", ["ぜんぜん違う列", "これも違う"])
    conn.execute('INSERT INTO "包装仕様" VALUES ("a","b")')
    conn.commit()
    conn.close()
    client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: str(folder)})

    body = browse(client, table="包装仕様")
    assert body["page"]["missing"] is False     # 表はある
    assert body["columns"] == []                # でも1つも打ち込めない
    assert "列名が想定と違う" in body["page"]["why"]
    # このツールが読む列名を具体的に言う
    assert "包装仕様NO" in body["page"]["why"]


def test_列名が違う表へ書こうとすると422(client, sandbox):
    unlock(client)
    folder = sandbox / "master"
    folder.mkdir(parents=True)
    conn = sqlite3.connect(folder / config.MATERIAL_DB_NAME)
    _create(conn, "包装仕様", ["ぜんぜん違う列"])
    conn.execute('INSERT INTO "包装仕様" VALUES ("a")')
    conn.commit()
    conn.close()
    client.post("/api/settings", json={config.KEY_MASTER_DB_DIR: str(folder)})

    r = client.post("/api/master/row/add",
                    json={"table": "包装仕様", "values": {"包装仕様NO": "1C0001"}})
    assert r.status_code == 422


def test_列名が一致していれば理由は出ない(looking):
    body = browse(looking, table="包装仕様")
    assert body["page"]["why"] == ""


# ==================================================================
# 画面
# ==================================================================
def test_設定の中に面がある(ready):
    """独立した画面は持たない。設定から見られるものを2か所に出さない"""
    r = ready.get("/settings?t=test-token")
    assert r.status_code == 200
    assert "マスタ管理".encode() in r.data
    # 表の一覧を入れる器がある
    assert b'id="mTables"' in r.data


def test_認証は面の外にある(ready):
    """守っている先が2つある(置き場所とマスタ)ので、どちらかの面の中に
    入れると、もう片方から使うときに「面を移って、打って、戻る」になる。
    """
    page = ready.get("/settings?t=test-token").data.decode()
    assert 'class="authbar authbar--top"' in page
    # タブより前に出ている
    assert page.index('id="authPass"') < page.index('class="tabs"')


def test_独立した画面は無い(ready):
    assert ready.get("/master?t=test-token").status_code == 404


def test_最初の描画では共有フォルダに触らない(client, sandbox):
    """画面を開くだけで共有を数えに行かない"""
    from modules.packing_material_calculation.coil_tool.presenters import master as presenter
    with db.connect() as conn:
        view = presenter.frame(conn)
    assert view.loaded is False
    assert view.tables == []
    assert view.source_dir          # どこへ書くかは共有に触らず分かる


def test_行を出す数には上限がある(client, sandbox):
    """全部は出さない。出さなかった分は必ず数で言う"""
    make_source(sandbox, spec_rows=master_admin.ROW_LIMIT + 5)
    client.post("/api/settings",
                json={config.KEY_MASTER_DB_DIR: str(sandbox / "master")})
    body = browse(client, table="包装仕様")
    assert body["page"]["shown"] == master_admin.ROW_LIMIT
    assert body["page"]["total"] == master_admin.ROW_LIMIT + 5
    assert "絞り込む" in body["page"]["note"]


def test_トークンが要る(sandbox):
    from modules.packing_material_calculation.app import create_app
    make_source(sandbox)
    with db.connect() as conn:
        db.apply_schema(conn)
    app = create_app(token="test-token")
    app.config["READY"] = True
    with app.test_client() as c:
        assert c.get("/api/master/browse").status_code == 403


# ==================================================================
# 列名の寄せ ── 取り込み元と手元で名前が違う列
# ==================================================================
# 取り込み元の包装仕様には**半角カナの列**がある(`ｺｲﾙ間は間紙入`)。
# 手元では全角へ寄せてある(Python の識別子が NFKC 正規化されるため。
# `docs/VBA解析.md` と `tests/test_schema_contract.py` を参照)。
#
# 直すのは**取り込み元のファイル**なので、画面も保存も取り込み元の
# 名前で動かないと、読むときは空に見え、書くときは「そんな列は無い」
# と断られる。
HALF_WIDTH = "ｺｲﾙ間は間紙入"
FULL_WIDTH = "コイル間は間紙入"


def test_列は取り込み元の名前で出る(looking):
    body = browse(looking, table="包装仕様")
    names = [c["name"] for c in body["columns"]]
    assert HALF_WIDTH in names          # 取り込み元の名前
    assert FULL_WIDTH not in names      # 手元の名前では出さない


def test_寄せた列は手元での名前も添える(looking):
    """どちらの名前なのか分からないと、マスタと見比べられない"""
    body = browse(looking, table="包装仕様")
    column = next(c for c in body["columns"] if c["name"] == HALF_WIDTH)
    assert column["local"] == FULL_WIDTH
    assert FULL_WIDTH in column["note"]


def test_寄せた列に書ける(ready, sandbox):
    """取り込み元の名前で書く。手元の名前で書くと落ちる"""
    key = first_key(ready)
    r = ready.post("/api/master/row/save",
                   json={"table": "包装仕様", "key": key,
                         "values": {HALF_WIDTH: "〇"}})
    assert r.status_code == 200, r.get_json().get("error")

    conn = sqlite3.connect(sandbox / "master" / config.MATERIAL_DB_NAME)
    got = conn.execute(
        f'SELECT "{HALF_WIDTH}" FROM "包装仕様" WHERE rowid = ?', (key,)).fetchone()
    conn.close()
    assert got[0] == "〇"


def test_寄せた列は手元へ取り込まれるときに全角へ戻る(ready):
    """書いたあとの取り込み直しで、手元は手元の名前のまま追いつく"""
    key = first_key(ready)
    ready.post("/api/master/row/save",
               json={"table": "包装仕様", "key": key,
                     "values": {HALF_WIDTH: "〇"}})
    with db.connect() as conn:
        row = conn.execute(
            f"SELECT {FULL_WIDTH} FROM 包装仕様 WHERE 包装仕様NO = '1C0000'"
        ).fetchone()
    assert row[FULL_WIDTH] == "〇"


def test_寄せた列を足すときも取り込み元の名前(ready):
    r = ready.post("/api/master/row/add",
                   json={"table": "包装仕様",
                         "values": {"包装仕様NO": "1C8888", HALF_WIDTH: "〇"}})
    assert r.status_code == 200, r.get_json().get("error")
    body = browse(ready, table="包装仕様", q="1C8888")
    assert body["page"]["rows"][0][HALF_WIDTH] == "〇"


# ==================================================================
# マスタ編集の認証
# ==================================================================
# **これは誰かを見分けるものではありません。** 共有フォルダのマスタを
# 誤って書き換えないための関門です。見るのはいつでも通し、直すときだけ
# 通します。
def test_認証しないと直せない(looking):
    key = first_key(looking)
    r = looking.post("/api/master/row/save",
                     json={"table": "包装仕様", "key": key,
                           "values": {"パレット": "全面"}})
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == master_admin.REFUSE_NOT_ALLOWED
    assert "パスワード" in r.get_json()["error"]["message"]


def test_認証しなくても見られる(looking):
    """中身を確かめられることと、書き換えられることは別の話"""
    body = browse(looking)
    assert body["page"]["total"] == 2
    assert body["can_edit"] is False
    assert "パスワード" in body["edit_why"]


def test_正しいパスワードで通る(looking):
    r = unlock(looking)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["auth"]["open"] is True
    assert body["auth"]["remains"] > 0


def test_違うパスワードは403(looking):
    r = unlock(looking, "ちがう")
    assert r.status_code == 403
    assert r.get_json()["reason"] == "wrong_password"
    assert r.get_json()["auth"]["open"] is False
    # **何が違うのかは言わない**(総当たりの手がかりを与えない)
    assert "違います" in r.get_json()["message"]


def test_空のパスワードは400(looking):
    r = looking.post("/api/master/auth", json={"password": ""})
    assert r.status_code == 400
    assert r.get_json()["reason"] == "no_password"


def test_認証すると直せる(looking):
    unlock(looking)
    key = first_key(looking)
    r = looking.post("/api/master/row/save",
                     json={"table": "包装仕様", "key": key,
                           "values": {"パレット": "全面"}})
    assert r.status_code == 200
    assert r.get_json()["can_edit"] is True


def test_閉じるとまた直せなくなる(ready):
    ready.post("/api/master/auth", json={"close": True})
    key = first_key(ready)
    r = ready.post("/api/master/row/save",
                   json={"table": "包装仕様", "key": key,
                         "values": {"パレット": "全面"}})
    assert r.status_code == 403


def test_放っておくと閉じる(looking, monkeypatch):
    """席を離れたあいだに共有のマスタを書き換えられないようにする"""
    from modules.packing_material_calculation.coil_tool import admin_session, config as cfg
    unlock(looking)
    assert admin_session.peek() is True
    # 時間切れの幅を 0 にして、次に確かめたときに閉じることを見る
    monkeypatch.setattr(cfg, "ADMIN_SESSION_IDLE_SEC", 0)
    assert admin_session.peek() is False

    key = first_key(looking)
    r = looking.post("/api/master/row/save",
                     json={"table": "包装仕様", "key": key,
                           "values": {"パレット": "全面"}})
    assert r.status_code == 403


def test_見ているだけでは時間切れが延びない(looking, monkeypatch):
    """画面を開きっぱなしにするだけで永久に開いたまま、を作らない"""
    from modules.packing_material_calculation.coil_tool import admin_session
    unlock(looking)
    browse(looking)          # 見るだけ
    browse(looking)
    # 見た回数では延びない。`peek` は数えるだけで触らない
    assert admin_session.peek() is True


def test_認証の状態は設定にも出る(ready):
    r = ready.get("/settings?t=test-token")
    assert r.status_code == 200
    # 値そのものは画面に出さない
    assert config.ADMIN_PASSWORD.encode() not in r.data


# ==================================================================
# パスワードを変える
# ==================================================================
def test_パスワードを変えられる(ready):
    r = ready.post("/api/master/password",
                   json={"current": config.ADMIN_PASSWORD,
                         "new": "atarashii", "confirm": "atarashii"})
    assert r.status_code == 200
    # 変えたら閉める。**新しいほうで入り直してもらう**
    assert r.get_json()["auth"]["open"] is False
    assert unlock(ready, config.ADMIN_PASSWORD).status_code == 403
    assert unlock(ready, "atarashii").status_code == 200


def test_いまのパスワードが違えば変えられない(ready):
    r = ready.post("/api/master/password",
                   json={"current": "ちがう", "new": "atarashii",
                         "confirm": "atarashii"})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "wrong_password"


def test_確認用と一致しなければ変えられない(ready):
    r = ready.post("/api/master/password",
                   json={"current": config.ADMIN_PASSWORD,
                         "new": "atarashii", "confirm": "chigau"})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "mismatch"


def test_短すぎるパスワードは断る(ready):
    r = ready.post("/api/master/password",
                   json={"current": config.ADMIN_PASSWORD,
                         "new": "ab", "confirm": "ab"})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "too_short"


def test_パスワードは平文で保存しない(ready):
    """配布はフォルダごとコピー。設定ファイルはそのまま持ち出せる"""
    import json
    ready.post("/api/master/password",
               json={"current": config.ADMIN_PASSWORD,
                     "new": "himitsu99", "confirm": "himitsu99"})
    raw = config.USER_CONFIG_PATH.read_text(encoding="utf-8")
    assert "himitsu99" not in raw
    stored = json.loads(raw)[config.KEY_ADMIN_PASSWORD]
    assert stored.startswith("pbkdf2$")


def test_既定のパスワードで通る(looking):
    """一度も変えていない端末は、これまでどおり既定で通る"""
    assert config.ADMIN_PASSWORD == "nisk"
    assert unlock(looking, "nisk").status_code == 200
