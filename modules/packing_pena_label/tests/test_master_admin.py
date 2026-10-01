# -*- coding: utf-8 -*-
"""マスタ管理（設定 → マスタ管理）の検証。

`ViVio-Hellon/python-web-tools` からの移植。考え方はそのままで、
このツールには取り込み工程が無いぶん単純になっている。

    移植元: 画面 → 共有ファイルへ書く → その表だけ取り込み直す → 手元が追いつく
    こちら: 画面 → 共有ファイルへ書く → キャッシュを捨てる → 次の読みで反映
"""

import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from modules.packing_pena_label import server
from modules.packing_pena_label.app.config import Config                          # noqa: E402
from modules.packing_pena_label.app.repositories.material_repo import MaterialRepository  # noqa: E402
from modules.packing_pena_label.app.services import master_admin as MA            # noqa: E402


def make_master(path, rows=None):
    rows = rows or [(1, "テスラピン", "0.5", "1"),
                    (2, "ﾊｰﾄﾞﾎﾞｰﾄﾞ", "1.25", "1"),
                    (3, "ｽﾄﾚｯﾁ", "0.08", "1")]
    c = sqlite3.connect(path)
    c.execute("DROP TABLE IF EXISTS 資材重量")
    c.execute("CREATE TABLE 資材重量 (管理番号 INTEGER, 梱包資材名 TEXT,"
              " 単位質量 TEXT, 係数 TEXT)")
    c.executemany("INSERT INTO 資材重量 VALUES (?,?,?,?)", rows)
    c.commit()
    c.close()


class MasterAdminServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "梱包資材マスタ.sqlite3")
        make_master(self.db)
        self.repo = MaterialRepository(accdb_dir="", csv_path="",
                                       prefer_access=False, db_path=self.db,
                                       refresh_sec=0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------- 読み
    def test_rows_carry_the_source_row_id(self):
        """行は管理番号ではなく取り込み元の rowid で指すこと。

        管理番号は業務の列で、重複も欠番もありうる。
        ここを取り違えると「直したつもりが別の行だった」になる。
        """
        p = MA.page(self.repo, "資材重量")
        self.assertEqual(p["rowKey"], "__行")
        self.assertTrue(all("__行" in r for r in p["rows"]))
        self.assertNotIn("__行", p["columns"], "隠し列を業務の列に混ぜない")

    def test_keyword_matches_any_column(self):
        p = MA.page(self.repo, "資材重量", keyword="ピン")
        self.assertEqual([r["梱包資材名"] for r in p["rows"]], ["テスラピン"])
        self.assertEqual(p["matched"], 1)
        self.assertEqual(p["total"], 3)

    def test_does_not_show_everything_and_says_so(self):
        """出さなかった分は必ず数で言うこと。黙って切らない。"""
        rows = [(i, "資材%03d" % i, "0.1", "1") for i in range(1, 301)]
        make_master(self.db, rows)
        self.repo.clear_cache()
        p = MA.page(self.repo, "資材重量")
        self.assertEqual(len(p["rows"]), MA.ROW_LIMIT)
        self.assertIn("300", p["note"])
        self.assertIn(str(MA.ROW_LIMIT), p["note"])

    # ---------------------------------------------------- 並べ替え(見出しクリック)
    def test_sort_by_a_column_both_ways(self):
        """見出しを押すとその列で並べ替える。もう一度押すと逆順(現場の指摘)。"""
        names = lambda p: [r["梱包資材名"] for r in p["rows"]]
        up = MA.page(self.repo, "資材重量", sort="単位質量", sort_dir="asc")
        self.assertEqual(names(up), ["ｽﾄﾚｯﾁ", "テスラピン", "ﾊｰﾄﾞﾎﾞｰﾄﾞ"])
        self.assertEqual((up["sort"], up["sortDir"]), ("単位質量", "asc"))
        down = MA.page(self.repo, "資材重量", sort="単位質量", sort_dir="desc")
        self.assertEqual(names(down), ["ﾊｰﾄﾞﾎﾞｰﾄﾞ", "テスラピン", "ｽﾄﾚｯﾁ"])
        self.assertEqual(down["sortDir"], "desc")

    def test_numbers_kept_as_text_sort_by_value(self):
        """資材マスタは数字も文字で持つことがある。文字の並びだと 10 が 9 より前へ来る。"""
        make_master(self.db, [(1, "A", "10", "1"), (2, "B", "9", "1"),
                              (3, "C", "0.5", "1"), (4, "D", "", "1")])
        up = MA.page(self.repo, "資材重量", sort="単位質量", sort_dir="asc")
        self.assertEqual([r["単位質量"] for r in up["rows"]], ["0.5", "9", "10", ""])
        down = MA.page(self.repo, "資材重量", sort="単位質量", sort_dir="desc")
        self.assertEqual([r["単位質量"] for r in down["rows"]], ["10", "9", "0.5", ""])

    def test_without_sort_rows_keep_the_source_order(self):
        p = MA.page(self.repo, "資材重量")
        self.assertEqual([r["管理番号"] for r in p["rows"]], ["1", "2", "3"])
        self.assertEqual(p["sort"], "")

    def test_unknown_column_falls_back_quietly(self):
        """実在しない列(表を切り替えた・列が消えた)は、押していないのと同じ並びへ。

        列名は ORDER BY に組み込むので、実在する列だけを許す。
        """
        for bad in ("無い列", '管理番号" DESC; DROP TABLE 資材重量; --'):
            p = MA.page(self.repo, "資材重量", sort=bad, sort_dir="desc")
            self.assertEqual([r["管理番号"] for r in p["rows"]], ["1", "2", "3"], bad)
            self.assertEqual(p["sort"], "")
        self.assertEqual(MA.page(self.repo, "資材重量")["total"], 3, "表は無事")

    def test_sort_covers_rows_that_are_not_shown(self):
        """並べ替えてから先頭を出す。出していない行(200件より後)も含めた並び。"""
        rows = [(i, "資材%03d" % i, "0.1", "1") for i in range(1, 301)]
        make_master(self.db, rows)
        p = MA.page(self.repo, "資材重量", sort="管理番号", sort_dir="desc")
        self.assertEqual(p["rows"][0]["管理番号"], "300")
        self.assertEqual(len(p["rows"]), MA.ROW_LIMIT)

    def test_sort_and_keyword_together(self):
        p = MA.page(self.repo, "資材重量", keyword="1", sort="梱包資材名", sort_dir="desc")
        self.assertEqual(p["matched"], 3)
        self.assertEqual([r["梱包資材名"] for r in p["rows"]],
                         sorted([r["梱包資材名"] for r in p["rows"]], reverse=True))

    def test_unknown_table_is_refused(self):
        with self.assertRaises(MA.Refused) as cm:
            MA.page(self.repo, "班員名簿")
        self.assertEqual(cm.exception.kind, MA.REFUSE_NOT_EDITABLE)

    def test_only_tables_this_tool_uses_are_listed(self):
        """個人情報を含む表（班員名簿など）を一覧に出さないこと。"""
        names = [t["table"] for t in MA.tables(self.repo)]
        self.assertEqual(names, ["資材重量", "ラベル台紙一覧"])
        self.assertNotIn("班員名簿", names)
        self.assertNotIn("アクセス権限", names)

    # ---------------------------------------------------- 書き
    def test_update_reaches_the_calculation(self):
        """直した内容が次の読みで計算に入ること。"""
        self.assertEqual(self.repo.load().unit_of("テスラピン"), ("0.5", "1"))
        p = MA.page(self.repo, "資材重量", keyword="テスラピン")
        row = p["rows"][0]
        MA.save_row(self.repo, "資材重量", int(row["__行"]),
                    {"単位質量": "9.99"})
        self.assertEqual(self.repo.load().unit_of("テスラピン"), ("9.99", "1"))

    def test_insert_and_delete(self):
        r = MA.save_row(self.repo, "資材重量", None,
                        {"管理番号": "99", "梱包資材名": "新資材",
                         "単位質量": "0.4", "係数": "1"})
        self.assertEqual(r["action"], "追加")
        self.assertEqual(self.repo.load().unit_of("新資材"), ("0.4", "1"))

        MA.delete_row(self.repo, "資材重量", int(r["rowId"]))
        self.assertEqual(self.repo.load().unit_of("新資材"), ("", ""))

    def test_a_backup_is_taken_before_writing(self):
        """共有のマスタを壊したら戻せないので、書く前に控えを取る。"""
        r = MA.save_row(self.repo, "資材重量", None, {"梱包資材名": "控え確認"})
        self.assertTrue(r["backup"])
        self.assertTrue(os.path.exists(r["backup"]))
        c = sqlite3.connect(r["backup"])
        try:
            n = c.execute("SELECT COUNT(*) FROM 資材重量").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(n, 3, "控えは書く前の状態であること")

    def test_unknown_column_is_refused(self):
        with self.assertRaises(MA.Refused) as cm:
            MA.save_row(self.repo, "資材重量", None, {"存在しない列": "x"})
        self.assertEqual(cm.exception.kind, MA.REFUSE_BAD_VALUE)

    def test_a_row_someone_else_deleted_is_refused(self):
        """先を越された場合。黙って別の行を書かない。"""
        p = MA.page(self.repo, "資材重量")
        gone = int(p["rows"][0]["__行"])
        MA.delete_row(self.repo, "資材重量", gone)
        with self.assertRaises(MA.Refused) as cm:
            MA.save_row(self.repo, "資材重量", gone, {"単位質量": "1"})
        self.assertEqual(cm.exception.kind, MA.REFUSE_NO_ROW)
        self.assertEqual(cm.exception.status, 409)

    def test_column_names_are_quoted(self):
        """列名に変な文字があっても壊れない（識別子を必ず囲む）。"""
        self.assertEqual(MA._quote('a"b'), '"a""b"')

    # ---------------------------------------------------- 直せないとき
    def test_csv_source_is_not_editable(self):
        repo = MaterialRepository(accdb_dir="", csv_path="/tmp/x.csv",
                                  prefer_access=False, db_path="",
                                  refresh_sec=0)
        self.assertFalse(MA.can_edit(repo))
        self.assertIn("SQLite", MA.editable_why_not(repo))
        with self.assertRaises(MA.Refused) as cm:
            MA.save_row(repo, "資材重量", None, {"梱包資材名": "x"})
        self.assertEqual(cm.exception.status, 403)

    def test_read_only_file_is_refused_with_a_reason(self):
        os.chmod(self.db, 0o444)
        try:
            if os.access(self.db, os.W_OK):
                self.skipTest("root なので読み取り専用にできない")
            self.assertIn("読み取り専用", MA.editable_why_not(self.repo))
        finally:
            os.chmod(self.db, 0o644)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MasterAdminHttpTest(unittest.TestCase):
    """断りが HTTP の番号に写ること。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "梱包資材マスタ.sqlite3")
        make_master(cls.db)
        cfg = Config()
        cfg.port = free_port()
        cfg.prefer_access = False
        cfg.material_db_file = cls.db
        cfg.__class__.local_dir = property(lambda self, d=cls.tmp: d)
        cls.httpd, cls.ctx = server.create_server(cfg)
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, kwargs={"poll_interval": 0.1},
            daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cfg.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.ctx.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_tables_and_rows(self):
        _, j = self.post("/api/master/tables", {})
        self.assertTrue(j["ok"])
        self.assertTrue(j["editable"], j.get("whyNot"))
        st, j = self.post("/api/master/rows", {"table": "資材重量"})
        self.assertEqual(st, 200)
        self.assertIn("梱包資材名", j["columns"])

    def test_rows_can_be_sorted(self):
        st, j = self.post("/api/master/rows",
                          {"table": "資材重量", "sort": "管理番号", "sortDir": "desc"})
        self.assertEqual(st, 200)
        self.assertEqual((j["sort"], j["sortDir"]), ("管理番号", "desc"))
        ids = [int(r["管理番号"]) for r in j["rows"]]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_settings_page_has_sortable_headers(self):
        with urllib.request.urlopen(self.base + "/settings", timeout=20) as r:
            html = r.read().decode("utf-8")
        self.assertIn("mSortBy", html)
        self.assertIn('className = "sortbtn"', html)

    def test_unknown_table_is_422(self):
        st, j = self.post("/api/master/rows", {"table": "アクセス権限"})
        self.assertEqual(st, 422)
        self.assertEqual(j["refuse"], MA.REFUSE_NOT_EDITABLE)

    def test_bad_row_id_is_400(self):
        st, _ = self.post("/api/master/save",
                          {"table": "資材重量", "rowId": "あ",
                           "values": {"係数": "1"}, "password": "nisk"})
        self.assertEqual(st, 400)

    def test_missing_row_is_409(self):
        st, j = self.post("/api/master/save",
                          {"table": "資材重量", "rowId": 9999,
                           "values": {"係数": "1"}, "password": "nisk"})
        self.assertEqual(st, 409)
        self.assertEqual(j["refuse"], MA.REFUSE_NO_ROW)

    def test_editing_needs_the_password(self):
        """マスタを直すと全員の計算・印刷に影響するので合言葉が要る。"""
        _, rows = self.post("/api/master/rows",
                            {"table": "資材重量", "keyword": "ｽﾄﾚｯﾁ"})
        rid = rows["rows"][0]["__行"]
        st, j = self.post("/api/master/save",
                          {"table": "資材重量", "rowId": rid,
                           "values": {"単位質量": "0.99"}})
        self.assertEqual(st, 403)
        self.assertTrue(j["needPassword"])

        st, j = self.post("/api/master/save",
                          {"table": "資材重量", "rowId": rid,
                           "values": {"単位質量": "0.99"}, "password": "ちがう"})
        self.assertEqual(st, 403)

    def test_save_round_trip(self):
        _, rows = self.post("/api/master/rows",
                            {"table": "資材重量", "keyword": "ｽﾄﾚｯﾁ"})
        rid = rows["rows"][0]["__行"]
        st, j = self.post("/api/master/save",
                          {"table": "資材重量", "rowId": rid,
                           "values": {"単位質量": "0.55"}, "password": "nisk"})
        self.assertEqual(st, 200)
        self.assertTrue(j["ok"], j)
        _, m = self.post("/api/materials", {})
        got = [r["mass"] for r in m["rows"] if r["name"] == "ｽﾄﾚｯﾁ"]
        self.assertEqual(got, ["0.55"], "書いた内容が計算側へ届いていない")


if __name__ == "__main__":
    unittest.main()


class LabelSheetMasterTest(unittest.TestCase):
    """ラベル台紙一覧（シート名・型番）が台紙へ紐付くこと。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "梱包資材マスタ.sqlite3")
        make_master(self.db)
        c = sqlite3.connect(self.db)
        c.execute('CREATE TABLE "ラベル台紙一覧" '
                  '(管理番号 INTEGER, シート名 TEXT, 型番 TEXT)')
        c.executemany('INSERT INTO "ラベル台紙一覧" VALUES (?,?,?)', [
            (5, "1.0mm×53.5mm 　丈1", "BJB7606500QR"),
            (6, "1.0mm×53.5mm 　丈2", "BJB7606500QR"),
            (13, "0.8mm×53.5mm 　丈1", "BJB7610400QR"),
        ])
        c.commit()
        c.close()
        self.repo = MaterialRepository(accdb_dir="", csv_path="",
                                       prefer_access=False, db_path=self.db,
                                       refresh_sec=0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _master(self):
        from modules.packing_pena_label.app.services.size_master import SizeMaster
        return SizeMaster(os.path.join(ROOT, "data", "size_master.json"))

    def test_overlay_replaces_sheet_name_and_kataban(self):
        from modules.packing_pena_label.app.services.label_sheet_master import LabelSheetMaster
        m = self._master()
        c = sqlite3.connect(self.db)
        c.execute('UPDATE "ラベル台紙一覧" SET 型番=?, シート名=? WHERE 管理番号=5',
                  ("NEW-KATABAN", "べつの名前 丈1"))
        c.commit()
        c.close()
        n = LabelSheetMaster().apply_to(m, self.db)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(m.get(5).kataban, "NEW-KATABAN")
        self.assertEqual(m.get(5).size_cell_label, "べつの名前 丈1")
        # 表に無い台紙は JSON のまま
        self.assertEqual(m.get(1).kataban, "BJB7604000QR")

    def test_blank_does_not_wipe_the_json_value(self):
        """空欄で型番が消えないこと。"""
        from modules.packing_pena_label.app.services.label_sheet_master import LabelSheetMaster
        c = sqlite3.connect(self.db)
        c.execute('UPDATE "ラベル台紙一覧" SET 型番="" WHERE 管理番号=5')
        c.commit()
        c.close()
        m = self._master()
        LabelSheetMaster().apply_to(m, self.db)
        self.assertEqual(m.get(5).kataban, "BJB7606500QR")

    def test_missing_table_is_not_an_error(self):
        """表が無い端末でも動くこと（JSON の値を使う）。"""
        from modules.packing_pena_label.app.services.label_sheet_master import LabelSheetMaster
        other = os.path.join(self.tmp, "表なし.sqlite3")
        make_master(other)
        m = self._master()
        lsm = LabelSheetMaster()
        self.assertEqual(lsm.apply_to(m, other), 0)
        self.assertEqual(m.get(5).kataban, "BJB7606500QR")

    def test_reread_after_the_file_changes(self):
        from modules.packing_pena_label.app.services.label_sheet_master import LabelSheetMaster
        lsm = LabelSheetMaster(refresh_sec=0)
        m = self._master()
        lsm.apply_to(m, self.db)
        self.assertEqual(m.get(6).kataban, "BJB7606500QR")
        time.sleep(1.1)                     # mtime を動かす
        c = sqlite3.connect(self.db)
        c.execute('UPDATE "ラベル台紙一覧" SET 型番="AFTER" WHERE 管理番号=6')
        c.commit()
        c.close()
        lsm.apply_to(m, self.db)
        self.assertEqual(m.get(6).kataban, "AFTER")

    # ---------------------------------------------------- 直せる範囲
    def test_only_the_two_columns_are_editable(self):
        p = MA.page(self.repo, "ラベル台紙一覧")
        self.assertEqual(p["editableColumns"], ["シート名", "型番"])
        self.assertTrue(p["fixedRows"])

    def test_the_key_column_is_refused(self):
        p = MA.page(self.repo, "ラベル台紙一覧")
        rid = int(p["rows"][0]["__行"])
        with self.assertRaises(MA.Refused) as cm:
            MA.save_row(self.repo, "ラベル台紙一覧", rid, {"管理番号": "99"})
        self.assertEqual(cm.exception.kind, MA.REFUSE_NOT_ALLOWED)

    def test_rows_cannot_be_added_or_removed(self):
        p = MA.page(self.repo, "ラベル台紙一覧")
        rid = int(p["rows"][0]["__行"])
        with self.assertRaises(MA.Refused) as cm:
            MA.save_row(self.repo, "ラベル台紙一覧", None, {"シート名": "x"})
        self.assertEqual(cm.exception.kind, MA.REFUSE_NOT_CREATABLE)
        with self.assertRaises(MA.Refused) as cm:
            MA.delete_row(self.repo, "ラベル台紙一覧", rid)
        self.assertEqual(cm.exception.kind, MA.REFUSE_NOT_CREATABLE)

    def test_editing_the_two_columns_works(self):
        p = MA.page(self.repo, "ラベル台紙一覧")
        rid = int(p["rows"][0]["__行"])
        MA.save_row(self.repo, "ラベル台紙一覧", rid,
                    {"シート名": "直した名前", "型番": "直した型番"})
        again = MA.page(self.repo, "ラベル台紙一覧")
        row = [r for r in again["rows"] if int(r["__行"]) == rid][0]
        self.assertEqual(row["シート名"], "直した名前")
        self.assertEqual(row["型番"], "直した型番")


class MasterReachesThePrintTest(unittest.TestCase):
    """**マスタを直すと印刷に届くこと。**

    画面から直した場合だけでなく、**別の PC が直した場合**も確かめる。
    直した内容が刷られないと、コイルに違う型番のラベルが貼られる。
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "梱包資材マスタ.sqlite3")
        make_master(cls.db)
        c = sqlite3.connect(cls.db)
        c.execute('CREATE TABLE "ラベル台紙一覧" '
                  '(管理番号 INTEGER, シート名 TEXT, 型番 TEXT)')
        c.executemany('INSERT INTO "ラベル台紙一覧" VALUES (?,?,?)',
                      [(ob, "名前%02d 丈%d" % (ob, 1 if ob % 2 else 2),
                        "BJB%07dQ" % ob) for ob in range(1, 15)])
        c.commit()
        c.close()
        cfg = Config()
        cfg.port = free_port()
        cfg.prefer_access = False
        cfg.material_db_file = cls.db
        cfg.__class__.local_dir = property(lambda self, d=cls.tmp: d)
        cls.httpd, cls.ctx = server.create_server(cfg)
        cls.thread = threading.Thread(
            target=cls.httpd.serve_forever, kwargs={"poll_interval": 0.1},
            daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cfg.port
        cls.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.ctx.store.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with self.opener.open(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def get(self, path):
        with self.opener.open(self.base + path, timeout=20) as r:
            return r.read().decode("utf-8")

    def _outside_edit(self, sql, args):
        """**このアプリを通さずに** マスタを書き換える（別 PC 相当）。"""
        c = sqlite3.connect(self.db, timeout=20)
        try:
            c.execute(sql, args)
            c.commit()
        finally:
            c.close()

    def _fill(self, ob=5):
        st = self.post("/api/state", {})["state"]
        st = self.post("/api/select-size", {"obIdx": ob, "current": st})["state"]
        st["kensaNo"] = "W123456"
        st["weight1"] = "20.0"
        st["weight2"] = "20.0"
        st["coilH"] = {"1": "11"}
        st = self.post("/api/apply-weight", {"current": st})["state"]
        return st

    def _printed(self, ob=5):
        import re
        html = self.get("/labels/print?ob=%d" % ob)
        return {
            # バーコードの中身（見た目ではなく実体）
            "barcodes": set(re.findall(r'class="bc39"[^>]*aria-label="([^"]+)"',
                                       html)),
            "text": html,
        }

    # --------------------------------------------------------
    def test_outside_edit_reaches_the_barcode_and_the_text(self):
        self._fill(5)
        # 他のテストの影響を受けないよう、始まりの値を自分で決める
        self._outside_edit(
            'UPDATE "ラベル台紙一覧" SET 型番=?, シート名=? WHERE 管理番号=?',
            ("BJB0000005Q", "はじめの名前 丈1", 5))
        before = self._printed(5)
        self.assertIn("BJB0000005Q", before["barcodes"])

        self._outside_edit(
            'UPDATE "ラベル台紙一覧" SET 型番=?, シート名=? WHERE 管理番号=?',
            ("QQQ5555555QR", "外で直した名前 丈1", 5))

        after = self._printed(5)
        self.assertIn("QQQ5555555QR", after["barcodes"],
                      "バーコードが古い型番のまま")
        self.assertNotIn("BJB0000005Q", after["barcodes"],
                         "古い型番のバーコードが残っている")
        self.assertIn("外で直した名前 丈1", after["text"],
                      "シート名が古いまま")

    def test_every_sheet_is_reached(self):
        """14 枚すべてに効くこと（1 枚だけ取り残さない）。"""
        for ob in range(1, 15):
            self._fill(ob)
        for ob in range(1, 15):
            self._outside_edit(
                'UPDATE "ラベル台紙一覧" SET 型番=? WHERE 管理番号=?',
                ("ZZZ%07dQ" % ob, ob))
        missed = []
        for ob in range(1, 15):
            if "ZZZ%07dQ" % ob not in self._printed(ob)["barcodes"]:
                missed.append(ob)
        self.assertEqual(missed, [], "反映されなかった台紙")

    def test_repeated_edits_are_not_swallowed(self):
        """続けて直しても取りこぼさないこと（間隔を置いてキャッシュしない）。"""
        self._fill(6)
        for i in range(4):
            want = "QQQ666%04dQ" % i
            self._outside_edit(
                'UPDATE "ラベル台紙一覧" SET 型番=? WHERE 管理番号=?', (want, 6))
            self.assertIn(want, self._printed(6)["barcodes"],
                          "%d 回目が反映されていない" % (i + 1))

    def test_same_length_value_is_still_noticed(self):
        """同じ長さの値でも気付くこと（ファイルサイズが変わらない）。"""
        self._fill(7)
        self._outside_edit('UPDATE "ラベル台紙一覧" SET 型番=? WHERE 管理番号=?',
                           ("AAA0000007Q", 7))
        self.assertIn("AAA0000007Q", self._printed(7)["barcodes"])
        self._outside_edit('UPDATE "ラベル台紙一覧" SET 型番=? WHERE 管理番号=?',
                           ("BBB0000007Q", 7))
        self.assertIn("BBB0000007Q", self._printed(7)["barcodes"],
                      "長さが同じだと気付けていない")

    def test_material_master_reaches_the_calculation_and_the_report(self):
        """資材重量を外から直したら、**計算を押した時点で**効くこと。

        鮮度チェックの間隔（既定 300 秒）に隠れて、古い単重のまま
        刷られることがあってはならない。
        """
        st = self._fill(5)
        st = self.post("/api/calc-tare", {"current": st})["state"]
        before = st["gw"].get("1")

        self._outside_edit("UPDATE 資材重量 SET 単位質量=? WHERE 梱包資材名=?",
                           ("99.0", "テスラピン"))
        st = self.post("/api/calc-tare", {"current": st})["state"]
        after = st["gw"].get("1")
        self.assertNotEqual(after, before, "外から直した単重が効いていない")
        self.assertIn(after, self.get("/labels/print?ob=5"),
                      "計算結果がラベルに出ていない")
        self.assertIn(after, self.get("/tare/print"),
                      "計算結果が風袋帳票に出ていない")
