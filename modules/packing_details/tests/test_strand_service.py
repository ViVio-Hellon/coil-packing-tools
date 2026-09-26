"""条数配分・条番号・積み上げ・廃棄 (RULE-01〜05)

VBA と同じ結果になることを確かめる。配分は**VBAの手順をそのまま
書き下した参照実装**と全組合せで突き合わせる ── 期待値を手で並べると、
書き写す時点で間違えたものがそのまま正解になってしまう。
"""
from __future__ import annotations

import unittest

from modules.packing_details.meisai import config
from modules.packing_details.meisai.strand_service import (Board, RefusedError, REFUSE_DISCARDED,
                                   REFUSE_FULL, REFUSE_NO_SLOTS,
                                   REFUSE_UNKNOWN_KEY, REFUSE_USED,
                                   distribute, key_of, keys_for_jou, parse_key)


def vba_distribute(total: int, jou_su: int) -> list[int]:
    """VBA `cmdShowJouTai_Click` の配分をそのまま書き下したもの。

        base = total \\ mJouSu
        rem_ = total Mod mJouSu
        For i = mJouSu To 1 Step -1
            mStrandsPerJou(i) = base
            If rem_ > 0 Then ... + 1 : rem_ = rem_ - 1
    """
    base = total // jou_su
    rem = total % jou_su
    out = [0] * jou_su
    for i in range(jou_su, 0, -1):
        out[i - 1] = base
        if rem > 0:
            out[i - 1] += 1
            rem -= 1
    return out


class DistributeTest(unittest.TestCase):
    """RULE-03: 条数配分。"""

    def test_全組合せがVBAと一致する(self):
        for total in range(0, 300):
            for jou in range(1, config.JOUSU_MAX + 1):
                with self.subTest(total=total, jou=jou):
                    self.assertEqual(distribute(total, jou),
                                     vba_distribute(total, jou))

    def test_余りは丈番号の大きいほうから配る(self):
        # 31本を2丈へ: 丈1=15, 丈2=16。昇順で配ると [16, 15] になる
        self.assertEqual(distribute(31, 2), [15, 16])
        # 5本を3丈へ: 丈3・丈2に1本ずつ乗る
        self.assertEqual(distribute(5, 3), [1, 2, 2])

    def test_割り切れれば均等(self):
        self.assertEqual(distribute(30, 2), [15, 15])
        self.assertEqual(distribute(21, 3), [7, 7, 7])

    def test_実績数が丈数より少ないと0条の丈ができる(self):
        # VBA も 0 を許し、BuildGimmickB がその丈を飛ばす
        self.assertEqual(distribute(2, 3), [0, 1, 1])
        self.assertEqual(distribute(0, 3), [0, 0, 0])

    def test_丈数1なら全部そこへ(self):
        self.assertEqual(distribute(24, 1), [24])

    def test_結果は整数(self):
        # `/` を使うと float になり、条番号キーが "1-7.0" になる
        for value in distribute(21, 3):
            self.assertIsInstance(value, int)

    def test_丈数0以下は断る(self):
        with self.assertRaises(ValueError):
            distribute(10, 0)
        with self.assertRaises(ValueError):
            distribute(10, -1)

    def test_実績数が負なら断る(self):
        with self.assertRaises(ValueError):
            distribute(-1, 2)


