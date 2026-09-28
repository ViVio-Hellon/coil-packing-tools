"""CSV の出力先を設定で変える(現場の指摘。VER 0.13.6)。

マスタ管理(梱包明細履歴など)と明細の履歴(全ライン・3年)の「CSV」は、これまで
アプリのフォルダの `export\\packing_details\\` に決め打ちで書いていた。設定の
「置き場所・取り込み」で変えられるようにした。空なら既定のまま。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from modules.packing_details.meisai import config, distribution, master_browse, user_settings

from . import _db
from .test_master_browse import _add_table
from .test_web import HEADERS, _client


class ExportDirTest(unittest.TestCase):
    def setUp(self):
        _db.settings_file(self)
        self.client, _ = _client(self)
        folder = tempfile.TemporaryDirectory(prefix="details-export-")
        self.addCleanup(folder.cleanup)
        self.target = Path(folder.name) / "CSV置き場"

    def _save(self, value):
        return self.client.post("/api/settings", json={"key": "export_dir", "value": value},
                                headers=HEADERS)

    def test_既定はアプリのフォルダのexport(self):
        self.assertEqual(config.export_dir(), config.EXPORT_DIR)
        body = self.client.get("/api/settings", headers=HEADERS).get_json()
        self.assertEqual(body["export_dir"], str(config.EXPORT_DIR))
        self.assertEqual(body["export_dir_setting"], "")

    def test_設定した場所へ書き出す(self):
        res = self._save(str(self.target))
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertIn(str(self.target), res.get_json()["note"])
        self.assertEqual(config.export_dir(), self.target)
        path = _db.master_file(self)
        _add_table(path, "CREATE TABLE 品 (コード TEXT)", [("K001",)], "INSERT INTO 品 VALUES (?)")
        result = master_browse.export_csv("master", "品")
        self.assertEqual(Path(result.folder), self.target)
        self.assertTrue((self.target / result.file).is_file())
        body = self.client.get("/api/settings", headers=HEADERS).get_json()
        self.assertEqual(body["export_dir"], str(self.target))

    def test_空にすると既定に戻る(self):
        self._save(str(self.target))
        res = self._save("")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(config.export_dir(), config.EXPORT_DIR)

    def test_書けない場所は保存しない(self):
        blocker = self.target.parent / "ファイル"
        blocker.parent.mkdir(parents=True, exist_ok=True)
        blocker.write_text("x", encoding="utf-8")
        res = self._save(str(blocker / "下"))            # ファイルの下にはフォルダを作れない
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"]["code"], "not_writable")
        self.assertEqual(user_settings.get(config.KEY_EXPORT_DIR, ""), "")

    def test_相対で書くとアプリのフォルダから(self):
        self.assertEqual(config.resolve_dir("CSV"), config.APP_DIR / "CSV")

    def test_配布設定にも入る(self):
        self.assertIn(config.KEY_EXPORT_DIR, distribution.ITEM_KEYS)

    def test_画面に欄がある(self):
        html = self.client.get(f"/meisai?t={HEADERS['X-Tool-Token']}").data.decode("utf-8")
        for key in ('id="setExportDir"', 'id="btnExportSave"', "CSVの出力先"):
            self.assertIn(key, html)


if __name__ == "__main__":
    unittest.main()
