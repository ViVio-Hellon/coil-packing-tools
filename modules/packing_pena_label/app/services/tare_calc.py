# -*- coding: utf-8 -*-
"""風袋（梱包資材）重量計算。VBA ``DB_Materials`` / ``DB_HB`` の移植。

**計算式・分岐・丸めは VBA と 1 対 1 に対応させている。**
VBA 側の宣言型（``TA As Single`` 等）も ``vba_compat.single()`` で再現する。

対応表
    SetCoilDimensions      -> set_coil_dimensions()
    SetDiameterInfo        -> set_diameter_info()
    CalculatePIN..DRY      -> _calc_pin() .. _calc_dry()
    DB_HB / CalculateHBData-> calc_hb()
    JudgeHBFlag            -> judge_hb_flag()
    CalculateHBValue       -> _calc_hb_value()
    CalculateNW            -> calc_nw()
    CalculateMaterials     -> calculate_materials()
    DB_Materials           -> calc_package()
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from ..models import HbData, MaterialWeights, TIP_DIAMETER
from ..repositories import material_repo as M
from .vba_compat import (PI, cdbl, cstr, cstr_single, fmt, single, val,
                         ws_round, ws_roundup)

log = logging.getLogger(__name__)

# ==================================================== 梱包資材の物理定数
# VBA 側と **同じ名前** にそろえてある（2026.09 の定数化に追従）。
# 片方だけ直すとズレる値が計算側と表示側の両方に埋まっていたため、
# VBA でも Public Const に出した。名前を合わせておくと突き合わせやすい。

#: チップボール 1 枚厚(mm)
CHIP_THICKNESS_MM = 0.7
#: チップボール枚数 = 本数 + この値（一番上の 1 枚）
CHIP_EXTRA_SHEETS = 1

# ---------------------------------------------------- 梱包寸法
#: 樹脂パレット高さ(mm)。上下各 1
PALLET_HEIGHT_MM = 150
#: ハードボード 1 枚厚(mm)。上下各 1
HB_THICKNESS_MM = 2.35
#: 胴巻き HB 外周方向 +700mm（巻き重ね代）
HB_MARGIN_M = 0.7
#: 胴巻き HB の高さ補正(m)。**0（補正しない）で確定**
#:
#: 積高と同寸だとバンドが締まらないので低くしたい、という話はあったが、
#: 標準 HB（固定物以外）は **在庫から現場でカットするため高さがランダム**で、
#: 「積高より ◯mm 低い」という基準がそもそも存在しない。
#:
#:     53.5 × 11本  積高 596.9  使用板 HB590  差 -6.9
#:     53.5 × 10本  積高 542.7  使用板 HB530  差 -12.7
#:
#: 固定物（HB590 / HB530）は品番で決まり、高さを計算に使わない
#: （マスタの固定値 × 2 枚）。将来基準が決まればここだけ変えれば反映される。
HB_BAND_CLEARANCE_M = 0.0
#: 補正後の下限(m)。これを割る場合は補正しない
HB_MIN_HEIGHT_M = 0.05
#: PET バンド 締め代(m)
BAND_SLACK_M = 0.2
#: ストレッチフィルム 巻き数
STRETCH_WRAPS = 5
#: これを超えたらエサフォーム 2 周(m)
ESA_DOUBLE_LIMIT_M = 0.82
#: 樹脂パレット 台数
PALLET_QTY = 2
#: ピン本数（固定）
PIN_COUNT = 4
#: 八角ハードボード 上下 2 枚
HB_OCT_COUNT = 2


# ==================================================== 共通の寸法計算
def get_coil_ta(coil_text: float, col: float) -> float:
    """コイル高さ TA(m)。VBA ``GetCoilTA``。

    ``TA = コイル積高 + チップボール積厚（本数+1枚 × 0.7mm）``

    **資材計算本体とハードボードの両方がこれを使う。**
    以前は ``SetCoilDimensions`` と ``GetHBCoilDimensions`` が別々に
    計算していて、後者は ``/10^3`` の位置が違い括弧の中で mm と m を
    足していたため、チップボール分（11本で 8.4mm）が実質消えていた。
    2026.09 に共通関数へ集約して統一（解析書 A-1）。

    戻り値は Double。``mat.TA`` は VBA の宣言が Single なので
    呼び出し側で ``single()`` を通すこと（``HB.TA`` は Double のまま）。
    """
    tipt = val((coil_text + CHIP_EXTRA_SHEETS) * CHIP_THICKNESS_MM / 10 ** 3)
    return val(coil_text * col / 10 ** 3) + tipt


def get_coil_gai(tip: str) -> float:
    """外周 GAI(m)。VBA ``GetCoilGAI``。

    コイル直径はパレットサイズと同等（チップボールの品番より一段大きい）。
    """
    diam = TIP_DIAMETER.get(tip, (0, 0))[0]
    return val(diam * PI / 10 ** 3) if diam else 0.0


def get_hb_band_height(ta: float) -> float:
    """胴巻きハードボードの高さ(m)。VBA ``GetHBBandHeight``。

    ``HB_BAND_CLEARANCE_M`` が 0 の間は積高をそのまま返す。
    補正後が下限を割る場合も積高をそのまま返す（実用上は発生しない想定）。
    """
    h = ta - HB_BAND_CLEARANCE_M
    return ta if h < HB_MIN_HEIGHT_M else h


def _unit(table: M.MaterialTable, name: str, missing: List[str],
          mat: Optional[MaterialWeights] = None,
          key: str = "") -> Tuple[float, float]:
    """VBA ``GetUnitMassAndCoefficient`` + ``Val()``。

    マスタに無ければ VBA と同じく 0 になるが、資材名を ``missing`` へ記録する。

    ``mat`` と ``key`` を渡すと、計算式の欄に出すための
    **マスタの文字列そのまま** を ``mat.units_raw`` へ控える。
    VBA は Variant をそのまま ``CStr`` するので、係数 "1" は "1" と出る
    （数値化してから文字列にすると "1.0" になってしまう）。
    """
    mass, coef = table.unit_of(name)
    if mass == "" and coef == "":
        if name not in missing:
            missing.append(name)
        log.warning("資材マスタに存在しません（0kg として計算）: %s", name)
    if mat is not None and key:
        mat.units_raw[key] = (mass, coef)
    return val(mass), val(coef)


# ============================================================ 寸法
def set_coil_dimensions(mat: MaterialWeights, coil_text: float,
                        width_mm: float, all_coil_empty: bool) -> Optional[float]:
    """VBA ``SetCoilDimensions``。

    戻り値は 画面の ``TA{coilNo}`` に表示する梱包高さ(mm)。
    全本数が空の場合は VBA と同じく計算せず ``None``（＝NW/GW/TA クリア）。
    """
    mat.col = width_mm

    # VBA: 本数が空白の場合終了（NW/GW/TA をクリアして Exit Sub）
    if all_coil_empty:
        return None

    # 高さ計算は共通関数へ集約（VBA GetCoilTA）
    mat.TIPT = single(val((coil_text + CHIP_EXTRA_SHEETS)
                          * CHIP_THICKNESS_MM / 10 ** 3))
    # mat.TA As Single（VBA の宣言が Single）
    mat.TA = single(get_coil_ta(coil_text, mat.col))
    # KTA As Single ： 樹脂パレット上下 + ハードボード上下
    mat.KTA = single(val(mat.TA * 1000 + PALLET_HEIGHT_MM * 2)
                     + (HB_THICKNESS_MM * 2))
    return mat.KTA


def set_diameter_info(mat: MaterialWeights, tip: str) -> None:
    """VBA ``SetDiameterInfo``。外径 / 半径 / 外周 GAI。"""
    if tip not in TIP_DIAMETER:
        # VBA では If/ElseIf のどれにも入らず 0 のまま
        mat.TempDiameter = ""
        mat.HalfDiameter = ""
        mat.GAI = 0.0
        return
    diam, half = TIP_DIAMETER[tip]
    mat.TempDiameter = str(diam)
    mat.HalfDiameter = str(half)
    mat.GAI = get_coil_gai(tip)


# ============================================================ 各資材
def _calc_pin(table, mat, missing) -> float:
    mass, coef = _unit(table, M.MAT_PIN, missing, mat, "PIN")
    mat.units["PIN"] = (mass, coef)
    result = val(PIN_COUNT * mass * coef)
    mat.formulas["PIsiki"] = (
        "【ピン】本数×単重\n"
        f"{PIN_COUNT}本 × {fmt(mass, '0.000')} = {fmt(result, '0.000')}")
    return result


def _calc_tip(table, mat, coil_text, tip, missing) -> float:
    name = M.TIP_MATERIAL.get(tip)
    if name is None:
        # VBA: どの TIP も選択されていないと 単重 が未初期化 -> 0
        mass, coef = 0.0, 0.0
        mat.units["TIP"] = (mass, coef)
    else:
        mass, coef = _unit(table, name, missing, mat, "TIP")
        mat.units["TIP"] = (mass, coef)
    maisu = coil_text + CHIP_EXTRA_SHEETS
    result = val(maisu * mass * coef)
    mat.formulas["TIPsiki"] = (
        f"【チップボール】（積み本数＋{CHIP_EXTRA_SHEETS}枚）×単重\n"
        f"({fmt(coil_text, '0')} + {CHIP_EXTRA_SHEETS})"
        f" × {fmt(mass, '0.000')} = {fmt(result, '0.00')}")
    return result


def _calc_sut(table, mat, missing) -> float:
    mass, coef = _unit(table, M.MAT_SUT, missing, mat, "SUT")
    mat.units["SUT"] = (mass, coef)
    result = val(mat.GAI * STRETCH_WRAPS * mass * coef)
    mat.formulas["SUTsiki"] = (
        "【ストレッチフィルム】外周5周分の長さ×単重\n"
        f"{fmt(mat.GAI, '0.000')}m × {STRETCH_WRAPS}周 × "
        f"{fmt(mass, '0.000')} = {fmt(result, '0.000')}")
    return result


def _calc_esa(table, mat, missing) -> float:
    """エサフォーム。``TA > 0.82`` で 2 周。TA は Single のまま比較する。"""
    mass, coef = _unit(table, M.MAT_ESA, missing, mat, "ESA")
    mat.units["ESA"] = (mass, coef)
    if mat.TA > ESA_DOUBLE_LIMIT_M:
        result = val(mat.GAI * 2 * mass * coef)
        wraps = 2
    else:
        result = val(mat.GAI * mass * coef)
        wraps = 1
    mat.formulas["ESAsiki"] = (
        f"【エサフォーム】外周の長さ×単重（{wraps}周）\n"
        f"{fmt(mat.GAI, '0.000')}m × {wraps}周 × "
        f"{fmt(mass, '0.000')} = {fmt(result, '0.000')}")
    return result


def _calc_por(table, mat, tip, missing) -> float:
    """ポリシート。円柱の表面積（底面×2 + 側面）。包み分は高さの半分。"""
    mass, coef = _unit(table, M.MAT_POR, missing, mat, "POR")
    mat.units["POR"] = (mass, coef)
    half = TIP_DIAMETER.get(tip, (0, 0))[1]
    area = val(half ** 2 * PI)
    area2 = val((mat.TA + mat.TA / 2) * mat.GAI)
    result = val((area * 2 + area2) / 10 ** 6 * mass * coef)
    mat.formulas["PORsiki"] = (
        "【ポリシート】円柱の表面積＝2πr^2＋2πrh\n"
        f"(底面:2π×{half}2 + 側面:2π×{half}×{fmt(mat.TA, '0.000')}) × "
        f"{fmt(mass, '0.000')} = {fmt(result, '0.000')}")
    return result


def _calc_pet(table, mat, tip, missing) -> float:
    """PETバンド。胴巻き4本 + 縦バンド4本×2。締め代 200mm。"""
    mass, coef = _unit(table, M.MAT_PET, missing, mat, "PET")
    mat.units["PET"] = (mass, coef)
    dou = val((mat.GAI + BAND_SLACK_M) * 4)
    diam = TIP_DIAMETER.get(tip, (0, 0))[0]
    tate = val(((mat.TA + BAND_SLACK_M) * 4) * 2 + ((diam * 4) * 2) / 10 ** 3)
    result = (dou + tate) * mass * coef
    mat.formulas["PETsiki"] = (
        "【PETバンド】胴巻き4本分＋縦バンド4本分\n"
        f"胴巻き:{fmt(dou, '0.000')}m + 縦バンド:{fmt(tate, '0.000')}m "
        f"= {fmt(result, '0.000')}")
    mat.formulas["_PET_DOU"] = fmt(dou, "0.000")
    mat.formulas["_PET_TATE"] = fmt(tate, "0.000")
    return result


def _calc_par(table, mat, missing) -> float:
    mass, coef = _unit(table, M.MAT_PAR, missing, mat, "PAR")
    mat.units["PAR"] = (mass, coef)
    result = mass * coef * PALLET_QTY
    mat.formulas["PARsiki"] = (
        f"【樹脂パレット】{PALLET_QTY}台\n"
        f"{fmt(mass, '0.000')} × {PALLET_QTY}台 = {fmt(result, '0.0')}")
    return result


def _calc_dry(table, mat, missing) -> float:
    mass, coef = _unit(table, M.MAT_DRY, missing, mat, "DRY")
    mat.units["DRY"] = (mass, coef)
    result = mass * coef
    mat.formulas["DRYsiki"] = (
        "【乾燥剤】1つ\n"
        f"{fmt(mass, '0.000')} × 1つ = {fmt(result, '0.000')}")
    return result


# ============================================================ ハードボード
def judge_hb_flag(coil_text: float, cb_key: str, ob_idx: int) -> bool:
    """VBA ``JudgeHBFlag``。

    上から順に評価し、最初に一致したもので決まる（VBA の Select Case True と同じ）。
    ``cb_key`` は CB モード時の CheckBox 名、``ob_idx`` は OB モード時の 1..14。

    注: 解析書のとおり、実際に結果へ影響するのは ``col = 53.5`` かつ 10/11 本のみ。
        それ以外の条件は Flag=True でも Flag=False と同じ式になるが、
        将来のサイズ別固定 HB 追加を見込んだ VBA の分岐構造をそのまま保持する。
    """
    n = coil_text
    #: CheckBox 名 -> Flag=True になる本数（VBA の Select Case True と同じ並び）
    cb_rules = {
        "CheckBox1": (14,),        # 40mm
        "CheckBox2": (15,),        # 33mm
        "CheckBox3": (8, 7),       # 73mm
        "CheckBox4": (7, 6),       # 82.5mm
        "CheckBox5": (11, 10),     # 53.5mm
        "CheckBox6": (9, 8),       # 63mm
        "ck_08_53": (11, 10),      # 0.8mm×53.5mm
    }
    if n in cb_rules.get(cb_key, ()):
        return True

    # OB モード: OptionButton5/6 と lblSpec_08_53_1/2（obIdx 13/14）
    if ob_idx in (5, 6) and n in (11, 10):
        return True
    if ob_idx in (13, 14) and n in (11, 10):
        return True
    return False


def calc_hb(table: M.MaterialTable, coil_text: float, width_mm: float,
            tip: str, cb_key: str, ob_idx: int) -> HbData:
    """VBA ``DB_HB`` + ``CalculateHBData``。

    **高さは資材計算本体と同じ** ``get_coil_ta`` を使う（2026.09 で統一）。

    以前は ``GetHBCoilDimensions`` が独自に
    ``TA = (本数×巾 + TIPT) / 10^3`` と計算しており、``/10^3`` の位置が
    誤っていて括弧の中で mm と m を足していた。結果チップボール分
    （11本で 8.4mm）が実質消えていた。共通関数へ集約して解消（解析書 A-1）。

    胴巻きハードボードの高さだけは ``get_hb_band_height`` を通す。
    現状 ``HB_BAND_CLEARANCE_M = 0`` なので積高と同じ値になる。
    """
    hb = HbData()
    missing = hb.missing

    # --- 寸法（VBA GetHBCoilDimensions）---
    hb.GAI = get_coil_gai(tip)
    hb.col = width_mm
    # ① SetCoilDimensions と完全に同一の値。ただし HB.TA は Double
    #    （mat.TA は VBA の宣言が Single なので single() を通している）
    hb.TA = get_coil_ta(coil_text, hb.col)

    # --- マスタ（VBA GetHBMaterialData）---
    hb590 = _unit(table, M.MAT_HB590, missing)
    hb530 = _unit(table, M.MAT_HB530, missing)
    hb_calc = _unit(table, M.MAT_HB, missing)
    hb_oct = _unit(table, M.MAT_HB_OCT, missing)

    # --- フラグ ---
    hb.HBFlag = judge_hb_flag(coil_text, cb_key, ob_idx)

    # --- 重量 ---
    _calc_hb_value(hb, coil_text, hb590, hb530, hb_calc, hb_oct)
    return hb


def _calc_hb_value(hb: HbData, coil_text: float,
                   hb590, hb530, hb_calc, hb_oct) -> None:
    """VBA ``CalculateHBValue``。分岐構造を VBA のまま保持。"""
    oct_w = HB_OCT_COUNT * hb_oct[0] * hb_oct[1]

    if hb.HBFlag:
        if hb.col == 53.5 and coil_text == 11:
            hb.HBValue = 2 * hb590[0] * hb590[1] + oct_w
            hb.HBFormula = (
                "【ハードボード】HB590*2000 上下2枚 + 八角上下2枚\n"
                f"胴巻き: 2 × {fmt(hb590[0], '0.000')}"
                f" + 八角: 2 × {fmt(hb_oct[0], '0.000')}"
                f" = {fmt(hb.HBValue, '0.000')}")
            return
        if hb.col == 53.5 and coil_text == 10:
            hb.HBValue = 2 * hb530[0] * hb530[1] + oct_w
            hb.HBFormula = (
                "【ハードボード】HB530*2000 上下2枚 + 八角上下2枚\n"
                f"胴巻き: 2 × {fmt(hb530[0], '0.000')}"
                f" + 八角: 2 × {fmt(hb_oct[0], '0.000')}"
                f" = {fmt(hb.HBValue, '0.000')}")
            return

    # Flag=True のその他、および Flag=False は同一式（VBA CalcHBStandard）
    #   外周方向: 外周 + 700mm … 巻き重ね代
    #   高さ方向: get_hb_band_height（現状は積高のまま）
    # 八角 HB（上下2枚）はマスタ固定値なので、この補正の影響を受けない
    hb_height = get_hb_band_height(hb.TA)
    hb.HBValue = ((hb.GAI + HB_MARGIN_M) * hb_height
                  * hb_calc[0] * hb_calc[1] + oct_w)
    hb.HBFormula = (
        f"【ハードボード】八角上下2枚+(外周+{fmt(HB_MARGIN_M * 1000, '0')}mm)"
        "×コイル積高×HB単重\n"
        f"2 × {fmt(hb_oct[0], '0.000')}"
        f" + ({fmt(hb.GAI, '0.000')} + {HB_MARGIN_M}) × {fmt(hb_height, '0.000')}"
        f" × {fmt(hb_calc[0], '0.000')} = {fmt(hb.HBValue, '0.000')}")


# ============================================================ NW
def calc_nw(coil_text: float, coil_no: int, t1: float, t2: float,
            take1_visible: bool, take2_visible: bool) -> float:
    """VBA ``CalculateNW``。

    VBA は ``Format(..., "0.0")`` の **文字列** を Double 戻り値へ代入している。
    つまり **NW は小数第1位に丸められてから** HU/GW に加算される。ここも同じにする。
    """
    if coil_no in (1, 2):
        if take1_visible:
            return float(fmt(val(coil_text * t1), "0.0"))
        return 0.0
    if coil_no in (3, 4):
        if take2_visible:
            return float(fmt(val(coil_text * t2), "0.0"))
        return 0.0
    return 0.0


def parse_label_weight(label: str) -> float:
    """VBA ``GetCoilWeights``: ``CDbl(Replace(lblWeightN, "kg", ""))``。"""
    s = (label or "").replace("kg", "")
    if s.strip() == "":
        return 0.0
    try:
        return cdbl(s)
    except ValueError:
        return 0.0


# ============================================================ 集約
def calculate_materials(table: M.MaterialTable, coil_text: float, coil_no: int,
                        width_mm: float, tip: str, cb_key: str, ob_idx: int,
                        t1: float, t2: float,
                        take1_visible: bool, take2_visible: bool,
                        all_coil_empty: bool = False) -> MaterialWeights:
    """VBA ``CalculateMaterials``。1 梱包分の資材重量一式を返す。"""
    mat = MaterialWeights()
    missing = mat.missing

    kta = set_coil_dimensions(mat, coil_text, width_mm, all_coil_empty)
    set_diameter_info(mat, tip)

    mat.PIN = _calc_pin(table, mat, missing)
    mat.TIP = _calc_tip(table, mat, coil_text, tip, missing)
    mat.SUT = _calc_sut(table, mat, missing)
    mat.ESA = _calc_esa(table, mat, missing)
    mat.POR = _calc_por(table, mat, tip, missing)

    hb = calc_hb(table, coil_text, width_mm, tip, cb_key, ob_idx)
    mat.HB = hb.HBValue
    mat.formulas["HBsiki"] = hb.HBFormula
    for name in hb.missing:
        if name not in missing:
            missing.append(name)

    mat.PET = _calc_pet(table, mat, tip, missing)
    mat.PAR = _calc_par(table, mat, missing)
    mat.DRY = _calc_dry(table, mat, missing)

    mat.NW = calc_nw(coil_text, coil_no, t1, t2, take1_visible, take2_visible)

    # 風袋 = 9 項目（羅列計算の 8 項目とは定義が異なる。解析書 8.8 参照）
    mat.HU = (mat.PIN + mat.TIP + mat.SUT + mat.ESA + mat.POR
              + mat.HB + mat.PET + mat.PAR + mat.DRY)
    mat.GW = mat.HU + mat.NW

    # --- 表記用の NW / 風袋 / GW 式（VBA SetDisplayFormValues）---
    t = t1 if coil_no in (1, 2) else t2
    mat.formulas["NWsiki"] = (
        f"{fmt(t, '0.0')} × {fmt(coil_text, '0')} = {fmt(mat.NW, '0.0')}")
    mat.formulas["HUsiki"] = "+".join([
        fmt(mat.PIN, "0.000"), fmt(mat.TIP, "0.000"), fmt(mat.SUT, "0.000"),
        fmt(mat.ESA, "0.000"), fmt(mat.POR, "0.000"), fmt(mat.HB, "0.000"),
        fmt(mat.PAR, "0.0"), fmt(mat.PET, "0.000"), fmt(mat.DRY, "0.000"),
    ])
    mat.formulas["GWsiki"] = f"{fmt(mat.HU, '0.000')}+{fmt(mat.NW, '0.0')}"
    mat.formulas["_KTA"] = "" if kta is None else fmt(ws_roundup(kta, -1), "0.0")
    mat.formulas["_HBFlag"] = "True" if hb.HBFlag else "False"
    mat.formulas["_HB_TA"] = fmt(hb.TA, "0.000")
    # 胴巻きハードボードの高さ（HB_BAND_CLEARANCE_M が 0 の間は積高と同じ）。
    # 画面に差を出すため、丸める前の値を残す。
    mat.HB_TA = get_hb_band_height(hb.TA)
    return mat


def display_values(mat: MaterialWeights) -> Dict[str, str]:
    """VBA ``SetDisplayFormValues`` / ``SetCoilFormValues`` の表示文字列。"""
    return {
        "PI": fmt(mat.PIN, "0.000"),
        "TIP": fmt(mat.TIP, "0.00"),
        "SUT": fmt(mat.SUT, "0.000"),
        "ESA": fmt(mat.ESA, "0.000"),
        "POR": fmt(mat.POR, "0.000"),
        "PAR": fmt(mat.PAR, "0.0"),
        "PET": fmt(mat.PET, "0.000"),
        "HB": fmt(mat.HB, "0.000"),
        "NW": fmt(mat.NW, "0.0"),
        "DRY": fmt(mat.DRY, "0.000"),
        "HU": fmt(ws_round(mat.HU, 0), "0.0"),
        "GW": fmt(ws_round(mat.GW, 0), "0.0"),
    }


def form_values(mat: MaterialWeights) -> Tuple[str, str]:
    """VBA ``SetCoilFormValues``: (NW{n}, GW{n})。"""
    return fmt(mat.NW, "0.0"), fmt(ws_round(mat.GW, 0), "0.0")


def sheet_formulas(mat: MaterialWeights, coil_text: float, coil_no: int,
                   t1: float, t2: float) -> Dict[str, str]:
    """VBA ``WriteSheetFormulas``（風袋計算の計算式の列）。

    ハードボード行の計算式も他の資材と同じように常に返す。

    VBA では ``WriteSheetFormulas`` が HB 行（i+6）だけ書かず、
    ``WriteHBToSheet`` が **シートのセルへ直接書いて**いた。
    その条件が ``If テスラ指定サイズ.CoilH1 <> "" Then`` だったため、
    丈1の1梱包目が空だと計算式の欄だけ空になる、という副作用があった。

    移植版は画面をデータから描くので **セルへ書く処理そのものが無い**。
    計算式は ``mat.formulas["HBsiki"]`` に必ず入っているため、
    条件分岐を持つ理由が無い（2026.09）。
    """
    # マスタ由来の値は **文字列のまま** 使う。
    # VBA は Variant をそのまま CStr するので、係数 "1" は "1" と出る
    #（数値化してから文字列にすると "1.0" になる）。
    u = mat.units_raw
    pin = u.get("PIN", ("0", "0"))
    tip = u.get("TIP", ("0", "0"))
    sut = u.get("SUT", ("0", "0"))
    esa = u.get("ESA", ("0", "0"))
    por = u.get("POR", ("0", "0"))
    par = u.get("PAR", ("0", "0"))
    pet = u.get("PET", ("0", "0"))
    dry = u.get("DRY", ("0", "0"))
    d = mat.TempDiameter
    h = mat.HalfDiameter
    # 計算値は VBA の CStr と同じ桁で文字列化する
    #（mat.TA は Single、mat.GAI は Double）
    ta = cstr_single(mat.TA)
    gai = cstr(mat.GAI)

    two = mat.TA > ESA_DOUBLE_LIMIT_M
    esa_tag = "[2周計算]" if two else "[1周計算]"
    esa_mul = "*2" if two else ""

    t = t1 if coil_no in (1, 2) else t2

    return {
        "PIN": f"4*{pin[0]}*{pin[1]}",
        "TIP": f"({cstr(coil_text)}+{CHIP_EXTRA_SHEETS})*{tip[0]}*{tip[1]}",
        "SUT": (f"{d}*{gai}/ 10 ^ 3)*{STRETCH_WRAPS}*{sut[0]}*{sut[1]}"
                f"[約{STRETCH_WRAPS}周計算]"),
        "ESA": f"{d}*{gai}/ 10 ^ 3){esa_mul}*{esa[0]}*{esa[1]}{esa_tag}",
        "POR": (f"[({h}^ 2 * PIN*2)]+[(({ta}+{ta}/ 2)*{d}*{gai}/ 10 ^ 3))"
                f"/ 10 ^ 6]*{por[0]}*{por[1]}  包み分は高さの半分とする"),
        "HB": mat.formulas.get("HBsiki", ""),
        "PAR": f"{par[0]}*{PALLET_QTY}",
        "PET": (f"[({d}*{gai}/ 10 ^ 3+{BAND_SLACK_M})*4)]"
                f"+[(({ta}+{BAND_SLACK_M})*4)*2)"
                f"+(({d}*4)*2) / 10 ^ 3]*{pet[0]}*{pet[1]}"
                f"  締める時の余分を{fmt(BAND_SLACK_M * 1000, '0')}mmとした"),
        "DRY": f"{dry[0]}*1",
        "NW": f"{cstr(t)}*{cstr(coil_text)}",
        "HU": mat.formulas.get("HUsiki", ""),
        "GW": mat.formulas.get("GWsiki", ""),
    }
