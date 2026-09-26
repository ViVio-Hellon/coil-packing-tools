# -*- coding: utf-8 -*-
"""羅列計算（1〜50本の一覧表）。VBA の ``羅列計算`` に相当する画面。

== 2026.09 方針変更 ==

VBA の ``羅列計算`` は ``DB_Materials`` とは **別の計算をしていた**。

    1. 風袋に EX-DRY を含めない（8 項目合計。DB_Materials は 9 項目）
    2. 梱包高さに ハードボード 2.35mm×2 を加えない
    3. HB を ``CalcHBForList``（HBFlag を使わず巾と本数だけで判定）で出す

これは「片側だけ直して、もう片側が残った」ものと確認できたため、
**一覧表は参考値であり ``CalculateMaterials`` を正とする** という判断に従い、
本モジュールは自前の計算をやめ、**本数 1〜50 について
``tare_calc.calculate_materials`` を呼ぶだけ**に変えた。

これにより
    ・風袋 / GW / 梱包高さ / HB が 風袋計算画面と必ず一致する
    ・資材の計算式が 1 箇所（tare_calc）だけになる
    ・EX-DRY が一覧にも現れる（列を 1 つ追加した）
"""

from __future__ import annotations

import logging
from typing import Dict, List

from ..repositories import material_repo as M
from . import tare_calc as TC
from .vba_compat import cstr, fmt, val, ws_roundup

log = logging.getLogger(__name__)

MAX_COILS = 50

#: 一覧の見出し（VBA の `50まで` シート 1 行目に相当。EX-DRY を追加）
HEADERS = [
    "本数",
    "ﾋﾟﾝ\n(kg)", "ﾁｯﾌﾟﾎﾞｰﾙ\n(kg)", "ｽﾄﾚｯﾁﾌｨﾙﾑ\n(kg)", "ｴｻﾌｫｰﾑ\n(kg)",
    "ﾎﾟﾘｼｰﾄ\n(kg)", "ﾊｰﾄﾞﾎﾞｰﾄﾞ\n(kg)", "樹脂ﾊﾟﾚｯﾄ\n(kg)", "ﾍﾟｯﾄﾊﾞﾝﾄﾞ\n(kg)",
    "EX-DRY\n(kg)",
    "NW\n(kg)", "風袋\n(kg)", "GW\n(kg)",
    "ｺｲﾙ高さ\n(mm)", "梱包高さ\n(mm)", "ﾁｯﾌﾟﾎﾞｰﾙ高さ\n(mm)",
]

#: 列キー（HEADERS と同順）
COLUMN_KEYS = ["count", "pin", "tip", "sut", "esa", "por", "hb", "par", "pet",
               "dry", "nw", "hu", "gw", "coilHeight", "packHeight", "tipHeight"]


def build_list(table: M.MaterialTable, width_mm: float, tip: str,
               t1: float, t2: float,
               take1_visible: bool, take2_visible: bool,
               cb_key: str = "", ob_idx: int = 0,
               ) -> Dict:
    """1〜50 本の一覧を作る。各行は ``calculate_materials`` の結果そのもの。

    NW の丈の選び方は VBA と同じ
    （丈1 が表示中なら丈1 の 1 条重量、丈2 だけなら丈2 の 1 条重量）。
    ``calculate_materials`` は coilNo 1,2 で T1・3,4 で T2 を使うため、
    丈1 が見えていれば 1、そうでなければ 3 を渡す。
    """
    coil_no = 1 if take1_visible else 3

    rows: List[dict] = []
    missing: List[str] = []

    for q in range(1, MAX_COILS + 1):
        mat = TC.calculate_materials(
            table, q, coil_no, width_mm, tip, cb_key, ob_idx,
            t1, t2, take1_visible, take2_visible,
            all_coil_empty=False,
            )

        for name in mat.missing:
            if name not in missing:
                missing.append(name)

        disp = TC.display_values(mat)
        rows.append({
            "count": str(q),
            "pin": disp["PI"],
            "tip": disp["TIP"],
            "sut": disp["SUT"],
            "esa": disp["ESA"],
            "por": disp["POR"],
            "hb": disp["HB"],
            "par": disp["PAR"],
            "pet": disp["PET"],
            "dry": disp["DRY"],
            "nw": disp["NW"],
            "hu": disp["HU"],
            "gw": disp["GW"],
            "coilHeight": fmt(val(mat.TA * 1000), "0"),
            # 風袋計算画面の高さ表示と同じ（樹脂パレット上下 + ハードボード上下、
            # 10mm 単位で切り上げ）
            "packHeight": fmt(ws_roundup(mat.KTA, -1), "0.0"),
            "tipHeight": fmt(val(mat.TIPT * 1000), "0"),
            "raw": {
                "pin": mat.PIN, "tip": mat.TIP, "sut": mat.SUT, "esa": mat.ESA,
                "por": mat.POR, "hb": mat.HB, "par": mat.PAR, "pet": mat.PET,
                "dry": mat.DRY, "nw": mat.NW, "hu": mat.HU, "gw": mat.GW,
                "ta": mat.TA, "kta": mat.KTA, "tipt": mat.TIPT,
            },
        })

    return {"headers": HEADERS, "columnKeys": COLUMN_KEYS,
            "rows": rows, "missing": missing}


def quick_height(coil_count: float, width_mm: float) -> Dict[str, str]:
    """VBA ``CommandButton13``（積み高さクイック計算）。

    注意: VBA は ``TA = 1 * col / 10^3 + TIPT`` と **本数を掛けない**（1条分）。
          また ``KTA`` に ハードボード 2.35×2 を加算しない。
          この 2 点は「1 条分の参考表示」という用途に沿っているため現行どおり。
          巾の対応表だけは他処理と食い違っていた（53.5 を 43.5 としていた）ため、
          53.5 に統一した（解析書 13章 #2）。
    """
    tipt = val((coil_count + TC.CHIP_EXTRA_SHEETS)
               * TC.CHIP_THICKNESS_MM / 10 ** 3)
    ta = val(1 * width_mm / 10 ** 3) + tipt
    kta = val(ta * 1000 + TC.PALLET_HEIGHT_MM * 2)
    return {
        "tip": fmt(tipt * 1000, "0.000"),
        "one": fmt(ta * 1000, "0.000"),
        "palletRoundUp": fmt(ws_roundup(kta, -1), "0.0"),
        "palletRaw": fmt(kta, "0.000"),
        # VBA は MsgBox で & 連結（＝ CStr）しているので同じ桁で出す。
        # 素の float を埋めると "8.399999999999999" のようになってしまう。
        "message": (f"ﾁｯﾌﾟ:{cstr(tipt * 1000)}\n"
                    f"1条分:{cstr(ta * 1000)}\n"
                    f"ﾊﾟﾚｯﾄ+分(繰上値):{cstr(ws_roundup(kta, -1))}"
                    f" : そのまま{cstr(kta)}"),
    }
