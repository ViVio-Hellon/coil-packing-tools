#!/usr/bin/env python3
"""起動の入口(`start_app.py` と同じもの)

推奨構成に合わせて置いてある名前。中身は `start_app.main` をそのまま呼ぶだけ。
`Start.vbs` / `start.bat` は `start_app.py` を呼ぶ(移植元3つと同じ名前なので、
現場の手順書がそのまま通る)。

    python run.py                  ふつうに開く
    python run.py --no-browser     ブラウザを開かない(検証用)
    python run.py --check          環境の確認だけして終わる
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from start_app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
