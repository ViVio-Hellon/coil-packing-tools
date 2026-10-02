"""共有の2つのファイルの置き場所を、それぞれ設定する(VER 0.13.10。現場の指摘)。

    置き場所・取り込み: 梱包明細履歴.sqlite3 / 梱包明細打ち出し.json
    それぞれにパス設定できるようにしてください

- 既定はどちらも梱包資材マスタのフォルダ(これまでと同じ)
- 明細の履歴(送る・探す・マスタ管理で見る)は、設定した場所を使う
- 控えのJSON(管理者パスワード・右上の文字の控え・書くときの鍵)は、設定した場所を使う。
  右上の文字の正(梱包資材マスタの表)は、梱包資材マスタのフォルダのまま
- 変えるには管理者パスワードが要る。配布設定にも入る
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from modules.packing_details.meisai import (admin_password, config, distribution, master_browse,
                                            qa_mark, shared_settings, slip_history, user_settings)

from . import _db
from .test_web import HEADERS, TOKEN, _client

PASSWORD = config.ADMIN_PASSWORD


class DefaultPlacesTest(unittest.TestCase):
    def setUp(self):
        _db.settings_file(self)

    def test_both_default_to_the_master_folder(self):
        self.assertEqual(shared_settings.json_dir(), shared_settings.shared_dir())
        self.assertEqual(shared_settings.history_dir(), shared_settings.shared_dir())
        self.assertEqual(slip_history.history_path(),
                         shared_settings.shared_dir() / config.HISTORY_DB_NAME)
        self.assertEqual(shared_settings.shared_path(),
                         shared_settings.shared_dir() / shared_settings.FILE_NAME)


class JsonPlaceTest(unittest.TestCase):
    """控えのJSONを別の場所にする。右上の文字の正(マスタ)は梱包資材マスタのフォルダのまま。"""

    def setUp(self):
        _db.settings_file(self)
        self.master = _db.master_file(self)
        self.jdir = config.SHARED_DIR.parent / "json-place"
        self.jdir.mkdir()
        user_settings.save(config.KEY_SHARED_JSON_DIR, str(self.jdir))

    def test_writes_go_to_the_json_place_and_the_master_stays(self):
        result = qa_mark.change("NLM.JSON.QA", PASSWORD)
        self.assertTrue(result.ok, result.message)
        data = json.loads((self.jdir / shared_settings.FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(data["qa_mark"], "NLM.JSON.QA")
        self.assertFalse((config.SHARED_DIR / shared_settings.FILE_NAME).exists())
        self.assertEqual(_db.master_rows(self.master)[0][1], "NLM.JSON.QA", "正はマスタ")
        snap = shared_settings.read()
        self.assertEqual(snap.source, "shared")
        self.assertEqual(snap.to_dict()["share_json"], str(self.jdir / shared_settings.FILE_NAME))
        self.assertEqual(snap.to_dict()["share_path"], str(config.SHARED_DIR))
        self.assertFalse(list(self.jdir.glob("*.lock")), "鍵は外す")

    def test_admin_password_lives_in_the_json_place(self):
        self.assertTrue(admin_password.change(PASSWORD, "newpass12", "newpass12").ok)
        self.assertTrue(admin_password.verify("newpass12"))
        # 既定の場所(梱包資材マスタのフォルダ)へ戻すと、そこには変えたパスワードは無い
        user_settings.save(config.KEY_SHARED_JSON_DIR, "")
        shared_settings.reset_for_tests()
        self.assertFalse(admin_password.verify("newpass12"))
        self.assertTrue(admin_password.verify(PASSWORD))


class HistoryPlaceTest(unittest.TestCase):
    def setUp(self):
        _db.settings_file(self)
        _db.master_file(self)
        self.hdir = config.SHARED_DIR.parent / "history-place"
        self.hdir.mkdir()
        user_settings.save(config.KEY_HISTORY_DIR, str(self.hdir))

    def test_history_is_made_and_read_in_its_own_place(self):
        self.assertEqual(slip_history.history_path(), self.hdir / config.HISTORY_DB_NAME)
        slip_history.ensure_shared()
        self.assertTrue((self.hdir / config.HISTORY_DB_NAME).exists())
        self.assertFalse((config.SHARED_DIR / config.HISTORY_DB_NAME).exists())

    def test_master_browse_reads_history_from_its_place(self):
        view = master_browse.browse(master_browse.HISTORY)
        self.assertEqual(view.error, "")
        self.assertEqual(view.folder, str(self.hdir))
        self.assertEqual(view.path, str(self.hdir / config.HISTORY_DB_NAME))
        master = master_browse.browse("master")
        self.assertEqual(master.folder, str(config.SHARED_DIR), "梱包資材マスタはそのまま")


class PlaceRouteTest(unittest.TestCase):
    def setUp(self):
        _db.settings_file(self)
        _db.master_file(self)
        self.client, _conn = _client(self)
        self.h = HEADERS

    def post(self, **body):
        return self.client.post("/api/settings/shared-dir", json=body, headers=self.h)

    def test_needs_the_admin_password(self):
        for which in ("history", "json"):
            with self.subTest(which=which):
                res = self.post(which=which, value=str(config.SHARED_DIR.parent / "x"),
                                password="ちがう")
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.get_json()["error"]["field"], "password")

    def test_unknown_place_is_refused(self):
        self.assertEqual(self.post(which="lot", value="x", password=PASSWORD).status_code, 400)

    def test_save_says_what_is_there(self):
        place = config.SHARED_DIR.parent / "history-place"
        place.mkdir()
        body = self.post(which="history", value=str(place), password=PASSWORD).get_json()
        self.assertTrue(body["ok"])
        self.assertIn("まだ 梱包明細履歴.sqlite3 がありません", body["message"])
        self.assertEqual(body["history_dir"], str(place))
        self.assertEqual(user_settings.get(config.KEY_HISTORY_DIR), str(place))

        jplace = config.SHARED_DIR.parent / "json-place"
        jplace.mkdir()
        body = self.post(which="json", value=str(jplace), password=PASSWORD).get_json()
        self.assertIn("管理者パスワードは既定に戻り", body["message"])
        self.assertEqual(body["json_dir"], str(jplace))

        # 空にすると梱包資材マスタのフォルダと同じ
        body = self.post(which="history", value="", password=PASSWORD).get_json()
        self.assertEqual(body["history_dir"], str(config.SHARED_DIR))
        self.assertIn("梱包資材マスタのフォルダと同じ場所", body["message"])

    def test_old_calls_still_change_the_master_folder(self):
        place = config.SHARED_DIR.parent / "other-share"
        place.mkdir()
        body = self.post(value=str(place), password=PASSWORD).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(shared_settings.shared_dir(), place)

    def test_settings_screen_has_both_boxes(self):
        html = self.client.get(f"/meisai?t={TOKEN}").get_data(as_text=True)
        for key in ("History", "Json"):
            self.assertIn(f'id="set{key}Dir"', html)
            self.assertIn(f'id="set{key}Password"', html)
            self.assertIn(f'id="btn{key}Save"', html)
        state = self.client.get("/api/settings", headers=self.h).get_json()
        self.assertEqual(state["history_dir"], str(config.SHARED_DIR))
        self.assertEqual(state["json_dir"], str(config.SHARED_DIR))


class DistributionTest(unittest.TestCase):
    def test_both_places_can_be_distributed(self):
        keys = [k for k, _l, _d in distribution.ITEMS]
        self.assertIn(config.KEY_HISTORY_DIR, keys)
        self.assertIn(config.KEY_SHARED_JSON_DIR, keys)


if __name__ == "__main__":
    unittest.main()
