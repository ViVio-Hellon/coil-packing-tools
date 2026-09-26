"""梱包明細の画面とAPI (VBA `frmCoilPacking` の移植)

VBA のボタン・イベントと1対1で対応させてある。

    VBA                           -> URL
    ---------------------------------------------------------------
    txtLotNo_Change (7桁)           POST /api/lot
    cmdToggleEditA_Click             POST /api/zen-kotei
    txtJouSu_Change                   POST /api/jou-su
    重量入力                            POST /api/weights
    cmdShowJouTai_Click                 POST /api/strands
    txtTsumiJouSu_Change                 POST /api/stack-max
    GimmickB_Click                        POST /api/stack
    GimmickC_Click                         POST /api/unstack
    cmdHaiki_Click + GimmickB_Click         POST /api/discard
    cmdReverse_Click                         POST /api/reverse
    cmdKettei_Click                           POST /api/output
    cmdPrint_Click                             GET  /report/<lot>/<no>
    cmdClearDup_Click                           POST /api/clear-history
    lstSheetList / RefreshSheetList               (画面の状態に含める)

【画面の状態は毎回まるごと返す】
操作のたびに「いま画面がどう見えるか」を全部返す。差分を返して画面側で
組み立て直すと、**サーバと画面で状態が二重になる** ── どちらが正しいかを
決める規則が要り、ずれたときに直す先が分からなくなる。
"""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import quote

from flask import (Blueprint, Response, current_app, jsonify, render_template,
                   request)

from modules.packing_details.meisai import (config, data_sync, history_repo, idle_exit, meisai_service,
                    qa_mark, report, screen, session, slip_history, strand_service)
from modules.packing_details.meisai.logging_utils import get_logger
from modules.packing_details.meisai.strand_service import RefusedError

from .. import base, conf, error_body, get_db

log = get_logger("app.routes.meisai")

bp = Blueprint("meisai", __name__)


# ==================================================================
# 画面
# ==================================================================
@bp.get("/meisai")
def page():
    """梱包明細の画面。1画面で完結する。

    **2枚目かどうかは、ここでは決められない。**
    ブラウザは再読込(F5)のとき、**古いページを畳む前に新しい要求を
    送る**。サーバから見ると「もう1枚開いた」と見分けがつかず、
    ここで断ると F5 のたびに断ることになる(実際そうなった)。

    見分けられるのはブラウザ側だけなので、名乗りは画面が開いたあとに
    させる(`/api/screen/claim`)。器を返すのは誰にでもしてよい ──
    **業務データは名乗ってからでないと触れない。**
    """
    conn = get_db()
    return render_template(
        "meisai.html",
        display_name=conf()["DISPLAY_NAME"],
        version=conf()["VERSION"],
        token=conf()["TOKEN"],
        # **出どころは `idle_exit` の1か所。** ここに数字を書き写すと、
        # 自動終了の待ち時間だけ変えたときに食い違う
        alive_poll_ms=idle_exit.HEARTBEAT_MS,
        # 前に戻ったとき「裏で開き直されていないか」を確かめる目印
        server_pid=os.getpid(),
        health_poll_ms=15_000,
        jousu_max=config.JOUSU_MAX,
        stack_limit=config.STACK_LIMIT,
        sheet_rows=config.SHEET_ROWS,
        lot_no_length=config.LOT_NO_LENGTH,
        hoso_url=config.URL_HOSO_SHIYOSHO,
        has_data=data_sync.has_lot_data(conn),
        # 上の帯に出すのは「いつ時点のものか」だけ。**取り込み元を見に行かない**
        # (届かない・遅い共有のぶんだけ画面が出るのが遅れるため)
        stamps=data_sync.stamps(conn, check=False),
        state=_state(conn),
    )


