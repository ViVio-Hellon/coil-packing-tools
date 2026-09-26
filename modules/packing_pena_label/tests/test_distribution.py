# -*- coding: utf-8 -*-
"""配布設定 ── アプリの直下の ``配布設定\\`` を、配った先の端末が読み込む

``ViVio-Hellon/python-web-tools`` の tests/test_distribution.py と同じ筋で確かめる。

    1. 一度起動して設定をする（設定画面の「配布設定」で書き出す）
    2. ``配布設定\\`` が作られ、配下に必要なものが入る
    3. 配った先は ``配布設定\\`` があれば読み込む
    4. その端末にすでにある設定は読み込まない
"""

import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from modules.packing_pena_label.app import config as C                            # noqa: E402
from modules.packing_pena_label.app.config import Config, load_config             # noqa: E402
from modules.packing_pena_label.app.services import distribution as D             # noqa: E402

PW = "nisk"


class DistributionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        # 端末ごとのローカル領域をテスト用の場所へ
        # 端末ごとに別の場所。他のテストが Config.local_dir を差し替えたまま
        # でも影響を受けないよう、ここで決め打ちする
        state = self.tmp / "state"
        p = mock.patch.object(Config, "local_dir",
                              property(lambda cfg: str(state / cfg.app_id)))
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(D, "DIR", self.tmp / "tool" / "配布設定")
        p.start(); self.addCleanup(p.stop)
        (self.tmp / "tool").mkdir()
        self.source = Config(app_id="SourcePC")
        self.dest = Config(app_id="DestPC")

    def local(self, cfg):
        return C.load_local_overrides(cfg)

    def put(self, cfg, **values):
        cur = self.local(cfg)
        cur.update(values)
        C.save_local_overrides(cur, cfg)

    def configure_source(self):
        self.put(self.source,
                 material_db_file=r"\\srv\共有\梱包資材マスタ.sqlite3",
                 label_offset_x_mm=2.5, label_offset_y_mm=1.5,
                 label_scale_pct=100.4, port=9999)

    def export(self, keys=None, password=PW):
        keys = [k for k, _, _, default in D.items() if default] \
            if keys is None else keys
        return D.export(password, list(keys), self.source)

    def written(self):
        return json.loads(D.settings_path().read_text(encoding="utf-8"))


class ExportTest(DistributionTestCase):
    def test_writes_only_what_this_terminal_changed(self):
        self.configure_source()
        r = self.export()
        self.assertTrue(r.ok, r.message)
        got = self.written()["settings"]
        self.assertEqual(got["material_db_file"], r"\\srv\共有\梱包資材マスタ.sqlite3")
        self.assertEqual(got["label_offset_x_mm"], 2.5)
        # この端末で変えていない項目は入れない（空で上書きしないため）
        self.assertNotIn("aim_ref_path", got)
        self.assertIn("既定のまま", r.message)

    def test_print_correction_is_in_by_default(self):
        """現場の求め: 印刷補正値も配布設定に入ること。"""
        defaults = {k for k, _, _, d in D.items() if d}
        for k in ("label_offset_x_mm", "label_offset_y_mm", "label_scale_pct"):
            self.assertIn(k, defaults)

    def test_port_is_left_out_by_default(self):
        self.configure_source()
        self.export()
        self.assertNotIn("port", self.written()["settings"])

    def test_needs_password(self):
        self.configure_source()
        r = self.export(password="")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, D.REFUSE_NEED_PASSWORD)
        self.assertFalse(D.DIR.exists(), "合言葉なしで作ってはいけない")

    def test_unknown_or_empty_selection_is_refused(self):
        self.configure_source()
        self.assertEqual(self.export(keys=["班員名簿"]).reason, D.REFUSE_BAD_INPUT)
        self.assertEqual(self.export(keys=[]).reason, D.REFUSE_BAD_INPUT)

    def test_nothing_changed_is_refused_and_says_why(self):
        r = self.export()
        self.assertFalse(r.ok)
        self.assertIn("既定のまま", r.message)

    def test_folder_has_settings_and_readme(self):
        self.configure_source()
        self.export()
        self.assertTrue((D.DIR / D.SETTINGS_NAME).is_file())
        readme = (D.DIR / D.README_NAME).read_text(encoding="utf-8-sig")
        self.assertIn("印刷位置の補正 X", readme)
        self.assertIn("\r\n", (D.DIR / D.README_NAME).read_bytes().decode("utf-8-sig"),
                      "メモ帳で崩れないよう CRLF")
        meta = self.written()
        self.assertEqual(meta["format"], D.FORMAT)
        self.assertTrue(meta["created_at"])

    def test_export_replaces_previous_contents(self):
        self.configure_source()
        self.export()
        self.export(keys=["label_offset_x_mm"])
        self.assertEqual(set(self.written()["settings"]), {"label_offset_x_mm"})
        self.assertFalse(D.DIR.with_name(D.DIR.name + ".作成中").exists())

    def test_failed_write_leaves_no_half_bundle(self):
        self.configure_source()
        with mock.patch.object(Path, "rename", side_effect=OSError("書けない")):
            r = self.export()
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, D.REFUSE_FAILED)
        self.assertFalse(D.DIR.with_name(D.DIR.name + ".作成中").exists())


