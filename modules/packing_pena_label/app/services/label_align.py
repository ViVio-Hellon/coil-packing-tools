# -*- coding: utf-8 -*-
"""試し刷りの実測値から、印刷の補正値（倍率・補正X/Y）を出す。

== なぜ要るか ==

ブラウザーの印刷ダイアログに「倍率 100%」「余白なし」が無い端末がある。
その場合ブラウザーは **用紙の印刷可能な範囲へ縮めて** 刷るので、

    * 全体が数 % 小さくなる（倍率のズレ）
    * 全体が右下へ数 mm ずれる（余白ぶんの原点のズレ）

が同時に起きる。下の段ほどズレが積み上がるため「大きくずれすぎ」に見える。
これは **一定の拡大 + 平行移動** なので、こちら側で逆の補正を掛ければ戻せる。

== 考え方 ==

紙に出る位置は、ブラウザー側の倍率 ``P`` と原点ズレ ``O`` を使って

    実測 = P × (補正値 + 設計値 × 現在倍率) + O

になる。測定線（設計 100.0mm）の実測から ``P`` が分かり、
基準十字の実測から ``O`` が分かるので、次の 2 本で打ち消せる。

    新しい倍率 = 現在倍率 × 100 ÷ 実測（測定線）
    新しい補正 = 現在補正 + 基準mm × 現在倍率 − 実測mm × 新しい倍率

いずれも **同じ 1 枚の試し刷り** から出した値であることが前提。
1 回で合わなくても、同じ手順を繰り返せば必ず近づく（収束する）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

#: 測定線の設計長さ（mm）。試し刷りに刷ってある横線・縦線の長さ。
SPAN_MM = 100.0

#: 基準十字の位置（mm）。紙の左上角からの設計値。
CROSS_X_MM = 20.0
CROSS_Y_MM = 20.0

#: 補正の上下限（設定画面の SettingSpec と合わせる）
OFFSET_MIN, OFFSET_MAX = -50.0, 50.0
SCALE_MIN, SCALE_MAX = 50.0, 150.0

#: 実測として受け付ける範囲（明らかな打ち間違いを弾く）
SPAN_TOLERANCE = 25.0          # 100mm に対して 75〜125mm まで
CROSS_TOLERANCE = 40.0         # 20mm に対して -20〜60mm まで

#: 横と縦で倍率がこれ以上違ったら知らせる（mm）
AXIS_DIFF_WARN_MM = 0.5


@dataclass
class AlignResult:
    """算出した補正値と、画面に出す説明。"""
    offset_x_mm: float
    offset_y_mm: float
    scale_pct: float
    messages: List[str]
    clamped: bool = False

    def to_dict(self) -> dict:
        return {"offsetXMm": self.offset_x_mm, "offsetYMm": self.offset_y_mm,
                "scalePct": self.scale_pct, "messages": list(self.messages),
                "clamped": self.clamped}


class AlignError(ValueError):
    """実測値を受け付けられないとき。"""


def _num(raw, label: str) -> Optional[float]:
    """空欄は None。数でなければ断る。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    # 全角数字でも受ける（現場の入力は半角とは限らない）
    s = s.translate(str.maketrans("０１２３４５６７８９．－", "0123456789.-"))
    try:
        return float(s)
    except ValueError:
        raise AlignError("%s は数字で入れてください（入力: %s）" % (label, raw))


def _round2(x: float) -> float:
    return round(x + 0.0, 2)


def _clamp(x: float, lo: float, hi: float):
    if x < lo:
        return lo, True
    if x > hi:
        return hi, True
    return x, False


