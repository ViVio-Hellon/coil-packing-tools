"""紙面の右上の文字 (既定 NLM.NAGOYA.QA) と管理者パスワード

**守ること**
- 変えていなければ VBA と同じ `NLM.NAGOYA.QA`
- 変えるにも既定に戻すにも、管理者パスワードが要る。断ったら何も変えない
- 変えたら**次に開く紙面から**新しい文字で刷られる(刷り直しも)
- パスワードは平文で持たない・ログにも応答にも出さない

全ラインで共有すること(別のPC・共有に届かないとき)は `test_shared_settings`。
"""
from __future__ import annotations

import json
import logging
import unittest

from modules.packing_details.meisai import admin_password, config, qa_mark, report, shared_settings
from modules.packing_details.meisai.meisai_service import Output

from . import _db
from .test_web import HEADERS, TOKEN, _client, _post, _seed

PASSWORD = config.ADMIN_PASSWORD         # 一度も変えていない端末で通る値


def _output() -> Output:
    return Output(lot_no="L5160Z0", seq_no=1, keys=["1-10", "2-8"],
                  weights=[250, 248])


class AdminPasswordTest(unittest.TestCase):

    def setUp(self):
        self.path = _db.settings_file(self)

    def test_変えていなければ既定で通る(self):
        self.assertTrue(admin_password.verify(PASSWORD))
        self.assertFalse(admin_password.verify(PASSWORD + "x"))
        self.assertFalse(admin_password.verify(""))
        self.assertFalse(admin_password.is_custom())

    def test_変えたら新しいほうだけ通る(self):
        result = admin_password.change(PASSWORD, "abcd1", "abcd1")
        self.assertTrue(result.ok, result.message)
        self.assertTrue(admin_password.verify("abcd1"))
        self.assertFalse(admin_password.verify(PASSWORD))
        self.assertTrue(admin_password.is_custom())

    def test_平文で持たない(self):
        """共有にも、この端末の写しにも平文は無い。"""
        admin_password.change(PASSWORD, "himitsu9", "himitsu9")
        shared = shared_settings.shared_path().read_text(encoding="utf-8")
        self.assertNotIn("himitsu9", shared)
        self.assertIn("pbkdf2$", shared)
        self.assertNotIn("himitsu9", self.path.read_text(encoding="utf-8"))

    def test_いまのパスワードを知らなければ変えられない(self):
        result = admin_password.change("違う", "abcd1", "abcd1")
        self.assertEqual(result.reason, admin_password.REFUSE_WRONG)
        self.assertTrue(admin_password.verify(PASSWORD))

    def test_短い_確認と違う_同じ_は断る(self):
        for new, confirm, reason in (
                ("abc", "abc", admin_password.REFUSE_TOO_SHORT),
                ("abcd1", "abcd2", admin_password.REFUSE_MISMATCH),
                (PASSWORD, PASSWORD, admin_password.REFUSE_SAME)):
            with self.subTest(new=new):
                result = admin_password.change(PASSWORD, new, confirm)
                self.assertEqual(result.reason, reason)

    def test_既定に戻せる(self):
        admin_password.change(PASSWORD, "abcd1", "abcd1")
        self.assertFalse(admin_password.reset(PASSWORD).ok)   # もう既定ではない
        self.assertTrue(admin_password.reset("abcd1").ok)
        self.assertTrue(admin_password.verify(PASSWORD))

    def test_ログに撹拌した値も出さない(self):
        """共有の写し(`shared_cache`)にも撹拌した値が入る。それも伏せる
        (伏せ忘れていたのを、この試験が見つけた)。"""
        logger = logging.getLogger("meisai")
        with self.assertLogs(logger, level="INFO") as logs:
            admin_password.change(PASSWORD, "abcd1", "abcd1")
        joined = "\n".join(logs.output)
        self.assertNotIn("pbkdf2", joined)
        self.assertIn("(伏せます)", joined)


