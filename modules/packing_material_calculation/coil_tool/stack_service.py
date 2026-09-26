"""積数と台数 (VBA `台数計算`)

    積数 … 1台(1パレット)に積むコイルの本数
    台数 … 検入数をその積数で割った、必要なパレットの数

決め方は**優先順位つきの5段**で、上から見て最初に当たったものを採る。
オーダー情報(受注そのものの指定)が包装仕様マスタより強い。

    1. オーダーの梱包単位_重量     → 重量 ÷ 単重 を切り捨て
    2. オーダーの梱包単位_枚数     → その枚数
    3. 包装仕様の梱包単位_重量     → 重量範囲を見て上限を決め、切り捨て
    4. 包装仕様の梱包単位_枚数     → 枚数範囲が「以下 / max」ならその枚数
    5. どれも無く 単重 > 500kg     → 1本積み

そのあと **包装仕様No別の規則(`special_rules`)で積数を上書き**し、
最後に「1本積み不可」と「高さ制限」で調整する。

【順番を変えないこと】
`PalletSize` が先に済んでいる前提で、高さの上限にパレットサイズを使う
場面がある(`限度高`)。VBA も `CommandButton5_Click` で
パレット種類 → パレット名称 → 台数 → リプラ の順に呼んでいる。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from . import special_rules, vba
from .logging_utils import get_logger
from .models import (CalcState, FLAG_COUNT, FLAG_HEIGHT, FLAG_NO_SINGLE,
                     FLAG_SINGLE_SPLIT, FLAG_SPEC, FLAG_WEIGHT, OrderInfo,
                     PackagingSpec)
from .pallet_service import get_spec

log = get_logger("stack_service")

# パレットの上下で見込む高さ(mm)。VBA が `- 210#` と直に書いていた値
PALLET_TOP_AND_BOTTOM = 210.0
# 「蓋除き」のときは上の分だけ
PALLET_TOP_ONLY = 80.0
# リプラサイズが「可変」のときに使う厚み(mm)
VARIABLE_RIPLA = 30.0
# データが足りないときに1本積みへ倒す単重の境目(kg)。2022.10.14 追加
ONE_COIL_WEIGHT = 500.0

# 重量範囲「程度」の上振れ(%)
TOLERANCE_PERCENT = 10.0

# 枚数範囲の値
COUNT_RANGE_NO_SINGLE = "1不可"
COUNT_RANGE_OK = ("以下", "max")
# 高さ範囲の値
HEIGHT_RANGE_MINUS_PALLET = ("以下", "程度", "max")
HEIGHT_RANGE_NO_LID = "蓋除き"
HEIGHT_RANGE_EXCL_PALLET = "パレット除き"

VARIABLE = "可変"


@dataclass
class StackResult:
    """`計算` の結果。台数そのものと、途中の判断を持ち帰る。"""
    台数: int = 0
    ok: bool = False
    # 積数が 0 になった等で計算を続けられなかったとき
    reason: str = ""


def _has_value(text: str) -> bool:
    """VBA の `<> "" And IsNumeric(...)` をまとめたもの。"""
    return bool(str(text).strip()) and vba.is_numeric(text)


# ==================================================================
# 5段の優先順位
# ==================================================================
def _decide_stack(state: CalcState, spec: PackagingSpec) -> tuple[float, bool, bool]:
    """積数を決める。戻り値は `(積数, 1本積み不可か, 重量で決めたか)`。

    `重量で決めたか`(VBA `Orderonly`)は、あとの高さ判定で
    「重量だけの制限だと積みすぎる」補正を効かせるかどうかに使う。
    """
    order = state.order
    weight = vba.val(order.製品単重)

    # オーダー情報に値があるか。**`"データなし"` は数値でないので False**
    w_flag = vba.is_numeric(order.梱包単位_重量)
    c_flag = vba.is_numeric(order.梱包単位_枚数)

    stack = 0.0
    impossible = False
    order_only = False

    if w_flag:
        # 1. オーダーの重量指定
        gross = vba.val(order.梱包単位_重量)
        stack = float(vba.round_down(gross / weight)) if weight else 0.0
        state.mark(FLAG_WEIGHT)
        order_only = True

    elif c_flag:
        # 2. オーダーの枚数指定
        stack = vba.val(order.梱包単位_枚数)
        state.mark(FLAG_COUNT)
        if spec.枚数範囲 == COUNT_RANGE_NO_SINGLE:
            # ﾌﾟﾛｽﾁ-ﾙ_LVS。1本だけ積むことが許されない
            impossible = True
            state.mark(FLAG_NO_SINGLE)

    elif _has_value(spec.梱包単位_重量):
        # 3. 包装仕様の重量指定。範囲の言い回しで上限が変わる
        base = vba.val(spec.梱包単位_重量)
        gross = 0.0
        if spec.重量範囲 == "程度":
            gross = base + base * TOLERANCE_PERCENT / 100.0
        elif spec.重量範囲 in ("以下", "max"):
            gross = base
        order_only = True
        stack = float(vba.round_down(gross / weight)) if weight else 0.0
        state.mark(FLAG_WEIGHT)

    elif _has_value(spec.梱包単位_枚数):
        # 4. 包装仕様の枚数指定
        if spec.枚数範囲 in COUNT_RANGE_OK:
            stack = vba.val(spec.梱包単位_枚数)
            state.mark(FLAG_COUNT)
        if spec.枚数範囲 == COUNT_RANGE_NO_SINGLE:
            impossible = True
            state.mark(FLAG_NO_SINGLE)
            # ここへ来る時点でオーダーの枚数指定は無い(`c_flag` は False)
            # ので、包装仕様の枚数を採る ── 吉川工業 1C1252
            stack = vba.val(spec.梱包単位_枚数)

    else:
        # 5. どれも無い。重いものは1本積みにしておく (2022.10.14)
        if weight > ONE_COIL_WEIGHT:
            stack = 1.0

    return stack, impossible, order_only


# ==================================================================
# 1本積み不可の端数処理
# ==================================================================
def _split_for_no_single(state: CalcState, stack: float, ins: float) -> tuple[int, float, int]:
    """最後の1台が1本積みになるときの振り分け。

    戻り値は `(台数, 積数, 偶数丸めの仮台数 x)`。

    VBA の `x` は**銀行丸め**(`Round(Ins / StackC, 0)`)。四捨五入に
    変えると端数の出方が変わって台数が動くので、そのまま使う。
    """
    x = vba.round_half_even(ins / stack)

    if not (ins - (x * stack) == 1 and FLAG_NO_SINGLE in state.flags):
        # ふつうの場合。切り上げて台数を出す
        return vba.round_up(ins / stack), stack, x

    if x == 1:
        # もともと2台ぶんしかない状況で1本積みが出るとき (2026.1.16)
        # **2台へ均等に分ける。** 別枠(Re_)は使わない ── ここに値を
        # 入れると管理表の台数が二重に増える(VBA のコメント)
        state.積数 = vba.int_text(ins / 2)
        state.mark(FLAG_SINGLE_SPLIT)
        state.mark(FLAG_SPEC)
        return 2, ins / 2, x

    # 仮の台数から1台戻して、その1台ぶんを2台に割る
    re_x = x - 1
    re_stack = (ins - (re_x * stack)) / 2
    state.Re_積数 = vba.int_text(re_stack)
    state.Re_台数 = "2"
    state.積数 = vba.int_text(stack)
    state.mark(FLAG_SINGLE_SPLIT)
    state.mark(FLAG_SPEC)
    return re_x, stack, x


# ==================================================================
# 高さ制限
# ==================================================================
def _ripla_thickness(spec: PackagingSpec) -> float:
    """高さ計算に使うリプラの厚み。「可変」は 30mm として扱う。"""
    if spec.リプラサイズ == VARIABLE:
        return VARIABLE_RIPLA
    return vba.val(spec.リプラサイズ)


def _apply_height(state: CalcState, spec: PackagingSpec, *,
                  stack: float, count: int, ins: float, haba: float,
                  order_only: bool) -> tuple[float, int, bool]:
    """高さ制限で積数と台数を取り直す。戻り値は `(積数, 台数, 積数を確定したか)`。

    2通りの入り方がある。

        梱包総高さの指定がある … 指定高さからパレット分を引いて上限にする
        指定は無いが重量で決めた … パレット幅から1本分引いた高さを上限に
                                  する (2022.07.25)。重量だけの制限だと
                                  積み上がりすぎることがあるため

    どちらも当たらなければ、積数も台数もそのまま。

    3つ目の戻り値が `True` なら、**この中で `state.積数` を入れ終えている**。
    VBA の重量だけの枝は `Exit Function` で抜けていて、最後の
    `If Not x = 1 Then 積数 = StackC` を通らない ── 積数 20・検入数 20 の
    ように `x = 1` になる組み合わせで、通す/通さないが結果を分ける。
    """
    if _has_value(spec.梱包総高さ):
        stack, count = _height_from_spec(state, spec, stack=stack, count=count,
                                         ins=ins, haba=haba)
        return stack, count, False
    if order_only:
        return _height_from_pallet(state, spec, stack=stack, count=count,
                                   ins=ins, haba=haba)
    return stack, count, False


def _recalc_by_height(state: CalcState, *, limit: float, ripla: float,
                      haba: float, ins: float) -> tuple[float, int]:
    """上限高さに収まる積数へ取り直し、台数も取り直す。"""
    per_coil = ripla + haba
    if per_coil <= 0:
        return 0.0, 0
    stack = float(vba.round_down(limit / per_coil))
    state.mark(FLAG_HEIGHT)
    state.総高さ = vba.int_text(stack * per_coil + PALLET_TOP_AND_BOTTOM)
    if stack <= 0:
        return 0.0, 0
    return stack, vba.round_up(ins / stack)


def _height_from_spec(state: CalcState, spec: PackagingSpec, *,
                      stack: float, count: int, ins: float,
                      haba: float) -> tuple[float, int]:
    """梱包総高さの指定から積数を見直す。

    **重量で決めたときだけ**高さ超過を見る(`FLAG_WEIGHT` が立っていると
    き)。枚数で決まっているなら取引先の指定なので、高さでは動かさない。

    【`GoTo LineSkip` の順番をそのまま残している】
    VBA はリプラの厚みを**2度**入れる。高さ範囲の枝で
    `Ripu = val(リプラサイズ)` を入れ、そのあと「可変なら 30」で
    入れ直す。高さ超過で先に飛ぶと**前者のまま**飛ぶので、
    リプラサイズが「可変」の品では `val("可変") = 0` が使われる。

    まとめて「可変は 30」にしてしまうと、この経路の積数が変わる。
    直すなら業務に確認してから(`docs/不明点.md`)。
    """
    total = vba.val(spec.梱包総高さ)
    ripla = vba.val(spec.リプラサイズ)   # 「可変」なら 0
    pallet_h = 0.0

    def jump(current_ripla: float) -> tuple[float, int]:
        """VBA `LineSkip:`"""
        limit = pallet_h
        if spec.高さ範囲 == HEIGHT_RANGE_EXCL_PALLET:
            # パレット分を引かず、指定高さをそのまま使う
            limit = total
        return _recalc_by_height(state, limit=limit, ripla=current_ripla,
                                 haba=haba, ins=ins)

    if spec.高さ範囲 in HEIGHT_RANGE_MINUS_PALLET:
        pallet_h = total - PALLET_TOP_AND_BOTTOM
        if FLAG_WEIGHT in state.flags and (stack * ripla) + (stack * haba) > pallet_h:
            return jump(ripla)

    if spec.高さ範囲 == HEIGHT_RANGE_NO_LID:
        pallet_h = total - PALLET_TOP_ONLY
        if FLAG_WEIGHT in state.flags and (stack * ripla) + (stack * haba) > pallet_h:
            return jump(ripla)

    if spec.リプラサイズ:
        if spec.リプラサイズ == VARIABLE:
            return jump(VARIABLE_RIPLA)
        ripla = vba.val(spec.リプラサイズ)

    # どの枝も飛ばなかった。積数も台数もそのまま
    return stack, count


def _height_from_pallet(state: CalcState, spec: PackagingSpec, *,
                        stack: float, count: int, ins: float,
                        haba: float) -> tuple[float, int, bool]:
    """高さの指定が無く、重量だけで決めたときの上限 (2022.07.25)。

    パレットの幅から1本分を引いた高さを上限にする。
    「550 が目安？だが積み本数がかなり減る」と VBA のコメントにある。

    **ここで積数を入れ終える**(VBA はこの枝の最後で `Exit Function`)。
    """
    ripla = _ripla_thickness(spec)
    per_coil = ripla + haba
    limit = vba.val(state.パレットサイズ) - per_coil

    if per_coil * stack > limit:
        stack, count = _recalc_by_height(state, limit=limit, ripla=ripla,
                                         haba=haba, ins=ins)
    state.積数 = vba.int_text(stack) if stack > 0 else ""
    return stack, count, True


# ==================================================================
# 本体
# ==================================================================
def calculate(conn: sqlite3.Connection, state: CalcState) -> StackResult:
    """積数と台数を決めて `state` へ書く (VBA `台数計算`)。"""
    # VBA 冒頭の色クリア。ここで消さないと前回の赤が残る
    for flag in (FLAG_WEIGHT, FLAG_COUNT, FLAG_HEIGHT, FLAG_SPEC,
                 FLAG_NO_SINGLE, FLAG_SINGLE_SPLIT):
        state.flags.discard(flag)

    order = state.order
    if not order.包装仕様NO:
        return StackResult(ok=False, reason="no_spec_no")

    if not str(state.検入数).strip():
        state.note("検入数を入力してください")
        return StackResult(ok=False, reason="no_inspection_count")

    spec = get_spec(conn, order.包装仕様NO)
    if spec is None:
        state.note(f"包装仕様No {order.包装仕様NO} がマスタに登録されていません")
        return StackResult(ok=False, reason="spec_not_found")

    # 引当データがまったく無いと、どの段にも当たらない
    if (order.梱包単位_重量 == OrderInfo.NO_DATA
            and order.梱包単位_枚数 == OrderInfo.NO_DATA):
        state.note("引当データなし。計算ができません")
        return StackResult(ok=False, reason="no_allocation_data")

    ins = state.検入数値
    atu = vba.val(order.受注板厚)
    haba = vba.val(order.受注板幅)

    # ---- 5段の優先順位 ----
    stack, impossible, order_only = _decide_stack(state, spec)
    if impossible:
        state.mark(FLAG_NO_SINGLE)

    # ---- 包装仕様No別の上書き ----
    stack, pack_flag = special_rules.apply_stack_rules(state, stack, atu, haba)

    if stack <= 0:
        # VBA はここでゼロ除算の実行時エラーになる。止めずに理由を返す
        # (`docs/不明点.md` Q6。正しい扱いが決まったらここを直す)
        state.note("積数が 0 になりました。製品単重と梱包単位の指定を確認してください")
        state.積数 = ""
        state.台数 = ""
        return StackResult(ok=False, reason="zero_stack")

    # ---- 1本積み不可の端数処理 ----
    count, stack_for_split, x = _split_for_no_single(state, stack, ins)

    # ---- 高さ制限 ----
    c_flag = vba.is_numeric(order.梱包単位_枚数)
    settled = False
    if pack_flag or c_flag:
        # 包装仕様No別の規則が効いた、または枚数の指定がある。
        # **高さ判定は丸ごと飛ばす** ── どちらも「この品はこう積む」と
        # 決まっているもので、高さで動かしてはいけない
        special_rules.apply_height_display_rules(state, stack, haba, spec.リプラサイズ)
    else:
        stack, count, settled = _apply_height(
            state, spec, stack=stack, count=count, ins=ins,
            haba=haba, order_only=order_only)
        # 【積数が0以下なら断る】(不明点 Q24・回答済み)
        # VBA はここで止まらず、**負の積数・台数をそのまま画面に出す**。
        # ﾎｲｰﾙで収まるパレットが無いとき(マスタの H1 は巾の上下限が空欄)、
        # パレット無しで高さを割って 積数-1・台数-5・HB枚数-10 になった
        # (2026-09-23 に疑似ロットで確認)。負の台数がチェックリストへ
        # 積めてしまうほうが危ないので、現場の判断で「**移植は断る**」と決まった
        if stack <= 0:
            state.note("高さの制限に収まる積数がありません")
            state.積数 = ""
            state.台数 = ""
            return StackResult(ok=False, reason="zero_stack_by_height")

    # VBA `PacSkip:` ── `x = 1` のときは、上で入れた均等分配の積数を残す。
    # `settled` の枝は VBA が `Exit Function` で抜けていて、ここを通らない
    if not settled and x != 1:
        state.積数 = vba.int_text(stack)
    state.台数 = vba.int_text(count)
    return StackResult(台数=count, ok=True)
