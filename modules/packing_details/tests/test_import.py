"""取り込み (取り込み元 → 手元)

**ここが移植で一番壊れやすい。** 取り込み元は全列 TEXT、LS4LOT は
型宣言すら無く、`ｵｰﾀﾞｰ板丈` の実値は `'0'` ではなく `'0.0'`。
変換を通さないとVBAの判定条件が成立しない。
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from modules.packing_details.meisai import data_sync, import_specs, lot_repo

from . import _db


class ConverterTest(unittest.TestCase):
    """変換関数。"""

    def test_to_realは文字列の数値を読む(self):
        self.assertEqual(import_specs.to_real("0.0"), 0.0)
        self.assertEqual(import_specs.to_real("1.985"), 1.985)
        self.assertEqual(import_specs.to_real(" 104.0 "), 104.0)

    def test_to_realは空とゴミをNoneにする(self):
        for bad in ("", "   ", None, "abc"):
            self.assertIsNone(import_specs.to_real(bad))

    def test_to_intは小数表記も読む(self):
        # 取り込み元は "2" でも "2.0" でも来る。int("2.0") は例外になる
        self.assertEqual(import_specs.to_int("2"), 2)
        self.assertEqual(import_specs.to_int("2.0"), 2)

    def test_to_intは空とゴミをNoneにする(self):
        for bad in ("", "   ", None, "abc"):
            self.assertIsNone(import_specs.to_int(bad))

    def test_to_textは前後の空白を落とす(self):
        self.assertEqual(import_specs.to_text("  R192 "), "R192")
        self.assertEqual(import_specs.to_text(None), "")


class ImportTest(unittest.TestCase):
    """取り込み本体。"""

    def setUp(self):
        self.conn = _db.case_db(self)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [p.unlink() for p in self.dir.glob("*")]
                        and None)

    def _lot_source(self, rows):
        return _db.make_source(self.dir / "SIKALOT.sqlite3", _db.LOT_COLUMNS,
                               rows, created_at="2026-09-14T09:00:26")

    def test_板丈00が数値0として入る(self):
        """**この試験が本丸。**

        取り込み元の実値は `'0.0'`。素で移すと `= '0'` も `= 0` も
        1件も一致しない。`to_real` を通して REAL 列へ入れることで、
        VBA の `ｵｰﾀﾞｰ板丈='0'` と同じ行が引けるようになる。
        """
        path = self._lot_source([_db.lot_row("L5160Z0")])
        data_sync.import_table(self.conn, "仕掛ロット", path)

        row = self.conn.execute(
            "SELECT オーダー板丈 FROM 仕掛ロット WHERE ロット番号 = 'L5160Z0'"
        ).fetchone()
        self.assertEqual(row["オーダー板丈"], 0.0)

        # 取り込み後は数値比較で引ける = VBAと同じ行が採れる
        found = self.conn.execute(
            "SELECT COUNT(*) c FROM 仕掛ロット WHERE オーダー板丈 = 0").fetchone()
        self.assertEqual(found["c"], 1)

    def test_板丈が0でない行は抽出条件から外れる(self):
        path = self._lot_source([
            _db.lot_row("AAA0001", **{"ｵｰﾀﾞｰ板丈": "0.0"}),
            _db.lot_row("BBB0002", **{"ｵｰﾀﾞｰ板丈": "3050.0"}),
        ])
        data_sync.import_table(self.conn, "仕掛ロット", path)
        self.assertTrue(lot_repo.find_lot(self.conn, "AAA0001").found)
        self.assertFalse(lot_repo.find_lot(self.conn, "BBB0002").found)

    def test_型宣言のない元から縦割数が数値で入る(self):
        """LS4LOT は型宣言が無く、値は text の "2"。

        素で引くと `当工程設計_縦割数 = 2` が1件も一致しない
        (`= '2'` なら一致する)。`to_int` を通して INTEGER 列へ。
        """
        path = _db.make_source(self.dir / "LS4LOT.sqlite3",
                               _db.TOKOTEI_COLUMNS,
                               [_db.tokotei_row("L5160Z0", tate="2", yoko="12")],
                               declare_text=False)
        data_sync.import_table(self.conn, "仕掛当工程", path)
        self.assertEqual(lot_repo.find_warisu(self.conn, "L5160Z0"), (2, 12))

    def test_鍵が空の行は入らない(self):
        path = self._lot_source([
            _db.lot_row("L5160Z0"),
            _db.lot_row(""),          # ロット番号が空
        ])
        result = data_sync.import_table(self.conn, "仕掛ロット", path)
        self.assertEqual(result.imported["仕掛ロット"], 1)
        self.assertEqual(result.skipped["仕掛ロット"], 1)

    def test_鍵の列が元に無ければ取り込まない(self):
        """空の鍵で総入れ替えすると、それまで使えていたデータまで消える。"""
        good = self._lot_source([_db.lot_row("L5160Z0")])
        data_sync.import_table(self.conn, "仕掛ロット", good)

        # ロット番号の列が無い写しが届いた
        broken = _db.make_source(
            self.dir / "SIKALOT_broken.sqlite3",
            tuple(c for c in _db.LOT_COLUMNS if c != "ﾛｯﾄ番号"),
            [{k: v for k, v in _db.lot_row("X").items() if k != "ﾛｯﾄ番号"}])
        result = data_sync.import_table(self.conn, "仕掛ロット", broken)

        self.assertFalse(result.ok)
        self.assertIn("取り込みません", " ".join(result.errors))
        # 前のデータが残っている
        self.assertTrue(lot_repo.find_lot(self.conn, "L5160Z0").found)

    def test_鍵でない列が無ければ空で取り込む(self):
        columns = tuple(c for c in _db.LOT_COLUMNS if c != "用途名")
        rows = [{k: v for k, v in _db.lot_row("L5160Z0").items() if k != "用途名"}]
        path = _db.make_source(self.dir / "SIKALOT_nocol.sqlite3", columns, rows)
        result = data_sync.import_table(self.conn, "仕掛ロット", path)

        self.assertEqual(result.imported["仕掛ロット"], 1)
        self.assertIn("空で取り込みます", " ".join(result.errors))
        self.assertEqual(lot_repo.find_lot(self.conn, "L5160Z0").yoto_name, "")

    def test_取り込みは総入れ替え(self):
        first = self._lot_source([_db.lot_row("AAA0001")])
        data_sync.import_table(self.conn, "仕掛ロット", first)
        second = _db.make_source(self.dir / "SIKALOT2.sqlite3", _db.LOT_COLUMNS,
                                 [_db.lot_row("BBB0002")])
        data_sync.import_table(self.conn, "仕掛ロット", second)

        self.assertFalse(lot_repo.find_lot(self.conn, "AAA0001").found)
        self.assertTrue(lot_repo.find_lot(self.conn, "BBB0002").found)

    def test_元の作成日時を覚える(self):
        """`_更新情報` があれば「いつ時点の台帳か」を出せる。"""
        path = self._lot_source([_db.lot_row("L5160Z0")])
        data_sync.import_table(self.conn, "仕掛ロット", path)
        row = self.conn.execute(
            "SELECT 元作成日時 FROM 取り込み記録 WHERE ファイル = ?",
            (str(path),)).fetchone()
        self.assertEqual(row["元作成日時"], "2026-09-14T09:00:26")

    def test_更新情報が無くても取り込める(self):
        """LS4LOT にはこの表が無い。"""
        path = _db.make_source(self.dir / "LS4LOT.sqlite3", _db.TOKOTEI_COLUMNS,
                               [_db.tokotei_row("L5160Z0")], declare_text=False)
        result = data_sync.import_table(self.conn, "仕掛当工程", path)
        self.assertTrue(result.ok)
        self.assertEqual(data_sync.source_created_at(path), "")

    def test_変わっていなければ取り込み直さない(self):
        path = self._lot_source([_db.lot_row("L5160Z0")])
        self.assertTrue(data_sync.needs_import(self.conn, path))
        data_sync.import_table(self.conn, "仕掛ロット", path)
        self.assertFalse(data_sync.needs_import(self.conn, path))

    def test_一度も取り込んでいなければ要ると答える(self):
        path = self._lot_source([_db.lot_row("L5160Z0")])
        self.assertTrue(data_sync.needs_import(self.conn, path))

    def test_届いていないファイルは見に行かない(self):
        self.assertFalse(data_sync.needs_import(self.conn,
                                                self.dir / "ない.sqlite3"))

class StampTest(unittest.TestCase):
    """取り込みの鮮度表示。"""

    def setUp(self):
        self.conn = _db.case_db(self)
        self.dir = Path(tempfile.mkdtemp())

    def test_元が届かなくても取り込み済みと分かる(self):
        """**パスを鍵にすると「取り込んであるのに未取込」と出る。**

        共有に届かない端末では `find_sources()` が空を返すが、
        手元には前回取り込んだデータが入っている。実績は
        テーブル名で引くので、届かなくても日時が出る。
        """
        path = _db.make_source(self.dir / "SIKALOT.sqlite3", _db.LOT_COLUMNS,
                               [_db.lot_row("L5160Z0")],
                               created_at="2026-09-14T09:00:26")
        data_sync.import_table(self.conn, "仕掛ロット", path)
        path.unlink()                      # 共有が切れた想定

        stamp = next(s for s in data_sync.stamps(self.conn)
                     if s.table == "仕掛ロット")
        self.assertFalse(stamp.found)      # いまは届かない
        self.assertTrue(stamp.imported)    # でも取り込んである
        self.assertEqual(stamp.as_of, "2026-09-14T09:00:26")
        self.assertEqual(stamp.count, 1)

    def test_画面へ渡す形に導いた値が入る(self):
        """**`__dict__` だと `as_of` と `imported` が落ちる。**

        落ちると受け取った側が `found` から組み立て直すことになり、
        「届かない」と「取り込んでいない」を取り違える。
        """
        path = _db.make_source(self.dir / "SIKALOT.sqlite3", _db.LOT_COLUMNS,
                               [_db.lot_row("L5160Z0")],
                               created_at="2026-09-14T09:00:26")
        data_sync.import_table(self.conn, "仕掛ロット", path)
        got = next(s.to_dict() for s in data_sync.stamps(self.conn)
                   if s.table == "仕掛ロット")
        self.assertEqual(got["as_of"], "2026-09-14T09:00:26")
        self.assertTrue(got["imported"])
        self.assertEqual(got["count"], 1)

    def test_上の帯は秒を落とすが年は残す(self):
        """年まで削ると**去年の台帳が今朝のものに見える。**

        設定画面は元のまま(`as_of`)を出す。短くするのは帯だけ。
        """
        path = _db.make_source(self.dir / "SIKALOT.sqlite3", _db.LOT_COLUMNS,
                               [_db.lot_row("L5160Z0")],
                               created_at="2026-09-14T09:00:26")
        data_sync.import_table(self.conn, "仕掛ロット", path)
        stamp = next(s for s in data_sync.stamps(self.conn)
                     if s.table == "仕掛ロット")
        self.assertEqual(stamp.as_of_short, "2026-09-14 09:00")
        self.assertEqual(stamp.as_of, "2026-09-14T09:00:26")
        self.assertEqual(stamp.to_dict()["as_of_short"], "2026-09-14 09:00")

    def test_日時の形が違えばそのまま出す(self):
        """**削れないものは削らない。** 元が名乗る形は台帳側の都合で
        変わりうるので、想定と違えば手を付けずに出す。
        """
        for raw in ["", "未取込", "2026-09-14", "9/14 9:00"]:
            with self.subTest(raw=raw):
                self.assertEqual(
                    data_sync.SourceStamp("仕掛ロット", "x", found=True,
                                          imported_at="x", created_at=raw
                                          ).as_of_short,
                    raw or "x")

    def test_一度も取り込んでいなければ未取込(self):
        stamp = next(s for s in data_sync.stamps(self.conn)
                     if s.table == "仕掛ロット")
        self.assertFalse(stamp.imported)
        self.assertEqual(stamp.as_of, "未取込")

    def test_取り込み直すと記録が増えない(self):
        path = _db.make_source(self.dir / "SIKALOT.sqlite3", _db.LOT_COLUMNS,
                               [_db.lot_row("L5160Z0")])
        data_sync.import_table(self.conn, "仕掛ロット", path)
        data_sync.import_table(self.conn, "仕掛ロット", path)
        rows = self.conn.execute("SELECT COUNT(*) c FROM 取り込み記録").fetchone()
        self.assertEqual(rows["c"], 1)


if __name__ == "__main__":
    unittest.main()
