"""設定部に「このPCに保存」と「複数のPCで共有」を分けて明記する(統合 1.0.10。現場の指摘)。

    ローカルに保存してそのPCで引き継いで使用するものは、設定部にそういうファイルが
    あるということを明記しておいてください(複数PCで共有するものと、該当PCで
    引き継ぐものは違いますよね)

3機能の設定画面に、同じ言葉(`common/storage_places.py`)で

- このPCに保存(このPCで引き継ぐ): 設定ファイル・手元のDB・ログ
- 複数のPCで共有(全ライン): 共有フォルダのマスタ・履歴など
- 配布設定(アプリのフォルダ)

を出し、設定の欄ごとにも札(このPCに保存／全ラインで共有)を付ける。
"""
from __future__ import annotations

import os
import unittest

from .test_app import TOKEN, _make_app


def _group(storage: dict, kind: str) -> dict:
    return next(g for g in storage["groups"] if g["kind"] == kind)


def _paths(group: dict) -> list:
    return [row["path"] for row in group["rows"]]


class CommonWordsTest(unittest.TestCase):
    def test_three_groups_in_order_with_the_same_words(self):
        from common import storage_places as SP
        d = SP.Places(local=[SP.Place("a", "/x", "b")]).to_dict()
        self.assertEqual([g["kind"] for g in d["groups"]], ["local", "shared", "dist"])
        local = _group(d, "local")
        self.assertEqual(local["title"], "このPCに保存（このPCで引き継ぐ）")
        self.assertIn("ほかのPCへは移りません", local["lead"])
        self.assertIn("新しい版に入れ替えても", local["lead"])
        self.assertIn("ほかのPCにもすぐ効きます", _group(d, "shared")["lead"])
        self.assertIn("まだ無い項目だけ", _group(d, "dist")["lead"])
        self.assertEqual(d["badge"], {"local": "このPCに保存", "shared": "全ラインで共有",
                                      "dist": "アプリのフォルダ"})

    def test_the_log_is_the_integrated_one(self):
        from common import logging_utils, storage_places as SP
        self.assertEqual(SP.log_place().path, str(logging_utils.log_dir()))


class DetailsStorageTest(unittest.TestCase):
    def setUp(self):
        self.client = _make_app(self).test_client()
        headers = {"X-Tool-Token": TOKEN, "X-Tool-Screen": "s1"}
        res = self.client.post("/details/api/screen/claim", json={"screen": "s1"},
                               headers={"X-Tool-Token": TOKEN})
        self.assertEqual(res.status_code, 200)
        self.body = self.client.get("/details/api/settings", headers=headers).get_json()

    def test_local_files_are_listed_with_their_paths(self):
        from modules.packing_details.meisai import config
        local = _group(self.body["storage"], "local")
        self.assertIn(str(config.USER_CONFIG_PATH), _paths(local))
        self.assertIn(str(config.DB_PATH), _paths(local))
        names = [r["name"] for r in local["rows"]]
        self.assertEqual(names, ["設定ファイル", "手元のDB", "ログ"])
        self.assertIn("CSVの出力先", local["rows"][0]["holds"])

    def test_shared_files_are_in_the_master_folder(self):
        from modules.packing_details.meisai import config, shared_settings
        shared = _group(self.body["storage"], "shared")
        share = shared_settings.shared_dir()
        for name in (config.MASTER_DB_NAME, shared_settings.FILE_NAME, config.HISTORY_DB_NAME):
            self.assertIn(str(share / name), _paths(shared), name)
        # 取り込み元は読むだけ
        self.assertTrue(any("読むだけ" in r["note"] for r in shared["rows"]))
        # 「どこを見るか」はこのPCに保存(ほかのPCは変わらない)
        self.assertIn("ほかのPCは変わりません", shared["note"])

    def test_distribution_file(self):
        """梱包明細の配布設定と、共通(ログの出力先。統合 1.0.14)の配布設定。"""
        from common import log_distribution
        from modules.packing_details.meisai import distribution
        self.assertEqual(_paths(_group(self.body["storage"], "dist")),
                         [str(distribution.settings_path()),
                          str(log_distribution.settings_path())])

    def test_dialog_has_the_tab_and_the_badges(self):
        html = self.client.get(f"/details/meisai?t={TOKEN}").get_data(as_text=True)
        self.assertIn('id="tabStore"', html)
        self.assertIn('id="panelStore"', html)
        # 置き場所の4欄はこのPC、右上の文字・管理者パスワードは共有
        self.assertEqual(html.count('class="where where-local"'), 6)
        self.assertEqual(html.count('class="where where-shared"'), 2)
        self.assertIn("どのフォルダを見るかは<b>このPCに保存</b>", html)


