"""書き込みの確実さ (トランザクション・ロック・失敗の伝え方)

**ここが緩むと、画面には「出力しました」と出るのに1行も書けていない。**
実際にそうなっていたので、その形を試験で固定する。
"""
from __future__ import annotations

import sqlite3
import time
import unittest

from modules.packing_details.meisai import config, db, history_repo, meisai_service, strand_service

from . import _db


def _board(lot_no: str = "L5160Z0") -> strand_service.Board:
    board = strand_service.Board(lot_no=lot_no, zen_kotei=24, jou_su=2)
    board.build_strands()
    board.weights[:] = [250, 248]
    board.set_stack_max(2)
    board.stack("1-12")
    board.stack("2-12")
    return board


class WriteFailureTest(unittest.TestCase):
    """書けなかったことを、書けたことにしない。"""

    def setUp(self):
        self.conn, self.path = _db.file_db(self)
        # 待ち時間を詰める。**振る舞いは同じで、試験が速くなるだけ**
        self._saved = (db.BUSY_TIMEOUT_MS, db.TX_MAX_RETRY, db.TX_RETRY_WAIT_SEC)
        db.BUSY_TIMEOUT_MS, db.TX_MAX_RETRY, db.TX_RETRY_WAIT_SEC = 200, 0, 0
        self.conn.execute("PRAGMA busy_timeout = 200")

    def tearDown(self):
        db.BUSY_TIMEOUT_MS, db.TX_MAX_RETRY, db.TX_RETRY_WAIT_SEC = self._saved

    def _hold(self):
        """別プロセスの代わりに、別接続で握りっぱなしにする。"""
        other = sqlite3.connect(str(self.path), timeout=5)
        other.execute("PRAGMA busy_timeout = 5000")
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE 明細出力状態 SET 印刷済 = 1 WHERE ID = 1")
        self.addCleanup(other.close)
        return other

    def test_書けなければ例外で止まる(self):
        """**「出力しました」と言わせない。**

        以前は `execute_with_retry` の戻り値を捨てていたので、
        1行も書けていないのに `ok=True` が返っていた。
        """
        self._hold()
        with self.assertRaises(db.WriteError):
            meisai_service.output(self.conn, _board(), confirm=True)

    def test_No指定でも例外で止まる(self):
        """No指定は連番を採らないので、以前はここだけ素通りしていた。"""
        self._hold()
        with self.assertRaises(db.WriteError):
            meisai_service.output(self.conn, _board(), specify_no=3, confirm=True)
        row = self.conn.execute("SELECT COUNT(*) c FROM 明細出力").fetchone()
        self.assertEqual(row["c"], 0)

    def test_途中で失敗したら全部戻る(self):
        """明細だけ残って副番が登録されない、という半端を作らない。"""
        original = history_repo.register_fuban

        def boom(_conn, _key):
            raise RuntimeError("途中で落ちた")

        history_repo.register_fuban = boom
        self.addCleanup(setattr, history_repo, "register_fuban", original)
        with self.assertRaises(RuntimeError):
            meisai_service.output(self.conn, _board(), confirm=True)

        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) c FROM 明細出力").fetchone()["c"], 0)
        # 連番も進んでいないこと。**進むと次の出力が番号を飛ばす**
        self.assertEqual(history_repo.current_seq(self.conn), 0)

    def test_待つ時間に上限がある(self):
        """**無言で固まらせない。**

        直す前は1文あたり46秒(busy_timeout 10秒 × 4 + 待機6秒)で、
        1操作が文の数だけ積み上がって90秒を超えていた。
        """
        db.BUSY_TIMEOUT_MS, db.TX_MAX_RETRY, db.TX_RETRY_WAIT_SEC = 200, 1, 0.1
        self._hold()
        start = time.monotonic()
        with self.assertRaises(db.WriteError):
            meisai_service.output(self.conn, _board(), confirm=True)
        self.assertLess(time.monotonic() - start, 3.0)

    def test_断りの文言に次にすることが入っている(self):
        self._hold()
        with self.assertRaises(db.WriteError) as caught:
            history_repo.save_snapshot(self.conn, history_repo.Snapshot(
                lot_no="L5160Z0", zen_kotei=24, jou_su=2,
                weights=[250, 248], strands=[12, 12], used=[], discarded=[]))
        self.assertIn("ほかのプログラム", str(caught.exception))


class TransactionTest(unittest.TestCase):
    """まとまりの入れ子。"""

    def setUp(self):
        self.conn = _db.case_db(self)

    def test_入れ子にしても内側で確定しない(self):
        """内側が確定すると、外側が失敗したときに戻せない。"""
        with self.assertRaises(RuntimeError):
            with db.transaction(self.conn, name="外"):
                db.write(self.conn, "INSERT INTO 副番履歴 (副番キー, 登録日時)"
                                    " VALUES ('A-1-1', '2026-01-01 00:00:00')")
                with db.transaction(self.conn, name="内"):
                    db.write(self.conn, "INSERT INTO 副番履歴 (副番キー, 登録日時)"
                                        " VALUES ('A-1-2', '2026-01-01 00:00:00')")
                raise RuntimeError("外で落ちた")
        row = self.conn.execute("SELECT COUNT(*) c FROM 副番履歴").fetchone()
        self.assertEqual(row["c"], 0, "内側のぶんも戻っていること")

    def test_読み取りはまとまりを開かない(self):
        """`SELECT` で取引が始まると、入れ子の判定を取り違える。"""
        self.conn.execute("SELECT COUNT(*) FROM 副番履歴").fetchone()
        self.assertFalse(self.conn.in_transaction)


class DbPathTest(unittest.TestCase):
    """手元DBの置き場所。**共有フォルダに置かせない。**"""

    def test_共有フォルダなら理由を返す(self):
        from pathlib import Path
        problem = config.db_path_problem(Path(r"\\srv\共有\packing.db"))
        self.assertIn("共有フォルダ", problem)

    def test_手元なら何も言わない(self):
        from pathlib import Path
        self.assertEqual(config.db_path_problem(Path("/home/u/x.db")), "")


if __name__ == "__main__":
    unittest.main()
