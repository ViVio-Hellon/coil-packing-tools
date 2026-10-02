"""チェックリスト ── 計算結果を15行に積む (VBA `資材発注管理` シート)"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from modules.packing_material_calculation.coil_tool import checklist_service, config, reports, user_settings
from modules.packing_material_calculation.coil_tool.logging_utils import get_logger

from .. import base, conf, get_db, session, shell

log = get_logger("app.routes.checklist")

bp = Blueprint("checklist", __name__)


def _view(conn) -> dict:
    rows = checklist_service.load_all(conn)
    used = sorted({int(r["行番号"]) for r in rows})
    return {
        "rows": rows,
        "used": used,
        "next_free": checklist_service.next_free_row(conn),
        "max_rows": config.CHECKLIST_ROWS,
        "line": user_settings.get_line(),
        "title": reports.checklist_title(user_settings.get_line()),
    }


@bp.get("/checklist")
def page():
    conn = get_db()
    return render_template(
        "checklist.html",
        page=shell.page("checklist"),
        pages=shell.PAGES,
        ribbon=shell.ribbon(conn),
        token=conf()["TOKEN"],
    )


@bp.get("/api/checklist")
def current():
    return jsonify(ok=True, view=_view(get_db()))


@bp.post("/api/checklist/add")
def add():
    """いまの計算結果を1行として積む (VBA `CommandButton6_Click`)。

    **担当者が決まっていないと書かない。** 依頼者欄は誰が出したかの
    記録で、空のまま倉庫へ回ると問い合わせ先が分からなくなる。
    """
    payload = request.get_json(silent=True) or {}
    conn = get_db()
    state = session.get()

    if not str(state.台数).strip():
        return jsonify(ok=False, reason="not_calculated",
                       message="先に計算してください"), 422

    worker = user_settings.get_worker()
    if not worker:
        return jsonify(ok=False, reason="no_worker",
                       message="担当者を選んでください(帯の担当者欄)"), 422

    row_no = payload.get("行番号")
    if row_no is None:
        row_no = checklist_service.next_free_row(conn)
        if row_no is None:
            return jsonify(ok=False, reason="checklist_full",
                           message=f"チェックリストが{config.CHECKLIST_ROWS}行"
                                   "埋まっています。発注票を作るか、行を消してください"), 422
    try:
        row_no = int(row_no)
    except (TypeError, ValueError):
        return jsonify(ok=False, reason="bad_row_no",
                       message="行番号が数字ではありません"), 400

    rows = checklist_service.build_rows(
        state, worker=worker, size_fixed=bool(payload.get("サイズ確定")))
    try:
        checklist_service.save_rows(conn, row_no, rows)
    except ValueError as exc:
        return jsonify(ok=False, reason="row_out_of_range", message=str(exc)), 422

    return jsonify(ok=True, view=_view(conn), 行番号=row_no)


@bp.post("/api/checklist/clear-row")
def clear_row():
    payload = request.get_json(silent=True) or {}
    try:
        row_no = int(payload.get("行番号"))
    except (TypeError, ValueError):
        return jsonify(ok=False, reason="bad_row_no",
                       message="行番号が数字ではありません"), 400
    conn = get_db()
    checklist_service.clear_row(conn, row_no)
    return jsonify(ok=True, view=_view(conn))


@bp.post("/api/checklist/clear")
def clear_all():
    """全部消す (VBA の「ﾁｪｯｸﾘｽﾄ発行」= 原紙の貼り直し)。"""
    conn = get_db()
    checklist_service.clear_all(conn)
    return jsonify(ok=True, view=_view(conn))


@bp.get("/report/checklist")
def report():
    """印刷用の紙面。A4横1ページ。"""
    from modules.packing_material_calculation.coil_tool import printing

    conn = get_db()
    rows = checklist_service.load_all(conn)
    rep = reports.build_checklist_report(rows, line=user_settings.get_line())
    # プレビューの上に「印刷する」(0.2.11。発注票と同じ。紙には出ない)
    return printing.render_html(rep, print_button=True)
