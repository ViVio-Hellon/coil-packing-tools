"""起動の入口(start_app)が、移植元の3つの start_app から引き継ぐこと。

移植漏れの点検で、統合版の起動が統合アプリの分しか確かめていなかったと分かった。

- `__pycache__` をアプリのフォルダに作らない(ペナラベルの移植元の `redirect_pycache`)
- 3機能の手元の領域を作れて書けるか(3つとも起動の前に確かめていた)
- 3機能の設定ファイルの読み込み失敗・手元DBの置き場所を起動ログに残す
  (梱包明細・資材計算の移植元)
- 移植元でログの置き場所を変えていた環境変数は、統合版では効かないと言う
- 起動待機画面の段: まだ終わっていない機能の段を出す(最後に知らせた機能で上書きしない)
- 梱包明細の開発用の道具(`make_golden.py`)が、統合版の置き場所から起動できる
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import start_app

ROOT = Path(__file__).resolve().parent.parent


class RedirectPycacheTest(unittest.TestCase):
    def setUp(self):
        self._saved = (sys.pycache_prefix, sys.dont_write_bytecode)
        self.addCleanup(self._restore)

    def _restore(self):
        sys.pycache_prefix, sys.dont_write_bytecode = self._saved

    def test_points_to_the_local_area(self):
        with tempfile.TemporaryDirectory() as d:
            sys.pycache_prefix = None
            sys.dont_write_bytecode = True
            start_app.redirect_pycache(Path(d))
            self.assertEqual(sys.pycache_prefix, str(Path(d) / "pycache"))
            self.assertTrue((Path(d) / "pycache").is_dir())
            # 書かないと決めてあれば(-B / PYTHONDONTWRITEBYTECODE)書かないまま
            self.assertEqual(sys.dont_write_bytecode, bool(sys.flags.dont_write_bytecode))

    def test_keeps_a_prefix_chosen_by_the_user(self):
        sys.pycache_prefix = "/somewhere/else"
        with tempfile.TemporaryDirectory() as d:
            start_app.redirect_pycache(Path(d))
            self.assertFalse((Path(d) / "pycache").exists())
        self.assertEqual(sys.pycache_prefix, "/somewhere/else")

    def test_real_start_writes_pycache_to_the_local_area(self):
        """本物の起動(`--check`)で、3機能の設定を読んだ `.pyc` がローカル領域に入る。"""
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items()
                   if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")}
            env["COIL_PACKING_TOOLS_LOCAL_DIR"] = str(Path(d) / "local")
            env["COIL_PACKING_TOOLS_LOG_DIR"] = str(Path(d) / "local" / "logs")
            env["PYTHONIOENCODING"] = "utf-8"
            res = subprocess.run([sys.executable, str(ROOT / "start_app.py"), "--check"],
                                 cwd=d, env=env, capture_output=True, timeout=120, encoding="utf-8", errors="replace")
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            prefix = Path(d) / "local" / "pycache"
            names = {p.name.split(".")[0] for p in prefix.rglob("*.pyc")}
            self.assertIn("app_config", names)
            # 機能の設定(modules\...\app_config.py)の分も向け先に入る
            self.assertTrue(any("packing_details" in str(p) for p in prefix.rglob("*.pyc")))


class ModuleAreasTest(unittest.TestCase):
    def test_three_areas_are_made_and_writable(self):
        start_app.check_module_areas()
        labels = [label for label, _ in start_app._module_areas()]
        self.assertEqual(labels, ["梱包明細", "ペナラベル", "資材計算"])
        for _, make in start_app._module_areas():
            self.assertTrue((Path(make()) / "runtime").is_dir())

    def test_unwritable_area_stops_the_start_with_its_name(self):
        def broken():
            raise PermissionError("拒否されました")
        with mock.patch.object(start_app, "_module_areas",
                               return_value=[("資材計算", broken)]):
            with self.assertRaises(start_app.StartupError) as ctx:
                start_app.check_module_areas()
        self.assertIn("資材計算", str(ctx.exception))
        self.assertIn("権限", ctx.exception.hint)


class ModuleEnvironmentLogTest(unittest.TestCase):
    def _log(self, **env):
        with mock.patch.dict(os.environ, env):
            with self.assertLogs("coil_packing_tools", level="INFO") as cm:
                start_app.log_module_environment()
        return "\n".join(cm.output)

    def test_places_of_the_three_features_are_logged(self):
        text = self._log()
        for label in ("梱包明細: 手元の領域", "資材計算: 手元の領域", "ペナラベル: 手元の領域"):
            self.assertIn(label, text)
        from modules.packing_details.meisai import config as d_paths
        self.assertIn(str(d_paths.DB_PATH), text)

    def test_config_error_and_db_on_a_share_are_warned(self):
        from modules.packing_details.meisai import app_config as d_cfg, config as d_paths
        from modules.packing_material_calculation.coil_tool import app_config as m_cfg
        with mock.patch.object(d_cfg, "load_error", return_value="config/app.json が読めません"), \
             mock.patch.object(m_cfg, "load_error", return_value="版の書き方が違います"), \
             mock.patch.object(d_paths, "db_path_problem", return_value="手元DBが共有フォルダにあります"):
            text = self._log()
        self.assertIn("WARNING:coil_packing_tools.launcher:梱包明細: config/app.json が読めません", text)
        self.assertIn("資材計算: 版の書き方が違います", text)
        self.assertIn("梱包明細: 手元DBが共有フォルダにあります", text)

    def test_legacy_log_dir_env_is_reported(self):
        text = self._log(PACKING_DETAILS_LOG_DIR=r"D:\logs")
        self.assertIn("PACKING_DETAILS_LOG_DIR は統合版では使いません", text)
        self.assertIn("COIL_PACKING_TOOLS_LOG_DIR", text)


class _Srv:
    """待機画面へ知らせたことを覚えておく代役。"""

    def __init__(self):
        self.stages = []
        self.errors = []
        self.ready = False

    def mark_stage(self, text, key="prepare"):
        self.stages.append((text, key))

    def mark_error(self, text):
        self.errors.append(text)

    def mark_ready(self, ready=True):
        self.ready = ready


def _module(label, *, background=None, fail=False):
    def initialize(report):
        report.stage(f"{label}: アプリを準備中")
        if fail:
            raise RuntimeError("こわれた")
        if background is None:
            return None
        report.stage(f"{label}: 取り込み中", "import")
        return background
    return types.SimpleNamespace(LABEL=label, initialize=initialize)


class StagesTest(unittest.TestCase):
    def test_a_later_feature_does_not_hide_an_import_still_running(self):
        srv = _Srv()
        details, material = threading.Event(), threading.Event()
        watch = start_app.run_initializers(srv, [
            _module("梱包明細", background=details), _module("ペナラベル"),
            _module("資材計算", background=material)], limit_sec=10)
        text, key = srv.stages[-1]
        self.assertIn("梱包明細: 取り込み中", text)
        self.assertIn("資材計算: 取り込み中", text)
        self.assertNotIn("ペナラベル", text, "終わった機能は出さない")
        self.assertEqual(key, "import", "取り込みの途中で「準備」へ戻らない")
        self.assertTrue(all(k == "import" for _, k in srv.stages[2:]), srv.stages)
        material.set()
        _wait(lambda: srv.stages[-1][0] == "梱包明細: 取り込み中")
        self.assertFalse(srv.ready)
        details.set()
        watch.join(timeout=5)
        self.assertTrue(srv.ready)

    def test_a_failed_feature_does_not_stop_the_others(self):
        srv = _Srv()
        start_app.run_initializers(srv, [_module("梱包明細", fail=True), _module("ペナラベル")])
        self.assertEqual(len(srv.errors), 1)
        self.assertIn("梱包明細: 初期化に失敗しました", srv.errors[0])
        self.assertTrue(srv.ready)

    def test_gives_up_waiting_after_the_limit(self):
        srv = _Srv()
        watch = start_app.run_initializers(
            srv, [_module("資材計算", background=threading.Event())], limit_sec=0.3)
        with self.assertLogs("coil_packing_tools", level="WARNING") as cm:
            watch.join(timeout=5)
        self.assertTrue(srv.ready)
        self.assertIn("資材計算 の取り込みが", "\n".join(cm.output))


def _wait(cond, timeout=5.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.05)
    raise AssertionError("待っても変わらない")


class MakeGoldenTest(unittest.TestCase):
    def test_starts_from_anywhere(self):
        """統合版の置き場所(modules\\packing_details\\scripts)から起動して、import で落ちない。"""
        script = ROOT / "modules" / "packing_details" / "scripts" / "make_golden.py"
        with tempfile.TemporaryDirectory() as d:
            res = subprocess.run([sys.executable, str(script), str(Path(d) / "無い")],
                                 cwd=d, capture_output=True, timeout=60, encoding="utf-8", errors="replace",
                                 env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                          PYTHONIOENCODING="utf-8"))
        self.assertNotIn("ModuleNotFoundError", res.stderr)
        self.assertIn("取り込み元が足りません", res.stdout + res.stderr)


class LogDirTest(unittest.TestCase):
    """ログは統合アプリの1か所。移植元の環境変数で表示だけ変わることがない。"""

    def test_feature_log_dirs_are_the_common_one(self):
        from common import logging_utils
        from modules.packing_details.meisai import config as d_paths
        from modules.packing_material_calculation.coil_tool import config as m_paths
        self.assertEqual(d_paths.LOG_DIR, logging_utils.log_dir())
        self.assertEqual(m_paths.LOG_DIR, logging_utils.log_dir())

    def test_legacy_env_does_not_move_the_shown_log_dir(self):
        code = ("import sys; sys.path.insert(0, %r); import tests._env; "
                "from common import logging_utils; "
                "from modules.packing_details.meisai import config as d; "
                "from modules.packing_material_calculation.coil_tool import config as m; "
                "print(d.LOG_DIR == logging_utils.log_dir() == m.LOG_DIR)" % str(ROOT))
        env = dict(os.environ, PACKING_DETAILS_LOG_DIR="/elsewhere/a", COIL_TOOL_LOG_DIR="/elsewhere/b",
                   PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        res = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                             capture_output=True, timeout=60, encoding="utf-8", errors="replace")
        self.assertEqual(res.stdout.strip(), "True", res.stderr)


if __name__ == "__main__":
    unittest.main()