def plan(cur_offset_x: float, cur_offset_y: float, cur_scale_pct: float,
         span_x=None, span_y=None, cross_x=None, cross_y=None) -> AlignResult:
    """試し刷りの実測値から新しい補正値を出す。

    引数はすべて「空欄なら触らない」。
    ``span_x`` / ``span_y`` は測定線（設計 100.0mm）の実測、
    ``cross_x`` / ``cross_y`` は紙の端から基準十字までの実測（設計 20.0mm）。
    """
    sx = _num(span_x, "横の測定線")
    sy = _num(span_y, "縦の測定線")
    cx = _num(cross_x, "基準十字までの横")
    cy = _num(cross_y, "基準十字までの縦")

    old_scale = float(cur_scale_pct or 100.0)
    if old_scale <= 0:
        old_scale = 100.0
    msgs: List[str] = []
    clamped = False

    # ---------------- 倍率 ----------------
    new_scale = old_scale
    spans = []
    for v, name in ((sx, "横"), (sy, "縦")):
        if v is None:
            continue
        if abs(v - SPAN_MM) > SPAN_TOLERANCE:
            raise AlignError(
                "%sの測定線の実測 %.1fmm は %.0fmm から離れすぎています。"
                "測る線を間違えていないか確認してください。" % (name, v, SPAN_MM))
        if v <= 0:
            raise AlignError("%sの測定線の実測は 0 より大きい値を入れてください。" % name)
        spans.append((v, name))

    if spans:
        if len(spans) == 2 and abs(spans[0][0] - spans[1][0]) >= AXIS_DIFF_WARN_MM:
            msgs.append(
                "横 %.1fmm と縦 %.1fmm で %.1fmm 違います。"
                "倍率は 1 つしか持てないので平均で合わせました。"
                "段の位置を優先するなら縦だけを入れてください。"
                % (spans[0][0], spans[1][0], abs(spans[0][0] - spans[1][0])))
        avg = sum(v for v, _ in spans) / len(spans)
        new_scale = old_scale * SPAN_MM / avg
        new_scale = _round2(new_scale)
        new_scale, c = _clamp(new_scale, SCALE_MIN, SCALE_MAX)
        clamped = clamped or c
        if c:
            msgs.append("倍率は %.0f〜%.0f%% までです。上限で止めました。"
                        % (SCALE_MIN, SCALE_MAX))

    # ---------------- 位置 ----------------
    # 実測 = P×(補正 + 設計×旧倍率) + O、新倍率 = 1/P。
    # 新しい補正 = 旧補正 + 設計×旧倍率 − 実測×新倍率
    old_f = old_scale / 100.0
    new_f = new_scale / 100.0

    new_x = float(cur_offset_x or 0.0)
    new_y = float(cur_offset_y or 0.0)

    for meas, target, name in ((cx, CROSS_X_MM, "横"), (cy, CROSS_Y_MM, "縦")):
        if meas is None:
            continue
        if abs(meas - target) > CROSS_TOLERANCE:
            raise AlignError(
                "基準十字までの%s %.1fmm は %.0fmm から離れすぎています。"
                "紙の端から十字の線までを測っているか確認してください。"
                % (name, meas, target))
        moved = target * old_f - meas * new_f
        if name == "横":
            new_x = _round2(new_x + moved)
        else:
            new_y = _round2(new_y + moved)

    new_x, c1 = _clamp(new_x, OFFSET_MIN, OFFSET_MAX)
    new_y, c2 = _clamp(new_y, OFFSET_MIN, OFFSET_MAX)
    clamped = clamped or c1 or c2
    if c1 or c2:
        msgs.append("補正は %+.0f〜%+.0fmm までです。端で止めました。"
                    % (OFFSET_MIN, OFFSET_MAX))

    if sx is None and sy is None and cx is None and cy is None:
        raise AlignError("実測値をどれか 1 つは入れてください。")

    return AlignResult(offset_x_mm=_round2(new_x), offset_y_mm=_round2(new_y),
                       scale_pct=_round2(new_scale), messages=msgs,
                       clamped=clamped)


def nudge(cur_offset_x: float, cur_offset_y: float, cur_scale_pct: float,
          dx=0.0, dy=0.0, dscale=0.0) -> AlignResult:
    """今の補正から、指定ぶんだけ動かす（微調整）。"""
    ddx = _num(dx, "横の移動量") or 0.0
    ddy = _num(dy, "縦の移動量") or 0.0
    dds = _num(dscale, "倍率の増減") or 0.0

    x, c1 = _clamp(_round2(float(cur_offset_x or 0.0) + ddx), OFFSET_MIN, OFFSET_MAX)
    y, c2 = _clamp(_round2(float(cur_offset_y or 0.0) + ddy), OFFSET_MIN, OFFSET_MAX)
    s, c3 = _clamp(_round2(float(cur_scale_pct or 100.0) + dds), SCALE_MIN, SCALE_MAX)
    msgs = []
    if c1 or c2 or c3:
        msgs.append("設定できる範囲の端で止めました。")
    return AlignResult(offset_x_mm=x, offset_y_mm=y, scale_pct=s,
                       messages=msgs, clamped=(c1 or c2 or c3))


def describe(offset_x: float, offset_y: float, scale_pct: float) -> str:
    """いまの補正を、現場の言葉で言う（文字だけ）。

    数値（X +2.50mm）では「どっちへ動いているのか」が分からないため。
    X が + なら右へ、Y が + なら下へ動かして刷っている。
    """
    return _describe(offset_x, offset_y, scale_pct, lambda t: t)


def describe_html(offset_x: float, offset_y: float, scale_pct: float) -> str:
    """``describe`` の HTML 版（量を太字にする）。"""
    return _describe(offset_x, offset_y, scale_pct, lambda t: "<b>%s</b>" % t)


def _describe(offset_x, offset_y, scale_pct, strong) -> str:
    x = float(offset_x or 0.0)
    y = float(offset_y or 0.0)
    s = float(scale_pct or 100.0)
    parts = []
    if abs(x) >= 0.005:
        parts.append("%sへ %s" % ("右" if x > 0 else "左", strong("%.1fmm" % abs(x))))
    if abs(y) >= 0.005:
        parts.append("%sへ %s" % ("下" if y > 0 else "上", strong("%.1fmm" % abs(y))))
    sized = abs(s - 100.0) >= 0.005
    size = "大きさ %s" % strong("%.2f%%" % s)
    if not parts and not sized:
        return "いまは%sで刷っています。" % strong("ずらさず・等倍")
    if parts and sized:
        return "いまは %s ずらし、%s で刷っています。" % ("・".join(parts), size)
    if parts:
        return "いまは %s ずらして刷っています。" % "・".join(parts)
    return "いまは 位置はそのまま、%s で刷っています。" % size
