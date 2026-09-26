"""発注票 (VBA `転記` → `Ripla_Count` → `注文票入力`)

チェックリストに溜めたものを、**倉庫へ出す形**に組み替える。

    チェックリスト(ロット単位・1セルに2つ)
        │  転記 … 改行で入っている2つを2レコードに戻す
        ▼
    リプラ / LVS ごとの明細(Lot, 種類, 角サイズ, 長さ, 本数)
        │  集計 … 角サイズ + 長さ をキーに本数を足す
        ▼
    発注票(角サイズごとに1枚。長さの行に本数とロットを並べる)

【角サイズは3種類まで】
30×40 / 40×60 / 80×80。VBA も最大3枚しか作らない。
**80×80 は長さを持たない**ので、長さの行は使わない。

【倉庫の定尺に無い長さ】
発注票の原紙は定尺8種+空欄4行でできていて、定尺に無い長さは空欄へ
書き足す。**5件以上あると入らない**ので、そこで断る。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import checklist_service, config, order_history, vba
from .logging_utils import get_logger

log = get_logger("order_sheet_service")

LF = "\n"

# 倉庫在庫の定尺(mm)。VBA は発注票の原紙(A5:A12)に直接書いていた。
# **設定で変えられるようにしてある**(`docs/不明点.md` Q8)
DEFAULT_STOCK_LENGTHS: tuple[int, ...] = (700, 800, 900, 950, 1000, 1050, 1100, 1150)

# 定尺に無い長さを書き足せる行数(原紙の A13:A16)
EXTRA_ROWS = 4

# 発注票1枚の行数(定尺 + 書き足し)
SHEET_ROWS = len(DEFAULT_STOCK_LENGTHS) + EXTRA_ROWS

# 1回で作れる発注票の枚数。角サイズの種類ぶん
MAX_SHEETS = 3

# コイル間に入るもの。この2つだけが発注票になる
KINDS = ("リプラ", "LVS")

REASON_TOO_MANY_SIZES = "too_many_extra_lengths"
REASON_DUPLICATE_LOT = "duplicate_lot"
REASON_EMPTY = "nothing_to_order"


def stock_lengths() -> list[int]:
    """いま使う定尺。設定があればそちら。"""
    from . import user_settings
    configured = user_settings.get(config.KEY_STOCK_LENGTHS)
    if isinstance(configured, list) and configured:
        return [int(vba.val(n)) for n in configured if vba.val(n) > 0]
    return list(DEFAULT_STOCK_LENGTHS)


# ==================================================================
# 転記 ── 1セルに2つ入っているものを2レコードに戻す
# ==================================================================
@dataclass
class Detail:
    """明細1件。"""
    LotNo: str = ""
    種類: str = ""       # リプラ / LVS
    角サイズ: str = ""   # "30×40" など
    長さ: str = ""
    本数: str = ""


def expand(rows: list[dict]) -> list[Detail]:
    """チェックリストの行を明細に開く (VBA `転記`)。

    リプラ長さ・リプラ本数は改行で2つ入っていることがある。
    **改行が無ければ1件のまま**にする ── 以前は必ず2行書いていて、
    改行の無い行が2重に数えられていた(コード冒頭のコメントにある修正)。
    """
    out: list[Detail] = []
    for row in rows:
        kind = str(row.get("コイル間", "") or "")
        if kind not in KINDS:
            continue

        lengths = str(row.get("リプラ長さ", "") or "").split(LF)
        counts = str(row.get("リプラ本数", "") or "").split(LF)
        lot = str(row.get("LotNo", "") or "")
        size = str(row.get("リプラサイズ", "") or "")

        out.append(Detail(LotNo=lot, 種類=kind, 角サイズ=size,
                          長さ=lengths[0].strip() if lengths else "",
                          本数=counts[0].strip() if counts else ""))

        if len(lengths) > 1 or len(counts) > 1:
            out.append(Detail(
                LotNo=lot, 種類=kind, 角サイズ=size,
                長さ=lengths[-1].strip() if len(lengths) > 1 else "",
                本数=counts[-1].strip() if len(counts) > 1 else ""))
    return out


# ==================================================================
# 集計 ── 角サイズ + 長さ で本数を足す
# ==================================================================
@dataclass
class Aggregated:
    角サイズ: str = ""
    長さ: str = ""
    本数: int = 0
    lots: list[str] = field(default_factory=list)


def aggregate(details: Iterable[Detail], kind: str) -> list[Aggregated]:
    """同じ角サイズ・同じ長さのものを1件にまとめる (VBA `Ripla_Count`)。

    ロットは**出てきた順のまま重複を落とす**。発注票のロット欄は
    倉庫が現物と突き合わせるためのものなので、並びが動くと読みにくい。
    """
    table: dict[tuple[str, str], Aggregated] = {}
    for d in details:
        if d.種類 != kind:
            continue
        if not d.長さ and not d.本数:
            continue
        key = (d.角サイズ, d.長さ)
        entry = table.setdefault(key, Aggregated(角サイズ=d.角サイズ, 長さ=d.長さ))
        entry.本数 += int(vba.val(d.本数))
        if d.LotNo and d.LotNo not in entry.lots:
            entry.lots.append(d.LotNo)
    return list(table.values())


# ==================================================================
# 発注票を組む
# ==================================================================
@dataclass
class SheetRow:
    長さ: str = ""
    LotNo: str = ""
    数量: str = ""


@dataclass
class OrderSheet:
    """発注票1枚。"""
    種類: str = ""        # リプラ / LVS
    角サイズ: str = ""    # "30×40" など
    提出日付: str = ""
    依頼者: str = ""
    rows: list[SheetRow] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return any(r.数量 for r in self.rows)


@dataclass
class BuildResult:
    sheets: list[OrderSheet] = field(default_factory=list)
    duplicates: list[order_history.Duplicate] = field(default_factory=list)
    ok: bool = False
    reason: str = ""
    # 定尺に無くて書き足した長さ。画面に出して倉庫へ伝える
    extra_lengths: list[str] = field(default_factory=list)


def _sizes_in(entries: list[Aggregated]) -> list[str]:
    """出てきた角サイズを、出てきた順のまま重複なしで。最大3種。"""
    seen: list[str] = []
    for e in entries:
        if e.角サイズ and e.角サイズ not in seen:
            seen.append(e.角サイズ)
    return seen[:MAX_SHEETS]


def _build_sheet(entries: list[Aggregated], *, kind: str, size: str,
                 worker: str, day: str) -> tuple[Optional[OrderSheet], list[str], str]:
    """角サイズ1つぶんの発注票。戻り値は `(票, 書き足した長さ, 断る理由)`。"""
    mine = [e for e in entries if e.角サイズ == size]
    sheet = OrderSheet(種類=kind, 角サイズ=size, 提出日付=day, 依頼者=worker)

    # 80×80 は長さを持たない。枠だけ作って長さ行は使わない
    if size == "80×80":
        sheet.rows = [SheetRow() for _ in range(SHEET_ROWS)]
        return sheet, [], ""

    lengths = stock_lengths()
    wanted = [e.長さ for e in mine if e.長さ]
    extra = [n for n in dict.fromkeys(wanted)
             if int(vba.val(n)) not in lengths]

    if len(extra) > EXTRA_ROWS:
        return None, extra, REASON_TOO_MANY_SIZES

    slots = [str(n) for n in lengths] + extra
    slots += [""] * (SHEET_ROWS - len(slots))

    rows: list[SheetRow] = []
    for slot in slots[:SHEET_ROWS]:
        row = SheetRow(長さ=slot)
        if slot:
            for e in mine:
                if e.長さ and vba.val(e.長さ) == vba.val(slot):
                    # 0 本は空欄にする。VBA も「空白をサイズとして
                    # とらえてしまうため」0 を消していた (2022.09.21)
                    row.数量 = str(e.本数) if e.本数 else ""
                    row.LotNo = " ".join(e.lots)
        rows.append(row)
    sheet.rows = rows
    return sheet, extra, ""


def build(conn: sqlite3.Connection, *, worker: str,
          check_history: bool = True) -> BuildResult:
    """チェックリストから発注票を起こす。

    **履歴の重複はここで返すだけ**で、止めはしない。続けるかどうかは
    人が決める(VBA も「このまま重複して発注しますか？」と聞いていた)。
    続けるときは `check_history=False` で呼び直す。
    """
    rows = checklist_service.load_all(conn)
    if not rows:
        return BuildResult(ok=False, reason=REASON_EMPTY)

    details = expand(rows)
    day = checklist_service.today_label()

    result = BuildResult()
    for kind in KINDS:
        entries = aggregate(details, kind)
        if not entries:
            continue
        for size in _sizes_in(entries):
            sheet, extra, reason = _build_sheet(
                entries, kind=kind, size=size, worker=worker, day=day)
            if reason:
                return BuildResult(ok=False, reason=reason, extra_lengths=extra)
            if sheet is not None and (sheet.has_content or size == "80×80"):
                result.sheets.append(sheet)
                result.extra_lengths.extend(extra)

    if not result.sheets:
        return BuildResult(ok=False, reason=REASON_EMPTY)

    if check_history:
        lots = [r.LotNo for s in result.sheets for r in s.rows if r.LotNo]
        result.duplicates = order_history.find_duplicates(conn, lots)

    result.ok = True
    return result


def commit(conn: sqlite3.Connection, sheets: list[OrderSheet], *,
           worker: str) -> int:
    """出した内容を履歴へ残す (VBA `SaveToHistory` の呼び出し)。

    **長さと数量の両方がある行だけ**。空欄の行を残しても、次の重複
    チェックで引っかかるだけで意味が無い。
    """
    day = checklist_service.today_label()
    entries = [
        {"処理日": day, "担当者": worker, "品名": s.種類, "サイズ": s.角サイズ,
         "長さ": r.長さ, "LotNo": r.LotNo, "数量": r.数量}
        for s in sheets for r in s.rows
        if str(r.LotNo).strip() and str(r.数量).strip()
    ]
    return order_history.save_many(conn, entries)
