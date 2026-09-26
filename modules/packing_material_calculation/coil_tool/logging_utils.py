"""ログ出力 ── 統合版では `common/logging_utils.py` への取り次ぎ

移植元はこの機能だけのファイル(`coil_tool_YYYYMMDD.log`)に書いていた。
統合版は3機能が1つのプロセスなので、**1つのファイル**
(`%LOCALAPPDATA%\\CoilPackingTools\\logs\\coil_packing_tools_YYYYMMDD.log`)に
書き、ロガーの名前(`coil_tool.*`)で機能を見分ける。理由は共通側に書いてある。

呼び手(この機能の中)は移植元と同じ `get_logger(name)` で使える。
"""
from __future__ import annotations

import logging

from common import logging_utils as _common

ROOT_NAME = "coil_tool"

DailyFileHandler = _common.DailyFileHandler
log_path_for = _common.log_path_for


def configure_logging() -> None:
    """アプリ起動時に一度だけ呼ぶ。二重呼び出しは無害(冪等)。"""
    _common.configure_logging()


def get_logger(name: str = ROOT_NAME) -> logging.Logger:
    """モジュール別ロガーを取得する(未初期化なら自動で初期化する)。"""
    return _common.get_logger(ROOT_NAME, name)


def silence_console() -> None:
    """コンソールへのログ出力だけを止める(ファイルへの出力は残す)。"""
    _common.silence_console()
