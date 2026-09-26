"""マスタ管理 (`/api/master/*`)

梱包資材マスタの中身を見て、直す。VBA の `UF_Material` 2ページ目
「仕様リスト」は**見るだけ**だったので、直す側はここで足した分。

**画面は設定の中にあります**(`app/templates/settings.html`)。
独立した画面にすると、設定からも見られるものが2か所に出てしまいます。

【書き先は取り込み元】
直すのは共有フォルダの sqlite3(梱包資材マスタ)で、手元のDBではない。
手元は総入れ替えで取り込まれるので、直しても次の取り込みで消える。
理由と手順は `coil_tool/master_admin.py` に書いてある。

【見るのと直すのは別の話】
見る(`browse`)はいつでも通す。**直すにはパスワードが要る**
(`admin_session`)。中身を確かめられることと、書き換えられることは
別の話なので、関門は書くほうにだけ置く。

判断は要求のたびに `master_admin.can_edit` で見る ── 起動時に決めて
登録を分けると、認証を通した瞬間から食い違う。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from modules.packing_material_calculation.coil_tool import admin_password, admin_session, master_admin
from modules.packing_material_calculation.coil_tool.logging_utils import get_logger
from modules.packing_material_calculation.coil_tool.presenters import master as master_presenter

from .. import get_db

log = get_logger("app.routes.master")

bp = Blueprint("master", __name__)

# 断りの種別を HTTP に写す。
#   400 … 入力の形が違う。**サーバの状態は動いていない**
#   403 … 許されていない
#   409 … 先を越された(見ていた行がもう無い)
#   422 … 業務としての断り
STATUS = {
    master_admin.REFUSE_BAD_VALUE: 400,
    master_admin.REFUSE_NOT_ALLOWED: 403,
    master_admin.REFUSE_NO_ROW: 409,
    master_admin.REFUSE_NOT_EDITABLE: 422,
    master_admin.REFUSE_NO_SOURCE: 422,
    master_admin.REFUSE_WRITE_FAILED: 422,
    master_admin.REFUSE_NOT_CREATABLE: 422,
}


@bp.get("/api/master/browse")
def browse():
    """表の一覧と、選んだ表の中身。"""
    view = master_presenter.browse(
        get_db(),
        table=request.args.get("table", ""),
        query=request.args.get("q", ""),
        sort=request.args.get("sort", ""),
        sort_dir=request.args.get("sort_dir", "asc"))
    return jsonify(master_presenter.to_dict(view))


@bp.post("/api/master/row/save")
def save_row():
    """1行を書き換える。`{"table":…, "key":…, "values":{…}}`"""
    body = request.get_json(silent=True) or {}
    return _write(master_admin.save_row(
        get_db(), _table(body), body.get("key"), _values(body)), body)


@bp.post("/api/master/row/add")
def add_row():
    """1行足す。`{"table":…, "values":{…}}`"""
    body = request.get_json(silent=True) or {}
    return _write(master_admin.add_row(
        get_db(), _table(body), _values(body)), body)


@bp.post("/api/master/row/delete")
def delete_row():
    """1行消す。`{"table":…, "key":…}`"""
    body = request.get_json(silent=True) or {}
    return _write(master_admin.delete_row(
        get_db(), _table(body), body.get("key")), body)


# ==================================================================
# マスタを直すための認証
# ==================================================================
# 画面は設定の中にあるが、**関門と同じ場所に置く**。何を守っている
# ものかが、守っている当人の隣にあるほうが追いやすい。
@bp.post("/api/master/auth")
def auth():
    """パスワードを入れて開ける / 閉じる。`{"password":…}` / `{"close":true}`"""
    body = request.get_json(silent=True) or {}
    if body.get("close"):
        admin_session.close()
        return jsonify(ok=True, auth=_auth_state(), message="認証を閉じました")

    password = str(body.get("password", ""))
    if not password:
        return jsonify(ok=False, reason="no_password",
                       message="パスワードを入れてください",
                       auth=_auth_state()), 400
    if not admin_session.open_with(password):
        # **何が違うのかは言わない。** 総当たりの手がかりを与えない
        return jsonify(ok=False, reason="wrong_password",
                       message="パスワードが違います",
                       auth=_auth_state()), 403
    return jsonify(ok=True, auth=_auth_state(),
                   message="マスタを直せるようになりました")


@bp.post("/api/master/password")
def change_password():
    """パスワードを変える。`{"current":…, "new":…, "confirm":…}`

    **いまのパスワードを知っている人だけ。** 肩越しに見ていた人が
    勝手に変えられる、を作らない。
    """
    body = request.get_json(silent=True) or {}
    result = admin_password.change(str(body.get("current", "")),
                                   str(body.get("new", "")),
                                   str(body.get("confirm", "")))
    if not result.ok:
        return jsonify(ok=False, reason=result.reason,
                       message=result.message, auth=_auth_state()), 422
    # 変えたら閉める。**新しいほうで入り直してもらう** ── 変えたつもりで
    # 打ち間違えていても、閉じていれば次に入るときに気づける
    admin_session.close()
    return jsonify(ok=True, message=result.message + "もう一度入れてください。",
                   auth=_auth_state())


def _auth_state() -> dict:
    state = admin_session.state()
    return {"open": state.open, "remains": state.remains,
            "custom": state.custom}


def _table(body: dict) -> str:
    return str(body.get("table", ""))


def _values(body: dict) -> dict:
    values = body.get("values")
    return values if isinstance(values, dict) else {}


def _write(result: master_admin.Result, body: dict):
    """書いたあとは**まるごとの状態**を返す。

    断ったときも同じ形で返す ── 画面は「何が起きたか」と「いまどう
    なっているか」を1回で受け取れる。断りの理由は `reason` が運び、
    画面は文言から推し量らない。

    絞り込み(`q`)と同じ理由で、いま押していた並び替え(`sort`/`sort_dir`)
    も送り返してもらって保つ ── 行を直すたびに並びが既定へ戻ると、
    並べ替えて探した続きの行を、また並べ替え直すことになる。
    """
    view = master_presenter.browse(
        get_db(), table=_table(body), query=str(body.get("q", "")),
        sort=str(body.get("sort", "")),
        sort_dir=str(body.get("sort_dir", "asc")),
        message=result.message if result.ok else "")
    payload = master_presenter.to_dict(view)
    if result.ok:
        return jsonify(payload)
    payload["error"] = {"code": result.reason, "message": result.message}
    return jsonify(payload), STATUS.get(result.reason, 422)
