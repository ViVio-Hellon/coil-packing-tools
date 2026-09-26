"""マスタ管理(見るだけ)

**守ること**
- 梱包資材マスタも梱包明細履歴も、**表は全部出す**(このアプリが使う表を先に)
- 行は200行ずつ。**出さなかった分は数で言う**
- 絞り込みはどの列でも。`%` `_` は文字どおり
- 並べ替えは実在する列だけ。ほかの指定は既定の並びに戻す(SQL に組み込まない)
- 履歴は新しいものが上。**履歴のファイルや表が無ければ作る**(現場の指定)
- **書かない**(見るだけ)。共有が応えなければ待たせ続けない
- CSV は書き出し先に書くだけ。ダウンロードしない・こちらから開かない
"""
from __future__ import annotations

import csv
import hashlib
import sqlite3
import threading
import time
import unittest
from pathlib import Path

from modules.packing_details.meisai import config, master_browse, shared_settings, slip_history

from . import _db
from .test_slip_history import _record
from .test_web import HEADERS, _client, _post


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _add_table(path: Path, ddl: str, rows=(), sql: str = "") -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(ddl)
        if rows:
            conn.executemany(sql, rows)
        conn.commit()
    finally:
        conn.close()


class MasterTest(unittest.TestCase):
    """梱包資材マスタを見る。"""

    def setUp(self):
        _db.settings_file(self)
        self.path = _db.master_file(self)

    def test_表は全部出す_このアプリが読む表が先(self):
        view = master_browse.browse("master")
        self.assertEqual(view.error, "")
        names = [t.table for t in view.tables]
        self.assertEqual(names[0], "梱包明細打ち出し")
        self.assertEqual(sorted(names[1:]), names[1:])
        self.assertIn("PalletMaster", names)
        self.assertIn("ツールで足した表", names)
        counts = {t.table: t.rows for t in view.tables}
        self.assertEqual(counts["PalletMaster"], 2)
        # 指定が無ければ先頭を開く
        self.assertEqual(view.table, "梱包明細打ち出し")
        self.assertEqual(view.columns, ["ID", "設定文字列"])
        self.assertEqual(view.rows[0]["設定文字列"], "NLM.NAGOYA.QA")
        self.assertIn(master_browse.ROW_KEY, view.rows[0])

    def test_もう無い表を頼まれたら先頭を開く(self):
        view = master_browse.browse("master", "消えた表")
        self.assertEqual(view.table, "梱包明細打ち出し")

    def test_200行ずつ_出さなかった分は数で言う(self):
        _add_table(self.path, "CREATE TABLE 多い (n INTEGER, 名前 TEXT)",
                   [(i, f"名前{i}") for i in range(1, 251)], "INSERT INTO 多い VALUES (?, ?)")
        view = master_browse.browse("master", "多い")
        self.assertEqual((view.total, len(view.rows)), (250, 200))
        self.assertIn("250行のうち 200行", view.note)
        self.assertIn("ほか 50行", view.note)
        # 入った順(古いものが上)
        self.assertEqual(view.rows[0]["n"], 1)

    def test_絞り込みはどの列でも_記号は文字どおり(self):
        _add_table(self.path, "CREATE TABLE 品 (コード TEXT, 名前 TEXT, 数 INTEGER)",
                   [("A-1", "松板", 10), ("B_2", "角材", 25), ("C%3", "ボード", 250),
                    ("AB2", "板", 5)], "INSERT INTO 品 VALUES (?, ?, ?)")
        for query, expect in (("松", ["A-1"]), ("25", ["B_2", "C%3"]),
                              ("_", ["B_2"]), ("%", ["C%3"]), ("b_", ["B_2"]),
                              ("無い", [])):
            with self.subTest(query=query):
                view = master_browse.browse("master", "品", query=query)
                self.assertEqual([r["コード"] for r in view.rows], expect)
                self.assertEqual(view.total, len(expect))
                self.assertEqual(view.note, "")

    def test_並べ替えは実在する列だけ(self):
        _add_table(self.path, "CREATE TABLE 品 (コード TEXT, 数 INTEGER)",
                   [("A", 3), ("B", 1), ("C", 2)], "INSERT INTO 品 VALUES (?, ?)")
        view = master_browse.browse("master", "品", sort="数", sort_dir="desc")
        self.assertEqual([r["コード"] for r in view.rows], ["A", "C", "B"])
        self.assertEqual((view.sort, view.sort_dir), ("数", "desc"))
        self.assertIn("大きい順", view.order_label)
        for bad in ("無い列", "数; DROP TABLE 品", 'rowid"--'):
            with self.subTest(sort=bad):
                view = master_browse.browse("master", "品", sort=bad)
                self.assertEqual(view.error, "")
                self.assertEqual(view.sort, "")
                self.assertEqual([r["コード"] for r in view.rows], ["A", "B", "C"])
        self.assertEqual(_q_count(self.path, "品"), 3)

    def test_見るだけ_ファイルを書き換えない(self):
        before = _hash(self.path)
        master_browse.browse("master", "PalletMaster", query="P", sort="名前")
        master_browse.export_csv("master", "PalletMaster")
        self.assertEqual(_hash(self.path), before)

    def test_バイナリや_rowidの無い表も出せる(self):
        _add_table(self.path, "CREATE TABLE 絵 (名前 TEXT, 中身 BLOB)",
                   [("a", b"\x00\x01\x02")], "INSERT INTO 絵 VALUES (?, ?)")
        _add_table(self.path, "CREATE TABLE 鍵 (k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID",
                   [("b", "2"), ("a", "1")], "INSERT INTO 鍵 VALUES (?, ?)")
        view = master_browse.browse("master", "絵")
        self.assertEqual(view.rows[0]["中身"], "（バイナリ 3 バイト）")
        view = master_browse.browse("master", "鍵")
        self.assertEqual(view.error, "")
        self.assertEqual(len(view.rows), 2)
        self.assertEqual([r[master_browse.ROW_KEY] for r in view.rows], [1, 2])
        view = master_browse.browse("master", "鍵", sort="k")
        self.assertEqual([r["k"] for r in view.rows], ["a", "b"])
        # 列名に「, 」が入っていても並べ替えられる
        _add_table(self.path, 'CREATE TABLE 札 ("a, b" TEXT PRIMARY KEY, v INTEGER) WITHOUT ROWID',
                   [("y", 1), ("x", 2)], "INSERT INTO 札 VALUES (?, ?)")
        for sort, expect in (("a, b", ["x", "y"]), ("v", ["y", "x"])):
            view = master_browse.browse("master", "札", sort=sort)
            self.assertEqual(view.error, "")
            self.assertEqual([r["a, b"] for r in view.rows], expect)
        self.assertEqual(len(master_browse.export_csv("master", "札", sort="a, b").file) > 0, True)

    def test_マスタが無い_共有に届かない_は理由と直し方を言う(self):
        self.path.unlink()
        view = master_browse.browse("master")
        self.assertIn("見つかりません", view.error)
        self.assertIn("梱包資材マスタのフォルダ", view.error)      # どこで直すか
        self.assertEqual(view.fix, "share")
        self.assertEqual(view.tables, [])
        self.path.parent.rmdir()
        view = master_browse.browse("master")
        self.assertIn("届きません", view.error)
        self.assertEqual(view.to_dict()["fix"], "share")

    def test_置き場所を変えたら_すぐその場所を見る(self):
        """現場の指摘: 設定でパスを変えても「届きません」のまま。"""
        from modules.packing_details.meisai import user_settings
        self.path.parent.rename(self.path.parent.with_name("移した先"))
        view = master_browse.browse("master")
        self.assertIn("届きません", view.error)
        user_settings.save(config.KEY_SHARED_DIR, str(self.path.parent.with_name("移した先")))
        view = master_browse.browse("master")
        self.assertEqual(view.error, "")
        self.assertEqual(view.path, str(self.path.parent.with_name("移した先") / self.path.name))
        self.assertEqual(view.table, "梱包明細打ち出し")

    def test_行数の控え_見ている表は数え直す(self):
        view = master_browse.browse("master", "PalletMaster")
        self.assertEqual(view.total, 2)
        calls = []
        original = master_browse._count
        master_browse._count = lambda conn, name: calls.append(name) or original(conn, name)
        self.addCleanup(setattr, master_browse, "_count", original)
        view = master_browse.browse("master", "PalletMaster", sort="名前")
        # ファイルが変わっていなければ、見ている表だけを数える
        self.assertEqual(calls, ["PalletMaster"])
        _add_table(self.path, "CREATE TABLE 足した (x INTEGER)", [(1,), (2,), (3,)],
                   "INSERT INTO 足した VALUES (?)")
        calls.clear()
        view = master_browse.browse("master", "PalletMaster")
        self.assertIn("足した", calls)                                # 変わったので数え直す
        self.assertEqual({t.table: t.rows for t in view.tables}["足した"], 3)

    def test_共有が応えなければ待たせ続けない_重ねない(self):
        release = threading.Event()
        original = master_browse._fill

        def slow(*args, **kwargs):
            release.wait(5)
            return original(*args, **kwargs)

        master_browse._fill = slow
        self.addCleanup(setattr, master_browse, "_fill", original)
        self.addCleanup(release.set)
        started = time.monotonic()
        view = master_browse.browse("master", timeout=0.2)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn("応えません", view.error)
        # 裏で読み終わっても、返したものは書き換わらない(半端な中身を出さない)
        payload = view.to_dict()
        self.assertEqual((payload["tables"], payload["rows"], payload["path"]), ([], [], ""))
        # 応えないまま残っているあいだは、重ねずに断る
        view = master_browse.browse("master", timeout=0.2)
        self.assertIn("前の読み込み", view.error)
        release.set()
        for _ in range(50):
            if not any(t.is_alive() for t, _s in master_browse._running):
                break
            time.sleep(0.05)
        master_browse._fill = original
        self.assertEqual(view.tables, [])
        self.assertEqual(master_browse.browse("master").error, "")


