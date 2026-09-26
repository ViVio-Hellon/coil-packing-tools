"""明細の履歴 (全ライン・3年)

**守ること**
- 出力したら、**同じまとまりで**手元の履歴に残る(明細だけ・履歴だけにならない)
- ロットを切り替えて明細が消えても、履歴は消えない
- 作り直し(No指定の上書き)は消さずに印を付ける。書き足し・紙面を開いたも映す
- 共有へは手元から送る。**二重にならない**・届かなければ残して送り直す
- 3年より前は整理する(共有も手元も、1日1回)
- CSV は Excel でそのまま表になる(副番が日付に化けない)。ダウンロードはしない
"""
from __future__ import annotations

import csv
import sqlite3
import threading
import unittest
from datetime import date
from pathlib import Path

from modules.packing_details.meisai import config, db, history_repo, meisai_service, session, shared_settings, slip_history

from . import _db
from .test_web import HEADERS, TOKEN, _client, _post, _seed


def _shared() -> Path:
    return shared_settings.shared_dir() / config.HISTORY_DB_NAME


def _q(path: Path, sql: str, params=()) -> list[tuple]:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _record(conn, lot="L5160Z0", no=1, keys=("1-10", "2-8"), weights=(250, 248),
            when: str = "") -> str:
    with db.transaction(conn, name="試験"):
        hid = slip_history.record_output(conn, lot_no=lot, seq_no=no, keys=list(keys),
                                         weights=list(weights))
        if when:
            db.write(conn, "UPDATE 明細履歴 SET 出力日時 = ? WHERE 送信ID = ?", (when, hid))
    return hid


class _Base(unittest.TestCase):
    def setUp(self):
        _db.settings_file(self)
        shared_settings.shared_dir().mkdir(parents=True)
        self.conn = _db.case_db(self)


class RecordTest(unittest.TestCase):
    """出力の経路(画面から)で、履歴が残るか。"""

    def setUp(self):
        _db.settings_file(self)
        self.client, self.conn = _client(self)
        _seed(self.conn)
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        _post(self.client, "/api/weights", {"weights": ["250", "248"]})
        _post(self.client, "/api/strands", {})
        _post(self.client, "/api/stack-max", {"value": "2"})

    def _out(self, keys=("1-10", "2-8"), **body):
        for key in keys:
            _post(self.client, "/api/stack", {"key": key})
        return _post(self.client, "/api/output", {"confirm": True, **body})

    def test_出力したら履歴に1枚とコイルが残る(self):
        self._out()
        head = self.conn.execute("SELECT * FROM 明細履歴").fetchall()
        self.assertEqual(len(head), 1)
        h = head[0]
        self.assertEqual((h["ロット番号"], h["No"], h["本数"], h["重量合計"]),
                         ("L5160Z0", 1, 2, 498.0))
        self.assertEqual(h["状態"], "有効")
        self.assertEqual(h["要送信"], 1)
        self.assertTrue(h["出力PC"])
        self.assertTrue(h["版"])
        coils = self.conn.execute("SELECT 順, 副番, 丈, 条, 重量 FROM 明細履歴_副番"
                                  " ORDER BY 順").fetchall()
        self.assertEqual([tuple(c) for c in coils], [(1, "1-10", 1, 10, 250.0),
                                                     (2, "2-8", 2, 8, 248.0)])

    def test_ロットの属性も残す(self):
        """数えるとき(用途・材質・寸法ごと)に使う。画面に出ているロット情報。"""
        self._out()
        h = self.conn.execute("SELECT * FROM 明細履歴").fetchone()
        lot = session.lot()
        self.assertEqual(h["製造材質"], lot.zaishitsu)
        self.assertEqual(h["製造板厚"], lot.seizou_thickness)
        self.assertEqual(h["用途名"], lot.yoto_name)

    def test_明細と履歴は同じ送信IDで結ばれる(self):
        self._out()
        out = meisai_service.find_output(self.conn, "L5160Z0", 1)
        self.assertEqual(out.history_id,
                         self.conn.execute("SELECT 送信ID FROM 明細履歴").fetchone()[0])

    def test_ロットを切り替えても履歴は消えない(self):
        self._out()
        _seed(self.conn, lot="L7110P0")
        _post(self.client, "/api/lot", {"lot_no": "L7110P0", "confirm": True})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM 明細出力").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM 明細履歴").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM 明細履歴_副番").fetchone()[0], 2)

    def test_作り直しは消さずに印を付ける(self):
        self._out()
        first = self.conn.execute("SELECT 送信ID FROM 明細履歴").fetchone()[0]
        self._out(keys=("1-9", "2-7"), specify_no=1)
        rows = self.conn.execute("SELECT 送信ID, 状態, 作り直し日時 FROM 明細履歴"
                                 " ORDER BY 出力日時, rowid").fetchall()
        self.assertEqual(len(rows), 2)
        old = [r for r in rows if r["送信ID"] == first][0]
        self.assertEqual(old["状態"], "作り直し")
        self.assertTrue(old["作り直し日時"])
        self.assertEqual([r["状態"] for r in rows if r["送信ID"] != first], ["有効"])

    def test_書き足しを映す(self):
        self._out()
        out = meisai_service.find_output(self.conn, "L5160Z0", 1)
        res = self.client.post(f"/report/L5160Z0/edits?t={TOKEN}",
                               json={"edits": {f"{out.row_id}.size": "1.985×104.0",
                                               f"{out.row_id}.lotno": "L5160Z0-A"}})
        self.assertEqual(res.status_code, 200)
        h = self.conn.execute("SELECT 手入力サイズ, 手入力ロット番号, 変更回数 FROM 明細履歴").fetchone()
        self.assertEqual((h[0], h[1]), ("1.985×104.0", "L5160Z0-A"))
        self.assertGreaterEqual(h[2], 1)

    def test_紙面を開いたら回数と右上の文字を残す(self):
        self._out()
        self.client.get(f"/report/L5160Z0/1?t={TOKEN}")
        self.client.get(f"/report/L5160Z0?nos=1&t={TOKEN}")
        h = self.conn.execute("SELECT 紙面を開いた回数, 最後に紙面を開いた日時, 右上の文字"
                              " FROM 明細履歴").fetchone()
        self.assertEqual(h[0], 2)
        self.assertTrue(h[1])
        self.assertEqual(h[2], "NLM.NAGOYA.QA")

    def test_出力に失敗したら履歴も残さない(self):
        """同じまとまり。明細だけ・履歴だけ、にならない。"""
        original = slip_history.record_output

        def boom(*a, **k):
            original(*a, **k)
            raise db.WriteError("試験の失敗")
        slip_history.record_output = boom
        self.addCleanup(setattr, slip_history, "record_output", original)
        res = self._out()
        self.assertEqual(res.status_code, 503)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM 明細履歴").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM 明細出力").fetchone()[0], 0)


