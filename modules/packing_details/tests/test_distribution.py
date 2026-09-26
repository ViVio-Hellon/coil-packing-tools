"""配布設定(梱包資材総合ツールから移したもの)

**守ること**
- 書き出す・読み込み直す・消すには管理者パスワードが要る
- 書き出すのは**この端末で変えた項目だけ**(既定のままの項目は入れない)
- 配った先は起動したとき、**その端末に無い項目だけ**読む(直した値を戻さない)
- 読み込み直すと、すでにある項目も上書きする
- 管理者パスワードと右上の文字は入れない(全ラインで共有しているため)
- 形の違う・知らない鍵は読まない。書きかけを残さない
"""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from modules.packing_details.meisai import config, distribution, user_settings

from . import _db
from .test_web import HEADERS, _client, _post

PASSWORD = config.ADMIN_PASSWORD


class _Base(unittest.TestCase):
    def setUp(self):
        _db.settings_file(self)
        self.dir = distribution.DIR
        shutil.rmtree(self.dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _written(self) -> dict:
        return json.loads(distribution.settings_path().read_text(encoding="utf-8"))

    def _place(self, settings: dict, **meta) -> None:
        self.dir.mkdir(parents=True)
        distribution.settings_path().write_text(json.dumps(
            {"format": distribution.FORMAT, "created_at": "2026-09-26 10:00:00",
             "created_on": "PC-A", "settings": settings, **meta},
            ensure_ascii=False), encoding="utf-8")


class ExportTest(_Base):
    def test_パスワードが無ければ書かない(self):
        user_settings.save(config.KEY_LOT_DB_DIR, r"C:\台帳")
        result = distribution.export("違う", [config.KEY_LOT_DB_DIR])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, distribution.REFUSE_NEED_PASSWORD)
        self.assertFalse(self.dir.exists())

    def test_変えた項目だけ書く_既定のままは言う(self):
        user_settings.save(config.KEY_LOT_DB_DIR, r"C:\台帳")
        user_settings.save(config.KEY_SHARED_DIR, r"\\srv\共有")
        keys = [k for k, _l, _d in distribution.ITEMS]
        result = distribution.export(PASSWORD, keys)
        self.assertTrue(result.ok, result.message)
        data = self._written()
        self.assertEqual(data["settings"], {config.KEY_LOT_DB_DIR: r"C:\台帳",
                                            config.KEY_SHARED_DIR: r"\\srv\共有"})
        self.assertIn("梱包課共有の仕掛フォルダ", result.message)   # 既定のままで入れていない
        readme = (self.dir / distribution.README_NAME).read_text(encoding="utf-8-sig")
        self.assertIn(r"C:\台帳", readme)
        self.assertIn("管理者パスワード", readme)                    # 入れていない理由
        self.assertFalse(self.dir.with_name(self.dir.name + ".作成中").exists())

    def test_パスワードや右上の文字は入れない(self):
        user_settings.save(config.KEY_LOT_DB_DIR, r"C:\台帳")
        user_settings.save(config.KEY_ADMIN_PASSWORD, "古い端末の値")
        result = distribution.export(PASSWORD, [config.KEY_LOT_DB_DIR,
                                                config.KEY_ADMIN_PASSWORD])
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, distribution.REFUSE_BAD_INPUT)
        self.assertNotIn(config.KEY_ADMIN_PASSWORD, distribution.ITEM_KEYS)
        self.assertNotIn(config.KEY_QA_MARK, distribution.ITEM_KEYS)

    def test_何も変えていなければ書けないと言う(self):
        result = distribution.export(PASSWORD, [config.KEY_LOT_DB_DIR])
        self.assertFalse(result.ok)
        self.assertIn("既定のまま", result.message)
        self.assertFalse(self.dir.exists())

    def test_書き直すと前の中身は置き換わる(self):
        user_settings.save(config.KEY_LOT_DB_DIR, r"C:\一回目")
        distribution.export(PASSWORD, [config.KEY_LOT_DB_DIR])
        (self.dir / "残りもの.txt").write_text("x", encoding="utf-8")
        user_settings.save(config.KEY_LOT_DB_DIR, r"C:\二回目")
        distribution.export(PASSWORD, [config.KEY_LOT_DB_DIR])
        self.assertEqual(self._written()["settings"][config.KEY_LOT_DB_DIR], r"C:\二回目")
        self.assertFalse((self.dir / "残りもの.txt").exists())


