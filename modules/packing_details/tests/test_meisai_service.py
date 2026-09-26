"""出力・副番の重複・連番・スナップショット (RULE-06〜08)"""
from __future__ import annotations

import unittest

from modules.packing_details.meisai import config, history_repo, meisai_service, report
from modules.packing_details.meisai.history_repo import Snapshot
from modules.packing_details.meisai.meisai_service import (CONFIRM_BEYOND_SEQ, CONFIRM_DUPLICATE,
                                   CONFIRM_OVERWRITE, REFUSE_INCOMPLETE,
                                   REFUSE_NO_WEIGHT, Output)
from modules.packing_details.meisai.strand_service import Board, RefusedError

from . import _db


def board(lot="L5160Z0", zen=24, jou=2, stack=6, weights=(250, 248)) -> Board:
    b = Board(lot_no=lot, zen_kotei=zen, jou_su=jou, weights=list(weights))
    b.build_strands()
    b.set_stack_max(stack)
    return b


def filled(lot="L5160Z0", keys=("1-10", "1-5", "2-8", "2-11", "2-12", "2-9")) -> Board:
    b = board(lot=lot, stack=len(keys))
    for key in keys:
        b.stack(key)
    return b


class WeightTest(unittest.TestCase):
    """RULE-06: 重量の読み取り。"""

    def test_整数として読む(self):
        # 入力欄が数字以外を弾く(VBA clsWeightCtrl)ので必ず整数
        self.assertEqual(meisai_service.read_weights(["250", "248"], 2), [250, 248])

    def test_未入力は断る(self):
        for values in ([""], ["250", ""], ["250"]):
            with self.subTest(values=values), self.assertRaises(RefusedError) as cm:
                meisai_service.read_weights(values, 2)
            self.assertEqual(cm.exception.reason, REFUSE_NO_WEIGHT)

    def test_数値でなければ断る(self):
        with self.assertRaises(RefusedError) as cm:
            meisai_service.read_weights(["abc", "248"], 2)
        self.assertEqual(cm.exception.reason, REFUSE_NO_WEIGHT)
        self.assertIn("丈1", cm.exception.message)

    def test_余分な欄は無視する(self):
        self.assertEqual(meisai_service.read_weights(["250", "248", "999"], 2),
                         [250, 248])


class OutputTest(unittest.TestCase):
    """出力そのもの。"""

    def setUp(self):
        self.conn = _db.case_db(self)

    def test_積み上げが埋まっていなければ断る(self):
        result = meisai_service.output(self.conn, board())
        self.assertFalse(result.ok)
        self.assertEqual(result.refuse, REFUSE_INCOMPLETE)

    def test_出力すると連番が1から始まる(self):
        result = meisai_service.output(self.conn, filled())
        self.assertTrue(result.ok)
        self.assertEqual(result.seq_no, 1)

    def test_出力のたびに連番が進む(self):
        self.assertEqual(meisai_service.output(self.conn, filled()).seq_no, 1)
        self.assertEqual(meisai_service.output(self.conn, filled(), confirm=True).seq_no, 2)

    def test_出力すると印刷済フラグがおりる(self):
        history_repo.set_printed(self.conn, True)
        meisai_service.output(self.conn, filled())
        self.assertFalse(history_repo.is_printed(self.conn))

    def test_副番が履歴へ入る(self):
        meisai_service.output(self.conn, filled())
        self.assertEqual(history_repo.fuban_count(self.conn), 6)
        self.assertTrue(history_repo.is_duplicate(self.conn, "L5160Z0-1-10"))

    def test_行数が紙を超えたら断る(self):
        # 積み条数の上限が15なのでふつうは起きないが、念のため
        b = board(zen=40, jou=2, stack=config.STACK_LIMIT)
        for key in b.all_keys[:config.STACK_LIMIT]:
            b.stack(key)
        b.slots.append("1-20")          # 無理やり16本目
        result = meisai_service.output(self.conn, b)
        self.assertFalse(result.ok)
        self.assertEqual(result.refuse, meisai_service.REFUSE_TOO_MANY)


