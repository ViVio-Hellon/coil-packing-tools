# -*- coding: utf-8 -*-
"""長時間起動・接続保持・ロック・マスタ鮮度の検証。

現場の指摘（ツールを起動したまま放置する／マスタが業務中に更新される／
DB がロックされる）に対する挙動を固定する。
"""

import gc
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

from modules.packing_pena_label.app.repositories.material_repo import MaterialRepository
from modules.packing_pena_label.app.repositories.sqlite_store import Store, is_network_path


def sqlite_conn_count() -> int:
    return sum(1 for o in gc.get_objects() if isinstance(o, sqlite3.Connection))


def _read_bytes(path):
    """中身を読んで、すぐ閉じる(Windows では開いたままのファイルは消せない)。"""
    with open(path, "rb") as f:
        return f.read()


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "state.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestConnectionLifetime(StoreTestBase):
    def test_single_connection_regardless_of_threads(self):
        """スレッドがいくつ来ても接続は 1 本に保たれる。

        以前はスレッドごとに接続を作っており、リクエストのたびに
        接続とファイルハンドルが積み上がっていた。
        """
        st = Store(self.db, idle_close_sec=0)
        try:
            base = sqlite_conn_count()

            def work(n):
                for i in range(20):
                    st.set_kv("k%d" % n, {"i": i})
                    st.get_kv("k%d" % n)

            threads = [threading.Thread(target=work, args=(n,)) for n in range(12)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            gc.collect()
            self.assertLessEqual(sqlite_conn_count() - base, 1,
                                 "接続がスレッド数ぶん増えている")
        finally:
            st.close()

    def test_concurrent_writes_do_not_lose_data(self):
        """同時書込でデータが消えない（直列化されている）。"""
        st = Store(self.db, idle_close_sec=0)
        try:
            def work(n):
                for i in range(25):
                    st.set_kv("t%02d_%02d" % (n, i), {"n": n, "i": i})

            threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            for n in range(8):
                for i in range(25):
                    self.assertEqual(st.get_kv("t%02d_%02d" % (n, i)),
                                     {"n": n, "i": i})
        finally:
            st.close()

    def test_close_releases_handle(self):
        st = Store(self.db, idle_close_sec=0)
        st.set_kv("a", 1)
        self.assertTrue(st.stats()["open"])
        st.close()
        self.assertFalse(st.stats()["open"])


class TestIdleRelease(StoreTestBase):
    def test_handle_released_when_idle_then_reopened(self):
        """放置中は DB ハンドルを離し、次の操作で自動的に開き直す。"""
        st = Store(self.db, idle_close_sec=1)
        try:
            st.set_kv("a", {"v": 1})
            self.assertTrue(st.stats()["open"])

            deadline = time.time() + 15
            while time.time() < deadline and st.stats()["open"]:
                time.sleep(0.3)
            self.assertFalse(st.stats()["open"], "アイドルでも接続が閉じられていない")

            # 閉じたあとも読み書きは透過的に続けられる
            self.assertEqual(st.get_kv("a"), {"v": 1})
            self.assertTrue(st.stats()["open"])
            st.set_kv("b", {"v": 2})
            self.assertEqual(st.get_kv("b"), {"v": 2})
        finally:
            st.close()

    def test_data_survives_idle_close(self):
        st = Store(self.db, idle_close_sec=1)
        try:
            st.put_cells(5, {(3, 22): "W111111", (3, 37): "10"})
            deadline = time.time() + 15
            while time.time() < deadline and st.stats()["open"]:
                time.sleep(0.3)
            self.assertEqual(st.get_cell(5, 3, 22), "W111111")
        finally:
            st.close()


class TestJournalAndFiles(StoreTestBase):
    def test_no_wal_sidecar_files(self):
        """WAL を使わないので -wal / -shm を作らない。

        共有フォルダーやバックアップでの取り扱いを単純にするため。
        """
        st = Store(self.db, idle_close_sec=0)
        try:
            st.set_kv("a", 1)
            names = os.listdir(self.tmp)
            self.assertIn("state.sqlite3", names)
            self.assertNotIn("state.sqlite3-wal", names)
            self.assertNotIn("state.sqlite3-shm", names)
        finally:
            st.close()

    def test_journal_mode_is_delete(self):
        st = Store(self.db, idle_close_sec=0)
        try:
            st.set_kv("a", 1)
            self.assertEqual(st.stats()["journalMode"], "DELETE")
        finally:
            st.close()

    def test_busy_timeout_is_set(self):
        st = Store(self.db, idle_close_sec=0, busy_timeout_ms=9000)
        try:
            self.assertEqual(st.stats()["busyTimeoutMs"], 9000)
        finally:
            st.close()


class TestNetworkPathDetection(unittest.TestCase):
    def test_unc_is_network(self):
        self.assertTrue(is_network_path(r"\\server\share\state.sqlite3"))

    def test_local_is_not_network(self):
        self.assertFalse(is_network_path(os.path.join(tempfile.gettempdir(),
                                                      "x.sqlite3")))


class TestSharedMasterIsReadOnly(unittest.TestCase):
    """共有される資材マスタには**絶対に書かない**（D-4 の前提）。

    複数 PC から同じ DB を触る運用があり得るとのことなので、
    「読むだけ」を保証しておく。読むだけなら SMB 上でも競合しない。
    書き込みが 1 箇所でも混ざると、その瞬間にロックの問題が始まる。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "梱包資材マスタ.sqlite3")
        conn = sqlite3.connect(self.db)
        conn.execute('CREATE TABLE "資材重量" '
                     '(管理番号 TEXT, 梱包資材名 TEXT, 単位質量 TEXT, 係数 TEXT)')
        conn.execute('INSERT INTO "資材重量" VALUES ("1","テスラピン","0.35","1")')
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _repo(self):
        return MaterialRepository("", "", prefer_access=False, refresh_sec=0,
                                  db_path=self.db)

    def test_reads_the_sqlite_master(self):
        self.assertEqual(self._repo().load().unit_of("テスラピン"),
                         ("0.35", "1"))

    def test_no_journal_files_are_left(self):
        """読むだけなので -wal / -shm / -journal を作らない。

        共有フォルダーでは、これらが残ること自体が他 PC の邪魔になる。
        """
        self._repo().load()
        left = [f for f in os.listdir(self.tmp) if f != os.path.basename(self.db)]
        self.assertEqual(left, [], "余計なファイルが残っている: %r" % left)

    def test_file_is_not_modified(self):
        """読んでも中身も更新時刻も変わらない。"""
        before = (os.stat(self.db).st_mtime_ns, os.stat(self.db).st_size,
                  _read_bytes(self.db))
        self._repo().load()
        after = (os.stat(self.db).st_mtime_ns, os.stat(self.db).st_size,
                 _read_bytes(self.db))
        self.assertEqual(before, after, "マスタが書き換わっている")

    def test_works_when_the_file_is_read_only(self):
        """共有側が読取専用で配られていても読める。"""
        os.chmod(self.db, 0o444)
        try:
            self.assertEqual(self._repo().load().unit_of("テスラピン"),
                             ("0.35", "1"))
        finally:
            os.chmod(self.db, 0o644)

    def test_another_process_can_read_at_the_same_time(self):
        """別 PC（＝別プロセス）が読んでいても読める。"""
        other = sqlite3.connect(self.db)
        try:
            other.execute('SELECT * FROM "資材重量"').fetchall()
            self.assertEqual(self._repo().load().unit_of("テスラピン"),
                             ("0.35", "1"))
        finally:
            other.close()

    def test_state_db_is_not_the_shared_master(self):
        """状態 DB は端末ごと。共有マスタとは別物であること。"""
        from modules.packing_pena_label.app.config import Config

        cfg = Config()
        cfg.material_db_file = self.db
        self.assertNotEqual(os.path.abspath(cfg.db_path),
                            os.path.abspath(self.db))
        self.assertIn(cfg.app_id, cfg.db_path)
        self.assertEqual(os.path.abspath(cfg.material_db_file),
                         os.path.abspath(self.db))


class TestMaterialFreshness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv = os.path.join(self.tmp, "m.csv")
        self._write("0.35")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, pin):
        with open(self.csv, "w", encoding="utf-8") as f:
            f.write("管理番号,梱包資材名,単位質量,係数\n")
            f.write("1,テスラピン,%s,1\n" % pin)

    def test_picks_up_master_update(self):
        """業務中にマスタが更新されたら自動で読み直す。"""
        repo = MaterialRepository("", self.csv, prefer_access=False, refresh_sec=0)
        self.assertEqual(repo.load().unit_of("テスラピン"), ("0.35", "1"))
        time.sleep(1.05)                      # mtime の解像度を跨ぐ
        self._write("0.99")
        self.assertEqual(repo.load().unit_of("テスラピン"), ("0.99", "1"))

    def test_refresh_sec_suppresses_stat(self):
        """rehresh_sec の間はチェックしない（共有フォルダーへの負荷を抑える）。"""
        repo = MaterialRepository("", self.csv, prefer_access=False,
                                  refresh_sec=3600)
        self.assertEqual(repo.load().unit_of("テスラピン"), ("0.35", "1"))
        time.sleep(1.05)
        self._write("0.99")
        self.assertEqual(repo.load().unit_of("テスラピン"), ("0.35", "1"))
        repo.refresh_sec = 0
        self.assertEqual(repo.load().unit_of("テスラピン"), ("0.99", "1"))

    def test_serves_last_known_when_source_unreachable(self):
        """取得元へ到達できなくなっても、直前の内容で業務を続けられる。"""
        repo = MaterialRepository("", self.csv, prefer_access=False, refresh_sec=0)
        repo.load()
        self.assertFalse(repo.serving_stale)
        os.remove(self.csv)
        table = repo.load()
        self.assertEqual(table.unit_of("テスラピン"), ("0.35", "1"))
        self.assertTrue(repo.serving_stale, "古い内容で動いていることが立っていない")
        self.assertEqual(repo.last_source, "csv", "取得元の表示が失われている")

    def test_raises_when_never_loaded_and_unreachable(self):
        from modules.packing_pena_label.app.repositories.access_bridge import AccessError
        repo = MaterialRepository("", os.path.join(self.tmp, "nope.csv"),
                                  prefer_access=False)
        with self.assertRaises(AccessError):
            repo.load()

    def test_clear_cache_resets_flags(self):
        repo = MaterialRepository("", self.csv, prefer_access=False, refresh_sec=0)
        repo.load()
        repo.clear_cache()
        info = repo.cache_info()
        self.assertFalse(info["cached"])
        self.assertFalse(info["servingStale"])


if __name__ == "__main__":
    unittest.main()


class TestSqliteMaterialSource(unittest.TestCase):
    """SQLite 版の梱包資材マスタ（Access からの移行先）。"""

    FIELDS = ["管理番号", "梱包資材名", "単位質量", "係数", "単位", "単位量", "備考"]
    ROWS = [
        ("1", "EX-DRY", "0.123", "1", "Kg", "個", ""),
        ("18", "テスラピン", "0.077", "1", "Kg", "個", ""),
        ("15", "チップボール1000ф", "0.379", "1", "Kg", "枚", ""),
        ("22", "樹脂パレット", "19", "1", "Kg", "個", ""),
    ]

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "梱包資材マスタ.sqlite3")
        conn = sqlite3.connect(self.db)
        cols = ", ".join('"%s"' % c for c in self.FIELDS)
        conn.execute('CREATE TABLE "資材重量" (%s)' % cols)
        conn.executemany(
            'INSERT INTO "資材重量" VALUES (%s)' % ",".join("?" * len(self.FIELDS)),
            self.ROWS)
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_sqlite(self):
        repo = MaterialRepository("", "", prefer_access=False, db_path=self.db)
        table = repo.load()
        self.assertEqual(repo.last_source, "sqlite")
        self.assertEqual(table.unit_of("テスラピン"), ("0.077", "1"))
        self.assertEqual(table.unit_of("樹脂パレット"), ("19", "1"))

    def test_cyrillic_material_name_matches(self):
        """チップボール名の ф は U+0444（キリル文字）。完全一致で引くため重要。"""
        from modules.packing_pena_label.app.repositories import material_repo as M
        repo = MaterialRepository("", "", prefer_access=False, db_path=self.db)
        table = repo.load()
        self.assertEqual(ord(M.MAT_TIP_1000[-1]), 0x0444)
        self.assertTrue(table.has(M.MAT_TIP_1000))
        self.assertEqual(table.unit_of(M.MAT_TIP_1000), ("0.379", "1"))

    def test_sqlite_takes_priority_over_csv(self):
        csv_path = os.path.join(self.tmp, "資材重量.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("管理番号,梱包資材名,単位質量,係数\n1,テスラピン,0.001,1\n")
        repo = MaterialRepository("", csv_path, prefer_access=False,
                                  db_path=self.db)
        self.assertEqual(repo.load().unit_of("テスラピン"), ("0.077", "1"))
        self.assertEqual(repo.last_source, "sqlite")

    def test_falls_back_to_csv_when_db_missing(self):
        csv_path = os.path.join(self.tmp, "資材重量.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("管理番号,梱包資材名,単位質量,係数\n1,テスラピン,0.001,1\n")
        repo = MaterialRepository("", csv_path, prefer_access=False,
                                  db_path=os.path.join(self.tmp, "nope.sqlite3"))
        self.assertEqual(repo.load().unit_of("テスラピン"), ("0.001", "1"))
        self.assertEqual(repo.last_source, "csv")

    def test_opened_read_only(self):
        """マスタは共有想定。こちらから書いたりジャーナルを作ったりしない。"""
        repo = MaterialRepository("", "", prefer_access=False, db_path=self.db)
        repo.load()
        names = os.listdir(self.tmp)
        self.assertNotIn("梱包資材マスタ.sqlite3-journal", names)
        self.assertNotIn("梱包資材マスタ.sqlite3-wal", names)

    def test_picks_up_master_update(self):
        repo = MaterialRepository("", "", prefer_access=False, db_path=self.db,
                                  refresh_sec=0)
        self.assertEqual(repo.load().unit_of("テスラピン"), ("0.077", "1"))
        time.sleep(1.05)
        conn = sqlite3.connect(self.db)
        conn.execute('UPDATE "資材重量" SET "単位質量"=? WHERE "梱包資材名"=?',
                     ("0.999", "テスラピン"))
        conn.commit()
        conn.close()
        self.assertEqual(repo.load().unit_of("テスラピン"), ("0.999", "1"))