class SendTest(_Base):
    """共有へ送る。"""

    def test_送ると共有に入り_手元は送った印(self):
        hid = _record(self.conn)
        result = slip_history.send_pending(self.conn)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.sent, 1)
        self.assertEqual(_q(_shared(), "SELECT 送信ID, ロット番号, No FROM 明細履歴"),
                         [(hid, "L5160Z0", 1)])
        self.assertEqual(len(_q(_shared(), "SELECT * FROM 明細履歴_副番")), 2)
        self.assertEqual(self.conn.execute("SELECT 要送信 FROM 明細履歴").fetchone()[0], 0)

    def test_共有のファイルはこのアプリが作る_DELETEで(self):
        self.assertFalse(_shared().exists())
        _record(self.conn)
        slip_history.send_pending(self.conn)
        self.assertTrue(_shared().is_file())
        self.assertEqual(_q(_shared(), "PRAGMA journal_mode")[0][0], "delete")
        names = {p.name for p in _shared().parent.iterdir()}
        self.assertEqual(names, {config.HISTORY_DB_NAME})          # 鍵も -journal も残さない

    def test_送り直しても二重にならない(self):
        hid = _record(self.conn)
        slip_history.send_pending(self.conn)
        db.write(self.conn, "UPDATE 明細履歴 SET 要送信 = 1")
        slip_history.send_pending(self.conn)
        self.assertEqual(len(_q(_shared(), "SELECT * FROM 明細履歴")), 1)
        self.assertEqual(len(_q(_shared(), "SELECT * FROM 明細履歴_副番 WHERE 送信ID = ?",
                                (hid,))), 2)

    def test_送ったあとの変化は送り直すと共有も変わる(self):
        hid = _record(self.conn)
        slip_history.send_pending(self.conn)
        with db.transaction(self.conn):
            slip_history.update_hand(self.conn, hid, "2.0×100", "LOT-X")
            slip_history.mark_replaced(self.conn, hid)
        slip_history.send_pending(self.conn)
        self.assertEqual(_q(_shared(), "SELECT 手入力サイズ, 手入力ロット番号, 状態 FROM 明細履歴"),
                         [("2.0×100", "LOT-X", "作り直し")])

    def test_届かなければ残して_あとで送る(self):
        _record(self.conn)
        original = config.SHARED_DIR
        config.SHARED_DIR = original.parent / "届かない" / "共有"
        result = slip_history.send_pending(self.conn)
        self.assertFalse(result.ok)
        self.assertIn("届きません", result.error)
        self.assertEqual(result.pending, 1)
        self.assertEqual(slip_history.status(self.conn)["last_error"], result.error)
        config.SHARED_DIR = original
        result = slip_history.send_pending(self.conn)
        self.assertEqual((result.sent, result.pending), (1, 0))
        self.assertEqual(slip_history.status(self.conn)["last_error"], "")

    def test_ほかの端末が書いている最中なら送らずに残す(self):
        _record(self.conn)
        lock = shared_settings.shared_dir() / slip_history.LOCK_NAME
        lock.write_text("line2", encoding="utf-8")
        original = shared_settings.LOCK_WAIT_SEC
        shared_settings.LOCK_WAIT_SEC = 0.2
        self.addCleanup(setattr, shared_settings, "LOCK_WAIT_SEC", original)
        result = slip_history.send_pending(self.conn)
        self.assertIn("ほかの端末", result.error)
        self.assertEqual(result.pending, 1)
        self.assertTrue(lock.exists())

    def test_2台のPCから送っても両方入る(self):
        other = _db.case_db(self)
        a = _record(self.conn, no=1)
        b = _record(other, no=2)
        slip_history.send_pending(self.conn)
        slip_history.send_pending(other)
        self.assertEqual(sorted(r[0] for r in _q(_shared(), "SELECT 送信ID FROM 明細履歴")),
                         sorted([a, b]))

    def test_送っている間に書き足されたら送れたことにしない(self):
        """読んでから送るまでに変わると、共有には古い値が入っている。"""
        hid = _record(self.conn)
        original = slip_history._write_rows

        def during(hconn, rows, details):
            original(hconn, rows, details)
            with db.transaction(self.conn):
                slip_history.update_hand(self.conn, hid, "新しい", "")
        slip_history._write_rows = during
        self.addCleanup(setattr, slip_history, "_write_rows", original)
        slip_history.send_pending(self.conn)
        self.assertEqual(self.conn.execute("SELECT 要送信 FROM 明細履歴").fetchone()[0], 1)
        slip_history._write_rows = original
        slip_history.send_pending(self.conn)
        self.assertEqual(_q(_shared(), "SELECT 手入力サイズ FROM 明細履歴"), [("新しい",)])

    def test_同時に送るのは1本だけ(self):
        """裏の送りと「いま送る」が重なっても、2本目は待たずに帰る。"""
        _conn, path = _db.file_db(self)
        _record(_conn)
        started, release = threading.Event(), threading.Event()
        original = slip_history._write_rows

        def slow(*a):
            started.set()
            release.wait(5)
            original(*a)
        slip_history._write_rows = slow
        self.addCleanup(setattr, slip_history, "_write_rows", original)

        def first():                     # 裏の送り(自分の接続で)
            with db.connect(path) as own:
                slip_history.send_pending(own)
        t = threading.Thread(target=first)
        t.start()
        self.assertTrue(started.wait(5))
        self.assertTrue(slip_history.send_pending(self.conn).busy)
        release.set()
        t.join(10)
        self.assertEqual(len(_q(_shared(), "SELECT * FROM 明細履歴")), 1)

    def test_手元と共有の列が揃っている(self):
        local = [r[1] for r in self.conn.execute("PRAGMA table_info(明細履歴)")]
        self.assertEqual(tuple(c for c in local if c not in slip_history.LOCAL_ONLY),
                         slip_history.HEADER_COLUMNS)
        detail = [r[1] for r in self.conn.execute("PRAGMA table_info(明細履歴_副番)")]
        self.assertEqual(tuple(detail), slip_history.DETAIL_COLUMNS)
        _record(self.conn)
        slip_history.send_pending(self.conn)
        shared = [r[1] for r in _q(_shared(), "PRAGMA table_info(明細履歴)")]
        self.assertEqual(tuple(shared), slip_history.HEADER_COLUMNS)


