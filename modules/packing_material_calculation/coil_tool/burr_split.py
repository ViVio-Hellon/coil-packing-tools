"""バリ揃え梱包 (VBA `UFalmo`)

コイルの切り口のバリが上向きの本数と下向きの本数を分けて梱包する品が
ある。**上下を混ぜて積めない**ので、それぞれ別の台に積む。

    上バリ n 本 → 上台数 = 切り上げ(n ÷ 積数)
    下バリ m 本 → 下台数 = 切り上げ(m ÷ 積数)
    台数 = 上台数 + 下台数

画面では検入数から上下へ振り分ける(スピンで1本ずつ移す)ので、
上下の合計と残りの検入数を足すと元の検入数になる。

【本体の `台数計算` と丸めが違う】
オーダーの重量指定から積数を出すとき、本体は**切り捨て**、こちらは
**切り上げ**になっている。こちらのほうが1本多く積む向き。
意図的な差かどうか分からないので、**VBA のまま**にしてある
(`docs/不明点.md` Q5)。揃えるなら両方を1か所から呼ぶ形へ直す。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from . import vba
from .logging_utils import get_logger
from .models import CalcState, OrderInfo, PackagingSpec
from .pallet_service import get_spec
from .stack_service import (COUNT_RANGE_OK, ONE_COIL_WEIGHT, TOLERANCE_PERCENT,
                            _has_value)

log = get_logger("burr_split")


@dataclass
class BurrSplit:
    """上下の振り分けと、その結果の台数。"""
    上本数: int = 0
    下本数: int = 0
    残り検入数: int = 0
    積数: int = 0
    上台数: int = 0
    下台数: int = 0
    台数: int = 0
    ok: bool = False
    reason: str = ""


def split(total: int, upper: int, lower: int) -> tuple[int, int, int]:
    """検入数を上下へ振り分ける。

    画面のスピンは検入数から1本ずつ移す作りなので、**合計が元の検入数を
    超えない**ことだけを守る。超える指定が来たら下バリ側を詰める。
    """
    upper = max(0, min(upper, total))
    lower = max(0, min(lower, total - upper))
    return upper, lower, total - upper - lower


def _decide_stack(order: OrderInfo, spec: PackagingSpec) -> float:
    """積数を決める (VBA `UFalmo.CommandButton5_Click`)。

    本体 `stack_service._decide_stack` と**枝の入れ子が違う**ので、
    共通化せずに書き写してある。共通化すると、片方を直したときに
    もう片方が黙って変わる。
    """
    weight = vba.val(order.製品単重)
    w_flag = vba.is_numeric(order.梱包単位_重量)
    c_flag = vba.is_numeric(order.梱包単位_枚数)

    if w_flag:
        # ★ここだけ切り上げ。本体は切り捨て
        gross = vba.val(order.梱包単位_重量)
        return float(vba.round_up(gross / weight)) if weight else 0.0

    if c_flag:
        return vba.val(order.梱包単位_枚数)

    if _has_value(spec.梱包単位_重量):
        base = vba.val(spec.梱包単位_重量)
        gross = 0.0
        if spec.重量範囲 == "程度":
            gross = base + base * TOLERANCE_PERCENT / 100.0
        elif spec.重量範囲 in ("以下", "max"):
            gross = base
        return float(vba.round_down(gross / weight)) if weight else 0.0

    if _has_value(spec.梱包単位_枚数):
        if spec.枚数範囲 in COUNT_RANGE_OK:
            return vba.val(spec.梱包単位_枚数)
        return 0.0

    if weight > ONE_COIL_WEIGHT:
        return 1.0
    return 0.0


def calculate(conn: sqlite3.Connection, state: CalcState, *,
              upper: int, lower: int) -> BurrSplit:
    """上下の本数から台数を出す。`state` は書き換えない。

    結果を本体へ送るのは呼び手の仕事(VBA の「結果転送」ボタン)。
    ここで書き換えてしまうと、計算しただけで画面が変わる。
    """
    order = state.order
    if not str(order.製品単重).strip():
        return BurrSplit(ok=False, reason="no_unit_weight")
    if not str(state.検入数).strip():
        return BurrSplit(ok=False, reason="no_inspection_count")

    spec = get_spec(conn, order.包装仕様NO)
    if spec is None:
        return BurrSplit(ok=False, reason="spec_not_found")

    total = int(state.検入数値)
    upper, lower, remain = split(total, upper, lower)

    stack = _decide_stack(order, spec)
    if stack <= 0:
        return BurrSplit(上本数=upper, 下本数=lower, 残り検入数=remain,
                         ok=False, reason="zero_stack")

    upper_units = vba.round_up(upper / stack) if upper else 0
    lower_units = vba.round_up(lower / stack) if lower else 0

    return BurrSplit(
        上本数=upper, 下本数=lower, 残り検入数=remain,
        積数=int(stack), 上台数=upper_units, 下台数=lower_units,
        台数=upper_units + lower_units, ok=True)


def transfer(state: CalcState, result: BurrSplit) -> None:
    """「結果転送」── 出した積数と台数を本体の画面へ送る。

    VBA は送ったあとリプラを計算し直していた。呼び手が
    `ripla_service.calculate` を続けて呼ぶ。
    """
    if not result.ok:
        return
    state.積数 = vba.int_text(result.積数)
    state.台数 = vba.int_text(result.台数)
