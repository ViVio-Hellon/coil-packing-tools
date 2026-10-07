r"""Microsoft Store の Python(現場の PC。統合 1.2.1 の再点検で見つけた)

Store の Python は、`%LOCALAPPDATA%` の下に新しく作ったファイルを、その Python だけの場所
(`%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.12_…\LocalCache\Local\…`)へ
置き換えて置く。Python からは元の場所にあるように見えるが、エクスプローラー・ブラウザ・
外枠(exe)は元の場所を見るので見つからない(CPython の Store 版は 3.11 からレジストリの
置き換えは止めたが、ファイルの置き換えは続いている)。

だから、**画面に出す場所・ほかのプログラムに渡す場所は、実際に置かれた場所**にする
(`app_config.real_location`)。ここでは置き換えを `os.path.realpath` の代わりで作って確かめる。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from common import app_config, boot_screen, storage_places

from .test_app import _make_app

LOCAL = "/home/u/AppData/Local"
CACHE = "/home/u/AppData/Local/Packages/PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0/LocalCache/Local"


def store_realpath(text: str) -> str:
    """Store の Python の `os.path.realpath` の代わり: %LOCALAPPDATA% の下を置き換え先に。"""
    return text.replace(LOCAL, CACHE, 1)


def fake_real_location(path) -> str:
    text = str(path)
    return text.replace(os.environ.get("COIL_PACKING_TOOLS_LOCAL_DIR", "\0"), "/LocalCache/CoilPackingTools", 1)


class RealLocationTest(unittest.TestCase):
    def test_under_localappdata_is_the_real_place(self):
        got = app_config._real_location(LOCAL + "/CoilPackingTools/logs", LOCAL, store_realpath)
        self.assertEqual(got, CACHE + "/CoilPackingTools/logs")

    def test_shares_and_app_folder_are_left_alone(self):
        """共有フォルダ・アプリのフォルダはそのまま(ネットワークドライブを UNC に書き換えない)。"""
        boom = mock.Mock(side_effect=AssertionError("呼ばない"))
        for text in ("/mnt/share/【■】_参照用ファイル", "/home/u/Desktop/app/export", LOCAL + "x/y"):
            with self.subTest(text=text):
                self.assertEqual(app_config._real_location(text, LOCAL, boom), text)

    def test_never_breaks_the_caller(self):
        self.assertEqual(app_config._real_location(LOCAL + "/a", "", store_realpath), LOCAL + "/a")
        broken = mock.Mock(side_effect=OSError("読めない"))
        self.assertEqual(app_config._real_location(LOCAL + "/a", LOCAL, broken), LOCAL + "/a")

    @unittest.skipIf(os.name == "nt", "Windows 以外")
    def test_other_os_is_unchanged(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": LOCAL}):
            self.assertEqual(app_config.real_location(LOCAL + "/x"), LOCAL + "/x")


class ShownPlacesTest(unittest.TestCase):
    """設定画面の「保存先」・待機画面・上の帯の「ログ」・起動できないときの画面。"""

    def test_storage_places_show_the_real_place(self):
        places = storage_places.Places(local=[storage_places.Place("ログ", LOCAL + "/C/logs", "記録")],
                                       shared=[storage_places.Place("マスタ", "/mnt/share/m.sqlite3", "x")])
        with mock.patch.object(app_config, "real_location", side_effect=store_realpath):
            rows = {g["kind"]: g["rows"] for g in places.to_dict()["groups"]}
        self.assertEqual(rows["local"][0]["path"], CACHE + "/C/logs")
        self.assertEqual(rows["shared"][0]["path"], "/mnt/share/m.sqlite3")

    def test_boot_screen_shows_the_real_log_place(self):
        with mock.patch.object(app_config, "real_location", side_effect=fake_real_location):
            html = boot_screen.render(display_name="x", version_label="VER1", token="t", app_id="a",
                                      poll_ms=500, home_url="/")
        self.assertIn("/LocalCache/CoilPackingTools/logs", html)
        self.assertNotIn("%LOCALAPPDATA%", html)

    def test_log_page_shows_the_real_settings_file(self):
        app = _make_app(self)
        with mock.patch.object(app_config, "real_location", side_effect=fake_real_location):
            html = app.test_client().get("/log?embed=1").get_data(as_text=True)
        self.assertIn("/LocalCache/CoilPackingTools/data/settings.json", html)

    def test_browser_error_page_is_opened_at_the_real_place(self):
        """ブラウザ版の「起動できませんでした」: ブラウザ(Python の外)に実際の場所を渡す。"""
        import start_app
        opened = []
        with mock.patch.object(app_config, "real_location", side_effect=fake_real_location), \
                mock.patch.object(start_app.webbrowser, "open", side_effect=opened.append):
            start_app.report_failure(start_app.StartupError("だめ", "こうする"), open_browser=True)
        self.assertEqual(len(opened), 1)
        self.assertIn("LocalCache/CoilPackingTools", opened[0])
        page = Path(app_config.local_dir("work") / "起動エラー.html").read_text(encoding="utf-8")
        self.assertIn("/LocalCache/CoilPackingTools/logs", page, "ログの場所も実際の場所")

    def test_desktop_failure_tells_the_shell_the_real_place(self):
        """デスクトップ版: 起動できないとき外枠へ知らせるログの場所も実際の場所。"""
        source = (Path(__file__).resolve().parent.parent / "bridge.py").read_text(encoding="utf-8")
        fatal = source[source.index("def fatal("):source.index('writer.event("fatal"')]
        self.assertIn("real_location(logging_utils.log_dir())", fatal)


if __name__ == "__main__":
    unittest.main()
