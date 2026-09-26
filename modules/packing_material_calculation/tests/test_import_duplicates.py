"""同じ番号の行が2つあったら、**先に出てきた行**を使う

【なぜ要るのか】
提出された実物の梱包資材マスタ(56名の班員名簿が入っているもの)を
取り込んでみたところ、**包装仕様 `1C0118` が2行**ありました。
中身は同じではありません:

    管理番号6 … 梱包単位_重量 1000 / 重量範囲「以下」
    管理番号7 … 梱包単位_枚数「オーダー指定」(重量の指定なし)

台数は「重量で決める」が「枚数で決める」より先に来る規則なので、
**どちらの行を使うかで台数が変わります**。

VBA は鍵で引いて `TempHiki(1, ...)` ── **最初の1件しか見ません**
(`PalletType` / `台数計算` / `リプラ計算` すべて同じ形)。
ところがこちらは `包装仕様NO` を主キーにして `INSERT OR REPLACE` で
写していたので、**あとの行で上書き**していました。つまり VBA と
違う行で計算していたことになります。

取り込みで先の行を残し、飛ばした番号は画面に出します ──
**黙って捨てると、元のマスタが間違っていることに誰も気づけません。**
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from modules.packing_material_calculation.coil_tool import data_sync, db, import_specs
from modules.packing_material_calculation.tests._web import client, sandbox   # noqa: F401


@pytest.fixture
def source(tmp_path) -> Path:
    """取り込み元(梱包資材マスタ)を、実物と同じ形で作る。"""
    path = tmp_path / "梱包資材マスタ.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.execute('CREATE TABLE "包装仕様" ('
                 '"管理番号","包装仕様NO","パレット","サイズ","種類",'
                 '"納入先名称","用途名","リプラサイズ","コイル間スペーサー",'
                 '"最下部スペーサー","下本数","緩衝材","ｺｲﾙ間は間紙入",'
                 '"梱包単位_重量","重量範囲","梱包単位_枚数","枚数範囲",'
                 '"梱包総高さ","高さ範囲")')
    def put(管理番号, **over):
        row = dict.fromkeys(
            ["包装仕様NO", "パレット", "サイズ", "種類", "納入先名称", "用途名",
             "リプラサイズ", "コイル間スペーサー", "最下部スペーサー", "下本数",
             "緩衝材", "ｺｲﾙ間は間紙入", "梱包単位_重量", "重量範囲",
             "梱包単位_枚数", "枚数範囲", "梱包総高さ", "高さ範囲"], "")
        row.update(over)
        conn.execute(
            f'INSERT INTO "包装仕様" ("管理番号", {", ".join(chr(34)+c+chr(34) for c in row)})'
            f' VALUES ({", ".join("?" for _ in range(len(row) + 1))})',
            [管理番号, *row.values()])
    # 実物の 1C0118 と同じ並び・同じ食い違い方
    put("6", 包装仕様NO="1C0118", パレット="全面", 梱包単位_重量="1000",
        重量範囲="以下", リプラサイズ="30")
    put("7", 包装仕様NO="1C0118", パレット="全面",
        梱包単位_枚数="オーダー指定", リプラサイズ="30")
    put("8", 包装仕様NO="1C0200", パレット="スカシ")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def conn(sandbox, monkeypatch, source):
    monkeypatch.setattr(data_sync, "find_master_db", lambda: source)
    with db.connect() as c:
        db.apply_schema(c)
        yield c


def imported(conn) -> data_sync.TableResult:
    return [t for t in data_sync.import_master(conn) if t.table == "包装仕様"][0]


# ==================================================================
# 先の行を残す
# ==================================================================
def test_先に出てきた行を残す(conn):
    """**ここが今回の変更点。** 以前はあとの行で上書きしていた。"""
    imported(conn)
    row = conn.execute("SELECT * FROM 包装仕様 WHERE 包装仕様NO='1C0118'").fetchone()
    assert row["梱包単位_重量"] == "1000"      # 管理番号6 の行
    assert row["重量範囲"] == "以下"
    assert row["梱包単位_枚数"] == ""          # 管理番号7 で上書きされていない


def test_飛ばした番号を返す(conn):
    """**黙って捨てない。** 元のマスタを直せるように画面へ出す。"""
    result = imported(conn)
    assert result.duplicates == ["1C0118"]


def test_件数は手元に入った数(conn):
    """取り込み元は3行だが、手元は2行。

    「3件」と言いながら2件しか無いと、何が起きたのか分からない。
    """
    result = imported(conn)
    assert result.rows == 2
    assert conn.execute("SELECT COUNT(*) FROM 包装仕様").fetchone()[0] == 2


def test_重なっていない行はそのまま(conn):
    imported(conn)
    assert conn.execute(
        "SELECT パレット FROM 包装仕様 WHERE 包装仕様NO='1C0200'").fetchone()[0] == "スカシ"


def test_取り込みは成功扱い(conn):
    """重なりは断る理由ではない ── 断ると、直すまで仕事が止まる。"""
    assert imported(conn).ok is True


# ==================================================================
# 鍵を持つ表・持たない表
# ==================================================================
def test_鍵を持つ表だけを見る():
    """パレットや班員名簿は同じ値の行が並んでよい(全部読む表)。"""
    assert set(import_specs.UNIQUE_KEY_COLUMN) == {"包装仕様", "仕掛受注"}


def test_鍵の無い表は落とさない():
    rows = [{"種類": "全面"}, {"種類": "全面"}]
    kept, dropped = import_specs.drop_duplicates("パレット", rows)
    assert len(kept) == 2 and dropped == []


def test_受注番号も先の行を残す():
    """仕掛受注も鍵で引いて1件目を使う(VBA `フォーム展開`)。"""
    rows = [{"受注番号": "A", "受注材質": "先"},
            {"受注番号": "A", "受注材質": "あと"},
            {"受注番号": "B", "受注材質": "別"}]
    kept, dropped = import_specs.drop_duplicates("仕掛受注", rows)
    assert [r["受注材質"] for r in kept] == ["先", "別"]
    assert dropped == ["A"]


def test_画面に出す(client, sandbox, monkeypatch, source):
    """設定の「いま取り込む」の結果に出る。"""
    monkeypatch.setattr(data_sync, "find_master_db", lambda: source)
    r = client.post("/api/settings/import", json={})
    tables = {t["table"]: t for t in r.get_json()["tables"]}
    assert tables["包装仕様"]["duplicates"] == ["1C0118"]