class MaterialStorageTest(unittest.TestCase):
    def setUp(self):
        self.client = _make_app(self).test_client()
        res = self.client.get("/material/api/settings", headers={"X-App-Token": TOKEN})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.view = res.get_json()["view"]

    def test_local_files_are_listed_with_their_paths(self):
        from modules.packing_material_calculation.coil_tool import config
        local = _group(self.view["storage"], "local")
        self.assertIn(str(config.USER_CONFIG_PATH), _paths(local))
        self.assertIn(str(config.DB_PATH), _paths(local))
        # 資材計算のパスワードはこのPCのもの(梱包明細は全ラインで共有)
        self.assertIn("パスワード（撹拌した値。このPCだけのもの）", local["rows"][0]["holds"])

    def test_master_edits_go_to_the_shared_master(self):
        from modules.packing_material_calculation.coil_tool import config
        shared = _group(self.view["storage"], "shared")
        self.assertIn(str(config.master_db_dir() / config.MATERIAL_DB_NAME), _paths(shared))
        self.assertIn("マスタ管理", shared["rows"][0]["holds"])

    def test_page_has_the_tab_and_the_badges(self):
        html = self.client.get(f"/material/settings?t={TOKEN}").get_data(as_text=True)
        self.assertIn('id="tab-storage"', html)
        self.assertIn('id="panel-storage"', html)
        self.assertIn("このPCだけのパスワードです", html)
        self.assertIn('マスタ管理 <span class="where where-shared">', html)
        self.assertIn('ラインと担当者 <span class="where where-local">', html)


    def test_every_tab_is_known_to_the_script(self):
        """面を足したら `settings.js` の PANELS にも。無いと押しても開かない(通し試験で見つかった)。"""
        import re
        from pathlib import Path
        base = Path(__file__).resolve().parent.parent / "modules/packing_material_calculation/app"
        html = (base / "templates/settings.html").read_text(encoding="utf-8")
        js = (base / "static/js/views/settings.js").read_text(encoding="utf-8")
        tabs = re.findall(r'data-panel="([^"]+)"', html)
        panels = re.findall(r"'([a-z]+)'", re.search(r"const PANELS = \[([^\]]+)\]", js).group(1))
        self.assertIn("storage", tabs)
        self.assertEqual(tabs, panels)


class PenaStorageTest(unittest.TestCase):
    def setUp(self):
        self.client = _make_app(self).test_client()

    def _card(self) -> str:
        html = self.client.get("/pena/settings").get_data(as_text=True)
        start = html.index('<div class="card" id="storage">')
        return html[start:html.index("この画面の値の決まり方", start)]

    def test_local_files_are_listed_with_their_paths(self):
        from modules.packing_pena_label import server
        cfg = server._context.cfg
        card = self._card()
        local = card[card.index('data-store="local"'):card.index('data-store="shared"')]
        self.assertIn(cfg.local_config_path, local)
        self.assertIn(cfg.db_path, local)
        self.assertIn("印刷位置の補正", local)

    def test_legacy_access_is_shown_only_when_set(self):
        from modules.packing_pena_label import server
        cfg = server._context.cfg
        old = cfg.aim_ref_path
        self.addCleanup(setattr, cfg, "aim_ref_path", old)
        cfg.aim_ref_path = ""
        self.assertNotIn("旧 Access）", self._card())
        cfg.aim_ref_path = os.path.join("share", "aim")
        card = self._card()
        self.assertIn(os.path.join("share", "aim", "梱包資材マスタ.accdb"), card)
        self.assertIn("読むだけ", card)

    def test_setting_cards_carry_badges(self):
        html = self.client.get("/pena/settings").get_data(as_text=True)
        self.assertIn('参照先（外部ファイル） <span class="where where-local">', html)
        self.assertIn('書き先 <span class="where where-shared">', html)
        self.assertIn('class="where where-dist"', html)

    def test_calibration_says_where_it_is_kept(self):
        html = self.client.get("/pena/labels/calibration").get_data(as_text=True)
        self.assertIn("このPCの設定（local.json）", html)
        self.assertIn("次に起動したときも引き継ぎます", html)


if __name__ == "__main__":
    unittest.main()
