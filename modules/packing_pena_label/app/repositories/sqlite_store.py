# -*- coding: utf-8 -*-
"""SQLite による状態保存。VBA が Excel シートへ持っていた状態を置き換える。

VBA -> DB 対応
    14 枚のラベル台紙シート        -> sheet_state（1 行 = 1 台紙）
    台紙のセル                      -> sheet_cell（(ob_idx, row, col) -> value）
    風袋計算シート                  -> tare_result（1 行 = 1 梱包）
    50まで シート                   -> list_result（JSON 1 件）
    フォームのコントロール状態      -> form_state（JSON 1 件）

== 接続の方針（長時間起動・ファイルハンドル対策）==

この DB は **1 台の PC の 1 利用者が使うローカル状態** であり、
共有フォルダーへ置いて複数 PC から書き込む用途ではない（docs/04 参照）。
そのため次の方針を採る。

1. **接続はプロセス全体で 1 本だけ**。全操作をロックで直列化する。
   （以前はスレッドごとに接続を作っていたため、リクエストスレッドが増えるたびに
     接続とファイルハンドルが積み上がり、GC 任せでしか閉じられなかった）
2. **無操作が続いたら接続を自動的に閉じる**。ツールを起動したまま放置しても
   DB ファイルのハンドルを掴み続けない（バックアップ・ウイルス対策ソフト・
   フォルダー削除を邪魔しない）。次の操作で自動的に開き直す。
3. **journal_mode は DELETE**。WAL は `-wal` / `-shm` を伴い、
   ネットワーク上のファイルシステムでは動作保証がない。
   本アプリの書込量はごく小さく WAL の利点がないため、
   ファイルが 1 つで済む DELETE を選ぶ。
4. `database is locked` に備えて **busy_timeout と再試行** を入れる。
   （同時アクセスを想定しているからではなく、Windows でバックアップや
     ウイルス対策が一瞬ロックする場合があるため）
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: 無操作でこの秒数が過ぎたら接続を閉じる（0 で無効）
DEFAULT_IDLE_CLOSE_SEC = 120
#: SQLite が他プロセスのロック解放を待つ時間
DEFAULT_BUSY_TIMEOUT_MS = 15_000
#: `database is locked` で失敗したときの再試行回数
LOCK_RETRY = 3
LOCK_RETRY_WAIT_SEC = 0.4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sheet_state (
    ob_idx      INTEGER PRIMARY KEY,
    kensa_no    TEXT NOT NULL DEFAULT '',
    weight      TEXT NOT NULL DEFAULT '',
    kataban     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS sheet_cell (
    ob_idx  INTEGER NOT NULL,
    row     INTEGER NOT NULL,
    col     INTEGER NOT NULL,
    value   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (ob_idx, row, col)
);

CREATE TABLE IF NOT EXISTS tare_result (
    coil_no     INTEGER PRIMARY KEY,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS kv_store (
    key         TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def is_network_path(path: str) -> bool:
    """UNC（``\\\\server\\share``）またはネットワークドライブらしいか。

    共有フォルダー上の SQLite は「複数 PC から同時に書く」用途では
    使ってはいけないため、検出したら警告を出す。
    """
    raw = str(path or "")
    # UNC は正規化前の文字列で判定する。
    # （Linux 上では abspath がバックスラッシュを区切りとみなさず、
    #   UNC プレフィックスが消えてしまうため）
    if raw.startswith("\\\\") or raw.startswith("//"):
        return True
    p = os.path.abspath(raw)
    if os.name == "nt" and len(p) >= 2 and p[1] == ":":
        try:
            import ctypes
            # DRIVE_REMOTE = 4
            return ctypes.windll.kernel32.GetDriveTypeW(p[:3]) == 4
        except Exception:
            return False
    return False


class Store:
    """アプリの状態ストア。

    接続は 1 本だけ持ち、全操作を 1 つのロックで直列化する。
    書込量が小さく単一利用者のため、直列化による性能上の問題はない。
    """

    def __init__(self, db_path: str,
                 idle_close_sec: int = DEFAULT_IDLE_CLOSE_SEC,
                 busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS):
        self.db_path = db_path
        self.idle_close_sec = int(idle_close_sec or 0)
        self.busy_timeout_ms = int(busy_timeout_ms)

        d = os.path.dirname(os.path.abspath(db_path))
        if d:
            os.makedirs(d, exist_ok=True)

        if is_network_path(db_path):
            log.warning(
                "状態DB がネットワーク上にあります: %s / "
                "SQLite を共有フォルダーで複数 PC から使う運用は想定していません。"
                "ローカル領域(%%LOCALAPPDATA%%)へ置いてください。", db_path)

        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._last_used = 0.0
        self._shutdown = threading.Event()
        self._janitor: Optional[threading.Thread] = None

        self._exec_script(_SCHEMA)

        if self.idle_close_sec > 0:
            self._janitor = threading.Thread(
                target=self._janitor_loop, name="store-idle-close", daemon=True)
            self._janitor.start()

    # ============================================================ 接続
    def _open(self) -> sqlite3.Connection:
        """接続を開く（ロック内から呼ぶこと）。"""
        c = sqlite3.connect(
            self.db_path,
            timeout=self.busy_timeout_ms / 1000.0,
            check_same_thread=False,          # 1本をロックで直列化して共有する
            isolation_level="DEFERRED",
        )
        c.row_factory = sqlite3.Row
        # WAL は使わない（-wal/-shm を作らず、ネットワーク FS でも壊れにくい）
        c.execute("PRAGMA journal_mode=DELETE")
        c.execute("PRAGMA synchronous=FULL")
        c.execute("PRAGMA busy_timeout=%d" % self.busy_timeout_ms)
        c.execute("PRAGMA foreign_keys=ON")
        log.debug("状態DB を開きました: %s", self.db_path)
        return c

    def _get(self) -> sqlite3.Connection:
        """接続を取得（無ければ開く）。ロック内から呼ぶこと。"""
        if self._conn is None:
            self._conn = self._open()
        # 経過時間は **進むだけの時計** で測る。
        # time.time() は NTP 補正や時刻変更で飛ぶため、
        # アイドル解放が即発火したり永遠に来なかったりする。
        self._last_used = time.monotonic()
        return self._conn

    def _close_locked(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error as exc:
                log.warning("状態DB を閉じる際にエラー: %s", exc)
            finally:
                self._conn = None
                log.debug("状態DB を閉じました（アイドル解放）")

    def _janitor_loop(self) -> None:
        """無操作が続いたら接続を閉じ、DB ファイルのハンドルを解放する。"""
        interval = max(5, min(30, self.idle_close_sec // 2 or 5))
        while not self._shutdown.wait(interval):
            with self._lock:
                if self._conn is None:
                    continue
                if time.monotonic() - self._last_used >= self.idle_close_sec:
                    self._close_locked()

    def set_idle_close_sec(self, sec: int) -> None:
        """アイドル解放までの秒数を変更する（再起動せずに反映）。

        監視スレッドの確認間隔は起動時のままなので、
        実際に閉じるのは最大で確認間隔ぶん遅れる。
        """
        with self._lock:
            self.idle_close_sec = int(sec or 0)

    def close(self) -> None:
        """接続を閉じ、アイドル監視も止める。"""
        self._shutdown.set()
        with self._lock:
            self._close_locked()
        j = self._janitor
        if j is not None and j.is_alive() and j is not threading.current_thread():
            j.join(timeout=2)

    # ------------------------------------------------------------ 実行
    @staticmethod
    def _rollback(conn) -> None:
        """失敗した操作の書込を必ず捨てる。

        これを怠ると、失敗した操作が書いた途中までの内容が接続に残り、
        **次の無関係な操作の commit で一緒に確定してしまう**。
        （例: 4 本分の計算の 2 本目で失敗 → そこまでの値が
        あとから別の操作で確定し、中途半端な結果が残る）
        """
        if conn is None:
            return
        try:
            conn.rollback()
        except sqlite3.Error:
            pass

    def _run(self, fn, write: bool):
        """1 操作をロック内で実行し、ロック衝突時だけ再試行する。

        **1 回の呼び出し = 1 トランザクション**。
        成功すれば commit、失敗すれば rollback。途中の状態は残さない。
        """
        last = None
        for attempt in range(LOCK_RETRY):
            with self._lock:
                c = None
                try:
                    c = self._get()
                    result = fn(c)
                    if write:
                        c.commit()
                    return result
                except sqlite3.OperationalError as exc:
                    msg = str(exc).lower()
                    self._rollback(c)
                    if "locked" not in msg and "busy" not in msg:
                        raise
                    last = exc
                    # ロックが壊れた接続を捨てて開き直す
                    self._close_locked()
                except BaseException:
                    # ロック以外の失敗（制約違反・プログラムの誤り・中断）でも
                    # 途中まで書いた分を残さない
                    self._rollback(c)
                    raise
            log.warning("状態DB がロックされています（%d/%d 回目）: %s",
                        attempt + 1, LOCK_RETRY, last)
            time.sleep(LOCK_RETRY_WAIT_SEC * (attempt + 1))
        raise sqlite3.OperationalError(
            "状態DB にアクセスできません（ロックが解放されませんでした）: %s" % last)

    def _exec_script(self, script: str) -> None:
        self._run(lambda c: c.executescript(script), write=True)

    def _query(self, sql: str, args=()):
        return self._run(lambda c: c.execute(sql, args).fetchall(), write=False)

    def _query_one(self, sql: str, args=()):
        return self._run(lambda c: c.execute(sql, args).fetchone(), write=False)

    def _write(self, sql: str, args=()) -> None:
        self._run(lambda c: c.execute(sql, args), write=True)

    def _write_many(self, sql: str, rows) -> None:
        self._run(lambda c: c.executemany(sql, rows), write=True)

    # ------------------------------------------------------------ 診断
    def stats(self) -> Dict[str, object]:
        """接続状態（/diag 画面と運用確認用）。"""
        with self._lock:
            open_now = self._conn is not None
            idle = (time.monotonic() - self._last_used) if self._last_used else None
        size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        return {
            "path": self.db_path,
            "open": open_now,
            "idleSec": round(idle, 1) if idle is not None else None,
            "idleCloseSec": self.idle_close_sec,
            "busyTimeoutMs": self.busy_timeout_ms,
            "journalMode": "DELETE",
            "sizeBytes": size,
            "networkPath": is_network_path(self.db_path),
        }

    # ============================================================ 台紙
    def set_sheet_header(self, ob_idx: int, kensa_no: str, weight: str,
                         kataban: str) -> None:
        self._write(
            "INSERT INTO sheet_state (ob_idx, kensa_no, weight, kataban) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(ob_idx) DO UPDATE SET "
            "kensa_no=excluded.kensa_no, weight=excluded.weight, "
            "kataban=excluded.kataban, updated_at=datetime('now','localtime')",
            (int(ob_idx), kensa_no, weight, kataban))

    def get_sheet_header(self, ob_idx: int) -> Dict[str, str]:
        r = self._query_one(
            "SELECT kensa_no, weight, kataban, updated_at "
            "FROM sheet_state WHERE ob_idx=?", (int(ob_idx),))
        if not r:
            return {"kensaNo": "", "weight": "", "kataban": "", "updatedAt": ""}
        return {"kensaNo": r["kensa_no"], "weight": r["weight"],
                "kataban": r["kataban"], "updatedAt": r["updated_at"]}

    def put_cells(self, ob_idx: int, cells: Dict[Tuple[int, int], str]) -> None:
        if not cells:
            return
        self._write_many(
            "INSERT INTO sheet_cell (ob_idx,row,col,value) VALUES (?,?,?,?) "
            "ON CONFLICT(ob_idx,row,col) DO UPDATE SET value=excluded.value",
            [(int(ob_idx), int(r), int(col), "" if v is None else str(v))
             for (r, col), v in cells.items()])

    def clear_cells(self, ob_idx: int,
                    targets: Optional[List[Tuple[int, int]]] = None) -> None:
        """VBA ``ClearCoilCells`` / シート全消し。"""
        if targets is None:
            self._write("DELETE FROM sheet_cell WHERE ob_idx=?", (int(ob_idx),))
        else:
            self._write_many(
                "DELETE FROM sheet_cell WHERE ob_idx=? AND row=? AND col=?",
                [(int(ob_idx), int(r), int(col)) for (r, col) in targets])

    def get_cell(self, ob_idx: int, row: int, col: int) -> str:
        r = self._query_one(
            "SELECT value FROM sheet_cell WHERE ob_idx=? AND row=? AND col=?",
            (int(ob_idx), int(row), int(col)))
        return r["value"] if r else ""

    def get_cells(self, ob_idx: int) -> Dict[Tuple[int, int], str]:
        rows = self._query(
            "SELECT row, col, value FROM sheet_cell WHERE ob_idx=? "
            "ORDER BY row, col", (int(ob_idx),))
        return {(r["row"], r["col"]): r["value"] for r in rows}

    # ============================================================ 風袋
    def set_tare(self, coil_no: int, payload: dict) -> None:
        self._write(
            "INSERT INTO tare_result (coil_no, payload) VALUES (?,?) "
            "ON CONFLICT(coil_no) DO UPDATE SET payload=excluded.payload, "
            "updated_at=datetime('now','localtime')",
            (int(coil_no), json.dumps(payload, ensure_ascii=False)))

    def get_tare(self, coil_no: int) -> Optional[dict]:
        r = self._query_one(
            "SELECT payload FROM tare_result WHERE coil_no=?", (int(coil_no),))
        return json.loads(r["payload"]) if r else None

    def all_tare(self) -> Dict[int, dict]:
        rows = self._query(
            "SELECT coil_no, payload FROM tare_result ORDER BY coil_no")
        return {r["coil_no"]: json.loads(r["payload"]) for r in rows}

    def clear_tare(self) -> None:
        """VBA ``RecreateSheet("風袋計算")`` 相当（毎回作り直し）。"""
        self._write("DELETE FROM tare_result")

    # ============================================================ KV
    def set_kv(self, key: str, payload) -> None:
        self._write(
            "INSERT INTO kv_store (key,payload) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, "
            "updated_at=datetime('now','localtime')",
            (key, json.dumps(payload, ensure_ascii=False)))

    def get_kv(self, key: str, default=None):
        r = self._query_one("SELECT payload FROM kv_store WHERE key=?", (key,))
        if not r:
            return default
        try:
            return json.loads(r["payload"])
        except json.JSONDecodeError:
            return default

    def del_kv(self, key: str) -> None:
        self._write("DELETE FROM kv_store WHERE key=?", (key,))
