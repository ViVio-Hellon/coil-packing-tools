"""いま画面に載っている作業 (VBA の UserForm が持っていた状態)

VBA では `frmCoilPacking` のコントロールと `mHandlersB` / `mHandlersC`
が状態を持っていた。Web版は要求ごとに独立しているので、**同じものを
プロセス側に置く**。

【なぜプロセスに1つでよいか】
このアプリは**1台のPCを1人が使う**。VBA も「ブック1つ＝端末1つ」で
閉じていて、副番履歴も連番もその中にしか無かった。同じ持ち方にする。

【メモリだけに置かない】
タブを閉じると `idle_exit` がプロセスを落とすので、ここに置いた
状態は消える。VBA が `SaveCurrentSnapshot` を呼んでいた3つの契機
── **条番号表示・廃棄の増減・出力** ── で必ずDBへ書き、開き直したら
そこから組み立て直す。ここは「いま触っているもの」を速く返すための
置き場であって、記録ではない。
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Optional

from . import config, db, history_repo, lot_repo
from .history_repo import Snapshot
from .logging_utils import get_logger
from .strand_service import Board

log = get_logger("session")

_lock = threading.RLock()
_board: Optional[Board] = None
_lot: Optional[lot_repo.LotInfo] = None
_orders: list[lot_repo.OrderInfo] = []
# 復元した直後か (VBA `mRestoredSnapshot`)。
# 条番号を再表示すると使用済みが消えるので、そのときだけ確認を挟む
_restored = False


def current() -> Optional[Board]:
    """いまの盤面。ロットを開いていなければ None。"""
    return _board


def lot() -> Optional[lot_repo.LotInfo]:
    return _lot


def orders() -> list[lot_repo.OrderInfo]:
    return list(_orders)


def restored() -> bool:
    """前回の途中経過から復元した直後か (VBA `mRestoredSnapshot`)。"""
    return _restored


def clear_restored() -> None:
    global _restored
    _restored = False


def require() -> Board:
    """盤面が無ければ断る。呼び出し側で毎回 None を確かめずに済む。"""
    board = current()
    if board is None:
        raise LookupError("ロット番号が選ばれていません。")
    return board


def clear() -> None:
    """作業を捨てる。"""
    global _board, _lot, _orders, _restored
    with _lock:
        _board = None
        _lot = None
        _orders = []
        _restored = False


# ------------------------------------------------------------------
# ロットを開く (VBA `txtLotNo_Change`)
# ------------------------------------------------------------------
def open_lot(conn: sqlite3.Connection, lot_no: str) -> lot_repo.SearchResult:
    """ロットを開く。VBA `txtLotNo_Change` の後半にあたる。

    VBA の順序をそのまま踏む。

        DB検索 → DisplayLotInfo → RefreshSheetList → RestoreSnapshot

    **前ロットの片付けはここではしない。** VBA は検索の**前**に
    「未印刷の確認 → シート削除 → 履歴クリア → 連番リセット」を
    行っており、利用者への確認を挟む。画面側(`routes`)が尋ねてから
    `close_lot()` を呼ぶ。
    """
    global _board, _lot, _orders, _restored

    result = lot_repo.search(conn, lot_no)
    if not result.found:
        clear()
        return result

    with _lock:
        _lot = result.lot
        _orders = result.orders
        _restored = False

        board = Board(lot_no=result.lot.lot_no,
                      zen_kotei=result.lot.zen_kotei_jisseki_su)

        # 丈数は縦割数を自動セット (RULE-02)。
        # VBA `DisplayLotInfo`: 1〜20なら入れる、21以上は警告のみ、
        # 0以下は何もしない
        tate = result.lot.tate_wari
        if 1 <= tate <= config.JOUSU_MAX:
            board.jou_su = tate
            board.weights = [0] * tate
        _board = board

        # 途中経過があれば復元 (VBA `RestoreSnapshot`)
        _restore(conn, board)

    history_repo.set_current_lot(conn, result.lot.lot_no)
    return result


def _restore(conn: sqlite3.Connection, board: Board) -> bool:
    """前回の途中経過を戻す (VBA `RestoreSnapshot`)。

    VBA が確かめていることを同じ順で確かめる。

      1. 条数の要素数と丈数が合わないなら**何もしない**
         (合わない状態で描くと、あるはずのコイルが出ない)
      2. 前工程実績数はスナップショット値で**上書きする**
         ── 台帳が更新されていても、作業中の数を勝手に変えない
      3. 丈数が範囲外なら諦める
    """
    global _restored

    snap = history_repo.load_snapshot(conn, board.lot_no)
    if snap is None:
        return False
    if len(snap.strands) != snap.jou_su:
        log.info("_restore: 条数データ不整合のためスキップ (%s)", board.lot_no)
        return False
    if not 1 <= snap.jou_su <= config.JOUSU_MAX:
        log.info("_restore: 丈数が範囲外 jou_su=%s", snap.jou_su)
        return False

    board.zen_kotei = snap.zen_kotei
    board.jou_su = snap.jou_su
    board.strands = list(snap.strands)
    board.weights = list(snap.weights) + [0] * (snap.jou_su - len(snap.weights))
    board.discarded = [k for k in snap.discarded if board.knows(k)]

    # 使用済みをスロットへ戻す。VBA は使用済みフラグだけを復元し、
    # 積み上げスロットは空のまま出していた(積み条数は入力し直す)。
    # ここも同じ ── スロット数が決まっていないので置き場が無い
    board.stack_max = 0
    board.slots = []
    board.restored_used = [k for k in snap.used if board.knows(k)]

    _restored = True
    log.info("_restore: 復元 %s 丈数=%s 合計=%s条 使用済=%s 廃棄=%s",
             board.lot_no, board.jou_su, sum(board.strands),
             len(board.restored_used), len(board.discarded))
    return True


def close_lot(conn: sqlite3.Connection, lot_no: str) -> None:
    """前ロットを片付ける (VBA `txtLotNo_Change` の前半)。

        DeleteLotSheets → ClearFubanList → ResetSeq → DeleteSnapshot

    **順序をそのまま守る。** 連番のリセットは副番履歴クリアでは
    起きないので、ここで明示的に呼ぶ(VBA のコメントが同じことを
    言っている)。
    """
    from . import meisai_service

    # **4つをひとまとまりに。** 途中で書けなくなると、出力だけ消えて
    # 副番と連番が残る(次のロットに前のロットの番号が付く)
    with db.transaction(conn, name="前ロットの片付け"):
        meisai_service.delete_lot_outputs(conn, lot_no)
        history_repo.clear_fuban(conn)
        history_repo.reset_seq(conn)
        history_repo.delete_snapshot(conn, lot_no)
    log.info("close_lot: %s を片付けました", lot_no)


# ------------------------------------------------------------------
# 保存 (VBA `SaveCurrentSnapshot`)
# ------------------------------------------------------------------
def save(conn: sqlite3.Connection) -> None:
    """いまの作業をDBへ書く。

    **VBA が呼んでいた3つの契機で必ず呼ぶ。**
      - 条番号表示 (`cmdShowJouTai_Click`)
      - 廃棄の増減 (`GimmickB_Click` の廃棄モード)
      - 出力       (`cmdKettei_Click`)

    タブを閉じるとプロセスが終わるので、ここを飛ばすと作業が消える。
    """
    board = current()
    if board is None or not board.strands:
        return
    history_repo.save_snapshot(conn, Snapshot(
        lot_no=board.lot_no,
        zen_kotei=board.zen_kotei,
        jou_su=board.jou_su,
        weights=list(board.weights),
        strands=list(board.strands),
        used=sorted(board.used, key=_key_order),
        discarded=list(board.discarded),
    ))


def _key_order(key: str) -> tuple[int, int]:
    """副番キーの並び順。丈→条の昇順。保存の中身を読みやすくする。"""
    from .strand_service import parse_key
    try:
        return parse_key(key)
    except ValueError:
        return (0, 0)
