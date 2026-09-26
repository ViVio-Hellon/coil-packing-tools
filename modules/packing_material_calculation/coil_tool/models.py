"""このツールが扱うデータの形

【なぜフォームを模した1つの入れ物にしたか】
VBA は `UF_Material`(フォーム)そのものを作業領域にしていました。
`PalletType` が `PFlag` を書き、`PalletSize` がそれを読む。
`台数計算` が `積数` を書き、`リプラ計算` がそれを読む ── というふうに、
**関数どうしがフォーム経由で値を渡し合っています**。

引数と戻り値に開くこともできますが、そうすると受け渡しの順番が変わり、
「この時点でこの欄はもう埋まっているか」という前提がコードから消えます。
移植で結果を合わせることを最優先にして、**同じ受け渡しの形**を残します。

    CalcState … フォームの1画面ぶん。計算はこれを読み、これに書く

色(赤・水色)も業務情報なので、`flags` として持ちます。
「どの規則で決まったか」は現場が見て判断に使っているものです。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from . import vba

# ------------------------------------------------------------------
# フラグ ── VBA の色。何で決まったかを運ぶ
# ------------------------------------------------------------------
# VBA                                   ここでの名前
# UF_Material.F重量.ForeColor = 赤   →  "weight"
# UF_Material.F枚数.ForeColor = 赤   →  "count"
# UF_Material.F高さ.ForeColor = 赤   →  "height"
# UF_Material.F包装仕様.ForeColor=赤 →  "spec"
# UF_Material.FM.BorderColor = 水色  →  "no_single"      (1本積み不可)
# UF_Material.FM.BorderColor = 赤    →  "single_split"   (1本不可の振り分けが起きた)
# 間リプラ種類/最下部リプラ種類 = 赤 →  "spacer_differs"
FLAG_WEIGHT = "weight"
FLAG_COUNT = "count"
FLAG_HEIGHT = "height"
FLAG_SPEC = "spec"
FLAG_NO_SINGLE = "no_single"
FLAG_SINGLE_SPLIT = "single_split"
FLAG_SPACER_DIFFERS = "spacer_differs"


@dataclass
class OrderInfo:
    """受注1件ぶん (VBA `フォーム展開` が仕掛受注から展開していたもの)。

    **数量系を文字列で持つ**のが要点。VBA は値が 0 のとき
    `"データなし"` という文字列をフォームへ入れて、以降 `IsNumeric` で
    「指定なし」と読んでいました。数値へ寄せると空欄と 0 の区別が消え、
    `台数計算` の優先順位が変わってしまいます。
    """
    受注番号: str = ""
    受注材質: str = ""
    受注調質: str = ""
    受注板厚: str = ""          # Format(x, "0.000")
    受注板幅: str = ""          # Format(x, "0.0")
    用途コード: str = ""
    用途名: str = ""
    包装仕様NO: str = ""        # Left(x, 6)
    納入先名称: str = ""
    取引先名称: str = ""
    製品単重: str = ""          # Format(x, "0.00")
    梱包単位_重量: str = ""      # 数値 or "データなし"
    梱包単位_枚数: str = ""      # 数値 or "データなし"
    コイル外径_MAX: str = ""     # 数値 or "データなし"
    コイル外径_目標: str = ""    # 数値 or "データなし"
    コイル外径_MIN: str = ""     # 数値 or "データなし"
    コイル内径_目標: str = ""    # 数値 or "データなし"
    工場用コメント: str = ""
    営業納期: str = ""
    比重: str = ""
    梱包コード: str = ""
    # 仕掛引当から。VBA は `.出荷` に入れ、あとで営業納期として帳票へ出す
    出荷日: str = ""

    # 「データなし」を表す文字列。VBA `フォーム展開` がそのまま入れていた
    NO_DATA = "データなし"


@dataclass
class PackagingSpec:
    """包装仕様マスタの1行 (梱包資材マスタ / テーブル「包装仕様」)。

    列はすべて文字列。VBA も `<> "" And IsNumeric(...)` の形で
    読んでいたので、数値へ寄せずそのまま持つ。
    """
    包装仕様NO: str = ""
    納入先名称: str = ""
    用途名: str = ""
    パレット: str = ""
    サイズ: str = ""              # 指定パレットフラグ
    種類: str = ""                # 特殊フラグ
    リプラサイズ: str = ""
    コイル間スペーサー: str = ""
    最下部スペーサー: str = ""
    下本数: str = ""
    緩衝材: str = ""
    コイル間は間紙入: str = ""
    梱包単位_重量: str = ""
    重量範囲: str = ""
    梱包単位_枚数: str = ""
    枚数範囲: str = ""
    梱包総高さ: str = ""
    高さ範囲: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "PackagingSpec":
        data = dict(row)
        return cls(**{f: str(data.get(f, "") or "") for f in cls.__dataclass_fields__})


@dataclass
class PalletRow:
    """パレットマスタの1行。"""
    種類: str = ""
    巾下限: float = 0.0
    巾上限: float = 0.0
    丈下限: float = 0.0
    丈上限: float = 0.0
    新記号: str = ""
    W: float = 0.0

    @classmethod
    def from_row(cls, row: Any) -> "PalletRow":
        data = dict(row)
        return cls(
            種類=str(data.get("種類", "") or ""),
            巾下限=vba.val(data.get("巾下限")),
            巾上限=vba.val(data.get("巾上限")),
            丈下限=vba.val(data.get("丈下限")),
            丈上限=vba.val(data.get("丈上限")),
            新記号=str(data.get("新記号", "") or ""),
            W=vba.val(data.get("W")),
        )


@dataclass
class CalcState:
    """フォーム1画面ぶんの作業領域 (VBA `UF_Material`)。

    入力(上)と、計算で埋まる欄(下)が同居している。VBA のフォームと
    同じ形にしてあるので、移植元の行と1対1で読み比べられる。
    """

    # ---------------- 入力 ----------------
    LOT: str = ""
    検入数: str = ""
    外径: str = ""
    order: OrderInfo = field(default_factory=OrderInfo)

    # ---------------- パレット ----------------
    パレット種類: str = ""
    パレット名称: str = ""
    パレットサイズ: str = ""
    # 指定パレットフラグ(包装仕様マスタ「サイズ」列)
    PFlag: str = ""
    # 特殊フラグ(包装仕様マスタ「種類」列、または判定中に立つ)
    特殊Flag: str = ""

    # ---------------- 積数・台数 ----------------
    積数: str = ""
    台数: str = ""
    # 1本積み不可で分けたときの別枠 (VBA `Re_積数` / `Re_台数`)
    Re_積数: str = ""
    Re_台数: str = ""
    # 総高さの確認表示 (VBA `HightCoil`)
    総高さ: str = ""
    単重再計算: str = ""

    # ---------------- リプラ・緩衝材 ----------------
    コイル間: str = ""
    リプラサイズ: str = ""
    最下部本数: str = ""
    最下部長さ: str = ""
    最下部長さ_短: str = ""
    長い本数: str = ""
    短い本数: str = ""
    リプラ長さ: str = ""
    本数: str = ""
    緩衝材: str = ""
    HB枚数: str = ""
    間リプラ種類: str = ""
    最下部リプラ種類: str = ""
    # 1C1282 (ｱｲｴﾑｱｲｶﾊﾞｰ) 専用
    IMI中間長さ: str = ""
    IMI中間本数: str = ""
    # リプラ総本数 (VBA `TotalC` = `リプラ計算` の戻り値)
    TotalC: str = ""

    # ---------------- 表示上の印 ----------------
    flags: set[str] = field(default_factory=set)
    # 計算を続けられなかった理由。画面はこれを見て断りを出す
    messages: list[str] = field(default_factory=list)

    # --------------------------------------------------------------
    def mark(self, flag: str) -> None:
        self.flags.add(flag)

    def note(self, message: str) -> None:
        """VBA の `MsgBox` にあたるもの。画面へそのまま出す。"""
        if message not in self.messages:
            self.messages.append(message)

    # --------------------------------------------------------------
    def soft_clear(self) -> None:
        """VBA `ソフトクリア`。**計算結果だけ**を消す。

        入力(LotNo・受注情報・検入数・外径)は残す。もう一度
        「計算Start」を押したときに入力から入れ直さずに済む。
        """
        self.総高さ = ""
        self.パレット種類 = ""
        self.パレットサイズ = ""
        self.パレット名称 = ""
        self.緩衝材 = ""
        self.HB枚数 = ""
        self.積数 = ""
        self.台数 = ""
        self.コイル間 = ""
        self.リプラサイズ = ""
        self.最下部本数 = ""
        self.最下部長さ = ""
        self.最下部長さ_短 = ""
        self.長い本数 = ""
        self.短い本数 = ""
        self.リプラ長さ = ""
        self.本数 = ""
        self.TotalC = ""
        self.Re_積数 = ""
        self.Re_台数 = ""
        self.特殊Flag = ""
        self.PFlag = ""
        self.最下部リプラ種類 = ""
        self.間リプラ種類 = ""
        self.IMI中間長さ = ""
        self.IMI中間本数 = ""
        self.flags.clear()
        self.messages.clear()

    def form_clear(self) -> None:
        """VBA `フォームクリア`。入力ごと全部消す(LotNo を打ち直したとき)。"""
        self.soft_clear()
        self.検入数 = ""
        self.外径 = ""
        self.単重再計算 = ""
        self.order = OrderInfo()

    # --------------------------------------------------------------
    # 読み取りの補助。判定側が `vba.val(state.積数)` と書き続けずに済む
    # --------------------------------------------------------------
    @property
    def 積数値(self) -> float:
        return vba.val(self.積数)

    @property
    def 台数値(self) -> float:
        return vba.val(self.台数)

    @property
    def Re_台数値(self) -> float:
        return vba.val(self.Re_台数)

    @property
    def 検入数値(self) -> float:
        return vba.val(self.検入数)

    @property
    def 外径値(self) -> float:
        return vba.val(self.外径)


@dataclass
class ChecklistRow:
    """資材発注管理チェックリストの1行 (VBA `資材配列` の戻り値)。

    帳票の列そのまま。**改行込みの文字列**で持つ ── VBA が1セルに
    2行入れていた形(`最下部長さ & vbLf & リプラ長さ`)をそのまま運ぶ。
    """
    依頼日: str = ""
    LotNo: str = ""
    用途名: str = ""
    パレット種類: str = ""
    台数: str = ""
    営業納期: str = ""
    コイル間: str = ""
    リプラ長さ: str = ""
    リプラ本数: str = ""
    リプラサイズ: str = ""
    緩衝材: str = ""
    依頼者: str = ""
    入荷日: str = ""
    # 「ｻｲｽﾞ確定品」のチェック。立っていると A 列へ `〆`
    サイズ確定: bool = False

    # 帳票の列の並び。VBA `資材発注印字` が B 列から順に貼っていた順番
    ORDER = ("依頼日", "LotNo", "用途名", "パレット種類", "台数", "営業納期",
             "コイル間", "リプラ長さ", "リプラ本数", "リプラサイズ", "緩衝材",
             "依頼者", "入荷日")

    def as_cells(self) -> list[str]:
        """B〜N 列の中身を並びどおりに返す。"""
        return [getattr(self, name) for name in self.ORDER]
