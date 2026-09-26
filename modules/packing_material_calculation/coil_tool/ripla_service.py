"""リプラ(再生プラスチック製スペーサー)の長さと本数 (VBA `リプラ計算`)

コイルはパレットの上に段積みされ、段と段のあいだと最下部にリプラを敷く。
**最下部とコイル間で長さが違う**のが要点で、パレットの種類によって
どれだけ短くするかが決まっている。

    最下部    … パレットサイズいっぱい、または在庫にある定尺のうち最大
    コイル間  … そこから 100mm(EXPS だけ 350mm)短いもの

本数は積数と台数から出す。**検入数が台数で割り切れないとき**は、最後の
1台が端数になるので、その台だけ別に数える。

【`台数計算` の後に呼ぶこと】
積数・台数・パレットサイズが埋まっている前提。VBA も
「台数計算の後に実行」とコメントしている。
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from . import db, special_rules, vba
from .logging_utils import get_logger
from .models import CalcState, FLAG_SPACER_DIFFERS, PackagingSpec
from .pallet_service import get_spec

log = get_logger("ripla_service")

# コイル間のリプラを最下部より何 mm 短くするか。パレット種類ごと
SHORTEN_DEFAULT = 100.0
SHORTEN_EXPS = 350.0

# 最下部の長さにパレットサイズをそのまま使う種類。
# それ以外は「在庫にある定尺のうちパレットサイズ以下で最大のもの」
TYPES_USE_PALLET_SIZE = ("全面", "EXPS")

# EXPS だけ最下部の本数が特別。長いのが1本、短いのが2本
EXPS = "EXPS"
EXPS_LONG_COUNT = 1
EXPS_SHORT_COUNT = 2

# コイル間の長さがこれ以上なら、定尺から選び直す。
# 端数の長さは倉庫でのカットが要るので、在庫にある長さに寄せる
RESELECT_THRESHOLD = 800.0

# リプラの角サイズ。80 は長さを持たない固定品
RIPLA_SIZE_FIXED = 80.0

# コイル間に入れるもの。これ以外(ザラ板など)はリプラを数えない
SPACER_RIPLA = "リプラ"
SPACER_LVS = "LVS"

# 協豊製作所向け。コイル間に間紙を入れるのでコイル間のリプラは要らない
INTERLEAF_MARK = "〇"
INTERLEAF_CUSHION = "外装紙"

# 角サイズの表示。帳票にはこの形で出る
RIPLA_SIZE_LABELS = {30: "30×40", 40: "40×60", 80: "80×80"}


@dataclass
class ShapeView:
    """最下部リプラの並びの図 (VBA `図形表示`)。

    EXPS と全面のときだけ図が出る。最下部本数が 3 / 2 / 0 で
    見せ方が変わり、2 のときは真ん中が抜ける。
    """
    kind: str = ""          # "EXPS" / "全面" / "" (図なし)
    bars: tuple[bool, bool, bool] = (False, False, False)


def shape_view(pallet_type: str, bottom_count: int) -> ShapeView:
    """VBA `図形表示` の移植。"""
    if pallet_type not in (EXPS, "全面"):
        return ShapeView()
    if bottom_count == 3:
        return ShapeView(kind=pallet_type, bars=(True, True, True))
    if bottom_count == 2:
        # 真ん中だけ抜ける
        return ShapeView(kind=pallet_type, bars=(True, False, True))
    if bottom_count == 0:
        return ShapeView(kind=pallet_type, bars=(False, False, False))
    # VBA は 3 / 2 / 0 以外を扱っていない。図は出すが棒は出さない
    return ShapeView(kind=pallet_type, bars=(False, False, False))


def ripla_size_label(size: str) -> str:
    """角サイズを帳票の表記にする。30 → "30×40" など。"""
    return RIPLA_SIZE_LABELS.get(int(vba.val(size)), "")


# ==================================================================
# 定尺の選定
# ==================================================================
def _stock_lengths(conn: sqlite3.Connection) -> list[float]:
    rows = db.fetch_all(conn, "SELECT リプラ長さ FROM リプラサイズ",
                        caller_name="stock_lengths") or []
    return [vba.val(r["リプラ長さ"]) for r in rows]


def _largest_within(lengths: list[float], limit: float) -> float:
    """`limit` 以下で最大の定尺。1つも無ければ 0。

    VBA は該当ぶんの配列を作って `WorksheetFunction.Max` を取っていた。
    該当 0 件のときに 0 が返るところまで同じ(配列が未代入のまま
    Max に渡されるため)。
    """
    fitting = [n for n in lengths if n <= limit]
    return max(fitting) if fitting else 0.0


# ==================================================================
# 本体
# ==================================================================
def calculate(conn: sqlite3.Connection, state: CalcState) -> int:
    """リプラの長さ・本数・HB枚数を `state` へ書き、総本数を返す。

    戻り値は VBA `リプラ計算` の戻り値(フォームの `TotalC`)。

    **途中で抜けても `TotalC` は 0 になる。** VBA は計算Start でも
    積数の手直しでも `UF_Material.TotalC = リプラ計算(...)` と戻り値を
    そのまま書くので、`Exit Function` で抜けると関数の既定値 0 が画面に
    出る(コイル間がチップ・外装紙・クラフトのとき など)。以前は空欄に
    していて、VBA の画面と食い違っていた(9/23 の照合で分かった)。
    """
    total = _calculate(conn, state)
    state.TotalC = vba.int_text(total)
    return total


def _calculate(conn: sqlite3.Connection, state: CalcState) -> int:
    """`リプラ計算` の本体。`TotalC` は呼ぶ側が戻り値から書く。"""
    spec_no = state.order.包装仕様NO
    if not spec_no:
        return 0
    spec = get_spec(conn, spec_no)
    if spec is None:
        return 0

    # ---- マスタの値をそのまま画面へ ----
    state.コイル間 = spec.コイル間スペーサー
    state.リプラサイズ = spec.リプラサイズ
    state.最下部本数 = spec.下本数
    state.緩衝材 = spec.緩衝材
    state.間リプラ種類 = spec.コイル間スペーサー
    state.最下部リプラ種類 = spec.最下部スペーサー

    # 種類が違うときだけ赤くする(2026.02.25 追加)。
    # 取り違えると現場で別のものを敷くことになる
    if spec.コイル間スペーサー != spec.最下部スペーサー:
        state.mark(FLAG_SPACER_DIFFERS)

    # ---- コイル間がリプラ / LVS でなければ、緩衝材だけ数えて終わり ----
    if spec.コイル間スペーサー not in (SPACER_RIPLA, SPACER_LVS):
        state.HB枚数 = vba.int_text(state.台数値 * 2)
        return 0

    # ---- 1C1188 ｼﾏﾉ。角サイズがサイズで変わる ----
    atu = vba.val(state.order.受注板厚)
    haba = vba.val(state.order.受注板幅)
    state.リプラサイズ = special_rules.ripla_size_override(
        spec_no, atu, haba, state.リプラサイズ)

    # 積数が決まっていないと本数が出せない
    if not str(state.積数).strip():
        return 0

    # ---- 角サイズ 80 は長さを持たない固定品 ----
    if vba.val(spec.リプラサイズ) == RIPLA_SIZE_FIXED:
        state.HB枚数 = vba.int_text(state.台数値)
        return int(state.積数値 * 2 * state.台数値)

    # ---- 長さを決める ----
    lengths = _stock_lengths(conn)
    pallet_size = vba.val(state.パレットサイズ)
    max_val = _largest_within(lengths, pallet_size)
    if max_val == 0.0 and not lengths:
        # 定尺が1つも無い。VBA もここで抜ける
        return 0

    pallet_type = state.パレット種類
    # 最下部の長さ。全面と EXPS だけパレットサイズをそのまま使う
    bottom_length = pallet_size if pallet_type in TYPES_USE_PALLET_SIZE else max_val
    # コイル間の長さ。EXPS だけ 350mm 短い
    shorten = SHORTEN_EXPS if pallet_type == EXPS else SHORTEN_DEFAULT
    coil_length = bottom_length - shorten

    # 800mm 以上なら定尺から選び直す(倉庫でのカットを避ける)
    if coil_length >= RESELECT_THRESHOLD:
        coil_length = _largest_within(lengths, coil_length)

    # 最下部の本数。EXPS だけ「長いの1本 + 短いの2本」
    if pallet_type == EXPS:
        bottom_long_each = float(EXPS_LONG_COUNT)
        bottom_short_each = float(EXPS_SHORT_COUNT)
    else:
        bottom_long_each = vba.val(state.最下部本数)
        bottom_short_each = 0.0

    # ---- 本数を数える ----
    total_units = state.台数値 + state.Re_台数値
    per_layer = (state.積数値 - 1) * 2        # 最下部を除いたコイル間リプラ
    long_count = total_units * bottom_long_each
    short_count = total_units * bottom_short_each

    coil_count = _coil_spacer_count(state, per_layer)

    # 別枠(1本積み不可で分けたぶん)があれば足す
    if str(state.Re_積数).strip():
        re_per_layer = (vba.val(state.Re_積数) - 1) * 2
        coil_count += re_per_layer * state.Re_台数値

    state.最下部長さ = vba.int_text(bottom_length)
    state.最下部長さ_短 = vba.int_text(coil_length)
    state.長い本数 = vba.int_text(long_count)
    state.短い本数 = vba.int_text(short_count)
    state.リプラ長さ = vba.int_text(coil_length)
    state.本数 = vba.int_text(coil_count)

    # ---- 協豊製作所向け。コイル間は間紙なのでリプラを出さない ----
    if spec.コイル間は間紙入 == INTERLEAF_MARK:
        state.最下部長さ_短 = ""
        state.リプラ長さ = ""
        state.本数 = ""
        state.緩衝材 = INTERLEAF_CUSHION

    # ---- 1C1282 ｱｲｴﾑｱｲｶﾊﾞｰ。リプラを3種類使う ----
    if spec_no == special_rules.IMI_COVER_SPEC_NO:
        state.特殊Flag = special_rules.IMI_COVER_FLAG
        state.リプラ長さ = vba.int_text(special_rules.IMI_COIL_LENGTH)
        state.最下部長さ_短 = vba.int_text(special_rules.IMI_BOTTOM_SHORT)
        state.IMI中間長さ = vba.int_text(special_rules.IMI_MIDDLE_LENGTH)
        state.IMI中間本数 = vba.int_text(total_units * 2)
        state.短い本数 = vba.int_text(total_units * 2)

    # ---- 1C1297 ｺﾄﾌﾞｷｾｲﾐﾂ。帳票を2行に分ける ----
    if spec_no == special_rules.KOTOBUKI_SPEC_NO:
        state.特殊Flag = special_rules.KOTOBUKI_FLAG

    # ---- 緩衝材(HB)の枚数 ----
    # VBA には `IsNumeric(RiplaC) = False` で2倍にする枝があるが、
    # `RiplaC` は `Long` で宣言されていて常に数値なので**通らない**。
    # 移植でも通らないままにしてある(通していた前提で足すと倍になる)
    state.HB枚数 = vba.int_text(total_units)

    # ---- 総本数 ----
    # 【間紙入(協豊製作所)は、VBA ではここへ来ない】(不明点 Q23・回答済み)
    # VBA は `.本数 = ""` の直後に `中間本数 = .本数`(Long)と書いていて、
    # **「型が一致しません」で必ず止まる**(2026-09-23 に疑似ロットで確認)。
    # そのため HB枚数の数え直しとトータル本数は VBA では一度も動いていない。
    # 現場の判断で「**移植は止めずに、書いてあるとおり続きを計算する**」と
    # 決まった(HB枚数 = 台数、トータル = 下本数 × 台数)。
    # 空の本数を 0 として読むのはそのため(`val` を通す)
    middle = vba.val(state.本数)
    bottom_long = vba.val(state.長い本数)
    bottom_short = vba.val(state.短い本数)

    if spec_no == special_rules.IMI_COVER_SPEC_NO:
        total = middle + bottom_long + bottom_short + vba.val(state.IMI中間本数)
    else:
        total = middle + bottom_long + bottom_short

    if spec.コイル間は間紙入 == INTERLEAF_MARK:
        # 最下部だけ。コイル間は間紙なので数えない
        total = vba.val(spec.下本数) * state.台数値

    return int(total)


def _coil_spacer_count(state: CalcState, per_layer: float) -> float:
    """コイル間に入るリプラの本数。

    **検入数が台数で割り切れるか**で変わる。割り切れないと最後の1台が
    端数になり、その台だけ積んだコイルの本数が少ない。

        割り切れる   … 1台ぶん × 台数
        割り切れない … 満載の台ぶん + 端数の台ぶん(コイル本数 × 2)

    別枠(`Re_積数`)があるときは、そちらで端数を吸収済みなので
    割り切れる側として扱う。
    """
    units = state.台数値
    if units <= 0:
        return 0.0

    quotient = state.検入数値 / units
    divisible = math.floor(quotient) == quotient

    if divisible or str(state.Re_積数).strip():
        return per_layer * units

    # 端数の1台を分けて数える
    full_units = units - 1
    remainder_coils = state.検入数値 - (full_units * state.積数値) - 1
    return (per_layer * full_units) + (remainder_coils * 2)
