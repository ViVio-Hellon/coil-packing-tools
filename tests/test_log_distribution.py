"""ログの出力先を配布設定に入れる(統合 1.0.14。現場の指摘)。

    ログの出力先を配布設定に入れるかどうか: 入れてください

- 上の帯の「ログ」→「出力先の設定」から、`配布設定\\common\\` に書き出す・読み込み直す・消す
  (梱包明細の管理者パスワード)
- 配った先は起動したとき読み込む。**そのPCで設定してある項目は読まない**(3機能と同じ)
- 読み込んだら、ログはすぐ新しい出力先へ出る
- ペナラベルの「配布用フォルダを作る」にも入る
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from common import dist_settings, local_settings, log_distribution, logging_utils

from .test_app import TOKEN, _make_app

H = {"X-Tool-Token": TOKEN}
PASSWORD = __import__("modules.packing_details.meisai.config",
                      fromlist=["ADMIN_PASSWORD"]).ADMIN_PASSWORD


class _Isolated(unittest.TestCase):
    """このPCの設定・配布設定・ログの出力先を、使い捨ての場所へ。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cpt-logdist-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # 元の場所へ戻す(後の試験のため)。**環境変数を戻したあとに**読み直す
        # (後片付けは逆順)。先に読み直すと、試験で入れた出力先(`\\server\share\logs`)を
        # 見に行き、Linux ではアプリのフォルダにその名のフォルダを作り、Windows では
        # ネットワークの場所へ書きに行っていた
        self.addCleanup(logging_utils.apply_settings)
        env = {k: v for k, v in os.environ.items() if k != logging_utils.ENV_LOG_DIR}
        env["COIL_PACKING_TOOLS_SETTINGS_PATH"] = str(self.tmp / "settings.json")
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        dist = mock.patch.object(log_distribution, "DIR", self.tmp / "配布設定" / "common")
        dist.start()
        self.addCleanup(dist.stop)
        logging_utils.apply_settings()

    def write_bundle(self, settings, fmt=1):
        log_distribution.DIR.mkdir(parents=True, exist_ok=True)
        log_distribution.settings_path().write_text(
            json.dumps({"format": fmt, "created_at": "2026-10-02 09:00:00",
                        "created_on": "LINE-PC-01", "settings": settings}, ensure_ascii=False),
            encoding="utf-8")


class NoStrayFolderTest(_Isolated):
    def test_cleanup_does_not_create_the_saved_log_place(self):
        """試験で入れた出力先を、後片付けで見に行かない(アプリのフォルダを汚さない)。"""
        from common import app_config
        local_settings.save(local_settings.KEY_LOG_DIR, r"\\server\share\logs")
        self.doCleanups()
        self.assertFalse((Path(app_config.APP_ROOT) / r"\\server\share\logs").exists())


class ExportTest(_Isolated):
    def test_writes_only_what_this_pc_changed(self):
        local_settings.save(local_settings.KEY_LOG_DIR, r"\\server\share\logs")
        result = log_distribution.export()
        self.assertTrue(result.ok, result.message)
        data = json.loads(log_distribution.settings_path().read_text(encoding="utf-8"))
        self.assertEqual(data["settings"], {"log_dir": r"\\server\share\logs"},
                         "残す日数は既定のままなので入れない")
        self.assertIn("ログとエラーの記録を残す日数", result.message)
        readme = (log_distribution.DIR / log_distribution.README_NAME).read_text(encoding="utf-8-sig")
        self.assertIn(r"ログの出力先: \\server\share\logs", readme)

    def test_nothing_to_export_when_all_default(self):
        result = log_distribution.export()
        self.assertFalse(result.ok)
        self.assertIn("先に出力先を保存してください", result.message)
        self.assertFalse(log_distribution.DIR.exists())

    def test_export_replaces_the_previous_one(self):
        local_settings.save(local_settings.KEY_LOG_KEEP_DAYS, 90)
        self.assertTrue(log_distribution.export().ok)
        local_settings.save(local_settings.KEY_LOG_KEEP_DAYS, 365)
        self.assertTrue(log_distribution.export().ok)
        data = json.loads(log_distribution.settings_path().read_text(encoding="utf-8"))
        self.assertEqual(data["settings"], {"log_keep_days": 365})
        self.assertFalse(log_distribution._previous().exists())
        self.assertFalse(log_distribution._staging().exists())