class PurgeTest(_Base):
    """3年より前は整理する。"""

    def test_3年前の今日より前を消す(self):
        self.assertEqual(slip_history.cutoff(date(2026, 9, 25)), "2023-09-25 00:00:00")
        self.assertEqual(slip_history.cutoff(date(2028, 2, 29)), "2025-02-28 00:00:00")

    def test_共有も手元も古いものを整理し_新しいものは残す(self):
        limit = slip_history.cutoff()
        old = _record(self.conn, no=1, when="2000-01-01 10:00:00")
        edge = _record(self.conn, no=2, when=limit)                     # ちょうど境目は残す
        new = _record(self.conn, no=3)
        # 先に古いのも共有へ入れておく(整理の前の形)
        slip_history._shared_purged_on = None
        with shared_settings.folder_lock(shared_settings.shared_dir(), slip_history.LOCK_NAME):
            h = slip_history._open_shared(_shared())
            rows = self.conn.execute(
                f"SELECT {', '.join(slip_history.HEADER_COLUMNS)} FROM 明細履歴").fetchall()
            details = self.conn.execute("SELECT * FROM 明細履歴_副番").fetchall()
            slip_history._write_rows(h, rows, details)
            h.commit()
            h.close()
        result = slip_history.send_pending(self.conn)
        self.assertEqual(result.purged_shared, 1)
        self.assertEqual(sorted(r[0] for r in _q(_shared(), "SELECT 送信ID FROM 明細履歴")),
                         sorted([edge, new]))
        self.assertEqual(_q(_shared(), "SELECT COUNT(*) FROM 明細履歴_副番 WHERE 送信ID = ?",
                            (old,)), [(0,)])
        self.assertEqual(sorted(r[0] for r in self.conn.execute("SELECT 送信ID FROM 明細履歴")),
                         sorted([edge, new]))

    def test_整理は1日1回(self):
        _record(self.conn)
        slip_history.send_pending(self.conn)
        self.assertEqual(_q(_shared(), "SELECT 値 FROM 整理記録 WHERE 項目 = '最後に整理した日'"),
                         [(date.today().isoformat(),)])
        # 今日はもう整理した。送るものが無ければ共有に触らない
        before = _shared().stat().st_mtime_ns
        self.assertEqual(slip_history.send_pending(self.conn).sent, 0)
        self.assertEqual(_shared().stat().st_mtime_ns, before)


