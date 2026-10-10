# -*- coding: utf-8 -*-
"""ラベル台紙への転記。VBA ``WriteAllToSheet`` / ``WriteCoilInfo`` の移植。

VBA は Excel シートのセルへ直接書いていたが、移植では
「セル座標 -> 値」のマップ（``CellMap``）を生成し、
それを DB へ保存 / 画面表示 / 印刷ビュー / CSV 出力に使い回す。

実シート（``1.0mm×53.5mm 丈1.xlsx``）の内容と突き合わせて検証済み。
    G8=-0101 / AM8=-0109 / G97=-0117 / AM160=-0132
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import size_master as SM
from .vba_compat import fmt, is_numeric


def weight_text(wt: str) -> str:
    """ラベルに刷る重量の形。VBA の台紙のセル書式 ``0.0_ `` と同じ **小数1桁**(四捨五入)。

    重量器は小数2桁まで量れないので、打った値はふつう小数1桁まで。打ったまま刷ると
    "20" が 20、"20.15" が 20.15 になり、VBA の台紙(20.0・20.2)と違っていた(統合 1.2.6)。
    **台紙のセル(保存する値)は打ったまま**(VBA も値はそのまま書き、セルの書式で小数1桁に
    見せる。本物の台紙と照らし合わせる試験 ``test_label_layout`` もそう)。書式を付けるのは
    画面・印刷に出すときだけ。バーコードは打ったまま(VBA も打った文字のまま組む)。
    """
    return fmt(wt, "0.0") if is_numeric(wt) else wt

#: (row, col) -> value
CellMap = Dict[Tuple[int, int], str]


def col_letter(col: int) -> str:
    """列番号(1始まり) -> Excel 列名。表示・照合用。"""
    s = ""
    n = int(col)
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def coil_numbers(take_type: int) -> List[Tuple[str, str]]:
    """VBA ``WriteAllToSheet`` のコイル番号採番。

    戻り値は 16 要素の ``(左面, 右面)``。
    左面 = 01..08 / 17..24、右面 = 09..16 / 25..32。
    """
    out = []
    for i in range(16):
        if i < 8:
            seq_l, seq_r = i + 1, i + 9
        else:
            seq_l, seq_r = i + 9, i + 17
        out.append((
            "-" + fmt(take_type, "00") + fmt(seq_l, "00"),
            "-" + fmt(take_type, "00") + fmt(seq_r, "00"),
        ))
    return out


def build_label_cells(ken: str, wt: str, kataban: str,
                      take_type: int) -> CellMap:
    """VBA ``WriteAllToSheet``。ヘッダー + ラベル 32 枚のセルマップを返す。"""
    cells: CellMap = {}
    bc_kata = "*" + kataban + "*"

    # --- ヘッダー部 ---
    cells[(SM.HDR_KEN_ROW, SM.HDR_KEN_COL)] = ken
    cells[(SM.HDR_KEN2_ROW, SM.HDR_KEN2_COL)] = ken
    cells[(SM.HDR_WT1_ROW, SM.HDR_WT1_COL)] = wt
    cells[(SM.HDR_WT2_ROW, SM.HDR_WT2_COL)] = wt

    # --- ラベル 16枚 × 左右2面 ---
    numbers = coil_numbers(take_type)
    for i, base in enumerate(SM.LABEL_BASE_ROWS):
        coil_l, coil_r = numbers[i]

        # コイル番号（表示形式 "@" = 文字列）
        #
        # VBA はこのあと「' 検査番号 (base+0, col 4/36)」というコメントに続けて
        # 同じ 7/39 列へ coilL/coilR を書き直している（NumberFormat="@" の設定が目的）。
        # コメントに反し **検査番号は書いていない**。
        # ラベル上の検査番号は台紙シート側の数式（D8=V3, AJ8=D8）で表示されるため、
        # ここでは VBA が実際に書く値だけを持ち、数式分は
        # build_template_derived_cells() で別途再現する。
        cells[(base + SM.OFS_COIL, SM.COL_COIL_L)] = coil_l
        cells[(base + SM.OFS_COIL, SM.COL_COIL_R)] = coil_r

        # 型番
        cells[(base + SM.OFS_KATABAN, SM.COL_KATA_L)] = kataban
        cells[(base + SM.OFS_KATABAN, SM.COL_KATA_R)] = kataban

        # 重量
        cells[(base + SM.OFS_WEIGHT, SM.COL_WT_L)] = wt
        cells[(base + SM.OFS_WEIGHT, SM.COL_WT_R)] = wt

        # 重量単位（重量と同じ行、コイル番号と同じ列）
        cells[(base + SM.OFS_WEIGHT, SM.COL_COIL_L)] = "kg"
        cells[(base + SM.OFS_WEIGHT, SM.COL_COIL_R)] = "kg"

        # バーコード(型番)
        cells[(base + SM.OFS_BC_KATA, SM.COL_BC_KATA_L)] = bc_kata
        cells[(base + SM.OFS_BC_KATA, SM.COL_BC_KATA_R)] = bc_kata

        # バーコード(検番 + コイル番号 + 重量)
        cells[(base + SM.OFS_BC_KEN, SM.COL_BC_KEN_L)] = f"*{ken}{coil_l} {wt}*"
        cells[(base + SM.OFS_BC_KEN, SM.COL_BC_KEN_R)] = f"*{ken}{coil_r} {wt}*"

    return cells


def build_template_derived_cells(ken: str) -> CellMap:
    """台紙シート側の数式が生む値を再現する。

    実台紙（``1.0mm×53.5mm 丈1.xlsx``）では、各ラベルの検査番号セルが
    ヘッダーを参照する数式になっている。

        D8  = V3    （1ブロック目: ヘッダー行 3 の検査番号）
        AJ8 = D8    （右面は左面を参照）
        D97 = V92   （2ブロック目: ヘッダー行 92 の検査番号）

    VBA は V3 と V92 の両方へ同じ検査番号を書くため、
    結果としてどのラベルにも同じ検査番号が出る。
    """
    cells: CellMap = {}
    for i, base in enumerate(SM.LABEL_BASE_ROWS):
        # 前半 8 枚 -> ヘッダー行 3、後半 8 枚 -> ヘッダー行 92 を参照
        cells[(base + SM.OFS_KENSA, SM.COL_KEN_L)] = ken
        cells[(base + SM.OFS_KENSA, SM.COL_KEN_R)] = ken
    return cells


def build_sheet_cells(ken: str, wt: str, kataban: str,
                      take_type: int) -> CellMap:
    """VBA が書くセル + 台紙数式が生むセル。印刷ビュー／照合用。"""
    cells = build_template_derived_cells(ken)
    cells.update(build_label_cells(ken, wt, kataban, take_type))
    return cells


def build_label_rows(ken: str, wt: str, kataban: str,
                     take_type: int) -> List[dict]:
    """画面・印刷用に「ラベル1枚」を 1 レコードへまとめた形。

    ``build_label_cells`` と同じ値を、人が読める単位で返す。
    """
    rows = []
    numbers = coil_numbers(take_type)
    for i, base in enumerate(SM.LABEL_BASE_ROWS):
        coil_l, coil_r = numbers[i]
        for side, coil, cols in (
            ("L", coil_l, (SM.COL_BC_KATA_L, SM.COL_COIL_L,
                           SM.COL_KATA_L, SM.COL_WT_L, SM.COL_BC_KEN_L)),
            ("R", coil_r, (SM.COL_BC_KATA_R, SM.COL_COIL_R,
                           SM.COL_KATA_R, SM.COL_WT_R, SM.COL_BC_KEN_R)),
        ):
            rows.append({
                "index": i + 1,
                "side": side,
                "baseRow": base,
                "coilNo": coil,
                "kataban": kataban,
                "kensaNo": ken,
                "weight": weight_text(wt),
                "unit": "kg",
                "barcodeKataban": "*" + kataban + "*",
                "barcodeKensa": f"*{ken}{coil} {wt}*",
                "cells": {
                    "barcodeKataban": f"{col_letter(cols[0])}{base + SM.OFS_BC_KATA}",
                    "coilNo": f"{col_letter(cols[1])}{base + SM.OFS_COIL}",
                    "kataban": f"{col_letter(cols[2])}{base + SM.OFS_KATABAN}",
                    "weight": f"{col_letter(cols[3])}{base + SM.OFS_WEIGHT}",
                    "barcodeKensa": f"{col_letter(cols[4])}{base + SM.OFS_BC_KEN}",
                },
            })
    return rows


def build_coil_info_cells(data_row: int, extra_row: int,
                          coil_h1: str, coil_h2: str,
                          ta1: str, ta2: str,
                          nw1: str, nw2: str,
                          gw1: str, gw2: str) -> CellMap:
    """VBA ``WriteCoilInfo``。本数 / 高さ / NW / GW を転記。

    ``dataRow`` と ``dataRow + 1`` の 2 段。``extraRow > 0`` なら同じ値を複写。
    """
    cells: CellMap = {}

    def put(r: int, h: str, ta: str, nw: str, gw: str) -> None:
        cells[(r, SM.COL_COILH)] = h
        cells[(r, SM.COL_TA)] = ta
        cells[(r, SM.COL_NW)] = nw
        cells[(r, SM.COL_GW)] = gw

    put(data_row, coil_h1, ta1, nw1, gw1)
    put(data_row + 1, coil_h2, ta2, nw2, gw2)

    if extra_row and extra_row > 0:
        put(extra_row, coil_h1, ta1, nw1, gw1)
        put(extra_row + 1, coil_h2, ta2, nw2, gw2)

    return cells


def clear_coil_info_cells(data_row: int, extra_row: int) -> List[Tuple[int, int]]:
    """VBA ``ClearCoilCells``（``クリア`` から呼ばれる）。消す座標を返す。"""
    targets = []
    for r in (data_row, data_row + 1):
        targets += [(r, SM.COL_COILH), (r, SM.COL_TA),
                    (r, SM.COL_NW), (r, SM.COL_GW)]
    if extra_row and extra_row > 0:
        for r in (extra_row, extra_row + 1):
            targets += [(r, SM.COL_COILH), (r, SM.COL_TA),
                        (r, SM.COL_NW), (r, SM.COL_GW)]
    return targets


def cellmap_to_a1(cells: CellMap) -> Dict[str, str]:
    """``(row, col)`` キーを ``"D8"`` 形式へ。照合・CSV 用。"""
    return {f"{col_letter(c)}{r}": v for (r, c), v in sorted(cells.items())}