# ==================================================================
# 画面の持ち主
# ==================================================================
# **2枚にするのではなく、移す。** 断るだけだと、タブが落ちたときに
# 誰も入れなくなる(猶予が切れるまで待つことになる)。
@bp.post("/api/screen/claim")
def claim_screen():
    """この画面が使うと名乗る。**取れなければ 409。**

    取れないのは、別のタブがすでに開いているとき。画面側はここで
    断られたら業務の画面を出さない。
    """
    body = request.get_json(silent=True) or {}
    screen_id = str(body.get("screen", ""))
    if not screen_id:
        return jsonify(error_body("bad_screen", "画面が指定されていません")), 400
    if not screen.claim(screen_id):
        log.warning("2枚目の画面を断った: %s", screen_id)
        held = screen.holder()
        return jsonify({
            **error_body("screen_busy", "この画面はすでに開いています"),
            "idle_sec": round(held.idle_sec) if held else 0,
            "grace_sec": round(screen.GRACE_SEC),
        }), 409
    return jsonify({"ok": True})


@bp.post("/api/screen/take-over")
def take_over():
    """前の画面に手放させる。**空けるだけ** ── 次に開いた画面が取る。

    ここで新しい持ち主を決めてしまうと、押した直後に開き直したとき
    **自分自身に断られる**(開き直しは新しい名前で来るため)。
    """
    screen.hand_over()
    return jsonify({"ok": True})


@bp.post("/api/screen/release")
def release_screen():
    """タブを閉じた。**すぐ空ける**ので、開き直すのに猶予を待たない。"""
    body = request.get_json(silent=True) or {}
    screen.release(str(body.get("screen", "")))
    return jsonify({"ok": True})


# ==================================================================
# 画面の状態
# ==================================================================
def _state(conn) -> dict:
    """いま画面がどう見えるか。**操作のたびにこれを丸ごと返す。**"""
    board = session.current()
    lot = session.lot()

    state: dict = {
        "lot_no": board.lot_no if board else "",
        "found": lot is not None and lot.found,
        "restored": session.restored(),
        "current_seq": history_repo.current_seq(conn),
        "printed": history_repo.is_printed(conn),
        "fuban_count": history_repo.fuban_count(conn),
        "lot": None,
        "orders": [],
        "zen_kotei": board.zen_kotei if board else 0,
        "jou_su": board.jou_su if board else 0,
        "weights": list(board.weights) if board else [],
        "strands": list(board.strands) if board else [],
        "stack_max": board.stack_max if board else 0,
        "slots": list(board.slots) if board else [],
        "reversed": board.reversed_view if board else False,
        "complete": board.is_complete if board else False,
        "rows": [],
        "outputs": [],
    }

    if lot is not None and lot.found:
        state["lot"] = {
            "yoto_code": lot.yoto_code,
            "yoto_name": lot.yoto_name,
            "zaishitsu": lot.zaishitsu,
            "choshitsu": lot.choshitsu,
            "odr_thickness": lot.odr_thickness_text,
            "odr_width": lot.odr_width_text,
            "seizou_thickness": lot.seizou_thickness_text,
            "seizou_width": lot.seizou_width_text,
            "sekkei_course": lot.sekkei_course,
            "jisseki_course": lot.jisseki_course,
            "tate_wari": lot.tate_wari,
            "yoko_wari": lot.yoko_wari,
            # 縦割数が上限を超えると丈数が自動で入らない。
            # **理由を画面に出す** ── 入らないだけだと壊れて見える
            "jou_su_note": _jou_su_note(lot),
        }
        state["orders"] = [{"order_no": o.order_no, "spec_no": o.spec_no,
                            "customer": o.customer, "deliver_to": o.deliver_to}
                           for o in session.orders()]

    if board is not None and board.strands:
        state["rows"] = [
            [{"key": key, "state": board.state_of(key),
              "weight": board.weight_of(key)} for key in row]
            for row in board.rows()
        ]

    if board is not None and board.lot_no:
        state["outputs"] = [
            {"no": o.seq_no, "name": o.sheet_name, "count": len(o.keys),
             "at": o.printed_at}
            for o in meisai_service.list_outputs(conn, board.lot_no)]

    return state


