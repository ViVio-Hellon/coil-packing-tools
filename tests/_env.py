"""統合テストの下ごしらえ ── 3機能の手元DB・設定・ログを使い捨ての場所へ

**本物の領域(`%LOCALAPPDATA%` / `~/.local`)を汚さない。** 3機能はそれぞれ
自分のローカル領域を環境変数で差し替えられる(移植元のまま)ので、
ここで全部を1つの一時フォルダの下へ向けてから import する。

import より前に環境変数を決める必要があるので、**このモジュールを最初に
読む**(`tests/__init__.py` が読む)。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_DIR = tempfile.TemporaryDirectory(prefix="coil-packing-tools-tests-")
ROOT = Path(_DIR.name)

os.environ.setdefault("COIL_PACKING_TOOLS_LOCAL_DIR", str(ROOT / "CoilPackingTools"))
os.environ.setdefault("COIL_PACKING_TOOLS_LOG_DIR", str(ROOT / "CoilPackingTools" / "logs"))
# 配布設定(共通: ログの出力先)。アプリのフォルダの 配布設定\common を汚さない
os.environ.setdefault("COIL_PACKING_TOOLS_DISTRIBUTION_DIR", str(ROOT / "common" / "配布設定"))
# 梱包明細・資材計算: `XDG_DATA_HOME`(非Windows のローカル領域)と個別の置き場所
os.environ.setdefault("XDG_DATA_HOME", str(ROOT / "xdg-data"))
os.environ.setdefault("PACKING_DETAILS_DB_PATH", str(ROOT / "details" / "packing_details.db"))
os.environ.setdefault("PACKING_DETAILS_CONFIG_PATH", str(ROOT / "details" / "user_config.json"))
os.environ.setdefault("PACKING_DETAILS_SHARED_DIR", str(ROOT / "details" / "share"))
os.environ.setdefault("PACKING_DETAILS_EXPORT_DIR", str(ROOT / "details" / "export"))
os.environ.setdefault("PACKING_DETAILS_DISTRIBUTION_DIR", str(ROOT / "details" / "配布設定"))
os.environ.setdefault("COIL_TOOL_DB_PATH", str(ROOT / "material" / "coil_tool.db"))
os.environ.setdefault("COIL_TOOL_CONFIG_PATH", str(ROOT / "material" / "user_config.json"))
os.environ.setdefault("COIL_TOOL_EXPORT_DIR", str(ROOT / "material" / "export"))
os.environ.setdefault("COIL_TOOL_DISTRIBUTION_DIR", str(ROOT / "material" / "配布設定"))
os.environ.setdefault("COIL_TOOL_MASTER_DB_DIR", str(ROOT / "material" / "master"))
os.environ.setdefault("COIL_TOOL_LOT_DB_DIR", str(ROOT / "material" / "lot"))
# ペナラベル: `XDG_STATE_HOME`(非Windows のローカル領域)。Access は試さない
os.environ.setdefault("XDG_STATE_HOME", str(ROOT / "xdg-state"))
os.environ.setdefault("PPL_PREFER_ACCESS", "0")
os.environ.setdefault("PACKING_PENA_DISTRIBUTION_DIR", str(ROOT / "pena" / "配布設定"))
