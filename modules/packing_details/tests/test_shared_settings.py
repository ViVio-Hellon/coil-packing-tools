"""全ラインで共有する設定 (紙面の右上の文字・管理者パスワード)

**守ること**
- 右上の文字の**正は梱包資材マスタの表**「梱包明細打ち出し」(ID=1)。
  表か行が無ければ控えのJSON、それも無ければ既定
- マスタへは1行を書き換えるだけ。**表を作らない・形を変えない・ほかの表に触らない**
- どのラインPCで変えても、**ほかのラインの次の紙面**に出る
- 共有に届かなくても紙は止めない。この端末が最後に読んだ値で刷り、
  紙面の画面にそう出す。写しも無ければ既定
- 共有に届かないときは**変えさせない**(手元だけ変えると、ラインごとに
  違う紙が出る)
- 共有が応えないとき、紙面を開くのを待たせない
- 2台が同時に書いても、片方の変更を消さない。書きかけを読ませない
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import unittest

from modules.packing_details.meisai import (admin_password, config, qa_mark, report, shared_settings,
                    user_settings)
from modules.packing_details.meisai.meisai_service import Output

from . import _db
from .test_web import HEADERS, TOKEN, _client, _post, _seed

PASSWORD = config.ADMIN_PASSWORD


def _output() -> Output:
    return Output(lot_no="L5160Z0", seq_no=1, keys=["1-10", "2-8"],
                  weights=[250, 248])


def _cut_off(case: unittest.TestCase) -> None:
    """共有に届かなくする(ネットワークが切れた・サーバが落ちた)。"""
    original = config.SHARED_DIR
    config.SHARED_DIR = original.parent / "届かない" / "サーバ" / original.name
    case.addCleanup(setattr, config, "SHARED_DIR", original)


def _json() -> dict:
    return json.loads(shared_settings.shared_path().read_text(encoding="utf-8"))


def _write_json(data: dict) -> None:
    path = shared_settings.shared_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class MasterTest(unittest.TestCase):
    """**正は梱包資材マスタ、無ければJSON。**"""

    def setUp(self):
        _db.settings_file(self)

    # --- 読む ---------------------------------------------------------
    def test_マスタの表が正(self):
        _db.master_file(self, [(1, "NLM.MASTER.QA")])
        _write_json({"qa_mark": "NLM.JSON.QA"})
        value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.MASTER.QA")
        self.assertEqual(snap.qa_origin, shared_settings.ORIGIN_MASTER)
        self.assertEqual(snap.master.state, "ok")
        self.assertEqual(qa_mark.notice(snap), "")

    def test_アップロードされた形そのままで読める(self):
        """現場の表は ID=1 / 設定文字列='NLM.NAGOYA.QA' の1行(列に型が無い)。"""
        _db.master_file(self)
        value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.NAGOYA.QA")
        self.assertEqual(snap.qa_origin, shared_settings.ORIGIN_MASTER)

    def test_表が無ければJSONを見る(self):
        _db.master_file(self, table=False)
        _write_json({"qa_mark": "NLM.JSON.QA"})
        value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.JSON.QA")
        self.assertEqual(snap.qa_origin, shared_settings.ORIGIN_JSON)
        self.assertEqual(snap.master.state, "no_table")
        self.assertEqual(qa_mark.notice(snap), "")        # 決めた逃げ道。紙面で騒がない

    def test_マスタが無くてもJSONを見る(self):
        _write_json({"qa_mark": "NLM.JSON.QA"})
        value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.JSON.QA")
        self.assertEqual(snap.master.state, "no_file")

    def test_ID1の行が無い_空ならJSONを見る(self):
        _write_json({"qa_mark": "NLM.JSON.QA"})
        for rows in ([(2, "NLM.OTHER.QA")], [(1, "")], [(1, None)], []):
            with self.subTest(rows=rows):
                path = shared_settings.shared_dir() / "梱包資材マスタ.sqlite3"
                path.unlink(missing_ok=True)
                _db.master_file(self, rows)
                value, snap = qa_mark.resolve()
                self.assertEqual(value, "NLM.JSON.QA")
                self.assertEqual(snap.master.state, "no_row")

    def test_どちらにも無ければ既定(self):
        _db.master_file(self, table=False)
        value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.NAGOYA.QA")
        self.assertEqual(snap.qa_origin, shared_settings.ORIGIN_DEFAULT)

    def test_IDが文字でも1として読む(self):
        """列に型が無いので、Access から来た ID は 1 のことも '1' のこともある。"""
        for raw in ("1", " 1 ", 1.0):
            with self.subTest(raw=raw):
                path = shared_settings.shared_dir() / "梱包資材マスタ.sqlite3"
                path.unlink(missing_ok=True)
                _db.master_file(self, [(raw, "NLM.TEXT.QA")])
                self.assertEqual(qa_mark.current(), "NLM.TEXT.QA")

    def test_拡張子がdbでも拾う(self):
        _db.master_file(self, [(1, "NLM.DB.QA")], name="梱包資材マスタ.db")
        self.assertEqual(qa_mark.current(), "NLM.DB.QA")

    def test_ID1が2行あれば上の行を使って_そう言う(self):
        _db.master_file(self, [(1, "NLM.FIRST.QA"), (1, "NLM.SECOND.QA")])
        with self.assertLogs("meisai.shared_settings", level="WARNING"):
            value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.FIRST.QA")
        self.assertIn("2行", snap.master.note)

    def test_マスタで刷れない値なら既定で刷って警告(self):
        _db.master_file(self, [(1, "あ" * (qa_mark.MAX_LEN + 1))])
        with self.assertLogs("meisai.qa_mark", level="WARNING"):
            self.assertEqual(qa_mark.current(), "NLM.NAGOYA.QA")

    # --- 総合ツールのマスタ管理で直されたとき -------------------------
    def test_マスタで直されたら次の紙面から効き_控えも合わせる(self):
        """総合ツールのマスタ管理からも直せる。こちらは次に読んだときから効く。"""
        path = _db.master_file(self)
        qa_mark.change("NLM.APP.QA", PASSWORD)
        conn = sqlite3.connect(str(path))
        conn.execute('UPDATE "梱包明細打ち出し" SET "設定文字列" = ? WHERE "ID" = 1',
                     ("NLM.TOOL.QA",))
        conn.commit()
        conn.close()
        self.assertEqual(qa_mark.current(), "NLM.TOOL.QA")
        shared_settings.wait_mirror()
        self.assertEqual(_json()["qa_mark"], "NLM.TOOL.QA")    # 控えも追いつく

    def test_表が消えたら控えで刷る(self):
        """総合ツールのマスタ管理では表を消せる(VER2.97.0)。消えても紙は止めない。"""
        path = _db.master_file(self)
        qa_mark.change("NLM.APP.QA", PASSWORD)
        conn = sqlite3.connect(str(path))
        conn.execute('DROP TABLE "梱包明細打ち出し"')
        conn.commit()
        conn.close()
        value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.APP.QA")
        self.assertEqual(snap.qa_origin, shared_settings.ORIGIN_JSON)

    # --- 書く ---------------------------------------------------------
    def test_変えるとマスタに書き_控えのJSONにも書く(self):
        path = _db.master_file(self)
        result = qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertTrue(result.ok, result.message)
        self.assertIn("梱包資材マスタ", result.message)
        self.assertEqual(_db.master_rows(path), [(1, "NLM.TEST.QA")])
        self.assertEqual(_json()["qa_mark"], "NLM.TEST.QA")

    def test_マスタの形とほかの表には触らない(self):
        path = _db.master_file(self)
        conn = sqlite3.connect(str(path))
        before = conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        pallets = conn.execute("SELECT * FROM PalletMaster ORDER BY ID").fetchall()
        registry = conn.execute("SELECT * FROM ツールで足した表").fetchall()
        conn.close()
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        qa_mark.reset(PASSWORD)
        conn = sqlite3.connect(str(path))
        self.assertEqual(conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall(), before)
        self.assertEqual(conn.execute("SELECT * FROM PalletMaster ORDER BY ID").fetchall(), pallets)
        self.assertEqual(conn.execute("SELECT * FROM ツールで足した表").fetchall(), registry)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        conn.close()
        names = {p.name for p in path.parent.iterdir()}
        self.assertEqual(names, {path.name, shared_settings.FILE_NAME})  # 鍵も -journal も残さない

    def test_ID1の行が無ければ1行足す(self):
        path = _db.master_file(self, [(2, "ほかの設定")])
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertEqual(_db.master_rows(path), [(2, "ほかの設定"), (1, "NLM.TEST.QA")])

    def test_ID1が2行あれば両方書き換える(self):
        path = _db.master_file(self, [(1, "A"), (1, "B")])
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertEqual(_db.master_rows(path), [(1, "NLM.TEST.QA"), (1, "NLM.TEST.QA")])

    def test_表が無ければJSONにだけ書き_表は作らない(self):
        path = _db.master_file(self, table=False)
        result = qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertTrue(result.ok)
        self.assertIn("表が無い", result.message)
        self.assertEqual(_json()["qa_mark"], "NLM.TEST.QA")
        conn = sqlite3.connect(str(path))
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        self.assertNotIn("梱包明細打ち出し", tables)

    def test_既定に戻すとマスタに既定の文字を書く(self):
        """空にすると「表が無い」と見分けがつかず、控えの古い値が出る。"""
        path = _db.master_file(self)
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        qa_mark.reset(PASSWORD)
        self.assertEqual(_db.master_rows(path), [(1, "NLM.NAGOYA.QA")])
        self.assertEqual(_json()["qa_mark"], "NLM.NAGOYA.QA")
        self.assertEqual(qa_mark.current(), "NLM.NAGOYA.QA")

    def test_パスワードはマスタに入れない(self):
        path = _db.master_file(self)
        admin_password.change(PASSWORD, "abcd1", "abcd1")
        self.assertNotIn("pbkdf2", path.read_bytes().decode("utf-8", "replace"))
        self.assertTrue(_json()["admin_password"].startswith("pbkdf2$"))

    # --- マスタが読めない・掴まれている ------------------------------------
    def test_マスタが壊れていたら控えで刷り_断り書き_変えさせない(self):
        _write_json({"qa_mark": "NLM.JSON.QA"})
        folder = shared_settings.shared_dir()
        (folder / "梱包資材マスタ.sqlite3").write_bytes(b"Access\x00\x01 not sqlite" * 50)
        value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.JSON.QA")
        self.assertEqual(snap.master.state, "error")
        self.assertIn("梱包資材マスタを読めない", qa_mark.notice(snap))
        result = qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertEqual(result.reason, qa_mark.REFUSE_SHARED)
        self.assertEqual(_json()["qa_mark"], "NLM.JSON.QA")    # JSONにだけ書いたりしない

    def test_マスタが掴まれていたら書かずに断る(self):
        """総合ツールや Access が書いている最中。JSONにだけ書いて食い違わせない。"""
        from modules.packing_details.meisai import source_db
        path = _db.master_file(self)
        original = source_db.BUSY_TIMEOUT_MS
        source_db.BUSY_TIMEOUT_MS = 200
        self.addCleanup(setattr, source_db, "BUSY_TIMEOUT_MS", original)
        qa_mark.change("NLM.BEFORE.QA", PASSWORD)
        holder = sqlite3.connect(str(path), timeout=0)
        holder.execute("BEGIN EXCLUSIVE")
        self.addCleanup(holder.close)
        result = qa_mark.change("NLM.TEST.QA", PASSWORD)
        holder.rollback()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, qa_mark.REFUSE_SHARED)
        self.assertEqual(_db.master_rows(path), [(1, "NLM.BEFORE.QA")])
        self.assertEqual(_json()["qa_mark"], "NLM.BEFORE.QA")

    def test_設定画面に正と控えの様子が出る(self):
        _db.master_file(self)
        client, _conn = _client(self)
        body = client.get("/api/settings/share", headers=HEADERS).get_json()
        self.assertEqual(body["qa_origin"], "master")
        self.assertEqual(body["share_master_state"], "ok")
        self.assertTrue(body["share_master"].endswith("梱包資材マスタ.sqlite3"))
        self.assertEqual(body["share_master_table"], "梱包明細打ち出し")
        self.assertTrue(body["share_json"].endswith(shared_settings.FILE_NAME))


class TwoLinesTest(unittest.TestCase):
    """**別のラインPC**で変えたら、こちらの次の紙面に出るか。"""

    def setUp(self):
        _db.settings_file(self)

    def test_ほかのラインで変えたら次の紙面に出る(self):
        """**このために作った。**"""
        _db.other_pc(self, "line2")
        self.assertIn(">NLM.NAGOYA.QA<", report.render([_output()]))
        _db.this_pc(self)
        self.assertTrue(qa_mark.change("NLM.TEST.QA", PASSWORD).ok)
        _db.other_pc(self, "line2")
        html = report.render([_output()])
        self.assertIn('<span class="qa-text">NLM.TEST.QA</span>', html)
        self.assertNotIn("NLM.NAGOYA.QA", html)

    def test_既定に戻しても全ラインが戻る(self):
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        _db.other_pc(self, "line3")
        self.assertTrue(qa_mark.reset(PASSWORD).ok)
        _db.this_pc(self)
        self.assertEqual(qa_mark.current(), "NLM.NAGOYA.QA")

    def test_パスワードも全ラインで1つ(self):
        """あるPCのパスワードを知っている人が、全ラインの紙を変えられる、を作らない。"""
        admin_password.change(PASSWORD, "abcd1", "abcd1")
        _db.other_pc(self, "line2")
        self.assertFalse(admin_password.verify(PASSWORD))
        self.assertTrue(admin_password.verify("abcd1"))
        self.assertFalse(qa_mark.change("X", PASSWORD).ok)
        self.assertTrue(qa_mark.change("X", "abcd1").ok)

    def test_右上の文字を変えてもパスワードは消えない(self):
        admin_password.change(PASSWORD, "abcd1", "abcd1")
        qa_mark.change("NLM.TEST.QA", "abcd1")
        data = json.loads(shared_settings.shared_path().read_text(encoding="utf-8"))
        self.assertEqual(data["qa_mark"], "NLM.TEST.QA")
        self.assertTrue(data["admin_password"].startswith("pbkdf2$"))

    def test_誰がいつ変えたかを残す(self):
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        data = json.loads(shared_settings.shared_path().read_text(encoding="utf-8"))
        self.assertEqual(data["format"], 1)
        self.assertRegex(data["updated_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertTrue(data["updated_by"])

    def test_端末ごとの古い値は使わない(self):
        """VER 0.7.0 で端末ごとに持っていた値。残っていても全ラインの値で刷る。"""
        user_settings.save(config.KEY_QA_MARK, "この端末だけ")
        with self.assertLogs("meisai.shared_settings", level="WARNING") as logs:
            self.assertEqual(qa_mark.current(), "NLM.NAGOYA.QA")
        self.assertIn("0.7.0", "\n".join(logs.output))


class FolderTest(unittest.TestCase):

    def setUp(self):
        _db.settings_file(self)

    def test_まだ誰も変えていなければ既定_届かない扱いにしない(self):
        """入れたばかりの全ラインが「届かない」と言い出さない。"""
        snap = shared_settings.read()
        self.assertEqual(snap.source, "shared")
        self.assertEqual(snap.problem, "")
        self.assertEqual(qa_mark.notice(snap), "")

    def test_最初に変えたときにフォルダを1段だけ作る(self):
        folder = shared_settings.shared_dir()
        self.assertFalse(folder.exists())
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertTrue((folder / shared_settings.FILE_NAME).is_file())

    def test_上のフォルダも無ければ作らずに断る(self):
        """道の打ち間違いで、共有の上にフォルダの列を作らない。"""
        _cut_off(self)
        result = qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertEqual(result.reason, qa_mark.REFUSE_SHARED)
        self.assertFalse(shared_settings.shared_dir().parent.exists())

    def test_取り込み元のファイルには触らない(self):
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        names = {p.name for p in shared_settings.shared_dir().iterdir()}
        self.assertEqual(names, {shared_settings.FILE_NAME})   # 鍵も一時ファイルも残さない

    def test_端末ごとに置き場所を差し替えられる(self):
        other = shared_settings.shared_dir().parent / "別の共有"
        user_settings.save(config.KEY_SHARED_DIR, str(other))
        self.assertEqual(shared_settings.shared_dir(), other)
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertTrue((other / shared_settings.FILE_NAME).is_file())


class OfflineTest(unittest.TestCase):
    """共有に届かないとき。**紙は止めない、変えさせない、黙らない。**"""

    def setUp(self):
        _db.settings_file(self)

    def test_届かなければ最後に読んだ値で刷る(self):
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        shared_settings.read()                                  # 写しが残る
        # 共有のファイルが消えた(サーバが落ちた)のと同じ: フォルダごと外す
        folder = shared_settings.shared_dir()
        os.rename(folder.parent, folder.parent.with_name("外した"))
        self.addCleanup(os.rename, folder.parent.with_name("外した"), folder.parent)
        value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.TEST.QA")
        self.assertEqual(snap.source, "cache")
        self.assertIn("届かない", qa_mark.notice(snap))
        self.assertIn(snap.read_at, qa_mark.notice(snap))

    def test_写しも無ければ既定で刷って_そう言う(self):
        _cut_off(self)
        value, snap = qa_mark.resolve()
        self.assertEqual(value, "NLM.NAGOYA.QA")
        self.assertEqual(snap.source, "none")
        self.assertIn("既定", qa_mark.notice(snap))

    def test_置き場所を変えたら前の共有の写しは使わない(self):
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        _cut_off(self)
        self.assertEqual(qa_mark.current(), "NLM.NAGOYA.QA")

    def test_届かなければ変えさせない(self):
        """手元だけ変えると、ラインごとに違う紙が出る。"""
        _cut_off(self)
        for result in (qa_mark.change("NLM.TEST.QA", PASSWORD),
                       qa_mark.reset(PASSWORD),
                       admin_password.change(PASSWORD, "abcd1", "abcd1")):
            with self.subTest(message=result.message):
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, "shared_unreachable")
                self.assertIn("届かない", result.message)

    def test_紙面の画面に断り書きが出る_紙には出ない(self):
        _cut_off(self)
        html = report.render([_output()])
        self.assertIn('class="screen-only notice" id="reportNotice">', html)
        self.assertIn("共有の設定に届かない", html)

    def test_届いていれば断り書きは隠しておく(self):
        html = report.render([_output()])
        self.assertIn('id="reportNotice" hidden></p>', html)

    def test_届かなくなったときだけログに出す(self):
        """紙面を開くたびに同じ警告を積まない。"""
        _cut_off(self)
        with self.assertLogs("meisai.shared_settings", level="WARNING") as logs:
            for _ in range(5):
                shared_settings.read()
        self.assertEqual(len(logs.output), 1)

    def test_共有のファイルが壊れていたら写しで刷って_上書きしない(self):
        """壊れた共有を黙って既定で上書きすると、何が入っていたか分からなくなる。"""
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        path = shared_settings.shared_path()
        path.write_text("{こわれた", encoding="utf-8")
        self.assertEqual(qa_mark.current(), "NLM.TEST.QA")
        self.assertEqual(qa_mark.change("NLM.OTHER.QA", PASSWORD).reason,
                         qa_mark.REFUSE_SHARED)
        self.assertEqual(path.read_text(encoding="utf-8"), "{こわれた")


class SlowShareTest(unittest.TestCase):
    """共有が応えないとき、紙面を開くのを待たせない。"""

    def setUp(self):
        _db.settings_file(self)
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        shared_settings.read()
        self.release = threading.Event()
        original = shared_settings._read_all

        def hang(path):
            self.release.wait(10)                 # 応えない共有
            return original(path)
        shared_settings._read_all = hang
        self.addCleanup(setattr, shared_settings, "_read_all", original)
        self.addCleanup(self.release.set)

    def test_待ちきれなければ写しで刷る(self):
        started = time.monotonic()
        value, snap = qa_mark.resolve(shared_settings.read(timeout=0.3))
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(value, "NLM.TEST.QA")
        self.assertEqual(snap.source, "cache")
        self.assertIn("応えません", snap.problem)

    def test_止まった読みを積み重ねない(self):
        """2回目は待たずに写しを返す(前の読みがまだ止まっているので)。"""
        shared_settings.read(timeout=0.2)
        started = time.monotonic()
        snap = shared_settings.read(timeout=5)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(snap.source, "cache")
        self.assertEqual(sum(t.name == "shared-read" and t.is_alive()
                             for t in threading.enumerate()), 1)

    def test_応えるようになったら共有を読む(self):
        shared_settings.read(timeout=0.2)
        self.release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snap = shared_settings.read(timeout=1)
            if snap.source == "shared":
                break
            time.sleep(0.05)
        self.assertEqual(snap.source, "shared")


class ConcurrentReadTest(unittest.TestCase):
    """紙面を開く要求と設定の読み込みは並んで来る。**元気な共有を「届かない」と言わない。**"""

    def setUp(self):
        _db.settings_file(self)
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        original = shared_settings._read_all

        def slow(path):
            time.sleep(0.3)                    # 少し遅いが応える共有
            return original(path)
        shared_settings._read_all = slow
        self.addCleanup(setattr, shared_settings, "_read_all", original)

    def test_同時に読んでもどちらも共有から(self):
        """直す前は、あとから来た要求が「応答を待っています」で写しに逃げていた。"""
        results = []

        def read():
            results.append(shared_settings.read(timeout=2).source)
        threads = [threading.Thread(target=read) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(results, ["shared"] * 4)


class WriteTest(unittest.TestCase):
    """2台が同時に書いても、片方の変更を消さない。"""

    def setUp(self):
        _db.settings_file(self)
        shared_settings.shared_dir().mkdir()

    def test_同時に書いても両方残る(self):
        errors = []

        def write(i):
            try:
                key = "qa_mark" if i % 2 else "admin_password"
                shared_settings.update({key: f"{key}-{i}"})
            except Exception as exc:                        # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=write, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.assertEqual(errors, [])
        data = json.loads(shared_settings.shared_path().read_text(encoding="utf-8"))
        self.assertTrue(data["qa_mark"].startswith("qa_mark-"))
        self.assertTrue(data["admin_password"].startswith("admin_password-"))

    def test_書いている最中の端末があれば待ってから断る(self):
        lock = shared_settings.shared_dir() / shared_settings.LOCK_NAME
        lock.write_text("line2", encoding="utf-8")
        original = shared_settings.LOCK_WAIT_SEC
        shared_settings.LOCK_WAIT_SEC = 0.3
        self.addCleanup(setattr, shared_settings, "LOCK_WAIT_SEC", original)
        with self.assertRaises(shared_settings.SharedError) as caught:
            shared_settings.update({"qa_mark": "X"})
        self.assertIn("ほかの端末", str(caught.exception))
        self.assertTrue(lock.exists())                   # 他人の鍵は外さない

    def test_落ちた端末が残した古い鍵は外す(self):
        lock = shared_settings.shared_dir() / shared_settings.LOCK_NAME
        lock.write_text("line2", encoding="utf-8")
        old = time.time() - shared_settings.LOCK_STALE_SEC - 5
        os.utime(lock, (old, old))
        shared_settings.update({"qa_mark": "X"})
        self.assertFalse(lock.exists())

    def test_読む側は書きかけを見ない(self):
        """別名で書いてから置き換える。途中で読んでも古いか新しいかのどちらか。"""
        shared_settings.update({"qa_mark": "A" * 20})
        seen, stop = set(), threading.Event()

        def reader():
            while not stop.is_set():
                seen.add(shared_settings.read(timeout=1).source)
        t = threading.Thread(target=reader)
        t.start()
        for i in range(30):
            shared_settings.update({"qa_mark": ("B" if i % 2 else "A") * 20})
        stop.set()
        t.join(5)
        self.assertEqual(seen, {"shared"})

    def test_共有しない値は書かせない(self):
        with self.assertRaises(ValueError):
            shared_settings.update({"lot_db_dir": "x"})


class LocalSettingsTest(unittest.TestCase):
    """端末ごとの設定ファイル。**共有の写しを書き直すたびに、ほかの設定を消さない。**"""

    def setUp(self):
        _db.settings_file(self)

    def test_並んで書いてもどれも残る(self):
        """紙面を開く要求と設定の保存は並んで来る。書きかけを読んで空とみなし、
        それを保存すると置き場所などが消える(直す前はそうなりうる作りだった)。"""
        def write(i):
            for j in range(20):
                user_settings.save(f"key{i}", j)
        threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        data = user_settings.load_all()
        self.assertEqual({k: data.get(k) for k in (f"key{i}" for i in range(8))},
                         {f"key{i}": 19 for i in range(8)})
        self.assertFalse(config.USER_CONFIG_PATH.with_name(
            config.USER_CONFIG_PATH.name + ".tmp").exists())


class SharedWebTest(unittest.TestCase):
    """画面から見た共有。"""

    def setUp(self):
        _db.settings_file(self)
        self.client, self.conn = _client(self)

    def _settings(self):
        return self.client.get("/api/settings/share", headers=HEADERS).get_json()

    def test_設定に共有の様子が出る(self):
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        body = self._settings()
        self.assertEqual(body["qa_mark"], "NLM.TEST.QA")
        self.assertEqual(body["share_source"], "shared")
        self.assertEqual(body["share_path"], str(shared_settings.shared_dir()))
        self.assertEqual(body["share_json"], str(shared_settings.shared_path()))
        self.assertEqual(body["share_default"], str(config.SHARED_DIR))
        self.assertTrue(body["share_updated_by"])
        self.assertNotIn("pbkdf2", json.dumps(body))

    def test_届かなければ設定にそう出て_変えると503(self):
        _cut_off(self)
        body = self._settings()
        self.assertEqual(body["share_source"], "none")
        self.assertIn("届きません", body["share_problem"])
        res = _post(self.client, "/api/settings/qa-mark",
                    {"value": "NLM.TEST.QA", "password": PASSWORD})
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.get_json()["error"]["code"], "shared_unreachable")

    def test_開いたままの紙面が聞き直すと断り書きも返る(self):
        _cut_off(self)
        body = self.client.get(f"/report/qa-mark?t={TOKEN}").get_json()
        self.assertEqual(body["qa_mark"], "NLM.NAGOYA.QA")
        self.assertIn("届かない", body["notice"])

    def test_置き場所の差し替えはパスワードが要る(self):
        other = str(shared_settings.shared_dir().parent / "別の共有")
        res = _post(self.client, "/api/settings/shared-dir",
                    {"value": other, "password": "違う"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(user_settings.get(config.KEY_SHARED_DIR), None)
        res = _post(self.client, "/api/settings/shared-dir",
                    {"value": other, "password": PASSWORD})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertEqual(body["share_setting"], other)
        self.assertIn("ほかのライン", body["message"])

    def test_置き場所を空にすると既定に戻る(self):
        _post(self.client, "/api/settings/shared-dir",
              {"value": "/どこか", "password": PASSWORD})
        res = _post(self.client, "/api/settings/shared-dir",
                    {"value": "", "password": PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(shared_settings.shared_dir(), config.SHARED_DIR)

    def test_届かない置き場所でも差し替えは受ける_そう言う(self):
        """届かない既定を直すための口。届くことは求めない。"""
        res = _post(self.client, "/api/settings/shared-dir",
                    {"value": "/存在しない/共有/梱包明細", "password": PASSWORD})
        self.assertEqual(res.status_code, 200)
        self.assertIn("届きません", res.get_json()["message"])

    def test_ほかのラインで変えたあと_こちらの紙面の経路でも新しい文字(self):
        _seed(self.conn)
        for path, body in (("/api/lot", {"lot_no": "L5160Z0"}),
                           ("/api/weights", {"weights": ["250", "248"]}),
                           ("/api/strands", {}), ("/api/stack-max", {"value": "2"}),
                           ("/api/stack", {"key": "1-10"}), ("/api/stack", {"key": "2-8"}),
                           ("/api/output", {"confirm": True})):
            _post(self.client, path, body)
        _db.other_pc(self, "line2")
        qa_mark.change("NLM.LINE2.QA", PASSWORD)
        _db.this_pc(self)
        html = self.client.get(f"/report/L5160Z0/1?t={TOKEN}").get_data(as_text=True)
        self.assertIn('<span class="qa-text">NLM.LINE2.QA</span>', html)


if __name__ == "__main__":
    unittest.main()
