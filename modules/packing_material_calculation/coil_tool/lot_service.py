"""ロットと受注を引く (VBA `LOT検索` / `フォーム展開`)

    LotNo(7桁) ──仕掛引当──▶ 受注番号の候補
    受注番号(8桁) ──仕掛受注──▶ 計算に要る値をぜんぶ

【候補は「両方にあるもの」だけ】
VBA は仕掛引当の行と仕掛受注の行を突き合わせて、**どちらにも居る
受注番号だけ**をコンボへ入れていました。引当はあるが受注が消えている
番号を選ばせても、そのあと何も展開できないためです。

【`"データなし"` をそのまま持ち込む】
値が 0 の欄には文字列 `"データなし"` を入れます。`台数計算` が
`IsNumeric` で「指定なし」と読むので、数値へ寄せると優先順位が変わります。
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from . import db, vba
from .logging_utils import get_logger
from .models import CalcState, OrderInfo

log = get_logger("lot_service")

# VBA の入力制限。LotNo は7桁、受注番号は8桁でそれぞれ検索が走る
LOT_NO_LENGTH = 7
ORDER_NO_LENGTH = 8

# 包装仕様NO は先頭6桁だけを使う (VBA `Left(..., 6)`)
SPEC_NO_LENGTH = 6


def normalize_lot_no(text: str) -> str:
    """LotNo の入力整形 (VBA `Lot_KeyPress`)。

    英数字だけを通し、小文字は大文字に直す。現場は英字を小文字で
    打つことがあり、そのままだと引当に当たらない。
    """
    out = []
    for ch in str(text or ""):
        if ch.isdigit() or ("A" <= ch <= "Z"):
            out.append(ch)
        elif "a" <= ch <= "z":
            out.append(ch.upper())
    return "".join(out)


def find_order_numbers(conn: sqlite3.Connection, lot_no: str) -> list[str]:
    """LotNo から受注番号の候補を返す (VBA `LOT検索`)。

    **仕掛引当と仕掛受注の両方にある番号だけ。** 重複は落とす
    (VBA は引当の行数だけコンボへ追加していたので、同じ番号が
    複数回並ぶことがあった)。
    """
    lot_no = normalize_lot_no(lot_no)
    if not lot_no:
        return []
    rows = db.fetch_all(
        conn,
        "SELECT DISTINCT h.受注番号 AS no FROM 仕掛引当 h"
        " JOIN 仕掛受注 o ON o.受注番号 = h.受注番号"
        " WHERE h.ロット番号 = ? ORDER BY h.受注番号",
        (lot_no,), caller_name="find_order_numbers") or []
    return [r["no"] for r in rows]


def shipping_date(conn: sqlite3.Connection, lot_no: str) -> str:
    """そのロットの出荷日 (VBA `LOT検索` の `.出荷`)。

    **あとで `expand_order` が営業納期で上書きする。** VBA も同じ順で
    書いていて、帳票の「営業納期」欄に出るのは受注側の営業納期のほう。
    ここはロットだけ引いた段階の表示に使う。
    """
    lot_no = normalize_lot_no(lot_no)
    if not lot_no:
        return ""
    row = db.fetch_one(
        conn, "SELECT 出荷日 FROM 仕掛引当 WHERE ロット番号 = ? LIMIT 1",
        (lot_no,), caller_name="shipping_date")
    return str(row["出荷日"]) if row is not None else ""


def _or_no_data(value: float, decimals: Optional[int] = None) -> str:
    """0 なら「データなし」、それ以外は値。

    VBA `フォーム展開` の
    `If TempHiki(1, C_…) <> 0 Then … Else "データなし"` の移植。
    """
    if value == 0:
        return OrderInfo.NO_DATA
    # 小数を切り捨てない(統合 1.2.6)。VBA は値をそのままテキストボックスへ入れる
    # (`.梱包単位_重量 = …`)。以前は整数にしていて、外径 1160.5 が 1160、重量 1000.5 が 1000 になり、
    # パレットの選び方・積数・チェックリスト・発注履歴へそのまま乗っていた。整数の値は今までどおり
    return vba.fmt(value, decimals) if decimals is not None else vba.num_text(value)


def expand_order(conn: sqlite3.Connection, order_no: str) -> Optional[OrderInfo]:
    """受注番号から計算に要る値を展開する (VBA `フォーム展開`)。"""
    order_no = str(order_no or "").strip()
    if not order_no:
        return None
    row = db.fetch_one(
        conn, "SELECT * FROM 仕掛受注 WHERE 受注番号 = ?", (order_no,),
        caller_name="expand_order")
    if row is None:
        return None

    data = dict(row)
    info = OrderInfo(
        受注番号=str(data.get("受注番号", "")),
        受注材質=str(data.get("受注材質", "")),
        受注調質=str(data.get("受注調質", "")),
        受注板厚=vba.fmt(data.get("受注板厚"), 3),
        受注板幅=vba.fmt(data.get("受注板幅"), 1),
        用途コード=str(data.get("用途コード", "")),
        用途名=str(data.get("用途名", "")),
        包装仕様NO=str(data.get("包装仕様NO", ""))[:SPEC_NO_LENGTH],
        納入先名称=str(data.get("納入先名称", "")),
        取引先名称=str(data.get("取引先名称", "")),
        製品単重=vba.fmt(data.get("製品単重"), 2),
        梱包単位_重量=_or_no_data(vba.val(data.get("梱包単位_重量"))),
        梱包単位_枚数=_or_no_data(vba.val(data.get("梱包単位_枚数"))),
        コイル外径_MAX=_or_no_data(vba.val(data.get("コイル外径_MAX"))),
        コイル外径_目標=_or_no_data(vba.val(data.get("コイル外径_目標"))),
        コイル外径_MIN=_or_no_data(vba.val(data.get("コイル外径_MIN"))),
        コイル内径_目標=_or_no_data(vba.val(data.get("コイル内径_目標"))),
        工場用コメント=str(data.get("工場用コメント", "")),
        # 帳票の「営業納期」欄はこれ。VBA も `.出荷` へ入れ直している
        営業納期=str(data.get("営業納期", "")),
        比重=vba.int_text(vba.val(data.get("材質_比重")))
        if vba.val(data.get("材質_比重")) == int(vba.val(data.get("材質_比重")))
        else str(vba.val(data.get("材質_比重"))),
        梱包コード=str(data.get("梱包コード", "")),
    )
    info.出荷日 = info.営業納期
    return info


def load_into(conn: sqlite3.Connection, state: CalcState, order_no: str) -> bool:
    """受注情報を画面の状態へ入れる。外径の既定値もここで入れる。

    VBA は `ｺｲﾙ外径_MAX` が 0 でなければ外径欄へ入れていた。現場は
    そのまま使うことも、打ち直すこともある。
    """
    info = expand_order(conn, order_no)
    if info is None:
        return False
    state.order = info
    if info.コイル外径_MAX != OrderInfo.NO_DATA:
        state.外径 = info.コイル外径_MAX
    return True
