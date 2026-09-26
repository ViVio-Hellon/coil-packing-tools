"""テスト用の手元DB ── 実マスタが無くても業務ロジックを確かめられるようにする

実データは共有フォルダにしか無いので、**同じ形の小さなマスタ**を
その場で作る。行の中身は VBA の判定が通る最小限。
"""
from __future__ import annotations

import sqlite3

from modules.packing_material_calculation.coil_tool import db


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.apply_schema(conn)
    return conn


SPEC_DEFAULTS = {
    "包装仕様NO": "", "納入先名称": "", "用途名": "", "パレット": "スカシ",
    "サイズ": "", "種類": "", "リプラサイズ": "30",
    "コイル間スペーサー": "リプラ", "最下部スペーサー": "リプラ",
    "下本数": "3", "緩衝材": "HB", "コイル間は間紙入": "",
    "梱包単位_重量": "", "重量範囲": "", "梱包単位_枚数": "", "枚数範囲": "",
    "梱包総高さ": "", "高さ範囲": "",
}


def add_spec(conn: sqlite3.Connection, spec_no: str, **over) -> None:
    row = dict(SPEC_DEFAULTS, 包装仕様NO=spec_no, **over)
    cols = ", ".join(f'"{c}"' for c in row)
    conn.execute(f"INSERT OR REPLACE INTO 包装仕様 ({cols})"
                 f" VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    conn.commit()


def add_pallet(conn: sqlite3.Connection, 種類: str, W: float, 新記号: str,
               巾下限=0.0, 巾上限=9999.0, 丈下限=0.0, 丈上限=9999.0) -> None:
    conn.execute(
        'INSERT INTO パレット (種類, 巾下限, 巾上限, 丈下限, 丈上限, 新記号, W)'
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (種類, 巾下限, 巾上限, 丈下限, 丈上限, 新記号, W))
    conn.commit()


def add_ripla_lengths(conn: sqlite3.Connection, *lengths: float) -> None:
    conn.executemany("INSERT INTO リプラサイズ (リプラ長さ, 長さ) VALUES (?, ?)",
                     [(n, str(int(n))) for n in lengths])
    conn.commit()


def standard_master(conn: sqlite3.Connection) -> None:
    """よく使う形をひととおり入れる。"""
    for w, mark in ((800, "P08"), (900, "P09"), (1000, "P10"),
                    (1100, "P11"), (1200, "P12"), (1300, "P13"), (1350, "P135")):
        add_pallet(conn, "スカシ", w, mark, 巾下限=0, 巾上限=w, 丈下限=0, 丈上限=w)
        add_pallet(conn, "全面", w, mark, 巾下限=0, 巾上限=w, 丈下限=0, 丈上限=w)
        add_pallet(conn, "EXPS", w, mark)
        add_pallet(conn, "強度UP", w, mark, 巾下限=0, 巾上限=w)
    add_ripla_lengths(conn, 700, 800, 900, 950, 1000, 1050, 1100, 1150)