def _jou_su_note(lot) -> str:
    """丈数が自動で入らなかった理由 (VBA の MsgBox にあたる)。"""
    if lot.tate_wari > config.JOUSU_MAX:
        return (f"BOX設計_縦割数({lot.tate_wari})が上限({config.JOUSU_MAX})を"
                "超えています。丈数は手で入力してください。")
    if lot.tate_wari < 1:
        return ("このロットは梱包課共有の仕掛(LS4LOT)に無いため、"
                "前工程実績数が0です。「修正する」で入力してください。")
    return ""


def _ok(conn, **extra) -> Response:
    body = {"ok": True, "state": _state(conn)}
    body.update(extra)
    return jsonify(body)


def _refused(exc: RefusedError, status: int = 400) -> Response:
    return jsonify(error_body(exc.reason, exc.message)), status


# ==================================================================
# ロット (VBA `txtLotNo_Change`)
# ==================================================================
@bp.post("/api/lot")
def open_lot():
    """ロット番号で検索する。

    VBA は7桁に達した時点で自動検索し、**前のロットと違えば**
    片付けの確認を挟んでいた。同じ判断をここでする ── ただし
    確認は1往復で返せないので、`confirm` を受け取る形にする。
    """
    conn = get_db()
    body = request.get_json(silent=True) or {}
    lot_no = str(body.get("lot_no", "")).strip().upper()
    confirm = bool(body.get("confirm"))

    if len(lot_no) != config.LOT_NO_LENGTH:
        return jsonify(error_body(
            "bad_lot_no",
            f"ロット番号は{config.LOT_NO_LENGTH}桁です。", "lot_no")), 400

    previous = history_repo.current_lot(conn)
    if previous and previous != lot_no:
        # VBA: 未印刷のまま切り替えると出力が消える。先に尋ねる
        if not history_repo.is_printed(conn) and not confirm:
            return jsonify({
                "ok": False,
                "confirm": "unprinted",
                "message": (f"前回の出力({previous})がまだ印刷されていません。\n"
                            "シートを削除してよいですか？"),
            })
        session.close_lot(conn, previous)

    result = session.open_lot(conn, lot_no)
    if not result.found:
        return jsonify(error_body("not_found", result.message, "lot_no")), 404

    message = ""
    if session.restored():
        board = session.require()
        message = (f"前回の条番号情報を復元しました。"
                   f"（丈数:{board.jou_su} 合計:{sum(board.strands)}条）")
    return _ok(conn, message=message)


@bp.post("/api/clear-lot")
def clear_lot():
    """ロットを閉じる(画面を空に戻す)。"""
    session.clear()
    return _ok(get_db())


# ==================================================================
# 前工程実績数 (VBA ギミックA `cmdToggleEditA_Click`)
# ==================================================================
@bp.post("/api/zen-kotei")
def set_zen_kotei():
    """前工程実績数を手で直す。

    VBA は「修正する → 確定する」のトグルで、確定時に数値かどうかを
    確かめていた。画面側が編集モードを持ち、確定でここへ送る。
    """
    conn = get_db()
    board = session.current()
    if board is None:
        return jsonify(error_body("no_lot", "ロット番号を入力してください。")), 409

    raw = str((request.get_json(silent=True) or {}).get("value", "")).strip()
    if not raw.isdigit():
        return jsonify(error_body(
            "bad_number", "前工程実績数は数値で入力してください。", "value")), 400
    board.zen_kotei = int(raw)
    log.info("set_zen_kotei: %s -> %s", board.lot_no, board.zen_kotei)
    return _ok(conn)


# ==================================================================
# 丈数と重量
# ==================================================================
@bp.post("/api/jou-su")
def set_jou_su():
    """丈数を決める (VBA `txtJouSu_Change`)。

    VBA は丈数が変わると**重量欄・条番号・積み上げ・廃棄をすべて
    作り直す**。同じことをする。
    """
    conn = get_db()
    board = session.current()
    if board is None:
        return jsonify(error_body("no_lot", "ロット番号を入力してください。")), 409

    raw = str((request.get_json(silent=True) or {}).get("value", "")).strip()
    if not raw.isdigit():
        return jsonify(error_body("bad_number", "丈数を入力してください。",
                                  "value")), 400
    value = int(raw)
    if not 1 <= value <= config.JOUSU_MAX:
        return jsonify(error_body(
            "out_of_range",
            f"丈数は1〜{config.JOUSU_MAX}です。", "value")), 400

    board.jou_su = value
    board.weights = [0] * value
    board.strands = []
    board.restored_used = []
    board.clear_slots()
    board.discarded = []            # VBA `ClearHaiki`
    session.clear_restored()
    return _ok(conn)


