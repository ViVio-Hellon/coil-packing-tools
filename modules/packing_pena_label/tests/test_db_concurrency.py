# -*- coding: utf-8 -*-
"""DB まわりの点検（2026.09）。

    ・SQL 方言 / データ型の差
    ・ロック（database is locked）と再試行
    ・トランザクション、同時更新によるデータ消失
    ・長時間放置（接続保持・ファイルハンドル）
    ・時計のずれ
    ・読み取り中に別プロセスが書き込む
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from modules.packing_pena_label.app.repositories.material_repo import MaterialRepository   # noqa: E402
from modules.packing_pena_label.app.repositories.sqlite_store import Store, is_network_path  # noqa: E402
from modules.packing_pena_label.app.services.screen_guard import ScreenGuard               # noqa: E402


def make_master(path, mass="0.5", coef="1", numeric=False):
    c = sqlite3.connect(path)
    c.execute("DROP TABLE IF EXISTS 資材重量")
    if numeric:
        c.execute("CREATE TABLE 資材重量 (管理番号 INTEGER, 梱包資材名 TEXT,"
                  " 単位質量 REAL, 係数 REAL)")
        c.execute("INSERT INTO 資材重量 VALUES (1,'テスラピン',?,?)",
                  (float(mass), float(coef)))
    else:
        c.execute("CREATE TABLE 資材重量 (管理番号 INTEGER, 梱包資材名 TEXT,"
                  " 単位質量 TEXT, 係数 TEXT)")
        c.execute("INSERT INTO 資材重量 VALUES (1,'テスラピン',?,?)", (mass, coef))
    c.commit()
    c.close()


class StoreConcurrencyTest(unittest.TestCase):
    """状態DB。画面は 1 つでも、HTTP の要求は並行して届く。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "state.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_many_threads_do_not_lose_writes(self):
        st = Store(self.db, idle_close_sec=0)
        self.addCleanup(st.close)
        errors = []

        def worker(n):
            try:
                for i in range(40):
                    st.set_kv("k%d" % n, {"i": i})
                    st.get_kv("k%d" % n, None)
            except Exception as exc:                 # noqa: BLE001
                errors.append("%d: %r" % (n, exc))

        ts = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        for n in range(8):
            self.assertEqual(st.get_kv("k%d" % n, {}).get("i"), 39)

    def test_waits_out_another_process_holding_the_lock(self):
        """``database is locked`` で諦めないこと。"""
        st = Store(self.db, idle_close_sec=0, busy_timeout_ms=15000)
        self.addCleanup(st.close)
        st.set_kv("warmup", {"a": 1})

        code = textwrap.dedent("""
            import sqlite3, time
            c = sqlite3.connect(%r, timeout=30, isolation_level="DEFERRED")
            c.execute("PRAGMA journal_mode=DELETE")
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR REPLACE INTO kv_store(key,payload)"
                      " VALUES('ext','\\"x\\"')")
            print("locked", flush=True)
            time.sleep(1.0)
            c.commit(); c.close()
        """) % self.db
        p = subprocess.Popen([sys.executable, "-c", code],
                             stdout=subprocess.PIPE, encoding="utf-8", errors="replace")
        try:
            self.assertEqual(p.stdout.readline().strip(), "locked")
            st.set_kv("mine", {"v": 1})          # 待たされるが成功すること
        finally:
            p.wait(timeout=30)
            p.stdout.close()
        self.assertEqual(st.get_kv("mine", None), {"v": 1})
        self.assertEqual(st.get_kv("ext", None), "x", "相手の書込が消えている")

    def test_a_failed_operation_leaves_nothing_behind(self):
        """1 操作の途中で失敗したら、その操作の書込は残らないこと。

        直す前は、失敗した操作が書いた途中までの内容が接続に残り、
        **次の無関係な書込の commit で一緒に確定していた**。
        """
        st = Store(self.db, idle_close_sec=0)
        self.addCleanup(st.close)
        with self.assertRaises(sqlite3.Error):
            st._run(lambda c: (
                c.execute("INSERT OR REPLACE INTO kv_store(key,payload)"
                          " VALUES('half','\"A\"')"),
                c.execute("INSERT INTO kv_store(存在しない列) VALUES (1)"),
            ), write=True)
        self.assertIsNone(st.get_kv("half", None))

        # 無関係な書込をしても、失敗した分が後から確定しないこと
        st.set_kv("other", {"v": 1})
        st.close()
        c = sqlite3.connect(self.db)
        try:
            keys = [r[0] for r in c.execute("SELECT key FROM kv_store")]
        finally:
            c.close()
        self.assertNotIn("half", keys, "失敗した操作が後から確定している")
        self.assertIn("other", keys)

    def test_a_constraint_error_also_rolls_back(self):
        """制約違反でも途中の書込を残さないこと。"""
        st = Store(self.db, idle_close_sec=0)
        self.addCleanup(st.close)
        with self.assertRaises(sqlite3.Error):
            st._run(lambda c: (
                c.execute("INSERT OR REPLACE INTO kv_store(key,payload)"
                          " VALUES('a','\"1\"')"),
                c.execute("INSERT INTO tare_result(coil_no,payload)"
                          " VALUES (1,'x')"),
                c.execute("INSERT INTO tare_result(coil_no,payload)"
                          " VALUES (1,'y')"),      # 主キー重複
            ), write=True)
        self.assertIsNone(st.get_kv("a", None))

    def test_cells_are_written_in_one_go(self):
        """台紙 1 枚分の書込がまとめて 1 操作であること。"""
        st = Store(self.db, idle_close_sec=0)
        self.addCleanup(st.close)
        cells = {(r, 4): "v%d" % r for r in range(1, 40)}
        st.put_cells(5, cells)
        got = st.get_cells(5)
        self.assertEqual(len(got), len(cells))


