# -*- coding: utf-8 -*-
"""Access(梱包資材マスタ.accdb)を読む経路の試験。

ラインPCでは Python に追加のライブラリを入れられないので、Access は
cscript.exe → VBScript → ADODB → Microsoft ACE OLE DB で読む
(`app/repositories/access_bridge.py` と `vbs/query_accdb.vbs`)。

【統合版】移植元では、この試験は起動まわりの試験(`test_launcher_files.py`・
`test_startup_conditions.py`)の中にあった。統合版で起動まわりを統合アプリのものに
置き換えたとき、ファイルごと外したため、**今も使っている Access の経路の試験まで
消えていた**(移植漏れの点検で見つかった)。3つのクラスをそのまま移した。
"""

import io
import json
import os
import shutil
import stat
import tempfile
import unittest

from modules.packing_pena_label.app.repositories.access_bridge import (  # noqa: E402
    AccessBridge, AccessError)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fake_cscript(path: str, payload: str, rc: int = 0) -> str:
    """cscript.exe の代わりに使う実行ファイル（テスト用）。"""
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/bin/sh\ncat <<'JSON'\n%s\nJSON\nexit %d\n" % (payload, rc))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class AccessBitnessFallbackTest(unittest.TestCase):
    """ACE の 32bit / 64bit 不一致で読めない件。

    ACE は 32bit と 64bit が別物で、``cscript.exe`` のビット数が
    合っていないと ``Provider が見つかりません`` になる。
    Office が 32bit の端末は多く、その場合 64bit の cscript（既定）
    からは読めない。駄目なら反対側を試す。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "梱包資材マスタ.accdb")
        with open(self.db, "wb") as f:
            f.write(b"dummy")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ok_payload(self):
        return json.dumps({"ok": True, "fields": ["梱包資材名", "単位質量"],
                           "records": [["テスラピン", "0.5"]]},
                          ensure_ascii=False)

    def _ng_payload(self, msg):
        return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)

    def test_first_candidate_is_used_when_it_works(self):
        exe = _fake_cscript(os.path.join(self.tmp, "cs_ok"), self._ok_payload())
        br = AccessBridge(cscript=exe)
        fields, records = br.fetch(self.db, "資材重量")
        self.assertEqual(fields, ["梱包資材名", "単位質量"])
        self.assertEqual(records, [["テスラピン", "0.5"]])

    def test_falls_back_to_the_other_bitness(self):
        bad = _fake_cscript(os.path.join(self.tmp, "cs_ng"),
                            self._ng_payload("connect failed: プロバイダーなし"))
        good = _fake_cscript(os.path.join(self.tmp, "cs_ok"), self._ok_payload())
        br = AccessBridge(cscript=bad)
        br.cscript_candidates = lambda: [bad, good]
        fields, _ = br.fetch(self.db, "資材重量")
        self.assertEqual(fields, ["梱包資材名", "単位質量"])

    def test_reports_every_attempt_when_all_fail(self):
        a = _fake_cscript(os.path.join(self.tmp, "a"),
                          self._ng_payload("ACE 12.0 なし"))
        b = _fake_cscript(os.path.join(self.tmp, "b"),
                          self._ng_payload("ACE 16.0 なし"))
        br = AccessBridge(cscript=a)
        br.cscript_candidates = lambda: [a, b]
        br._diagnose = lambda args: ""
        with self.assertRaises(AccessError) as cm:
            br.fetch(self.db, "資材重量")
        msg = str(cm.exception)
        self.assertIn("ACE 12.0 なし", msg)
        self.assertIn("ACE 16.0 なし", msg)

    def test_missing_candidate_is_skipped_not_fatal(self):
        good = _fake_cscript(os.path.join(self.tmp, "cs_ok"), self._ok_payload())
        br = AccessBridge(cscript="/存在しない/cscript.exe")
        br.cscript_candidates = lambda: ["/存在しない/cscript.exe", good]
        fields, _ = br.fetch(self.db, "資材重量")
        self.assertEqual(fields, ["梱包資材名", "単位質量"])

    def test_missing_db_is_named(self):
        br = AccessBridge(cscript="/bin/true")
        with self.assertRaises(AccessError) as cm:
            br.fetch(os.path.join(self.tmp, "ない.accdb"), "資材重量")
        self.assertIn("見つかりません", str(cm.exception))


class AceProviderTest(unittest.TestCase):
    """VBScript 側が 12.0 と 16.0 の両方を試すこと。"""

    def test_tries_both_providers(self):
        path = os.path.join(ROOT, "app", "repositories", "vbs",
                            "query_accdb.vbs")
        with open(path, "rb") as f:
            text = f.read().decode("ascii")
        self.assertIn("Microsoft.ACE.OLEDB.12.0", text)
        self.assertIn("Microsoft.ACE.OLEDB.16.0", text)
        self.assertLess(text.index("12.0"), text.index("16.0"),
                        "VBA と同じ 12.0 を先に試すこと")


class AccessBridgeVbsTest(unittest.TestCase):
    """Access 読み取りの VBScript が、どの端末でも読めること。

    ``cscript.exe`` は .vbs を UTF-8 ではなく端末の ANSI コードページで読む。
    日本語コメントを UTF-8 で保存すると解釈に失敗し、しかも ``//B`` が
    エラーを握り潰すため

        Access 読取の応答が空です: rc=1 stderr=

    としか出ない。現場ではこれで資材マスタが CSV へ落ちていた。
    **ASCII だけ**にしておけばコードページに左右されない。
    """

    PATH = os.path.join(ROOT, "app", "repositories", "vbs", "query_accdb.vbs")

    def setUp(self):
        with open(self.PATH, "rb") as f:
            self.data = f.read()

    def test_is_pure_ascii(self):
        try:
            self.data.decode("ascii")
        except UnicodeDecodeError as exc:
            self.fail("query_accdb.vbs に非 ASCII がある"
                      "（コードページで読み違える）: %s" % exc)

    def test_crlf_only(self):
        lone = self.data.count(b"\n") - self.data.count(b"\r\n")
        self.assertEqual(lone, 0, "LF 単独の行がある")

    def test_no_bom(self):
        self.assertNotEqual(self.data[:3], b"\xef\xbb\xbf")

    def test_still_speaks_ace_and_json(self):
        text = self.data.decode("ascii")
        self.assertIn("Microsoft.ACE.OLEDB.12.0", text)
        self.assertIn("adOpenStatic", text)
        self.assertIn("adLockReadOnly", text)
        self.assertIn('"ok":true', text.replace('""', '"'))

    def test_failure_is_diagnosed(self):
        """//B が握り潰したエラーを拾い直す経路があること。"""
        with io.open(os.path.join(ROOT, "app", "repositories",
                                  "access_bridge.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_diagnose", src)
        self.assertIn("//T:10", src, "時間を区切らないとダイアログで止まる")
        self.assertIn("cp932", src, "stderr をコードページで読んでいない")


if __name__ == "__main__":
    unittest.main()
