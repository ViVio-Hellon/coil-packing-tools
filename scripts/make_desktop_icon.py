#!/usr/bin/env python3
"""デスクトップ版(Tauri)のアイコンを作る

窓・タスクバー・exe に出るアイコン。**統合画面のタブの印(base.html の favicon)と同じ**
ティールの角丸に白の「梱」。python-web-tools(梱包資材総合ツール)は紺なので、
タスクバーに並んでも色で見分けられる。
作り直すときだけ流す(作ったものはリポジトリに入れてある)。

    python scripts/make_desktop_icon.py

要るもの: Pillow と日本語のフォント(`--font` で指定)。開発機だけ。
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src-tauri" / "icons"
TEAL_TOP = (11, 61, 74)          # 上の帯の色(shell.css の --bar-a)
TEAL_BOTTOM = (11, 93, 110)      # (--bar-b)


def draw(size: int, font_path: str):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # 上から下へ、帯と同じ2色を移す
    ground = Image.new("RGBA", (size, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(TEAL_TOP, TEAL_BOTTOM)) + (255,)
        ImageDraw.Draw(ground).line([(0, y), (size, y)], fill=color)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 6, fill=255)
    image.paste(ground, (0, 0), mask)
    font = ImageFont.truetype(font_path, int(size * 0.66))
    ImageDraw.Draw(image).text((size / 2, size / 2 + size * 0.02), "梱", font=font,
                               fill="white", anchor="mm")
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", default="/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw(512, args.font)
    big.save(OUT / "icon.png")
    for size in (32, 128):
        draw(size, args.font).save(OUT / f"{size}x{size}.png")
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                      (64, 64), (128, 128), (256, 256)])
    print(f"作りました: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
