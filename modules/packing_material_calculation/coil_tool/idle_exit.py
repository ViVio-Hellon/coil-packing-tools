"""画面が居なくなったら終わる ── 統合版では `common/idle_exit.py` そのもの

**見張りはプロセスに1つ。** 3機能の心拍はどれも同じ見張りへ届く。
このファイルは名前を残すためだけにあり、読み込むと共通側のモジュール
そのものに置き換わる(`sys.modules` の差し替え)。移植元と同じ呼び方
(`idle_exit.install` / `idle_exit.get` / `idle_exit.HEARTBEAT_MS` /
試験の `idle_exit._watch` の差し替え)がそのまま効く。
"""
from __future__ import annotations

import sys

from common import idle_exit as _common

sys.modules[__name__] = _common
