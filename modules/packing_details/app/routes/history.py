"""明細の履歴 (全ライン・3年) の画面の口

- 探す … 期間・ロット番号・副番で紙を探して並べる
- 紙面を作り直す … 履歴の中身のとおりに明細票を組み直す(読むだけ)
- CSV … **ブラウザのダウンロードはしない**(現場の指定)。書き出し先の
  フォルダに書いて、場所を返すだけ。**Excel などをこちらから開くことも
  しない**(開くのは使う人がすること。現場の指定)

**この口は画面の書き込みの順番待ち(`app._WRITE_LOCK`)に入れない。**
共有を待つことがあり、そのあいだ出力などの操作を止めてしまうため。
手元のDBは自分の接続で、SQLite のロックを取って書く。
"""
from __future__ import annotations

import threading

from flask import Blueprint, Response, jsonify, request

from modules.packing_details.meisai import printing, qa_mark, report, slip_history
from modules.packing_details.meisai.logging_utils import get_logger

from .. import error_body, get_db

log = get_logger("app.routes.history")

bp = Blueprint("history", __name__)

# 「いま送る」で待つ上限。共有が応えないとき、画面をいつまでも待たせない
SEND_WAIT_SEC = 15.0


@bp.get("/api/history/status")
def history_status():
    """手元の送り残しと、共有の履歴の件数・期間。"""
    return jsonify(slip_history.status(get_db()))


@bp.post("/api/history/send")
def history_send():
    """いま送る。**待つのは `SEND_WAIT_SEC` まで**(その先は裏で続く)。"""
    box: dict = {}

    def run() -> None:
        box["result"] = slip_history.send_pending()

    thread = threading.Thread(target=run, name="history-send-now", daemon=True)
    thread.start()
    thread.join(SEND_WAIT_SEC)
    if thread.is_alive():
        message = "共有の応えを待っています。送り終わるまで裏で続けます。"
    else:
        result = box["result"]
        if result.busy:
            message = "いま裏で送っているところです。少し待ってから様子を見てください。"
        elif result.error:
            message = f"送れませんでした(あとで送り直します): {result.error}"
        elif result.sent:
            message = f"{result.sent}枚送りました。"
        else:
            message = "送るものはありませんでした。"
    return jsonify({"ok": True, "message": message,
                    **slip_history.status(get_db())})


@bp.post("/api/history/search")
def history_search():
    """紙を探す(新しい順・200枚まで)。共有に届かなければこの PC の分から。"""
    body = request.get_json(silent=True) or {}
    try:
        result = slip_history.search(get_db(), str(body.get("from", "")),
                                     str(body.get("to", "")), str(body.get("lot", "")),
                                     str(body.get("fuban", "")))
    except slip_history.HistoryError as exc:
        return jsonify(error_body("history", str(exc))), 400
    return jsonify({"ok": True, "rows": result.rows, "total": result.total,
                    "source": result.source, "note": result.note,
                    "limit": slip_history.SEARCH_LIMIT,
                    "load_limit": slip_history.LOAD_LIMIT})


@bp.post("/api/history/export")
def history_export():
    """共有の履歴を CSV に書き出す(期間・ロット番号・副番で絞る)。**書くだけ。**"""
    body = request.get_json(silent=True) or {}
    # 書き出す前に、この PC の送り残しを送っておく(いま出した分も入るように)
    slip_history.send_pending(get_db())
    try:
        result = slip_history.export_csv(str(body.get("from", "")),
                                         str(body.get("to", "")),
                                         str(body.get("lot", "")),
                                         str(body.get("fuban", "")))
    except slip_history.HistoryError as exc:
        return jsonify(error_body("history", str(exc))), 400
    return jsonify({"ok": True, "detail_file": result.detail_file,
                    "summary_file": result.summary_file,
                    "detail_rows": result.detail_rows, "slips": result.slips,
                    "summary_rows": result.summary_rows, "folder": result.folder,
                    "note": result.note})


@bp.get("/report/history")
def history_sheets():
    """履歴から明細票を作り直す(`?ids=<送信ID>,<送信ID>`)。**読むだけ。**

    `/report/` の下なので、紙面と同じく**トークンは要る・画面の持ち主で
    なくてよい**(別のタブで開くため)。見つからない紙があれば作らない ──
    頼んだ枚数と刷る枚数が食い違わないように。
    """
    ids = [i for i in request.args.get("ids", "").split(",") if i.strip()]
    try:
        slips, source = slip_history.load_slips(get_db(), ids)
    except slip_history.HistoryError as exc:
        return Response(
            '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
            "<title>履歴の紙面</title></head><body>"
            f"<h1>紙面を作り直せませんでした</h1><p>{printing.escape(exc)}</p>"
            "</body></html>", mimetype="text/html", status=404)
    html = report.render_history(slips, current_qa=qa_mark.current(), source=source)
    log.info("履歴から紙面を作り直しました: %s枚(%s)", len(slips), source)
    return Response(html, mimetype="text/html")
