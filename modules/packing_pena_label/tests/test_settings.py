# -*- coding: utf-8 -*-
"""設定（ファイルパスの変更）の検証。

VBA では参照先がコード内の定数に埋まっていたものを、
画面から変更できる設定として外へ出した部分。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from modules.packing_pena_label.app import config as C
from modules.packing_pena_label.app.services import settings as S


class SettingsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = C.Config()
        # ローカル領域をテスト用に差し替える
        type(self.cfg).local_dir = property(lambda s, d=self.tmp: d)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSchema(unittest.TestCase):
    def test_every_setting_maps_to_a_config_field(self):
        cfg = C.Config()
        for spec in S.SETTINGS:
            with self.subTest(key=spec.key):
                self.assertTrue(hasattr(cfg, spec.key),
                                "Config に %s がない" % spec.key)

    def test_every_setting_is_in_a_known_group(self):
        for spec in S.SETTINGS:
            self.assertIn(spec.group, S.GROUP_ORDER)

    def test_keys_are_unique(self):
        keys = [s.key for s in S.SETTINGS]
        self.assertEqual(len(keys), len(set(keys)))


class TestCoerce(unittest.TestCase):
    def test_int_range(self):
        spec = S.SETTINGS_BY_KEY["port"]
        self.assertEqual(S.coerce(spec, "8080"), 8080)
        with self.assertRaises(ValueError):
            S.coerce(spec, "99999")
        with self.assertRaises(ValueError):
            S.coerce(spec, "80")
        with self.assertRaises(ValueError):
            S.coerce(spec, "abc")

    def test_bool(self):
        spec = S.SETTINGS_BY_KEY["prefer_access"]
        self.assertIs(S.coerce(spec, True), True)
        self.assertIs(S.coerce(spec, "on"), True)
        self.assertIs(S.coerce(spec, "false"), False)

    def test_dir_trailing_separator_removed(self):
        spec = S.SETTINGS_BY_KEY["aim_ref_path"]
        self.assertEqual(S.coerce(spec, r"\\srv\share\\"), r"\\srv\share")

    def test_required_empty_rejected(self):
        spec = S.SETTINGS_BY_KEY["port"]
        with self.assertRaises(ValueError):
            S.coerce(spec, "")

    def test_validate_all_collects_errors(self):
        errs = S.validate_all({"port": "99999", "material_refresh_sec": "abc"})
        self.assertIn("port", errs)
        self.assertIn("material_refresh_sec", errs)

    def test_unknown_key_rejected(self):
        errs = S.validate_all({"nope": "1"})
        self.assertIn("nope", errs)


class TestCheckPath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_dir_is_error(self):
        spec = S.SETTINGS_BY_KEY["debug_print_path"]
        r = S.check_path(spec, os.path.join(self.tmp, "nope"))
        self.assertFalse(r.ok)
        self.assertEqual(r.level, "error")

    def test_empty_optional_is_warn_not_error(self):
        spec = S.SETTINGS_BY_KEY["debug_print_path"]
        r = S.check_path(spec, "")
        self.assertTrue(r.ok)
        self.assertEqual(r.level, "warn")

    def test_aim_ref_requires_accdb_inside(self):
        spec = S.SETTINGS_BY_KEY["aim_ref_path"]
        r = S.check_path(spec, self.tmp)
        self.assertFalse(r.ok)
        self.assertIn(S.ACCDB_NAME, r.message)

        open(os.path.join(self.tmp, S.ACCDB_NAME), "w").close()
        r = S.check_path(spec, self.tmp)
        self.assertTrue(r.ok, r.message)

    def test_file_kind_rejects_directory(self):
        spec = S.SETTINGS_BY_KEY["material_csv_file"]
        r = S.check_path(spec, self.tmp)
        self.assertFalse(r.ok)
        self.assertIn("ファイルではありません", r.message)


class TestSaveLoad(SettingsTestBase):
    def _read_local(self):
        with open(self.cfg.local_config_path, encoding="utf-8") as f:
            return json.load(f)

    def test_save_writes_local_only(self):
        r = S.save({"material_refresh_sec": 60}, self.cfg)
        self.assertTrue(r["ok"], r)
        self.assertTrue(os.path.exists(self.cfg.local_config_path))
        data = self._read_local()
        self.assertEqual(data["material_refresh_sec"], 60)
        # アプリ本体側の config/app.json は触らない
        self.assertNotEqual(os.path.abspath(self.cfg.local_config_path),
                            os.path.abspath(self.cfg.app_config_path))

    def test_value_equal_to_current_is_not_stored(self):
        """既定・同梱の既定と同じ値なら端末側に上書きを残さない。"""
        same = self.cfg.material_refresh_sec
        S.save({"material_refresh_sec": same}, self.cfg)
        data = self._read_local()
        self.assertNotIn("material_refresh_sec", data)

    def test_restart_items_are_reported(self):
        r = S.save({"port": self.cfg.port + 1}, self.cfg)
        self.assertTrue(r["ok"])
        self.assertTrue(r["restart"], "再起動が必要な項目が報告されていない")

    def test_unreachable_path_saves_with_warning(self):
        """共有が落ちていても設定はできるべき。保存はするが警告を返す。"""
        bad = os.path.join(self.tmp, "no-such-share")
        r = S.save({"aim_ref_path": bad}, self.cfg)
        self.assertTrue(r["ok"], "保存自体は成功すべき")
        self.assertEqual(r["level"], "warn")
        self.assertTrue(r["warnings"])
        data = self._read_local()
        self.assertEqual(data["aim_ref_path"], bad)

    def test_invalid_value_blocks_save(self):
        r = S.save({"port": "99999"}, self.cfg)
        self.assertFalse(r["ok"])
        self.assertIn("port", r["errors"])
        self.assertFalse(os.path.exists(self.cfg.local_config_path))

    def test_backup_kept_on_overwrite(self):
        S.save({"material_refresh_sec": 60}, self.cfg)
        S.save({"material_refresh_sec": 90}, self.cfg)
        self.assertTrue(os.path.exists(self.cfg.local_config_path + ".bak"))

    def test_reset_removes_local(self):
        S.save({"material_refresh_sec": 60}, self.cfg)
        self.assertTrue(os.path.exists(self.cfg.local_config_path))
        S.reset(self.cfg)
        self.assertFalse(os.path.exists(self.cfg.local_config_path))


class TestSaveIsIdempotent(SettingsTestBase):
    """同じ内容をもう一度保存しても、上書きが消えないこと。

    設定画面は**毎回すべての項目を送る**。「既定と同じ値なら上書きを残さない」
    判定を *現在有効な値* と比べて行うと、1 度保存した設定は
    2 度目の保存（値を変えず「保存」を押すだけ）で上書きが全部消え、
    同梱の既定へ黙って戻ってしまう。比較は **同梱の既定** と行う。
    """

    def _read_local(self):
        with open(self.cfg.local_config_path, encoding="utf-8") as f:
            return json.load(f)

    def _adopt(self, values):
        """画面と同じく、保存後は読み直した値を cfg へ反映する。"""
        for k, v in values.items():
            setattr(self.cfg, k, S.coerce(S.SETTINGS_BY_KEY[k], v))

    def test_same_value_saved_twice_keeps_override(self):
        vals = {"material_refresh_sec": 60}
        S.save(vals, self.cfg)
        self._adopt(vals)
        self.assertEqual(self._read_local()["material_refresh_sec"], 60)

        S.save(vals, self.cfg)                      # 値を変えずもう一度
        self.assertEqual(self._read_local().get("material_refresh_sec"), 60,
                         "2 度目の保存で上書きが消えている")

    def test_repeated_saves_keep_every_override(self):
        vals = {"material_refresh_sec": 60, "access_timeout_sec": 30,
                "label_scale_pct": 101.5, "barcode_mode": "font"}
        for _ in range(3):
            S.save(vals, self.cfg)
            self._adopt(vals)
        data = self._read_local()
        for k, v in vals.items():
            self.assertEqual(data.get(k), v, "%s が失われた" % k)

    def test_second_save_reports_no_change(self):
        vals = {"material_refresh_sec": 60}
        S.save(vals, self.cfg)
        self._adopt(vals)
        r = S.save(vals, self.cfg)
        self.assertTrue(r["ok"])
        self.assertEqual(r["changed"], [], "変更なしと報告すべき")

    def test_back_to_default_removes_override_and_reports_it(self):
        """既定へ戻す操作は、上書きを消したうえで『変更した』と報告する。"""
        base = C.load_base_config()
        default = base.material_refresh_sec
        S.save({"material_refresh_sec": default + 5}, self.cfg)
        self._adopt({"material_refresh_sec": default + 5})
        self.assertIn("material_refresh_sec", self._read_local())

        r = S.save({"material_refresh_sec": default}, self.cfg)
        self.assertNotIn("material_refresh_sec", self._read_local())
        self.assertTrue(r["changed"], "既定へ戻したことが報告されていない")

    def test_restart_flag_only_on_actual_change(self):
        base = C.load_base_config()
        vals = {"port": base.port + 1}
        r1 = S.save(vals, self.cfg)
        self._adopt(vals)
        self.assertTrue(r1["restart"])
        r2 = S.save(vals, self.cfg)
        self.assertEqual(r2["restart"], [], "変わっていないのに再起動を要求している")


class TestAccdbPath(unittest.TestCase):
    """``PATH_AIM_参照`` は VBA の値をそのまま貼れること。

    VBA 側は末尾に ``\\`` を付けて文字列連結していたので、
    現場の人はその形で貼り付ける。落とさずに繋ぐと区切りが重なる。
    """

    def _p(self, base):
        cfg = C.Config()
        cfg.aim_ref_path = base
        return cfg.accdb_path

    def test_trailing_separator_does_not_double_up(self):
        with_sep = self._p("\\\\srv\\共有\\AIM\\")
        without = self._p("\\\\srv\\共有\\AIM")
        self.assertEqual(with_sep, without, "末尾の区切りで結果が変わる")
        self.assertNotIn("\\\\梱包", with_sep)
        self.assertNotIn("\\/", with_sep)
        self.assertNotIn("//", with_sep.replace("\\\\srv", "srv"))

    def test_unc_prefix_is_kept(self):
        got = self._p("\\\\nlmsrvngy03\\各課共有\\AIM\\")
        self.assertTrue(got.startswith("\\\\nlmsrvngy03"),
                        "UNC の先頭 \\\\ が消えている: %r" % got)

    def test_forward_slashes_also_work(self):
        self.assertEqual(self._p("C:/x/y/"), self._p("C:/x/y"))

    def test_filename_is_appended(self):
        self.assertTrue(self._p("C:/x").endswith("梱包資材マスタ.accdb"))

    def test_empty_stays_empty(self):
        self.assertEqual(self._p(""), "")
        self.assertEqual(self._p("   "), "")

    def test_default_matches_the_vba_constant(self):
        """同梱の既定は VBA の Public Const と同じ場所を指す。"""
        base = C.load_base_config()
        self.assertIn("nlmsrvngy03", base.aim_ref_path)
        self.assertIn("梱包課", base.aim_ref_path)
        self.assertTrue(base.accdb_path.endswith("梱包資材マスタ.accdb"))

    def test_default_is_still_overridable(self):
        """既定があっても端末ごとに変えられる（設定項目として残っている）。"""
        self.assertIn("aim_ref_path", S.SETTINGS_BY_KEY)
        self.assertFalse(S.SETTINGS_BY_KEY["aim_ref_path"].restart,
                         "再起動なしで変えられるべき")


class TestBaseConfig(unittest.TestCase):
    """同梱の既定だけを読む経路（設定画面の比較に使う）。"""

    def test_base_ignores_local_layer(self):
        """local.json に何が入っていても、同梱の既定には混ざらない。"""
        tmp = tempfile.mkdtemp()
        try:
            cfg = C.Config()
            type(cfg).local_dir = property(lambda s, d=tmp: d)
            C.save_local_overrides({"material_refresh_sec": 12345,
                                    "barcode_mode": "font"}, cfg)
            self.assertEqual(C.load_config().material_refresh_sec, 12345,
                             "前提: 通常の読込では上書きが効く")

            base = C.load_base_config()
            self.assertNotEqual(base.material_refresh_sec, 12345,
                                "同梱の既定に端末の上書きが混ざっている")
            self.assertNotEqual(base.barcode_mode, "font")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_base_has_no_local_layer_in_sources(self):
        base = C.load_base_config()
        self.assertNotIn(C.LAYER_LOCAL, set(base.sources.values()))
        self.assertNotIn(C.LAYER_ENV, set(base.sources.values()))


class TestLayering(SettingsTestBase):
    def test_local_overrides_app(self):
        """この端末の設定が同梱の既定より優先される。"""
        C.save_local_overrides({"material_refresh_sec": 77}, self.cfg)
        loaded = C.Config()
        type(loaded).local_dir = property(lambda s, d=self.tmp: d)
        data = C.load_local_overrides(loaded)
        self.assertEqual(data["material_refresh_sec"], 77)

    def test_removed_setting_left_in_local_json_is_harmless(self):
        """「日報ブックのファイル名」は削除した。古い local.json に残っていても、
        起動も保存も止めない（その値は使わない）。"""
        C.save_local_overrides({"daily_report_book": "日報＆資材計算.xlsm",
                                "material_refresh_sec": 42}, self.cfg)
        with mock.patch.object(C, "local_app_dir", lambda app_id=C.APP_ID, d=self.tmp: d):
            loaded = C.load_config()
        self.assertFalse(hasattr(loaded, "daily_report_book"))
        self.assertEqual(loaded.material_refresh_sec, 42)
        r = S.save({"material_refresh_sec": 43}, self.cfg)
        self.assertTrue(r["ok"], r)

    def test_broken_local_json_does_not_crash(self):
        os.makedirs(self.cfg.local_config_dir, exist_ok=True)
        with open(self.cfg.local_config_path, "w", encoding="utf-8") as f:
            f.write("{ broken")
        self.assertEqual(C.load_local_overrides(self.cfg), {})

    def test_comment_keys_are_ignored(self):
        C.save_local_overrides({"material_refresh_sec": 55}, self.cfg)
        data = C.load_local_overrides(self.cfg)
        self.assertNotIn("_comment", data)
        self.assertEqual(data["material_refresh_sec"], 55)


class TestResolvedPaths(unittest.TestCase):
    def test_empty_uses_bundled_default(self):
        cfg = C.Config()
        self.assertEqual(cfg.size_master_path, cfg.default_size_master_path)
        self.assertEqual(cfg.material_csv_path, cfg.default_material_csv_path)

    def test_override_wins(self):
        cfg = C.Config()
        cfg.size_master_file = "/tmp/other.json"
        self.assertEqual(cfg.size_master_path, "/tmp/other.json")

    def test_accdb_path_built_from_folder(self):
        cfg = C.Config()
        self.assertEqual(cfg.accdb_path, "")
        cfg.aim_ref_path = os.path.join("/srv", "share")
        self.assertEqual(cfg.accdb_path,
                         os.path.join("/srv", "share", S.ACCDB_NAME))


class TestDescribe(SettingsTestBase):
    def test_describe_covers_all_settings(self):
        self.cfg.sources = {s.key: "app" for s in S.SETTINGS}
        items = S.describe(self.cfg)
        self.assertEqual(len(items), len(S.SETTINGS))
        for it in items:
            self.assertIn("label", it)
            self.assertIn("layerLabel", it)
            if it["kind"] in ("dir", "file"):
                self.assertIn("check", it)


if __name__ == "__main__":
    unittest.main()
