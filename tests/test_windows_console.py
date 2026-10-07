"""Windows のコマンド(tasklist・wmic・PowerShell)の出力を読むところ(統合 1.2.1)

【現場で起きたこと】統合 1.2.0 のデスクトップ版が、日本語の Windows で**起動できなかった**。

    Exception in thread Thread-1 (_readerthread):
    UnicodeDecodeError: 'utf-8' codec can't decode byte 0x8f in position 0
    起動に失敗: 起動中に思わぬエラー: argument of type 'NoneType' is not iterable

起動時に、前に動いていたもの(統合前の単体版・前回のブラウザ版)の pid がまだ生きているかを
`tasklist` で見る。生きていなければ tasklist は日本語で「情報: 指定された条件に一致するタスクは
実行されていません。」を cp932 で出す。外枠が Python を UTF-8 モード(`-X utf8`)で起こして
いたので、それを UTF-8 として読んで落ち、結果が None のまま使われて起動ごと止まった。
英語の Windows(GitHub Actions)では出力が英語なので見つからなかった。

ここでは、日本語の Windows の出力(cp932)を、UTF-8 モードでもそうでなくても読めることを確かめる。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import launch_guard

ROOT = Path(__file__).resolve().parent.parent

#: 日本語の Windows の tasklist が、該当が無いときに出す知らせ(cp932)
NO_TASK = "情報: 指定された条件に一致するタスクは実行されていません。\r\n".encode("cp932")


def completed(stdout: bytes, code: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=b"")


class TasklistTest(unittest.TestCase):
    def test_japanese_no_task_message_means_not_alive(self):
        with mock.patch.object(launch_guard.subprocess, "run", return_value=completed(NO_TASK)) as run:
            self.assertFalse(launch_guard._is_alive_windows(12444))
        kwargs = run.call_args.kwargs
        self.assertNotIn("text", kwargs, "文字にしない(バイトのまま見る)")
        self.assertNotIn("encoding", kwargs)

    def test_running_process_is_found_in_bytes(self):
        line = '"python.exe","12444","Console","1","45,000 K"\r\n'.encode("cp932")
        with mock.patch.object(launch_guard.subprocess, "run", return_value=completed(line)):
            self.assertTrue(launch_guard._is_alive_windows(12444))
            self.assertFalse(launch_guard._is_alive_windows(1244), "別の pid と取り違えない")

    def test_nothing_or_failure_does_not_crash(self):
        with mock.patch.object(launch_guard.subprocess, "run", return_value=completed(None)):
            self.assertFalse(launch_guard._is_alive_windows(1))
        with mock.patch.object(launch_guard.subprocess, "run", side_effect=OSError("tasklist なし")):
            self.assertTrue(launch_guard._is_alive_windows(1), "分からないときは生きている側")

    def test_works_in_utf8_mode_too(self):
        """PC 全体で PYTHONUTF8=1 が入っていても落ちない(-X utf8 で別の Python を起こして確かめる)。"""
        code = (
            "import sys, subprocess; sys.path.insert(0, %r)\n"
            "from unittest import mock\n"
            "import launch_guard as L\n"
            "raw = %r\n"
            "res = subprocess.CompletedProcess([], 0, stdout=raw, stderr=b'')\n"
            "with mock.patch.object(L.subprocess, 'run', return_value=res):\n"
            "    print(L._is_alive_windows(12444), L.console_text(raw) != '')\n" % (str(ROOT), NO_TASK))
        out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True,
                             timeout=60, cwd=str(ROOT), encoding="utf-8", errors="replace")
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        self.assertEqual(out.stdout.strip(), "False True")


@unittest.skipUnless(sys.platform != "win32", "偽の tasklist を PATH に置ける OS で(Windows は System32 が先)")
class RealProcessTest(unittest.TestCase):
    """現場で起きたことを本物の子プロセスで再現する: 日本語の「情報: …」を出す tasklist を、
    UTF-8 モード(`-X utf8`。統合 1.2.0 の外枠が付けていた)の Python から呼ぶ。"""

    def test_japanese_tasklist_under_utf8_mode(self):
        import os
        import stat
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "tasklist"
            fake.write_bytes(b"#!/bin/sh\nprintf '" + b"".join(b"\\%03o" % c for c in NO_TASK) + b"'\n")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ.get("PATH", ""))
            code = ("import sys; sys.path.insert(0, %r); import launch_guard as L; "
                    "print('alive', L._is_alive_windows(12444))" % str(ROOT))
            out = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True,
                                 timeout=60, env=env, encoding="utf-8", errors="replace")
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        self.assertEqual(out.stdout.strip(), "alive False")
        self.assertNotIn("UnicodeDecodeError", out.stderr)


class ConsoleTextTest(unittest.TestCase):
    def test_never_raises(self):
        for raw in (b"", None, NO_TASK, b"\xff\xfe\x00", bytes(range(256))):
            with self.subTest(raw=raw[:8] if raw else raw):
                self.assertIsInstance(launch_guard.console_text(raw), str)

    def test_utf16_from_wmic(self):
        raw = "\r\nCommandLine=python.exe start_app.py\r\n".encode("utf-16-le")
        self.assertIn("CommandLine=python.exe start_app.py", launch_guard.console_text(raw))

    @unittest.skipUnless(sys.platform == "win32", "端末のコードページは Windows だけ")
    def test_japanese_windows_text_is_read(self):  # pragma: no cover - Windows だけ
        self.assertIn("情報", launch_guard.console_text(NO_TASK))


class CommandLineTest(unittest.TestCase):
    def test_wmic_in_japanese_windows(self):
        raw = "\r\r\nCommandLine=C:\\アプリ\\python.exe start_app.py\r\r\n".encode("cp932")
        with mock.patch.object(launch_guard.subprocess, "run", return_value=completed(raw)):
            got = launch_guard._command_line_windows(4242)
        self.assertTrue(got.endswith("start_app.py"), got)

    def test_falls_back_to_powershell_when_wmic_is_gone(self):
        """Windows 11 の新しい版には wmic が無いことがある。"""
        calls = []

        def run(args, **kwargs):
            calls.append(args[0])
            if args[0] == "wmic":
                raise FileNotFoundError("wmic")
            return completed("CommandLine=pythonw.exe start_app.py\r\n".encode("cp932"))

        with mock.patch.object(launch_guard.subprocess, "run", side_effect=run):
            got = launch_guard._command_line_windows(4242)
        self.assertEqual(calls, ["wmic", "powershell"])
        self.assertEqual(got, "pythonw.exe start_app.py")

    def test_nothing_found_is_empty(self):
        with mock.patch.object(launch_guard.subprocess, "run", side_effect=OSError("なし")):
            self.assertEqual(launch_guard._command_line_windows(4242), "")


class LegacyCheckDoesNotBlockStartupTest(unittest.TestCase):
    def test_unexpected_error_while_checking_does_not_stop_startup(self):
        """単体版が動いているかの見分けで思わぬエラーが出ても、起動を止めない。"""
        locks = {app_id: Path("/nonexistent/lock.json") for _, app_id, _ in launch_guard.LEGACY_APPS}
        with mock.patch.object(launch_guard, "_legacy_lock_files", return_value=locks), \
                mock.patch.object(launch_guard, "_answers_as", side_effect=TypeError("思わぬ")), \
                self.assertLogs(launch_guard.log, "WARNING") as logs:
            self.assertEqual(launch_guard.find_legacy_instances(), [])
        self.assertTrue(any("見分けられませんでした" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
