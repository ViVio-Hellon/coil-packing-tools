r"""統合アプリの設定(このPCに保存) ── 3機能に共通のもの

3機能の設定はそれぞれの機能が持つ(梱包明細・資材計算の `user_config.json`、
ペナラベルの `local.json`)。ここは**3機能で1つしかないもの**だけを持つ:

    log_dir        ログの出力先(空なら このPCの既定 = %LOCALAPPDATA%\CoilPackingTools\logs)
    log_keep_days  ログとエラーの記録を残す日数
    theme          画面の色(auto = ブラウザの外観に合わせる / light / dark。統合 1.0.16)

置き場所は `%LOCALAPPDATA%\CoilPackingTools\data\settings.json`(このPCで引き継ぐ。
アプリのフォルダを入れ替えても残る)。

**読み書きは1つずつ、別名で書いてから置き換える**(梱包明細の `user_settings` と同じ)。
途中で落ちても書きかけのファイルを残さない。壊れていても起動は止めない(既定で動く)。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from . import app_config

FILE_NAME = "settings.json"

KEY_LOG_DIR = "log_dir"
KEY_LOG_KEEP_DAYS = "log_keep_days"
KEY_THEME = "theme"

#: 画面の色。auto はブラウザの「外観」(prefers-color-scheme)に合わせる(これまでどおり)
THEMES = ("auto", "light", "dark")

#: ログとエラーの記録を残す日数の既定と、選べる幅。
#: なぜなぜ分析は「先月も同じことがあったか」を見ることが多いので、半年は残す
LOG_KEEP_DAYS_DEFAULT = 180
LOG_KEEP_DAYS_MIN = 14
LOG_KEEP_DAYS_MAX = 3650

_lock = threading.RLock()


def path() -> Path:
    override = os.environ.get("COIL_PACKING_TOOLS_SETTINGS_PATH")
    if override and override.strip():
        return Path(override.strip())
    return app_config.local_dir("data") / FILE_NAME


def load_all() -> dict[str, Any]:
    """全部読む。無い・壊れている・読めないときは空(既定で動く)。"""
    target = path()
    try:
        with _lock:
            if not target.exists():
                return {}
            data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # ここで get_logger は使わない(ログの設定を読む途中で呼ばれるため)
        logging.getLogger("coil_packing_tools.settings").warning(
            "統合アプリの設定を読めませんでした(既定で続けます): %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def get(key: str, default: Any = None) -> Any:
    return load_all().get(key, default)


def save(key: str, value: Any) -> None:
    """1つ書く。書けなければ `OSError`(画面に理由を出すため、握りつぶさない)。"""
    target = path()
    with _lock:
        data = load_all()
        if value is None or value == "":
            data.pop(key, None)
        else:
            data[key] = value
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)


def log_keep_days() -> int:
    """ログを残す日数。おかしな値は既定へ(手で直した設定ファイルでも止めない)。"""
    raw = get(KEY_LOG_KEEP_DAYS, LOG_KEEP_DAYS_DEFAULT)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return LOG_KEEP_DAYS_DEFAULT
    return min(max(days, LOG_KEEP_DAYS_MIN), LOG_KEEP_DAYS_MAX)


def theme() -> str:
    """画面の色(auto / light / dark)。おかしな値は auto(手で直した設定ファイルでも止めない)。"""
    value = get(KEY_THEME, "auto")
    return value if value in THEMES else "auto"
