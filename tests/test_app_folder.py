"""「アプリのフォルダ」は統合アプリの根(Start.vbs があるフォルダ)。

移植元では、設定で置き場所を相対で書くと**アプリのフォルダ**からたどり、共有に届かない
端末は**アプリのフォルダ**に台帳の写しを置けば拾った。統合版では各機能のコードが
`modules\\<機能>` の下に移ったので、同じ書き方のままだと基準が機能のフォルダにずれ、
相対で書いた設定が黙って別の場所を指していた(統合版で直した)。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common import app_config

from .test_app import TOKEN, _make_app


class DetailsAppFolderTest(unittest.TestCase):
    def setUp(self):
        from modules.packing_details.meisai import config
        self.config = config

    def test_app_dir_is_the_integrated_root(self):
        self.assertEqual(self.config.APP_DIR, Path(app_config.APP_ROOT))
        self.assertNotEqual(self.config.APP_DIR, self.config.BASE_DIR)
        self.assertTrue((self.config.APP_DIR / "Start.vbs").exists())

    def test_relative_setting_is_read_from_the_app_folder(self):
        self.assertEqual(self.config.resolve_dir("台帳の写し"),
                         Path(app_config.APP_ROOT) / "台帳の写し")
        self.assertEqual(self.config.resolve_dir(r"..\共有".replace("\\", "/")),
                         Path(app_config.APP_ROOT) / ".." / "共有")

    def test_copies_in_the_app_folder_are_found(self):
        """共有に届かない端末は、アプリのフォルダに写しを置けば取り込める。"""
        from modules.packing_details.meisai import data_sync
        with tempfile.TemporaryDirectory() as d:
            app_dir = Path(d)
            for name in ("SIKALOT.sqlite3", "LS4LOT.sqlite3"):
                (app_dir / name).write_bytes(b"")
            missing = app_dir / "届かない共有"
            with mock.patch.object(self.config, "APP_DIR", app_dir):
                found = data_sync.find_sources(lot_dir=missing, konpo_dir=missing)
        self.assertEqual(found["仕掛ロット"].name, "SIKALOT.sqlite3")
        self.assertEqual(found["仕掛当工程"].name, "LS4LOT.sqlite3")

    def test_settings_screen_shows_the_resolved_place(self):
        client = _make_app(self).test_client()
        h = {"X-Tool-Token": TOKEN, "X-Tool-Screen": "S1"}
        client.post("/details/api/screen/claim", json={"screen": "S1"}, headers=h)
        client.post("/details/api/settings", json={"key": "lot_db_dir", "value": "台帳の写し"},
                    headers=h)
        body = client.get("/details/api/settings", headers=h).get_json()
        self.assertEqual(body["lot_db_dir"], str(Path(app_config.APP_ROOT) / "台帳の写し"))
        self.assertEqual(body["lot_db_dir_setting"], "台帳の写し")


class MaterialAppFolderTest(unittest.TestCase):
    def test_relative_setting_is_read_from_the_app_folder(self):
        from modules.packing_material_calculation.coil_tool import config
        self.assertEqual(config.APP_DIR, Path(app_config.APP_ROOT))
        self.assertEqual(config.resolve_dir("data/src"), Path(app_config.APP_ROOT) / "data" / "src")

    def test_legacy_places_are_under_the_app_folder(self):
        """旧い置き場所(本体の隣の data)も、アプリのフォルダの隣として見る。"""
        from modules.packing_material_calculation.coil_tool import config
        root = Path(app_config.APP_ROOT)
        self.assertEqual(config.LEGACY_DB_PATH, root / "data" / "coil_tool.db")
        self.assertEqual(config.LEGACY_USER_CONFIG_PATH, root / "data" / "user_config.json")


if __name__ == "__main__":
    unittest.main()