class IdleCloseTest(unittest.TestCase):
    """起動したまま放置してもファイルを掴み続けないこと。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "state.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _open_fds(self) -> int:
        try:
            return len([f for f in os.listdir("/proc/self/fd")
                        if os.path.realpath("/proc/self/fd/" + f)
                        == os.path.realpath(self.db)])
        except OSError:
            self.skipTest("この環境では fd を数えられない")
            return 0

    def test_connection_is_released_and_reopened(self):
        st = Store(self.db, idle_close_sec=1)
        self.addCleanup(st.close)
        st.set_kv("x", {"a": 1})
        self.assertTrue(st.stats()["open"])
        for _ in range(60):
            time.sleep(0.25)
            if not st.stats()["open"]:
                break
        self.assertFalse(st.stats()["open"], "アイドルでも接続を離さない")
        self.assertEqual(self._open_fds(), 0, "ファイルハンドルが残っている")
        self.assertEqual(st.get_kv("x", None), {"a": 1}, "開き直せない")

    def test_state_db_is_never_on_a_share(self):
        """状態DB は設定できず、必ずローカル領域に置かれること。

        共有フォルダー上の SQLite を複数 PC から**書く**のは
        破損の原因になるため、そもそも選べないようにしてある。
        """
        from modules.packing_pena_label.app.config import Config
        cfg = Config()
        self.assertIn(cfg.local_dir, cfg.db_path)
        from modules.packing_pena_label.app.services import settings as S
        keys = [sp.key for sp in S.SPECS] if hasattr(S, "SPECS") else []
        self.assertNotIn("db_path", keys)
        self.assertNotIn("state_db_file", keys)

    def test_network_path_is_detected(self):
        self.assertTrue(is_network_path(r"\\srv\share\state.sqlite3"))
        self.assertTrue(is_network_path("//srv/share/state.sqlite3"))
        self.assertFalse(is_network_path(self.db))


class MasterTypesAndFreshnessTest(unittest.TestCase):
    """資材マスタ。Access(数値/通貨) / SQLite(TEXT) / CSV(文字列) の差。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "梱包資材マスタ.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _repo(self, refresh_sec=0):
        return MaterialRepository(accdb_dir="", csv_path="",
                                  prefer_access=False, db_path=self.db,
                                  refresh_sec=refresh_sec)

    def test_text_and_numeric_give_the_same_weight(self):
        """型が違っても計算に入る値は同じであること。"""
        from modules.packing_pena_label.app.services.vba_compat import val
        seen = []
        for mass, numeric in (("0.5", False), ("0.5", True), (".5", False),
                              ("0.50", False), (" 0.5 ", False)):
            make_master(self.db, mass=mass, numeric=numeric)
            raw = self._repo().load().unit_of("テスラピン")[0]
            seen.append(val(raw))
        self.assertEqual(seen, [0.5] * len(seen), seen)

    def test_missing_material_is_zero_not_an_error(self):
        """VBA と同じく、無い資材は 0kg で静かに続けること。"""
        from modules.packing_pena_label.app.services.vba_compat import val
        make_master(self.db)
        t = self._repo().load()
        mass, coef = t.unit_of("存在しない資材")
        self.assertEqual(val(mass) * val(coef), 0.0)

    def test_master_update_is_picked_up_while_running(self):
        """起動したまま放置中にマスタが差し替わったら読み直すこと。"""
        make_master(self.db, mass="0.5")
        repo = self._repo()
        self.assertEqual(repo.load().unit_of("テスラピン")[0], "0.5")
        time.sleep(1.1)                       # mtime を動かす
        make_master(self.db, mass="0.9")
        self.assertEqual(repo.load().unit_of("テスラピン")[0], "0.9")

    def test_refresh_window_eventually_catches_up(self):
        make_master(self.db, mass="0.5")
        repo = self._repo(refresh_sec=2)
        self.assertEqual(repo.load().unit_of("テスラピン")[0], "0.5")
        time.sleep(1.1)
        make_master(self.db, mass="1.7")
        self.assertEqual(repo.load().unit_of("テスラピン")[0], "0.5",
                         "鮮度チェック間隔の中は古いままでよい")
        time.sleep(2.2)
        self.assertEqual(repo.load().unit_of("テスラピン")[0], "1.7",
                         "間隔を過ぎても追いつかない")

    def test_reading_while_another_process_writes(self):
        """読んでいる最中に別 PC がマスタを更新しても壊れないこと。"""
        make_master(self.db, mass="0.5")
        repo = self._repo()
        repo.load()
        stop = threading.Event()
        write_errors = []

        def writer():
            c = sqlite3.connect(self.db, timeout=10)
            i = 0
            while not stop.is_set():
                try:
                    c.execute("UPDATE 資材重量 SET 単位質量=? WHERE 管理番号=1",
                              (str(0.5 + i * 0.01),))
                    c.commit()
                    i += 1
                except Exception as exc:              # noqa: BLE001
                    write_errors.append(repr(exc))
                time.sleep(0.01)
            c.close()

        th = threading.Thread(target=writer)
        th.start()
        try:
            for _ in range(120):
                repo.load(use_cache=False).unit_of("テスラピン")
        finally:
            stop.set()
            th.join()
        self.assertEqual(write_errors, [])

    def test_wal_master_can_still_be_read(self):
        """別システムが WAL で書いているマスタも読めること。"""
        make_master(self.db)
        c = sqlite3.connect(self.db)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("UPDATE 資材重量 SET 単位質量='2.5' WHERE 管理番号=1")
        c.commit()
        c.close()
        self.assertEqual(self._repo().load().unit_of("テスラピン")[0], "2.5")

    def test_master_is_opened_read_only(self):
        """こちらから共有マスタへ書かないこと。"""
        import io
        with io.open(os.path.join(ROOT, "app", "repositories",
                                  "material_repo.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("mode=ro", src)


class ClockChangeTest(unittest.TestCase):
    """PC の時計がずれても誤動作しないこと。

    ライン端末は長時間つけっぱなしで、NTP 補正や手動の時刻変更が起きうる。
    経過時間を ``time.time()`` で測っていると、1 時間進んだだけで
    「誰も使っていない」と誤判定して**作業中の画面を奪われる**。
    """

    def test_screen_guard_survives_a_clock_jump(self):
        from modules.packing_pena_label.app.services import screen_guard as SG
        real = time.time
        offset = [0.0]
        SG.time.time = lambda: real() + offset[0]
        try:
            g = ScreenGuard(ttl_sec=20.0)
            self.assertTrue(g.claim("A")["granted"])
            self.assertFalse(g.claim("B")["granted"])
            offset[0] = 3600.0                      # 時計が 1 時間進んだ
            self.assertFalse(g.claim("B")["granted"],
                             "時計が進んだだけで画面を奪われる")
            offset[0] = -3600.0                     # 戻った場合
            self.assertFalse(g.claim("B")["granted"])
        finally:
            SG.time.time = real

    def test_elapsed_time_uses_a_monotonic_clock(self):
        import io
        for rel in (("app", "services", "screen_guard.py"),
                    ("app", "repositories", "sqlite_store.py"),
                    ("app", "repositories", "material_repo.py")):
            with io.open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
                src = f.read()
            with self.subTest(rel[-1]):
                # screen_guard は時計を差し替えられる作り（既定が time.monotonic）
                self.assertTrue("time.monotonic()" in src
                                or "clock or time.monotonic" in src,
                                "経過時間を壁時計で測っている")

    def test_screen_guard_default_clock_is_monotonic(self):
        import time as _t
        from modules.packing_pena_label.app.services.screen_guard import ScreenGuard
        self.assertIs(ScreenGuard()._clock, _t.monotonic)


class HeartbeatReasonTest(unittest.TestCase):
    """放置で期限切れになっただけなら、黙って取り直せること。"""

    def test_the_holder_may_resume_after_being_idle(self):
        """放置しても、誰も取っていなければそのまま使い続けられること。

        （期限切れは「他の画面が取れる」という意味であって、
        　持ち主が追い出される意味ではない）
        """
        g = ScreenGuard(ttl_sec=0.3)
        g.claim("A")
        time.sleep(0.5)
        self.assertTrue(g.heartbeat_info("A")["granted"])

    def test_released_screen_is_not_reported_as_taken(self):
        """閉じて開き直した画面は、黙って取り直せること。"""
        g = ScreenGuard(ttl_sec=20.0)
        g.claim("A")
        g.release("A")                       # タブを閉じたとき
        r = g.heartbeat_info("A")
        self.assertFalse(r["granted"])
        self.assertFalse(r["heldByOther"], "誰も居ないのに『取られた』扱い")

    def test_taken_over_is_reported_as_taken(self):
        g = ScreenGuard(ttl_sec=20.0)
        g.claim("A")
        g.claim("B", force=True)
        r = g.heartbeat_info("A")
        self.assertFalse(r["granted"])
        self.assertTrue(r["heldByOther"])


if __name__ == "__main__":
    unittest.main()