@bp.post("/api/weights")
def set_weights():
    """丈ごとの重量を入れる。

    VBA は出力の直前にも読み直していた(`cmdKettei_Click` の
    `ReadWeights`)。ここで持っておけば、出力時に読み直したのと
    同じことになる。
    """
    conn = get_db()
    board = session.current()
    if board is None:
        return jsonify(error_body("no_lot", "ロット番号を入力してください。")), 409

    values = (request.get_json(silent=True) or {}).get("weights", [])
    try:
        board.weights = meisai_service.read_weights(values, board.jou_su)
    except RefusedError as exc:
        return _refused(exc)
    return _ok(conn)


# ==================================================================
# 条番号 (VBA `cmdShowJouTai_Click`)
# ==================================================================
@bp.post("/api/strands")
def build_strands():
    """条番号を作る。**VBA が確かめていることを同じ順で確かめる。**

        1. 復元直後なら上書きしてよいか尋ねる
        2. 丈数が入っているか
        3. 前工程実績数が数値で、0より大きいか
        4. 重量がすべて入っているか
    """
    conn = get_db()
    board = session.current()
    if board is None:
        return jsonify(error_body("no_lot", "ロット番号を入力してください。")), 409

    body = request.get_json(silent=True) or {}
    if session.restored() and not body.get("confirm"):
        return jsonify({
            "ok": False,
            "confirm": "overwrite_restored",
            "message": ("前回の状態が復元されています。\n"
                        "条番号を再表示すると使用済み情報がリセットされます。"
                        "(廃棄は維持)\n続行しますか？"),
        })

    if board.jou_su < 1:
        return jsonify(error_body("no_jou_su", "丈数を入力してください。")), 400
    if board.zen_kotei <= 0:
        return jsonify(error_body(
            "bad_zen_kotei",
            "前工程実績数が0以下です。正しい値を入力してください。")), 400
    if any(w <= 0 for w in board.weights[:board.jou_su]):
        missing = next(i + 1 for i, w in enumerate(board.weights[:board.jou_su])
                       if w <= 0)
        return jsonify(error_body(
            "no_weight", f"丈{missing}の重量が未入力です。")), 400

    board.build_strands()
    session.clear_restored()
    session.save(conn)                  # VBA `SaveCurrentSnapshot`
    return _ok(conn)


@bp.post("/api/reverse")
def reverse():
    """表示の向きを入れ替える (VBA `cmdReverse_Click`)。"""
    conn = get_db()
    board = session.current()
    if board is None or not board.strands:
        return jsonify(error_body("no_strands", "条番号が表示されていません。")), 409
    board.reverse()
    return _ok(conn)


# ==================================================================
# 積み上げ (VBA ギミックC)
# ==================================================================
@bp.post("/api/stack-max")
def set_stack_max():
    """積み条数を決める (VBA `txtTsumiJouSu_Change`)。"""
    conn = get_db()
    board = session.current()
    if board is None:
        return jsonify(error_body("no_lot", "ロット番号を入力してください。")), 409

    raw = str((request.get_json(silent=True) or {}).get("value", "")).strip()
    if not raw.isdigit():
        return jsonify(error_body("bad_number", "積み条数を入力してください。",
                                  "value")), 400
    try:
        board.set_stack_max(int(raw))
    except ValueError as exc:
        return jsonify(error_body("out_of_range", str(exc), "value")), 400
    return _ok(conn)


