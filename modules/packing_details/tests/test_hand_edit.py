"""印刷の前の書き足し (サイズ・LOTNO)

VBA は紙がExcelシートだったので、刷る前にセルへ打ち込めた。
Web版は紙面の上で同じことをする(流用元の `printing.editable`)。

**守ること**
- 書き足さなければ、VBA の紙面と同じ(何も印字しない)
- 書き足した文字は「寸法」「検番」の行へ、A列の文字に揃えて出る
- 書き足した内容はその明細に残り、刷り直しても出る
- 作り直された明細には紛れ込まない
- 紙の上で切れる長さは受け取らない
"""
from __future__ import annotations

import re
import sqlite3
import unittest

from modules.packing_details.meisai import db, history_repo, meisai_service, report, strand_service
from modules.packing_details.meisai.meisai_service import Output

from . import _db


def _board(lot_no: str = "L5160Z0") -> strand_service.Board:
    board = strand_service.Board(lot_no=lot_no, zen_kotei=24, jou_su=2)
    board.build_strands()
    board.weights[:] = [250, 248]
    board.set_stack_max(2)
    board.stack("1-12")
    board.stack("2-12")
    return board


def _hand_cell(html: str) -> str:
    """B〜C列の書き足しの欄(1面め)。"""
    m = re.search(r'<td colspan="2" class="hand">(.*?)</td>', html, re.S)
    assert m, "書き足しの欄が無い"
    return m.group(1)


class SlipTest(unittest.TestCase):
    """紙面の組み立て。"""

    def _output(self, **kw) -> Output:
        return Output(lot_no="L5160Z0", seq_no=1, keys=["1-12", "2-12"],
                      weights=[250, 248], row_id=7, **kw)

    def test_書き足さなければ何も印字しない(self):
        """**VBA の紙面と同じ。** B2:F4 は現物のラベルを貼る場所。"""
        cell = _hand_cell(report.build_slip(self._output()))
        self.assertEqual(re.sub(r"<[^>]+>", "", cell), "")

    def test_サイズは寸法の行_LOTNOは検番の行(self):
        """A列の「種類質別 / 寸法 / 検番」と同じ順に3行組む。"""
        cell = _hand_cell(report.build_slip(
            self._output(hand_size="1.985×104.0", hand_lotno="L5160Z0")))
        lines = re.findall(r'<span class="hl">(.*?)</span>', cell)
        self.assertEqual(lines, ["", "1.985×104.0", "L5160Z0"])

    def test_小ラベル添付はD3のまま(self):
        """VBA `Cells(3, 4)`。**書き足しで押し出さない。**"""
        html = report.build_slip(self._output(hand_size="1.985×104.0"))
        self.assertIn('<td colspan="2" class="hand">', html)
        self.assertRegex(html, r'</td><td class="paste">小ラベル添付</td>'
                               r'<td colspan="2"></td></tr>')

    def test_直せる紙面では欄の名前が明細ごとに違う(self):
        """まとめて開くと何枚ぶんもの欄が1つの画面に並ぶ。混ざらないように。"""
        html = report.build_slip(self._output(), edit=True)
        self.assertIn('data-edit="7.size"', html)
        self.assertIn('data-edit="7.lotno"', html)

    def test_読むだけの紙面には直せる欄を出さない(self):
        html = report.build_slip(self._output(hand_size="1.985"))
        self.assertNotIn("contenteditable", html)

    def test_書き足しは文字として出す(self):
        """打ち込まれたものを HTML として解釈しない。"""
        html = report.build_slip(self._output(hand_lotno="<b>x</b>"), edit=True)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("<b>x</b>", html)

    def test_直せる紙面に印刷の案内と送り先が付く(self):
        html = report.render([self._output()], edit_url="/report/L5160Z0/edits?t=x")
        self.assertIn('"/report/L5160Z0/edits?t=x"', html)
        self.assertIn("サイズ・LOTNO", html)
        self.assertIn("window.print()", html)

    def test_案内と点線は紙に出ない(self):
        """**画面のときだけ。** 紙には点線も案内も出さない。"""
        html = report.render([self._output()], edit_url="/x")
        self.assertIn("@media print { .edit { border: 0; } .editbar { display: none; } }",
                      html)
        # 空欄の案内(「サイズ」「LOTNO」)も画面のときだけ
        screen_css = html.split("@media screen {", 2)[2].split("@media print", 1)[0]
        self.assertIn(".edit:empty::before", screen_css)