class ApplyOnStartTest(_Isolated):
    def test_fills_only_what_this_pc_does_not_have(self):
        self.write_bundle({"log_dir": str(self.tmp / "share"), "log_keep_days": 365})
        local_settings.save(local_settings.KEY_LOG_KEEP_DAYS, 60)
        result = log_distribution.apply_on_start()
        self.assertTrue(result.ok)
        self.assertEqual(result.applied, ["ログの出力先"])
        self.assertEqual(result.kept, ["ログとエラーの記録を残す日数"])
        self.assertEqual(local_settings.get("log_dir"), str(self.tmp / "share"))
        self.assertEqual(local_settings.get("log_keep_days"), 60, "このPCの値はそのまま")
        # 2回目は「すでにある」── このPCで直した値を戻さない
        local_settings.save(local_settings.KEY_LOG_DIR, str(self.tmp / "mine"))
        again = log_distribution.apply_on_start()
        self.assertEqual(again.applied, [])
        self.assertEqual(local_settings.get("log_dir"), str(self.tmp / "mine"))

    def test_nothing_placed_is_quiet(self):
        result = log_distribution.apply_on_start()
        self.assertTrue(result.ok)
        self.assertEqual((result.applied, result.message), ([], ""))

    def test_bad_values_are_named_not_used(self):
        self.write_bundle({"log_dir": "  ", "log_keep_days": "365", "unknown": 1})
        result = log_distribution.apply_on_start()
        self.assertFalse(result.ok)
        self.assertIn("入っている項目がありません", result.message)
        self.write_bundle({"log_dir": str(self.tmp / "share"), "log_keep_days": 5})
        result = log_distribution.apply_on_start()
        self.assertEqual(result.applied, ["ログの出力先"])
        self.assertIn("ログとエラーの記録を残す日数", result.message)
        self.assertIsNone(local_settings.get("log_keep_days"))

    def test_broken_file_is_reported(self):
        log_distribution.DIR.mkdir(parents=True)
        log_distribution.settings_path().write_text("{こわれた", encoding="utf-8")
        result = log_distribution.apply_on_start()
        self.assertFalse(result.ok)
        self.assertIn("読めません", result.message)
        self.write_bundle({"log_dir": "x"}, fmt=2)
        self.assertIn("形が違います", log_distribution.apply_on_start().message)

    def test_start_reads_it_before_the_first_log_line(self):
        """起動の記録から、配られた出力先へ出る(その下の PC の名前のフォルダ)。"""
        import start_app
        share = self.tmp / "share"
        self.write_bundle({"log_dir": str(share)})
        with mock.patch.object(start_app, "log_module_environment"), \
                mock.patch.object(start_app, "tidy_logs"):
            start_app.log_environment("main")
        folder = share / logging_utils.pc_name()
        self.assertEqual(logging_utils.log_dir(), folder)
        text = (folder / f"coil_packing_tools_{date.today():%Y%m%d}.log").read_text(encoding="utf-8")
        self.assertIn("起動: mode=main", text)
        self.assertIn("配布設定(共通)を読み込みました: ログの出力先", text)


    def test_a_broken_part_does_not_stop_the_start(self):
        import start_app
        with mock.patch.object(log_distribution, "apply_on_start", side_effect=RuntimeError("x")):
            loaded = start_app.apply_common_distribution()
        self.assertEqual((loaded.applied, loaded.kept), ([], []))
        self.assertIn("読めませんでした", loaded.message)


