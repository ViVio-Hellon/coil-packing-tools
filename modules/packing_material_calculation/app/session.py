"""画面をまたぐ作業状態 ── プロセスに1つ

VBA は `UF_Material`(フォーム)そのものが作業状態だった。閉じるまで
値が残り、どの処理からも同じフォームを読み書きしていた。

ここも同じ持ち方にする。**1サーバ = 1セッション。** このツールは
1台の端末を1人が使う前提なので、利用者ごとに分ける理由が無く、
分けると「資材計算で出した結果がチェックリストに出ない」が起きる。

【1つしかないものは、1つの画面からしか触らせない】
この持ち方には前提がある ── **触る画面が1枚であること。**
ブラウザのタブは何枚でも開けるので、2枚開くと2枚とも入力できて
しまい、同じ1つの状態を奪い合う。しかも打った値は相手の画面に
出ないので、**上書きされたことに本人が気づけない。**

    タブA: LotNo 1234567 を打つ  → ここの状態 = 1234567
    タブB: LotNo 7654321 を打つ  → ここの状態 = 7654321
    タブA: 計算Start            → **7654321 で計算される**

そこで `app/screen.py` が、使ってよい画面を1枚だけに絞っている。
**状態を分けるのではなく、画面を絞るほうで解いた** ── 分けると
VBA と挙動が変わる(画面をまたいで値が続かなくなる)ため。

保存はしない(落ちたら消える)。ただし**チェックリストだけは DB に
持つ** ── VBA ではブックに残っていたもので、閉じて消えると困る。
"""
from __future__ import annotations

import threading

from modules.packing_material_calculation.coil_tool.models import CalcState

_lock = threading.Lock()
_state = CalcState()


def get() -> CalcState:
    """いまの作業状態。"""
    return _state


def reset() -> CalcState:
    """まっさらにする (VBA `フォームクリア`)。"""
    global _state
    with _lock:
        _state = CalcState()
    return _state
