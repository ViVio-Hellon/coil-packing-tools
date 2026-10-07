"""手元のDBが壊れていたときの立て直し(統合 1.2.1 の再点検で見つけた)

手元のDBを壊して起動すると、ペナラベルの状態DBでは統合アプリごと起動せず、梱包明細では
画面が 500、資材計算では「初期化に失敗しました」のまま使えなかった(移行の手本の
python-web-tools でも移行後に同じ不具合が見つかった)。

- 壊れていたら横へ退けて(消さずに)作り直す。退けたファイルは残る
- 掴まれている・混んでいる(`database is locked`)は壊れているとみなさない
- 3機能とも、実際の「DBを開いて表を整える」で確かめる。**開きかけの接続を閉じずに退けようと
  すると Windows では動かせない**ので、Windows の自動試験でもここが通ること
"""
from __future__ import annotations

import logging
import sqlite3
import tempfile
import unittest
from pathlib import Path

from common import db_recover

ROOT = Path(__file__).resolve().parent.parent
GARBAGE = b"this is not a sqlite database " * 200
log = logging.getLogger("test_db_recover")


def _broken(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(GARBAGE)
    (folder / (name + "-wal")).write_bytes(b"wal")
    return path


def _tables(path: Path) -> set:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


class JudgeTest(unittest.TestCase):
    def test_broken_file_is_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _broken(Path(tmp), "x.db")
            conn = sqlite3.connect(str(path))
            try:
                with self.assertRaises(sqlite3.DatabaseError) as caught:
                    conn.execute("SELECT * FROM sqlite_master").fetchall()
            finally:
                conn.close()
        self.assertTrue(db_recover.is_broken(caught.exception))

    def test_busy_or_other_errors_are_not_broken(self):
        self.assertFalse(db_recover.is_broken(sqlite3.OperationalError("database is locked")))
        self.assertFalse(db_recover.is_broken(sqlite3.OperationalError("no such table: x")))
        self.assertFalse(db_recover.is_broken(OSError("file is not a database")))

    def test_set_aside_keeps_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _broken(Path(tmp), "梱包.db")
            moved = db_recover.set_aside(path, "試験", log)
            self.assertFalse(path.exists())
            self.assertTrue(moved.is_file())
            self.assertEqual(moved.read_bytes(), GARBAGE, "消さない(中身はそのまま)")
            self.assertIn("壊れていた_", moved.name)
            self.assertTrue(Path(str(moved) + "-wal").is_file(), "付き物も一緒に退ける")
            self.assertFalse(Path(str(path) + "-wal").exists())

    def test_other_failures_are_not_swallowed(self):
        def prepare():
            raise sqlite3.OperationalError("database is locked")
        with tempfile.TemporaryDirectory() as tmp:
            path = _broken(Path(tmp), "x.db")
            with self.assertRaises(sqlite3.OperationalError):
                db_recover.open_or_rebuild(path, "試験", log, prepare)
            self.assertTrue(path.exists(), "掴まれているだけなら退けない")


class ModulesRebuildTest(unittest.TestCase):
    """3機能の本物の「DBを開いて表を整える」で、壊れたDBを退けて作り直せる。"""

    def _check(self, name: str, prepare_for, expect_table: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = _broken(Path(tmp), name)
            with self.assertLogs(log, "ERROR"):
                moved = db_recover.open_or_rebuild(path, "試験", log, lambda: prepare_for(path))
            self.assertTrue(moved and moved.is_file(), "退けたファイルが残る")
            self.assertIn(expect_table, _tables(path), "作り直したDBに表がある")
            # もう一度開いても(壊れていないので)退けない
            self.assertIsNone(db_recover.open_or_rebuild(path, "試験", log, lambda: prepare_for(path)))

    def test_details(self):
        from modules.packing_details.meisai import db

        def prepare(path):
            with db.connect(path) as conn:
                db.apply_schema(conn)
        self._check("packing_details.db", prepare, "明細出力")

    def test_material(self):
        from modules.packing_material_calculation.coil_tool import db

        def prepare(path):
            with db.connect(path) as conn:
                db.apply_schema(conn)
        self._check("coil_tool.db", prepare, "取り込み履歴")

    def test_pena_state_store(self):
        """ペナラベルの状態DB: 壊れていても Store を作れる(統合アプリごと起動しなかった)。"""
        from modules.packing_pena_label.app.repositories.sqlite_store import Store
        with tempfile.TemporaryDirectory() as tmp:
            path = _broken(Path(tmp), "state.sqlite3")
            store = Store(str(path), idle_close_sec=0)
            try:
                self.assertTrue(_tables(path))
                left = sorted(p.name for p in Path(tmp).iterdir() if "壊れていた_" in p.name)
                self.assertTrue(left, "退けたファイルが残る")
            finally:
                store.close()

    def test_modules_use_it_on_start(self):
        """3機能の起動(初期化)が、この立て直しを通る。"""
        for rel in ("modules/packing_details/__init__.py",
                    "modules/packing_material_calculation/__init__.py",
                    "modules/packing_pena_label/app/repositories/sqlite_store.py"):
            with self.subTest(rel=rel):
                self.assertIn("db_recover.open_or_rebuild(", (ROOT / rel).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