def _q_count(path: Path, table: str) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


class HistoryTest(unittest.TestCase):
    """梱包明細履歴を見る。"""

    def setUp(self):
        _db.settings_file(self)
        shared_settings.shared_dir().mkdir(parents=True)
        self.conn = _db.case_db(self)
        self.shared = shared_settings.shared_dir() / config.HISTORY_DB_NAME

    def test_無ければ作って_表を出す(self):
        """現場の指定: 梱包明細履歴のテーブルが無ければ自動で作る。"""
        self.assertFalse(self.shared.exists())
        view = master_browse.browse("history")
        self.assertEqual(view.error, "")
        self.assertTrue(self.shared.exists())
        self.assertEqual([t.table for t in view.tables],
                         ["明細履歴", "明細履歴_副番", "整理記録"])
        self.assertEqual(view.table, "明細履歴")
        self.assertEqual(view.total, 0)
        self.assertEqual(view.columns, list(slip_history.HEADER_COLUMNS))

    def test_表が消えていたら作り直す(self):
        slip_history.ensure_shared()
        conn = sqlite3.connect(str(self.shared))
        conn.execute("DROP TABLE 明細履歴_副番")
        conn.commit()
        conn.close()
        view = master_browse.browse("history", "明細履歴_副番")
        self.assertEqual(view.error, "")
        self.assertEqual(view.table, "明細履歴_副番")

    def test_新しいものが上(self):
        """明細履歴は出力日時の新しい順(まとめて送ると入った順は出した順と揃わない)。"""
        for lot, when in (("L0000A2", "2026-09-02 08:00:00"), ("L0000A3", "2026-09-03 08:00:00"),
                          ("L0000A1", "2026-09-01 08:00:00")):
            _record(self.conn, lot=lot, when=when)
        slip_history.send_pending(self.conn)
        view = master_browse.browse("history")
        self.assertEqual([r["ロット番号"] for r in view.rows],
                         ["L0000A3", "L0000A2", "L0000A1"])
        self.assertEqual(view.order_label, "出力日時 の新しい順")
        # 見出しを押せばそちらが先
        view = master_browse.browse("history", sort="ロット番号")
        self.assertEqual([r["ロット番号"] for r in view.rows],
                         ["L0000A1", "L0000A2", "L0000A3"])
        # コイルの表は共有に入った順(新しいものが上)
        view = master_browse.browse("history", "明細履歴_副番")
        self.assertIn("新しいものが上", view.order_label)
        view = master_browse.browse("history", "明細履歴_副番", query="1-10")
        self.assertEqual(view.total, 3)

    def test_共有に届かなければ作らずに理由を言う(self):
        shared_settings.shared_dir().rmdir()
        view = master_browse.browse("history")
        self.assertIn("届きません", view.error)
        self.assertFalse(self.shared.exists())


