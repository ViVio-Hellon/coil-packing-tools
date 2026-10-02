"""発注票 ── チェックリストから倉庫へ出す票を作る

【重複は止めずに知らせる】
同じロットが2週間以内に出ていたら確認を出す。続けるかどうかは人が決める
(VBA も「このまま重複して発注しますか？」と聞いていた)。
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from modules.packing_material_calculation.coil_tool import order_history, order_sheet_service, reports, user_settings
from modules.packing_material_calculation.coil_tool.logging_utils import get_logger

from .. import base, conf, get_db, shell

log = get_logger("app.routes.order")

bp = Blueprint("order", __name__)

# 組み立てた発注票を、確認のあいだだけ持っておく。
# **プロセスに1つ**(このアプリは1台を1人が使う)
_pending: dict = {"sheets": [], "worker": ""}


def _sheet_view(sheet) -> dict:
    return {
        "種類": sheet.種類, "角サイズ": sheet.角サイズ,
        "提出日付": sheet.提出日付, "依頼者": sheet.依頼者,
        "rows": [{"長さ": r.長さ, "LotNo": r.LotNo, "数量": r.数量}
                 for r in sheet.rows],
    }


@bp.get("/order")
def page():
    conn = get_db()
    return render_template(
        "order.html",
        page=shell.page("order"),
        pages=shell.PAGES,
        ribbon=shell.ribbon(conn),
        token=conf()["TOKEN"],
    )


@bp.get("/api/order")
def current():
    return jsonify(ok=True, sheets=[_sheet_view(s) for s in _pending["sheets"]],
                   worker=_pending["worker"])


_MESSAGES = {
    order_sheet_service.REASON_EMPTY:
        "チェックリストに、発注票にできる行がありません"
        "(コイル間がリプラ / LVS の行だけが対象です)",
    order_sheet_service.REASON_TOO_MANY_SIZES:
        "発注リストにないサイズが多すぎます(書き足せるのは4件までです)",
}


@bp.post("/api/order/build")
def build():
    """チェックリストから発注票を組む。**履歴の重複は知らせるだけ。**"""
    conn = get_db()
    worker = user_settings.get_worker()
    if not worker:
        return jsonify(ok=False, reason="no_worker",
                       message="担当者を選んでください(帯の担当者欄)"), 422

    payload = request.get_json(silent=True) or {}
    check = not bool(payload.get("確認済み"))

    result = order_sheet_service.build(conn, worker=worker, check_history=check)
    if not result.ok:
        return jsonify(ok=False, reason=result.reason,
                       message=_MESSAGES.get(result.reason, "発注票を作れません"),
                       extra_lengths=result.extra_lengths), 422

    _pending["sheets"] = result.sheets
    _pending["worker"] = worker
    return jsonify(
        ok=True,
        sheets=[_sheet_view(s) for s in result.sheets],
        extra_lengths=result.extra_lengths,
        duplicates=[{
            "LotNo": d.LotNo, "処理日": d.処理日, "担当者": d.担当者,
            "サイズ": d.サイズ, "数量": d.数量, "日数表示": d.日数表示,
            "message": d.message(),
        } for d in result.duplicates],
    )


@bp.post("/api/order/commit")
def commit():
    """出した内容を履歴へ残す。これを押すまでは履歴に入らない。"""
    conn = get_db()
    if not _pending["sheets"]:
        return jsonify(ok=False, reason="nothing_pending",
                       message="先に発注票を作ってください"), 422
    n = order_sheet_service.commit(conn, _pending["sheets"],
                                   worker=_pending["worker"])
    return jsonify(ok=True, saved=n)


@bp.get("/api/order/history")
def history():
    return jsonify(ok=True, rows=order_history.recent(get_db()))


@bp.get("/report/order")
def report():
    """印刷用の紙面。**1枚 = 1ページ。**"""
    from modules.packing_material_calculation.coil_tool import printing

    if not _pending["sheets"]:
        return "<p>発注票がまだ作られていません。</p>", 404
    rep = reports.build_order_sheet_report(_pending["sheets"])
    # プレビューの上に「印刷する」(0.2.9。現場の指摘)
    return printing.render_html(rep, print_button=True)
