"""見出しクリックの並び替え ── 数字は数の大きさで並べる(3機能のマスタ管理で共通)

取り込み元(共有フォルダの sqlite3)は、数字も **TEXT の列に文字で**入っていることが多い
(仕掛台帳・梱包資材マスタ。Access からの移し替えや、型を書かずに作った表)。
そのまま ``ORDER BY 列`` にすると文字の並びになり、

    巾上限 ▲  1000, 1100, 1200, 1300, 1350, 800, 900, 9999

のように **800 が 1350 の後ろ**へ来る(現場の指摘: 列名を押して並べ替えたい ── 通し試験で
見つけた)。並べ替えは次の順にする:

    1. 数字に見えるもの(INTEGER・REAL、または数字だけの文字)を**数の大きさ**で
    2. それ以外の文字を文字の並びで
    3. 空(NULL・空白だけ)は**昇順でも降順でもいちばん後ろ**(Excel の並べ替えと同じ)

降順は 1・2 を逆にする(文字 → 数字の順)。同じ値の行の順は呼ぶ側が rowid で確定させる。
"""
from __future__ import annotations


def _looks_numeric(col: str) -> str:
    """その値が数字に見えるか(SQL の式)。

    ``-12`` ``3.5`` ``+7`` は数字。``1,000`` ``1-2`` ``1.2.3`` ``1C0118`` ``''`` は文字。
    全角の数字は文字のまま(取り込み元に全角の数字はない)。
    """
    t = f"trim(CAST({col} AS TEXT))"
    return (f"(typeof({col}) IN ('integer', 'real') OR ("
            f"{t} <> '' AND {t} GLOB '*[0-9]*'"
            f" AND {t} NOT GLOB '*[^0-9.+-]*'"       # 数字・小数点・符号だけ
            f" AND {t} NOT GLOB '?*[+-]*'"           # 符号は先頭だけ
            f" AND {t} NOT GLOB '*.*.*'))")          # 小数点は1つまで


def order_terms(col: str, direction: str) -> str:
    """``ORDER BY`` に並べる式(``col`` は引用済みの列名、``direction`` は ASC / DESC)。

    呼ぶ側が列名を**実在する列だけ**に絞ってから渡すこと(式に組み込むため)。
    """
    d = "DESC" if str(direction).upper() == "DESC" else "ASC"
    num = _looks_numeric(col)
    blank = f"(trim(CAST({col} AS TEXT)) = '' OR {col} IS NULL)"
    return (f"CASE WHEN {blank} THEN 1 ELSE 0 END ASC, "
            f"CASE WHEN {num} THEN 0 ELSE 1 END {d}, "
            f"CASE WHEN {num} THEN CAST({col} AS REAL) END {d}, "
            f"{col} {d}")
