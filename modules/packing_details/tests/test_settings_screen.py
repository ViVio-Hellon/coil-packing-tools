"""設定画面・取り込みの進み具合(VER 0.13.0)

**守ること**(現場の指摘から)
- 欄ごとに「保存して取り込み」: 仕掛台帳のフォルダは仕掛台帳の3つだけ、
  梱包課共有の仕掛フォルダは LS4LOT だけを取り込む
- 取り込みの進み具合を覗ける(何を読んでいるか・n/m・経過秒)
- 設定画面を開くとき、**共有・取り込み元を見に行かない**(すぐ返す)。
  届くか・共有の様子は別の口で、**待つのは決めた秒数まで**
- 画面を出すとき(上の帯の鮮度)も取り込み元を見に行かない
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from modules.packing_details.meisai import config, data_sync, user_settings

from . import _db
from .test_web import HEADERS, _client, _post


def _sources(case) -> tuple[Path, Path]:
    """仕掛台帳のフォルダ(3つ)と梱包課共有の仕掛フォルダ(LS4LOT)を作って、設定に入れる。"""
    root = Path(tempfile.mkdtemp())
    lot, konpo = root / "lot", root / "konpo"
    lot.mkdir()
    konpo.mkdir()
    _db.make_source(lot / "SIKALOT.sqlite3", _db.LOT_COLUMNS, [_db.lot_row("L5160Z0")],
                    created_at="2026-09-14T09:00:26")
    _db.make_source(lot / "SIKAHIKI.sqlite3", _db.HIKI_COLUMNS,
                    [{"ﾛｯﾄ番号": "L5160Z0", "引当番号": "1", "受注番号": "O1"}])
    _db.make_source(lot / "SIKAODR.sqlite3", _db.ODR_COLUMNS,
                    [{"受注番号": "O1", "包装仕様NO": "1C0123"}])
    _db.make_source(konpo / "LS4LOT.sqlite3", _db.TOKOTEI_COLUMNS,
                    [_db.tokotei_row("L5160Z0")], declare_text=False)
    user_settings.save(config.KEY_LOT_DB_DIR, str(lot))
    user_settings.save(config.KEY_KONPO_DB_DIR, str(konpo))
    return lot, konpo


def _count(conn, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


class PartialImportTest(unittest.TestCase):
    """欄ごとの取り込み。"""

    def setUp(self):
        _db.settings_file(self)
        self.lot, self.konpo = _sources(self)
        self.client, self.conn = _client(self)

    def test_仕掛台帳だけ取り込む(self):
        result = data_sync.import_all(self.conn, force=True, only=config.LOT_DB_FILES)
        self.assertEqual(set(result.imported), set(config.LOT_DB_FILES))
        self.assertEqual(_count(self.conn, "仕掛当工程"), 0)        # LS4LOT は読まない
        self.assertEqual(result.errors, [])

    def test_梱包課共有だけ取り込む_無ければその欄のことだけ言う(self):
        (self.konpo / "LS4LOT.sqlite3").unlink()
        result = data_sync.import_all(self.conn, force=True, only=config.KONPO_DB_FILES)
        self.assertEqual(result.imported, {})
        self.assertEqual(len(result.errors), 1)
        self.assertIn("LS4LOT", result.errors[0])
        self.assertNotIn("SIKALOT", result.errors[0])                # ほかの欄の話はしない
        self.assertEqual(_count(self.conn, "仕掛ロット"), 0)

    def test_画面から_欄ごとに取り込む(self):
        res = _post(self.client, "/api/import", {"force": True, "only": "konpo"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.get_json()["imported"]), {"仕掛当工程"})
        self.assertEqual(_count(self.conn, "仕掛ロット"), 0)
        res = _post(self.client, "/api/import", {"force": True, "only": "lot"})
        self.assertEqual(set(res.get_json()["imported"]), set(config.LOT_DB_FILES))
        res = _post(self.client, "/api/import", {"force": True, "only": "どれか"})
        self.assertEqual(res.status_code, 400)

    def test_進み具合を覗ける(self):
        seen = []
        original = data_sync.import_table

        def slow(conn, table, path, **kwargs):
            time.sleep(0.15)
            return original(conn, table, path, **kwargs)

        data_sync.import_table = slow
        self.addCleanup(setattr, data_sync, "import_table", original)

        def watch():
            for _ in range(40):
                body = self.client.get("/api/import/progress", headers=HEADERS).get_json()
                if body["running"]:
                    seen.append(body["message"])
                time.sleep(0.03)

        thread = threading.Thread(target=watch)
        thread.start()
        _post(self.client, "/api/import", {"force": True})
        thread.join()
        self.assertTrue(any("（1/4）" in m for m in seen), seen)
        self.assertTrue(any("（4/4）" in m for m in seen), seen)
        body = self.client.get("/api/import/progress", headers=HEADERS).get_json()
        self.assertFalse(body["running"])
        self.assertGreaterEqual(body["elapsed"], 0.4)                # 終わったあとも経過は残る


class FastSettingsTest(unittest.TestCase):
    """設定画面を開くときに待たせない。"""

    def setUp(self):
        _db.settings_file(self)
        self.client, self.conn = _client(self)
        self.calls = []
        original = data_sync.find_sources

        def slow(*args, **kwargs):
            self.calls.append(1)
            time.sleep(1.5)
            return original(*args, **kwargs)

        data_sync.find_sources = slow
        self.addCleanup(setattr, data_sync, "find_sources", original)

    def test_設定はすぐ返る_取り込み元を見に行かない(self):
        started = time.monotonic()
        body = self.client.get("/api/settings", headers=HEADERS).get_json()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.calls, [])
        self.assertTrue(all(s["found"] is None for s in body["stamps"]))
        for key in ("lot_tables", "konpo_tables", "share_dir", "distribution",
                    "master_db_name", "history_db_name"):
            self.assertIn(key, body)
        self.assertNotIn("qa_mark", body)                            # 共有の様子は別の口

    def test_共有の様子は待つ秒数を区切る(self):
        from modules.packing_details.app.routes import settings as routes
        original = routes.REACH_WAIT_SEC
        routes.REACH_WAIT_SEC = 0.2
        self.addCleanup(setattr, routes, "REACH_WAIT_SEC", original)
        started = time.monotonic()
        body = self.client.get("/api/settings/share", headers=HEADERS).get_json()
        self.assertLess(time.monotonic() - started, 1.2)
        self.assertIn("応えません", body["reach_problem"])
        self.assertIn("qa_mark", body)
        self.assertTrue(all(s["found"] is None for s in body["stamps"]))

    def test_届けば届くと返す(self):
        lot, _konpo = _sources(self)
        body = self.client.get("/api/settings/share", headers=HEADERS).get_json()
        self.assertEqual(body["reach_problem"], "")
        found = {s["table"]: s["found"] for s in body["stamps"]}
        self.assertEqual(found, {t: True for t in config.ALL_SOURCE_FILES})

    def test_画面を出すときも取り込み元を見に行かない(self):
        res = self.client.get("/meisai")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.calls, [])


class ScreenMarkupTest(unittest.TestCase):
    """設定画面の形。**ボタンは欄ごと**(1つのボタンでまとめない)。"""

    def setUp(self):
        self.client, self.conn = _client(self)
        self.html = self.client.get("/meisai").get_data(as_text=True)

    def test_欄ごとに保存のボタンがある(self):
        for button in ('id="btnLotSave"', 'id="btnKonpoSave"', 'id="btnShareSave"',
                       'id="btnQaSave"', 'id="btnAdminSave"', 'id="btnDistExport"'):
            self.assertIn(button, self.html)
        self.assertNotIn("btnSettingsSave", self.html)              # まとめて保存は無い
        self.assertEqual(self.html.count(">保存して取り込み<"), 2)

    def test_タブが4つ(self):
        for tab in ("置き場所・取り込み", "紙面の右上の文字", "管理者パスワード", "配布設定"):
            self.assertIn(f">{tab}</button>", self.html)
        self.assertIn('id="busyDialog"', self.html)


if __name__ == "__main__":
    unittest.main()