@bp.post("/api/stack")
def stack():
    """条番号を積む (VBA `GimmickB_Click` の通常モード)。"""
    conn = get_db()
    board = session.current()
    if board is None:
        return jsonify(error_body("no_lot", "ロット番号を入力してください。")), 409

    key = str((request.get_json(silent=True) or {}).get("key", "")).strip()
    try:
        slot_no = board.stack(key)
    except RefusedError as exc:
        # 使用済み・廃棄済みのクリックは VBA では**黙って無視**される。
        # 断りの理由は返すが、画面は警告を出さずに状態だけ描き直す
        if exc.reason in (strand_service.REFUSE_USED,
                          strand_service.REFUSE_DISCARDED):
            return _ok(conn, ignored=exc.reason)
        return _refused(exc)
    return _ok(conn, slot_no=slot_no)


@bp.post("/api/unstack")
def unstack():
    """スロットを解除する (VBA `GimmickC_Click`)。"""
    conn = get_db()
    board = session.current()
    if board is None:
        return jsonify(error_body("no_lot", "ロット番号を入力してください。")), 409

    raw = (request.get_json(silent=True) or {}).get("slot_no")
    try:
        key = board.unstack(int(raw))
    except (TypeError, ValueError):
        return jsonify(error_body("bad_slot", "スロット番号が不正です。")), 400
    except RefusedError as exc:
        # 空スロットのクリックは VBA では何も起きない
        return _ok(conn, ignored=exc.reason)
    return _ok(conn, released=key)


@bp.post("/api/discard")
def discard():
    """廃棄を付け外しする (VBA 廃棄モードの `GimmickB_Click`)。"""
    conn = get_db()
    board = session.current()
    if board is None:
        return jsonify(error_body("no_lot", "ロット番号を入力してください。")), 409

    key = str((request.get_json(silent=True) or {}).get("key", "")).strip()
    try:
        now_discarded = board.toggle_discard(key)
    except RefusedError as exc:
        # 使用済みは廃棄にできない。VBA は黙って無視する
        if exc.reason == strand_service.REFUSE_USED:
            return _ok(conn, ignored=exc.reason)
        return _refused(exc)
    session.save(conn)                  # VBA `SaveCurrentSnapshot`
    return _ok(conn, discarded=now_discarded)


# ==================================================================
# 出力 (VBA `cmdKettei_Click`)
# ==================================================================
@bp.post("/api/output")
def output():
    """明細を1枚出す。"""
    conn = get_db()
    board = session.current()
    if board is None:
        return jsonify(error_body("no_lot", "ロット番号を入力してください。")), 409

    body = request.get_json(silent=True) or {}
    confirm = bool(body.get("confirm"))
    specify = body.get("specify_no")
    specify_no = None
    if specify not in (None, ""):
        try:
            specify_no = int(specify)
        except (TypeError, ValueError):
            return jsonify(error_body(
                "bad_no", "出力するNoを入力してください。", "specify_no")), 400

    result = meisai_service.output(conn, board, specify_no=specify_no,
                                   confirm=confirm, lot=session.lot(),
                                   orders=session.orders())
    if result.needs_confirm:
        return jsonify({
            "ok": False,
            "confirm": "output",
            "confirms": [{"kind": c.kind, "message": c.message,
                          "detail": c.detail} for c in result.confirms],
        })
    if not result.ok:
        return jsonify(error_body(result.refuse, result.message)), 400

    # VBA: 保存 → 積み上げだけリセット → 一覧を作り直す
    session.save(conn)
    meisai_service.reset_stack(board)
    slip_history.kick()                 # 履歴を共有へ(裏で)
    return _ok(conn, seq_no=result.seq_no, message=result.message)


@bp.post("/api/printed")
def mark_printed():
    """印刷したことを覚える (VBA `cmdPrint_Click` の `SetPrinted True`)。

    ブラウザの印刷は押されたかどうかを確実には拾えないので、
    **帳票を開いた時点で印刷済みとみなす**のではなく、画面の
    「印刷」ボタンがここを叩いてから帳票を開く。
    """
    conn = get_db()
    history_repo.set_printed(conn, True)
    return _ok(conn)


