# -*- coding: utf-8 -*-
"""入力チェック（VBA の ValidateKensaNo / InpCheck / KeyPress 制限 相当）。

**VBA のメッセージ文言・判定順を一切変更していない。**

メッセージの「N桁目」は **右から数えた桁**（`Mid` は左からの位置なので
一見ずれて見えるが、7 桁の番号を右から読んだ表現で全部そろっている）。

VBA は 3 文字目を一度も検査しておらず、`W1A1111` が通っていた。
2026.09 の判断でこの穴を塞いだ（解析書 B-2）。
"""

from __future__ import annotations

from typing import List, Optional

from .vba_compat import is_numeric, to_narrow

#: VBA ValidateKensaNo の共通メッセージ前半（Chr(10) = LF）
_MSG_HEAD = "検査NOが7桁入力されていない\nもしくは"


def normalize_kensa_no(raw: str) -> str:
    """VBA ``StrConv(UCase(txtKensaNo), vbNarrow)`` と同じ正規化。"""
    return to_narrow((raw or "").upper())


def _mid(s: str, start: int, length: int) -> str:
    """VBA ``Mid(s, start, length)``（1 始まり、範囲外は空文字）。"""
    if start < 1:
        return ""
    return s[start - 1:start - 1 + length]


def validate_kensa_no(ken: str) -> Optional[str]:
    """VBA ``ValidateKensaNo``。OK なら None、NG ならメッセージを返す。

    判定順も VBA のまま（最初に該当したものだけを返す）。

    VBA は 1 / 2 / 4〜7 文字目しか見ておらず、**3 文字目に穴があった**
    （``W1A1111`` が通り、そのままラベルとバーコードに載っていた）。
    2026.09 の判断で塞いだ（解析書 B-2）。

    3 文字目の判定は **最後に置いている**。先に置くと、
    2 文字目と 3 文字目の両方が不正な入力のメッセージが変わってしまう。
    最後なら、**これまで弾かれていた入力の文言は 1 つも変わらず**、
    これまで通り抜けていたものだけが新しく弾かれる。
    """
    if not is_numeric(_mid(ken, 2, 1)):
        return _MSG_HEAD + "6桁目にアルファベットが入力されています"
    if not is_numeric(_mid(ken, 7, 1)):
        return _MSG_HEAD + "1桁目にアルファベットが入力されています"
    if not is_numeric(_mid(ken, 4, 4)):
        return _MSG_HEAD + "1～4桁目にアルファベットが入力されています"
    if is_numeric(_mid(ken, 1, 1)):
        return _MSG_HEAD + "7桁目に数字が入力されています"
    # 2026.09 追加：3 文字目（右から 5 桁目）
    if not is_numeric(_mid(ken, 3, 1)):
        return _MSG_HEAD + "5桁目にアルファベットが入力されています"
    return None


def inp_check(state, mode: int) -> bool:
    """VBA ``InpCheck(a)``。重量が未入力なら True。

    mode 1 : 丈1（OptionButton 奇数 / lblSpec_08_53_1）
    mode 2 : 丈2（OptionButton 偶数 / lblSpec_08_53_2）
    mode 3 : 丈1,2 同時（CheckBox1..6 / ck_08_53）
    """
    w1 = state.weight1 or ""
    w2 = state.weight2 or ""

    if mode == 1:
        # VBA: For i = 1 To 11 Step 2 … 最初に ON を見つけたら Exit For
        if state.selected_ob and state.selected_ob % 2 == 1 and state.selected_ob <= 11:
            return w1 == ""
        # 名前付き OB（lblSpec_08_53_1 = obIdx 13）
        if state.selected_ob == 13:
            return w1 == ""
        return False

    if mode == 2:
        if state.selected_ob and state.selected_ob % 2 == 0 and state.selected_ob <= 12:
            return w2 == ""
        if state.selected_ob == 14:
            return w2 == ""
        return False

    if mode == 3:
        if state.selected_cb:
            return w1 == "" or w2 == ""
        if state.named_cb:
            return w1 == "" or w2 == ""
        return False

    return False


def check_weight_inputs(state) -> Optional[str]:
    """VBA CommandButton1 の 3 連続 InpCheck。NG メッセージ or None。"""
    if inp_check(state, 1):
        return "重量入力がありません　丈1"
    if inp_check(state, 2):
        return "重量入力がありません　丈2"
    if inp_check(state, 3):
        return "重量入力がありません　丈1　or　丈2"
    return None


