"""統合アプリのモード

**モードは1つしかありません。** 3つの機能(梱包明細・ペナラベル・資材計算)は
モードではなく**タブ**で分けます。同じプロセス・同じポート・同じロックの
中に3つが同居するので、起動基盤(`launch_guard` / `process_manager` /
`server`)が受け渡す名前は1つで足ります。

それでもモジュールを置いてあるのは、起動基盤が移植元(梱包明細・資材計算)
と同じ形でモード名を引数に取るためです。基盤側を書き換えずに済ませます。
"""
from __future__ import annotations

MAIN = "main"
DEFAULT = MAIN

# 起動基盤が「使えるモードの一覧」として参照する
KEYS: tuple[str, ...] = (MAIN,)
ALL = KEYS

# 旧名の読み替え表(このアプリには無い)
LEGACY_NAMES: dict[str, str] = {}


def normalize(mode: str) -> str:
    """モード名をそろえる。空や未知の名前は既定のモードにする。

    断らないのが要点。モードが1つしかない以上、どんな値を渡されても
    行き先は1つに決まる。ここで例外を投げると、打ち間違いが「起動失敗」
    として現れ、直す先が分からない。
    """
    key = (mode or "").strip().lower()
    return LEGACY_NAMES.get(key, key) if key in KEYS or key in LEGACY_NAMES else DEFAULT


def label(mode: str = MAIN) -> str:
    """画面やログに出す名前。"""
    return "統合"
