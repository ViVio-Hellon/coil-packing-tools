"""コイル重量の再計算 (VBA `コイル重量計算`)

外径・内径・幅・比重から、コイル1本の重量(kg)を出す。
引当の製品単重が当てにならないときに、現物の寸法から出し直すためのもの。

    重量 = π/4 × (外径² − 内径²) × 比重 × 幅 ÷ 10⁶

外径・内径は mm、幅も mm、比重は g/cm³。10⁶ で割って kg になる。
"""
from __future__ import annotations

import math

from . import vba


def coil_weight(outer: float, inner: float, width: float, gravity: float) -> float:
    """コイル1本の重量(kg)。**小数2桁で切り捨て**。

    VBA は `Format$(Application.RoundDown(tkg, 2), "0.00")` と、
    切り捨ててから書式を当てている。四捨五入にすると、重量の上限で
    積数を決める品の積数が1本変わることがある。
    """
    tkg = math.pi / 4 * (outer ** 2 - inner ** 2) * gravity * width / 10 ** 6
    # RoundDown(x, 2) は 0 に近いほうへの切り捨て
    scaled = tkg * 100
    truncated = math.floor(scaled) if scaled >= 0 else math.ceil(scaled)
    return truncated / 100


def coil_weight_text(outer: float, inner: float, width: float, gravity: float) -> str:
    """画面へ出す形 ("0.00")。"""
    return vba.fmt(coil_weight(outer, inner, width, gravity), 2)
