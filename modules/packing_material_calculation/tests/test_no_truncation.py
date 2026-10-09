"""数の丸め・切り捨てを VBA と同じにする(統合 1.2.6)

python-web-tools で「取り込み・書き込みで数を勝手に丸め、違う数を書いていた」不具合が見つかり、
同じ種類が無いかを洗った。資材計算で見つかったもの:

1. 書式 `fmt`(VBA `Format(x,"0.00")`)が Python の書式(2進の値のまま偶数の側へ丸め)だった。
   VBA は有効数字 15 桁で見て四捨五入する。製品単重 106.475 は VBA の書き出しで 106.48
   (`tests/data/VBA書き出し_20260923_疑似` の Q1293A0)、ツールは 106.47 だった。単重は積数に使う
2. 受注の 梱包単位_重量・枚数、コイル外径・内径を整数に切り捨てていた(1160.5 → 1160)。
   VBA は値のままテキストボックスへ入れる。外径はパレットの選び方、重量・枚数は積数 →
   台数 → チェックリスト・発注履歴へ乗る
"""
from __future__ import annotations

import sqlite3

import pytest

from modules.packing_material_calculation.coil_tool import db, lot_service, vba


@pytest.mark.parametrize("value, decimals, want", [
    (106.475, 2, "106.48"),     # VBA の書き出しどおり(2進では 106.47499…)
    ("106.475", 2, "106.48"),
    (1.4, 3, "1.400"),
    (47, 1, "47.0"),
    (0.125, 2, "0.13"),         # 半分は 0 から遠いほうへ(Python の書式は 0.12)
    (2.675, 2, "2.68"),
    (-0.001, 2, "0.00"),        # "-0.00" にしない
])
def test_fmt_rounds_like_vba_format(value, decimals, want):
    assert vba.fmt(value, decimals) == want


@pytest.mark.parametrize("value, want", [
    (1160, "1160"), (1160.0, "1160"), (1160.5, "1160.5"), ("2.9", "2.9"), (1000.25, "1000.25"),
])
def test_num_text_keeps_decimals_and_integers(value, want):
    assert vba.num_text(value) == want


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.apply_schema(c)
    yield c
    c.close()


def _order(conn, **values):
    row = {"受注番号": "T1", "包装仕様NO": "1C0001", "製品単重": 106.475, "受注板厚": 1.4,
           "受注板幅": 47.0, "梱包単位_重量": 1000.5, "梱包単位_枚数": 2.9,
           "コイル外径_MAX": 1160.5, "コイル外径_目標": 1100.0, "コイル外径_MIN": 0.0,
           "コイル内径_目標": 508.7, "材質_比重": 2.7}
    row.update(values)
    cols = ", ".join(row)
    conn.execute(f"INSERT INTO 仕掛受注 ({cols}) VALUES ({', '.join('?' * len(row))})",
                 list(row.values()))


def test_expand_order_keeps_decimals(conn):
    _order(conn)
    info = lot_service.expand_order(conn, "T1")
    assert info.製品単重 == "106.48"
    assert (info.梱包単位_重量, info.梱包単位_枚数) == ("1000.5", "2.9")
    assert (info.コイル外径_MAX, info.コイル外径_目標, info.コイル内径_目標) == ("1160.5", "1100", "508.7")
    assert info.コイル外径_MIN == lot_service.OrderInfo.NO_DATA      # 0 は「データなし」のまま


def test_integer_values_are_unchanged(conn):
    """整数の受注は今までと同じ形(画面・帳票の見た目を変えない)。"""
    _order(conn, 梱包単位_重量=1000.0, 梱包単位_枚数=0.0, コイル外径_MAX=1160.0,
           コイル内径_目標=508.0, 製品単重=106.48)
    info = lot_service.expand_order(conn, "T1")
    assert (info.梱包単位_重量, info.梱包単位_枚数, info.コイル外径_MAX, info.コイル内径_目標) == \
        ("1000", lot_service.OrderInfo.NO_DATA, "1160", "508")
    assert info.製品単重 == "106.48"
