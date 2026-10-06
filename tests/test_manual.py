"""操作説明書(統合 1.2.0。現場の依頼: 各ツールの操作説明書を GUI の写真入りの HTML で作り、
ツール上で開けるように。各ツールの版を表示する)

- 目次と4冊(はじめに・統合画面 / 梱包明細 / ペナラベル / 資材計算)が HTML のまま開く(ダウンロードにしない)
- どの冊にも、いま動いている版が出る(統合ツールと、そのツール)
- 説明書が使う写真はすべて `static/manual/img` にあり、控え(`shots.json`)の大きさと合う。
  使っていない写真は置かない
- 写真を撮った版と、いま動いているそのツールの版が違えば「写真は○○の画面」と断る
- 統合画面の上の帯に「説明書」(いま見ているタブの説明書をダイアログで開く・別の窓で開く)
- 梱包明細の見出しの「説明書」(移植元の文字だけの詳しい説明書)はそのまま。写真入りの説明書から案内する
"""
from __future__ import annotations

import json
import re
import struct
import unittest
from pathlib import Path
from unittest import mock

from common import manual, versions

from .test_app import TOKEN, _make_app

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates" / "manual"
IMG = ROOT / "static" / "manual" / "img"


def png_size(path: Path) -> tuple:
    """PNG の幅と高さ(IHDR)。Pillow を使わない(現場の PC には無い)。"""
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def referenced_images() -> dict:
    """説明書のテンプレートが `m.fig("名前", …)` で使う写真 → 使っている冊。"""
    found: dict = {}
    for path in TEMPLATES.glob("*.html"):
        for name in re.findall(r'm\.fig\("([^"]+)"', path.read_text(encoding="utf-8")):
            found.setdefault(name, []).append(path.name)
    return found


class ManualPagesTest(unittest.TestCase):
    def setUp(self):
        self.app = _make_app(self)
        self.client = self.app.test_client()

    def get(self, path):
        res = self.client.get(path)
        self.assertEqual(res.status_code, 200, path)
        self.assertEqual(res.mimetype, "text/html", path)
        self.assertNotIn("attachment", res.headers.get("Content-Disposition", ""),
                         "ダウンロードにしない")
        return res.get_data(as_text=True)

    def test_index_and_four_books_open_with_versions(self):
        current = versions.all_versions()
        html = self.get("/manual/")
        for value in current.values():
            self.assertIn(f"VER{value}", html, "目次に4つの版")
        for key, title, vkey, _ in manual.MANUALS:
            with self.subTest(key=key):
                page = self.get(f"/manual/{key}")
                self.assertIn(title, page)
                self.assertIn(f"VER{current['app']}", page, "統合ツールの版")
                self.assertIn(f"VER{current[vkey]}", page, "そのツールの版")
                for other in manual.KEYS:
                    self.assertIn(f'href="/manual/{other}"', page, "4冊を行き来できる")

    def test_unknown_book_goes_to_the_index(self):
        res = self.client.get("/manual/nothing")
        self.assertEqual(res.status_code, 302)
        self.assertTrue(res.headers["Location"].endswith("/manual/"))

    def test_embedded_links_stay_embedded(self):
        """統合画面のダイアログの中(?embed=1)で冊を移っても、ダイアログの形のまま。"""
        page = self.get("/manual/details?embed=1")
        self.assertIn('class="embed"', page)
        for key in manual.KEYS:
            self.assertIn(f'href="/manual/{key}?embed=1"', page)

    def test_every_photo_is_shown(self):
        page = "".join(self.get(f"/manual/{key}") for key in manual.KEYS)
        self.assertNotIn('class="noshot"', page, "写真が無い図がある(tools/make_manual_shots.py で撮る)")
        for name in referenced_images():
            self.assertIn(f"/static/manual/img/{name}.png?v=", page, name)

    def test_photos_older_than_the_tool_are_noted(self):
        meta = manual.shots_meta()
        current = versions.all_versions()
        old = dict(meta, versions=dict(current, pena="0.0.1"))
        with mock.patch.object(manual, "shots_meta", return_value=old):
            pena = self.get("/manual/pena")
            details = self.get("/manual/details")
        self.assertIn("写真は", pena)
        self.assertIn("VER0.0.1", pena)
        self.assertNotIn('class="stale"', details, "ほかのツールの版は関わらない")

    def test_desktop_and_theme_are_applied_like_other_screens(self):
        self.app.config["BRIDGE"] = True
        page = self.get("/manual/details")
        self.assertIn("js/desktop.js", page, "デスクトップ版では窓まわりを外枠に頼む")
        self.assertIn("js/theme.js", page, "画面の色の切り替えを受ける")


class ManualPhotosTest(unittest.TestCase):
    def test_used_photos_exist_and_match_the_record(self):
        meta = manual.shots_meta()
        images = meta.get("images") or {}
        for name, books in referenced_images().items():
            with self.subTest(name=name, books=books):
                path = IMG / f"{name}.png"
                self.assertTrue(path.is_file(), f"{name}.png が無い")
                self.assertIn(name, images, "shots.json に控えが無い")
                self.assertEqual(png_size(path), (images[name]["w"], images[name]["h"]),
                                 "控えの大きさと違う(撮り直したら shots.json も書き直す)")

    def test_no_unused_photos(self):
        used = set(referenced_images())
        on_disk = {p.stem for p in IMG.glob("*.png")}
        self.assertEqual(on_disk - used, set(), "説明書で使っていない写真がある")
        self.assertEqual(set((manual.shots_meta().get("images") or {})) - used, set())

    def test_record_says_when_and_which_versions(self):
        meta = manual.shots_meta()
        self.assertRegex(meta.get("taken", ""), r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(set(meta.get("versions") or {}), set(versions.all_versions()))

    def test_no_password_in_the_books(self):
        """パスワード(既定値)は説明書に載せない。"""
        for path in TEMPLATES.glob("*.html"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("nisk", text, path.name)


class OpenFromTheToolTest(unittest.TestCase):
    def setUp(self):
        self.app = _make_app(self)
        self.client = self.app.test_client()

    def test_shell_has_the_manual_button_and_dialog(self):
        html = self.client.get(f"/?go=1&t={TOKEN}").get_data(as_text=True)
        self.assertIn('id="manualBtn"', html)
        self.assertIn('id="manualDialog"', html)
        self.assertIn('id="manualPop"', html, "別の窓で開く")
        js = (ROOT / "static" / "js" / "shell.js").read_text(encoding="utf-8")
        self.assertIn('"/manual/" + encodeURIComponent(currentKey()) + "?embed=1"', js,
                      "いま見ているタブの説明書を開く")
        self.assertIn('window.open(path, "_blank")', js)

    def test_details_own_manual_stays_and_is_linked(self):
        """梱包明細の「説明書」(文字だけの詳しい説明書)は変えない。写真入りから案内する。"""
        self.assertEqual(self.client.get("/details/docs").status_code, 200)
        page = self.client.get("/manual/details").get_data(as_text=True)
        self.assertIn('href="/details/docs"', page)

    def test_manual_keys_match_the_tabs(self):
        tabs = {m["key"] for m in self.app.config["MODULES"]}
        self.assertEqual(tabs | {"shell"}, set(manual.KEYS))


if __name__ == "__main__":
    unittest.main()
