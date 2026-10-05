"""画面の色(統合 1.0.16。現場の指摘)。

    ペナラベルだけ背景がライトなのでダークにする
    3つのツール共通でダークとライトを切り替えるボタンの配置

- 統合画面の上の帯に「画面の色: 自動 / ライト / ダーク」。このPCに保存
- どの画面にも、選んだ色を `<html data-theme>` で付けて渡す(開いた瞬間から選んだ色)。
  開いている画面は `static/js/theme.js` がその場で切り替える
- 3機能・統合画面・ログの CSS は、どれも `data-theme` をブラウザの外観より優先する
  (ペナラベルは 1.5.12 で「ダークを選んだとき」を足した)
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app as app_module
from common import local_settings

from .test_app import TOKEN, _make_app

H = {"X-Tool-Token": TOKEN}
ROOT = Path(__file__).resolve().parent.parent


class _Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cpt-theme-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.dict(os.environ, {"COIL_PACKING_TOOLS_SETTINGS_PATH":
                                               str(self.tmp / "settings.json")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = _make_app(self).test_client()

    def set(self, theme, headers=H):
        return self.client.post("/api/theme", json={"theme": theme}, headers=headers)

    def html(self, path):
        sep = "&" if "?" in path else "?"
        res = self.client.get(f"{path}{sep}t={TOKEN}")
        self.assertEqual(res.status_code, 200, path)
        return res.get_data(as_text=True)


class SwitchTest(_Isolated):
    def test_the_bar_has_three_choices_and_auto_is_first_on(self):
        html = self.html("/?go=1")
        buttons = re.findall(r'data-theme-set="(\w+)"\s+aria-pressed="(\w+)"', html)
        self.assertEqual(buttons, [("auto", "true"), ("light", "false"), ("dark", "false")])
        self.assertIn("画面の色", html)

    def test_save_needs_the_token_and_a_known_value(self):
        self.assertEqual(self.set("dark", headers={}).status_code, 403)
        self.assertEqual(self.set("purple").status_code, 400)
        self.assertEqual(local_settings.theme(), "auto")
        res = self.set("dark")
        self.assertEqual(res.get_json(), {"ok": True, "theme": "dark"})
        self.assertEqual(local_settings.theme(), "dark", "このPCに保存")
        self.assertEqual(self.client.get("/api/theme").get_json()["theme"], "dark")
        self.set("auto")
        self.assertNotIn(local_settings.KEY_THEME, local_settings.load_all(), "自動は消す(既定)")

    def test_the_chosen_button_is_on_after_reopening(self):
        self.set("light")
        html = self.html("/?go=1")
        self.assertIn('data-theme-set="light"\n                aria-pressed="true"', html)

    def test_a_broken_value_in_the_file_is_auto(self):
        local_settings.save(local_settings.KEY_THEME, "まっくら")
        self.assertEqual(local_settings.theme(), "auto")


class EveryScreenGetsTheColorTest(_Isolated):
    SCREENS = ("/?go=1", "/log", "/details/meisai", "/pena/", "/pena/tare", "/material/calc",
               "/material/settings", "/pena/tare/print")

    def test_dark_is_put_on_every_screen(self):
        self.set("dark")
        for path in self.SCREENS:
            with self.subTest(path=path):
                html = self.html(path)
                self.assertRegex(html, r'<html lang="ja" data-theme="dark">')
                self.assertEqual(html.count(app_module.THEME_MARK), 1, "切り替えを受ける theme.js")

    def test_auto_puts_nothing(self):
        self.set("dark")
        self.set("auto")
        for path in self.SCREENS:
            with self.subTest(path=path):
                html = self.html(path)
                self.assertNotIn("data-theme=", html.split(">", 2)[1] + ">")
                self.assertIn('<html lang="ja">', html)

    def test_fragments_are_left_alone(self):
        """ペナラベルの「中身だけ」の断片(統合画面の中で差し替える)には付けない。"""
        self.set("dark")
        html = self.html("/pena/tare?pane=1")
        self.assertNotIn("<html", html)
        self.assertNotIn(app_module.THEME_MARK, html)

    def test_apply_theme(self):
        f = app_module.apply_theme
        self.assertEqual(f('<html lang="ja" data-theme="light"><body>', "dark"),
                         '<html lang="ja" data-theme="dark"><body>')
        self.assertEqual(f('<html lang="ja" data-theme="dark"><body>', "auto"),
                         '<html lang="ja"><body>')
        self.assertEqual(f("<div>断片</div>", "dark"), "<div>断片</div>")


class CssFollowsTheChoiceTest(unittest.TestCase):
    """どの画面の CSS も、`data-theme="dark"` なら暗く・`"light"` ならブラウザがダークでも明るい。"""

    FILES = ("static/css/shell.css", "static/css/log.css",
             "modules/packing_details/app/static/css/meisai.css",
             "modules/packing_material_calculation/app/static/css/tokens.css",
             "modules/packing_pena_label/app/static/css/app.css")

    def test_every_css_honours_the_choice(self):
        for name in self.FILES:
            with self.subTest(name):
                css = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn(':root[data-theme="dark"]', css, "ダークを選んだとき")
                self.assertIn(':root:not([data-theme="light"])', css, "ライトを選んだら外観は見ない")

    def test_pena_forced_dark_matches_the_auto_dark(self):
        """ペナラベルの「ダークを選んだとき」は、ブラウザがダークのときと同じ中身(食い違わせない)。"""
        css = (ROOT / self.FILES[-1]).read_text(encoding="utf-8")

        def block(head):
            start = css.index(head)
            i = css.index("{", start) + 1
            depth = 1
            while depth:
                depth += {"{": 1, "}": -1}.get(css[i], 0)
                i += 1
            return css[css.index("{", start) + 1:i - 1]

        auto = block("@media screen and (prefers-color-scheme:dark){")
        forced = block("@media screen{")
        self.assertEqual(forced, auto.replace(':root:not([data-theme="light"])',
                                              ':root[data-theme="dark"]'))
        self.assertGreater(forced.count(':root[data-theme="dark"]'), 50)


if __name__ == "__main__":
    unittest.main()
