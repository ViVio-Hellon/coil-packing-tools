"""出力の業務ロジック (VBA `cmdKettei_Click` の移植)

紙を1枚出すまでにVBAがやっていたことを、順番どおりに並べ直したもの。

    1. 重量を読み直す            ReadWeights
    2. 積み上げ順に条番号を並べる   slotKeys
    3. 副番の重複を確かめる        IsDuplicate  (No指定時はスキップ)
    4. Noを決める                IncrementSeq / 指定値
    5. 明細を作る                CreateMeisaiSheet + WriteData
    6. 副番を登録する             RegisterFuban
    7. 印刷済フラグをOFFにする     SetPrinted False
    8. 積み上げだけリセットする     ClearGimmickC + BuildGimmickC

【VBAが確認を挟む場所は、ここでは「断り」として返す】
VBA は `MsgBox ... vbYesNo` で利用者に尋ね、「はい」なら続けていた。
Webでは1往復で決められないので、**まず確認事項を返し、利用者が
了解したら `confirm=True` でもう一度呼ぶ**形にする。判断そのもの
(何を尋ねるか・尋ねずに進めてよいか)はVBAと同じ。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from . import config, db, history_repo, slip_history, strand_service
from .logging_utils import get_logger
from .strand_service import Board

log = get_logger("meisai_service")

# 確認の種類。**文言から推し量らない**
CONFIRM_DUPLICATE = "duplicate"      # 副番がすでに出力済み
CONFIRM_OVERWRITE = "overwrite"      # そのNoはもうある
CONFIRM_BEYOND_SEQ = "beyond_seq"    # 現在連番を超えるNoを指定した

# 断りの種類
REFUSE_INCOMPLETE = "incomplete"     # スロットが埋まっていない
REFUSE_NO_WEIGHT = "no_weight"       # 重量が未入力
REFUSE_BAD_NO = "bad_no"             # No指定が数値でない
REFUSE_TOO_MANY = "too_many"         # 紙の行数を超える


@dataclass
class Confirm:
    """利用者に尋ねること (VBA の `MsgBox ... vbYesNo` 1つ分)。"""

    kind: str
    message: str
    detail: list[str] = field(default_factory=list)


@dataclass
class OutputResult:
    """出力の結果。"""

    ok: bool = False
    seq_no: int = 0
    confirms: list[Confirm] = field(default_factory=list)
    refuse: str = ""
    message: str = ""

    @property
    def needs_confirm(self) -> bool:
        return bool(self.confirms)


# ==================================================================
# 重量 (RULE-06)
# ==================================================================
def read_weights(values: list[object], jou_su: int) -> list[int]:
    """重量欄を読む (VBA `ReadWeights`)。

    VBA は `CLng(Val(ctl.Value))` で読み、数値でなければ
    「丈Nの重量が未入力です。」で止める。入力欄が数字以外を弾く
    (`clsWeightCtrl.Txt_KeyPress`)ので、**小数点も入らない＝整数**。

    足りない欄・空欄は**未入力として断る**。0を入れて進めると、
    紙に `0.0Kg` と印字されたまま現場に出る。
    """
    out: list[int] = []
    for index in range(jou_su):
        raw = values[index] if index < len(values) else None
        text = str(raw if raw is not None else "").strip()
        if text == "":
            raise strand_service.RefusedError(
                REFUSE_NO_WEIGHT, f"丈{index + 1}の重量が未入力です。")
        try:
            number = float(text)
        except ValueError:
            raise strand_service.RefusedError(
                REFUSE_NO_WEIGHT, f"丈{index + 1}の重量が未入力です。") from None
        # **小数を黙って切り捨てない**(統合 1.2.6)。VBA の入力欄は数字以外を弾くので
        # 小数は入らない。ツールでも日本語入力(IME)からは小数点が入りえて、以前は
        # 2502.7 を 2502 にして紙・履歴(共有)へ書いていた。違う数を書くより断る
        if number != number or number in (float("inf"), float("-inf")) or not number.is_integer():
            raise strand_service.RefusedError(
                REFUSE_NO_WEIGHT,
                f"丈{index + 1}の重量は整数で入力してください(小数点は使えません): {text}")
        out.append(int(number))
    return out


# ==================================================================
# 出力 (RULE-07 / RULE-08)
# ==================================================================
def output(conn: sqlite3.Connection, board: Board, *,
           specify_no: Optional[int] = None,
           confirm: bool = False,
           lot: Any = None, orders: Iterable[Any] = ()) -> OutputResult:
    """明細を1枚出す (VBA `cmdKettei_Click`)。

    `specify_no` は「No指定」チェックに相当。指定すると
    **副番の重複チェックを丸ごと飛ばす**(VBA と同じ。再出力用の
    逃げ道なので、同じ副番が出るのは織り込み済み)。

    `confirm=False` のうちは確認事項を返すだけで**何も書かない**。
    利用者が了解したら `confirm=True` で呼び直す。

    `lot` / `orders` は画面に出ているロット情報。**明細の履歴**に残す
    (`slip_history`。全ライン・3年。あとから「このコイルはどの紙か」を
    追う・数えるため)。
    """
    result = OutputResult()

    if not board.is_complete:
        result.refuse = REFUSE_INCOMPLETE
        result.message = "積み上げが埋まっていません。"
        return result

    keys = board.stacked_keys()
    if len(keys) > config.SHEET_ROWS:
        # VBA `WriteData` は16本目以降を黙って捨てるが、こちらは断る。
        # 積み条数の上限が15なのでふつうは起きない
        result.refuse = REFUSE_TOO_MANY
        result.message = (f"1枚に載るのは{config.SHEET_ROWS}行までです"
                          f"（いま{len(keys)}行）。")
        return result

    confirms: list[Confirm] = []

    # --- 副番の重複 (No指定時はスキップ) ---
    if specify_no is None:
        dup = history_repo.duplicates(conn, board.lot_no, keys)
        if dup:
            confirms.append(Confirm(
                CONFIRM_DUPLICATE,
                "以下の副番はすでに出力済みです。このまま出力しますか？",
                dup))

    # --- Noを決める ---
    if specify_no is not None:
        if specify_no < 1:
            result.refuse = REFUSE_BAD_NO
            result.message = "出力するNoを入力してください。"
            return result
        existing = db.fetch_one(
            conn, "SELECT 1 FROM 明細出力 WHERE ロット番号 = ? AND No = ?",
            (board.lot_no, specify_no), caller_name="meisai_service.output")
        if existing is not None:
            confirms.append(Confirm(
                CONFIRM_OVERWRITE, f"No{specify_no}は既に存在します。上書きしますか？"))
        elif specify_no > history_repo.current_seq(conn):
            confirms.append(Confirm(
                CONFIRM_BEYOND_SEQ,
                f"No{specify_no}は現在の連番"
                f"(No{history_repo.current_seq(conn)})を超えています。"
                "作成してよいですか？"))

    if confirms and not confirm:
        result.confirms = confirms
        result.message = "確認してください。"
        return result

    # --- ここから書く ---
    #
    # **ひとまとまりにする。** 文ごとに確定していくと、
    #   ・明細は残ったが副番が登録されず、次に同じコイルを出しても
    #     重複の警告が出ない
    #   ・連番だけ進んで明細が無い
    # といった半端な状態がありうる。まとめれば、全部書けるか
    # 1行も書かないかのどちらかになる。
    #
    # **書けなければ `db.WriteError` が飛ぶ。** 以前は書き込みの
    # 結果を捨てていたので、1行も書けていないのに画面には
    # 「出力しました」と出ていた。
    with db.transaction(conn, name="明細の出力"):
        if specify_no is not None:
            seq_no = specify_no
            # 上書きされる紙の履歴は**消さずに**「作り直し」の印を付ける
            for old in conn.execute(
                    "SELECT 履歴ID FROM 明細出力 WHERE ロット番号 = ? AND No = ?",
                    (board.lot_no, seq_no)).fetchall():
                slip_history.mark_replaced(conn, old["履歴ID"])
            db.write(conn, "DELETE FROM 明細出力 WHERE ロット番号 = ? AND No = ?",
                     (board.lot_no, seq_no), name="明細の出力")
        else:
            seq_no = history_repo.next_seq(conn)

        # **履歴にも同じまとまりで書く。** 明細だけ残って履歴が無い(あるいは
        # その逆)を作らない
        history_id = slip_history.record_output(
            conn, lot_no=board.lot_no, seq_no=seq_no, keys=keys,
            weights=board.weights, lot=lot, orders=orders)
        db.write(
            conn,
            "INSERT INTO 明細出力 (ロット番号, No, 副番の並び, 重量, 出力日時, 履歴ID)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (board.lot_no, seq_no, ",".join(keys),
             ",".join(str(w) for w in board.weights), db.now_db_string(), history_id),
            name="明細の出力")

        # --- 副番を登録 (VBA: すでにあるものは足さない) ---
        for key in keys:
            full = history_repo.fuban_key(board.lot_no, key)
            if not history_repo.is_duplicate(conn, full):
                history_repo.register_fuban(conn, full)

        # --- No指定なら現在連番を引き上げる (VBA `SetCurrentSeq`) ---
        if specify_no is not None and specify_no > history_repo.current_seq(conn):
            history_repo.set_seq(conn, specify_no)

        # --- 印刷済フラグをOFF ---
        history_repo.set_printed(conn, False)

    log.info("output: %s-No%s を出力しました (%s行)", board.lot_no, seq_no, len(keys))
    result.ok = True
    result.seq_no = seq_no
    result.message = f"出力しました。No{seq_no}を確認してください。"
    return result


def reset_stack(board: Board) -> None:
    """出力のあと積み上げだけ空にする (VBA `cmdKettei_Click` の末尾)。

    **出したコイルは使用済みのまま残す。** VBA は
    `ClearGimmickC` → `BuildGimmickC` しか呼ばず、ギミックBの
    `IsUsed` はそのまま ── 「Cスロットのみ。Bは出力後もそのまま残す」
    とコメントしている。**同じコイルを次の梱包へ二重に積ませない**
    ための仕掛けなので、ここを落とすと現物と紙が食い違う。

    スロットから出すだけだと `used` はスロット由来なので消えてしまう。
    復元と同じ「使用済みだがスロットには無い」側へ移す。
    """
    carried = [k for k in board.slots if k]
    board.clear_slots()
    for key in carried:
        if key not in board.restored_used:
            board.restored_used.append(key)


# ==================================================================
# 出力の一覧と読み出し
# ==================================================================
@dataclass
class Output:
    """出力済みの1枚。"""

    lot_no: str
    seq_no: int
    keys: list[str]
    weights: list[int]
    printed_at: str = ""
    # 行の番号(`管理番号`)。**書き足しを返すときの目印。**
    # No指定で上書きすると行が作り直されて番号が変わるので、古い紙面
    # からの書き足しが新しい明細へ紛れ込まない
    row_id: int = 0
    # 印刷の前に紙面で書き足したもの。空なら何も印字しない
    hand_size: str = ""
    hand_lotno: str = ""
    # 明細の履歴(`slip_history`)の送信ID。紙面を開いたことを履歴に映すため
    history_id: str = ""

    @property
    def sheet_name(self) -> str:
        """VBA のシート名と同じ形。`N3250Q0-No1`。"""
        return f"{self.lot_no}-No{self.seq_no}"

    def weight_of(self, key: str) -> int:
        """行の重量 (VBA `WriteData` の `weights(jouPart)`)。"""
        try:
            jou, _ = strand_service.parse_key(key)
        except ValueError:
            return 0
        return self.weights[jou - 1] if 1 <= jou <= len(self.weights) else 0

    def rows(self) -> list[tuple[str, str]]:
        """紙に出る行。`(副番, 重量の表示)`。

        VBA `WriteData` は不正なキー(`-` が無い/先頭が空)を飛ばしつつ
        **行だけは進める**。同じ見え方にするため、読めないキーは
        重量を空にして行を残す。
        """
        out: list[tuple[str, str]] = []
        for key in self.keys:
            try:
                strand_service.parse_key(key)
            except ValueError:
                log.debug("rows: 不正キー key=%s", key)
                out.append((key, ""))
                continue
            out.append((key, f"{self.weight_of(key):.1f}Kg"))
        return out


def _to_output(row: sqlite3.Row) -> Output:
    return Output(
        lot_no=row["ロット番号"],
        seq_no=int(row["No"]),
        keys=[k for k in str(row["副番の並び"] or "").split(",") if k],
        weights=[int(float(w)) for w in str(row["重量"] or "").split(",") if w.strip()],
        printed_at=str(row["出力日時"] or ""),
        row_id=int(row["管理番号"] or 0),
        hand_size=str(row["手入力サイズ"] or ""),
        hand_lotno=str(row["手入力ロット番号"] or ""),
        history_id=str(row["履歴ID"] or "") if "履歴ID" in row.keys() else "",
    )


# ==================================================================
# 印刷の前の書き足し
# ==================================================================
# 紙面の「寸法」「検番」の行へ、刷る前に人が書き足すもの。
#
# **VBA はシートを直してから刷れた。** 紙がExcelシートだったので、
# 気に入らなければセルへ打ち込めた。Web版は紙面がHTMLなので、同じ
# ことを紙面の上でできるようにする(流用元の `printing.editable`)。
HAND_FIELDS = {
    "size": ("手入力サイズ", "サイズ"),
    "lotno": ("手入力ロット番号", "LOTNO"),
}

# 1欄に入れられる文字数。**紙の上で入り切る長さ。**
# 欄の幅は B〜C列(約47mm)で、9pt の太字なら半角20字ほど。それを
# 超えると紙の上で切れる ── 切れて刷られるより、入れる前に断る
HAND_MAX_LEN = 20


class HandEditError(ValueError):
    """書き足しを受け取れない。`status` は画面へ返すHTTPの番号。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def save_hand_edits(conn: sqlite3.Connection, lot_no: str,
                    edits: dict[str, object]) -> int:
    """紙面で書き足した内容を覚える。直した明細の枚数を返す。

    `edits` は紙面の欄の名前と中身。名前は `<管理番号>.<欄>`
    (例 `12.size` / `12.lotno`)。紙面は開くたびに全欄を送ってくる
    ので、**1枚ぶんは丸ごと入れ替える**。

    **1枚でも書けなければ、1枚も書かない。** まとめて開いた紙面で
    一部だけ残ると、刷った紙と記録が食い違う。

    **作り直された明細には書かない。** No指定で上書きすると行が
    作り直されて `管理番号` が変わる。古い紙面を開いたままだと、
    前の明細への書き足しが新しい明細へ紛れ込むので、断る。
    """
    if not isinstance(edits, dict):
        raise HandEditError("書き足した内容が読めませんでした。")

    per_row: dict[int, dict[str, str]] = {}
    for name, raw in edits.items():
        row_text, _, field_name = str(name).partition(".")
        if not row_text.isdigit() or field_name not in HAND_FIELDS:
            raise HandEditError(f"知らない欄です: {name}")
        value = db.sanitize_for_db(str(raw if raw is not None else ""))
        label = HAND_FIELDS[field_name][1]
        if len(value) > HAND_MAX_LEN:
            raise HandEditError(
                f"{label}は{HAND_MAX_LEN}文字までです(いま{len(value)}文字)。"
                "長いと紙の上で切れて刷られます。")
        per_row.setdefault(int(row_text), {})[field_name] = value

    with db.transaction(conn, name="紙面の書き足し"):
        for row_id, fields in per_row.items():
            sets = ", ".join(f'"{HAND_FIELDS[f][0]}" = ?' for f in fields)
            count = db.write(
                conn,
                f"UPDATE 明細出力 SET {sets} WHERE 管理番号 = ? AND ロット番号 = ?",
                (*fields.values(), row_id, lot_no), name="紙面の書き足し")
            if count != 1:
                # 例外で抜ければ、まとまりごと戻る(ほかの面も書かない)
                raise HandEditError(
                    "この明細は作り直されたか、もうありません。"
                    "紙面を開き直してください。", status=409)
            # 履歴にも映す(**紙に出る中身**なので、あとから追えるように)
            row = conn.execute(
                "SELECT 履歴ID, 手入力サイズ, 手入力ロット番号 FROM 明細出力"
                " WHERE 管理番号 = ?", (row_id,)).fetchone()
            if row is not None and row["履歴ID"]:
                slip_history.update_hand(conn, row["履歴ID"], row["手入力サイズ"],
                                         row["手入力ロット番号"])
    log.info("save_hand_edits: %s %s枚", lot_no, len(per_row))
    return len(per_row)