class DuplicateTest(unittest.TestCase):
    """RULE-07: 副番の重複。"""

    def setUp(self):
        self.conn = _db.case_db(self)
        meisai_service.output(self.conn, filled())   # 1回目

    def test_重複すると確認を返す(self):
        result = meisai_service.output(self.conn, filled())
        self.assertFalse(result.ok)
        self.assertTrue(result.needs_confirm)
        self.assertEqual([c.kind for c in result.confirms], [CONFIRM_DUPLICATE])

    def test_確認の内訳は積み上げ順(self):
        b = filled(keys=("2-8", "1-10"))
        result = meisai_service.output(self.conn, b)
        self.assertEqual(result.confirms[0].detail, ["2-8", "1-10"])

    def test_確認しなければ何も書かれない(self):
        before = history_repo.fuban_count(self.conn)
        meisai_service.output(self.conn, filled())
        self.assertEqual(history_repo.fuban_count(self.conn), before)
        self.assertEqual(history_repo.current_seq(self.conn), 1)

    def test_了解すれば出力できる(self):
        result = meisai_service.output(self.conn, filled(), confirm=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.seq_no, 2)

    def test_同じ副番は二重に登録しない(self):
        # VBA: If Not IsDuplicate Then RegisterFuban
        meisai_service.output(self.conn, filled(), confirm=True)
        self.assertEqual(history_repo.fuban_count(self.conn), 6)

    def test_No指定なら重複チェックを飛ばす(self):
        """VBA: `If Not chkSpecifyNo.Value Then ...` 再出力用の逃げ道。

        **重複の確認だけが消える。** No指定そのものの確認(上書き・
        連番超過)は残る ── VBA も `chkSpecifyNo` の枝の中で
        `ElseIf seqNo > GetCurrentSeq Then` を尋ねている。
        いまの連番は1なので、5を指定すれば連番超過だけが返る。
        """
        result = meisai_service.output(self.conn, filled(), specify_no=5)
        self.assertNotIn(CONFIRM_DUPLICATE, [c.kind for c in result.confirms])
        self.assertEqual([c.kind for c in result.confirms], [CONFIRM_BEYOND_SEQ])

    def test_連番内のNo指定なら何も尋ねずに出せる(self):
        # 既出の副番ばかりでも、No指定なら重複は尋ねない
        meisai_service.output(self.conn, filled(), confirm=True)   # No2 まで進める
        result = meisai_service.output(self.conn, filled(), specify_no=2,
                                       confirm=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.seq_no, 2)

    def test_一部だけ重複していればその分だけ出す(self):
        b = filled(keys=("1-10", "1-1"))       # 1-10 は既出、1-1 は新しい
        result = meisai_service.output(self.conn, b)
        self.assertEqual(result.confirms[0].detail, ["1-10"])


class SeqTest(unittest.TestCase):
    """RULE-08: 連番とNo指定。"""

    def setUp(self):
        self.conn = _db.case_db(self)

    def test_既存のNoを指定すると上書き確認(self):
        meisai_service.output(self.conn, filled())        # No1
        result = meisai_service.output(self.conn, filled(), specify_no=1)
        self.assertEqual([c.kind for c in result.confirms], [CONFIRM_OVERWRITE])

    def test_上書きを了解すると入れ替わる(self):
        meisai_service.output(self.conn, filled())
        b = filled(keys=("2-1", "2-2"))
        meisai_service.output(self.conn, b, specify_no=1, confirm=True)
        found = meisai_service.find_output(self.conn, "L5160Z0", 1)
        self.assertEqual(found.keys, ["2-1", "2-2"])
        self.assertEqual(len(meisai_service.list_outputs(self.conn, "L5160Z0")), 1)

    def test_連番を超えるNoは確認する(self):
        result = meisai_service.output(self.conn, filled(), specify_no=9)
        self.assertEqual([c.kind for c in result.confirms], [CONFIRM_BEYOND_SEQ])

    def test_了解すると現在連番が引き上がる(self):
        meisai_service.output(self.conn, filled(), specify_no=9, confirm=True)
        self.assertEqual(history_repo.current_seq(self.conn), 9)

    def test_現在連番より小さいNoは引き下げない(self):
        meisai_service.output(self.conn, filled())                     # No1
        meisai_service.output(self.conn, filled(), specify_no=1, confirm=True)
        self.assertEqual(history_repo.current_seq(self.conn), 1)

    def test_0以下のNoは断る(self):
        result = meisai_service.output(self.conn, filled(), specify_no=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.refuse, meisai_service.REFUSE_BAD_NO)

    def test_連番のリセット(self):
        meisai_service.output(self.conn, filled())
        history_repo.reset_seq(self.conn)
        self.assertEqual(history_repo.current_seq(self.conn), 0)

    def test_副番履歴クリアは連番を戻さない(self):
        # VBA: 「seqNo は副番履歴クリアでリセットしない」
        meisai_service.output(self.conn, filled())
        history_repo.clear_fuban(self.conn)
        self.assertEqual(history_repo.current_seq(self.conn), 1)
        self.assertEqual(history_repo.fuban_count(self.conn), 0)
        self.assertTrue(history_repo.is_printed(self.conn))


