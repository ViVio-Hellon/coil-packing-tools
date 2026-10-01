"""見出しクリックの並び替え: 数字は数の大きさで(`common/sql_sort.py`。統合 1.0.11)。

現場の指摘(列名を押して並べ替えたい)を通し試験で確かめたとき、取り込み元が数字を文字で
持っているため、巾上限で並べると 800 が 1350 の後ろへ来ていた。3機能のマスタ管理が同じ
式で並べる。
"""
from __future__ import annotations

import sqlite3
import unittest

from common.sql_sort import order_terms

VALUES = ["1000", "800", "9999", "1350", "", None, "abc", "1C0118", " 70 ",
          "-5", "1.5", "+3", "1,000", "1-2", "1.2.3"]


def _sorted(values, direction, declared="TEXT"):
    conn = sqlite3.connect(":memory:")
    conn.execute(f'CREATE TABLE t ("v" {declared})')
    conn.executemany("INSERT INTO t VALUES (?)", [(v,) for v in values])
    sql = f'SELECT v FROM t ORDER BY {order_terms(chr(34) + "v" + chr(34), direction)}, rowid'
    return [r[0] for r in conn.execute(sql)]


class OrderTermsTest(unittest.TestCase):
    def test_numbers_by_value_then_text_then_blanks(self):
        self.assertEqual(_sorted(VALUES, "ASC"),
                         ["-5", "1.5", "+3", " 70 ", "800", "1000", "1350", "9999",
                          "1,000", "1-2", "1.2.3", "1C0118", "abc", None, ""])

    def test_descending_reverses_but_blanks_stay_last(self):
        self.assertEqual(_sorted(VALUES, "DESC"),
                         ["abc", "1C0118", "1.2.3", "1-2", "1,000",
                          "9999", "1350", "1000", "800", " 70 ", "+3", "1.5", "-5", "", None])

    def test_real_numbers_and_numeric_text_mix(self):
        self.assertEqual(_sorted([10, "9", 8.5, "abc"], "ASC", declared=""),
                         [8.5, "9", 10, "abc"])

    def test_direction_is_only_asc_or_desc(self):
        self.assertIn(" ASC", order_terms('"v"', "drop table"))
        self.assertNotIn("drop", order_terms('"v"', "drop table"))


if __name__ == "__main__":
    unittest.main()