@bp.get("/report/qa-mark")
def current_qa_mark():
    """いま刷る右上の文字。**開いたままの紙面が、前に戻ったときに聞き直す。**

    設定で変えたあとに古い紙面で刷ると、古い文字のまま出る。**ほかの
    ラインで変えたとき**は画面からの知らせ(`BroadcastChannel`)が届かない
    ので、ここで共有を読み直して追いつく。共有に届かないときの断り書きも返す。
    業務データは含まないが、紙面と同じくトークンは要る。
    """
    value, snapshot = qa_mark.resolve()
    return jsonify({"qa_mark": value, "notice": qa_mark.notice(snapshot)})


@bp.get("/report/<lot_no>/<int:seq_no>")
def sheet(lot_no: str, seq_no: int):
    """明細1枚の帳票。ブラウザの印刷(Ctrl+P)でそのまま出せる。

    **A4横の左半分に1枚、右半分は空ける**(現場で切り取る)。
    まとめて刷るときは `/report/<lot>?nos=1,2,3`(1枚ずつ別の用紙)。
    """
    conn = get_db()
    found = meisai_service.find_output(conn, lot_no.upper(), seq_no)
    if found is None:
        return jsonify(error_body("not_found", "その出力はありません。")), 404
    return _render_report(conn, [found], lot_no)


@bp.get("/report/<lot_no>")
def sheets(lot_no: str):
    """複数枚まとめての帳票。`?nos=1,2,3` を No の順に並べる。

    **1枚ずつ別の用紙**に出る。A4 1枚に2枚並べる運用はしていない
    (現場に確認済み)。どの用紙も左半分に明細、右半分は空ける。
    """
    conn = get_db()
    raw = request.args.get("nos", "")
    seq_nos: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            return jsonify(error_body("bad_no", f"Noが不正です: {part}")), 400
        if value not in seq_nos:
            seq_nos.append(value)
    if not seq_nos:
        return jsonify(error_body("bad_no", "印刷するNoを指定してください。")), 400

    # **並びは No 順に固定する。** 押した順で刷ると、同じ組み合わせでも
    # 紙のどちら側に来るかが変わり、切ったあとの並べ替えが要る
    outputs = []
    for seq_no in sorted(seq_nos):
        found = meisai_service.find_output(conn, lot_no.upper(), seq_no)
        if found is None:
            return jsonify(error_body(
                "not_found", f"No{seq_no}の出力はありません。")), 404
        outputs.append(found)
    return _render_report(conn, outputs, lot_no)


def _render_report(conn, outputs, lot_no: str) -> Response:
    """紙面を組む。**開いたことを明細の履歴に残す**(刷るために開くので、
    何回開いたか・そのとき刷られる右上の文字は、あとから追う手がかり)。"""
    resolved = qa_mark.resolve()
    html = report.render(outputs, edit_url=_edit_url(lot_no), resolved=resolved)
    ids = [o.history_id for o in outputs if o.history_id]
    if ids:
        slip_history.note_opened(conn, ids, resolved[0])
        slip_history.kick()
    return Response(html, mimetype="text/html")


def _edit_url(lot_no: str) -> str:
    """紙面で書き足した内容の送り先。

    **紙面は別のタブの独立したページ**で、アプリ本体のJSは動いていない。
    `printing.render_html` が埋め込む小さなスクリプトが、この窓が開いた
    ときのトークン(`?t=`)を付けて送る ── 流用元と同じ形。
    """
    token = request.args.get("t", "")
    url = f"{base()}/report/{quote(lot_no.upper())}/edits"
    return url + (f"?t={quote(token)}" if token else "")


@bp.post("/report/<lot_no>/edits")
def save_edits(lot_no: str):
    """印刷の前に紙面で書き足したサイズ・LOTNOを覚える。

    **画面の持ち主でなくても受ける**(`/report/*` は画面の見張りの外)。
    紙面は別のタブで開くので画面を持っていないし、ここで触るのは
    出力済みの明細の書き足し欄だけで、作業中の盤面には触らない。
    トークンは要る。
    """
    conn = get_db()
    body = request.get_json(silent=True) or {}
    try:
        count = meisai_service.save_hand_edits(conn, lot_no.upper(),
                                               body.get("edits"))
    except meisai_service.HandEditError as exc:
        return jsonify(error_body("bad_edit", str(exc))), exc.status
    slip_history.kick()
    return jsonify({"ok": True, "saved": count})