class ExportTest(_Base):
    """CSV に書き出す。**Excel でそのまま表になる。**"""

    def _rows(self, name: str) -> list[list[str]]:
        path = config.EXPORT_DIR / name
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))                # BOM(Excel 用)
        self.assertIn(b"\r\n", raw)
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return list(csv.reader(handle))

    def test_明細と集計の2つを書く(self):
        _record(self.conn, no=1)
        _record(self.conn, no=2, keys=("1-9",), weights=(250, 248))
        slip_history.send_pending(self.conn)
        today = date.today().isoformat()
        result = slip_history.export_csv(today, today)
        self.assertEqual((result.detail_rows, result.slips), (3, 2))
        detail = self._rows(result.detail_file)
        self.assertEqual(tuple(detail[0]), slip_history.DETAIL_CSV_COLUMNS)
        self.assertEqual(len(detail), 4)
        summary = self._rows(result.summary_file)
        self.assertEqual(tuple(summary[0]), slip_history.SUMMARY_CSV_COLUMNS)
        self.assertEqual(summary[1][2:5], ["2", "3", "748.0"])          # 2枚・3本・重量合計

    def test_副番が日付に化けない(self):
        """Excel は 1-10 を「1月10日」にする。="1-10" で書く。"""
        _record(self.conn, keys=("1-10",))
        slip_history.send_pending(self.conn)
        today = date.today().isoformat()
        detail = self._rows(slip_history.export_csv(today, today).detail_file)
        col = slip_history.DETAIL_CSV_COLUMNS.index("副番")
        self.assertEqual(detail[1][col], '="1-10"')

    def test_打ち込まれた文字を式として動かさない(self):
        hid = _record(self.conn)
        with db.transaction(self.conn):
            slip_history.update_hand(self.conn, hid, "=HYPERLINK(\"x\")", "-1+1")
        slip_history.send_pending(self.conn)
        today = date.today().isoformat()
        detail = self._rows(slip_history.export_csv(today, today).detail_file)
        cols = slip_history.DETAIL_CSV_COLUMNS
        self.assertEqual(detail[1][cols.index("手入力サイズ")], '="=HYPERLINK(""x"")"')
        self.assertEqual(detail[1][cols.index("手入力ロット番号")], '="-1+1"')

    def test_作り直しは明細には出し_集計では数えない(self):
        hid = _record(self.conn)
        _record(self.conn, keys=("1-9",))
        with db.transaction(self.conn):
            slip_history.mark_replaced(self.conn, hid)
        slip_history.send_pending(self.conn)
        today = date.today().isoformat()
        result = slip_history.export_csv(today, today)
        summary = self._rows(result.summary_file)
        self.assertEqual(summary[1][2:], ["1", "1", "250.0", "1"])
        detail = self._rows(result.detail_file)
        self.assertEqual(len(detail), 4)

    def test_期間とロット番号で絞る(self):
        _record(self.conn, lot="L5160Z0", when="2026-01-10 08:00:00")
        _record(self.conn, lot="L7110P0", when="2026-01-10 09:00:00")
        _record(self.conn, lot="L5160Z0", when="2026-02-01 00:00:00")
        slip_history.send_pending(self.conn)
        result = slip_history.export_csv("2026-01-01", "2026-01-31", "l5160")
        self.assertEqual(result.slips, 1)
        result = slip_history.export_csv("2026-01-10", "2026-01-10")
        self.assertEqual(result.slips, 2)

    def test_日付が読めない_逆さま_は理由を言う(self):
        for args, word in ((("2026/1/1", "2026-01-31"), "読めません"),
                           (("2026-02-01", "2026-01-01"), "後になって")):
            with self.subTest(args=args):
                with self.assertRaises(slip_history.HistoryError) as caught:
                    slip_history.export_csv(*args)
                self.assertIn(word, str(caught.exception))

    def test_共有に履歴がまだ無ければ作って見出しだけ書く(self):
        """「まだ無い」で止めない(現場の指定: 無ければ自動で作る)。"""
        self.assertFalse(_shared().exists())
        result = slip_history.export_csv("2026-01-01", "2026-01-31")
        self.assertTrue(_shared().exists())
        self.assertEqual(result.detail_rows, 0)
        self.assertIn("ありませんでした", result.note)
        self.assertEqual(self._rows(result.detail_file),
                         [list(slip_history.DETAIL_CSV_COLUMNS)])

    def test_共有フォルダに届かなければ書き出さない(self):
        shared_settings.shared_dir().rmdir()
        with self.assertRaises(slip_history.HistoryError) as caught:
            slip_history.export_csv("2026-01-01", "2026-01-31")
        self.assertIn("届きません", str(caught.exception))

    def test_こちらからファイルやExcelを開かない(self):
        """開くのは使う人がすること(現場の指定)。書き出しは書くだけ。"""
        import inspect
        from modules.packing_details.app.routes import history as history_routes
        for module in (slip_history, history_routes):
            with self.subTest(module=module.__name__):
                source = inspect.getsource(module)
                self.assertNotIn("startfile(", source)
                self.assertNotIn("subprocess", source)
                self.assertNotIn("webbrowser", source)


