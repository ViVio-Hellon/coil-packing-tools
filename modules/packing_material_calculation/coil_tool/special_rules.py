"""包装仕様No別の業務規則 ── 取引先ごとの「この品はこう積む」

VBA では `PalletType` / `PalletSize` / `台数計算` / `リプラ計算` の
あちこちに `If PackagingNO = "1C1188" Then …` の形で散っていました。
**どこに何があるか分からない**のが元ツールのいちばんの保守負債なので、
ここ1か所に集めます。判定の順番と中身は変えていません。

【増やすとき】
このファイルに1件足して、`tests/test_special_rules.py` に境界の
テストを足す。判定側(`stack_service` など)は触らない。

【厚み・幅の一致判定】
`vba.num_equal` を使う(単精度どうしの比較)。理由は `vba.py` を参照。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from . import vba
from .models import CalcState, FLAG_HEIGHT, FLAG_SPEC

# ==================================================================
# パレット種類 (VBA `PalletType` の末尾)
# ==================================================================
# 1C1103 ｲｽﾞﾐﾒﾀﾙ
#
# 【境界が重なっている】VBA の並びは
#     外径 < 800            → 全面
#     780 <= 外径 <= 890    → 強度UP
#     890 <= 外径 <= 990    → 強度UP
# で、780〜799 は最初の条件にも当たります。`ElseIf` は先勝ちなので
# **実際には「全面」**になります。990 超は上書きされず、マスタの値のまま。
#
# ここでも先勝ちのまま再現します(`docs/不明点.md` Q1)。
IZUMI_SPEC_NO = "1C1103"


def pallet_type_override(spec_no: str, outer: float, current: str) -> str:
    """包装仕様No別のパレット種類の上書き。当たらなければ `current` のまま。"""
    if spec_no != IZUMI_SPEC_NO:
        return current
    if outer < 800.0:
        return "全面"
    if 780.0 <= outer <= 890.0:
        return "強度UP"
    if 890.0 <= outer <= 990.0:
        return "強度UP"
    # 990 超はマスタの値のまま(VBA も上書きしていない)
    return current


# ==================================================================
# パレットサイズ (VBA `PalletSize` の「外径指定」)
# ==================================================================
# 指定パレットフラグが "外径指定" のとき(1C1258)、外径からＷを段階で決める。
#
# 【隙間がある】700 未満・1330 超、および 780.5 のような小数は
# どの段にも当たらず 0(= 該当なし)になります。VBA のまま
# (`docs/不明点.md` Q2)。
OUTER_DESIGNATION = "外径指定"

_OUTER_STEPS: tuple[tuple[float, float, float], ...] = (
    (700.0, 780.0, 800.0),
    (781.0, 880.0, 900.0),
    (881.0, 980.0, 1000.0),
    (981.0, 1080.0, 1100.0),
    (1081.0, 1180.0, 1200.0),
    (1181.0, 1249.0, 1300.0),
    (1250.0, 1330.0, 1350.0),
)


def pallet_width_by_outer(outer: float) -> float:
    """「外径指定」の段階表。当たらなければ 0(該当なし)。"""
    for low, high, width in _OUTER_STEPS:
        if low <= outer <= high:
            return width
    return 0.0


# ==================================================================
# 積数 (VBA `台数計算` の「ランダム>>>サイズで変化するもの_包装仕様」)
# ==================================================================
@dataclass(frozen=True)
class StackRule:
    """1つの包装仕様Noぶんの積数の決め方。"""
    spec_no: str
    customer: str
    # (state, 積数, 板厚, 板幅) -> 新しい積数。変えないなら None を返す
    apply: Callable[[CalcState, float, float, float], Optional[float]]


def _shimano(state: CalcState, stack: float, atu: float, haba: float) -> Optional[float]:
    """1C1188 ｼﾏﾉ。2.0×180 は包装仕様書に載っていない(VBA のコメント)。"""
    if vba.num_equal(atu, 0.8) and vba.num_equal(haba, 260.0):
        return 2.0
    if vba.num_equal(atu, 2.0) and vba.num_equal(haba, 180.0):
        return 3.0
    return None


def _sekisui(state: CalcState, stack: float, atu: float, haba: float) -> Optional[float]:
    """1C1178 積水化学工業。"""
    if (vba.num_equal(atu, 0.25) and vba.num_equal(haba, 43.7)) or \
       (vba.num_equal(atu, 0.3) and vba.num_equal(haba, 49.9)):
        return 4.0
    if (vba.num_equal(atu, 0.35) and vba.num_equal(haba, 62.3)) or \
       (vba.num_equal(atu, 0.45) and vba.num_equal(haba, 77.3)) or \
       (vba.num_equal(atu, 0.5) and vba.num_equal(haba, 97.2)) or \
       (vba.num_equal(atu, 0.75) and vba.num_equal(haba, 125.0)):
        return 3.0
    if (vba.num_equal(atu, 0.95) and vba.num_equal(haba, 156.0)) or \
       (vba.num_equal(atu, 1.2) and vba.num_equal(haba, 194.0)):
        return 2.0
    return None


def _nishihara(state: CalcState, stack: float, atu: float, haba: float) -> Optional[float]:
    """1C1271 ﾆｼﾊﾗﾘｺｳ(ｶ。総高さの確認表示も出す。"""
    new = None
    if vba.num_equal(atu, 0.8) and vba.num_equal(haba, 72.0):
        new = 4.0
    elif vba.num_equal(atu, 1.0) and vba.num_equal(haba, 85.0):
        new = 3.0
    elif vba.num_equal(atu, 2.0) and vba.num_equal(haba, 140.0):
        new = 3.0
    # VBA はここで必ず総高さを出している(当たらなくても現在の積数で計算)
    used = new if new is not None else stack
    state.総高さ = f"{vba.round_down((80.0 + haba) * used + 210.0)}mm"
    return new


def _teirado(state: CalcState, stack: float, atu: float, haba: float) -> Optional[float]:
    """1C1295 ｶ)ﾃｲﾗﾄﾞ。"""
    if vba.num_equal(atu, 4.5) and vba.num_in(haba, 117.0, 130.0, 132.0, 170.0):
        return 2.0
    if vba.num_equal(atu, 4.5) and vba.num_in(haba, 180.0, 214.0, 260.0):
        return 1.0
    return None


def _hitachi(state: CalcState, stack: float, atu: float, haba: float) -> Optional[float]:
    """1C1244 ｶ)ﾋﾀﾁｷﾝｿﾞｸﾈｵﾏﾃﾘｱﾙ。"""
    if vba.num_equal(atu, 0.16) and vba.num_equal(haba, 315.0):
        return 1.0
    if (vba.num_equal(atu, 0.5) and vba.num_equal(haba, 115.0)) or \
       (vba.num_equal(atu, 0.3) and vba.num_equal(haba, 125.0)) or \
       (vba.num_equal(atu, 0.25) and vba.num_equal(haba, 125.0)) or \
       (vba.num_equal(atu, 0.36) and vba.num_equal(haba, 50.0)):
        return 2.0
    return None


def _hatsudai(state: CalcState, stack: float, atu: float, haba: float) -> Optional[float]:
    """1C1277 ｶ)ﾊﾂﾀﾞｲｾｲｻｸｼﾖ。幅だけで決まる。"""
    if haba <= 100.0:
        return 3.0
    return 2.0


def _logimate(state: CalcState, stack: float, atu: float, haba: float) -> Optional[float]:
    """1C1250 ロジメイト送り梱包。"""
    if vba.num_equal(atu, 1.8) and vba.num_in(haba, 110.0, 108.0):
        return 2.0
    if vba.num_equal(atu, 1.8) and vba.num_in(haba, 107.8, 89.0, 86.0):
        return 4.0
    if vba.num_equal(atu, 1.5) and vba.num_in(haba, 82.0, 87.0):
        return 5.0
    if vba.num_equal(atu, 1.3) and vba.num_equal(haba, 80.0):
        return 5.0
    return None


def _fdk(state: CalcState, stack: float, atu: float, haba: float) -> Optional[float]:
    """1C1263 ＦＤＫ鳥取向け条割コイル。"""
    if vba.num_equal(atu, 0.3) and vba.num_equal(haba, 34.0):
        return 8.0
    if vba.num_equal(atu, 0.25) and vba.num_equal(haba, 50.0):
        return 5.0
    if vba.num_equal(atu, 0.5) and vba.num_equal(haba, 65.0):
        return 4.0
    return None


def _shimano_kanshou(state: CalcState, stack: float, atu: float, haba: float) -> Optional[float]:
    """1C1254 シマノ金商。上限重量を決めて、単重で割って切り捨てる。

    VBA のコメント:「材調質の指定もあるが…なんか 5052 とかで全部
    引っかからなくなるから一旦そこは除く」── **材質・調質は見ない**。
    """
    limit = 0.0
    if vba.num_equal(atu, 0.6) and vba.num_equal(haba, 34.0):
        limit = 300.0
    elif vba.num_equal(atu, 1.0) and vba.num_in(haba, 96.0, 130.0):
        limit = 500.0
    if limit <= 0.0:
        return None
    weight = vba.val(state.order.製品単重)
    if weight <= 0.0:
        return None
    gross = limit + limit * 10.0 / 100.0   # 「程度」と同じ +10%
    return float(vba.round_down(gross / weight))


def _miyama(state: CalcState, stack: float, haba: float, ripla_size: str) -> None:
    """1C1281 ﾐﾔﾏｾｲｺｳ(ｶ。積数は変えず、総高さの確認だけ出す。

    VBA では `台数計算` の後半、`If PackFlag Or Cflag Then` の中に
    あります。**包装仕様フラグか枚数指定が立っているときだけ**通るので、
    `stack_service` が同じ条件で呼びます。

    リプラの角サイズは**包装仕様マスタの値**を見る(フォーム側ではない)。
    VBA も `Ripu = val(TempHiki(1, C_リプラサイズ))` と書いている。
    """
    ripla = vba.val(ripla_size)
    state.総高さ = f"{vba.round_down((stack * ripla) + (stack * haba) + 210.0)}mm"
    state.mark(FLAG_SPEC)
    state.mark(FLAG_HEIGHT)


# 積数を上書きする規則。VBA の `If … Then` の並び順そのまま
STACK_RULES: tuple[StackRule, ...] = (
    StackRule("1C1188", "ｼﾏﾉ", _shimano),
    StackRule("1C1178", "積水化学工業", _sekisui),
    StackRule("1C1271", "ﾆｼﾊﾗﾘｺｳ(ｶ", _nishihara),
    StackRule("1C1295", "ｶ)ﾃｲﾗﾄﾞ", _teirado),
    StackRule("1C1244", "ｶ)ﾋﾀﾁｷﾝｿﾞｸﾈｵﾏﾃﾘｱﾙ", _hitachi),
    StackRule("1C1277", "ｶ)ﾊﾂﾀﾞｲｾｲｻｸｼﾖ", _hatsudai),
    StackRule("1C1250", "ロジメイト送り梱包", _logimate),
    StackRule("1C1263", "ＦＤＫ鳥取向け条割コイル", _fdk),
    StackRule("1C1254", "シマノ金商", _shimano_kanshou),
)

_STACK_BY_NO = {r.spec_no: r for r in STACK_RULES}

# 総高さの確認だけを出すもの(積数は変えない)
MIYAMA_SPEC_NO = "1C1281"


def apply_stack_rules(state: CalcState, stack: float, atu: float, haba: float) -> tuple[float, bool]:
    """積数の上書きを試す。

    戻り値は `(積数, 規則が当たったか)`。

    **「当たった」は規則が動いたかどうか**で、積数が変わったかでは
    ありません。VBA も `PackFlag = True` を `If PackagingNO = … Then`
    の中で無条件に立てていて、このフラグが立つと**高さ判定を丸ごと
    スキップ**します。ここを取り違えると、高さで積数が減るはずのない
    品が減ります。
    """
    spec_no = state.order.包装仕様NO
    rule = _STACK_BY_NO.get(spec_no)
    if rule is None:
        return stack, False
    new = rule.apply(state, stack, atu, haba)
    state.mark(FLAG_SPEC)
    return (new if new is not None else stack), True


def apply_height_display_rules(state: CalcState, stack: float, haba: float,
                               ripla_size: str) -> None:
    """`PackFlag` か `Cflag` が立っているときだけ通る総高さの表示。

    VBA `台数計算` の `If PackFlag Or Cflag Then … GoTo PacSkip` の中。
    """
    if state.order.包装仕様NO == MIYAMA_SPEC_NO:
        _miyama(state, stack, haba, ripla_size)


# ==================================================================
# リプラ (VBA `リプラ計算` の中)
# ==================================================================
# 1C1188 ｼﾏﾉ ── リプラの角サイズがサイズで変わる
SHIMANO_SPEC_NO = "1C1188"

# 1C1282 ｱｲｴﾑｱｲｶﾊﾞｰ ── リプラを3種類使う。チェックリストへ2行出す
IMI_COVER_SPEC_NO = "1C1282"
IMI_COVER_FLAG = "ｱｲｴﾑｱｲｶﾊﾞｰ"
IMI_COIL_LENGTH = 800.0      # コイル間
IMI_BOTTOM_SHORT = 600.0     # 最下部の短
IMI_MIDDLE_LENGTH = 950.0    # 中間
# 最下部長さは 1050, 950, 800, 600 の4通りある(VBA のコメント)

# 1C1297 ｶ)ｺﾄﾌﾞｷｾｲﾐﾂ ── 最下部と間でリプラ種類が違う。2行出す
KOTOBUKI_SPEC_NO = "1C1297"
KOTOBUKI_FLAG = "ｺﾄﾌﾞｷｾｲﾐﾂ"

# 2行出力になる特殊フラグ
TWO_ROW_FLAGS = (IMI_COVER_FLAG, KOTOBUKI_FLAG)


def ripla_size_override(spec_no: str, atu: float, haba: float, current: str) -> str:
    """リプラの角サイズの上書き (1C1188 ｼﾏﾉ)。"""
    if spec_no != SHIMANO_SPEC_NO:
        return current
    if vba.num_equal(atu, 0.8) and vba.num_equal(haba, 260.0):
        return "40"
    return "30"
