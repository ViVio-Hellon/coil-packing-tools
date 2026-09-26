"""作業用DBと利用者設定は、利用者ごとのローカル領域に置く

【なぜ要るのか】
README は「本体を共有フォルダに置いて複数の端末から使っても混ざり
ません」と言っている。**ログだけがローカル領域に移してあり、
作業用DBと利用者設定は移し忘れていた**(アプリ本体の隣のままだった)。

そのままだと、本体を共有に置いた運用で:

- 担当者・ライン・取り込み元の置き場所が**全端末で1つ**になり、
  あとから保存した端末の値で黙って上書きされる
- チェックリストも全端末で1つになり、同じ行番号を2台が使うと
  先に書いたほうが消える
- 共有フォルダ上の SQLite に**書く**ことになる(読むだけなら妥当だが、
  書き先としては SMB のロックが当てにならず推奨されない)

端末ごとにフォルダを写して配る運用なら起きないが、**起きない前提を
コードが持っていない**のが問題だった。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from modules.packing_material_calculation.coil_tool import app_config, config


def test_作業用DBと設定はローカル領域にある():
    """アプリ本体の隣ではない。ここが**共有されない**ことが前提。

    統合版では、作業用DBと設定はこの機能の領域(`CoilMaterialTool`)のまま、
    ログだけ統合アプリの領域(`CoilPackingTools`)にまとめる。
    """
    from common import app_config as integrated
    local = app_config.local_root().resolve()
    for path in (config.DB_PATH, config.USER_CONFIG_PATH):
        assert local in path.resolve().parents, f"{path} がローカル領域の外です"
    shared_logs = integrated.local_root().resolve()
    assert (local in config.LOG_DIR.resolve().parents
            or shared_logs in config.LOG_DIR.resolve().parents), \
        f"{config.LOG_DIR} がローカル領域の外です"


def test_アプリ本体の下に作業用のものを置かない():
    """本体は共有フォルダに置かれうる。**書くものを本体の隣に置かない。**"""
    base = config.BASE_DIR.resolve()
    for path in (config.DB_PATH, config.USER_CONFIG_PATH, config.LOG_DIR):
        assert base not in path.resolve().parents, f"{path} が本体の下にあります"


# ==================================================================
# 旧い置き場所からの引っ越し
# ==================================================================
@pytest.fixture
def places(monkeypatch, tmp_path):
    """旧・新の置き場所をテスト用に作る。"""
    old, new = tmp_path / "app" / "data", tmp_path / "local" / "data"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    monkeypatch.setattr(config, "LEGACY_DB_PATH", old / "coil_tool.db")
    monkeypatch.setattr(config, "LEGACY_USER_CONFIG_PATH", old / "user_config.json")
    monkeypatch.setattr(config, "DB_PATH", new / "coil_tool.db")
    monkeypatch.setattr(config, "USER_CONFIG_PATH", new / "user_config.json")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "local" / "logs")
    return old, new


def _make_legacy_db(path: Path, lot: str = "OLD") -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode = WAL")
    schema = (Path(__file__).resolve().parent.parent
              / "coil_tool" / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.execute(
        "INSERT INTO 発注履歴 (処理日,担当者,品名,サイズ,長さ,LotNo,数量,単位)"
        " VALUES ('d','たろう','p','s','l',?,'9','本')", (lot,))
    conn.commit()          # **WAL にだけ在る状態のまま**置く
    conn.close()


def test_旧い置き場所から写す(places):
    old, _ = places
    _make_legacy_db(config.LEGACY_DB_PATH)
    config.LEGACY_USER_CONFIG_PATH.write_text(
        json.dumps({"worker": "たろう"}, ensure_ascii=False), encoding="utf-8")

    moved = config.migrate_local()
    assert len(moved) == 2

    conn = sqlite3.connect(str(config.DB_PATH))
    lots = [r[0] for r in conn.execute("SELECT LotNo FROM 発注履歴")]
    conn.close()
    assert lots == ["OLD"]
    assert json.loads(config.USER_CONFIG_PATH.read_text(encoding="utf-8")) \
        == {"worker": "たろう"}


def test_WALにしかない分も落とさない(places):
    """ファイルをコピーすると、本体へまだ落ちていない分を取りこぼす。

    `backup()` は WAL の中身も含めて写す。

    閉じると WAL は本体へ落ちてしまうので、**開いたまま**にして
    「まだ落ちていない」状態を作って確かめる。
    """
    _make_legacy_db(config.LEGACY_DB_PATH, lot="OLD")
    held = sqlite3.connect(str(config.LEGACY_DB_PATH))
    try:
        held.execute("PRAGMA journal_mode = WAL")
        held.execute(
            "INSERT INTO 発注履歴 (処理日,担当者,品名,サイズ,長さ,LotNo,数量,単位)"
            " VALUES ('d','x','p','s','l','WAL-ONLY','1','本')")
        held.commit()
        # 本体にはまだ落ちていない(-wal に在る)
        assert config.LEGACY_DB_PATH.with_suffix(".db-wal").exists()

        config.migrate_local()
    finally:
        held.close()

    conn = sqlite3.connect(str(config.DB_PATH))
    lots = sorted(r[0] for r in conn.execute("SELECT LotNo FROM 発注履歴"))
    conn.close()
    assert lots == ["OLD", "WAL-ONLY"]


def test_旧ファイルは消さない(places):
    """写し損ねていたときに戻せなくなる。共有に置いてあった場合、
    他の端末がまだ旧い側を見ていることもある。"""
    _make_legacy_db(config.LEGACY_DB_PATH)
    config.migrate_local()
    assert config.LEGACY_DB_PATH.exists()


def test_新しい側があれば何もしない(places):
    """**こちらで使い始めた内容を、古いほうで上書きしない。**"""
    _make_legacy_db(config.LEGACY_DB_PATH, lot="OLD")
    config.migrate_local()

    conn = sqlite3.connect(str(config.DB_PATH))
    conn.execute(
        "INSERT INTO 発注履歴 (処理日,担当者,品名,サイズ,長さ,LotNo,数量,単位)"
        " VALUES ('d','x','p','s','l','NEW','1','本')")
    conn.commit()
    conn.close()

    assert config.migrate_local() == []        # 2度目は何もしない

    conn = sqlite3.connect(str(config.DB_PATH))
    lots = sorted(r[0] for r in conn.execute("SELECT LotNo FROM 発注履歴"))
    conn.close()
    assert lots == ["NEW", "OLD"]              # 消えていない


def test_旧い置き場所が無ければ何もしない(places):
    assert config.migrate_local() == []
    assert not config.DB_PATH.exists()


def test_写せなくても起動を止めない(places, monkeypatch):
    """壊れたファイルが残っていても、アプリは立ち上がるべき。"""
    config.LEGACY_DB_PATH.write_text("これは sqlite ではありません", encoding="utf-8")
    moved = config.migrate_local()             # 例外を投げない
    assert moved == []
    # 中途半端な写しを残さない(次の起動でやり直せる)
    assert not config.DB_PATH.exists()