class SearchTest(_Base):
    """探す(このコイルはどの紙か)。"""

    def setUp(self):
        super().setUp()
        self.a = _record(self.conn, lot="L5160Z0", no=1, keys=("1-10", "2-8"),
                         when="2026-01-10 08:00:00")
        self.b = _record(self.conn, lot="L5160Z0", no=2, keys=("1-9", "2-7"),
                         when="2026-01-10 09:00:00")
        self.c = _record(self.conn, lot="L7110P0", no=1, keys=("1-10",),
                         when="2026-01-11 09:00:00")
        slip_history.send_pending(self.conn)

    def test_副番でどの紙かが分かる(self):
        found = slip_history.search(self.conn, "2026-01-01", "2026-01-31", "L5160Z0", "1-10")
        self.assertEqual([r["送信ID"] for r in found.rows], [self.a])
        self.assertEqual(found.rows[0]["副番の並び"], "1-10 2-8")
        self.assertEqual(found.source, "shared")

    def test_副番だけなら全ロットから(self):
        found = slip_history.search(self.conn, "2026-01-01", "2026-01-31", "", " 1 - 10 ")
        self.assertEqual({r["送信ID"] for r in found.rows}, {self.a, self.c})

    def test_新しい順に並ぶ(self):
        found = slip_history.search(self.conn, "2026-01-01", "2026-01-31")
        self.assertEqual([r["送信ID"] for r in found.rows], [self.c, self.b, self.a])

    def test_多すぎれば並べた数と合う数を言う(self):
        found = slip_history.search(self.conn, "2026-01-01", "2026-01-31", limit=2)
        self.assertEqual((len(found.rows), found.total), (2, 3))
        self.assertIn("3枚", found.note)

    def test_共有に届かなければこのPCの分から_そう言う(self):
        original = config.SHARED_DIR
        config.SHARED_DIR = original.parent / "届かない" / "共有"
        self.addCleanup(setattr, config, "SHARED_DIR", original)
        found = slip_history.search(self.conn, "2026-01-01", "2026-01-31", "L5160Z0")
        self.assertEqual(found.source, "local")
        self.assertEqual(len(found.rows), 2)
        self.assertIn("この PC の分だけ", found.note)


