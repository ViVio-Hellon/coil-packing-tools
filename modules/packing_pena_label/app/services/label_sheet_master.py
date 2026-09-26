# -*- coding: utf-8 -*-
"""ラベル台紙一覧（共有マスタの ``ラベル台紙一覧`` 表）。

台紙 1 枚ごとの **シート名** と **型番** を共有マスタで持つ。
これまで ``data/size_master.json`` に書いていた 2 項目を、
現場がマスタ管理の画面から直せるようにするためのもの。

    管理番号 … obIdx（1..14。奇数=丈1 / 偶数=丈2）
    シート名 … 印刷ヘッダーの「ｺｲﾙｻｲｽﾞ：」に出る文字
    型番     … ラベルに刷る型番

**JSON は土台、マスタは上書き。**
表が無い・読めない場合は JSON の値がそのまま使われるので、
マスタを用意していない端末でも動く。

読み直しの契機
    1. ラベル台紙の画面を開いたとき（現場の求め）
    2. マスタ管理で書き換えたとき
    3. 共有ファイルの更新日時・サイズが変わったとき
       （別の PC で直された場合に追いつくため）
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

#: 共有マスタ側の表名
TABLE_NAME = "ラベル台紙一覧"

#: 列名
COL_OB = "管理番号"
COL_SHEET = "シート名"
COL_KATABAN = "型番"

#: 更新の確認を省く時間（秒）。共有フォルダーへの stat を減らす
DEFAULT_REFRESH_SEC = 5.0


class LabelSheetMaster:
    """``ラベル台紙一覧`` を読んで、サイズ構成へ上書きする。"""

    def __init__(self, refresh_sec: float = DEFAULT_REFRESH_SEC):
        self.refresh_sec = float(refresh_sec)
        self.last_error = ""
        self.last_path = ""
        self.row_count = 0
        self._rows: Dict[int, Tuple[str, str]] = {}
        self._stamp: Optional[Tuple[str, float, int]] = None
        self._at = 0.0

    # ------------------------------------------------------------
    @staticmethod
    def _stamp_of(path: str) -> Optional[Tuple[str, float, int]]:
        try:
            st = os.stat(path)
            return (os.path.abspath(path), st.st_mtime, st.st_size)
        except OSError:
            return None

    def rows(self) -> Dict[int, Tuple[str, str]]:
        """``{obIdx: (シート名, 型番)}``。読めていなければ空。"""
        return dict(self._rows)

    def invalidate(self) -> None:
        """次に呼ばれたら必ず読み直す（マスタを書き換えた直後に使う）。"""
        self._stamp = None
        self._at = 0.0

    # ------------------------------------------------------------
    def load(self, db_path: str, force: bool = False) -> Dict[int, Tuple[str, str]]:
        """共有マスタから読む。更新が無ければ前回の内容を返す。"""
        path = str(db_path or "")
        self.last_path = path
        if not path or not os.path.exists(path):
            self._rows = {}
            self.row_count = 0
            self.last_error = "" if not path else "見つかりません: %s" % path
            return {}

        now = time.monotonic()
        if not force and self._stamp is not None \
                and (now - self._at) < self.refresh_sec:
            return dict(self._rows)

        stamp = self._stamp_of(path)
        if not force and stamp is not None and stamp == self._stamp:
            self._at = now
            return dict(self._rows)

        out: Dict[int, Tuple[str, str]] = {}
        try:
            uri = "file:%s?mode=ro" % path.replace("?", "%3f").replace("#", "%23")
            conn = sqlite3.connect(uri, uri=True, timeout=10)
            try:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    'SELECT "%s","%s","%s" FROM "%s"'
                    % (COL_OB, COL_SHEET, COL_KATABAN, TABLE_NAME))
                for r in cur.fetchall():
                    try:
                        ob = int(r[COL_OB])
                    except (TypeError, ValueError):
                        continue
                    sheet = "" if r[COL_SHEET] is None else str(r[COL_SHEET])
                    kata = "" if r[COL_KATABAN] is None else str(r[COL_KATABAN])
                    out[ob] = (sheet, kata)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            # 表が無い端末でも動かす。JSON の値がそのまま使われる。
            self.last_error = str(exc)
            log.info("%s を読めません（JSON の値を使います）: %s", TABLE_NAME, exc)
            self._rows = {}
            self.row_count = 0
            self._stamp = stamp
            self._at = now
            return {}

        self.last_error = ""
        self._rows = out
        self.row_count = len(out)
        self._stamp = stamp
        self._at = now
        log.info("%s を読みました: %d 件 (%s)", TABLE_NAME, len(out), path)
        return dict(out)

    # ------------------------------------------------------------
    def apply_to(self, master, db_path: str, force: bool = False) -> int:
        """サイズ構成へ上書きする。上書きした件数を返す。"""
        rows = self.load(db_path, force=force)
        if not rows:
            return 0
        return master.apply_label_overlay(rows)
