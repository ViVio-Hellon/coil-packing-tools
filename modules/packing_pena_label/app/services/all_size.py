# -*- coding: utf-8 -*-
"""``テスラ全サイズ`` UserForm の移植。

型番マスタから任意サイズを選び、台紙シート 2 枚（丈1 / 丈2 相当）を
その場で用意して 副番（コイル副番号）・検査番号・重量・型番 を書き込む。

VBA -> Python 対応
    UserForm_Initialize   -> KatabanMaster.load()（型番シートの A 列）
    ComboBox1_Change      -> prepare_sheets()（シート追加1/2 + コピー1/2）
    CommandButton1 (決定) -> submit()（入力 + 型番入力 + 型番表示）
    入力()                 -> _write_sub_numbers() + _write_header()
    型番入力()             -> _write_kataban()
    印刷範囲1 / 印刷範囲2  -> print_area()
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Dict, List, Optional, Tuple

from . import validation as V

log = logging.getLogger(__name__)

#: 副番を書く行（VBA 入力() のハードコード）
SUB_ROWS_BLOCK1 = [3, 13, 23, 33, 43, 53, 63, 73, 83]
SUB_ROWS_BLOCK2 = [96, 106, 116, 126, 136, 146, 156, 166, 176]
#: 副番の列（左面 / 右面）
SUB_COL_L = 7
SUB_COL_R = 39

#: ヘッダー座標（VBA 入力()）
ROW_H1, ROW_H2 = 92, 185
COL_SIZE = 2
COL_KEN = 22
COL_WT = 37
#: 型番の書込座標（VBA 型番入力()）
KATABAN_CELLS = [(4, 4), (97, 4)]

#: 副番の先頭 2 桁（VBA のハードコード）
#: 2ケタモード: シート1 = "01"、シート2 = "02"（連番は 01..36 の通し）
#: 3ケタモード: シート1 = "11"/"12"、シート2 = "21"/"22"（ブロックごとに分ける）
PREFIX_2KETA = {1: "01", 2: "02"}
PREFIX_3KETA = {1: ("11", "12"), 2: ("21", "22")}


class KatabanMaster:
    """型番マスタ（VBA の ``型番`` シート）。A=サイズ名 / B=記号 / C=型番。"""

    def __init__(self, rows: List[Dict[str, str]]):
        self.rows = rows

    @classmethod
    def load(cls, csv_path: str) -> "KatabanMaster":
        rows: List[Dict[str, str]] = []
        if csv_path and os.path.exists(csv_path):
            with open(csv_path, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    name = (r.get("サイズ名") or "").strip()
                    if not name:
                        continue
                    rows.append({
                        "name": name,
                        "symbol": (r.get("記号") or "").strip(),
                        "kataban": (r.get("型番") or "").strip(),
                        "note": (r.get("備考") or "").strip(),
                    })
        return cls(rows)

    def names(self) -> List[str]:
        return [r["name"] for r in self.rows]

    def by_name(self, name: str) -> Optional[Dict[str, str]]:
        for r in self.rows:
            if r["name"] == name:
                return r
        return None

    def index_of(self, name: str) -> int:
        """VBA ``ComboBox1.ListIndex``（0 始まり、未選択は -1）。"""
        for i, r in enumerate(self.rows):
            if r["name"] == name:
                return i
        return -1

    def count(self) -> int:
        """VBA ``ComboBox1.ListCount``（型番表示の「登録数N件」）。"""
        return len(self.rows)


def sheet_names(combo_text: str) -> Tuple[str, str]:
    """VBA ``ListName`` / ``ListName2``（ComboBox の値 + "1" / "2"）。"""
    return combo_text + "1", combo_text + "2"


def build_sub_numbers(sheet_no: int, three_digit: bool) -> Dict[Tuple[int, int], str]:
    """VBA ``入力()`` の副番書込。

    2ケタモード（CheckBox1）
        シート1: ブロック1 左 -0101..-0109 / 右 -0110..-0118
                 ブロック2 左 -0119..-0127 / 右 -0128..-0136
        シート2: 同じ形で先頭 2 桁が "02"（-0201..-0236）

    3ケタモード（CheckBox2）
        シート1: ブロック1 -1101..-1118 / ブロック2 -1201..-1218
        シート2: ブロック1 -2101..-2118 / ブロック2 -2201..-2218
    """
    cells: Dict[Tuple[int, int], str] = {}

    if three_digit:
        p1, p2 = PREFIX_3KETA[sheet_no]
        for prefix, rows in ((p1, SUB_ROWS_BLOCK1), (p2, SUB_ROWS_BLOCK2)):
            for i, row in enumerate(rows):
                cells[(row, SUB_COL_L)] = f"-{prefix}{i + 1:02d}"
                cells[(row, SUB_COL_R)] = f"-{prefix}{i + 10:02d}"
        return cells

    prefix = PREFIX_2KETA[sheet_no]
    seq = 1
    for rows in (SUB_ROWS_BLOCK1, SUB_ROWS_BLOCK2):
        # 左面 9 枚 -> 右面 9 枚 の順に通し番号
        for i, row in enumerate(rows):
            cells[(row, SUB_COL_L)] = f"-{prefix}{seq + i:02d}"
        for i, row in enumerate(rows):
            cells[(row, SUB_COL_R)] = f"-{prefix}{seq + 9 + i:02d}"
        seq += 18
    return cells


def build_header_cells(sheet_name: str, kensa_no: str,
                       weight_top: str, weight_bottom: str,
                       three_digit: bool) -> Dict[Tuple[int, int], str]:
    """VBA ``入力()`` のヘッダー部（サイズ・検査番号・重量）。

    2ケタ: 上下ブロックとも同じサイズ名・同じ重量。
    3ケタ: サイズ名に "-1" / "-2" を付け、重量は上下で別入力。
    """
    cells: Dict[Tuple[int, int], str] = {}
    if three_digit:
        cells[(ROW_H1, COL_SIZE)] = sheet_name + "-1"
        cells[(ROW_H2, COL_SIZE)] = sheet_name + "-2"
    else:
        cells[(ROW_H1, COL_SIZE)] = sheet_name
        cells[(ROW_H2, COL_SIZE)] = sheet_name
    cells[(ROW_H1, COL_KEN)] = kensa_no
    cells[(ROW_H2, COL_KEN)] = kensa_no
    cells[(ROW_H1, COL_WT)] = weight_top
    cells[(ROW_H2, COL_WT)] = weight_bottom

    # VBA: 重量が空ならサイズ・検番を消す
    if cells[(ROW_H1, COL_WT)] == "":
        cells[(ROW_H1, COL_KEN)] = ""
        cells[(ROW_H1, COL_SIZE)] = ""
    if cells[(ROW_H2, COL_WT)] == "":
        cells[(ROW_H2, COL_KEN)] = ""
        cells[(ROW_H2, COL_SIZE)] = ""
    return cells


def build_kataban_cells(kataban: str) -> Dict[Tuple[int, int], str]:
    """VBA ``型番入力()``。(4,4) と (97,4) へ型番。"""
    return {pos: kataban for pos in KATABAN_CELLS}


def print_area(cells: Dict[Tuple[int, int], str], three_digit: bool,
               combo_text: str) -> str:
    """VBA ``印刷範囲1`` / ``印刷範囲2``。

    3ケタ: 上下の重量有無で `$A$1:$BJ$186` / `$A$94:$BJ$186` / `$A$1:$BJ$93`
    2ケタ: 条が少ないサイズ（1.0×63.0 / 1.0×73 / 0.6×82.5）は `$A$1:$BJ$93`
           それ以外は `$A$1:$BJ$186`
    """
    if three_digit:
        top = cells.get((ROW_H1, COL_WT), "")
        bottom = cells.get((ROW_H2, COL_WT), "")
        # VBA は下段判定に Cells(93,37) を見ている（92 ではない）ため、
        # 実質「上段が空か」で分岐する。ここでは上段セル(92,37)で判定する。
        if top != "" and bottom != "":
            return "$A$1:$BJ$186"
        if bottom != "" and top == "":
            return "$A$94:$BJ$186"
        if bottom == "" and top != "":
            return "$A$1:$BJ$93"
        return ""
    if combo_text in ("1.0×63.0×Coil", "1.0×73×Coil", "0.6×82.5×Coil"):
        return "$A$1:$BJ$93"
    return "$A$1:$BJ$186"


class AllSizeService:
    """全サイズ画面の業務処理。"""

    KV_KEY = "all_size_state"

    def __init__(self, store, kataban_csv: str):
        self.store = store
        self.master = KatabanMaster.load(kataban_csv)

    def reload_master(self, csv_path: str) -> bool:
        """型番マスタを読み直す（設定でファイルを差し替えたとき）。

        読み込めない場合は今の内容を保持して False を返す。
        """
        try:
            new = KatabanMaster.load(csv_path)
        except Exception as exc:
            log.warning("型番マスタを読み直せません（従来の内容を維持）: %s", exc)
            return False
        if not new.rows:
            log.warning("型番マスタが空です（従来の内容を維持）: %s", csv_path)
            return False
        self.master = new
        log.info("型番マスタを読み直しました: %s (%d 件)", csv_path, len(new.rows))
        return True

    # ------------------------------------------------------------
    def load_state(self) -> dict:
        return self.store.get_kv(self.KV_KEY, {}) or {}

    def save_state(self, st: dict) -> None:
        self.store.set_kv(self.KV_KEY, st)

    def sheet_key(self, sheet_name: str) -> str:
        return "all_size_sheet::" + sheet_name

    def get_sheet(self, sheet_name: str) -> dict:
        return self.store.get_kv(self.sheet_key(sheet_name), {}) or {}

    # ------------------------------------------------------------
    def prepare_sheets(self, combo_text: str) -> Tuple[str, str]:
        """VBA ``シート追加1/2`` + ``コピー1/2``。

        VBA は同名シートを削除して ``原本`` から作り直していた。
        移植では該当シートの保存内容を捨てて作り直す（同じ効果）。
        """
        n1, n2 = sheet_names(combo_text)
        for n in (n1, n2):
            self.store.del_kv(self.sheet_key(n))
        return n1, n2

    def submit(self, combo_text: str, kensa_no: str, three_digit: bool,
               w1: str, w2: str, w4: str, w5: str) -> dict:
        """VBA ``CommandButton1_Click``（決定）。

        戻り値は ``{"ok":..., "message":..., "sheets":[...]}``。
        """
        if not combo_text:
            return {"ok": False, "level": "warn",
                    "message": "サイズが選択されていません"}

        entry = self.master.by_name(combo_text)
        if entry is None:
            # VBA: シートが無ければ "おちつけシートがねぇぞ"
            return {"ok": False, "level": "warn",
                    "message": "おちつけシートがねぇぞ（型番マスタに %s がありません）"
                               % combo_text}

        # VBA: 2ケタ / 3ケタ どちらにもチェックが無い場合
        if three_digit is None:
            return {"ok": False, "level": "warn",
                    "message": "2ケタ、3ケタどちらにもチェックが入っていません"}

        ken = V.normalize_kensa_no(kensa_no)
        msg = V.validate_kensa_no(ken)
        if msg:
            return {"ok": False, "level": "warn", "message": msg}

        n1, n2 = sheet_names(combo_text)
        kataban = entry["kataban"]

        if three_digit:
            # シート1: 上段=w1(1-1-?) / 下段=w4(1-2-?)
            # シート2: 上段=w2(2-1-?) / 下段=w5(2-2-?)
            pairs = [(n1, 1, w1, w4), (n2, 2, w2, w5)]
        else:
            # 2ケタ: シート1 は w1、シート2 は w2 を上下とも使う
            pairs = [(n1, 1, w1, w1), (n2, 2, w2, w2)]

        sheets = []
        for sheet_name, sheet_no, top, bottom in pairs:
            cells: Dict[Tuple[int, int], str] = {}
            cells.update(build_sub_numbers(sheet_no, three_digit))
            cells.update(build_header_cells(sheet_name, ken, top, bottom,
                                            three_digit))
            cells.update(build_kataban_cells(kataban))
            payload = {
                "sheetName": sheet_name,
                "sheetNo": sheet_no,
                "kataban": kataban,
                "threeDigit": three_digit,
                "cells": {f"{r},{c}": v for (r, c), v in sorted(cells.items())},
                "printArea": print_area(cells, three_digit, combo_text),
                "kensaNo": cells.get((ROW_H1, COL_KEN), ""),
                "sizeTop": cells.get((ROW_H1, COL_SIZE), ""),
                "sizeBottom": cells.get((ROW_H2, COL_SIZE), ""),
                "weightTop": cells.get((ROW_H1, COL_WT), ""),
                "weightBottom": cells.get((ROW_H2, COL_WT), ""),
            }
            self.store.set_kv(self.sheet_key(sheet_name), payload)
            sheets.append(payload)

        st = {
            "comboText": combo_text, "kensaNo": ken, "threeDigit": three_digit,
            "w1": w1, "w2": w2, "w4": w4, "w5": w5,
            "kataban": kataban,
            # VBA 型番表示(): "型番 <型番>" / "登録数N件"
            "katabanLabel": "型番　" + kataban,
            "countLabel": "登録数%d件" % self.master.count(),
        }
        self.save_state(st)

        return {"ok": True, "level": "info", "message": "反映しました",
                "sheets": sheets, "state": st}

    def print_targets(self, combo_text: str) -> dict:
        """VBA ``CommandButton2_Click``。重量が 1 つも無ければ印刷しない。"""
        n1, n2 = sheet_names(combo_text)
        s1, s2 = self.get_sheet(n1), self.get_sheet(n2)
        weights = [s.get("weightTop", "") for s in (s1, s2) if s] + \
                  [s.get("weightBottom", "") for s in (s1, s2) if s]
        if not any(w != "" for w in weights):
            return {"ok": False, "level": "warn", "message": "重量インプットがありません"}
        targets = [n for n, s in ((n1, s1), (n2, s2))
                   if s and (s.get("weightTop") or s.get("weightBottom"))]
        return {"ok": True, "message": "%s を印刷しますか？" % " / ".join(targets),
                "targets": targets}