class ReprintTest(_Base):
    """履歴から紙面を作り直す(読むだけ)。"""

    def _slip(self, **kw):
        hid = _record(self.conn, **kw)
        return hid

    def test_記録のとおりに組む(self):
        hid = self._slip(keys=("2-8", "1-10"), weights=(250, 248))    # 積んだ順のまま
        with db.transaction(self.conn):
            slip_history.update_hand(self.conn, hid, "1.985×104.0", "L5160Z0-A")
            slip_history.note_opened(self.conn, [hid], "NLM.OLD.QA")
        slip_history.send_pending(self.conn)
        slips, source = slip_history.load_slips(self.conn, [hid])
        self.assertEqual(source, "shared")
        from modules.packing_details.meisai import report
        html = report.render_history(slips, current_qa="NLM.NOW.QA", source=source)
        self.assertLess(html.index(">2-8<"), html.index(">1-10<"))
        self.assertIn("248.0Kg", html)
        self.assertIn("250.0Kg", html)
        self.assertIn("1.985×104.0", html)
        self.assertIn("L5160Z0-A", html)
        self.assertIn('<span class="qa-text">NLM.OLD.QA</span>', html)   # 刷ったときの文字
        self.assertNotIn("NLM.NOW.QA", html)
        self.assertNotIn("data-edit", html)                            # 書き足しはできない
        self.assertIn("var SYNC = false", html)                         # いまの文字へ差し替えない
        self.assertIn("履歴から作り直した紙面", html)

    def test_右上の文字の記録が無ければいまの文字で_そう言う(self):
        hid = self._slip()
        slips, source = slip_history.load_slips(self.conn, [hid])
        from modules.packing_details.meisai import report
        html = report.render_history(slips, current_qa="NLM.NOW.QA", source=source)
        self.assertIn('<span class="qa-text">NLM.NOW.QA</span>', html)
        self.assertIn("記録が無いため", html)

    def test_作り直された紙はそう言う(self):
        hid = self._slip()
        with db.transaction(self.conn):
            slip_history.mark_replaced(self.conn, hid)
        slips, source = slip_history.load_slips(self.conn, [hid])
        from modules.packing_details.meisai import report
        html = report.render_history(slips, current_qa="Q", source=source)
        self.assertIn("このあと作り直されています", html)

    def test_頼んだ順に並べ_見つからなければ作らない(self):
        a, b = self._slip(no=1), self._slip(no=2)
        slip_history.send_pending(self.conn)
        slips, _ = slip_history.load_slips(self.conn, [b, a])
        self.assertEqual([s["head"]["送信ID"] for s in slips], [b, a])
        with self.assertRaises(slip_history.HistoryError):
            slip_history.load_slips(self.conn, [a, "無い送信ID"])
        with self.assertRaises(slip_history.HistoryError):
            slip_history.load_slips(self.conn, [])

    def test_一度に作り直せるのは20枚まで(self):
        ids = [self._slip(no=n) for n in range(1, slip_history.LOAD_LIMIT + 2)]
        with self.assertRaises(slip_history.HistoryError) as caught:
            slip_history.load_slips(self.conn, ids)
        self.assertIn("20枚まで", str(caught.exception))

    def test_まだ送れていない紙はこのPCの手元から(self):
        hid = self._slip()                                  # 送っていない
        slips, source = slip_history.load_slips(self.conn, [hid])
        self.assertEqual(source, "local")
        self.assertEqual(slips[0]["head"]["送信ID"], hid)

    def test_作り直しても履歴は書き換えない(self):
        hid = self._slip()
        slip_history.send_pending(self.conn)
        before = _q(_shared(), "SELECT * FROM 明細履歴")
        slips, source = slip_history.load_slips(self.conn, [hid])
        from modules.packing_details.meisai import report
        report.render_history(slips, current_qa="Q", source=source)
        self.assertEqual(_q(_shared(), "SELECT * FROM 明細履歴"), before)
        self.assertEqual(self.conn.execute("SELECT 要送信 FROM 明細履歴").fetchone()[0], 0)


