"""パレット種類とサイズの選定 (VBA `PalletType` / `PalletSize`)

    包装仕様マスタ「包装仕様」→ パレット種類 / 指定パレットフラグ / 特殊フラグ
    パレットマスタ「パレット」→ 外径が収まる最小のＷ → 新記号

**呼ぶ順番が決まっている。** `pallet_type()` が指定パレットフラグを
`state.PFlag` へ書き、`pallet_size()` がそれを読む。VBA も
`CommandButton5_Click` でこの順に呼んでいた。
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from . import db, special_rules, vba
from .logging_utils import get_logger
from .models import CalcState, PackagingSpec, PalletRow

log = get_logger("pallet_service")

# 断りの種類。文言ではなく定数で運ぶ(文言を直した日に区別が壊れないように)
REASON_SPEC_NOT_FOUND = "spec_not_found"
REASON_NO_PALLET = "no_pallet_fits"

# 収まるパレットが無ければ**計算を断る**パレット種類(不明点 Q24・回答済み)。
#
# VBA は「収まるパレットがDB上にありません」と知らせたあと止まらずに
# 計算を続ける。ﾎｲｰﾙではパレット無しで高さを割り、積数-1・台数-5・
# HB枚数-10 を画面に出した(2026-09-23、疑似ロット Q1201A0)。
# 現場の判断で「ﾎｲｰﾙでパレットが無いときは移植は断る」と決まった。
#
# **ﾎｲｰﾙだけ。** ほかの種類は VBA と同じく、知らせたうえで続ける
# (パレット欄は空のまま)。広げるかどうかは決まっていない
REFUSE_WITHOUT_PALLET: tuple[str, ...] = ("ﾎｲｰﾙ",)

# パレット種類ごとの適合条件の違い。VBA `PalletSize` の分岐そのまま
#   スカシ / 全面 … 巾と丈の両方を見る
#   強度UP        … 巾だけ(2024.3.4 の暫定仕様・佐藤氏)
#   それ以外      … 上下限の設定が無いのでＷだけを見る
TYPES_WIDTH_AND_LENGTH = ("スカシ", "全面")
TYPE_STRENGTH_UP = "強度UP"


# ==================================================================
# 包装仕様を引く
# ==================================================================
def get_spec(conn: sqlite3.Connection, spec_no: str) -> Optional[PackagingSpec]:
    """包装仕様マスタの1行。無ければ None。"""
    if not spec_no:
        return None
    row = db.fetch_one(
        conn, "SELECT * FROM 包装仕様 WHERE 包装仕様NO = ?", (spec_no,),
        caller_name="get_spec")
    return PackagingSpec.from_row(row) if row is not None else None


# ==================================================================
# パレット種類 (VBA `PalletType`)
# ==================================================================
def pallet_type(conn: sqlite3.Connection, state: CalcState) -> str:
    """パレット種類を決めて `state` へ書く。

    包装仕様マスタの「パレット」列がそのまま種類になる。あわせて

        「サイズ」列 → `PFlag`  (指定パレットフラグ)
        「種類」列   → `特殊Flag`

    を立てる。**どちらも `pallet_size` と `ripla_service` が読む**ので、
    ここで書いておかないと後段が効かない。
    """
    spec_no = state.order.包装仕様NO
    if not spec_no:
        return ""

    spec = get_spec(conn, spec_no)
    if spec is None:
        # VBA: MsgBox "包装仕様No未登録", vbCritical
        state.note(f"包装仕様No {spec_no} がマスタに登録されていません")
        return ""

    state.パレット種類 = spec.パレット
    if spec.サイズ:
        state.PFlag = spec.サイズ
    if spec.種類:
        state.特殊Flag = spec.種類

    # 【最後に効かせる】VBA のコメント:「izumi の条件を最後に実行する
    # _2024.3.4(スカシにもどってしまう)」── マスタの値を入れたあとで
    # 上書きしないと、外径で決めた種類が元に戻る
    state.パレット種類 = special_rules.pallet_type_override(
        spec_no, state.外径値, state.パレット種類)
    return state.パレット種類


# ==================================================================
# パレットサイズ (VBA `PalletSize`)
# ==================================================================
def _load_pallets(conn: sqlite3.Connection, pallet_type_name: str) -> list[PalletRow]:
    rows = db.fetch_all(
        conn, "SELECT * FROM パレット WHERE 種類 = ?", (pallet_type_name,),
        caller_name="load_pallets") or []
    return [PalletRow.from_row(r) for r in rows]


def _fits(row: PalletRow, pallet_type_name: str, outer: float) -> bool:
    """その行は外径に対して使えるか。種類ごとに見る列が違う。"""
    if pallet_type_name in TYPES_WIDTH_AND_LENGTH:
        return (row.巾下限 <= outer <= row.巾上限
                and row.丈下限 <= outer <= row.丈上限)
    if pallet_type_name == TYPE_STRENGTH_UP:
        # 丈の上下限を見ない(2024.3.4 の暫定仕様)
        return row.巾下限 <= outer <= row.巾上限
    # EXPS / ｸｰﾗｰﾌｨﾝ など。上下限の設定が無いのでＷだけ
    return row.W >= outer


def pallet_size(conn: sqlite3.Connection, state: CalcState) -> str:
    """パレット名称(新記号)を決めて `state` へ書く。

    使える行のうち**Ｗがいちばん小さいもの**を選ぶ。大きいほうが確実に
    収まるが、パレットは小さいほど安いし場所も取らない。

    そのあと指定パレットフラグ(`PFlag`)があれば上書きする。
    """
    pallet_type_name = state.パレット種類
    if not pallet_type_name:
        return ""
    outer = state.外径値

    rows = _load_pallets(conn, pallet_type_name)
    fitting = [r for r in rows if _fits(r, pallet_type_name, outer)]
    if not fitting:
        # VBA: MsgBox "収まるパレットがDB上にありません" & "PalletSize"
        state.note(f"外径 {vba.fmt(outer, 2)} が収まる「{pallet_type_name}」の"
                   f"パレットがマスタにありません")
        return ""

    smallest = min(r.W for r in fitting)
    # **同じＷの行が複数あることがある。** VBA は全行を走って最後に
    # 一致したものを採るので、ここも最後の一致に合わせる
    chosen = [r for r in rows if r.W == smallest]
    if chosen:
        state.パレット名称 = chosen[-1].新記号
        state.パレットサイズ = vba.int_text(chosen[-1].W)

    _apply_designated_pallet(state, rows, outer)
    return state.パレット名称


def _apply_designated_pallet(state: CalcState, rows: list[PalletRow], outer: float) -> None:
    """指定パレットフラグ(包装仕様マスタ「サイズ」列)による上書き。

    2通りある。

        数値        … そのＷのパレットに決め打つ
        "外径指定"  … 外径から段階表で決める (1C1258)

    どちらでも**最小選定を上回る**。マスタが「この品はこのパレット」と
    言っているなら、収まるかどうかより指定が優先。
    """
    flag = state.PFlag
    if not flag:
        return

    if vba.is_numeric(flag):
        target = vba.val(flag)
    elif flag == special_rules.OUTER_DESIGNATION:
        target = special_rules.pallet_width_by_outer(outer)
        if target <= 0.0:
            # どの段にも当たらない。VBA も何もせず最小選定のまま進む
            log.info("外径指定の段に当たりませんでした: 外径=%s", outer)
            return
    else:
        # パレット種類ごとに意味が変わる指定。VBA のコメント:
        # 「パレット種類ごと変更 再定義要***PalletTypeで定義」
        # いまのところ定義が無いので何もしない
        return

    for row in rows:
        if row.W == target:
            state.パレット名称 = row.新記号
            state.パレットサイズ = vba.int_text(row.W)


def refuse_without_pallet(state: CalcState) -> bool:
    """収まるパレットが無く、計算を断るべきか(`REFUSE_WITHOUT_PALLET`)。"""
    return (state.パレット種類 in REFUSE_WITHOUT_PALLET
            and not str(state.パレット名称).strip())