class ApplyTest(_Base):

    def test_起動時は無い項目だけ読む_直した値は戻さない(self):
        self._place({config.KEY_LOT_DB_DIR: r"\\配布\台帳",
                     config.KEY_KONPO_DB_DIR: r"\\配布\梱包",
                     "知らない鍵": "捨てる"})
        user_settings.save(config.KEY_KONPO_DB_DIR, r"D:\この端末で直した")
        result = distribution.apply_on_start()
        self.assertEqual(result.applied, ["仕掛台帳のフォルダ"])
        self.assertEqual(result.kept, ["梱包課共有の仕掛フォルダ"])
        self.assertEqual(user_settings.get(config.KEY_LOT_DB_DIR), r"\\配布\台帳")
        self.assertEqual(user_settings.get(config.KEY_KONPO_DB_DIR), r"D:\この端末で直した")
        self.assertIsNone(user_settings.get("知らない鍵"))
        # 2回目は何も読まない(もうある)
        self.assertEqual(distribution.apply_on_start().applied, [])

    def test_読み込み直すと上書きする_パスワードが要る(self):
        self._place({config.KEY_KONPO_DB_DIR: r"\\配布\梱包"})
        user_settings.save(config.KEY_KONPO_DB_DIR, r"D:\この端末で直した")
        self.assertFalse(distribution.reapply("違う").ok)
        self.assertEqual(user_settings.get(config.KEY_KONPO_DB_DIR), r"D:\この端末で直した")
        result = distribution.reapply(PASSWORD)
        self.assertTrue(result.ok)
        self.assertEqual(user_settings.get(config.KEY_KONPO_DB_DIR), r"\\配布\梱包")

    def test_形が違う_壊れている_なら読まない(self):
        self._place({config.KEY_LOT_DB_DIR: r"\\配布\台帳"}, format=99)
        self.assertIsNone(distribution.read())
        distribution.settings_path().write_text("{壊れた", encoding="utf-8")
        self.assertIsNone(distribution.read())
        self.assertEqual(distribution.apply_on_start().applied, [])
        self.assertIsNone(user_settings.get(config.KEY_LOT_DB_DIR))

    def test_消すにもパスワードが要る_端末の設定は残す(self):
        self._place({config.KEY_LOT_DB_DIR: r"\\配布\台帳"})
        distribution.apply_on_start()
        self.assertFalse(distribution.remove("違う").ok)
        self.assertTrue(self.dir.exists())
        self.assertTrue(distribution.remove(PASSWORD).ok)
        self.assertFalse(self.dir.exists())
        self.assertEqual(user_settings.get(config.KEY_LOT_DB_DIR), r"\\配布\台帳")

    def test_起動のとき取り込みより先に読む(self):
        """置き場所が配布設定で決まる端末で、既定の置き場所を取り込みに行かない。"""
        import inspect
        import modules.packing_details as module
        # 統合版では、起動時の初期化は機能の入口(`initialize`)にある
        source = inspect.getsource(module.initialize)
        self.assertIn("distribution.apply_on_start()", source)
        self.assertLess(source.index("distribution.apply_on_start()"),
                        source.index("_start_auto_import("))


