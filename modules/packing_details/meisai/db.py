"""手元DBの接続とスキーマ適用

汎用のSQLite操作(リトライ付き実行・読み取り・ロック判定)は
`sqlite_toolkit` にある。このファイルに残るのは**このアプリ固有の事情**
── DBファイルをどこに置くか、`schema.sql` をどう当てるか ── だけ。
`fetch_all` などはここから再エクスポートしているので、呼び出し側は
`db.fetch_all(...)` で使える。

【接続は要求ごとに開いて閉じる】
waitress はスレッドプールで動くので、`sqlite3` の接続をプロセス全体で
共有できない(既定で `check_same_thread=True`)。常駐接続を持たない
ことは、長時間放置してもファイルハンドルやロックが滞留しない、という
効果も兼ねる。
"""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

from . import config, sqlite_toolkit
from .logging_utils import get_logger

log = get_logger("db")

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# 手元DBでロックを待つ時間(ミリ秒)。
#
# **短くしてある。** 手元DBは端末ごとで、書く要求はプロセス内で
# 1つずつ通している(`app/__init__._WRITE_LOCK`)ので、外から握られる
# こと自体が異常 ── バックアップ・ウイルス対策・共有フォルダへ置いた、
# のいずれか。待っても直らないものを長く待つと、**画面が無言で
# 固まるだけ**になる。
#
# 取り込み元(共有の上)は上流が書いている最中に当たるのが普通なので、
# あちらは `source_db.BUSY_TIMEOUT_MS` で別に長く待つ。
BUSY_TIMEOUT_MS = 4000

# トランザクションごとのやり直し回数と待ち。
# 最悪 4.0 + 0.5 + 4.0 + 1.0 + 4.0 = 13.5秒 で返す
TX_MAX_RETRY = 2
TX_RETRY_WAIT_SEC = 0.5


class WriteError(RuntimeError):
    """書き込めなかった。**黙って無かったことにしない。**

    呼び出し側が結果を見ない書き方(`execute_with_retry` の戻り値を
    捨てる)だと、ロックに当たって1行も書けていないのに画面には
    「出力しました」と出る。実際にそうなっていた。
    """

# `sqlite_toolkit` から再エクスポート(呼び出し側を書き換えないため)
execute_with_retry = sqlite_toolkit.execute_with_retry
fetch_all = sqlite_toolkit.fetch_all
fetch_one = sqlite_toolkit.fetch_one
update_record = sqlite_toolkit.update_record
insert_record = sqlite_toolkit.insert_record
is_lock_error = sqlite_toolkit.is_lock_error
now_db_string = sqlite_toolkit.now_db_string
sanitize_for_db = sqlite_toolkit.sanitize_for_db


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """SQLite接続を1本開いて返す。

    ロック待ち(`busy_timeout`)を設定して短時間の競合は自動で吸収する。
    VBA版の `ConnectionTimeout = 10秒` に相当。
    """
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    # isolation_level は既定("" = DEFERRED)のまま。これにより
    # INSERT/UPDATE/DELETE の前で暗黙にトランザクションが始まり、
    # `with conn:` の終了時に commit/rollback される。
    # `isolation_level=None`(autocommit)にすると `with conn:` が
    # 何もまとめなくなり、**複数文をまたぐ原子性が失われる**
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    sqlite_toolkit.enable_wal(conn)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection, *,
                name: str = "書き込み") -> Iterator[sqlite3.Connection]:
    """**ひとまとまりの書き込み。** 全部書けるか、1行も書かないか。

    中では素の `conn.execute` を使う ── `execute_with_retry` は自前で
    commit してしまうので、途中まで書けた状態が残りうる。

    ロックに当たったら**まとまりごと**やり直す。文ごとにやり直すと、
    文の数だけ待ち時間が積み上がる(実測で1操作 90秒超)。

    入れ子で呼べる。内側は外側のまとまりに乗るだけで、そこでは
    commit しない ── 内側が commit すると、外側が失敗したときに
    戻せなくなる。
    """
    # `sqlite3.Connection` に印は付けられない(属性を生やせない)ので、
    # sqlite 自身が持っている「いま取引中か」を見る。
    # `SELECT` では始まらないので、読み取りだけの内側を取り違えない
    if conn.in_transaction:
        yield conn                      # 外側のまとまりに乗る
        return

    attempt = 0
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.Error as exc:
            if not is_lock_error(exc) or attempt >= TX_MAX_RETRY:
                log.warning("%s: 書き込めません: %s", name, exc)
                raise WriteError(
                    "データベースに書き込めませんでした。"
                    "ほかのプログラムが使っているかもしれません。"
                    "少し待ってからもう一度お試しください。") from exc
            attempt += 1
            time.sleep(TX_RETRY_WAIT_SEC * attempt)

    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        try:
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            log.warning("%s: 確定できません: %s", name, exc)
            raise WriteError(
                "データベースに書き込めませんでした。"
                "ほかのプログラムが使っているかもしれません。") from exc


