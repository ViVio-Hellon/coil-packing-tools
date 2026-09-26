"""現場の指摘で直したところ(資材計算 0.2.5)。

- 帯の「ライン」「担当者」の選択の文字が小さい → 計算画面の入力欄(18px)にそろえる
- 取り込み元の置き場所の欄の文字が小さい(10px)→ 区画 `.sec` に、別の部品用の
  `.sec`(10px・薄い色・はみ出しは切る)が掛かっていた。区画では打ち消す
"""
from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "coil.css"


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def test_区画は升の小さな文字を受け継がない():
    body = _rule(CSS.read_text(encoding="utf-8"), ".sec")
    for prop in ("font-size:inherit", "color:inherit", "overflow:visible", "white-space:normal"):
        assert prop in body.replace(" ", ""), prop


def test_帯のラインと担当者は入力欄と同じ大きさ():
    body = _rule(CSS.read_text(encoding="utf-8"), ".ribbon select.input")
    assert "font-size:var(--fs-key)" in body.replace(" ", "")
