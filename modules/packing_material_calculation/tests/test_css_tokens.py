"""色は tokens.css から取る ── 綴り違いを黙って通さない

【なぜ要るのか】
`coil.css` の先頭に「色は必ず tokens.css から取る(直値を書かない)」と
書いてあるのに、実際には**定義されていないトークン名**が使われていて、
予備値の直値で動いていた。

    .input--need{ background:var(--warn-soft,#fdf6d8); }
                              ^^^^^^^^^^^^ どこにも無い
                                           ^^^^^^^ 明るい地の色だけ

明るい地では正しく見えるので気づけない。暗い地にすると、字
(`--ink`)は白に変わるのに地は淡い黄のままなので、**打った字が
読めなくなる**。現場から「入力の文字色が見えない」と上がってきた。

【どう防ぐか】
1. 使っているトークンが**全部定義されている**こと
2. 色のトークンに**予備値を書かない**こと ── 予備値があると、綴りを
   間違えても動いてしまい、1 の検査をすり抜ける
3. 明るい地で定義した色は、**暗い地にも定義がある**こと
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "css"

# 画面が動くときに値を差し込むもの(`style="--w:40%"`)。CSS には定義が無い
RUNTIME_SET = {"--w"}

# **名前ではなく値で見分ける。** 名前の付け方は変わるが、
# 「直に書かれた色かどうか」は変わらない
LITERAL_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(|hsla?\()")


def _is_literal_color(value: str) -> bool:
    """明暗で書き分けが要る色か。

    要らないのは3つ ── 寸法(`44px`)、字の並び(`system-ui,…`)、
    そして**他のトークンから作る色**(`color-mix(… var(--focus) …)`)。
    最後のものは元が変われば一緒に変わるので、書き分けると二重に持つ
    ことになる。
    """
    value = value.strip()
    if value.startswith("var(") or "var(" in value:
        return False
    return bool(LITERAL_COLOR.match(value))


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def _sheets() -> dict[str, str]:
    return {p.name: _strip_comments(p.read_text(encoding="utf-8"))
            for p in sorted(CSS_DIR.glob("*.css"))}


def _defined(text: str) -> set[str]:
    return set(re.findall(r"(--[A-Za-z0-9-]+)\s*:", text))


def _declarations(text: str) -> dict[str, str]:
    """トークン名 → 書かれている値。"""
    return {m.group(1): m.group(2)
            for m in re.finditer(r"(--[A-Za-z0-9-]+)\s*:\s*([^;}]+)", text)}


def _used(text: str) -> set[str]:
    return set(re.findall(r"var\(\s*(--[A-Za-z0-9-]+)", text))


def test_使っているトークンは全部定義されている():
    sheets = _sheets()
    defined: set[str] = set()
    for text in sheets.values():
        defined |= _defined(text)

    missing: dict[str, set[str]] = {}
    for name, text in sheets.items():
        for token in _used(text) - defined - RUNTIME_SET:
            missing.setdefault(token, set()).add(name)

    assert not missing, (
        "tokens.css に無いトークンを使っています。"
        "予備値で動いていても、明暗のどちらかで読めなくなります:\n"
        + "\n".join(f"  {t} ({', '.join(sorted(f))})"
                    for t, f in sorted(missing.items())))


def test_色のトークンに予備値を書かない():
    """`var(--なにか, #直値)` を禁じる。

    予備値は**綴り間違いの隠れ蓑**になる。書いてあると、上の検査を
    すり抜けたうえに、明るい地の色だけで固定されてしまう。
    """
    bad: list[str] = []
    for name, text in _sheets().items():
        for line_no, line in enumerate(text.splitlines(), 1):
            for m in re.finditer(r"var\(\s*(--[A-Za-z0-9-]+)\s*,([^)]*)\)", line):
                token, fallback = m.group(1), m.group(2).strip()
                if token in RUNTIME_SET or not _is_literal_color(fallback):
                    continue
                bad.append(f"  {name}:{line_no}  {token} の予備値 {fallback}")
    assert not bad, "色に予備値を書かないでください:\n" + "\n".join(bad)


def test_明るい地の色は暗い地にも定義がある():
    """片方だけ定義すると、もう片方で**地と字が同じ側に寄る**。

    「入力欄の文字が見えない」はこれだった ── 地は明るいまま、
    字だけが暗い地の色(白)に変わっていた。
    """
    text = _strip_comments((CSS_DIR / "tokens.css").read_text(encoding="utf-8"))

    # 3つの塊: 明(:root) / 暗(@media) / 暗(:root[data-theme="dark"])
    dark_media = re.search(
        r'@media \(prefers-color-scheme: dark\)\s*\{(.*?)\n\}', text, re.S)
    dark_attr = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\n\}', text, re.S)
    assert dark_media and dark_attr, "暗い地の定義が見つかりません"

    light_block = text[:dark_media.start()]
    light = {t for t, v in _declarations(light_block).items()
             if _is_literal_color(v)}
    media = _defined(dark_media.group(1))
    attr = _defined(dark_attr.group(1))

    # 暗い地で**上書きしないと決めた**色。
    #   帯   … アプリの顔なので明暗で変えない
    #   夜   … もともと暗い面(ログの表示)。明るい地でも暗いまま
    same_in_both = {t for t in light
                    if t.startswith(("--bar-", "--night"))}

    for label, block in (("@media", media), ('[data-theme="dark"]', attr)):
        missing = sorted(light - block - same_in_both)
        assert not missing, (
            f"暗い地({label})に定義が無い色があります。"
            f"明るい地の値のまま残ると読めなくなります:\n  "
            + "\n  ".join(missing))


def test_要入力の地は明暗どちらにもある():
    """現場から上がった「入力の文字色が見えない」の、そのものの検査。"""
    text = _strip_comments((CSS_DIR / "tokens.css").read_text(encoding="utf-8"))
    assert text.count("--need-bg:") >= 3, (
        "--need-bg は 明 / @media の暗 / [data-theme=dark] の暗 の"
        "3か所に要ります")
