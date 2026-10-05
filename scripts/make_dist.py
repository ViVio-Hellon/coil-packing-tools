#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""配布用フォルダを作る（python-web-tools の scripts/make_dist.py と同じ使い方）

統合版: 統合アプリのフォルダ一式（3機能ぶん）を配る。中身は
modules/packing_pena_label/app/services/dist_folder.py（ペナラベルの設定画面の
「配布用フォルダを作る」と同じもの）。

    python scripts\make_dist.py                        # アプリの隣に「nlm.coil-packing-tools_v版」
    python scripts\make_dist.py --out D:\配布\今回      # 置き場所を指定
    python scripts\make_dist.py --zip                  # zip も作る
    python scripts\make_dist.py --no-settings          # 配布設定を入れない
    python scripts\make_dist.py --exe D:\受け取り\CoilPackingTools.exe
                                                       # 入れるデスクトップ版の exe を指定

デスクトップ版の exe（GitHub Actions の「デスクトップ版(Windows)」で作る
CoilPackingTools.exe）は、アプリの直下か src-tauri\target\release にあれば
「コイル梱包ツール.exe」という名前で入れる。無くてもブラウザ版（Start.vbs）で動く。

ペナラベルの設定画面の「配布設定」からも同じことができる。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
def main(argv=None) -> int:
    from modules.packing_pena_label.app.services import dist_folder as DF

    parser = argparse.ArgumentParser(description="配布用フォルダを作る")
    parser.add_argument("--out", help="作る場所（既定: アプリの隣に nlm.coil-packing-tools_v版）")
    parser.add_argument("--no-settings", action="store_true",
                        help="配布設定（アプリ直下の 配布設定 フォルダ）を入れない")
    parser.add_argument("--force", action="store_true",
                        help="前に作った配布用フォルダがあれば消して作り直す")
    parser.add_argument("--zip", action="store_true", help="zip も作る")
    parser.add_argument("--exe", help="入れるデスクトップ版の exe"
                                      "（既定: 直下の CoilPackingTools.exe など）")
    args = parser.parse_args(argv)

    out = Path(os.path.abspath(args.out)) if args.out else None
    try:
        r = DF.build(out, with_settings=not args.no_settings,
                     force=args.force, make_zip=args.zip,
                     exe=Path(os.path.abspath(args.exe)) if args.exe else None)
    except DF.BuildRefused as exc:
        print(exc, file=sys.stderr)
        return 1
    print("\n".join(r.lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
