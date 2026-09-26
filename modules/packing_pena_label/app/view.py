# -*- coding: utf-8 -*-
"""最小テンプレートエンジン。

追加ライブラリを入れられない制約のため、Jinja2 等は使わず標準ライブラリのみ。
必要な機能は次の 3 つだけ。

    {{ name }}      … HTML エスケープして差し込む
    {{{ name }}}    … エスケープせず差し込む（生成済み HTML 用）
    {% include x %} … 部分テンプレートの取り込み
"""

from __future__ import annotations

import html
import os
import re
from typing import Any, Dict

_VAR_RAW = re.compile(r"\{\{\{\s*([A-Za-z0-9_.]+)\s*\}\}\}")
_VAR = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
_INCLUDE = re.compile(r"\{%\s*include\s+([A-Za-z0-9_./-]+)\s*%\}")


class Renderer:
    def __init__(self, templates_dir: str, cache: bool = True):
        self.dir = templates_dir
        self.cache = cache
        self._files: Dict[str, str] = {}

    def _read(self, name: str) -> str:
        if self.cache and name in self._files:
            return self._files[name]
        path = os.path.join(self.dir, name)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if self.cache:
            self._files[name] = text
        return text

    @staticmethod
    def _lookup(ctx: Dict[str, Any], key: str) -> Any:
        cur: Any = ctx
        for part in key.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part, "")
            else:
                cur = getattr(cur, part, "")
            if cur is None:
                return ""
        return cur

    def render(self, name: str, ctx: Dict[str, Any] | None = None) -> str:
        ctx = ctx or {}
        text = self._read(name)

        # include を先に展開（1段のみ。入れ子は使わない方針）
        def _inc(m: re.Match) -> str:
            return self._read(m.group(1))
        text = _INCLUDE.sub(_inc, text)

        def _raw(m: re.Match) -> str:
            return str(self._lookup(ctx, m.group(1)))
        text = _VAR_RAW.sub(_raw, text)

        def _esc(m: re.Match) -> str:
            return html.escape(str(self._lookup(ctx, m.group(1))), quote=True)
        text = _VAR.sub(_esc, text)
        return text


def esc(v: Any) -> str:
    """HTML エスケープ。"""
    return html.escape("" if v is None else str(v), quote=True)


def nl2br(v: Any) -> str:
    """改行を <br> へ（エスケープ後）。VBA の vbCrLf 入りメッセージ用。"""
    return esc(v).replace("\n", "<br>")