class KeyTest(unittest.TestCase):
    """RULE-04: 条番号キー。"""

    def test_描画順は条番号の降順(self):
        # VBA: For k = mStrandsPerJou(i) To 1 Step -1 → 左端が最大
        self.assertEqual(keys_for_jou(2, 4), ["2-4", "2-3", "2-2", "2-1"])

    def test_0条の丈は空(self):
        # VBA: If jouCount < 1 Then GoTo ContinueJou
        self.assertEqual(keys_for_jou(3, 0), [])
        self.assertEqual(keys_for_jou(3, -1), [])

    def test_キーの組み立てと分解(self):
        self.assertEqual(key_of(2, 15), "2-15")
        self.assertEqual(parse_key("2-15"), (2, 15))

    def test_読めないキーは断る(self):
        for bad in ("", "2", "-15", "abc", "2-"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_key(bad)


def _board(zen: int = 24, jou: int = 2, stack: int = 6,
           weights: list[int] | None = None) -> Board:
    board = Board(lot_no="L5160Z0", zen_kotei=zen, jou_su=jou,
                  weights=weights if weights is not None else [250, 248])
    board.build_strands()
    board.set_stack_max(stack)
    return board


class StackTest(unittest.TestCase):
    """RULE-05: 積み上げ。"""

    def test_最も若い空きスロットへ入る(self):
        board = _board()
        self.assertEqual(board.stack("1-10"), 1)
        self.assertEqual(board.stack("1-5"), 2)
        self.assertEqual(board.stack("2-8"), 3)

    def test_解除したスロットへ次が入る(self):
        # VBA は詰め直さない。空いたところへ次が入る
        board = _board(stack=3)
        board.stack("1-1")
        board.stack("1-2")
        board.stack("1-3")
        self.assertEqual(board.unstack(2), "1-2")
        self.assertEqual(board.stack("1-4"), 2)

    def test_全部埋まるまで出力できない(self):
        board = _board(stack=2)
        self.assertFalse(board.is_complete)
        board.stack("1-1")
        self.assertFalse(board.is_complete)
        board.stack("1-2")
        self.assertTrue(board.is_complete)

    def test_解除すると未完成に戻る(self):
        board = _board(stack=1)
        board.stack("1-1")
        self.assertTrue(board.is_complete)
        board.unstack(1)
        self.assertFalse(board.is_complete)

    def test_スロットが無ければ断る(self):
        board = Board(lot_no="X", zen_kotei=24, jou_su=2, weights=[1, 1])
        board.build_strands()
        with self.assertRaises(RefusedError) as cm:
            board.stack("1-1")
        self.assertEqual(cm.exception.reason, REFUSE_NO_SLOTS)

    def test_上限に達したら断る(self):
        board = _board(stack=2)
        board.stack("1-1")
        board.stack("1-2")
        with self.assertRaises(RefusedError) as cm:
            board.stack("1-3")
        self.assertEqual(cm.exception.reason, REFUSE_FULL)

    def test_使用済みは積めない(self):
        board = _board()
        board.stack("1-1")
        with self.assertRaises(RefusedError) as cm:
            board.stack("1-1")
        self.assertEqual(cm.exception.reason, REFUSE_USED)

    def test_存在しない条番号は断る(self):
        board = _board()          # 丈2まで、各12条
        for bad in ("3-1", "1-13", "1-0", "0-1"):
            with self.subTest(bad=bad), self.assertRaises(RefusedError) as cm:
                board.stack(bad)
            self.assertEqual(cm.exception.reason, REFUSE_UNKNOWN_KEY)

    def test_積み条数の上限(self):
        board = _board()
        with self.assertRaises(ValueError):
            board.set_stack_max(config.STACK_LIMIT + 1)
        with self.assertRaises(ValueError):
            board.set_stack_max(0)

    def test_積み条数を変えるとスロットは空になる(self):
        # VBA `txtTsumiJouSu_Change` は ResetGimmickBUsed してから作り直す
        board = _board()
        board.stack("1-1")
        board.set_stack_max(3)
        self.assertEqual(board.slots, [None, None, None])
        self.assertEqual(board.used, set())

    def test_積み上げ順がそのまま紙の行順(self):
        board = _board()
        for key in ["1-10", "1-5", "2-8", "2-11", "2-12", "2-9"]:
            board.stack(key)
        self.assertEqual(board.stacked_keys(),
                         ["1-10", "1-5", "2-8", "2-11", "2-12", "2-9"])


class DiscardTest(unittest.TestCase):
    """廃棄 (VBA `GimmickB_Click` の廃棄モード)。"""

    def test_付けて外せる(self):
        board = _board()
        self.assertTrue(board.toggle_discard("1-3"))
        self.assertEqual(board.state_of("1-3"), "discarded")
        self.assertFalse(board.toggle_discard("1-3"))
        self.assertEqual(board.state_of("1-3"), "available")

    def test_廃棄済みは積めない(self):
        board = _board()
        board.toggle_discard("1-3")
        with self.assertRaises(RefusedError) as cm:
            board.stack("1-3")
        self.assertEqual(cm.exception.reason, REFUSE_DISCARDED)

    def test_使用済みは廃棄にできない(self):
        board = _board()
        board.stack("1-3")
        with self.assertRaises(RefusedError) as cm:
            board.toggle_discard("1-3")
        self.assertEqual(cm.exception.reason, REFUSE_USED)

    def test_条数が減ると存在しない廃棄は落ちる(self):
        # VBA `ReapplyHaiki`: ハンドラが取れないものは新しい Collection に入れない
        board = _board(zen=24, jou=2)       # 各12条
        board.toggle_discard("2-12")
        board.zen_kotei = 4                 # 各2条へ
        board.build_strands()
        self.assertEqual(board.discarded, [])

    def test_条数が変わっても残る廃棄は残る(self):
        board = _board(zen=24, jou=2)
        board.toggle_discard("1-1")
        board.zen_kotei = 4
        board.build_strands()
        self.assertEqual(board.discarded, ["1-1"])


class ViewTest(unittest.TestCase):
    """表示まわり。"""

    def test_反転しても条番号と状態は変わらない(self):
        # VBA `cmdReverse_Click` はラベルの Left だけを入れ替える
        board = _board()
        board.stack("1-10")
        before = board.stacked_keys()
        forward = board.rows()[0]
        board.reverse()
        self.assertEqual(board.rows()[0], list(reversed(forward)))
        self.assertEqual(board.stacked_keys(), before)
        self.assertEqual(board.state_of("1-10"), "used")

    def test_重量は丈単位で引かれる(self):
        # VBA `WriteData`: weights(jouPart) ── 同じ丈なら同じ値
        board = _board(weights=[250, 248])
        self.assertEqual(board.weight_of("1-1"), 250)
        self.assertEqual(board.weight_of("1-12"), 250)
        self.assertEqual(board.weight_of("2-1"), 248)

    def test_範囲外の丈は重量0(self):
        board = _board(weights=[250, 248])
        self.assertEqual(board.weight_of("9-1"), 0)

    def test_条番号表示で使用済みはリセットされる(self):
        # VBA: BuildGimmickB がラベルを作り直すので IsUsed は消える
        board = _board()
        board.stack("1-1")
        board.build_strands()
        self.assertEqual(board.used, set())


if __name__ == "__main__":
    unittest.main()
