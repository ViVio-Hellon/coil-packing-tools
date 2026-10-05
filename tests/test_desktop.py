"""デスクトップ版の決まり(統合 1.1.0)── ブラウザ版とのすみ分け

- `desktop.js`(窓まわりを外枠に頼む)は**デスクトップ版のときだけ**、画面の先頭に差し込む
- ブラウザ版とデスクトップ版は**同時に動かさない**(同じ手元のDB・作業状態を使う)。
  exe は動いているあいだ `runtime/desktop.lock` を握っている
- デスクトップ版はポートを使わないので、画面の「ポート」は「なし（デスクトップ版）」
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app as app_module
import launch_guard
import start_app

from .test_app import TOKEN, _make_app


class DesktopScriptTest(unittest.TestCase):
    def setUp(self):
        self.app = _make_app(self)
        self.client = self.app.test_client()

    def page(self, path):
        sep = "&" if "?" in path else "?"
        return self.client.get(f"{path}{sep}t={TOKEN}").get_data(as_text=True)

    def test_only_the_desktop_version_gets_it(self):
        for path in ("/?go=1", "/details/meisai", "/pena/", "/material/calc", "/log"):
            with self.subTest(path=path, version="ブラウザ版"):
                self.assertNotIn(app_module.DESKTOP_MARK, self.page(path))
        self.app.config["BRIDGE"] = True
        for path in ("/?go=1", "/details/meisai", "/pena/", "/material/calc", "/log",
                     "/pena/tare/print"):
            with self.subTest(path=path, version="デスクトップ版"):
                html = self.page(path)
                self.assertEqual(html.count(app_module.DESKTOP_MARK), 1)
                head = html.lower().index("<head>")
                self.assertLess(html.index(app_module.DESKTOP_MARK), html.index("</head>"))
                self.assertGreater(html.index(app_module.DESKTOP_MARK), head,
                                   "画面のスクリプトより先(head の先頭)")

    def test_fragments_are_left_alone(self):
        self.app.config["BRIDGE"] = True
        self.assertNotIn(app_module.DESKTOP_MARK, self.page("/pena/tare?pane=1"))

    def test_desktop_host_is_allowed_only_in_the_desktop_version(self):
        res = self.client.get("/api/health", headers={"Host": "app.localhost"})
        self.assertEqual(res.status_code, 400, "ブラウザ版では知らない宛先")
        self.app.config["BRIDGE"] = True
        res = self.client.get("/api/health", headers={"Host": "app.localhost"})
        self.assertEqual(res.status_code, 200)


class PortShownAsNoneTest(unittest.TestCase):
    def test_pena_footer_and_diag(self):
        flask_app = _make_app(self)
        flask_app.config["BRIDGE"] = True
        flask_app.config["PORT"] = 0
        client = flask_app.test_client()
        html = client.get(f"/pena/?t={TOKEN}").get_data(as_text=True)
        self.assertIn("ポート: なし（デスクトップ版）", html)
        diag = client.get(f"/pena/diag?t={TOKEN}").get_data(as_text=True)
        self.assertIn("なし（デスクトップ版）", diag)

    def test_details_and_material_scripts(self):
        root = Path(__file__).resolve().parent.parent
        meisai = (root / "modules/packing_details/app/static/js/meisai.js").read_text(encoding="utf-8")
        self.assertIn("health.port || \"なし（デスクトップ版）\"", meisai)
        settings = (root / "modules/packing_material_calculation/app/static/js/views/settings.js"
                    ).read_text(encoding="utf-8")
        self.assertIn("'なし（デスクトップ版）'", settings)


class DesktopLockTest(unittest.TestCase):
    """exe が握る `runtime/desktop.lock` を、ブラウザ版が見分ける。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cpt-desklock-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.dict(os.environ, {"COIL_PACKING_TOOLS_LOCAL_DIR": str(self.tmp)})
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.tmp / "runtime").mkdir()
        self.lock = self.tmp / "runtime" / launch_guard.DESKTOP_LOCK_NAME

    def test_no_file_means_not_running(self):
        self.assertFalse(launch_guard.desktop_running())

    def test_a_held_lock_means_running(self):
        """exe の代わりに錠を握る(OS のロック。別に開いた口からは締められない)。"""
        with open(self.lock, "a+b") as held:
            self.assertTrue(launch_guard._try_lock(held))
            self.assertTrue(launch_guard.desktop_running())
            launch_guard._unlock(held)
        self.assertFalse(launch_guard.desktop_running(), "落ちれば OS が外す(印が残っても惑わない)")

    def test_browser_version_refuses_while_the_desktop_runs(self):
        guard = mock.Mock(should_start=True, reason="ロックなし")
        with mock.patch.object(launch_guard, "check_existing", return_value=guard), \
                mock.patch.object(launch_guard, "find_legacy_instances", return_value=[]), \
                mock.patch.object(launch_guard, "desktop_running", return_value=True), \
                mock.patch.object(launch_guard, "pick_port") as pick, \
                mock.patch.object(start_app, "log_environment"):
            with self.assertRaises(start_app.StartupError) as caught:
                start_app.start("main", open_browser=False)
        self.assertIn("デスクトップ版", str(caught.exception))
        pick.assert_not_called()


if __name__ == "__main__":
    unittest.main()