class RouteTest(_Isolated):
    def setUp(self):
        super().setUp()
        self.client = _make_app(self).test_client()

    def post(self, **body):
        return self.client.post("/api/log/distribution", json=body, headers=H)

    def test_needs_the_admin_password(self):
        for action in ("export", "reapply", "remove"):
            with self.subTest(action=action):
                res = self.post(action=action, password="ちがう")
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.get_json()["field"], "password")
        self.assertEqual(self.post(action="なにか", password=PASSWORD).status_code, 400)
        self.assertEqual(self.client.get("/api/log/distribution").status_code, 403, "トークン")

    def test_export_then_reapply_switches_the_log_at_once(self):
        share = self.tmp / "share"
        saved = self.client.post("/api/log/settings", headers=H,
                                 json={"action": "save", "log_dir": str(share), "keep_days": 90})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        res = self.post(action="export", password=PASSWORD)
        self.assertEqual(res.status_code, 200, res.get_json())
        body = res.get_json()
        self.assertTrue(body["distribution"]["exists"])
        self.assertEqual([c["label"] for c in body["distribution"]["contents"]],
                         ["ログの出力先", "ログとエラーの記録を残す日数"])

        # このPCを既定に戻してから、読み込み直す → 配布設定の出力先へすぐ切り替わる
        self.client.post("/api/log/settings", headers=H, json={"action": "reset", "keep_days": 180})
        self.assertEqual(logging_utils.log_dir(), logging_utils.default_log_dir())
        res = self.post(action="reapply", password=PASSWORD)
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()["log"]["dir"], str(share / logging_utils.pc_name()))
        self.assertEqual(res.get_json()["log"]["keep_days"], 90)
        logging.getLogger("coil_packing_tools.x").info("読み込み直したあとの行")
        today = share / logging_utils.pc_name() / f"coil_packing_tools_{date.today():%Y%m%d}.log"
        self.assertIn("読み込み直したあとの行", today.read_text(encoding="utf-8"))

        res = self.post(action="remove", password=PASSWORD)
        self.assertTrue(res.get_json()["ok"])
        self.assertFalse(log_distribution.DIR.exists())
        self.assertEqual(logging_utils.log_dir(), share / logging_utils.pc_name(),
                         "消してもこのPCの設定はそのまま")

    def test_export_with_nothing_set_is_refused_with_the_reason(self):
        res = self.post(action="export", password=PASSWORD)
        self.assertEqual(res.status_code, 400)
        self.assertIn("既定のまま", res.get_json()["message"])

    def test_log_page_has_the_distribution_card(self):
        html = self.client.get(f"/log?t={TOKEN}").get_data(as_text=True)
        for key in ("distCard", "distPassword", "btnDistExport", "btnDistReapply", "btnDistRemove"):
            self.assertIn(f'id="{key}"', html)
        self.assertIn('type="password" id="distPassword"', html)


class DistFolderTest(_Isolated):
    """ペナラベルの「配布用フォルダを作る」にも入る(書き出してあれば)。"""

    def test_common_goes_in_with_the_three(self):
        from modules.packing_pena_label.app.services import dist_folder as DF
        self.assertIn(dist_settings.COMMON, dist_settings.ALL)
        self.assertNotIn(dist_settings.COMMON, dist_settings.MODULES,
                         "「書き出していない機能」は3機能だけを数える")
        local_settings.save(local_settings.KEY_LOG_DIR, r"\\server\share\logs")
        self.assertTrue(log_distribution.export().ok)
        found = DF._settings_sources(DF.ROOT, None)
        self.assertIn("common", [key for key, _, _, _ in found])
        common = [f for f in found if f[0] == "common"][0]
        lines = DF._settings_lines(common[2], common[3])
        self.assertIn(r"  ログの出力先: \\server\share\logs", lines)

    def test_storage_tabs_name_the_common_distribution(self):
        from common import storage_places
        place = storage_places.log_dist_place()
        self.assertEqual(place.path, str(log_distribution.settings_path()))
        self.assertIn("まだありません", place.note)


if __name__ == "__main__":
    unittest.main()