class ApplyTest(DistributionTestCase):
    def setUp(self):
        super().setUp()
        self.configure_source()
        self.assertTrue(self.export().ok)

    def test_start_fills_what_the_terminal_lacks(self):
        r = D.apply_on_start(self.dest)
        self.assertIn("印刷位置の補正 X（mm）", r.applied)
        got = self.local(self.dest)
        self.assertEqual(got["label_offset_x_mm"], 2.5)
        self.assertEqual(got["material_db_file"], r"\\srv\共有\梱包資材マスタ.sqlite3")
        # 実際に効く設定として読めること
        dest_dir = self.dest.local_dir
        with mock.patch.object(Config, "local_dir",
                               property(lambda cfg: dest_dir)):
            cfg = load_config()
        self.assertEqual(cfg.label_offset_x_mm, 2.5)

    def test_start_keeps_what_the_terminal_already_has(self):
        self.put(self.dest, label_offset_x_mm=-1.0)
        r = D.apply_on_start(self.dest)
        self.assertIn("印刷位置の補正 X（mm）", r.kept)
        self.assertEqual(self.local(self.dest)["label_offset_x_mm"], -1.0)
        self.assertEqual(self.local(self.dest)["label_offset_y_mm"], 1.5)

    def test_later_local_change_is_not_undone_on_next_start(self):
        D.apply_on_start(self.dest)
        self.put(self.dest, label_offset_x_mm=0.3)          # 端末で直した
        D.apply_on_start(self.dest)                         # 次の起動
        self.assertEqual(self.local(self.dest)["label_offset_x_mm"], 0.3)

    def test_reapply_overwrites_and_needs_password(self):
        self.put(self.dest, label_offset_x_mm=-1.0)
        self.assertEqual(D.reapply("", self.dest).reason, D.REFUSE_NEED_PASSWORD)
        self.assertEqual(self.local(self.dest)["label_offset_x_mm"], -1.0)
        r = D.reapply(PW, self.dest)
        self.assertTrue(r.ok)
        self.assertEqual(self.local(self.dest)["label_offset_x_mm"], 2.5)

    def test_broken_value_is_skipped_not_fatal(self):
        meta = self.written()
        meta["settings"]["label_scale_pct"] = 999      # 範囲外（50〜150）
        D.settings_path().write_text(json.dumps(meta), encoding="utf-8")
        r = D.apply_on_start(self.dest)
        self.assertIn("印刷倍率（%）", r.skipped)
        self.assertNotIn("label_scale_pct", self.local(self.dest))
        self.assertEqual(self.local(self.dest)["label_offset_x_mm"], 2.5)

    def test_broken_file_is_ignored(self):
        D.settings_path().write_text("{壊れている", encoding="utf-8")
        self.assertIsNone(D.read())
        r = D.apply_on_start(self.dest)
        self.assertTrue(r.ok)
        self.assertEqual(r.applied, [])

    def test_other_format_is_ignored(self):
        meta = self.written(); meta["format"] = 99
        D.settings_path().write_text(json.dumps(meta), encoding="utf-8")
        self.assertIsNone(D.read())

    def test_unknown_keys_in_file_are_dropped(self):
        meta = self.written(); meta["settings"]["班員名簿"] = "x"
        D.settings_path().write_text(json.dumps(meta), encoding="utf-8")
        self.assertNotIn("班員名簿", D.read().settings)

    def test_bundle_from_the_line_pc_is_read(self):
        """現場で書き出した 設定.json と同じ形（参照先 2 項目だけ）が読めること。"""
        D.settings_path().write_text(json.dumps({
            "format": 1, "created_at": "2026-09-25 10:26:28", "created_on": "LINE-PC",
            "settings": {
                "aim_ref_path": r"\\server\共有\AIM\【■】_参照用ファイル\Test環境",
                "material_db_file":
                    r"\\server\共有\AIM\【■】_参照用ファイル\Test環境\梱包資材マスタ.sqlite3",
            }}, ensure_ascii=False), encoding="utf-8")
        r = D.apply_on_start(self.dest)
        self.assertEqual(sorted(r.applied),
                         sorted(["資材マスタの参照先フォルダー", "梱包資材マスタ（SQLite）"]))
        self.assertEqual(r.skipped, [])

    def test_removed_setting_in_old_bundle_is_ignored(self):
        """削除した「日報ブックのファイル名」が古い配布設定に残っていても読み飛ばす。"""
        meta = self.written()
        meta["settings"]["daily_report_book"] = "日報＆資材計算.xlsm"
        D.settings_path().write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        self.assertNotIn("daily_report_book", D.read().settings)
        D.apply_on_start(self.dest)
        self.assertNotIn("daily_report_book", self.local(self.dest))

    def test_remove_needs_password_and_keeps_terminal(self):
        D.apply_on_start(self.dest)
        self.assertEqual(D.remove("").reason, D.REFUSE_NEED_PASSWORD)
        self.assertTrue(D.DIR.exists())
        self.assertTrue(D.remove(PW).ok)
        self.assertFalse(D.DIR.exists())
        self.assertEqual(self.local(self.dest)["label_offset_x_mm"], 2.5)

    def test_summary_shows_contents_and_when_applied(self):
        D.apply_on_start(self.dest)
        sm = D.summary(self.dest)
        self.assertTrue(sm["exists"])
        self.assertTrue(sm["appliedAt"])
        labels = [c["label"] for c in sm["contents"]]
        self.assertIn("印刷位置の補正 X（mm）", labels)

    def test_other_settings_save_does_not_lose_applied_values(self):
        """設定画面で別の項目を保存しても、読み込んだ値が消えないこと。"""
        from modules.packing_pena_label.app.services import settings as S
        D.apply_on_start(self.dest)
        dest_dir = self.dest.local_dir
        with mock.patch.object(Config, "local_dir",
                               property(lambda cfg: dest_dir)):
            cfg = load_config()
            S.save({"label_offset_y_mm": 0.7}, cfg)
        got = self.local(self.dest)
        self.assertEqual(got["label_offset_y_mm"], 0.7)
        self.assertEqual(got["label_offset_x_mm"], 2.5)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DistributionHttpTest(unittest.TestCase):
    """設定画面から押したときの流れ。"""

    @classmethod
    def setUpClass(cls):
        from modules.packing_pena_label import server
        cls.tmp = Path(tempfile.mkdtemp())
        db = cls.tmp / "m.sqlite3"
        c = sqlite3.connect(str(db))
        c.execute("CREATE TABLE 資材重量 (管理番号 INTEGER, 梱包資材名 TEXT,"
                  " 単位質量 TEXT, 係数 TEXT)")
        c.execute("INSERT INTO 資材重量 VALUES (1,'テスラピン','0.5','1')")
        c.commit(); c.close()
        cls.dir_patch = mock.patch.object(D, "DIR", cls.tmp / "tool" / "配布設定")
        cls.dir_patch.start()
        (cls.tmp / "tool").mkdir()
        cfg = Config()
        cfg.port = free_port()
        cfg.prefer_access = False
        cfg.material_db_file = str(db)
        # 他のテストへ漏らさないよう、元の定義を取っておいて戻す
        cls.orig_local_dir = Config.__dict__["local_dir"]
        Config.local_dir = property(lambda self, d=str(cls.tmp): d)
        cls.httpd, cls.ctx = server.create_server(cfg)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      kwargs={"poll_interval": 0.1}, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cfg.port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.ctx.store.close()
        cls.dir_patch.stop()
        Config.local_dir = cls.orig_local_dir
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def post(self, path, payload):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=20) as r:
            return r.read().decode("utf-8")

    def test_flow_export_then_reapply_is_live(self):
        # 位置合わせで補正を入れる（この端末に入る）
        self.post("/api/label/align", {"mode": "nudge", "dx": 2.5, "dy": 1.5})

        j = self.post("/api/settings/distribution/export",
                      {"items": ["label_offset_x_mm", "label_offset_y_mm"]})
        self.assertTrue(j.get("needPassword"), "合言葉なしは聞き返す")
        j = self.post("/api/settings/distribution/export",
                      {"items": ["label_offset_x_mm", "label_offset_y_mm"],
                       "password": PW})
        self.assertTrue(j["ok"], j)
        self.assertTrue(j["distribution"]["exists"])

        # 端末側を別の値にしてから読み込み直すと、配布設定の値に戻り、すぐ効く
        self.post("/api/label/align", {"mode": "reset"})
        j = self.post("/api/settings/distribution/reapply", {"password": PW})
        self.assertTrue(j["ok"], j)
        self.assertIn("translate(2.500mm,1.500mm)", self.get("/labels/calibration"))

        j = self.post("/api/settings/distribution/remove", {"password": PW})
        self.assertTrue(j["ok"])
        self.assertFalse(j["distribution"]["exists"])
        self.post("/api/label/align", {"mode": "reset"})

    def test_build_distribution_folder_from_the_screen(self):
        """設定 → 配布設定 から配布用フォルダを作れること（合言葉が要る）。"""
        out = self.tmp / "配布物" / "PackingPenaLabel_test"
        j = self.post("/api/settings/distribution/build", {"out": str(out)})
        self.assertTrue(j.get("needPassword"))
        self.assertFalse(out.exists())
        j = self.post("/api/settings/distribution/build",
                      {"out": str(out), "password": PW, "withSettings": True})
        self.assertTrue(j["ok"], j)
        self.assertTrue((out / "配布メモ.txt").exists())
        self.assertTrue((out / "start_app.py").exists())
        self.assertFalse((out / "tests").exists())
        # 同じ場所へもう一度は、作り直しを選ばないと断る
        j = self.post("/api/settings/distribution/build",
                      {"out": str(out), "password": PW})
        self.assertFalse(j["ok"])

    def test_settings_page_has_the_build_card(self):
        html = self.get("/settings")
        self.assertIn('id="distBuild"', html)
        self.assertIn("配布用フォルダを作る", html)

    def test_daily_report_book_is_gone(self):
        """日報ブックは Excel でだけ必要だった（現場確認）。設定にも画面にも出さない。"""
        self.assertNotIn("日報ブック", self.get("/settings"))
        self.assertNotIn("日報（", self.get("/"))
        self.assertNotIn("daily_report_book",
                         [k for k, _, _, _ in D.items()])

    def test_settings_page_has_distribution_tab(self):
        html = self.get("/settings")
        self.assertIn('data-sub="dist"', html)
        self.assertIn('id="distExport"', html)
        self.assertIn('data-dist-item="label_offset_x_mm"', html)
        # アプリ同梱の app.json は「同梱の既定」と呼ぶ（配布設定と取り違えない）
        self.assertIn("同梱の既定", html)


if __name__ == "__main__":
    unittest.main()