class EnsureSharedTest(_Base):
    """共有の履歴の表が**無ければ作る**(現場の指定)。ファイル・表・列。"""

    def _tables(self) -> set[str]:
        return {r[0] for r in _q(_shared(), "SELECT name FROM sqlite_master"
                                            " WHERE type = 'table'")}

    def test_ファイルが無ければ作る_2回目は何もしない(self):
        self.assertTrue(slip_history.ensure_shared())
        self.assertEqual(self._tables(), {"明細履歴", "明細履歴_副番", "整理記録"})
        self.assertEqual(_q(_shared(), "PRAGMA journal_mode")[0][0].lower(), "delete")
        self.assertFalse(slip_history.ensure_shared())
        # 鍵は残さない
        self.assertFalse((shared_settings.shared_dir() / slip_history.LOCK_NAME).exists())

    def test_空のファイルや表の無いファイルなら表を作る(self):
        for label, prepare in (("空のファイル", lambda: _shared().write_bytes(b"")),
                               ("ほかの表だけ", lambda: _q(_shared(),
                                                          "CREATE TABLE メモ (x TEXT)"))):
            with self.subTest(label):
                if _shared().exists():
                    _shared().unlink()
                prepare()
                self.assertTrue(slip_history.ensure_shared())
                self.assertTrue({"明細履歴", "明細履歴_副番", "整理記録"} <= self._tables())

    def test_途中で表が消えても_探すときに作り直す(self):
        _record(self.conn)
        slip_history.send_pending(self.conn)
        _q(_shared(), "DROP TABLE 明細履歴_副番")
        result = slip_history.search(self.conn, "2000-01-01", "2999-12-31")
        self.assertEqual(result.source, "shared")
        self.assertIn("明細履歴_副番", self._tables())
        self.assertEqual(result.total, 1)

    def test_確かめた控えが古くても_読むときに作り直す(self):
        """ファイルの様子が同じに見えても、表が無ければ作り直して読み直す。"""
        _record(self.conn)
        slip_history.send_pending(self.conn)
        slip_history.search(self.conn, "2000-01-01", "2999-12-31")     # 控えができる
        stamp = slip_history._shape_ok[str(_shared())]
        _q(_shared(), "DROP TABLE 明細履歴_副番")
        slip_history._shape_ok[str(_shared())] = slip_history._file_stamp(_shared())
        self.assertNotEqual(stamp, None)
        result = slip_history.search(self.conn, "2000-01-01", "2999-12-31")
        self.assertEqual((result.source, result.total), ("shared", 1))
        self.assertIn("明細履歴_副番", self._tables())

    def test_手で作った表に足りない列は足して_送れる(self):
        conn = sqlite3.connect(str(_shared()))
        conn.execute("CREATE TABLE 明細履歴 (送信ID TEXT PRIMARY KEY, ロット番号 TEXT NOT NULL,"
                     " No INTEGER NOT NULL, 出力日時 TEXT NOT NULL)")
        conn.commit()
        conn.close()
        self.assertTrue(slip_history.ensure_shared())
        have = {r[1] for r in _q(_shared(), "PRAGMA table_info(明細履歴)")}
        self.assertEqual(have, set(slip_history.HEADER_COLUMNS))
        _record(self.conn)
        self.assertEqual(slip_history.send_pending(self.conn).sent, 1)
        self.assertEqual(_q(_shared(), "SELECT 本数, 状態 FROM 明細履歴"), [(2, "有効")])

    def test_主キーが無い表には一意を足して_送り直しても増えない(self):
        conn = sqlite3.connect(str(_shared()))
        conn.executescript(slip_history.SHARED_DDL.replace(
            "送信ID            TEXT    PRIMARY KEY", "送信ID            TEXT    NOT NULL"))
        conn.close()
        slip_history.ensure_shared()
        hid = _record(self.conn)
        slip_history.send_pending(self.conn)
        # 送り直す(書き足しで要送信に戻る)
        slip_history.update_hand(self.conn, hid, "S", "L")
        slip_history.send_pending(self.conn)
        self.assertEqual(_q(_shared(), "SELECT COUNT(*), 手入力サイズ FROM 明細履歴"),
                         [(1, "S")])

    def test_足せない列が無い表は断って_触らない(self):
        conn = sqlite3.connect(str(_shared()))
        conn.execute("CREATE TABLE 明細履歴 (ロット番号 TEXT, No INTEGER)")
        conn.commit()
        conn.close()
        with self.assertRaises(slip_history.HistoryError) as caught:
            slip_history.ensure_shared()
        self.assertIn("送信ID", str(caught.exception))
        self.assertEqual({r[1] for r in _q(_shared(), "PRAGMA table_info(明細履歴)")},
                         {"ロット番号", "No"})
        # 送りも同じ理由で断る(形の違う表へ黙って書かない)
        _record(self.conn)
        result = slip_history.send_pending(self.conn)
        self.assertEqual(result.sent, 0)
        self.assertIn("送信ID", result.error)

    def test_共有フォルダに届かなければ作らない(self):
        shared_settings.shared_dir().rmdir()
        with self.assertRaises(slip_history.HistoryError) as caught:
            slip_history.ensure_shared()
        self.assertIn("届きません", str(caught.exception))
        self.assertFalse(_shared().exists())