class QaMarkTest(unittest.TestCase):

    def setUp(self):
        self.path = _db.settings_file(self)

    def test_変えていなければVBAと同じ(self):
        self.assertEqual(qa_mark.current(), "NLM.NAGOYA.QA")
        self.assertEqual(qa_mark.DEFAULT, "NLM.NAGOYA.QA")
        self.assertFalse(qa_mark.is_custom())

    def test_パスワードが違えば何も変えない(self):
        for password in ("", "違う", PASSWORD + " "):
            with self.subTest(password=password):
                result = qa_mark.change("NLM.TEST.QA", password)
                self.assertEqual(result.reason, qa_mark.REFUSE_NEED_PASSWORD)
                self.assertEqual(qa_mark.current(), "NLM.NAGOYA.QA")
        # 共有には1バイトも書いていない(フォルダも作っていない)
        self.assertFalse(shared_settings.shared_path().parent.exists())

    def test_パスワードが合えば変わる(self):
        result = qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(qa_mark.current(), "NLM.TEST.QA")
        self.assertTrue(qa_mark.is_custom())

    def test_空にはできない(self):
        for value in ("", "   ", "\n\t"):
            with self.subTest(value=value):
                self.assertEqual(qa_mark.change(value, PASSWORD).reason,
                                 qa_mark.REFUSE_EMPTY)

    def test_長すぎれば理由を言って断る(self):
        """**黙って削らない。** 削ると、打った文字と刷られた文字が食い違う。"""
        ok = "あ" * qa_mark.MAX_LEN
        self.assertTrue(qa_mark.change(ok, PASSWORD).ok)
        result = qa_mark.change(ok + "あ", PASSWORD)
        self.assertEqual(result.reason, qa_mark.REFUSE_TOO_LONG)
        self.assertIn(f"{qa_mark.MAX_LEN + 1}文字", result.message)
        self.assertEqual(qa_mark.current(), ok)

    def test_改行とタブは空白に_見えない文字は落とす(self):
        qa_mark.change(" NLM\nNAGOYA\tQA​\x07 ", PASSWORD)
        self.assertEqual(qa_mark.current(), "NLM NAGOYA QA")

    def test_既定に戻すにもパスワードが要る(self):
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        self.assertFalse(qa_mark.reset("違う").ok)
        self.assertEqual(qa_mark.current(), "NLM.TEST.QA")
        self.assertTrue(qa_mark.reset(PASSWORD).ok)
        self.assertEqual(qa_mark.current(), "NLM.NAGOYA.QA")

    def test_変えたパスワードで守られる(self):
        admin_password.change(PASSWORD, "abcd1", "abcd1")
        self.assertFalse(qa_mark.change("X", PASSWORD).ok)
        self.assertTrue(qa_mark.change("X", "abcd1").ok)

    def test_共有を手で壊しても刷れない値は刷らない(self):
        """手で書き換えて壊れても、右上が空の紙・はみ出した紙を黙って出さない。"""
        path = shared_settings.shared_path()
        path.parent.mkdir()
        for stored in (["配列"], 123, "あ" * (qa_mark.MAX_LEN + 1), "\x07"):
            with self.subTest(stored=stored):
                path.write_text(json.dumps({"qa_mark": stored}), encoding="utf-8")
                with self.assertLogs("meisai.qa_mark", level="WARNING"):
                    self.assertEqual(qa_mark.current(), "NLM.NAGOYA.QA")

    def test_紙面に刷られる(self):
        """**開くたびにその場で読む。** 前に作った明細でも新しい文字。"""
        out = _output()
        self.assertIn(">NLM.NAGOYA.QA<", report.render([out]))
        qa_mark.change("NLM.TEST.QA", PASSWORD)
        html = report.render([out])
        self.assertIn(">NLM.TEST.QA<", html)
        self.assertNotIn("NLM.NAGOYA.QA", html)

    def test_紙面では文字として出す(self):
        """`<` や `&` を入れても、紙面のHTMLを壊さない。"""
        qa_mark.change("<b>QA&</b>", PASSWORD)
        html = report.render([_output()])
        self.assertIn("&lt;b&gt;QA&amp;&lt;/b&gt;", html)
        self.assertNotIn("<b>QA&</b>", html)


