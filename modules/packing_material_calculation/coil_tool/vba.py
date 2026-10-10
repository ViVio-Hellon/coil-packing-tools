"""VBA の数値の振る舞いをそのまま持ってくる

移植で結果がずれる原因は、たいてい業務ロジックではなく**丸めと型**です。
ここに集めて、判定側では `vba.round_up(...)` のように呼ぶだけにする。

    VBA                                  -> ここ
    ---------------------------------------------------------------
    Val(s)                               -> val(s)
    IsNumeric(s)                         -> is_numeric(s)
    Round(x, 0)                          -> round_half_even(x)   ← 銀行丸め
    WorksheetFunction.RoundUp(x, 0)      -> round_up(x)
    WorksheetFunction.RoundDown(x, 0)    -> round_down(x)
    Format(x, "0.00")                    -> fmt(x, 2)
    (Single 同士の比較)                   -> num_equal(a, b)
"""
from __future__ import annotations

import math
import re
import struct
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

# 先頭から数値として読める部分。VBA `Val` は読めるところまでを数値にする
_LEADING_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def val(value: Any) -> float:
    """VBA `Val()`。数値として読めなければ 0。

    VBA の `Val` は**読めるところまで**を数値にする("12abc" は 12)。
    空欄が来る場面が多い(`Val(.本数)` の `.本数` はまだ計算していない
    ことがある)ので、ここが 0 を返すことに業務が乗っている。
    """
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    # VBA `Val` は空白を無視するが、桁区切りのカンマは読まない
    text = text.replace(" ", "").replace("　", "")
    match = _LEADING_NUMBER.match(text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def is_numeric(value: Any) -> bool:
    """VBA `IsNumeric()`。

    **「データなし」の判定に使われている。** 引当データが無いとき、VBA は
    フォームへ文字列 `"データなし"` を入れて、以降はこの関数で
    「数値でない = 指定なし」と読んでいた。数値へ寄せてしまうと
    空欄と 0 の区別が消えるので、この形のまま持ち込む。
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not (math.isnan(value) or math.isinf(value))
    text = str(value).strip().replace(" ", "").replace("　", "")
    if not text:
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False


def round_half_even(value: float) -> int:
    """VBA の `Round(x, 0)`。**銀行丸め(偶数丸め)**。

    0.5 は近いほうの偶数へ行く(2.5 → 2、3.5 → 4)。Python の組み込み
    `round` と同じ規則なので、そのまま使える。

    `台数計算` の1本積み不可の判定
    (`x = Round(Ins / StackC, 0)`)がこれに乗っている。
    四捨五入に変えると、端数の出方が変わって台数が動く。
    """
    return int(round(value))


def round_up(value: float) -> int:
    """Excel `WorksheetFunction.RoundUp(x, 0)`。**0 から遠いほうへ**。

    Python の `math.ceil` と違い、負の数は下へ行く(-1.2 → -2)。
    このツールで負が来ることは無いが、規則としては合わせておく。
    """
    return int(math.ceil(value)) if value >= 0 else int(math.floor(value))


def round_down(value: float) -> int:
    """Excel `WorksheetFunction.RoundDown(x, 0)`。**0 に近いほうへ**(切り捨て)。

    積数は**小数点第1位で切り捨て**(現場の確認。Re_積数 3.5 → 3 も同じ)。
    **割り切れる計算は割り切れたまま切り捨てる**: 1760 ÷ 70.4 は 2進では 24.999999999999996 に
    なり、そのまま切り捨てると 24(正しくは 25)。Excel と同じく有効数字 15 桁で見てから切り捨てる
    (統合 1.2.6。重量 ÷ 単重 がちょうど割り切れるときだけ起きていた)。
    """
    number = Decimal(f"{value:.15g}")
    return int(number.to_integral_value(rounding=ROUND_DOWN))


def to_single(value: float) -> float:
    """Double を Single(単精度)へ落とす。"""
    return struct.unpack("f", struct.pack("f", value))[0]


def num_equal(a: Any, b: Any) -> bool:
    """VBA の `Single` 同士の一致判定。

    【なぜ単純な `==` にしないか】
    包装仕様No別の規則は `If (Atu = 0.8 And Haba = 260#) Then` の形で
    書かれていて、`Atu` / `Haba` は `Single` で宣言された引数です。
    0.8 は2進の浮動小数点でちょうどには表せないので、Double のまま
    比べると**規則がひとつも当たらなくなります**。

    現場で使えている以上、実際には単精度どうしの比較として成立して
    いるはずなので、**両辺を Single へ落としてから比べます**。
    0.8 も 43.7 も 107.8 も、これで意図どおり当たります。

    (この判断は `docs/不明点.md` にも挙げてあります。厚み・幅に
     どこまでの精度を見るかは、本来は業務が決めることなので)
    """
    return to_single(val(a)) == to_single(val(b))


def num_in(value: Any, *candidates: Any) -> bool:
    """`Haba = 117# Or Haba = 130# Or …` の形をまとめたもの。"""
    return any(num_equal(value, c) for c in candidates)


def fmt(value: Any, decimals: int) -> str:
    """VBA `Format(x, "0.00")` 相当。数値でなければそのまま返す。

    **VBA の Format は、数を有効数字 15 桁で見て、半分は 0 から遠いほうへ丸める**(四捨五入)。
    以前は Python の書式(`f"{x:.2f}"`)で、2進の値のまま偶数の側へ丸めていたため、
    製品単重 106.475 が VBA の 106.48 ではなく 106.47 になっていた(統合 1.2.6。VBA の書き出し
    `tests/data/VBA書き出し_20260923_疑似` の Q1293A0 で確かめた値)。単重は積数の計算に使う。
    """
    if not is_numeric(value):
        return "" if value is None else str(value)
    number = Decimal(f"{val(value):.15g}")
    rounded = number.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)          # "-0.00" にしない
    return f"{rounded:.{decimals}f}"


def num_text(value: Any) -> str:
    """数を VBA がテキストボックスへ入れたときの形にする(`CStr`: 有効数字 15 桁)。

    **整数は整数のまま、小数は小数のまま**(切り捨てない)。1160 → "1160"、1160.5 → "1160.5"。
    """
    number = val(value)
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.15g}"


def int_text(value: Any) -> str:
    """整数を画面・帳票へ出す形。0 も "0" として出す。

    空欄にしたいときは呼び手が分岐する。ここで空にしてしまうと、
    「0 本」と「まだ計算していない」が同じに見える。
    """
    return str(int(val(value)))
