"""発注履歴と、2週間の重複チェック (VBA `CheckHistoryLotNo` / `SaveToHistory`)

発注票を起こす前に「このロット、最近も出していないか」を調べる。
同じロットの資材を二重に発注すると、倉庫に余る。

【VBA との違い: 置き場所】
元はブック内の隠しシートだったので、**端末ごと(ブックの写しごと)に
履歴が別**だった。ここでは手元の DB に持つ。共有フォルダに置けば
全端末で1つになり、重複検出が今より効くようになる ── **これは挙動の
変更**なので、置き場所の判断は `docs/不明点.md` Q3 のまま保留にして、
どちらでも動く形(追記しかしない)にしてある。

【VBA の `FindNext` は別の列を見ていた】
`Find` は F列(LotNo)だが `FindNext` が E列(長さ)で、同じロットが
複数あると2件目以降の判定が別の列で行われていた。ここでは LotNo で
統一している(`docs/不明点.md` Q4)。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from . import config, db
from .logging_utils import get_logger

log = get_logger("order_history")

UNIT = "本"


@dataclass
class Duplicate:
    """2週間以内に同じロットで出ていた1件。"""
    LotNo: str = ""
    処理日: str = ""
    担当者: str = ""
    サイズ: str = ""
    数量: str = ""
    日数: int = 0

    @property
    def 日数表示(self) -> str:
        """「本日」か「N日前」(VBA `dayText`)。"""
        return "本日" if self.日数 == 0 else f"{self.日数}日前"

    def message(self) -> str:
        """確認ダイアログに出す文面 (VBA の `MsgBox` と同じ並び)。"""
        return (f"ロット「{self.LotNo}」は {self.日数表示}"
                f"({self.処理日})に発注済みです。\n"
                f"担当者　： {self.担当者}\n"
                f"登録内容： {self.サイズ} / {self.数量}本")


def split_lots(text: str) -> list[str]:
    """空白区切りの LotNo を分ける (VBA `Split(Trim(newLot), " ")`)。"""
    return [p for p in str(text or "").replace("　", " ").split(" ") if p]


def _parse_day(text: str) -> Optional[date]:
    """処理日を日付にする。読めなければ None(VBA `IsDate` 相当)。

    保存しているのは `mm/dd` なので**年が無い**。年をまたぐと
    「12/31 が来年の 12/31」に見えてしまうため、登録日時(年つき)を
    優先して読む。
    """
    text = str(text or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def find_duplicates(conn: sqlite3.Connection, lots: Iterable[str], *,
                    within_days: int = config.ORDER_HISTORY_WARN_DAYS,
                    today: Optional[date] = None) -> list[Duplicate]:
    """最近その LotNo で発注していないか調べる。

    同じロットが何件あっても**1件だけ返す**(いちばん新しいもの)。
    VBA も最初に見つけた1件で確認を出して `Exit Do` していた。
    """
    today = today or date.today()
    found: list[Duplicate] = []
    seen: set[str] = set()

    for lot in lots:
        for part in split_lots(lot):
            if part in seen:
                continue
            seen.add(part)
            rows = db.fetch_all(
                conn,
                "SELECT * FROM 発注履歴 WHERE LotNo LIKE ?"
                " ORDER BY 登録日時 DESC, id DESC",
                (f"%{part}%",), caller_name="find_duplicates") or []
            for row in rows:
                when = _parse_day(row["登録日時"])
                if when is None:
                    continue
                diff = (today - when).days
                if 0 <= diff <= within_days:
                    found.append(Duplicate(
                        LotNo=part, 処理日=str(row["処理日"]),
                        担当者=str(row["担当者"]), サイズ=str(row["サイズ"]),
                        数量=str(row["数量"]), 日数=diff))
                    break
    return found


def save(conn: sqlite3.Connection, *, 処理日: str, 担当者: str, 品名: str,
         サイズ: str, 長さ: str, LotNo: str, 数量: str) -> None:
    """1件追記する (VBA `SaveToHistory`)。

    **追記しかしない。** 共有フォルダに置いても行の更新競合が起きない。
    """
    with conn:
        conn.execute(
            "INSERT INTO 発注履歴"
            " (処理日, 担当者, 品名, サイズ, 長さ, LotNo, 数量, 単位, 登録日時)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (処理日, 担当者, 品名, サイズ, 長さ, LotNo, 数量, UNIT,
             db.now_db_string()))


def save_many(conn: sqlite3.Connection, entries: list[dict]) -> int:
    """まとめて追記する。1つの発注票ぶんを1回で書く。"""
    if not entries:
        return 0
    stamp = db.now_db_string()
    with conn:
        conn.executemany(
            "INSERT INTO 発注履歴"
            " (処理日, 担当者, 品名, サイズ, 長さ, LotNo, 数量, 単位, 登録日時)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(e["処理日"], e["担当者"], e["品名"], e["サイズ"], e["長さ"],
              e["LotNo"], e["数量"], UNIT, stamp) for e in entries])
    log.info("発注履歴に %d 件追記しました", len(entries))
    return len(entries)


def recent(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict]:
    """画面に出す履歴。新しい順。"""
    rows = db.fetch_all(
        conn, "SELECT * FROM 発注履歴 ORDER BY 登録日時 DESC, id DESC LIMIT ?",
        (limit,), caller_name="history_recent") or []
    return [dict(r) for r in rows]
