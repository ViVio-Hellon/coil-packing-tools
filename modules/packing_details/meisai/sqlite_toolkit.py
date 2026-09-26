"""SQLite の汎用操作 ── 統合版では `common/sqlite_toolkit.py` への取り次ぎ

移植元の梱包明細と資材計算で**1行も違わなかった**ので1つにした。
"""
from __future__ import annotations

from common.sqlite_toolkit import *  # noqa: F401,F403  (再エクスポート)
from common.sqlite_toolkit import (  # noqa: F401  (名前を明示)
    ExecResult, UpdateResult, enable_wal, execute_with_retry, fetch_all,
    fetch_one, insert_record, is_lock_error, now_db_string, sanitize_for_db,
    update_record,
)
