"""「古い」は、取り込んだ日と今日が違えば出る

以前は「取り込みから24時間経ったか」で見ていた。仕掛台帳は日々の
台帳なので、日付が変われば上流の中身も変わる。ところが経過時間で
見ていると:

    23:50 に取り込む → 翌 00:10 に使う → 経過20分 → 「古い」が出ない

本人は「さっき取り込んだ」と思っているので、**前日の写しで計算して
いることに気づけない**。

日付で見ると取りこぼしが無い。同じ日の中は最長でも24時間未満なので、
**経過時間で見るより甘くなることはない**。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from modules.packing_material_calculation.coil_tool import data_sync, db, import_specs
from modules.packing_material_calculation.tests._web import sandbox   # noqa: F401

ALL_TABLES = (set(import_specs.MASTER_IMPORT_SPECS)
              | set(import_specs.LOT_IMPORT_SPECS))


@pytest.fixture
def conn(sandbox):
    with db.connect() as c:
        db.apply_schema(c)
        yield c


@pytest.fixture
def 時計(monkeypatch):
    """「いま」を固定する。

    実時刻のままだと、試験を走らせた時刻で結果が変わる ── 23:55 に
    走らせると「昨夜 23:50」が24時間より前になってしまい、
    *24時間未満で日をまたぐ* という肝心の場面を作れない。
    """
    def 止める(when: datetime):
        class 固定(datetime):
            @classmethod
            def now(cls, tz=None):
                return when
        monkeypatch.setattr(data_sync, "datetime", 固定)
        return when
    return 止める


def imported(conn, when: datetime, *, tables=None) -> None:
    """全テーブルを、その時刻に取り込んだことにする。"""
    conn.execute("DELETE FROM 取り込み履歴")
    for table in (tables if tables is not None else ALL_TABLES):
        conn.execute(
            "INSERT INTO 取り込み履歴 (テーブル名, 取り込み日時, 元ファイル, 件数)"
            " VALUES (?, ?, 'x', 0)",
            (table, when.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


# ==================================================================
# 日付で見る
# ==================================================================
def test_同じ日なら23時間経っていても古くない(conn, 時計):
    """日付で見るので、同じ日の中はいくら経っても新しい扱い。"""
    時計(datetime(2026, 9, 22, 23, 30))
    imported(conn, datetime(2026, 9, 22, 0, 10))
    assert data_sync.is_stale(conn) is False


def test_20分前でも日付が変わっていれば古い(conn, 時計):
    """**これが今回の変更点。** 経過時間では古くならなかった場面。

        昨夜 23:50 に取り込む → いま 00:10 → 経過 20分
    """
    昨夜 = datetime(2026, 9, 21, 23, 50)
    いま = datetime(2026, 9, 22, 0, 10)
    時計(いま)
    imported(conn, 昨夜)

    assert (いま - 昨夜).total_seconds() / 3600 < 24      # 24時間は経っていない
    assert data_sync.is_stale(conn) is True               # それでも古い
    assert data_sync.stale_reason(conn) == data_sync.STALE_DATE_CHANGED


def test_何日も前なら当然古い(conn):
    imported(conn, datetime.now() - timedelta(days=3))
    assert data_sync.is_stale(conn) is True


def test_未取り込みは古い(conn):
    assert data_sync.is_stale(conn) is True
    assert data_sync.stale_reason(conn) == data_sync.STALE_NOT_IMPORTED


def test_1つでも未取り込みなら古い(conn):
    """1表だけ取り込めていない端末を「新しい」と言わない。"""
    残り = sorted(ALL_TABLES)[:-1]
    imported(conn, datetime.now(), tables=残り)
    assert data_sync.is_stale(conn) is True


def test_いちばん古い取り込みで判断する(conn):
    """「マスタは今朝、仕掛は昨日」なら古い。新しいほうを見ない。"""
    now = datetime.now()
    conn.execute("DELETE FROM 取り込み履歴")
    for i, table in enumerate(sorted(ALL_TABLES)):
        # 1つだけ昨日にする
        when = now - timedelta(days=1) if i == 0 else now
        conn.execute(
            "INSERT INTO 取り込み履歴 (テーブル名, 取り込み日時, 元ファイル, 件数)"
            " VALUES (?, ?, 'x', 0)", (table, when.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    assert data_sync.is_stale(conn) is True


def test_読めない日時は古い扱い(conn):
    """黙って新しい扱いにしない。"""
    conn.execute("DELETE FROM 取り込み履歴")
    for table in ALL_TABLES:
        conn.execute(
            "INSERT INTO 取り込み履歴 (テーブル名, 取り込み日時, 元ファイル, 件数)"
            " VALUES (?, 'こわれた', 'x', 0)", (table,))
    conn.commit()
    assert data_sync.is_stale(conn) is True


def test_経過時間で見るより甘くならない(conn, 時計):
    """同じ日の中は最長でも24時間未満なので、**日付で見て新しいのに
    24時間を超えている**ということは起こらない。

    その日のいちばん端(00:00:00 と 23:59:59)で確かめる。
    """
    いま = datetime(2026, 9, 22, 23, 59, 59)
    時計(いま)
    取り込み = datetime(2026, 9, 22, 0, 0, 0)
    imported(conn, 取り込み)
    assert data_sync.is_stale(conn) is False
    assert (いま - 取り込み).total_seconds() < 24 * 3600


# ==================================================================
# なぜ古いのかを出す
# ==================================================================
def test_古い理由を人の言葉で出す(conn, 時計):
    """20分前に取り込んだ人が日付をまたいだだけで「古い」と言われると、
    壊れたように見える。"""
    時計(datetime(2026, 9, 22, 0, 10))
    imported(conn, datetime(2026, 9, 21, 23, 50))
    assert "日付" in data_sync.stale_message(conn)

    imported(conn, datetime(2026, 9, 22, 0, 5))
    assert data_sync.stale_message(conn) == ""


def test_帯に理由が出る(sandbox):
    """画面は文言ではなく `stale_reason` を見るが、人には言葉で出す。"""
    from modules.packing_material_calculation.app import create_app, session, screen
    session.reset(); screen.reset()
    with db.connect() as c:
        db.apply_schema(c)
        imported(c, (datetime.now() - timedelta(days=1)).replace(hour=23, minute=50))

    app = create_app(token="test-token")
    app.config["READY"] = True
    with app.test_client() as client:
        client.environ_base["HTTP_X_APP_TOKEN"] = "test-token"
        page = client.get("/calc?t=test-token").data.decode()
    assert "古い" in page
    assert "日付が変わりました" in page
