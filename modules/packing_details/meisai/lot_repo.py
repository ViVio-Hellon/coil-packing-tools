"""仕掛台帳の読み取り (VBA 標準モジュール `mDB` の移植)

    VBA                          -> Python
    ---------------------------------------------------------------
    mDB.GetLotInfo                -> find_lot()
    mDB.GetWariSuFromShikakari     -> find_warisu()
    mDB.GetJuchuBangouList          -> find_order_nos()
    mDB.GetOdrInfo                   -> find_spec_no()
    mDB.SearchLotAll                  -> search()
    mDB.OpenAdo / SafeStr              -> 不要(手元のSQLite・プレースホルダ)

【VBAのSQLとの対応】
VBA は取り込み元(Access)を直接開いていた。こちらは取り込み済みの
手元のテーブルを引くので、列名が全角になり、`TOP 1` が `LIMIT 1` に、
`ｵｰﾀﾞｰ板丈='0'` が `オーダー板丈 = 0` になる。**それ以外の条件は
1つも足していないし、減らしてもいない。**

`オーダー板丈` が文字列比較から数値比較に変わる理由は
`import_specs` の説明を参照(取り込み元の実値は `'0'` ではなく `'0.0'`)。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from . import db
from .logging_utils import get_logger

log = get_logger("lot_repo")


# ------------------------------------------------------------------
# 値の読み方
# ------------------------------------------------------------------
def val(value: object) -> float:
    """VBA `Val()` 相当。**先頭から数値として読める分だけ**を返す。

    VBA の `Val("12abc")` は 12 を、`Val("")` と `Val("abc")` は 0 を
    返す。Python の `float()` は例外を投げるので、そのまま置き換えると
    **台帳に空欄や単位付きの値があるだけで画面が落ちる**。

    VBA と同じく、全角数字は読めない(`Val("１２")` は 0)。ここで
    全角を受けると、VBAでは0になる値がPythonでは12になり、表示が
    食い違う。
    """
    text = str(value if value is not None else "").strip()
    out = ""
    for ch in text:
        if ch.isdigit() and ch.isascii():
            out += ch
        elif ch == "." and "." not in out:
            out += ch
        elif ch in "+-" and not out:
            out += ch
        else:
            break
    try:
        return float(out)
    except ValueError:
        return 0.0


@dataclass
class LotInfo:
    """ロット情報 (VBA `TLotInfo`)。"""

    found: bool = False
    lot_no: str = ""
    yoto_code: str = ""          # 用途ｺｰﾄﾞ
    yoto_name: str = ""          # 用途名
    zaishitsu: str = ""          # 製造材質
    choshitsu: str = ""          # 製造調質
    seizou_thickness: float = 0.0   # 製造板厚
    seizou_width: float = 0.0       # 製造板幅
    odr_thickness: float = 0.0      # ｵｰﾀﾞｰ板厚
    odr_width: float = 0.0          # ｵｰﾀﾞｰ板幅
    sekkei_course: str = ""      # 設計_設備ｺｰｽ
    jisseki_course: str = ""     # 実績_設備ｺｰｽ
    # 別DB(LS4LOT)から引く。前工程実績数と丈数の源
    tate_wari: int = 0           # 当工程設計_縦割数
    yoko_wari: int = 0           # 当工程設計_横割数

    @property
    def zen_kotei_jisseki_su(self) -> int:
        """前工程実績数 = 縦割数 × 横割数 (VBA `ZenKoteiJissekiSu`)。"""
        return self.tate_wari * self.yoko_wari

    # --- 表示書式 (VBA `DisplayLotInfo` の Format) ---
    @property
    def odr_thickness_text(self) -> str:
        return f"{self.odr_thickness:.3f}"

    @property
    def odr_width_text(self) -> str:
        return f"{self.odr_width:.1f}"

    @property
    def seizou_thickness_text(self) -> str:
        return f"{self.seizou_thickness:.3f}"

    @property
    def seizou_width_text(self) -> str:
        return f"{self.seizou_width:.1f}"


@dataclass
class OrderInfo:
    """受注1件 (VBA `clsOdrInfo`)。"""

    order_no: str = ""           # 受注番号
    spec_no: str = ""            # 包装仕様NO
    customer: str = ""           # 取引先名称
    deliver_to: str = ""         # 納入先名称


@dataclass
class SearchResult:
    """ロット1本ぶんの検索結果 (VBA `SearchLotAll` の3つの戻り値)。"""

    found: bool = False
    lot: LotInfo = field(default_factory=LotInfo)
    orders: list[OrderInfo] = field(default_factory=list)
    message: str = ""


# ------------------------------------------------------------------
# 個別の引き方
# ------------------------------------------------------------------
def find_lot(conn: sqlite3.Connection, lot_no: str) -> LotInfo:
    """VBA `mDB.GetLotInfo` の移植。

        SELECT TOP 1 ... FROM 仕掛
         WHERE ﾛｯﾄ番号 = ? AND ｵｰﾀﾞｰ板丈 = '0'

    **縦割数・横割数もここで引く。** VBA も `GetLotInfo` の中から
    `GetWariSuFromShikakari` を呼んでおり、ロット情報を取れたときだけ
    別DBを見に行く。呼ぶ順を変えると、ロットが無いのにLS4LOTだけ
    引きに行く形になる。
    """
    row = db.fetch_one(
        conn,
        "SELECT * FROM 仕掛ロット"
        " WHERE ロット番号 = ? AND オーダー板丈 = 0"
        " ORDER BY 管理番号 LIMIT 1",
        (lot_no,), caller_name="lot_repo.find_lot")

    if row is None:
        log.debug("find_lot: 該当データなし ロット番号=%s", lot_no)
        return LotInfo(found=False, lot_no=lot_no)

    tate, yoko = find_warisu(conn, lot_no)
    lot = LotInfo(
        found=True,
        lot_no=lot_no,
        yoto_code=row["用途コード"] or "",
        yoto_name=row["用途名"] or "",
        zaishitsu=row["製造材質"] or "",
        choshitsu=row["製造調質"] or "",
        seizou_thickness=val(row["製造板厚"]),
        seizou_width=val(row["製造板幅"]),
        odr_thickness=val(row["オーダー板厚"]),
        odr_width=val(row["オーダー板幅"]),
        sekkei_course=row["設計_設備コース"] or "",
        jisseki_course=row["実績_設備コース"] or "",
        tate_wari=tate,
        yoko_wari=yoko,
    )
    log.debug("find_lot: 取得成功 用途名=%s 縦割=%s 横割=%s 前工程実績数=%s",
              lot.yoto_name, tate, yoko, lot.zen_kotei_jisseki_su)
    return lot


def find_warisu(conn: sqlite3.Connection, lot_no: str) -> tuple[int, int]:
    """VBA `mDB.GetWariSuFromShikakari` の移植。`(縦割数, 横割数)`。

    **見つからなければ (0, 0)。** VBA もエラー時・該当なし時は両方0に
    落としている。そのまま前工程実績数0になり、利用者が手で入れ直す
    (ギミックA)。ここで例外にすると、LS4LOTに載っていないロットを
    開いただけで作業が止まる。
    """
    row = db.fetch_one(
        conn,
        "SELECT 当工程設計_縦割数, 当工程設計_横割数 FROM 仕掛当工程"
        " WHERE ロット番号 = ? ORDER BY 管理番号 LIMIT 1",
        (lot_no,), caller_name="lot_repo.find_warisu")
    if row is None:
        log.debug("find_warisu: 該当データなし ロット番号=%s", lot_no)
        return 0, 0
    return int(row["当工程設計_縦割数"] or 0), int(row["当工程設計_横割数"] or 0)


def find_order_nos(conn: sqlite3.Connection, lot_no: str) -> list[str]:
    """VBA `mDB.GetJuchuBangouList` の移植。**初出順**で重複を除く。

    VBA は `Collection` へ順に足し、既にあれば飛ばしていた
    (`InCollection`)。並びは取り込み元の物理順。`MIN(管理番号)` で
    並べると取り込み順＝元の物理順になり、同じ順序を再現できる。
    """
    rows = db.fetch_all(
        conn,
        "SELECT 受注番号 FROM 仕掛引当"
        " WHERE ロット番号 = ? AND 受注番号 <> ''"
        " GROUP BY 受注番号 ORDER BY MIN(管理番号)",
        (lot_no,), caller_name="lot_repo.find_order_nos") or []
    return [r["受注番号"] for r in rows]


def find_spec_no(conn: sqlite3.Connection, order_no: str) -> OrderInfo:
    """VBA `mDB.GetOdrInfo` の移植。

    **見つからなくても受注番号は返す。** VBA も `r.juchuBangou` を先に
    入れてから検索しており、該当が無ければ包装仕様NOが空のまま返る。
    画面はそれを空欄として出し、リンクも張らない。実データでは
    引当側の受注番号の19%がここに無い。
    """
    row = db.fetch_one(
        conn,
        "SELECT 包装仕様NO, 取引先名称, 納入先名称 FROM 仕掛受注"
        " WHERE 受注番号 = ? ORDER BY 管理番号 LIMIT 1",
        (order_no,), caller_name="lot_repo.find_spec_no")
    if row is None:
        log.debug("find_spec_no: 該当データなし 受注番号=%s", order_no)
        return OrderInfo(order_no=order_no)
    return OrderInfo(
        order_no=order_no,
        spec_no=row["包装仕様NO"] or "",
        customer=row["取引先名称"] or "",
        deliver_to=row["納入先名称"] or "",
    )


# ------------------------------------------------------------------
# まとめて引く
# ------------------------------------------------------------------
def search(conn: sqlite3.Connection, lot_no: str) -> SearchResult:
    """VBA `mDB.SearchLotAll` の移植。ロット番号1本で全部引く。

    VBA は7桁入力された時点で自動検索していた(`txtLotNo_Change`)。
    桁数の判定は呼び出し側(画面)の責務。ここは空だけ弾く。
    """
    lot_no = (lot_no or "").strip().upper()
    if not lot_no:
        return SearchResult(found=False, message="ロット番号を入力してください。")

    lot = find_lot(conn, lot_no)
    if not lot.found:
        return SearchResult(found=False, lot=lot,
                            message="該当するﾛｯﾄ情報が見つかりません。")

    orders = [find_spec_no(conn, no) for no in find_order_nos(conn, lot_no)]
    log.debug("search: 完了 ロット番号=%s 受注件数=%s", lot_no, len(orders))
    return SearchResult(found=True, lot=lot, orders=orders,
                        message=f"ロット {lot_no}")
