# -*- coding: utf-8 -*-
"""VBA / Excel の数値・書式の挙動を Python で再現する層。

移植で最も事故が起きやすいのが「丸め」と「型」であるため、
業務ロジックからは必ずこのモジュール経由で丸め・書式化を行う。

再現対象
    Val()                       -> val()
    CDbl()                      -> cdbl()
    Format(x, "0.0")            -> fmt()
    WorksheetFunction.Round     -> ws_round()   … 四捨五入(0から遠い方)
    WorksheetFunction.RoundUp   -> ws_roundup() … 絶対値の切り上げ
    Dim x As Single             -> single()     … 単精度への丸め
    IsNumeric()                 -> is_numeric()
    StrConv(s, vbNarrow)        -> to_narrow()
    UCase()                     -> str.upper()
"""

from __future__ import annotations

import math
import re
import struct
import unicodedata
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP, InvalidOperation

__all__ = [
    "single", "val", "cdbl", "fmt", "ws_round", "ws_roundup",
    "is_numeric", "to_narrow", "PI", "round_half_up",
    "cstr", "cstr_single",
]

#: Excel の WorksheetFunction.PI() と同じ値
PI = math.pi


# ---------------------------------------------------------------- 型
def single(x: float) -> float:
    """``Dim x As Single`` 相当。float64 -> float32 -> float64。

    ``MaterialWeights.TA`` / ``KTA`` は VBA 側で Single 宣言されており、
    エサフォームの ``TA > 0.82`` 判定が単精度で行われる。
    境界値で結果が変わるため、宣言どおり再現する。
    """
    if x is None:
        return 0.0
    try:
        return struct.unpack("<f", struct.pack("<f", float(x)))[0]
    except (OverflowError, ValueError):
        return float(x)


# ---------------------------------------------------------------- 変換
_VAL_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def val(x) -> float:
    """VBA の ``Val()``。先頭の数値部分だけを取り出す。数値でなければ 0。"""
    if x is None:
        return 0.0
    if isinstance(x, bool):
        return -1.0 if x else 0.0          # VBA の True は -1
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(" ", "").replace("\t", "")
    m = _VAL_RE.match(s)
    return float(m.group(0)) if m else 0.0


def cdbl(x) -> float:
    """VBA の ``CDbl()``。変換できない場合は ValueError。"""
    if isinstance(x, bool):
        return -1.0 if x else 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = to_narrow(str(x)).strip().replace(",", "")
    if s == "":
        raise ValueError("CDbl: 空文字は変換できません")
    return float(s)


def is_numeric(x) -> bool:
    """VBA の ``IsNumeric()``。空文字・None は False。"""
    if x is None:
        return False
    if isinstance(x, bool):
        return True
    if isinstance(x, (int, float)):
        return True
    s = to_narrow(str(x)).strip()
    if s == "":
        return False
    # VBA は前後の +/- と通貨記号、末尾の型文字を許容するが
    # 本システムの入力範囲では通常の数値判定で足りる
    try:
        float(s.replace(",", ""))
        return True
    except ValueError:
        return False


def to_narrow(s: str) -> str:
    """``StrConv(s, vbNarrow)`` 相当。全角英数記号を半角へ。"""
    if s is None:
        return ""
    return unicodedata.normalize("NFKC", str(s))


# ---------------------------------------------------------------- 丸め
def round_half_up(x: float, digits: int = 0) -> float:
    """四捨五入（0 から遠い方向）。Excel の ROUND と同じ。

    Python 組込 ``round()`` は銀行家丸めのため使用しない。
    """
    if x is None:
        return 0.0
    try:
        d = Decimal(repr(float(x)))
    except (InvalidOperation, ValueError, OverflowError):
        return 0.0
    q = Decimal(1).scaleb(-digits)          # digits=-1 -> 10
    sign = -1 if d < 0 else 1
    r = (abs(d) / q).quantize(Decimal(1), rounding=ROUND_HALF_UP) * q
    return float(r * sign)


def ws_round(x: float, digits: int = 0) -> float:
    """``Application.WorksheetFunction.Round``。"""
    return round_half_up(x, digits)


def ws_roundup(x: float, digits: int = 0) -> float:
    """``Application.WorksheetFunction.RoundUp``。絶対値を切り上げる。

    ``RoundUp(1234, -1)`` -> 1240
    """
    if x is None:
        return 0.0
    try:
        d = Decimal(repr(float(x)))
    except (InvalidOperation, ValueError, OverflowError):
        return 0.0
    q = Decimal(1).scaleb(-digits)
    sign = -1 if d < 0 else 1
    r = (abs(d) / q).quantize(Decimal(1), rounding=ROUND_UP) * q
    return float(r * sign)


# ---------------------------------------------------------------- 書式
def fmt(x, pattern: str = "0.0") -> str:
    """VBA の ``Format(x, "0.0")`` 等。本システムで使う書式のみ対応。

    対応: "0", "0.0", "0.00", "0.000", "00"
    丸めは half-up（Excel 表示と同じ方向）。
    """
    if x is None or x == "":
        return ""
    if pattern == "00":
        return "%02d" % int(val(x))
    if pattern == "0":
        return "%d" % int(round_half_up(val(x), 0))

    m = re.fullmatch(r"0\.(0+)", pattern)
    if not m:
        raise ValueError("未対応の書式: %r" % pattern)
    digits = len(m.group(1))
    v = round_half_up(val(x), digits)
    # -0.0 を 0.0 に正規化
    if v == 0:
        v = 0.0
    return ("%." + str(digits) + "f") % v


# ---------------------------------------------------------------- 文字列化
#: VBA が Double を ``CStr`` するときの有効桁
_CSTR_DOUBLE_DIGITS = 15
#: 同じく Single のとき
_CSTR_SINGLE_DIGITS = 7


def cstr(x, digits: int = _CSTR_DOUBLE_DIGITS) -> str:
    """VBA ``CStr``（数値）。

    VBA は Double を **有効数字 15 桁**、Single を **7 桁** で文字列化し、
    末尾の 0 は落とす。整数値なら小数点を付けない。

    Python の ``str(float)`` は最短往復表現（17 桁になりうる）なので、
    そのまま使うと計算式の欄に ``0.5968999862670898`` のような値が並ぶ。
    VBA の帳票では ``0.5969`` と出ていた。

    文字列を渡した場合はそのまま返す（VBA の ``CStr`` が
    Variant 文字列に対して恒等なのと同じ。マスタ値はこの経路）。
    """
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, bool):
        return "True" if x else "False"
    v = float(x)
    if v != v or v in (float("inf"), float("-inf")):
        return str(v)
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    s = "%.*g" % (digits, v)
    # 指数表記になった場合は VBA の書き方（E+xx）へ寄せる
    if "e" in s:
        mant, exp = s.split("e")
        s = "%sE%s%02d" % (mant, "+" if int(exp) >= 0 else "-", abs(int(exp)))
    return s


def cstr_single(x) -> str:
    """Single 型の値を VBA ``CStr`` と同じ 7 桁で文字列化する。"""
    return cstr(x, _CSTR_SINGLE_DIGITS)
