"""画面の JS(ES モジュール)が、版付きと版なしの2つの URL で読まれないこと。

統合版は静的ファイルの URL に版を付ける(`?v=<版>-<指紋>`。common/security.py)。
ES モジュールは **URL ごとに別物** になるので、同じファイルをテンプレートが版付きで読み、
別の JS が `./x.js` と相対で import すると、モジュールが2つできる。片方へ出した指示
(例: 認証したあとの refresh)が、画面を出しているもう片方に届かない。

移植元は URL に版を付けていなかったので起きなかった。統合版で実際に起きた:
資材計算の設定画面で認証しても、マスタ管理の面が「見るだけ」のままだった。
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

from .test_app import TOKEN, _make_app

ROOT = Path(__file__).resolve().parent.parent
MODULES = ("packing_details", "packing_material_calculation")

# テンプレートが版付きで読む JS(`v=None` で版を外したものは除く)
_ENTRY = re.compile(r"url_for\('[a-z]+\.static',\s*filename='(js/[^']+)'(?P<rest>[^)]*)\)")
# JS の中の相対 import(静的 import と動的 import)
_IMPORT = re.compile(r"""(?:import[^'"]*?from\s*|import\s*\(\s*)['"](\.{1,2}/[^'"]+)['"]""")


def _versioned_entries(module: str) -> set:
    found = set()
    for t in (ROOT / "modules" / module / "app" / "templates").rglob("*.html"):
        for m in _ENTRY.finditer(t.read_text(encoding="utf-8")):
            if "v=None" not in m.group("rest"):
                found.add(m.group(1))
    return found


def _relative_imports(module: str) -> dict:
    static = ROOT / "modules" / module / "app" / "static"
    found: dict = {}
    for js in static.rglob("*.js"):
        for m in _IMPORT.finditer(js.read_text(encoding="utf-8")):
            target = Path(os.path.normpath(js.parent / m.group(1)))
            found.setdefault(target.relative_to(static).as_posix(), []).append(
                js.relative_to(static).as_posix())
    return found


class NoSplitModulesTest(unittest.TestCase):
    def test_no_js_is_both_a_versioned_entry_and_a_relative_import(self):
        for module in MODULES:
            entries = _versioned_entries(module)
            imports = _relative_imports(module)
            self.assertTrue(entries, module)
            both = {e: imports[e] for e in entries if e in imports}
            self.assertEqual(both, {}, f"{module}: 版付きで読み、相対でも import している")

    def test_material_settings_loads_master_without_version(self):
        """資材計算の設定画面: master.js はテンプレートからも版なしで読む。"""
        client = _make_app(self).test_client()
        html = client.get(f"/material/settings?t={TOKEN}").get_data(as_text=True)
        m = re.search(r'import \{ start \} from "([^"]+)"', html)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "/material/static/js/views/master.js")


if __name__ == "__main__":
    unittest.main()
