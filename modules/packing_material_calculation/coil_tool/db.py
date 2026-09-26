"""DB共通アクセス層 (VBA の ADO ラッパー群の移植)

対応関係の目安:

    VBA                              -> Python
    -----------------------------------------------------------------
    adoConnection()                  -> get_connection()
    GetRecordsArr() / GetFieldsArr()  -> fetch_all() / 取り込み元は source_db
    ExecuteSQLWithRetry()             -> execute_with_retry()
    IsLockError()                     -> sqlite_toolkit.is_lock_error()
    BuildSQLLiteral()                 -> 不要(`?` プレースホルダが型に応じて
                                         束縛するので、SQL文字列を手で組む
                                         必要が無い)

【sqlite_toolkit との役割分担】
テーブル名やスキーマに依存しない SQLite 操作(リトライ付き実行・読み取り・
簡易CRUD・ロック判定)は `sqlite_toolkit` にある。こちらに残すのは
「このツールの DB をどこに置くか」「`schema.sql` の適用」だけ。

【接続は持ち回さない】
`sqlite3` の接続はスレッドをまたげないので、**要求ごとに取り直す**。
画面を開いたまま放置されてもファイルハンドルもロックも残らない。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

from . import config, sqlite_toolkit
from .logging_utils import get_logger
from .sqlite_toolkit import ExecResult, UpdateResult, now_db_string, sanitize_for_db

log = get_logger("db")

__all__ = [
    "get_connection", "connect", "apply_schema",
    "execute_with_retry", "fetch_all", "fetch_one",
    "update_record", "insert_record",
    "now_db_string", "sanitize_for_db",
    "ExecResult", "UpdateResult",
]


# ------------------------------------------------------------------
# 接続
# ------------------------------------------------------------------
def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """SQLite 接続を1本開いて返す。

    共有フォルダ上の .db を複数端末から開く運用を想定して、ロック待ち
    (`busy_timeout`)を入れてある。短い競合はここで吸収される。
    """
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    # isolation_level は既定("" = DEFERRED)のまま。これで
    # INSERT/UPDATE/DELETE の前に暗黙のトランザクションが始まり、
    # `with conn:` の終わりで commit/rollback される。
    # None(autocommit)にすると `with conn:` が何もまとめなくなり、
    # 複数文をまたぐ原子性が失われる
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")  # ms
    sqlite_toolkit.enable_wal(conn)
    return conn


@contextmanager
def connect(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """`with db.connect() as conn:` の形で使う。"""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def apply_schema(conn: sqlite3.Connection) -> None:
    """`schema.sql` を適用する(何度呼んでも同じ結果)。"""
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    conn.executescript(schema_path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# 再エクスポート(呼び出し元は `db.fetch_all(...)` の形で使える)
# ------------------------------------------------------------------
def execute_with_retry(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] = (),
    *,
    caller_name: str = "SQL",
    max_retry: int = config.MAX_RETRY,
) -> ExecResult:
    return sqlite_toolkit.execute_with_retry(
        conn, sql, params, caller_name=caller_name,
        max_retry=max_retry, retry_wait_sec=config.RETRY_BASE_WAIT_SEC)


def fetch_all(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] = (),
    *,
    caller_name: str = "SELECT",
    max_retry: int = config.MAX_RETRY,
) -> Optional[list[sqlite3.Row]]:
    return sqlite_toolkit.fetch_all(
        conn, sql, params, caller_name=caller_name,
        max_retry=max_retry, retry_wait_sec=config.RETRY_BASE_WAIT_SEC)


def fetch_one(
    conn: sqlite3.Connection, sql: str, params: Sequence[Any] = (),
    *, caller_name: str = "SELECT",
) -> Optional[sqlite3.Row]:
    return sqlite_toolkit.fetch_one(
        conn, sql, params, caller_name=caller_name,
        retry_wait_sec=config.RETRY_BASE_WAIT_SEC)


def update_record(
    conn: sqlite3.Connection,
    table: str,
    key_field: str,
    key_value: Any,
    values: Mapping[str, Any],
    *,
    where_clause: Optional[str] = None,
    where_params: Sequence[Any] = (),
    max_retry: int = config.MAX_RETRY,
) -> UpdateResult:
    return sqlite_toolkit.update_record(
        conn, table, key_field, key_value, values,
        where_clause=where_clause, where_params=where_params,
        max_retry=max_retry, retry_wait_sec=config.RETRY_BASE_WAIT_SEC)


def insert_record(
    conn: sqlite3.Connection, table: str, values: Mapping[str, Any],
    *, caller_name: Optional[str] = None,
) -> ExecResult:
    return sqlite_toolkit.insert_record(
        conn, table, values, caller_name=caller_name,
        retry_wait_sec=config.RETRY_BASE_WAIT_SEC)
