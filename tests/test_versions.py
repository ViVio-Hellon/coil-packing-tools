"""統合ツールの版と3機能の版(分けて持つ。`common/versions.py`)

- 統合ツールの版は `config/app.json`、機能の版はそれぞれの出どころのまま
- 画面(帯の版の一覧・タブ)と起動確認(`/api/health`)に両方が出る
- 起動時の入れ替え判定は**組**で比べる(機能の版だけ上げても入れ替わる)
- 静的ファイルの控えは中身が変われば必ず作り直される
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from common import app_config, security, versions

from .test_app import TOKEN, _make_app

ROOT = Path(__file__).resolve().parent.parent


def _json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class SourcesTest(unittest.TestCase):
    def test_版はそれぞれの出どころから読む(self):
        from modules.packing_pena_label.app import config as pena_config
        got = versions.all_versions()
        self.assertEqual(got["app"], app_config.version())
        self.assertEqual(got["details"], _json("modules/packing_details/config/app.json")["version"])
        self.assertEqual(got["material"],
                         _json("modules/packing_material_calculation/config/app.json")["version"])
        self.assertEqual(got["pena"], pena_config.APP_VERSION)

    def test_統合ツールの版は機能の版と別に持つ(self):
        self.assertEqual(list(versions.all_versions()), ["app", "details", "pena", "material"])
        self.assertNotIn("modules", _json("config/app.json"), "機能の版を統合ツールの設定に写さない")

    def test_移植元の版を記録している(self):
        for m in versions.module_versions():
            self.assertRegex(m["ported_from"].get("repo", ""), r"^ViVio-Hellon/packing-", m["key"])
            self.assertRegex(m["ported_from"].get("version", ""), r"^\d+\.\d+\.\d+$", m["key"])

    def test_版の組は読み戻せる(self):
        text = versions.version_set()
        self.assertTrue(text.startswith("app="), text)
        self.assertEqual(versions.parse_set(text), versions.all_versions())

    def test_違いの説明(self):
        mine = {"app": "1.0.0", "details": "0.13.2", "pena": "1.5.0", "material": "0.2.0"}
        self.assertEqual(versions.differences(dict(mine), mine), [])
        old = dict(mine, details="0.13.1")
        self.assertEqual(versions.differences(old, mine), ["梱包明細 0.13.1 → 0.13.2"])
        old = dict(mine, app="0.9.0")
        self.assertEqual(versions.differences(old, mine),
                         [f"{app_config.display_name()} 0.9.0 → 1.0.0"])

    def test_人に見せる形(self):
        text = versions.describe()
        for label in (app_config.display_name(), "梱包明細", "ペナラベル", "資材計算"):
            self.assertIn(label, text)


class ShownTest(unittest.TestCase):
    def setUp(self):
        self.client = _make_app(self).test_client()

    def test_起動確認に両方が出る(self):
        body = self.client.get("/api/health").get_json()
        self.assertEqual(body["version"], app_config.version())
        self.assertEqual(body["versions"], versions.all_versions())
        self.assertEqual(body["version_set"], versions.version_set())
        self.assertTrue(all(m["ported_from"] for m in body["modules"]))

    def test_画面の帯に版の一覧が出る(self):
        html = self.client.get(f"/?t={TOKEN}").get_data(as_text=True)
        start = html.index("<tbody>", html.index('id="verPanel"'))
        panel = html[start:html.index("</tbody>", start)]
        rows = re.findall(r"<tr[^>]*>\s*<th>(.*?)<small>.*?</small></th>\s*<td>(.*?)</td>", panel, re.S)
        got = [(label.strip(), ver.strip()) for label, ver in rows]
        v = versions.all_versions()
        self.assertEqual(got, [(app_config.display_name(), v["app"]), ("梱包明細", v["details"]),
                               ("ペナラベル", v["pena"]), ("資材計算", v["material"])])
        # タブにもそれぞれの機能の版
        for key in ("details", "pena", "material"):
            self.assertIn(f"VER{v[key]}", html)


class ReplaceTest(unittest.TestCase):
    """起動時の入れ替え判定(`launch_guard._stale_versions`)。"""

    def test_同じ組なら合流する(self):
        import launch_guard
        health = {"version": app_config.version(), "version_set": versions.version_set()}
        self.assertEqual(launch_guard._stale_versions(health), [])

    def test_機能の版だけ違っても入れ替える(self):
        import launch_guard
        old = dict(versions.all_versions(), details="0.0.1")
        health = {"version": app_config.version(), "version_set": versions.version_set(old)}
        diff = launch_guard._stale_versions(health)
        self.assertEqual(len(diff), 1)
        self.assertIn("梱包明細 0.0.1 →", diff[0])

    def test_組を答えない古いプロセスは統合ツールの版で比べる(self):
        import launch_guard
        self.assertEqual(launch_guard._stale_versions({"version": app_config.version()}), [])
        self.assertTrue(launch_guard._stale_versions({"version": "0.0.1"}))


class StaticStampTest(unittest.TestCase):
    def setUp(self):
        self.client = _make_app(self).test_client()

    def test_静的ファイルの版は版と中身の指紋(self):
        html = self.client.get(f"/?t={TOKEN}").get_data(as_text=True)
        self.assertRegex(html, r"/static/css/shell\.css\?v=%s-[0-9a-f]{8}\"" % re.escape(app_config.version()))
        details = self.client.get(f"/details/meisai?t={TOKEN}").get_data(as_text=True)
        self.assertRegex(details, r"/details/static/[^\"]+\?v=%s-[0-9a-f]{8}\""
                         % re.escape(versions.all_versions()["details"]))

    def test_中身が変われば指紋が変わる(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.js"
            p.write_text("one", encoding="utf-8")
            first = security._fingerprint(d, "a.js")
            p.write_text("two!", encoding="utf-8")
            second = security._fingerprint(d, "a.js")
            self.assertRegex(first, r"^[0-9a-f]{8}$")
            self.assertNotEqual(first, second)
            self.assertEqual(security._fingerprint(d, "../a.js"), "", "外は読まない")
            self.assertEqual(security._fingerprint(d, "none.js"), "")


if __name__ == "__main__":
    unittest.main()
