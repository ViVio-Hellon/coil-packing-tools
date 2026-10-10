"""配った先でショートカットを作る scripts\\make_shortcuts.vbs(統合 1.2.6)

配布フォルダを配ったあと、その PC でダブルクリックすると、ツールのフォルダ(scripts の1つ上)に
「コイル梱包ツール(ブラウザ版).lnk」(→ Start.vbs)と「コイル梱包ツール(デスクトップ版).lnk」
(→ コイル梱包ツール.exe)を作る。python-web-tools の scripts\\make_shortcuts.vbs と同じ作り。

Windows の WSH が要るので、ここでは動かさずに中身を確かめる:
- CP932 と CRLF(WSH はシステムの文字コードで読む。ほかの起動ファイルと同じ決まり)
- ショートカットの名前は文字の番号から組み立てる(日本語でない Windows で化けない)。
  その番号が正しい名前になる。exe の名前は配布フォルダに入れる名前と同じ
- 指す先は scripts の1つ上(押したときのフォルダ)。Start.vbs はそこにある
- 配布フォルダに入る(scripts は配る・除外の名前に当たらない)
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from modules.packing_pena_label.app.services import dist_folder

ROOT = Path(__file__).resolve().parent.parent
VBS = ROOT / "scripts" / "make_shortcuts.vbs"


def _codes(text: str, name: str) -> str:
    """`NAME = U("30B3 ...")` の番号を文字列に戻す(VBS の U() と同じ)。"""
    m = re.search(r'^%s = U\("([0-9A-F ]+)"\)' % name, text, re.M)
    assert m, name
    return "".join(chr(int(c, 16)) for c in m.group(1).split())


class MakeShortcutsTest(unittest.TestCase):
    def setUp(self):
        self.raw = VBS.read_bytes()
        self.text = self.raw.decode("cp932")

    def test_cp932_and_crlf(self):
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""), "LF だけの行がある")
        self.assertIn("Option Explicit", self.text)

    def test_names_are_built_from_code_points(self):
        self.assertEqual(_codes(self.text, "APP_NAME"), "コイル梱包ツール")
        self.assertEqual(_codes(self.text, "BROWSER"), "(ブラウザ版)")
        self.assertEqual(_codes(self.text, "DESKTOP"), "(デスクトップ版)")
        self.assertIn('EXE_NAME = APP_NAME & ".exe"', self.text)
        self.assertEqual(_codes(self.text, "APP_NAME") + ".exe", dist_folder.EXE_NAME,
                         "配布フォルダに入れる exe の名前と同じ")
        # 名前の組み立てに日本語を直に書かない(コメントは化けても困らない)
        for line in self.text.splitlines():
            code = line.split("'", 1)[0]
            if "MakeLink APP_NAME" in code or code.strip().startswith(("APP_NAME =", "EXE_NAME =")):
                self.assertTrue(all(ord(c) < 128 for c in code), line)

    def test_targets_the_folder_above_scripts(self):
        self.assertIn("GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))", self.text)
        self.assertIn('MakeLink APP_NAME & BROWSER & ".lnk", "Start.vbs", False', self.text)
        self.assertIn('MakeLink APP_NAME & DESKTOP & ".lnk", EXE_NAME, True', self.text)
        self.assertTrue((ROOT / "Start.vbs").is_file())
        # exe の無いフォルダでは作らずに知らせる・アイコンは exe のもの
        self.assertIn("If Not fso.FileExists(target) Then", self.text)
        self.assertIn('lnk.IconLocation = target & ",0"', self.text)
        self.assertIn("lnk.WorkingDirectory = here", self.text)

    def test_goes_into_the_distribution(self):
        self.assertIn("scripts", dist_folder.INCLUDE)
        self.assertFalse(dist_folder._excluded(VBS.name))
        self.assertIn("make_shortcuts.vbs", dist_folder._memo([]))


if __name__ == "__main__":
    unittest.main()
