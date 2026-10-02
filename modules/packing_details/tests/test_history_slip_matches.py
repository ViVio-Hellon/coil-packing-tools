"""履歴から呼び出した紙面が、出力したときの紙面と同じか(VER 0.13.10。現場の指摘)。

    履歴から呼び出した紙面が正しいかのチェックをお願いします

画面から出力し、紙面で書き足し(サイズ・LOTNO)、紙面を開いた ── その紙面と、
明細の履歴(全ライン・共有)から作り直した紙面の**明細の表**を突き合わせる。
表題(ロット-No)・右上の文字・書き足し・15行の副番と重量が1字も違わないこと。
"""
from __future__ import annotations

import re
import unittest
from datetime import date

from modules.packing_details.meisai import meisai_service, qa_mark, shared_settings, slip_history

from . import _db
from .test_web import HEADERS, TOKEN, _client, _post, _seed

PASSWORD = __import__("modules.packing_details.meisai.config",
                      fromlist=["ADMIN_PASSWORD"]).ADMIN_PASSWORD


def tables(html: str) -> list[str]:
    """紙面の明細の表(1枚ずつ)。書き足しの欄(画面でだけ直せる印)は中身の文字にする。"""
    found = re.findall(r'<table class="meisai">.*?</table>', html, flags=re.S)
    return [re.sub(r'<span class="edit"[^>]*>(.*?)</span>', r"\1", t) for t in found]


def cells(table: str) -> list[str]:
    return [re.sub(r"<[^>]+>", "", c) for c in re.findall(r"<td[^>]*>.*?</td>", table, flags=re.S)]


class HistorySlipMatchesTest(unittest.TestCase):
    def setUp(self):
        _db.settings_file(self)
        _db.master_file(self)
        self.client, self.conn = _client(self)
        _seed(self.conn)
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        _post(self.client, "/api/weights", {"weights": ["250", "1248"]})
        _post(self.client, "/api/strands", {})

    def output(self, keys):
        # 積み条数ぶん積むと出力できる(VBA と同じ)
        _post(self.client, "/api/stack-max", {"value": str(len(keys))})
        for key in keys:
            _post(self.client, "/api/stack", {"key": key})
        res = _post(self.client, "/api/output", {"confirm": True})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        return res.get_json()

    def write_hand(self, seq_no, size, lotno):
        out = meisai_service.find_output(self.conn, "L5160Z0", seq_no)
        res = self.client.post(f"/report/L5160Z0/edits?t={TOKEN}", headers=HEADERS,
                               json={"edits": {f"{out.row_id}.size": size,
                                               f"{out.row_id}.lotno": lotno}})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))

    def original(self, seq_no):
        res = self.client.get(f"/report/L5160Z0/{seq_no}?t={TOKEN}")
        self.assertEqual(res.status_code, 200)
        return res.get_data(as_text=True)

    def from_history(self, *seq_nos):
        slip_history.send_pending(self.conn)
        today = date.today().isoformat()
        body = _post(self.client, "/api/history/search",
                     {"from": today, "to": today, "lot_no": "L5160Z0"}).get_json()
        by_no = {r["No"]: r["送信ID"] for r in body["rows"] if r["状態"] == "有効"}
        ids = ",".join(by_no[n] for n in seq_nos)
        res = self.client.get(f"/report/history?ids={ids}&t={TOKEN}")
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        self.assertEqual(body["source"], "shared", "全ラインの履歴から")
        return res.get_data(as_text=True)

    def test_one_slip_with_hand_entries_is_the_same(self):
        self.output(["1-10", "2-8", "1-3", "2-12", "1-1"])
        self.write_hand(1, "2.0x104", "A1234-5")
        a = tables(self.original(1))
        b = tables(self.from_history(1))
        self.assertEqual(len(a), 1)
        self.assertEqual(a, b)
        c = cells(b[0])
        self.assertIn("L5160Z0-No1  梱包明細表", c)
        self.assertIn("2.0x104", "".join(c))
        self.assertIn("A1234-5", "".join(c))
        self.assertIn("1248.0Kg", c, "丈2の重量")
        self.assertEqual(c.count("250.0Kg"), 3, "丈1の3本")

    def test_several_slips_keep_their_own_contents(self):
        self.output(["1-10", "2-8"])
        self.output(["1-4", "2-2", "2-6"])
        self.write_hand(2, "1.9x100", "")
        first, second = tables(self.original(1)), tables(self.original(2))
        both = tables(self.from_history(1, 2))
        self.assertEqual(both, first + second)

    def test_hand_entries_after_sending_reach_the_shared_history(self):
        """送ったあとで紙面に書き足しても、全ラインの履歴の紙面に映る。"""
        self.output(["1-10", "2-8"])
        slip_history.send_pending(self.conn)
        self.write_hand(1, "2.0x104", "B9999")
        a = tables(self.original(1))
        b = tables(self.from_history(1))
        self.assertEqual(a, b)
        self.assertIn("B9999", b[0])

    def test_the_top_right_text_is_the_one_printed_then(self):
        """紙面を開いたあとで右上の文字を変えても、履歴の紙面は**そのとき刷った文字**。"""
        self.output(["1-10", "2-8"])
        before = tables(self.original(1))
        self.assertTrue(qa_mark.change("NLM.CHANGED.QA", PASSWORD).ok)
        shared_settings.reset_for_tests()
        after = tables(self.from_history(1))
        self.assertEqual(after, before)
        self.assertIn("NLM.NAGOYA.QA", after[0])


if __name__ == "__main__":
    unittest.main()