class ReviewTest(_Base):
    """見直しで見つけたもの(VER 0.13.1)。どれも直す前のコードで落ちる。"""

    def test_空の値は無いのと同じ_以前の画面で空のまま保存した端末にも効く(self):
        """以前の設定画面は「保存して取り込み」のたびに空の欄も空のまま保存していた。"""
        user_settings.save(config.KEY_LOT_DB_DIR, "")
        user_settings.save(config.KEY_KONPO_DB_DIR, "   ")
        user_settings.save(config.KEY_SHARED_DIR, r"D:\この端末で決めた")
        self._place({config.KEY_LOT_DB_DIR: r"\\配布\台帳",
                     config.KEY_KONPO_DB_DIR: r"\\配布\梱包",
                     config.KEY_SHARED_DIR: r"\\配布\共有"})
        result = distribution.apply_on_start()
        self.assertEqual(result.applied, ["仕掛台帳のフォルダ", "梱包課共有の仕掛フォルダ"])
        self.assertEqual(result.kept, ["梱包資材マスタのフォルダ（共有）"])
        self.assertEqual(user_settings.get(config.KEY_LOT_DB_DIR), r"\\配布\台帳")
        self.assertEqual(user_settings.get(config.KEY_SHARED_DIR), r"D:\この端末で決めた")

    def test_空の値は書き出さない_画面でも既定のまま(self):
        user_settings.save(config.KEY_LOT_DB_DIR, "")
        user_settings.save(config.KEY_KONPO_DB_DIR, r"C:\梱包")
        items = {i["key"]: i for i in distribution.summary()["items"]}
        self.assertFalse(items[config.KEY_LOT_DB_DIR]["set"])
        self.assertEqual(items[config.KEY_LOT_DB_DIR]["value"], "（既定のまま）")
        result = distribution.export(PASSWORD, [config.KEY_LOT_DB_DIR, config.KEY_KONPO_DB_DIR])
        self.assertTrue(result.ok)
        self.assertEqual(self._written()["settings"], {config.KEY_KONPO_DB_DIR: r"C:\梱包"})
        self.assertIn("仕掛台帳のフォルダ", result.message)            # 入れていないと言う

    def test_形の違う値は読まない_理由を出す(self):
        self._place({config.KEY_AUTO_IMPORT: "false",                 # 文字の "false"
                     config.KEY_LOT_DB_DIR: 123,
                     config.KEY_SHARED_DIR: "",
                     config.KEY_KONPO_DB_DIR: r"  \\配布\梱包  "})
        result = distribution.apply_on_start()
        self.assertEqual(result.applied, ["梱包課共有の仕掛フォルダ"])
        self.assertEqual(user_settings.get(config.KEY_KONPO_DB_DIR), r"\\配布\梱包")   # 前後の空白は落とす
        self.assertIsNone(user_settings.get(config.KEY_AUTO_IMPORT))
        self.assertTrue(user_settings.auto_import_enabled())         # 既定のまま(「する」)
        self.assertIsNone(user_settings.get(config.KEY_LOT_DB_DIR))
        summary = distribution.summary()
        self.assertTrue(summary["exists"])
        self.assertEqual(len(summary["skipped"]), 3)
        self.assertTrue(any("起動時の自動取り込み" in x and "true か false" in x
                            for x in summary["skipped"]))
        # 本物の真偽なら読む
        self._place_again({config.KEY_AUTO_IMPORT: False})
        distribution.apply_on_start()
        self.assertFalse(user_settings.auto_import_enabled())

    def _place_again(self, settings):
        shutil.rmtree(self.dir)
        self._place(settings)

    def test_壊れた設定jsonは_なし_ではなく読めないと言う(self):
        self._place({config.KEY_LOT_DB_DIR: "x"})
        distribution.settings_path().write_text("{壊れ", encoding="utf-8")
        summary = distribution.summary()
        self.assertFalse(summary["exists"])
        self.assertIn("読めません", summary["problem"])
        result = distribution.reapply(PASSWORD)
        self.assertFalse(result.ok)
        self.assertIn("読めません", result.message)                   # 「置かれていません」と言わない
        distribution.settings_path().unlink()
        self.assertIn("設定.json がありません", distribution.summary()["problem"])
        shutil.rmtree(self.dir)
        self.assertEqual(distribution.summary()["problem"], "")        # 本当に無いときは何も言わない

    def _export(self, value):
        user_settings.save(config.KEY_LOT_DB_DIR, value)
        return distribution.export(PASSWORD, [config.KEY_LOT_DB_DIR])

    def _fail_rename(self, *, place_only_once: bool):
        real = Path.rename
        state = {"n": 0}

        def boom(path, target):
            if Path(target) == self.dir:
                state["n"] += 1
                if not place_only_once or state["n"] == 1:
                    raise PermissionError("使用中です")
            return real(path, target)

        Path.rename = boom
        self.addCleanup(setattr, Path, "rename", real)
        return lambda: setattr(Path, "rename", real)

    def test_書き出し直しに失敗しても_前の配布設定を壊さない(self):
        self.assertTrue(self._export(r"C:\一回目").ok)
        # 前のものをよけられない(フォルダを開いている等)
        real = Path.rename

        def busy(path, target):
            if Path(path) == self.dir:
                raise PermissionError("使用中です")
            return real(path, target)

        Path.rename = busy
        try:
            result = self._export(r"C:\二回目")
        finally:
            Path.rename = real
        self.assertFalse(result.ok)
        self.assertIn("前の配布設定はそのまま", result.message)
        self.assertEqual(distribution.read().settings[config.KEY_LOT_DB_DIR], r"C:\一回目")
        # 新しいものを置けない(1回だけ)→ 前のものを戻す
        restore = self._fail_rename(place_only_once=True)
        result = self._export(r"C:\三回目")
        restore()
        self.assertFalse(result.ok)
        self.assertEqual(distribution.read().settings[config.KEY_LOT_DB_DIR], r"C:\一回目")
        self.assertEqual(sorted(x.name for x in self.dir.parent.iterdir()), ["配布設定"])

    def test_戻すこともできなければ_場所を言い_次の書き出しで直る(self):
        self.assertTrue(self._export(r"C:\一回目").ok)
        restore = self._fail_rename(place_only_once=False)
        result = self._export(r"C:\二回目")
        restore()
        self.assertFalse(result.ok)
        self.assertIn("配布設定.前", result.message)
        self.assertIn("途中で止まりました", distribution.summary()["problem"])
        self.assertFalse(self.dir.with_name("配布設定.作成中").exists())   # 作りかけは残さない
        # もう一度書き出せば元どおり(前のものを消してしまわない)
        self.assertTrue(self._export(r"C:\三回目").ok)
        self.assertEqual(distribution.read().settings[config.KEY_LOT_DB_DIR], r"C:\三回目")
        self.assertEqual(sorted(x.name for x in self.dir.parent.iterdir()), ["配布設定"])

    def test_はじめに読むはメモ帳で読める形(self):
        self.assertTrue(self._export(r"C:\台帳").ok)
        raw = (self.dir / distribution.README_NAME).read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))            # BOM
        self.assertIn(b"\r\n", raw)
        text = raw.decode("utf-8-sig")
        self.assertTrue(text.startswith("コイル梱包明細打ち出しシステム 配布設定"))
        # 設定.json を手で開いて保存し直しても(BOM つき)読める
        data = distribution.settings_path().read_text(encoding="utf-8")
        distribution.settings_path().write_text(data, encoding="utf-8-sig")
        self.assertEqual(distribution.read().settings[config.KEY_LOT_DB_DIR], r"C:\台帳")


