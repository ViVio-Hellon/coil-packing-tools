"""マスタ管理(見るだけ)の口 (`/api/master/*`)

梱包資材総合ツールのマスタ管理のうち「中身を見る」ところだけを移したもの
(`meisai/master_browse.py`)。**直す口は作らない** ── 梱包資材マスタを直すのは
総合ツールの役目。

- 見る … 梱包資材マスタ / 梱包明細履歴 の表の一覧と、選んだ表の1ページ
- CSV … **ブラウザのダウンロードはしない。こちらから Excel なども開かない**
  (現場の指定)── 書き出し先のフォルダに書いて、場所を返すだけ

**この口は画面の書き込みの順番待ち(`app._WRITE_LOCK`)に入れない。**
共有を待つことがあり、そのあいだ出力などの操作を止めてしまうため
(履歴の口と同じ)。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from modules.packing_details.meisai import master_browse, slip_history
from modules.packing_details.meisai.logging_utils import get_logger

from .. import error_body, get_db

log = get_logger("app.routes.master")

bp = Blueprint("master", __name__)


def _args(source: dict) -> dict:
    return {"query": str(source.get("q", "")), "sort": str(source.get("sort", "")),
            "sort_dir": str(source.get("sort_dir", "asc"))}


@bp.get("/api/master/browse")
def browse():
    """表の一覧と、選んだ表の中身(1ページ)。**共有から直に読む。**"""
    view = master_browse.browse(request.args.get("source", ""),
                                request.args.get("table", ""), **_args(request.args))
    body = view.to_dict()
    if view.source == master_browse.HISTORY:
        # 共有に見えるのは**送った分だけ。** 送り残しがあれば数を言って、裏で送る
        body["pending"] = slip_history.pending_count(get_db())
        if body["pending"]:
            slip_history.kick()
    return jsonify(body)


@bp.post("/api/master/export")
def export():
    """いま見ている表を CSV に書き出す(絞り込み・並べ替えのまま、全行)。**書くだけ。**"""
    body = request.get_json(silent=True) or {}
    source = str(body.get("source", ""))
    if source == master_browse.HISTORY:
        # 書き出す前に、この PC の送り残しを送っておく(いま出した分も入るように)
        slip_history.send_pending(get_db())
    try:
        result = master_browse.export_csv(source, str(body.get("table", "")), **_args(body))
    except master_browse.BrowseError as exc:
        return jsonify(error_body("master", str(exc))), 400
    return jsonify({"ok": True, **result.to_dict()})