def validate_settings(state) -> Optional[str]:
    """VBA ``IsValidSettings``。NG ならメッセージ、OK なら None。

    判定順は VBA のまま（チップボール → 丈フレーム表示 → サイズ選択）。

    VBA では 2 番目の「丈フレームが両方とも非表示」は **無言で中止** していた。
    これはフォームのコントロール ``Take1.Visible`` / ``Take2.Visible`` が
    サイズ選択とは独立したプロパティだったためで、
    「CheckBox のチェックを外したが Take フレームは表示のまま」という
    食い違った状態が作れたことによる。

    移植では丈フレームの表示を **選択状態から導出** している（models.FormState）。
    そのため「両方非表示」と「サイズ未選択」は常に同時に成立し、
    無言中止が起きる状況＝サイズ未選択 に一致する。
    利用者が理由の分からないまま放置されないよう、この場合は
    VBA の 3 番目のメッセージを返す。**計算を行わない点は VBA と同じ。**
    """
    from ..models import TIP_CHOICES

    if state.tip not in TIP_CHOICES:
        return "チップボールチェックがありません"
    if not (state.take1_visible or state.take2_visible):
        return "コイルサイズが選択されていません"
    if not state.any_size_selected():
        return "コイルサイズが選択されていません"
    return None


# ---------------------------------------------------------------- 入力制限
def filter_alnum_upper(raw: str) -> str:
    """VBA の KeyPress 制限（0-9, A-Z, a-z のみ / 小文字は大文字へ）。

    ``txtKensaNo`` / ``CoilH1..4`` / 全サイズの TextBox1..5 に適用。
    """
    out = []
    for ch in (raw or ""):
        code = ord(ch)
        if 48 <= code <= 57:        # 0-9
            out.append(ch)
        elif 65 <= code <= 90:      # A-Z
            out.append(ch)
        elif 97 <= code <= 122:     # a-z -> 大文字
            out.append(chr(code - 32))
        # それ以外は VBA と同じく破棄
    return "".join(out)


def filter_numeric_text(raw: str) -> str:
    """VBA の ``*_Change`` 制限。

    ``If Not IsNumeric(text) Then text = ""``（ただし "-" 単独は残す）。
    CoilH1..4 は "-" 許容の記述が無いため ``allow_minus=False`` で使う。
    """
    s = raw or ""
    if s == "-":
        return s
    return s if is_numeric(s) else ""


def filter_coil_text(raw: str) -> str:
    """``CoilH1..4_Change``: 数値でなければ空にする（"-" の例外なし）。"""
    s = raw or ""
    return s if is_numeric(s) else ""


#: VBA CommandButton8（全角→半角変換）の置換表。**順序も VBA のまま**
FULLWIDTH_REPLACEMENTS: List[tuple] = [
    ("Ｘ", "Ｘ"), ("ｘ", "Ｘ"), ("X", "Ｘ"), ("x", "Ｘ"), ("×", "Ｘ"),
    ("（", "("), ("）", ")"),
    ("０", "0"), ("５", "5"), ("８", "8"), ("４", "4"), ("１", "1"),
    ("２", "2"), ("３", "3"), ("６", "6"), ("７", "7"), ("９", "9"),
]


def convert_fullwidth(text: str) -> str:
    """VBA ``CommandButton8_Click`` の置換を 1 セル分に適用。"""
    s = text if text is not None else ""
    s = str(s)
    for old, new in FULLWIDTH_REPLACEMENTS:
        s = s.replace(old, new)
    return s


def num_extract(value) -> str:
    """VBA ``numExtract``。数字と小数点だけを抽出。"/" があれば前半のみ。"""
    s = "" if value is None else str(value)
    if "/" in s:
        s = s.split("/")[0]
    return "".join(ch for ch in s if ch.isdigit() or ch == ".")


def convert_to_half(value) -> str:
    """VBA ``ConvertToHalf``。全角数字を半角にしてから数字だけ抽出。"""
    s = "" if value is None else str(value)
    out = []
    for ch in s:
        if "０" <= ch <= "９":
            out.append(chr(ord(ch) - 0xFEE0))
        else:
            out.append(ch)
    return num_extract("".join(out))
