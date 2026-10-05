"""3機能の Web の試験を、統合アプリに載せた形(入口+トークン)でもう一度流す。

3機能の Web の試験は移植元のままで、機能だけの Flask(入口なし)を相手にしている。
統合版で実際に動くのは統合アプリに `/details` `/pena` `/material` で載った形なので、
その形でも流す(移植漏れの点検で見つかった)。差し替えは `tools/pytest_mounted.py`。

それぞれ別のプロセスで流す(差し替えが根の試験に漏れないように。3機能の試験は
それぞれ自分の置き場所の隔離を持っている)。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from .test_app import TOKEN, _make_app

ROOT = Path(__file__).resolve().parent.parent

#: 画面・API を叩く試験(相手を `create_app` / `create_server` で作るもの)
WEB_TESTS = {
    "details": ("modules/packing_details/tests", (
        "test_web.py", "test_settings_screen.py", "test_master_browse.py", "test_slip_history.py",
        "test_qa_mark.py", "test_shared_settings.py", "test_distribution.py", "test_background.py")),
    "material": ("modules/packing_material_calculation/tests", (
        "test_web_flow.py", "test_web_master.py", "test_web_settings.py",
        "test_checklist_and_order.py", "test_screen.py", "test_staff.py", "test_stale.py",
        "test_import_duplicates.py", "test_distribution.py", "test_idle_exit.py")),
    "pena": ("modules/packing_pena_label/tests", (
        "test_http.py", "test_screen_guard.py", "test_label_align.py", "test_master_admin.py",
        "test_distribution.py")),
}


def _run(key: str) -> subprocess.CompletedProcess:
    folder, files = WEB_TESTS[key]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    # 根の試験の隔離(tests/_env.py)を持ち込まない。機能の試験は自分で隔離する
    for name in list(env):
        if name.startswith(("PACKING_DETAILS_", "COIL_TOOL_", "PACKING_PENA_", "PPL_",
                            "COIL_PACKING_TOOLS_")) or name in ("XDG_DATA_HOME", "XDG_STATE_HOME"):
            env.pop(name)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           "-p", "tools.pytest_mounted", *[f"{folder}/{f}" for f in files]]
    # 子の出力は UTF-8 で読む(Windows の既定 cp1252/cp932 では日本語で読み手が落ちる)
    return subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=900)


class MountedWebTestsTest(unittest.TestCase):
    def _check(self, key: str, at_least: int):
        res = _run(key)
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, 0, out[-4000:])
        sent = re.search(r"入口付きで送った要求: .*\b%s=(\d+)" % key, out)
        self.assertIsNotNone(sent, out[-2000:])
        self.assertGreater(int(sent.group(1)), 0, "載せた形で流れていない")
        passed = int(re.search(r"(\d+) passed", out).group(1))
        self.assertGreaterEqual(passed, at_least, out[-2000:])

    def test_details(self):
        self._check("details", 300)

    def test_material(self):
        self._check("material", 270)

    def test_pena(self):
        self._check("pena", 150)


class DifferentByDesignTest(unittest.TestCase):
    """載せた形では答えが違うのが正しい試験の、統合版での形(`DIFFERENT_BY_DESIGN`)。"""

    def test_material_badge_goes_to_its_own_settings(self):
        client = _make_app(self).test_client()
        for path in ("/calc", "/checklist", "/order", "/settings"):
            page = client.get(f"/material{path}?t={TOKEN}").get_data(as_text=True)
            self.assertIn(f'class="ver" href="/material/settings?t={TOKEN}#about"', page, path)

    def test_every_exception_names_where_it_is_checked(self):
        from tools import pytest_mounted
        for node, why in pytest_mounted.DIFFERENT_BY_DESIGN.items():
            self.assertTrue((ROOT / node.split("::")[0]).exists(), node)
            self.assertTrue(why.strip(), node)


if __name__ == "__main__":
    unittest.main()