class SnapshotTest(unittest.TestCase):
    """途中経過の保存と復元。"""

    def setUp(self):
        self.conn = _db.case_db(self)

    def _snap(self, lot="L5160Z0") -> Snapshot:
        return Snapshot(lot_no=lot, zen_kotei=24, jou_su=2,
                        weights=[250, 248], strands=[12, 12],
                        used=["1-10", "2-8"], discarded=["2-3"])

    def test_保存して復元すると全項目が一致する(self):
        history_repo.save_snapshot(self.conn, self._snap())
        got = history_repo.load_snapshot(self.conn, "L5160Z0")
        self.assertEqual(got, self._snap())

    def test_無ければNone(self):
        self.assertIsNone(history_repo.load_snapshot(self.conn, "XXXXXXX"))

    def test_同じロットは上書きされる(self):
        history_repo.save_snapshot(self.conn, self._snap())
        second = self._snap()
        second.jou_su = 3
        second.strands = [8, 8, 8]
        history_repo.save_snapshot(self.conn, second)
        self.assertEqual(history_repo.load_snapshot(self.conn, "L5160Z0").jou_su, 3)
        self.assertEqual(len(history_repo.snapshot_lots(self.conn)), 1)

    def test_直近5件だけ残る(self):
        for i in range(7):
            history_repo.save_snapshot(self.conn, self._snap(lot=f"LOT{i:04d}"))
        self.assertEqual(len(history_repo.snapshot_lots(self.conn)),
                         config.SNAPSHOT_KEEP)

    def test_1ロットだけ消せる(self):
        history_repo.save_snapshot(self.conn, self._snap())
        history_repo.delete_snapshot(self.conn, "L5160Z0")
        self.assertIsNone(history_repo.load_snapshot(self.conn, "L5160Z0"))

    def test_全消去(self):
        history_repo.save_snapshot(self.conn, self._snap())
        history_repo.clear_snapshots(self.conn)
        self.assertEqual(history_repo.snapshot_lots(self.conn), [])

    def test_空の並びは空の一覧になる(self):
        snap = self._snap()
        snap.used = []
        snap.discarded = []
        history_repo.save_snapshot(self.conn, snap)
        got = history_repo.load_snapshot(self.conn, "L5160Z0")
        self.assertEqual(got.used, [])
        self.assertEqual(got.discarded, [])


