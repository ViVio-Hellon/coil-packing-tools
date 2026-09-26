"""資材発注管理チェックリスト (VBA `資材配列` ほかと `資材発注印字`)

計算結果を1行ぶんの帳票データに組み替えて、15行の表へ積む。

【1セルに2行入る】
リプラは最下部とコイル間で長さが違うので、**1つのセルに改行で2つ**
入れている(`最下部長さ \\n コイル間長さ`)。発注票を起こすときは
ここを分解して2レコードに戻す(`order_sheet_service`)。

【2行になる品がある】
1C1282(ｱｲｴﾑｱｲｶﾊﾞｰ)はリプラを3種類、1C1297(ｺﾄﾌﾞｷｾｲﾐﾂ)は最下部と間で
種類が違うので、**チェックリストへ2行**書く。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from . import config, db, ripla_service, special_rules, vba
from .logging_utils import get_logger
from .models import CalcState, ChecklistRow

log = get_logger("checklist_service")

# パレット名称も一緒に出す種類。VBA `資材配列` の分岐
TYPES_WITH_NAME = ("スカシ", "ｸｰﾗｰﾌｨﾝ")

LF = "\n"


def today_label() -> str:
    """依頼日の表記 (VBA `Format$(Now, "mm/dd")`)。"""
    return datetime.now().strftime("%m/%d")


# ==================================================================
# 1行を組む
# ==================================================================
def _pallet_text(state: CalcState) -> str:
    """パレット欄。スカシとｸｰﾗｰﾌｨﾝだけ名称を挟んで2行にする。"""
    if state.パレット種類 in TYPES_WITH_NAME:
        return f"{state.パレット種類}{state.パレット名称}{LF}{state.パレットサイズ}"
    return f"{state.パレット種類}{state.パレットサイズ}"


def _unit_count(state: CalcState) -> str:
    """台数欄。別枠(1本積み不可で分けたぶん)があれば足す。"""
    if str(state.Re_台数).strip():
        return vba.int_text(state.Re_台数値 + state.台数値)
    return vba.int_text(state.台数値)


def _normalize_bottom(state: CalcState) -> None:
    """最下部がリプラでないときの後始末 (VBA の `SkipA` の手前)。

    `長い本数` が 0 なら最下部にリプラを敷かない品なので、長さも本数も
    空にする。**`state` を書き換える**のは VBA と同じ ── このあと
    2行目を組むときに、消えたことが前提になっている。

    `長い本数` が空のまま来ることがある(連続で押したとき)。VBA は
    そこで型違いのエラーになるため `GoTo SkipA` で避けていた。
    """
    if not str(state.長い本数).strip():
        return
    if vba.val(state.長い本数) == 0:
        state.最下部長さ = ""
        state.長い本数 = ""


def _cushion_text(state: CalcState) -> str:
    return f"{state.緩衝材}{LF}{state.HB枚数}枚"


def _base_row(state: CalcState, *, worker: str, size_fixed: bool) -> ChecklistRow:
    """どの種類でも共通の欄を埋めた行。"""
    return ChecklistRow(
        依頼日=today_label(),
        LotNo=state.LOT,
        用途名=state.order.用途名,
        パレット種類=_pallet_text(state),
        台数=_unit_count(state),
        営業納期=state.order.営業納期,
        リプラサイズ=ripla_service.ripla_size_label(state.リプラサイズ),
        緩衝材=_cushion_text(state),
        依頼者=worker,
        入荷日="",
        サイズ確定=size_fixed,
    )


def standard_row(state: CalcState, *, worker: str, size_fixed: bool = False) -> ChecklistRow:
    """ふつうの1行 (VBA `資材配列`)。"""
    _normalize_bottom(state)
    row = _base_row(state, worker=worker, size_fixed=size_fixed)
    row.コイル間 = state.コイル間
    row.リプラ長さ = f"{state.最下部長さ}{LF}{state.リプラ長さ}"
    # **改行より算術が先**。VBA の `.長い本数 & vbLf & val(.本数) + val(.短い本数)`
    # は「長い本数」改行「本数 + 短い本数」になる
    row.リプラ本数 = (f"{state.長い本数}{LF}"
                      f"{vba.int_text(vba.val(state.本数) + vba.val(state.短い本数))}")
    return row


def imi_cover_rows(state: CalcState, *, worker: str,
                   size_fixed: bool = False) -> list[ChecklistRow]:
    """1C1282 ｱｲｴﾑｱｲｶﾊﾞｰ。リプラ3種類なので2行に分ける。

    1行目 … 最下部の長いの + コイル間
    2行目 … 最下部の短いの + 中間
    """
    _normalize_bottom(state)

    first = _base_row(state, worker=worker, size_fixed=size_fixed)
    first.コイル間 = state.コイル間
    first.リプラ長さ = f"{state.最下部長さ}{LF}{state.リプラ長さ}"
    # ここは短い本数を足さない (2025.9.8 の修正)
    first.リプラ本数 = f"{state.長い本数}{LF}{vba.int_text(state.本数)}"

    second = ChecklistRow(
        LotNo=state.LOT,
        コイル間=state.コイル間,
        リプラ長さ=f"{state.最下部長さ_短}{LF}{state.IMI中間長さ}",
        リプラ本数=f"{state.短い本数}{LF}{state.IMI中間本数}",
        リプラサイズ=ripla_service.ripla_size_label(state.リプラサイズ),
    )
    return [first, second]


def kotobuki_rows(state: CalcState, *, worker: str,
                  size_fixed: bool = False) -> list[ChecklistRow]:
    """1C1297 ｶ)ｺﾄﾌﾞｷｾｲﾐﾂ。最下部と間でリプラ種類が違うので2行に分ける。

    1行目 … 間リプラ種類 + コイル間の長さ・本数
    2行目 … 最下部リプラ種類 + 最下部の長さ・本数
    """
    _normalize_bottom(state)

    first = _base_row(state, worker=worker, size_fixed=size_fixed)
    first.コイル間 = state.間リプラ種類
    first.リプラ長さ = state.リプラ長さ
    first.リプラ本数 = vba.int_text(state.本数)

    second = ChecklistRow(
        LotNo=state.LOT,
        コイル間=state.最下部リプラ種類,
        リプラ長さ=state.最下部長さ,
        リプラ本数=state.長い本数,
        リプラサイズ=ripla_service.ripla_size_label(state.リプラサイズ),
    )
    return [first, second]


def build_rows(state: CalcState, *, worker: str,
               size_fixed: bool = False) -> list[ChecklistRow]:
    """特殊フラグを見て、1行か2行かを決める (VBA `CommandButton6_Click`)。"""
    if state.特殊Flag == special_rules.IMI_COVER_FLAG:
        return imi_cover_rows(state, worker=worker, size_fixed=size_fixed)
    if state.特殊Flag == special_rules.KOTOBUKI_FLAG:
        return kotobuki_rows(state, worker=worker, size_fixed=size_fixed)
    return [standard_row(state, worker=worker, size_fixed=size_fixed)]


# ==================================================================
# 15行の表へ積む
# ==================================================================
REASON_ROW_OUT_OF_RANGE = "row_out_of_range"


def save_rows(conn: sqlite3.Connection, row_no: int,
              rows: list[ChecklistRow]) -> None:
    """指定の行へ書く (VBA `資材発注印字`)。

    2行になる品は `行番号` は同じで `枝番` が 0, 1 になる。VBA は
    `i` と `i + 1` の2行へ書いていたが、そうすると次の行を人が選ぶときに
    1つ飛ばす必要がある。**1つの計算結果は1行番号**にまとめる。
    """
    if not (1 <= row_no <= config.CHECKLIST_ROWS):
        raise ValueError(f"行番号は 1〜{config.CHECKLIST_ROWS} です: {row_no}")

    with conn:
        conn.execute("DELETE FROM 発注チェックリスト WHERE 行番号 = ?", (row_no,))
        for branch, row in enumerate(rows):
            values = {
                "行番号": row_no,
                "枝番": branch,
                "サイズ確定": "〆" if row.サイズ確定 else "",
                "更新日時": db.now_db_string(),
            }
            for name in ChecklistRow.ORDER:
                values[name] = getattr(row, name)
            cols = ", ".join(f'"{c}"' for c in values)
            marks = ", ".join("?" for _ in values)
            conn.execute(
                f"INSERT INTO 発注チェックリスト ({cols}) VALUES ({marks})",
                list(values.values()))
    log.info("チェックリスト %d 行目に %d 件書きました", row_no, len(rows))


def load_all(conn: sqlite3.Connection) -> list[dict]:
    """15行ぶんを行番号順に返す。空の行は含めない。"""
    rows = db.fetch_all(
        conn, "SELECT * FROM 発注チェックリスト ORDER BY 行番号, 枝番",
        caller_name="checklist_load") or []
    return [dict(r) for r in rows]


def clear_row(conn: sqlite3.Connection, row_no: int) -> None:
    with conn:
        conn.execute("DELETE FROM 発注チェックリスト WHERE 行番号 = ?", (row_no,))


def clear_all(conn: sqlite3.Connection) -> None:
    """チェックリストを空にする(VBA の「ﾁｪｯｸﾘｽﾄ発行」= 原紙の貼り直し)。"""
    with conn:
        conn.execute("DELETE FROM 発注チェックリスト")


def used_rows(conn: sqlite3.Connection) -> set[int]:
    rows = db.fetch_all(
        conn, "SELECT DISTINCT 行番号 FROM 発注チェックリスト",
        caller_name="checklist_used") or []
    return {int(r["行番号"]) for r in rows}


def next_free_row(conn: sqlite3.Connection) -> Optional[int]:
    """空いている最初の行。埋まっていれば None。"""
    used = used_rows(conn)
    for n in range(1, config.CHECKLIST_ROWS + 1):
        if n not in used:
            return n
    return None
