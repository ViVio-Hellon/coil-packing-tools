# -*- coding: utf-8 -*-
"""配布用フォルダを作る（python-web-tools の scripts/make_dist.py の移植）の検証。

配るものだけが入り、配ってはいけないもの（テスト・マスタの写し・ログ・
起動中の印・端末の上書き設定）が紛れないこと。配布設定は選んだときだけ入ること。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 統合版: 配るのは統合アプリの根(3つ上)
ROOT = Path(__file__).resolve().parent.parent.parent.parent
MODULE = "modules/packing_pena_label"
from modules.packing_pena_label.app.services import dist_folder as DF          # noqa: E402
from modules.packing_pena_label.app.services import distribution as D          # noqa: E402
from common import dist_settings                                                # noqa: E402

#: 統合版: 配布設定は 配布設定\<機能>\ に機能ごとに置く(common/dist_settings.py)
PENA = "packing_pena_label"


def fake_app(base: Path) -> Path:
    """本物のアプリを汚さないよう、写しを作ってそこから配布用フォルダを作る。"""
    src = base / "app_src"
    for name in DF.INCLUDE:
        p = ROOT / name
        if p.is_dir():
            shutil.copytree(str(p), str(src / name),
                            ignore=shutil.ignore_patterns("__pycache__"))
        elif p.exists():
            src.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(p), str(src / name))
    # 開発用・端末ごとのもの（配ってはいけない）
    (src / "tests").mkdir()
    (src / "tests" / "test_x.py").write_text("x", encoding="utf-8")
    (src / "tools").mkdir()
    (src / "tools" / "smoke_all.py").write_text("x", encoding="utf-8")
    (src / ".git").mkdir()
    (src / MODULE / "data" / "梱包資材マスタ.sqlite3").write_bytes(b"SQLite format 3\x00")
    (src / "config" / "local.json").write_text("{}", encoding="utf-8")
    (src / MODULE / "app" / "__pycache__").mkdir(exist_ok=True)
    (src / MODULE / "app" / "__pycache__" / "x.pyc").write_bytes(b"x")
    # 機能ごとの試験・手元DBの写し(統合版で足した除外)
    (src / MODULE / "tests").mkdir(exist_ok=True)
    (src / MODULE / "tests" / "test_y.py").write_text("y", encoding="utf-8")
    (src / "modules" / "packing_details" / "data").mkdir(parents=True, exist_ok=True)
    (src / "modules" / "packing_details" / "data" / "packing_details.db").write_bytes(b"SQLite format 3\x00")
    (src / "runtime").mkdir()
    (src / "runtime" / "python.exe").write_bytes(b"MZ")
    (src / "runtime" / "instance.lock").write_bytes(b"")
    (src / "runtime" / "instance.json").write_text("{}", encoding="utf-8")
    (src / "logs").mkdir()
    (src / "logs" / "app.log").write_text("x", encoding="utf-8")
    return src


class DistFolderTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.base), True)
        self.src = fake_app(self.base)
        self.out = self.base / "out" / "PackingPenaLabel_vX"

    def build(self, **kw):
        kw.setdefault("root", self.src)
        return DF.build(self.out, **kw)

    def test_only_what_is_distributed_goes_in(self):
        r = self.build()
        for name in DF.INCLUDE:
            self.assertTrue((self.out / name).exists(), name)
        self.assertTrue((self.out / "runtime" / "python.exe").exists(),
                        "同梱の Python は配る")
        for bad in ("tests", "tools", ".git", "config/local.json", "logs",
                    MODULE + "/data/梱包資材マスタ.sqlite3", "runtime/instance.lock",
                    "runtime/instance.json", MODULE + "/app/__pycache__",
                    MODULE + "/tests", "modules/packing_details/data/packing_details.db",
                    "modules/packing_details/tests"):
            self.assertFalse((self.out / bad).exists(), "%s が紛れた" % bad)
        self.assertGreater(r.files, 20)

    def test_shipped_masters_in_data_are_distributed(self):
        """このツールの data\\ は同梱のマスタ（端末ごとの中身ではない）。"""
        self.build()
        for name in ("label_stock.json", "size_master.json"):
            self.assertTrue((self.out / MODULE / "data" / name).exists(), name)

    def test_bat_files_are_copied_byte_for_byte(self):
        """start.bat / stop.bat は CP932 + CRLF。変換すると壊れる。"""
        self.build()
        for name in ("start.bat", "stop.bat", "Start.vbs"):
            self.assertEqual((self.out / name).read_bytes(),
                             (self.src / name).read_bytes(), name)

    def test_memo_is_written_for_notepad(self):
        self.build()
        raw = (self.out / DF.MEMO_NAME).read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "BOM が無い")
        text = raw.decode("utf-8-sig")
        self.assertIn("\r\n", text)
        self.assertIn("配った先ですること", text)
        self.assertIn("%LOCALAPPDATA%", text, "入れ替えで写すものが無いことを書く")

    def test_settings_go_in_only_when_asked(self):
        d = self.src / DF.SETTINGS / PENA
        d.mkdir(parents=True)
        (d / D.SETTINGS_NAME).write_text(json.dumps({
            "format": 1, "created_at": "2026-09-26 08:00:00", "created_on": "PC1",
            "settings": {"label_offset_x_mm": 2.5}}, ensure_ascii=False),
            encoding="utf-8")
        r = self.build(with_settings=False)
        self.assertFalse((self.out / DF.SETTINGS).exists())
        self.assertFalse(r.with_settings)
        r = self.build(with_settings=True, force=True)
        self.assertTrue((self.out / DF.SETTINGS / PENA / D.SETTINGS_NAME).exists())
        self.assertTrue(r.with_settings)
        self.assertTrue(any("印刷位置の補正 X" in ln for ln in r.lines))
        memo = (self.out / DF.MEMO_NAME).read_text(encoding="utf-8-sig")
        self.assertIn("印刷位置の補正 X（mm）: 2.5", memo)

    def test_settings_are_taken_from_where_they_were_exported(self):
        """書き出した場所（distribution.DIR）から入れる。置き場所を変えていても入ること。"""
        elsewhere = self.base / "別の場所" / "配布設定"
        elsewhere.mkdir(parents=True)
        (elsewhere / D.SETTINGS_NAME).write_text(json.dumps({
            "format": 1, "settings": {"label_scale_pct": 100.5}}), encoding="utf-8")
        with mock.patch.object(D, "DIR", elsewhere):
            r = DF.build(self.out)               # root を渡さない＝本番と同じ経路
        self.assertTrue(r.with_settings)
        self.assertTrue((self.out / DF.SETTINGS / PENA / D.SETTINGS_NAME).exists(),
                        "配った先が読む名前（配布設定\\packing_pena_label）で入れる")

    # ---- 統合版: 3機能の配布設定 ---------------------------------------
    def _export(self, key: str, settings: dict) -> None:
        d = self.src / DF.SETTINGS / key
        d.mkdir(parents=True, exist_ok=True)
        (d / D.SETTINGS_NAME).write_text(json.dumps({
            "format": 1, "created_at": "2026-09-26 08:00:00", "created_on": "PC1",
            "settings": settings}, ensure_ascii=False), encoding="utf-8")
        (d / D.README_NAME).write_text("x", encoding="utf-8")

    def _export_all(self) -> None:
        self._export("packing_details", {"export_dir": "D:\\出力"})
        self._export(PENA, {"label_offset_x_mm": 2.5})
        self._export("packing_material_calculation", {"auto_import": True})

    def _stale_in_module(self) -> Path:
        """移植したときの古い置き場所(機能のフォルダの中)に残った配布設定。"""
        stale = self.src / MODULE / DF.SETTINGS
        stale.mkdir(parents=True, exist_ok=True)
        (stale / D.SETTINGS_NAME).write_text("{}", encoding="utf-8")
        return stale

    def test_all_three_modules_settings_go_in(self):
        """梱包明細・ペナラベル・資材計算の配布設定がまとめて入る。"""
        self._export_all()
        r = self.build()
        self.assertTrue(r.with_settings)
        for key, label, _ in dist_settings.MODULES:
            self.assertTrue((self.out / DF.SETTINGS / key / D.SETTINGS_NAME).exists(), key)
            self.assertTrue(any(label in ln for ln in r.lines), label)
        memo = (self.out / DF.MEMO_NAME).read_text(encoding="utf-8-sig")
        self.assertIn("配布設定\\packing_details", memo)
        self.assertIn("印刷位置の補正 X（mm）: 2.5", memo)
        self.assertNotIn("書き出していない機能", memo)

    def test_no_settings_means_none_at_all(self):
        """「入れない」を選んだら、どの機能の配布設定も入らない(古い置き場所の分も)。"""
        self._export_all()
        self._stale_in_module()
        r = self.build(with_settings=False)
        self.assertFalse(r.with_settings)
        leaked = [str(p.relative_to(self.out)) for p in self.out.rglob("*")
                  if p.name == DF.SETTINGS or p.name == D.SETTINGS_NAME]
        self.assertEqual(leaked, [], "入れないと決めたのに入った")

    def test_old_place_inside_module_is_not_copied(self):
        """機能のフォルダの中の古い 配布設定\\ は写さない(入れる場所は決まった1か所)。"""
        self._export(PENA, {"label_offset_x_mm": 2.5})
        self._stale_in_module()
        self.build(with_settings=True)
        self.assertFalse((self.out / MODULE / DF.SETTINGS).exists())
        self.assertTrue((self.out / DF.SETTINGS / PENA / D.SETTINGS_NAME).exists())

    def test_only_exported_modules_go_in_and_the_rest_are_named(self):
        self._export("packing_details", {"export_dir": "D:\\出力"})
        r = self.build()
        self.assertTrue((self.out / DF.SETTINGS / "packing_details").is_dir())
        self.assertFalse((self.out / DF.SETTINGS / PENA).exists())
        self.assertTrue(any("書き出していない機能" in ln and "ペナラベル" in ln
                            and "資材計算" in ln for ln in r.lines))

    def test_every_module_exports_where_the_build_reads(self):
        """3機能の書き出し先(distribution.DIR)は、統合アプリの 配布設定\\<機能> が既定。"""
        import importlib
        for key, _, modname in dist_settings.MODULES:
            env = {"packing_details": "PACKING_DETAILS_DISTRIBUTION_DIR",
                   PENA: "PACKING_PENA_DISTRIBUTION_DIR",
                   "packing_material_calculation": "COIL_TOOL_DISTRIBUTION_DIR"}[key]
            module = importlib.import_module(modname)
            src = open(module.__file__, encoding="utf-8").read()
            self.assertIn(env, src, key)
            self.assertIn('default_dir("%s")' % key, src, key)
            self.assertEqual(dist_settings.default_dir(key),
                             ROOT / "配布設定" / key)

    def test_refuses_to_build_inside_the_app(self):
        with self.assertRaises(DF.BuildRefused):
            DF.build(self.src / "dist", root=self.src)

    def test_refuses_relative_path(self):
        with self.assertRaises(DF.BuildRefused):
            DF.build(Path("dist"), root=self.src)

    def test_existing_folder_needs_force(self):
        self.build()
        with self.assertRaises(DF.BuildRefused):
            self.build()
        self.build(force=True)

    def test_force_never_deletes_a_folder_it_did_not_make(self):
        """打ち間違えた場所（ふつうのフォルダ）を消さないこと。"""
        other = self.base / "大事なフォルダ"
        other.mkdir()
        (other / "大事.txt").write_text("消さないで", encoding="utf-8")
        with self.assertRaises(DF.BuildRefused) as cm:
            DF.build(other, root=self.src, force=True)
        self.assertIn("消して作り直すことはしません", str(cm.exception))
        self.assertTrue((other / "大事.txt").exists())

    def test_missing_required_file_is_refused_and_cleaned_up(self):
        (self.src / "server.py").unlink()
        with self.assertRaises(DF.BuildRefused):
            self.build()
        self.assertFalse(self.out.exists(), "半端なフォルダを残した")

    def test_zip(self):
        r = self.build(make_zip=True)
        self.assertTrue(os.path.exists(r.zip_path))

    def test_script_runs(self):
        """scripts/make_dist.py（make_dist.bat の中身）がそのまま動くこと。"""
        out = self.base / "by_script"
        p = subprocess.run([sys.executable, str(self.src / "scripts" / "make_dist.py"),
                            "--out", str(out)], capture_output=True, text=True,
                           timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((out / DF.MEMO_NAME).exists())

    def test_bat_is_ascii_crlf_and_prefers_bundled_python(self):
        raw = (ROOT / "scripts" / "make_dist.bat").read_bytes()
        raw.decode("ascii")
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "改行が CRLF でない")
        self.assertIn(b"runtime\\python.exe", raw)


if __name__ == "__main__":
    unittest.main()
