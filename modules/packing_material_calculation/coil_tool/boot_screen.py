"""起動待機画面 ── 統合版では `common/boot_screen.py` への取り次ぎ

統合版では待機画面は統合アプリの入口(`/`)が出す。この機能の入口
(`/`)は準備が終わる前に開かれたときだけ同じものを出す。
"""
from __future__ import annotations

from common.boot_screen import render, tokens_css  # noqa: F401
