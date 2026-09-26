"""担当者は「班員名簿」から、担当ラインが機側の人だけ

画面に7名を直書きしない。

VBA `UF_Material` は担当者を W1〜W7 の7個のボタンとして画面に直書き
していた。異動のたびにコードを直す必要があり、端末ごとに写しがずれる。

名簿は**すでにある** ── 姉妹ツール(日報)の人員フォームが、同じ
梱包資材マスタの中の「班員名簿」を読んでいる。こちらも同じ表を読む。
名簿を二重に持つと、異動で片方だけ古くなる。
"""
from __future__ import annotations

import pytest

from modules.packing_material_calculation.coil_tool import config, db, staff, user_settings
from modules.packing_material_calculation.tests._web import client, sandbox, seed   # noqa: F401


def put(conn, 名前, 班="", 読み="", 担当ライン="機側"):
    conn.execute(
        "INSERT INTO 班員名簿 (管理番号, 苗字, 班, 名前, 読み, 担当ライン)"
        " VALUES ('', '', ?, ?, ?, ?)", (班, 名前, 読み, 担当ライン))


@pytest.fixture
def conn(sandbox):
    with db.connect() as c:
        db.apply_schema(c)
        yield c


# ==================================================================
# 並び ── 姉妹ツールの group_by_team と同じ
# ==================================================================
def test_読み順に並べてから班でまとめる(conn):
    """班の中では読み順が残る(安定ソート)。"""
    put(conn, "山田", "B", "やまだ")
    put(conn, "青木", "B", "あおき")
    put(conn, "石井", "A", "いしい")
    conn.commit()
    assert staff.names(conn) == ["石井", "青木", "山田"]


def test_班の並びは画面の左から右(conn):
    """A / B / C / D / 昼 / 丸。姉妹ツールの `TEAM_ORDER` と同じ。"""
    for team in reversed(staff.TEAM_ORDER):
        put(conn, f"{team}さん", team, "あ")
    conn.commit()
    assert staff.names(conn) == [f"{t}さん" for t in staff.TEAM_ORDER]


def test_名前が空の行は出さない(conn):
    """依頼者欄に出せない。日報ツールも同じ扱い。"""
    put(conn, "", "A", "あ")
    put(conn, "  ", "A", "い")
    put(conn, "田中", "A", "う")
    conn.commit()
    assert staff.names(conn) == ["田中"]


def test_知らない班の人も落とさない(conn):
    """**姉妹ツールと変えたところ。**

    向こうは知らない班を落とす。これは班ごとに画面の列(left座標)を
    割り当てていた**画面の作りの都合**で、業務の決まりではない。
    こちらは一覧から選ぶだけなので場所の制約が無く、落とすと
    **その人は依頼者になれず、チェックリストへ積めない**。
    """
    put(conn, "佐藤", "A", "さとう")
    put(conn, "鈴木", "E", "すずき")      # 知らない班
    put(conn, "高橋", "", "たかはし")     # 班が空
    conn.commit()
    names = staff.names(conn)
    assert "鈴木" in names and "高橋" in names
    # 末尾に寄せる。**落とさないが、既知の班より後ろ**
    assert names.index("佐藤") < names.index("鈴木")

    groups = {g["team"]: g["names"] for g in staff.choices(conn)["groups"]}
    assert set(groups[staff.OTHER_TEAM]) == {"鈴木", "高橋"}


def test_同じ名前は1つにまとめる(conn):
    put(conn, "田中", "A", "たなか")
    put(conn, "田中", "B", "たなか")
    conn.commit()
    assert staff.names(conn) == ["田中"]


# ==================================================================
# 名簿が無いとき ── 仕事を止めない
# ==================================================================
def test_名簿が空なら予備の一覧へ落ちる(conn):
    """担当者が無いとチェックリストへ積めない。

    **名簿が読めないだけで仕事が止まらないようにする。**
    """
    c = staff.choices(conn)
    assert c["from_master"] is False
    assert c["names"] == list(config.WORKERS)