class ExportTest(unittest.TestCase):
    """CSV に書き出す。"""

    def setUp(self):
        _db.settings_file(self)
        self.path = _db.master_file(self)
        _add_table(self.path, "CREATE TABLE 品 (コード TEXT, 副番 TEXT, 数 INTEGER, 絵 BLOB)",
                   [(f"K{i:03d}", "1-10" if i == 1 else f"{i}", i, None)
                    for i in range(1, 301)],
                   "INSERT INTO 品 VALUES (?, ?, ?, ?)")

    def _rows(self, result) -> list[list[str]]:
        path = config.EXPORT_DIR / result.file
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))      # BOM(Excel が UTF-8 で読む)
        self.assertIn(b"\r\n", raw)
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return list(csv.reader(handle))

    def test_全行を_絞り込みと並べ替えのまま書く(self):
        result = master_browse.export_csv("master", "品")
        rows = self._rows(result)
        self.assertEqual(rows[0], ["コード", "副番", "数", "絵"])     # `__行` は書かない
        self.assertEqual(len(rows), 301)                            # 200行で切らない
        self.assertEqual(result.rows, 300)
        # 副番が日付に化けない
        self.assertEqual(rows[1][1], '="1-10"')
        self.assertEqual(Path(result.folder), config.EXPORT_DIR)
        self.assertTrue(result.file.startswith("梱包資材マスタ_品_"))

        result = master_browse.export_csv("master", "品", query="K00", sort="数",
                                          sort_dir="desc")
        rows = self._rows(result)
        self.assertEqual([r[0] for r in rows[1:]],
                         [f"K{i:03d}" for i in range(9, 0, -1)])
        self.assertIn("絞り込み「K00」", result.note)

    def test_無い表は断る_書きかけを残さない(self):
        with self.assertRaises(master_browse.BrowseError):
            master_browse.export_csv("master", "無い表")
        self.assertEqual(list(config.EXPORT_DIR.glob("*.csv")), [])

    def test_Excelの行数を超える分は書かずに数で言う(self):
        original = master_browse.EXCEL_MAX_ROWS
        master_browse.EXCEL_MAX_ROWS = 100
        self.addCleanup(setattr, master_browse, "EXCEL_MAX_ROWS", original)
        result = master_browse.export_csv("master", "品")
        self.assertEqual(result.rows, 100)
        self.assertIn("ほか 200行", result.note)

    def test_こちらからファイルやExcelを開かない(self):
        import inspect
        from modules.packing_details.app.routes import master as master_routes
        for module in (master_browse, master_routes):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                for word in ("startfile(", "subprocess", "webbrowser", "send_file",
                             "Content-Disposition"):
                    self.assertNotIn(word, source)


