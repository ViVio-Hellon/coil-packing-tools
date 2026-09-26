"""Web テストの下ごしらえ ── 手元DBと設定をテスト用に閉じ込める"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from modules.packing_material_calculation.coil_tool import config


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """DB・設定・ログをテスト専用の場所へ向ける。

    **本物の共有フォルダを見に行かせない。** 既定のパスは社内の UNC で、
    テスト環境からは届かない(届いても触ってはいけない)。
    """
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "coil_tool.db")
    monkeypatch.setattr(config, "USER_CONFIG_PATH", tmp_path / "user_config.json")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "DEFAULT_MASTER_DB_DIR", tmp_path / "master")
    monkeypatch.setattr(config, "DEFAULT_LOT_DB_DIR", tmp_path / "lot")
    monkeypatch.setattr(config, "DEFAULT_EXPORT_DIR", tmp_path / "export")
    # 配布設定: ツールのフォルダの直下に書くので、試験では逃がす
    from modules.packing_material_calculation.coil_tool import distribution
    monkeypatch.setattr(distribution, "DIR", tmp_path / "tool" / "配布設定")
    return tmp_path


@pytest.fixture
def client(sandbox):
    from modules.packing_material_calculation.app import create_app, screen, session
    from modules.packing_material_calculation.coil_tool import admin_session, db

    # **作業状態はプロセスに1つ。** 本番はそれでよい(1台を1人が使う)が、
    # テストは前の試験の続きから始まってしまうので、毎回まっさらにする。
    # マスタ編集の認証も同じ(通したままで次の試験に入らない)
    session.reset()
    admin_session.reset()
    # 使ってよい画面も戻す。前の試験が符牒を持ったままだと、
    # 名乗らない試験の書き込みが 409 で断られる
    screen.reset()

    with db.connect() as conn:
        db.apply_schema(conn)

    app = create_app(token="test-token")
    app.config["READY"] = True
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.environ_base["HTTP_X_APP_TOKEN"] = "test-token"
        yield c


def seed(sandbox):
    """テスト用のマスタを手元DBへ直に入れる。"""
    from modules.packing_material_calculation.coil_tool import db
    from modules.packing_material_calculation.tests import _fixture

    with db.connect() as conn:
        db.apply_schema(conn)
        _fixture.standard_master(conn)
        _fixture.add_spec(conn, "1C0001", 枚数範囲="以下")
        conn.execute(
            "INSERT OR REPLACE INTO 仕掛受注"
            " (受注番号, 受注板厚, 受注板幅, 用途名, 包装仕様NO, 製品単重,"
            "  梱包単位_枚数, コイル外径_MAX, 営業納期, 材質_比重, コイル内径_目標)"
            " VALUES ('12345678', 1.0, 100.0, 'ﾃｽﾄ用途', '1C0001', 100.0,"
            "         5, 900, '04/01', 2.71, 508)")
        conn.execute(
            "INSERT INTO 仕掛引当 (ロット番号, 受注番号, 出荷日)"
            " VALUES ('A123456', '12345678', '03/28')")
        conn.commit()
