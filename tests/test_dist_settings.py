"""配布設定の置き場所(統合版: `common/dist_settings.py`)

値は機能ごとのまま、置き場所の決まりだけを1つにした。3機能の書き出し先と、
配布用フォルダを作る処理が読む場所が食い違わないことを確かめる。
"""
from __future__ import annotations

import importlib
import unittest
from pathlib import Path

from common import app_config, dist_settings


class DistSettingsTest(unittest.TestCase):
    def test_3機能とも統合アプリの直下の配布設定の下(self):
        keys = [key for key, _, _ in dist_settings.MODULES]
        self.assertEqual(keys, ["packing_details", "packing_pena_label",
                                "packing_material_calculation"])
        for key in keys:
            self.assertEqual(dist_settings.default_dir(key),
                             app_config.APP_ROOT / "配布設定" / key)

    def test_配布設定を持たない機能は断る(self):
        with self.assertRaises(ValueError):
            dist_settings.default_dir("そんな機能")

    def test_各機能の書き出し先は同じ決まりを使う(self):
        """環境変数で変えていなければ、各機能の `distribution.DIR` は既定の場所。"""
        import os
        env = {"packing_details": "PACKING_DETAILS_DISTRIBUTION_DIR",
               "packing_pena_label": "PACKING_PENA_DISTRIBUTION_DIR",
               "packing_material_calculation": "COIL_TOOL_DISTRIBUTION_DIR"}
        for key, _, modname in dist_settings.MODULES:
            module = importlib.import_module(modname)
            self.assertEqual(module.SETTINGS_NAME, "設定.json", key)
            if env[key] in os.environ:
                continue                        # 試験の隔離で変えてある
            if key == "packing_details":
                continue                        # 梱包明細の試験は DIR を差し替える
            self.assertEqual(Path(module.DIR), dist_settings.default_dir(key), key)

    def test_案内の文はアプリのフォルダからの道筋(self):
        where = dist_settings.where(dist_settings.default_dir("packing_pena_label"))
        self.assertEqual(where, "配布設定\\packing_pena_label")

    def test_アプリの外ならそのまま(self):
        outside = Path("/somewhere/else/配布設定")
        self.assertEqual(dist_settings.where(outside), str(outside))


if __name__ == "__main__":
    unittest.main()