def test_名簿があれば予備は使わない(conn):
    put(conn, "新人", "A", "しんじん")
    conn.commit()
    c = staff.choices(conn)
    assert c["from_master"] is True
    assert c["names"] == ["新人"]
    assert "高村" not in c["names"]


def test_表が無くても落ちない(sandbox):
    """取り込み前・スキーマが古い端末。例外を投げない。"""
    with db.connect() as c:
        assert staff.names(c) == []
        assert staff.choices(c)["from_master"] is False


# ==================================================================
# 異動のあと ── 黙って別人に変えない
# ==================================================================
def test_名簿から消えた人も選ばれたままなら残す(conn):
    put(conn, "田中", "A", "たなか")
    conn.commit()
    c = staff.choices(conn, current="退職者")
    assert "退職者" in c["names"]
    assert c["stale"] is True
    # 見出しで理由が分かる
    assert c["groups"][-1]["team"] == "名簿にありません"


def test_名簿に居る人はそのまま(conn):
    put(conn, "田中", "A", "たなか")
    conn.commit()
    assert staff.choices(conn, current="田中")["stale"] is False


def test_選ばれている名前は保存できる(conn):
    """通さないと、異動のあった端末で**設定を開いただけで弾かれる**。"""
    put(conn, "田中", "A", "たなか")
    conn.commit()
    assert staff.is_known(conn, "退職者", current="退職者") is True
    assert staff.is_known(conn, "知らない人", current="田中") is False
    assert staff.is_known(conn, "", current="田中") is True     # 未選択


# ==================================================================
# 画面まで通す
# ==================================================================
def test_帯の担当者は名簿から出る(client, sandbox):
    with db.connect() as c:
        db.apply_schema(c)
        put(c, "名簿太郎", "A", "めいぼ")
        c.commit()
    page = client.get("/calc?t=test-token").data.decode()
    assert "名簿太郎" in page
    assert "高村" not in page          # 直書きの予備は出ない


def test_名簿の人を担当者にできる(client, sandbox):
    with db.connect() as c:
        db.apply_schema(c)
        put(c, "名簿太郎", "A", "めいぼ")
        c.commit()
    r = client.post("/api/settings", json={"worker": "名簿太郎"})
    assert r.status_code == 200
    assert user_settings.get_worker() == "名簿太郎"


def test_名簿にない人は断る(client, sandbox):
    with db.connect() as c:
        db.apply_schema(c)
        put(c, "名簿太郎", "A", "めいぼ")
        c.commit()
    r = client.post("/api/settings", json={"worker": "知らない人"})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "bad_worker"


def test_名簿が無いときは予備の名前で通る(client, sandbox):
    """取り込み前の端末でも担当者を選べる(選べないと積めない)。"""
    r = client.post("/api/settings", json={"worker": config.WORKERS[0]})
    assert r.status_code == 200


def test_名簿は直せない表として出る(client, sandbox):
    """人の出入りは日報ツールで直す。ここで直すと食い違う。"""
    from modules.packing_material_calculation.coil_tool import master_admin
    why = master_admin.view_only_why(config.TBL_STAFF)
    assert "日報" in why


# ==================================================================
# 取り込み
# ==================================================================
def test_名簿は梱包資材マスタから取り込む(conn):
    """置き場所は包装仕様・パレットと同じファイル。"""
    from modules.packing_material_calculation.coil_tool import import_specs
    assert config.TBL_STAFF in import_specs.MASTER_IMPORT_SPECS
    cols = [local for local, _, _ in
            import_specs.MASTER_IMPORT_SPECS[config.TBL_STAFF]]
    assert cols == ["管理番号", "苗字", "班", "名前", "読み", "担当ライン"]


def test_取り込み元に無い列があっても取り込める(conn):
    """並べ替えと区切りに使うだけの列。無くても名前は出せる。"""
    from modules.packing_material_calculation.coil_tool import import_specs
    missing = import_specs.missing_columns(config.TBL_STAFF, {"名前", "班"})
    assert missing == []