def list_outputs(conn: sqlite3.Connection, lot_no: str) -> list[Output]:
    """そのロットの出力一覧 (VBA `RefreshSheetList`)。No順。"""
    rows = db.fetch_all(conn,
                        "SELECT * FROM 明細出力 WHERE ロット番号 = ? ORDER BY No",
                        (lot_no,), caller_name="meisai_service.list_outputs") or []
    return [_to_output(r) for r in rows]


def find_output(conn: sqlite3.Connection, lot_no: str,
                seq_no: int) -> Optional[Output]:
    """1枚を読み出す。帳票を組み立てるときに使う。"""
    row = db.fetch_one(conn,
                       "SELECT * FROM 明細出力 WHERE ロット番号 = ? AND No = ?",
                       (lot_no, seq_no), caller_name="meisai_service.find_output")
    return _to_output(row) if row else None


def delete_lot_outputs(conn: sqlite3.Connection, lot_no: str) -> int:
    """そのロットの出力を全部消す (VBA `DeleteLotSheets`)。

    VBA はロットを切り替えるときに前ロットのシートを消していた。
    戻り値は消した枚数。
    """
    count = db.write(conn, "DELETE FROM 明細出力 WHERE ロット番号 = ?",
                     (lot_no,), name="前ロットの出力の削除")
    log.info("delete_lot_outputs: %s の出力を%s件消しました", lot_no, count)
    return count