class WebTest(_Base):
    def setUp(self):
        super().setUp()
        self.client, self.conn = _client(self)

    def test_設定画面に出る_パスワードの値は出ない(self):
        user_settings.save(config.KEY_LOT_DB_DIR, r"C:\台帳")
        body = self.client.get("/api/settings", headers=HEADERS).get_json()
        dist = body["distribution"]
        self.assertFalse(dist["exists"])
        items = {i["key"]: i for i in dist["items"]}
        self.assertTrue(items[config.KEY_LOT_DB_DIR]["set"])
        self.assertFalse(items[config.KEY_KONPO_DB_DIR]["set"])
        self.assertEqual([n["label"] for n in dist["not_included"]],
                         ["管理者パスワード", "紙面の右上の文字"])
        self.assertNotIn(PASSWORD, json.dumps(body, ensure_ascii=False))

    def test_書き出す_読み込み直す_消す(self):
        user_settings.save(config.KEY_LOT_DB_DIR, r"C:\台帳")
        res = _post(self.client, "/api/settings/distribution/export",
                    {"items": [config.KEY_LOT_DB_DIR], "password": "違う"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json()["error"]["field"], "password")
        res = _post(self.client, "/api/settings/distribution/export",
                    {"items": [config.KEY_LOT_DB_DIR], "password": PASSWORD})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertTrue(body["distribution"]["exists"])
        self.assertEqual(body["distribution"]["contents"],
                         [{"label": "仕掛台帳のフォルダ", "value": r"C:\台帳"}])
        self.assertEqual(res.mimetype, "application/json")         # ダウンロードではない

        user_settings.save(config.KEY_LOT_DB_DIR, r"C:\直した")
        res = _post(self.client, "/api/settings/distribution/reapply", {"password": PASSWORD})
        self.assertEqual(res.get_json()["lot_db_dir_setting"], r"C:\台帳")

        res = _post(self.client, "/api/settings/distribution/export",
                    {"items": "形が違う", "password": PASSWORD})
        self.assertEqual(res.status_code, 400)
        res = _post(self.client, "/api/settings/distribution/remove", {"password": PASSWORD})
        self.assertFalse(res.get_json()["distribution"]["exists"])


if __name__ == "__main__":
    unittest.main()