def write(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = (), *,
          name: str = "書き込み") -> int:
    """1文書く。**書けなければ例外。** 影響行数を返す。

    `execute_with_retry` との違いは戻り値を捨てられないこと。
    業務の記録はすべてこちらを使う。
    """
    with transaction(conn, name=name) as c:
        try:
            return c.execute(sql, params).rowcount
        except sqlite3.Error as exc:
            log.warning("%s: %s SQL=%s", name, exc, sql)
            if is_lock_error(exc):
                raise WriteError(
                    "データベースに書き込めませんでした。"
                    "ほかのプログラムが使っているかもしれません。") from exc
            raise WriteError(f"書き込めませんでした: {exc}") from exc


@contextmanager
def connect(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """`with` で開いて必ず閉じる。"""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def apply_schema(conn: sqlite3.Connection) -> None:
    """`schema.sql` を当てる。何度当てても同じ結果になる。

    **形が変わった表は先に片付ける。** `CREATE TABLE IF NOT EXISTS` は
    すでにある表には何もしないので、列を足しただけでは古いDBに
    追いつかない(そのまま動かすと「そんな列は無い」で落ちる)。
    """
    _migrate_before_schema(conn)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    _add_missing_columns(conn)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """その表の列名。表が無ければ空。"""
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.Error:
        return set()
    return {row[1] for row in rows}


# 作り直してよい表と、その表が持っているべき列。
#
# **どちらも作り直して困らないものだけ。** `取り込み記録` は鮮度の
# 控えなので、消しても次の取り込みで埋まる(1回だけ余分に読み直す)。
# 業務の記録(`副番履歴` `明細出力` `明細スナップショット`)はここに
# 入れない ── 作り直したら現場の作業が消える。列を足すときは
# `ALTER TABLE` で足す。
_REBUILDABLE: dict[str, str] = {
    "取り込み記録": "テーブル",
}


# 後から足した列。**業務の記録の表は作り直せない**ので、`ALTER TABLE`
# で足す。既にあれば何もしない(何度当てても同じ結果)。
#
#     (表, 列, 型と既定値)
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("明細出力", "手入力サイズ", "TEXT NOT NULL DEFAULT ''"),
    ("明細出力", "手入力ロット番号", "TEXT NOT NULL DEFAULT ''"),
    ("明細出力", "履歴ID", "TEXT NOT NULL DEFAULT ''"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    """古いDBに、後から足した列を足す。**行は1つも消さない。**"""
    for table, column, decl in _ADDED_COLUMNS:
        if column in _columns(conn, table):
            continue
        log.info("%s に列 %s を足します", table, column)
        with conn:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {decl}')


def _migrate_before_schema(conn: sqlite3.Connection) -> None:
    """古い形の表を片付ける。"""
    for table, needed in _REBUILDABLE.items():
        columns = _columns(conn, table)
        if columns and needed not in columns:
            log.info("%s の形が古いので作り直します(控えなので消してよい)", table)
            with conn:
                conn.execute(f'DROP TABLE "{table}"')


# ------------------------------------------------------------------
# 出力状態 (1行しか無い表への読み書き)
# ------------------------------------------------------------------
def state_row(conn: sqlite3.Connection) -> sqlite3.Row:
    """`明細出力状態` の唯一の行。無ければ作ってから返す。

    スキーマ適用時に `INSERT OR IGNORE` で1行入れているが、古いDBを
    開いた場合に備えてここでも確かめる ── **無いときに None を返すと、
    呼び出し側すべてが None を気にすることになる。**
    """
    row = fetch_one(conn, "SELECT * FROM 明細出力状態 WHERE ID = 1",
                    caller_name="db.state_row")
    if row is None:
        write(conn,
              "INSERT OR IGNORE INTO 明細出力状態 (ID, 現在ロット番号, 現在連番, 印刷済)"
              " VALUES (1, '', 0, 1)", name="出力状態の作成")
        row = fetch_one(conn, "SELECT * FROM 明細出力状態 WHERE ID = 1",
                        caller_name="db.state_row")
    return row


def set_state(conn: sqlite3.Connection, **values: Any) -> None:
    """`明細出力状態` の列を書き換える。列名はこの表にあるものだけ。"""
    allowed = {"現在ロット番号", "現在連番", "印刷済"}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"明細出力状態に無い列です: {', '.join(sorted(unknown))}")
    if not values:
        return
    with transaction(conn, name="出力状態の更新"):
        state_row(conn)                  # 行があることを確かめてから書く
        sets = ", ".join(f'"{k}" = ?' for k in values)
        write(conn, f"UPDATE 明細出力状態 SET {sets} WHERE ID = 1",
              tuple(values.values()), name="出力状態の更新")