# ==================================================================
# 担当ラインで絞る ── 機側の人だけ (Q18 / Q19 の回答)
# ==================================================================
def test_機側の人だけ出す(conn):
    """資材を発注するのは機側。名簿には機側以外の人も載っている。"""
    put(conn, "機側さん", "A", "あ", "機側")
    put(conn, "検査さん", "A", "い", "検査")
    put(conn, "空欄さん", "A", "う", "")
    conn.commit()
    assert staff.names(conn) == ["機側さん"]


def test_絞ったことと隠した人数を返す(conn):
    """画面が「機側の何名」と言えるように。"""
    put(conn, "機側さん", "A", "あ", "機側")
    put(conn, "検査さん", "A", "い", "検査")
    conn.commit()
    c = staff.choices(conn)
    assert c["narrowed"] is True
    assert c["line"] == "機側"
    assert c["hidden"] == 1
    assert c["count"] == 1


def test_書き方が揺れていても機側として拾う(conn):
    """「機側A」「機 側」で人を落とすと、**その人は依頼者になれない**。

    落とし過ぎるほうが害が大きいので広めに拾う。
    """
    put(conn, "号機さん", "A", "あ", "1号機側")
    put(conn, "空白さん", "A", "い", "機 側")
    put(conn, "全角さん", "A", "う", "機　側")
    put(conn, "検査さん", "A", "え", "検査")
    conn.commit()
    assert staff.names(conn) == ["号機さん", "空白さん", "全角さん"]


def test_機側が1人も居なければ絞らない(conn):
    """担当ラインがまだ埋まっていない端末。

    絞ると**一覧が空になって誰も選べず、チェックリストへ積めない**。
    絞れなかったことは画面が言う。
    """
    put(conn, "田中", "A", "たなか", "")
    put(conn, "鈴木", "B", "すずき", "検査")
    conn.commit()
    c = staff.choices(conn)
    assert c["narrowed"] is False
    assert c["names"] == ["田中", "鈴木"]
    assert c["hidden"] == 0


def test_機側の中で班の並びは残る(conn):
    put(conn, "B青木", "B", "あおき", "機側")
    put(conn, "A石井", "A", "いしい", "機側")
    put(conn, "A検査", "A", "あ", "検査")
    conn.commit()
    assert staff.names(conn) == ["A石井", "B青木"]


def test_機側以外は新しく選べない(conn):
    put(conn, "機側さん", "A", "あ", "機側")
    put(conn, "検査さん", "A", "い", "検査")
    conn.commit()
    assert staff.is_known(conn, "機側さん") is True
    assert staff.is_known(conn, "検査さん") is False


def test_選ばれたまま機側から外れた人は残す(conn):
    """黙って別人に変えない。**理由は「名簿にない」と分けて言う** ──
    名簿から消えたのか、担当ラインが違うのかで、次の動きが違う。"""
    put(conn, "機側さん", "A", "あ", "機側")
    put(conn, "異動さん", "A", "い", "検査")
    conn.commit()
    c = staff.choices(conn, current="異動さん")
    assert "異動さん" in c["names"]
    assert c["stale"] is True
    assert c["stale_why"] == "not_machine_side"
    assert "機側" in c["groups"][-1]["team"]
    # 設定を開いただけで弾かれない
    assert staff.is_known(conn, "異動さん", current="異動さん") is True


def test_名簿から消えた人とは理由を分ける(conn):
    put(conn, "機側さん", "A", "あ", "機側")
    conn.commit()
    c = staff.choices(conn, current="退職者")
    assert c["stale_why"] == "not_in_roster"
    assert c["groups"][-1]["team"] == "名簿にありません"


def test_機側以外は帯に出ない(client, sandbox):
    with db.connect() as c:
        db.apply_schema(c)
        put(c, "機側太郎", "A", "き", "機側")
        put(c, "検査花子", "A", "け", "検査")
        c.commit()
    page = client.get("/calc?t=test-token").data.decode()
    assert "機側太郎" in page
    assert "検査花子" not in page


def test_機側以外は保存できない(client, sandbox):
    with db.connect() as c:
        db.apply_schema(c)
        put(c, "機側太郎", "A", "き", "機側")
        put(c, "検査花子", "A", "け", "検査")
        c.commit()
    r = client.post("/api/settings", json={"worker": "検査花子"})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "bad_worker"
