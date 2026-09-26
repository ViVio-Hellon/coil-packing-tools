"""モード ── この画面は誰のためのものか

このツールは**1モードだけ**。元の VBA も担当者が1つのフォームで
最初から最後まで操作する作りで、画面ごとに資格が分かれていない。

それでもモジュールを残してあるのは、起動基盤(`app_config` / `launch_guard` /
`process_manager`)がモードを引数に取る作りで、共通基盤側を業務都合で
書き換えないためです(基盤仕様書 5.2「共通基盤へ業務固有の名称を
埋め込まない」)。モードを増やす日が来たら、ここに1行足せば足ります。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mode:
    key: str
    label: str
    detail: str
    color: str
    mark: str


MAIN = "main"

ALL: tuple[Mode, ...] = (
    Mode(key=MAIN, label="資材計算",
         detail="ロットから資材の員数を出し、チェックリストと発注票を作る",
         color="#0b5d51", mark="資"),
)

KEYS: tuple[str, ...] = tuple(m.key for m in ALL)

DEFAULT = MAIN

# 旧名は無い(このツールは最初からモードが1つ)
LEGACY_NAMES: dict[str, str] = {}


def get(key: str) -> Mode:
    for mode in ALL:
        if mode.key == key:
            return mode
    raise ValueError(f"未知のモード: {key!r} (使えるのは {', '.join(KEYS)})")


def label(key: str) -> str:
    return get(key).label


def normalize(key: str) -> str:
    """知らない名前はそのまま返し、呼び手に判断させる。

    ここで既定へ倒すと、打ち間違いが「既定で起動した」としか見えず、
    なぜそうなったのか分からなくなる。
    """
    text = (key or "").strip()
    return LEGACY_NAMES.get(text, text)