class ReportTest(unittest.TestCase):
    """帳票。"""

    def _output(self, keys=("1-10", "1-5", "2-8"), weights=(250, 248)) -> Output:
        return Output(lot_no="L5160Z0", seq_no=1, keys=list(keys),
                      weights=list(weights))

    def test_シート名はVBAと同じ形(self):
        self.assertEqual(self._output().sheet_name, "L5160Z0-No1")

    def test_行の重量は丈から引かれる(self):
        rows = self._output().rows()
        self.assertEqual(rows, [("1-10", "250.0Kg"), ("1-5", "250.0Kg"),
                                ("2-8", "248.0Kg")])

    def test_読めないキーは重量を空にして行を残す(self):
        # VBA WriteData は不正キーを飛ばしつつ行だけ進める
        rows = self._output(keys=("1-10", "こわれた", "2-8")).rows()
        self.assertEqual(rows[1], ("こわれた", ""))
        self.assertEqual(len(rows), 3)

    def test_紙は必ず15行(self):
        html = report.render([self._output()])
        self.assertEqual(html.count('class="r-data"'), config.SHEET_ROWS)

    def test_用紙はA4横で余白0(self):
        html = report.render([self._output()])
        self.assertIn("size: A4 landscape", html)
        self.assertIn("margin: 0mm", html)

    def test_見出しの文言(self):
        html = report.render([self._output()])
        for text in ("L5160Z0-No1  梱包明細表", "NLM.NAGOYA.QA",
                     "コイル副番", "重量(Kg)", "小ラベル添付"):
            self.assertIn(text, html)

    def test_0行でも紙は成立する(self):
        html = report.render([self._output(keys=())])
        self.assertEqual(html.count('class="r-data"'), config.SHEET_ROWS)

    # --- 用紙: A4横の左半分に1枚、右半分は空ける ---------------------
    def _outs(self, *nos):
        outs = []
        for no in nos:
            out = self._output()
            out.seq_no = no
            outs.append(out)
        return outs

    def test_明細1枚は左半分で右半分は空ける(self):
        """**現場は右半分を切り取って使う。** VBA の紙面も同じ
        (`FitToPages` は縮めるだけで引き伸ばさないので、1面は等倍で
        左半分に出る)。

        **右半分を何かで埋めない。** 別の明細を並べる運用はしておらず、
        同じ明細を写すと同じ梱包の紙が2枚になる。
        """
        html = report.render([self._output()])
        self.assertEqual(html.count('class="page"'), 1)
        self.assertEqual(html.count('class="meisai"'), 1)
        self.assertIn('<div class="half blank"></div></div>', html)

    def test_まとめて刷っても1枚ずつ別の用紙(self):
        """**A4 1枚に2枚並べる運用はしていない**(現場に確認済み)。"""
        for count in (2, 3, 4):
            with self.subTest(count=count):
                html = report.render(self._outs(*range(1, count + 1)))
                self.assertEqual(html.count('class="page"'), count)
                self.assertEqual(html.count('class="meisai"'), count)
                self.assertEqual(html.count('class="half blank"'), count)

    def test_どの用紙も左に明細_右は空(self):
        """紙ごとに「明細 → 空」の順。右に明細が来る紙を作らない。"""
        html = report.render(self._outs(1, 2, 3))
        pages = html.split('<div class="page">')[1:]
        for page in pages:
            left, right = page.split('<div class="half blank">', 1)
            self.assertIn('class="meisai"', left)
            self.assertNotIn('class="meisai"', right.split('</div></div>', 1)[0])

    def test_右半分に線を引かない(self):
        """VBA の紙面の右半分は何も無い(切り取る側)。"""
        self.assertNotIn(".half + .half", report._sheet_css())
        self.assertNotIn("dashed", report._sheet_css())

    # --- 刷れる範囲と文字の寄せ ---------------------------------------
    @staticmethod
    def _rule(selector: str) -> str:
        """`_sheet_css()` の中の1つの規則の中身。"""
        css = report._sheet_css()
        start = css.index(selector + "{")
        return css[start:css.index("}", start)]

    def test_紙の端から5mm内に置く(self):
        """プリンタは紙の縁から約4mmを刷れない。**刷ると左と上が欠けて
        いた**(現場の実機。プレビューでは収まって見える)。

        余白は中身(`.half` の内側)で取る ── 現場は「余白なし」で刷る
        ので、`@page` の余白は上書きされて効かない。右も同じだけ空ける
        (切り目が紙の端になる)。
        """
        self.assertGreaterEqual(report.SAFE_MARGIN_MM, 5.0)
        half = self._rule(".half")
        # 線の太さぶん足してある(表の外枠の線は箱から少しはみ出す)
        self.assertIn(f"padding:calc({report.SAFE_MARGIN_MM:g}mm + 1px);", half)
        self.assertIn("overflow:hidden", half)

    def test_副番と重量は中央(self):
        """VBA は `MergePut` で結合して `xlCenter` にしてから値を入れる。
        以前は副番を左・重量を右に寄せていて、VBA と違っていた。
        """
        self.assertIn("text-align:center", self._rule(".meisai .r-data td"))
        css = report._sheet_css()
        self.assertNotIn("text-align:right", css)
        self.assertNotIn("text-align:left", css)

    def test_書き足したサイズとLOTNOも中央(self):
        self.assertIn("text-align:center", self._rule(".meisai .r-label .hand .hl"))

    def test_どの文字も端に寄せない(self):
        """見出し・小ラベル添付・左の見出しも中央(VBA はどれも `xlCenter`)。"""
        for selector in (".meisai .r-title td", ".meisai .r-label .cap",
                         ".meisai .r-label .paste", ".meisai .r-head td"):
            with self.subTest(selector=selector):
                self.assertIn("text-align:center", self._rule(selector))

    def test_寸法は実ブックの比率(self):
        """行の合計が 595.5pt = A4の高さ。列は1面ぶん(=A4横の半分)で100%。"""
        self.assertAlmostEqual(sum(report._col_percents()), 100.0, places=3)
        title, label, head, data = report._row_percents()
        self.assertAlmostEqual(title + label + head + data * config.SHEET_ROWS,
                               100.0, places=3)


