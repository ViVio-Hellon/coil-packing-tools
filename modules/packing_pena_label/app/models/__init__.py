# -*- coding: utf-8 -*-
"""アプリ内で扱うデータ形。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

#: チップボール径の選択肢（VBA TIP1000 / TIP950 / TIP820）
TIP_CHOICES = ("TIP1000", "TIP950", "TIP820")

#: VBA SetDiameterInfo のマッピング: 選択 -> (外径mm, 半径mm)
TIP_DIAMETER = {
    "TIP1000": (1100, 550),
    "TIP950": (1000, 500),
    "TIP820": (900, 450),
}


@dataclass
class FormState:
    """``テスラ指定サイズ`` UserForm の状態。

    VBA ではフォームのコントロールが状態そのものだったため、
    移植では明示的な状態オブジェクトへ置き換える。
    """

    #: 選択中の OptionButton（1..14）。未選択は 0。VBA DetectSelectedOB の戻り値。
    selected_ob: int = 0
    #: 選択中の CheckBox（1..6）。未選択は 0。
    selected_cb: int = 0
    #: 名前付き CheckBox ck_08_53 が ON か
    named_cb: bool = False

    #: チップボール径
    tip: str = "TIP1000"

    #: 入力欄
    kensa_no: str = ""
    weight1: str = ""
    weight2: str = ""
    coil_h: Dict[int, str] = field(default_factory=lambda: {1: "", 2: "", 3: "", 4: ""})

    #: 表示ラベル（VBA lblKensaNo / lblSize1 / lblWeight1 / lblSize2 / lblWeight2）
    lbl_kensa_no: str = ""
    lbl_size1: str = ""
    lbl_weight1: str = ""
    lbl_size2: str = ""
    lbl_weight2: str = ""

    #: 計算結果表示（VBA NW1..4 / GW1..4 / TA1..4）
    nw: Dict[int, str] = field(default_factory=lambda: {1: "", 2: "", 3: "", 4: ""})
    gw: Dict[int, str] = field(default_factory=lambda: {1: "", 2: "", 3: "", 4: ""})
    ta: Dict[int, str] = field(default_factory=lambda: {1: "", 2: "", 3: "", 4: ""})

    # ---------------------------------------------------------- 表示状態
    @property
    def take1_visible(self) -> bool:
        """VBA ``Take1.Visible``。

        - CB モード（丈1,2 同時）: 常に True
        - OB モード: 選択 OB が奇数（丈1）のとき True
        - 未選択: False
        """
        if self.selected_cb or self.named_cb:
            return True
        if self.selected_ob:
            return self.selected_ob % 2 == 1
        return False

    @property
    def take2_visible(self) -> bool:
        """VBA ``Take2.Visible``。"""
        if self.selected_cb or self.named_cb:
            return True
        if self.selected_ob:
            return self.selected_ob % 2 == 0
        return False

    @property
    def is_cb_mode(self) -> bool:
        return bool(self.selected_cb or self.named_cb)

    @property
    def is_ob_mode(self) -> bool:
        return bool(self.selected_ob)

    def any_size_selected(self) -> bool:
        """VBA ``IsAnySizeSelected``。"""
        return bool(self.selected_ob or self.selected_cb or self.named_cb)

    def all_coil_empty(self) -> bool:
        """VBA ``IsAllCoilEmpty``。"""
        return all(str(self.coil_h.get(i, "") or "").strip() == "" for i in (1, 2, 3, 4))

    def filled_coil_count(self) -> int:
        """VBA ``CountFilledCoils``。"""
        return sum(1 for i in (1, 2, 3, 4)
                   if str(self.coil_h.get(i, "") or "").strip() != "")

    def to_dict(self) -> dict:
        return {
            "selectedOb": self.selected_ob,
            "selectedCb": self.selected_cb,
            "namedCb": self.named_cb,
            "tip": self.tip,
            "kensaNo": self.kensa_no,
            "weight1": self.weight1,
            "weight2": self.weight2,
            "coilH": {str(k): v for k, v in self.coil_h.items()},
            "lblKensaNo": self.lbl_kensa_no,
            "lblSize1": self.lbl_size1,
            "lblWeight1": self.lbl_weight1,
            "lblSize2": self.lbl_size2,
            "lblWeight2": self.lbl_weight2,
            "nw": {str(k): v for k, v in self.nw.items()},
            "gw": {str(k): v for k, v in self.gw.items()},
            "ta": {str(k): v for k, v in self.ta.items()},
            "take1Visible": self.take1_visible,
            "take2Visible": self.take2_visible,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FormState":
        st = cls()
        if not d:
            return st
        st.selected_ob = int(d.get("selectedOb") or 0)
        st.selected_cb = int(d.get("selectedCb") or 0)
        st.named_cb = bool(d.get("namedCb"))
        st.tip = d.get("tip") or "TIP1000"
        st.kensa_no = d.get("kensaNo") or ""
        st.weight1 = d.get("weight1") or ""
        st.weight2 = d.get("weight2") or ""
        for key, target in (("coilH", st.coil_h), ("nw", st.nw),
                            ("gw", st.gw), ("ta", st.ta)):
            src = d.get(key) or {}
            for i in (1, 2, 3, 4):
                target[i] = src.get(str(i), src.get(i, "")) or ""
        st.lbl_kensa_no = d.get("lblKensaNo") or ""
        st.lbl_size1 = d.get("lblSize1") or ""
        st.lbl_weight1 = d.get("lblWeight1") or ""
        st.lbl_size2 = d.get("lblSize2") or ""
        st.lbl_weight2 = d.get("lblWeight2") or ""
        return st


@dataclass
class MaterialWeights:
    """VBA ``Type MaterialWeights`` に対応。"""
    col: float = 0.0            # コイル巾(mm)
    TA: float = 0.0             # コイル高さ(m)   ※VBA は Single
    KTA: float = 0.0            # 梱包高さ(mm)    ※VBA は Single
    TIPT: float = 0.0           # チップボール分の高さ(m) ※VBA は Single
    PIN: float = 0.0
    TIP: float = 0.0
    SUT: float = 0.0
    ESA: float = 0.0
    HB: float = 0.0
    POR: float = 0.0
    PET: float = 0.0
    PAR: float = 0.0
    NW: float = 0.0
    HU: float = 0.0             # 風袋
    GW: float = 0.0
    DRY: float = 0.0

    #: 胴巻きハードボードの高さ(m)。``TA`` から締結代を引いた値。
    #: ``HB_BAND_CLEARANCE_M`` が 0 の間は ``TA`` と同じ（解析書 A-1）。
    HB_TA: float = 0.0

    TempDiameter: str = ""      # 外径
    HalfDiameter: str = ""      # 半径
    GAI: float = 0.0            # 外周(m)

    #: 検算用式（表記用フォームへ出す文字列）
    formulas: Dict[str, str] = field(default_factory=dict)
    #: 単重・係数（数値化したもの。計算に使う）
    units: Dict[str, tuple] = field(default_factory=dict)
    #: 単重・係数の **マスタの文字列そのまま**（計算式の欄に出す）。
    #: VBA は Variant をそのまま ``CStr`` するので、"1" は "1" のまま出る。
    units_raw: Dict[str, tuple] = field(default_factory=dict)
    #: マスタに見つからなかった資材名
    missing: List[str] = field(default_factory=list)


@dataclass
class HbData:
    """VBA ``Type hbData`` に対応。"""
    col: float = 0.0
    TA: float = 0.0             # ※VBA は Double（MaterialWeights.TA とは別式）
    GAI: float = 0.0
    HBValue: float = 0.0
    HBFormula: str = ""
    HBFlag: bool = False
    missing: List[str] = field(default_factory=list)