class WebTest(unittest.TestCase):
    """画面の口。"""

    def setUp(self):
        _db.settings_file(self)
        self.path = _db.master_file(self)
        self.client, self.conn = _client(self)

    def test_見る(self):
        res = self.client.get("/api/master/browse?source=master&table=PalletMaster"
                              "&q=P-2&sort=ID&sort_dir=desc", headers=HEADERS)
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body["table"], "PalletMaster")
        self.assertEqual([r["名前"] for r in body["rows"]], ["P-2"])
        self.assertEqual(body["sources"][1]["key"], "history")
        self.assertEqual(body["row_key"], master_browse.ROW_KEY)

    def test_履歴は送り残しの数を言う_無ければ作る(self):
        _record(self.conn)
        body = self.client.get("/api/master/browse?source=history",
                               headers=HEADERS).get_json()
        self.assertEqual(body["error"], "")
        self.assertEqual(body["pending"], 1)
        self.assertEqual(body["table"], "明細履歴")

    def test_書き出しは場所を返す_ダウンロードではない(self):
        res = _post(self.client, "/api/master/export",
                    {"source": "master", "table": "PalletMaster"})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertEqual(res.mimetype, "application/json")
        self.assertNotIn("Content-Disposition", res.headers)
        body = res.get_json()
        self.assertEqual(body["rows"], 2)
        self.assertTrue((Path(body["folder"]) / body["file"]).exists())

    def test_履歴の書き出しは送り残しを先に送る(self):
        _record(self.conn)
        res = _post(self.client, "/api/master/export",
                    {"source": "history", "table": "明細履歴"})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertEqual(res.get_json()["rows"], 1)
        self.assertEqual(slip_history.pending_count(self.conn), 0)

    def test_書き出せなければ理由を言う(self):
        res = _post(self.client, "/api/master/export", {"source": "master", "table": "無い"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("無い", res.get_json()["error"]["message"])

    def test_トークンが無ければ断る(self):
        res = self.client.get("/api/master/browse")
        self.assertEqual(res.status_code, 401)

    def test_書き込みの順番待ちに入れない(self):
        from modules.packing_details import app as app_module
        self.assertTrue("/api/master/export".startswith(app_module._NO_LOCK_PREFIXES))


if __name__ == "__main__":
    unittest.main()