class HistoryWebTest(unittest.TestCase):
    """画面の口。"""

    def setUp(self):
        _db.settings_file(self)
        shared_settings.shared_dir().mkdir(parents=True)
        self.client, self.conn = _client(self)

    def test_様子を返す(self):
        body = self.client.get("/api/history/status", headers=HEADERS).get_json()
        self.assertEqual(body["pending"], 0)
        self.assertEqual(body["keep_years"], 3)
        self.assertTrue(body["shared_path"].endswith(config.HISTORY_DB_NAME))
        # 無ければ作る(現場の指定)。作ったばかりなので0枚
        self.assertEqual(body["shared_problem"], "")
        self.assertEqual((body["shared_count"], body["shared_coils"]), (0, 0))
        self.assertTrue(_shared().exists())

    def test_書き出しは場所を返す_ダウンロードではない(self):
        _record(self.conn)
        slip_history.send_pending(self.conn)
        today = date.today().isoformat()
        res = _post(self.client, "/api/history/export", {"from": today, "to": today})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertEqual(res.mimetype, "application/json")
        body = res.get_json()
        self.assertTrue((config.EXPORT_DIR / body["detail_file"]).is_file())
        self.assertEqual(body["folder"], str(config.EXPORT_DIR))

    def test_トークンが要る(self):
        self.assertEqual(self.client.get("/api/history/status").status_code, 401)
        self.assertEqual(self.client.get("/report/history?ids=x").status_code, 401)

    def test_開く口は無い(self):
        res = _post(self.client, "/api/history/open", {"name": "x.csv"})
        self.assertIn(res.status_code, (404, 405))

    def test_探して_選んだ紙面を開ける(self):
        hid = _record(self.conn)
        slip_history.send_pending(self.conn)
        today = date.today().isoformat()
        body = _post(self.client, "/api/history/search",
                     {"from": today, "to": today, "fuban": "1-10"}).get_json()
        self.assertEqual([r["送信ID"] for r in body["rows"]], [hid])
        self.assertEqual(body["source"], "shared")
        res = self.client.get(f"/report/history?ids={hid}&t={TOKEN}")
        self.assertEqual(res.status_code, 200)
        self.assertIn("L5160Z0-No1  梱包明細表", res.get_data(as_text=True))

    def test_作り直す口は画面の持ち主でなくても通る(self):
        """紙面は別のタブで開く(ほかの紙面と同じ扱い)。"""
        hid = _record(self.conn)
        from modules.packing_details.meisai import screen
        screen.hand_over()
        screen.claim("別の画面")
        res = self.client.get(f"/report/history?ids={hid}&t={TOKEN}")
        self.assertEqual(res.status_code, 200)

    def test_見つからなければ理由を出す(self):
        res = self.client.get(f"/report/history?ids=無い&t={TOKEN}")
        self.assertEqual(res.status_code, 404)
        self.assertIn("作り直せませんでした", res.get_data(as_text=True))

    def test_日付が読めなければ400(self):
        res = _post(self.client, "/api/history/search", {"from": "x", "to": "y"})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