# ==================================================================
# 副番履歴 (VBA `cmdClearDup_Click`)
# ==================================================================
@bp.post("/api/clear-history")
def clear_history():
    """副番履歴とスナップショットを消す。

    VBA は加えて**画面の使用済みと積み上げをほどく**(キャンバスは
    残す)。同じことをする ── 連番は戻さない。
    """
    conn = get_db()
    history_repo.clear_fuban(conn)
    history_repo.clear_snapshots(conn)

    board = session.current()
    if board is not None:
        board.release_all_used()
    return _ok(conn, message="副番履歴をクリアしました。")


# ==================================================================
# 取り込み
# ==================================================================
# 取り込みの進み具合。**取り込みは1本ずつ**(書く要求の順番待ちに入る)なので、
# プロセスに1つあれば足りる。画面は取り込みを頼んだあと、ここを覗いて
# 「いま何を読んでいるか・何秒たったか」を出す(黙って待たせない)
_IMPORT_PARTS = {
    "lot": tuple(config.LOT_DB_FILES),          # 仕掛台帳のフォルダの3つ
    "konpo": tuple(config.KONPO_DB_FILES),      # 梱包課共有の仕掛フォルダの1つ
}
_progress_lock = threading.Lock()
_progress: dict = {"running": False, "pct": 0, "message": "", "started": 0.0,
                   "finished": 0.0}


def _set_progress(**values) -> None:
    with _progress_lock:
        _progress.update(values)


@bp.get("/api/import/progress")
def import_progress():
    """取り込みの進み具合(読むだけ・待たない)。"""
    with _progress_lock:
        now = time.monotonic()
        state = dict(_progress)
    end = state["finished"] if not state["running"] and state["finished"] else now
    state["elapsed"] = round(end - state["started"], 1) if state["started"] else 0
    del state["started"], state["finished"]
    return jsonify(state)


@bp.post("/api/import")
def run_import():
    """取り込み元から読み直す。

    `{"only": "lot"}` なら仕掛台帳のフォルダの3つだけ、`"konpo"` なら
    梱包課共有の LS4LOT だけ(設定画面で欄ごとに「保存して取り込み」)。
    """
    conn = get_db()
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    part = str(body.get("only") or "")
    if part and part not in _IMPORT_PARTS:
        return jsonify(error_body("bad_part", "どの取り込み元かが分かりません。", "only")), 400
    _set_progress(running=True, pct=0, message="取り込み元を探しています...",
                  started=time.monotonic(), finished=0.0)
    try:
        result = data_sync.import_all(
            conn, force=force, only=_IMPORT_PARTS.get(part),
            progress=lambda pct, message, ok=True: _set_progress(pct=pct, message=message))
    finally:
        _set_progress(running=False, pct=100, finished=time.monotonic())

    # **開いているロットは古いままだと断る。**
    #
    # 取り込みは台帳を総入れ替えするが、画面に載っているロット情報は
    # 開いたときに読んだもので、勝手には変わらない(実測: 取り込み後も
    # 前の用途名が出たままだった)。
    #
    # かといって黙って読み直すのも駄目で、**「修正する」で手入力した
    # 前工程実績数が消える**(LS4LOT に無いロットではこれが常用手段)。
    # どちらを選ぶかは利用者にしか決められないので、事実だけ伝える。
    note = ""
    board = session.current()
    if result.ok and board is not None and board.lot_no:
        note = (f"ロット {board.lot_no} は取り込み前の内容のままです。"
                "新しい台帳で引き直すには、ロット番号を入れ直してください"
                "（手で直した前工程実績数は入れ直しになります）。")

    return jsonify({
        "ok": result.ok,
        "summary": result.summary(),
        "imported": result.imported,
        "errors": result.errors,
        "note": note,
        "stamps": [s.to_dict() for s in data_sync.stamps(conn)],
    })
