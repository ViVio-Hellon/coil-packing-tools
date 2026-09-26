"""待ち受けソケット ── 同じポートを2重に掴めない(ペナラベルの移植元から)

Windows の `SO_REUSEADDR` は待ち受け中のポートを奪えるので、そこでは
`SO_EXCLUSIVEADDRUSE` を付けたソケットを先に作って waitress に渡す
(`server.bind_exclusive`)。ここ(Linux)では bind そのものと、2つ目が
断られることを確かめる。
"""
from __future__ import annotations

import os
import socket
import unittest

import server


class BindTest(unittest.TestCase):
    def test_待ち受けを作って2つ目は断られる(self):
        sock = server.bind_exclusive("127.0.0.1", 0, exclusive=False)
        self.addCleanup(sock.close)
        port = sock.getsockname()[1]
        with self.assertRaises(OSError):
            server.bind_exclusive("127.0.0.1", port, exclusive=False)

    def test_排他の指定が効かない環境でも待ち受けは作れる(self):
        """POSIX には `SO_EXCLUSIVEADDRUSE` が無い。付けられなくても bind は続ける。"""
        sock = server.bind_exclusive("127.0.0.1", 0, exclusive=True)
        self.addCleanup(sock.close)
        self.assertGreater(sock.getsockname()[1], 0)

    def test_失敗したらソケットを残さない(self):
        held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(held.close)
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        with self.assertRaises(OSError):
            server.bind_exclusive("127.0.0.1", held.getsockname()[1], exclusive=False)

    @unittest.skipUnless(os.name == "nt", "Windows だけ")
    def test_Windowsでは排他を付ける(self):        # pragma: no cover
        sock = server.bind_exclusive("127.0.0.1", 0)
        self.addCleanup(sock.close)
        self.assertEqual(sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
