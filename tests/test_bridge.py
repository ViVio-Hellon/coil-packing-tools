"""デスクトップ版の入口(`bridge.py`)── ポートで待ち受けずに画面を返す(統合 1.1.0)

外枠(Rust/Tauri、`src-tauri/`)は `bridge.py` を子として起動し、標準入出力で要求を渡す
(python-web-tools VER4.0.0 の作りを移した)。ここでは外枠の代わりをして、次を確かめる:

- やりとりの形(見出し1行 + 本文)が往復で崩れない(日本語・バイナリ・大きい本文)
- **ポートで待ち受けない**(`bind`・`listen` をしたら落ちるようにして起動する)。
  統合前の単体版を探す問い合わせ(こちらから繋ぎに行くだけ)は今までどおり
- 待機画面 → 準備完了 → 統合画面・3機能の画面・ログ・API が返る
- 静的ファイルの置き場所(外枠が直接返す)を知らせる
- トークン無し・知らない宛先は今までどおり断る
- 「終了」で `quit` を知らせ、標準入力を閉じればプロセスが終わる
- 起動できないときは `fatal` で理由を知らせる
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import bridge
from common import security

ROOT = Path(__file__).resolve().parent.parent

#: 待ち受けを禁じる(こちらから繋ぎに行くのは許す)
NO_LISTEN = (
    "import socket\n"
    "def _no(*a, **k):\n"
    "    raise RuntimeError('ポートで待ち受けようとしました')\n"
    "socket.socket.bind = _no\n"
    "socket.socket.listen = _no\n"
)


def isolated_env(root: Path, **extra) -> dict:
    """子プロセスの置き場所を、使い捨てのフォルダへ(本物の領域も試験の領域も汚さない)。"""
    env = dict(os.environ)
    env.update(
        COIL_PACKING_TOOLS_LOCAL_DIR=str(root / "local"),
        COIL_PACKING_TOOLS_LOG_DIR=str(root / "local" / "logs"),
        COIL_PACKING_TOOLS_SETTINGS_PATH=str(root / "local" / "settings.json"),
        COIL_PACKING_TOOLS_DISTRIBUTION_DIR=str(root / "dist" / "common"),
        XDG_DATA_HOME=str(root / "xdg-data"), XDG_STATE_HOME=str(root / "xdg-state"),
        PACKING_DETAILS_DB_PATH=str(root / "details" / "packing_details.db"),
        PACKING_DETAILS_CONFIG_PATH=str(root / "details" / "user_config.json"),
        PACKING_DETAILS_SHARED_DIR=str(root / "details" / "share"),
        PACKING_DETAILS_DISTRIBUTION_DIR=str(root / "details" / "dist"),
        COIL_TOOL_DB_PATH=str(root / "material" / "coil_tool.db"),
        COIL_TOOL_CONFIG_PATH=str(root / "material" / "user_config.json"),
        COIL_TOOL_DISTRIBUTION_DIR=str(root / "material" / "dist"),
        COIL_TOOL_MASTER_DB_DIR=str(root / "material" / "master"),
        COIL_TOOL_LOT_DB_DIR=str(root / "material" / "lot"),
        PACKING_PENA_DISTRIBUTION_DIR=str(root / "pena" / "dist"),
        PPL_PREFER_ACCESS="0",
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.update(extra)
    return env


class FrameTest(unittest.TestCase):
    def roundtrip(self, head: dict, body: bytes):
        out = io.BytesIO()
        bridge.FrameWriter(out).write(head, body)
        out.seek(0)
        return bridge.read_frame(out)

    def test_japanese_and_binary_bodies_survive(self):
        body = "梱包明細表\n".encode("utf-8") + bytes(range(256))
        head, got = self.roundtrip({"id": 3, "status": 200, "headers": [["X", "あ"]]}, body)
        self.assertEqual(got, body)
        self.assertEqual(head["len"], len(body))
        self.assertEqual(head["headers"], [["X", "あ"]])

    def test_large_body(self):
        body = os.urandom(3 * 1024 * 1024)
        self.assertEqual(self.roundtrip({"id": 1}, body)[1], body)

    def test_events_have_no_body(self):
        out = io.BytesIO()
        bridge.FrameWriter(out).event("quit")
        self.assertEqual(json.loads(out.getvalue()), {"event": "quit"})

    def test_end_is_none_and_a_cut_frame_is_an_error(self):
        self.assertIsNone(bridge.read_frame(io.BytesIO(b"")))
        with self.assertRaises(EOFError):
            bridge.read_frame(io.BytesIO(b'{"id": 1, "len": 10}\nabc'))
        with self.assertRaises(ValueError):
            bridge.read_frame(io.BytesIO(b'{"id": 1, "len": -1}\n'))


class CallWsgiTest(unittest.TestCase):
    def test_method_path_query_body_and_headers_arrive(self):
        import flask
        app = flask.Flask("試し")

        @app.post("/api/echo")
        def echo():                                      # noqa: ANN202
            return flask.jsonify({"q": flask.request.args.get("a"),
                                  "body": flask.request.get_json(),
                                  "host": flask.request.host,
                                  "token": flask.request.headers.get("X-Tool-Token")})

        status, headers, data = bridge.call_wsgi(app, {
            "method": "POST", "path": "/api/echo", "query": "a=%E3%81%82",
            "headers": [["Content-Type", "application/json"], ["X-Tool-Token", "t"],
                        ["Host", "app.localhost"]]},
            json.dumps({"重量": 248}).encode("utf-8"))
        self.assertEqual(status, 200)
        self.assertIn(["Content-Type", "application/json"], headers)
        self.assertEqual(json.loads(data), {"q": "あ", "body": {"重量": 248},
                                            "host": "app.localhost", "token": "t"})


class BridgeProcessTest(unittest.TestCase):
    """本物の子プロセスとして起動し、外枠の代わりに要求を投げる。"""

    TOKEN = "desk-test-token"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="cpt-bridge-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "nolisten").mkdir()
        (root / "nolisten" / "sitecustomize.py").write_text(NO_LISTEN, encoding="utf-8")
        self.root = root
        env = isolated_env(root, COIL_PACKING_TOOLS_TOKEN=self.TOKEN,
                           PYTHONPATH=str(root / "nolisten"))
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(ROOT / "bridge.py")], env=env, cwd=str(ROOT),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self._kill)
        self.err: list = []
        threading.Thread(target=lambda: self.err.extend(
            self.proc.stderr.read().decode("utf-8", "replace").splitlines()),
            daemon=True).start()
        self.events: list = []
        self.replies: dict = {}
        self.lock = threading.Lock()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass

    def _read(self):
        while True:
            try:
                frame = bridge.read_frame(self.proc.stdout)
            except (ValueError, EOFError, OSError):
                return
            if frame is None:
                return
            head, body = frame
            if "event" in head:
                self.events.append(head)
            else:
                self.replies[head["id"]] = (head, body)

    def request(self, method, path, *, body=b"", headers=None, token=True,
                host="app.localhost", timeout=60.0):
        with self.lock:
            self.next_id += 1
            i = self.next_id
            query = ""
            if "?" in path:
                path, query = path.split("?", 1)
            h = [["Host", host]] + ([["X-Tool-Token", self.TOKEN]] if token else [])
            h += headers or []
            head = {"id": i, "method": method, "path": path, "query": query,
                    "headers": h, "len": len(body)}
            self.proc.stdin.write(json.dumps(head).encode("utf-8") + b"\n" + body)
            self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while i not in self.replies:
            if time.monotonic() > deadline:
                self.fail(f"{path} の応答が来ない。stderr: {self.err[-15:]}")
            time.sleep(0.01)
        return self.replies.pop(i)

    def wait_event(self, name, timeout=60.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for e in self.events:
                if e.get("event") == name:
                    return e
            if self.proc.poll() is not None and not any(e.get("event") == name for e in self.events):
                break
            time.sleep(0.02)
        self.fail(f"知らせ {name} が来ない: {self.events} / stderr: {self.err[-15:]}")

    def wait_ready(self):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            head, body = self.request("GET", "/api/health")
            if head["status"] == 200 and json.loads(body).get("ready"):
                return json.loads(body)
            time.sleep(0.1)
        self.fail(f"準備完了にならない。stderr: {self.err[-15:]}")

    def test_serves_every_screen_without_a_port_and_quits(self):
        self.wait_event("started")
        health = self.wait_ready()
        self.assertEqual(health["port"], 0, "ポートは無い")

        # 静的ファイルの置き場所(外枠が直接返す)。3機能と統合画面の分がある
        statics = {prefix: folder for prefix, folder in self.wait_event("static")["static"]}
        for prefix in ("/static/", "/details/static/", "/material/static/", "/pena/static/"):
            with self.subTest(prefix=prefix):
                self.assertTrue(Path(statics[prefix]).is_dir(), statics)
        self.assertTrue((Path(statics["/static/"]) / "js" / "desktop.js").is_file())

        for path in ("/?go=1", "/log", "/details/meisai", "/pena/", "/material/calc",
                     "/static/css/shell.css"):
            with self.subTest(path=path):
                head, body = self.request("GET", path + ("&" if "?" in path else "?") + f"t={self.TOKEN}")
                self.assertEqual(head["status"], 200, body[:300])
        # 画面には窓まわりを外枠に頼む desktop.js が入る(いちばん先に)
        _, page = self.request("GET", f"/pena/?t={self.TOKEN}")
        text = page.decode("utf-8")
        self.assertLess(text.index("js/desktop.js"), text.index("app.js"))

        # 同時に来ても取り違えない(3機能が心拍・進み具合・操作を同時に出す)
        results: list = []
        threads = [threading.Thread(target=lambda: results.append(
            self.request("GET", "/api/health")[0]["status"])) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, [200] * 12)

        # 守りは今までどおり(トークン・宛先)
        self.assertEqual(self.request("GET", "/api/log/status", token=False)[0]["status"], 403)
        self.assertEqual(self.request("GET", "/api/health", host="evil.example")[0]["status"], 400)

        head, body = self.request("POST", "/api/shutdown", body=b"{}",
                                  headers=[["Content-Type", "application/json"]])
        self.assertEqual(head["status"], 200, body)
        self.wait_event("quit", timeout=15)
        self.proc.stdin.close()
        self.assertEqual(self.proc.wait(timeout=30), 0, self.err[-15:])
        self.assertFalse([l for l in self.err if "待ち受けようとしました" in l], self.err[-20:])

    def test_ends_when_the_window_closes_first(self):
        self.wait_event("started")
        self.wait_ready()
        self.proc.stdin.close()
        self.assertEqual(self.proc.wait(timeout=30), 0, self.err[-15:])


class FatalTest(unittest.TestCase):
    def test_tells_why_it_cannot_start(self):
        with tempfile.TemporaryDirectory(prefix="cpt-bridge-") as tmp:
            blocker = Path(tmp) / "ファイル"
            blocker.write_text("x", encoding="utf-8")
            env = isolated_env(Path(tmp), COIL_PACKING_TOOLS_LOCAL_DIR=str(blocker),
                               COIL_PACKING_TOOLS_LOG_DIR=str(Path(tmp) / "logs"))
            done = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "bridge.py")],
                                  env=env, cwd=str(ROOT), input=b"", capture_output=True,
                                  timeout=120)
        self.assertEqual(done.returncode, 1, done.stderr[-800:])
        event = json.loads(done.stdout.decode("utf-8").splitlines()[0])
        self.assertEqual(event["event"], "fatal")
        self.assertTrue(event["message"])


class DesktopShellTest(unittest.TestCase):
    """外枠(Rust)と Python の決まりが食い違わない。"""

    def test_versions_match_the_app(self):
        """exe のプロパティに出る版と、画面の帯に出る版を食い違わせない。"""
        app = json.loads((ROOT / "config" / "app.json").read_text(encoding="utf-8"))["version"]
        conf = json.loads((ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        cargo = re.search(r'^version = "([^"]+)"', (ROOT / "src-tauri" / "Cargo.toml")
                          .read_text(encoding="utf-8"), re.M).group(1)
        self.assertEqual((conf["version"], cargo), (app, app))

    def test_the_window_address_is_the_same_name(self):
        main_rs = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertIn('const SCHEME: &str = "app";', main_rs)
        self.assertIn("app.localhost", security.BRIDGE_HOSTS)

    def test_env_names_match(self):
        """外枠が渡す環境変数の名前を、Python 側が読む名前とそろえる。"""
        rust = ((ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
                + (ROOT / "src-tauri" / "src" / "bridge.rs").read_text(encoding="utf-8"))
        py = (ROOT / "bridge.py").read_text(encoding="utf-8")
        self.assertIn('"COIL_PACKING_TOOLS_TOKEN"', rust)
        self.assertIn('"COIL_PACKING_TOOLS_TOKEN"', py)
        self.assertIn('"COIL_PACKING_TOOLS_LOCAL_DIR"', rust)
        from common import app_config
        src = Path(app_config.__file__).read_text(encoding="utf-8")
        self.assertIn('"COIL_PACKING_TOOLS_LOCAL_DIR"', src)
        self.assertIn("desktop.lock", rust)


if __name__ == "__main__":                        # pragma: no cover
    unittest.main()