class QaFitTest(unittest.TestCase):
    """長い文字も欄からはみ出させない。"""

    def test_欄の幅は紙の上の値で持つ(self):
        """左半分 148.5mm − 余白 → 明細 約138mm。E〜F列はその 24/70.75。"""
        self.assertAlmostEqual(report._qa_fit_mm(), 42.8, delta=0.2)

    def test_紙面に合わせる仕掛けが付く(self):
        _db.settings_file(self)
        html = report.render([_output()])
        self.assertIn('<span class="qa-text">NLM.NAGOYA.QA</span>', html)
        self.assertIn(f"FIT = {report._qa_fit_mm():.2f} * MM", html)
        self.assertIn('"meisai.print.v1"', html)
        # 書き足し欄の無い紙面(読むだけ)にも付く
        self.assertIn("qa-text", html)

    def test_欄の外へ出さない囲い(self):
        css = report._sheet_css()
        start = css.index(".meisai .r-title .qa{")
        self.assertIn("overflow:hidden", css[start:css.index("}", start)])


class QaMarkWebTest(unittest.TestCase):
    """画面から変えて、**すぐ次の紙面**に出るか。"""

    def setUp(self):
        self.path = _db.settings_file(self)
        self.client, self.conn = _client(self)
        _seed(self.conn)
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        _post(self.client, "/api/weights", {"weights": ["250", "248"]})
        _post(self.client, "/api/strands", {})
        _post(self.client, "/api/stack-max", {"value": "2"})
        for key in ("1-10", "2-8"):
            _post(self.client, "/api/stack", {"key": key})
        _post(self.client, "/api/output", {"confirm": True})

    def _slip(self) -> str:
        return self.client.get(f"/report/L5160Z0/1?t={TOKEN}").get_data(as_text=True)

    def _set(self, **body):
        return _post(self.client, "/api/settings/qa-mark", body)

    def test_設定に今の文字が出る_パスワードは出ない(self):
        # 共有の様子は後から埋める口(`/api/settings/share`)に出る
        body = self.client.get("/api/settings/share", headers=HEADERS).get_json()
        self.assertEqual(body["qa_mark"], "NLM.NAGOYA.QA")
        self.assertEqual(body["qa_mark_default"], "NLM.NAGOYA.QA")
        self.assertFalse(body["qa_mark_custom"])
        self.assertEqual(body["qa_mark_max"], qa_mark.MAX_LEN)
        self.assertNotIn(PASSWORD, json.dumps(body, ensure_ascii=False))
        local = self.client.get("/api/settings", headers=HEADERS).get_json()
        self.assertEqual(local["qa_mark_max"], qa_mark.MAX_LEN)
        self.assertNotIn(PASSWORD, json.dumps(local, ensure_ascii=False))

    def test_パスワード無しでは変えられない(self):
        for body in ({"value": "NLM.TEST.QA"},
                     {"value": "NLM.TEST.QA", "password": "違う"}):
            with self.subTest(body=body):
                res = self._set(**body)
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.get_json()["error"]["code"], "need_password")
                self.assertEqual(res.get_json()["error"]["field"], "password")
        self.assertIn(">NLM.NAGOYA.QA<", self._slip())

    def test_変えたらすぐ次の紙面に出る(self):
        """**このために作った。** 変えた直後に開いた紙面が新しい文字。"""
        self.assertIn(">NLM.NAGOYA.QA<", self._slip())
        res = self._set(value="NLM.TEST.QA", password=PASSWORD)
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertEqual(res.get_json()["qa_mark"], "NLM.TEST.QA")
        html = self._slip()
        self.assertIn('<span class="qa-text">NLM.TEST.QA</span>', html)
        self.assertNotIn("NLM.NAGOYA.QA", html)

    def test_まとめて刷る紙面もすべて変わる(self):
        _post(self.client, "/api/stack", {"key": "1-9"})
        _post(self.client, "/api/stack", {"key": "2-7"})
        _post(self.client, "/api/output", {"confirm": True})
        self._set(value="NLM.TEST.QA", password=PASSWORD)
        html = self.client.get(f"/report/L5160Z0?nos=1,2&t={TOKEN}").get_data(as_text=True)
        self.assertEqual(html.count('<span class="qa-text">NLM.TEST.QA</span>'), 2)
        self.assertNotIn("NLM.NAGOYA.QA", html)

    def test_既定に戻したら次の紙面も戻る(self):
        self._set(value="NLM.TEST.QA", password=PASSWORD)
        self.assertEqual(self._set(reset=True, password="違う").status_code, 403)
        self.assertIn(">NLM.TEST.QA<", self._slip())
        res = self._set(reset=True, password=PASSWORD)
        self.assertEqual(res.status_code, 200)
        self.assertIn(">NLM.NAGOYA.QA<", self._slip())

    def test_値の誤りは400で理由を返す(self):
        res = self._set(value="あ" * (qa_mark.MAX_LEN + 1), password=PASSWORD)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"]["code"], "too_long")
        self.assertEqual(res.get_json()["error"]["field"], "value")

    def test_開いたままの紙面が今の文字を聞き直せる(self):
        """知らせを取りこぼした紙面が、前に戻ったときに追いつく口。"""
        url = f"/report/qa-mark?t={TOKEN}"
        self.assertEqual(self.client.get(url).get_json()["qa_mark"], "NLM.NAGOYA.QA")
        self._set(value="NLM.TEST.QA", password=PASSWORD)
        self.assertEqual(self.client.get(url).get_json()["qa_mark"], "NLM.TEST.QA")

    def test_聞き直す口もトークンが要る(self):
        self.assertEqual(self.client.get("/report/qa-mark").status_code, 401)

    def test_聞き直す口は画面を持っていなくても通る(self):
        """紙面は別のタブで、画面を持っていない(書き足しの送り先と同じ扱い)。"""
        from modules.packing_details.meisai import screen
        screen.hand_over()
        res = self.client.get(f"/report/qa-mark?t={TOKEN}")
        self.assertEqual(res.status_code, 200)

    def test_管理者パスワードを変えたら新しいほうで守られる(self):
        res = _post(self.client, "/api/settings/admin-password",
                    {"current": PASSWORD, "new": "abcd1", "confirm": "abcd1"})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertTrue(res.get_json()["admin_custom"])
        self.assertNotIn("pbkdf2", res.get_data(as_text=True))
        self.assertEqual(self._set(value="A", password=PASSWORD).status_code, 403)
        self.assertEqual(self._set(value="A", password="abcd1").status_code, 200)

    def test_管理者パスワードの変更も今のが違えば断る(self):
        res = _post(self.client, "/api/settings/admin-password",
                    {"current": "違う", "new": "abcd1", "confirm": "abcd1"})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json()["error"]["field"], "current")
        self.assertTrue(admin_password.verify(PASSWORD))

    def test_置き場所の保存にはパスワードは要らない(self):
        """守る対象を増やすほど、現場はパスワードを紙に貼る。"""
        res = _post(self.client, "/api/settings", {"key": "lot_db_dir", "value": ""})
        self.assertEqual(res.status_code, 200)

    def test_画面を持っていない古いタブからは変えられない(self):
        """設定の変更は `/api/` なので、引き継がれた画面からは断られる。"""
        from modules.packing_details.meisai import screen
        screen.hand_over()
        screen.claim("別の画面")
        res = self._set(value="NLM.TEST.QA", password=PASSWORD)
        self.assertEqual(res.status_code, 409)
        self.assertFalse(shared_settings.shared_path().exists())


if __name__ == "__main__":
    unittest.main()