class OutputListTest(unittest.TestCase):
    """出力の一覧と削除。"""

    def setUp(self):
        self.conn = _db.case_db(self)

    def test_No順に並ぶ(self):
        meisai_service.output(self.conn, filled())
        meisai_service.output(self.conn, filled(), confirm=True)
        got = meisai_service.list_outputs(self.conn, "L5160Z0")
        self.assertEqual([o.seq_no for o in got], [1, 2])

    def test_ロットが違えば混ざらない(self):
        meisai_service.output(self.conn, filled())
        meisai_service.output(self.conn, filled(lot="AAA0001"))
        self.assertEqual(len(meisai_service.list_outputs(self.conn, "L5160Z0")), 1)

    def test_ロットごと消せる(self):
        meisai_service.output(self.conn, filled())
        meisai_service.output(self.conn, filled(), confirm=True)
        self.assertEqual(meisai_service.delete_lot_outputs(self.conn, "L5160Z0"), 2)
        self.assertEqual(meisai_service.list_outputs(self.conn, "L5160Z0"), [])

    def test_出力の中身が読み戻せる(self):
        meisai_service.output(self.conn, filled())
        got = meisai_service.find_output(self.conn, "L5160Z0", 1)
        self.assertEqual(got.keys, ["1-10", "1-5", "2-8", "2-11", "2-12", "2-9"])
        self.assertEqual(got.weights, [250, 248])

    def test_出力後に積み上げだけ空になる(self):
        # VBA: ClearGimmickC のみ。条番号のキャンバスは残る
        b = filled()
        meisai_service.output(self.conn, b)
        meisai_service.reset_stack(b)
        self.assertEqual(b.slots, [None] * 6)
        self.assertEqual(b.strands, [12, 12])

    def test_出力したコイルは使用済みのまま残る(self):
        """VBA「Cスロットのみ。Bは出力後もそのまま残す」。

        **同じコイルを次の梱包へ二重に積ませない**ための仕掛け。
        ここが消えると、現物と紙が食い違う。
        """
        b = filled()
        keys = b.stacked_keys()
        meisai_service.output(self.conn, b)
        meisai_service.reset_stack(b)

        self.assertEqual(b.used, set(keys))
        for key in keys:
            self.assertEqual(b.state_of(key), "used")

    def test_出力済みのコイルは積み直せない(self):
        b = filled()
        meisai_service.output(self.conn, b)
        meisai_service.reset_stack(b)
        with self.assertRaises(RefusedError) as cm:
            b.stack("1-10")
        self.assertEqual(cm.exception.reason, "used")

    def test_条番号を作り直せば使用済みは消える(self):
        # VBA: BuildGimmickB がラベルを作り直すので IsUsed も消える
        b = filled()
        meisai_service.output(self.conn, b)
        meisai_service.reset_stack(b)
        b.build_strands()
        self.assertEqual(b.used, set())


if __name__ == "__main__":
    unittest.main()