class SaveTest(unittest.TestCase):
    """書き足しを覚える。"""

    def setUp(self):
        self.conn = _db.case_db(self)
        meisai_service.output(self.conn, _board(), confirm=True)
        self.out = meisai_service.find_output(self.conn, "L5160Z0", 1)

    def _edits(self, size="", lotno="", row_id=None):
        rid = self.out.row_id if row_id is None else row_id
        return {f"{rid}.size": size, f"{rid}.lotno": lotno}

    def test_書き足しがその明細に残る(self):
        meisai_service.save_hand_edits(
            self.conn, "L5160Z0", self._edits("1.985×104.0", "L5160Z0"))
        again = meisai_service.find_output(self.conn, "L5160Z0", 1)
        self.assertEqual((again.hand_size, again.hand_lotno),
                         ("1.985×104.0", "L5160Z0"))

    def test_刷り直しても出る(self):
        meisai_service.save_hand_edits(self.conn, "L5160Z0", self._edits("2.0×104"))
        again = meisai_service.find_output(self.conn, "L5160Z0", 1)
        self.assertIn("2.0×104", report.render([again]))

    def test_空にすれば消える(self):
        meisai_service.save_hand_edits(self.conn, "L5160Z0", self._edits("x", "y"))
        meisai_service.save_hand_edits(self.conn, "L5160Z0", self._edits("", ""))
        again = meisai_service.find_output(self.conn, "L5160Z0", 1)
        self.assertEqual((again.hand_size, again.hand_lotno), ("", ""))

    def test_改行やタブは空白にする(self):
        """1行の欄。改行が入ると紙の上でずれる。"""
        meisai_service.save_hand_edits(self.conn, "L5160Z0",
                                       self._edits(" 1.985\n×\t104 "))
        again = meisai_service.find_output(self.conn, "L5160Z0", 1)
        self.assertEqual(again.hand_size, "1.985 × 104")

    def test_長すぎれば断る(self):
        """**切れて刷られるより、入れる前に断る。**"""
        too_long = "X" * (meisai_service.HAND_MAX_LEN + 1)
        with self.assertRaises(meisai_service.HandEditError) as caught:
            meisai_service.save_hand_edits(self.conn, "L5160Z0", self._edits(too_long))
        self.assertIn("サイズ", str(caught.exception))
        self.assertEqual(caught.exception.status, 400)

    def test_ちょうど上限までは受ける(self):
        just = "X" * meisai_service.HAND_MAX_LEN
        meisai_service.save_hand_edits(self.conn, "L5160Z0", self._edits(just))

    def test_知らない欄は断る(self):
        for edits in ({f"{self.out.row_id}.kenban": "x"}, {"size": "x"},
                      {"abc.size": "x"}):
            with self.subTest(edits=edits):
                with self.assertRaises(meisai_service.HandEditError):
                    meisai_service.save_hand_edits(self.conn, "L5160Z0", edits)

    def test_作り直された明細には書かない(self):
        """No指定で上書きすると行が作り直される。**古い紙面から紛れ込ませない。**"""
        old_id = self.out.row_id
        meisai_service.output(self.conn, _board(), specify_no=1, confirm=True)
        with self.assertRaises(meisai_service.HandEditError) as caught:
            meisai_service.save_hand_edits(self.conn, "L5160Z0",
                                           self._edits("x", row_id=old_id))
        self.assertEqual(caught.exception.status, 409)
        fresh = meisai_service.find_output(self.conn, "L5160Z0", 1)
        self.assertEqual(fresh.hand_size, "")

    def test_別のロットの明細には書かない(self):
        with self.assertRaises(meisai_service.HandEditError):
            meisai_service.save_hand_edits(self.conn, "L9999Z9", self._edits("x"))

    def test_1枚でも書けなければ1枚も書かない(self):
        """まとめて開いた紙面で一部だけ残ると、刷った紙と記録が食い違う。"""
        edits = {**self._edits("残らないはず"), "999999.size": "x"}
        with self.assertRaises(meisai_service.HandEditError):
            meisai_service.save_hand_edits(self.conn, "L5160Z0", edits)
        again = meisai_service.find_output(self.conn, "L5160Z0", 1)
        self.assertEqual(again.hand_size, "")

    def test_ロットを切り替えれば消える(self):
        """明細の行と一緒に消える(流用元の「別のロットを検索するまで」)。"""
        from modules.packing_details.meisai import session
        meisai_service.save_hand_edits(self.conn, "L5160Z0", self._edits("x"))
        session.close_lot(self.conn, "L5160Z0")
        self.assertIsNone(meisai_service.find_output(self.conn, "L5160Z0", 1))


class MigrationTest(unittest.TestCase):
    """現場の古いDBに列を足す。**明細を1枚も消さない。**"""

    def test_古い形の明細出力に列が足される(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.executescript(
            "CREATE TABLE 明細出力 (管理番号 INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ロット番号 TEXT NOT NULL, No INTEGER NOT NULL,"
            " 副番の並び TEXT NOT NULL DEFAULT '', 重量 TEXT NOT NULL DEFAULT '',"
            " 出力日時 TEXT NOT NULL DEFAULT '');"
            "INSERT INTO 明細出力 (ロット番号, No, 副番の並び, 重量)"
            " VALUES ('L5160Z0', 1, '1-12,2-12', '250,248');")
        db.apply_schema(conn)
        db.apply_schema(conn)                   # 2度当てても同じ
        out = meisai_service.find_output(conn, "L5160Z0", 1)
        self.assertIsNotNone(out, "前からある明細が消えていない")
        self.assertEqual((out.hand_size, out.hand_lotno), ("", ""))
        self.assertEqual(out.keys, ["1-12", "2-12"])


if __name__ == "__main__":
    unittest.main()
