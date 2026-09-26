"""計算Start ── パレットから員数までを順に通す

VBA `UF_Material.CommandButton5_Click` の移植。**順番がすべて**です。

    ソフトクリア
      → パレット種類   (PalletType)    … 指定パレットフラグ・特殊フラグが立つ
      → パレット名称   (PalletSize)    … 指定パレットフラグを読む
      → 積数・台数     (台数計算)      … パレットサイズを高さの上限に使う
      → リプラ員数     (リプラ計算)    … 積数・台数を読む。最後

前の段が書いた値を次の段が読むので、入れ替えると結果が変わります。
`docs/VBA解析.md` §4 に、どの段が何を書くかの表があります。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from . import pallet_service, ripla_service, stack_service, vba
from .logging_utils import get_logger
from .models import CalcState

log = get_logger("calc_service")


@dataclass
class CalcResult:
    """計算の結果。画面はこれを見て表示を組む。"""
    ok: bool = False
    reason: str = ""
    total_ripla: int = 0
    shape: ripla_service.ShapeView = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.shape is None:
            self.shape = ripla_service.ShapeView()


def calculate(conn: sqlite3.Connection, state: CalcState) -> CalcResult:
    """計算Start (VBA `CommandButton5_Click`)。

    検入数が無いと何も出せないので、そこだけ先に断る。
    """
    if not str(state.検入数).strip():
        state.note("検入数を入力してください")
        return CalcResult(ok=False, reason="no_inspection_count")

    state.soft_clear()

    pallet_service.pallet_type(conn, state)
    pallet_service.pallet_size(conn, state)
    if pallet_service.refuse_without_pallet(state):
        # ﾎｲｰﾙで収まるパレットが無い。VBA は負の台数を出すが、移植は断る
        # (不明点 Q24・回答済み)。理由の文言は pallet_size が入れている
        return CalcResult(ok=False, reason=pallet_service.REASON_NO_PALLET)

    stack = stack_service.calculate(conn, state)
    if not stack.ok:
        # パレットまでは出ている。そこまでを見せて、理由を添える
        return CalcResult(ok=False, reason=stack.reason)

    total = ripla_service.calculate(conn, state)

    shape = ripla_service.shape_view(
        state.パレット種類, int(vba.val(state.最下部本数)))
    return CalcResult(ok=True, total_ripla=total, shape=shape)


def recalculate_from_stack(conn: sqlite3.Connection, state: CalcState) -> CalcResult:
    """積数を手で直したときの再計算 (VBA `積数_AfterUpdate`)。

    パレットは引き直すが、**台数は積数から出し直す**(`台数計算` は
    呼ばない)。手で入れた積数を、規則で上書きしてしまわないため。

        台数 = 切り上げ(検入数 ÷ 積数)
    """
    if not str(state.積数).strip():
        return CalcResult(ok=False, reason="no_stack")

    pallet_service.pallet_type(conn, state)
    pallet_service.pallet_size(conn, state)
    if pallet_service.refuse_without_pallet(state):
        return CalcResult(ok=False, reason=pallet_service.REASON_NO_PALLET)

    if state.積数値 <= 0:
        state.note("積数には1以上を入れてください")
        return CalcResult(ok=False, reason="zero_stack")

    state.台数 = vba.int_text(vba.round_up(state.検入数値 / state.積数値))

    total = ripla_service.calculate(conn, state)
    shape = ripla_service.shape_view(
        state.パレット種類, int(vba.val(state.最下部本数)))
    return CalcResult(ok=True, total_ripla=total, shape=shape)
