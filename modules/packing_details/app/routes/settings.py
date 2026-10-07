"""設定 (取り込み元の置き場所と、いつ時点の台帳か・紙面の右上の文字)

移植元の設定画面から、このアプリに要るものだけを持ってきた。
アクセス権限・モードは無い(このアプリにその概念が無い)。マスタ管理は
「中身を見る」ところだけを別の口に移した(`app/routes/master.py`)。

紙面の右上の文字(既定 `NLM.NAGOYA.QA`)は**全ラインで共有**し、
**管理者パスワードで守る**(`meisai/qa_mark.py` / `shared_settings.py`)。
梱包資材マスタのフォルダ(共有)を変えるのも守る(刷る文字が変わるため)。置き場所・自動取り込みは守らない ── 間違えても
その端末の取り込みが「見つからない」と言うだけで、紙には出ない。
**守る対象を増やすほど、現場はパスワードを紙に貼る**(流用元と同じ考え)。
"""
from __future__ import annotations

import re
import threading

from flask import Blueprint, Response, jsonify, request

from common import app_config as _common_app_config
from common import logging_utils as _common_logging
from common import storage_places
from modules.packing_details.meisai import (admin_password, config, data_sync, distribution, printing,
                    qa_mark, shared_settings, user_settings)
from modules.packing_details.meisai.logging_utils import get_logger

from .. import error_body, get_db

log = get_logger("app.routes.settings")

bp = Blueprint("settings", __name__)

# 画面から変えられる設定。**一覧に無いものは変えられない。**
# 経路ごとに書き分けると、増やすたびに検証を書き足すことになる
EDITABLE = {
    config.KEY_LOT_DB_DIR: "仕掛台帳のフォルダ",
    config.KEY_KONPO_DB_DIR: "梱包課共有の仕掛フォルダ",
    config.KEY_EXPORT_DIR: "CSVの出力先",
}


# 設定画面を開いたとき、取り込み元が届くかを確かめるのを待つ上限。
# 画面は先に出してあり、ここは後から埋める(`/api/settings/share`)
REACH_WAIT_SEC = 5.0


def _local_state() -> dict:
    """この端末の設定と、取り込んである台帳の鮮度。**共有・取り込み元を見に行かない。**

    設定画面はこれを受け取ったらすぐ出す。届くか・共有の様子は
    `/api/settings/share` で後から埋める ── 届かない・遅い共有のぶんだけ
    画面が出るのを待たせない。
    """
    conn = get_db()
    return {
        "lot_db_dir": str(config.lot_db_dir()),
        "konpo_db_dir": str(config.konpo_db_dir()),
        "lot_db_dir_setting": user_settings.get(config.KEY_LOT_DB_DIR, ""),
        "konpo_db_dir_setting": user_settings.get(config.KEY_KONPO_DB_DIR, ""),
        "lot_db_dir_default": str(config.LOT_DB_DIR),
        "konpo_db_dir_default": str(config.KONPO_DB_DIR),
        "auto_import": user_settings.auto_import_enabled(),
        "export_dir": str(config.export_dir()),
        "export_dir_setting": user_settings.get(config.KEY_EXPORT_DIR, "") or "",
        "export_dir_default": str(config.EXPORT_DIR),
        # 画面に出す場所は実際に置かれている場所(Microsoft Store の Python。統合 1.2.2)
        "db_path": _common_app_config.real_location(config.DB_PATH),
        # いま書いている場所(上の帯の「ログ」で変えられる。統合 1.0.12)
        "log_dir": _common_app_config.real_location(_common_logging.log_dir()),
        # **どのフォルダに何を探しているか**を欄ごとに分けて渡す。
        # まとめて1つの表で渡すと、画面側が「どちらの欄の話か」を
        # 名前から推し量ることになる
        "lot_files": list(config.LOT_DB_FILES.values()),
        "konpo_files": list(config.KONPO_DB_FILES.values()),
        "lot_tables": list(config.LOT_DB_FILES),
        "konpo_tables": list(config.KONPO_DB_FILES),
        "files": config.ALL_SOURCE_FILES,
        "stamps": [s.to_dict() for s in data_sync.stamps(conn, check=False)],
        "qa_mark_default": qa_mark.DEFAULT,
        "qa_mark_max": qa_mark.MAX_LEN,
        "admin_min_length": admin_password.MIN_LENGTH,
        "share_default": str(config.SHARED_DIR),
        "share_setting": user_settings.get(config.KEY_SHARED_DIR, "") or "",
        "share_dir": str(shared_settings.shared_dir()),
        "share_file": shared_settings.FILE_NAME,
        "master_db_name": config.MASTER_DB_NAME,
        "history_db_name": config.HISTORY_DB_NAME,
        # 共有の2つのファイルの置き場所(既定は梱包資材マスタのフォルダと同じ)
        "history_dir": str(shared_settings.history_dir()),
        "history_setting": user_settings.get(config.KEY_HISTORY_DIR, "") or "",
        "json_dir": str(shared_settings.json_dir()),
        "json_setting": user_settings.get(config.KEY_SHARED_JSON_DIR, "") or "",
        "distribution": distribution.summary(),
        "storage": _storage(),
    }


def _storage() -> dict:
    """設定・データの保存先。**このPCに残るもの**と**複数のPCで共有するもの**を分けて出す。

    現場の指摘: このPCで引き継いで使うものと、複数のPCで共有するものは違う。
    設定部にそういうファイルがあることを明記してほしい。
    共有フォルダの**どこを見るか**という設定そのものはこのPCに入っている、も言う
    (「梱包資材マスタのフォルダ（共有）」を変えても、ほかのPCは変わらない)。
    """
    P = storage_places.Place
    share = shared_settings.shared_dir()
    jdir = shared_settings.json_dir()
    hdir = shared_settings.history_dir()
    config_path = config.USER_CONFIG_PATH
    return storage_places.Places(
        local=[
            P("設定ファイル", str(config_path),
              "「置き場所・取り込み」の値（仕掛台帳・梱包課共有の仕掛・CSVの出力先・"
              "梱包資材マスタのフォルダをどこにするか）・起動時の自動取り込み・"
              "右上の文字と管理者パスワードの控え（共有に届かないときに使う）",
              "" if config_path.exists() else "まだありません（設定を保存すると作ります）"),
            P("手元のDB", str(config.DB_PATH),
              "取り込んだ台帳の写し・副番履歴・作業の途中（直近5ロット）・出力した明細・"
              "共有へ送る前の明細の履歴（届かないときはここに残して、あとで送る）"),
            storage_places.log_place(),
        ],
        shared=[
            P("紙面の右上の文字", str(share / config.MASTER_DB_NAME),
              "表「梱包明細打ち出し」の右上の文字。「紙面の右上の文字」の面で変える"),
            P("管理者パスワード", str(jdir / shared_settings.FILE_NAME),
              "管理者パスワード（撹拌した値）と右上の文字の控え。「管理者パスワード」の面で変える"),
            P("明細の履歴", str(hdir / config.HISTORY_DB_NAME),
              "出力した明細の履歴（全ライン・3年）。出力するたびにこのPCから送る"),
            P("仕掛台帳", str(config.lot_db_dir()),
              "取り込み元（" + " / ".join(config.LOT_DB_FILES.values()) + "）",
              "読むだけ（このツールは書き換えません）"),
            P("梱包課共有の仕掛", str(config.konpo_db_dir()),
              "取り込み元（" + " / ".join(config.KONPO_DB_FILES.values()) + "）",
              "読むだけ（このツールは書き換えません）"),
        ],
        dist=[
            P("配布設定", str(distribution.settings_path()),
              "「配布設定」の面で書き出した値（"
              + "・".join(label for _, label, _ in distribution.ITEMS) + "）"),
            storage_places.log_dist_place(),
        ],
        shared_note=("共有フォルダの場所（どこを見るか）は、このPCの設定ファイルに入っています。"
                     "「置き場所・取り込み」で変えても、ほかのPCは変わりません"
                     "（ほかのPCもそろえるときは配布設定）。"),
    ).to_dict()


@bp.get("/api/settings")
def read_settings():
    """いまの設定と、取り込んである台帳の鮮度。**すぐ返す**(共有を見に行かない)。"""
    return jsonify(_local_state())


@bp.get("/api/settings/share")
def read_share():
    """取り込み元が届くか・共有の様子(右上の文字・管理者パスワード)。

    **待つのは `REACH_WAIT_SEC` まで。** 取り込み元を探すのと共有を読むのを
    並べて行い、取り込み元が応えなければ「確かめきれない」と返す。
    """
    box: dict = {}

    def find() -> None:
        box["found"] = data_sync.find_sources()

    thread = threading.Thread(target=find, name="settings-reach", daemon=True)
    thread.start()
    state = _qa_state()
    thread.join(REACH_WAIT_SEC)
    stamps = data_sync.stamps(get_db(), check=False, found=box.get("found"))
    reach_problem = ""
    if "found" not in box:
        reach_problem = (f"取り込み元のフォルダが{REACH_WAIT_SEC:g}秒以内に応えません"
                         "（届くかどうかを確かめきれませんでした）。")
    return jsonify({"stamps": [st.to_dict() for st in stamps],
                    "reach_problem": reach_problem, **state})


def _qa_state() -> dict:
    """紙面の右上の文字と、管理者パスワード・共有の様子。

    **パスワードの値は出さない。** 共有は1回だけ読む(届かないときに
    何度も待たせない)。
    """
    snap = shared_settings.read()
    value, _ = qa_mark.resolve(snap)
    return {
        "qa_mark": value,
        "qa_mark_default": qa_mark.DEFAULT,
        "qa_mark_custom": value != qa_mark.DEFAULT,
        "qa_mark_max": qa_mark.MAX_LEN,
        "admin_custom": admin_password.is_custom(snap),
        "admin_min_length": admin_password.MIN_LENGTH,
        "share_default": str(config.SHARED_DIR),
        "share_setting": user_settings.get(config.KEY_SHARED_DIR, "") or "",
        "share_dir": str(shared_settings.shared_dir()),
        "share_file": shared_settings.FILE_NAME,
        **snap.to_dict(),
    }


def _status(reason: str, *, password_reasons: tuple[str, ...]) -> int:
    """断りの種類から HTTP の番号。**文言から推し量らない。**"""
    if reason in password_reasons:
        return 403
    if reason == qa_mark.REFUSE_SHARED:
        return 503                      # 共有に届かない。時間を置けば通る
    return 400


@bp.post("/api/settings/qa-mark")
def set_qa_mark():
    """紙面の右上の文字を変える・既定に戻す。**管理者パスワードが要る。**

    `{"value": "...", "password": "..."}` で変える、
    `{"reset": true, "password": "..."}` で既定に戻す。
    **次に開く紙面から効く**(紙面は開くたびに組む)。開いたままの紙面へは、
    画面が知らせて差し替える。
    """
    body = request.get_json(silent=True) or {}
    password = str(body.get("password", ""))
    if body.get("reset"):
        result = qa_mark.reset(password)
    else:
        result = qa_mark.change(body.get("value", ""), password)
    if not result.ok:
        status = _status(result.reason,
                         password_reasons=(qa_mark.REFUSE_NEED_PASSWORD,))
        field = "password" if status == 403 else "value"
        return jsonify(error_body(result.reason, result.message, field)), status
    return jsonify({"ok": True, "message": result.message, **_qa_state()})


@bp.post("/api/settings/admin-password")
def change_admin_password():
    """管理者パスワードを変える・既定に戻す。**値は保存も応答もしない**(撹拌して持つ)。"""
    body = request.get_json(silent=True) or {}
    if body.get("reset"):
        result = admin_password.reset(str(body.get("current", "")))
    else:
        result = admin_password.change(str(body.get("current", "")),
                                       str(body.get("new", "")),
                                       str(body.get("confirm", "")))
    if not result.ok:
        status = _status(result.reason,
                         password_reasons=(admin_password.REFUSE_WRONG,))
        field = "current" if status == 403 else "new"
        return jsonify(error_body(result.reason, result.message, field)), status
    return jsonify({"ok": True, "message": result.message, **_qa_state()})


# 共有の置き場所(どれも管理者パスワードで守る)。`which` → (設定の鍵, 呼び名, 置くファイル)
SHARED_PLACES = {
    "share": (config.KEY_SHARED_DIR, "梱包資材マスタのフォルダ（共有）", config.MASTER_DB_NAME),
    "history": (config.KEY_HISTORY_DIR, "明細の履歴のフォルダ", config.HISTORY_DB_NAME),
    "json": (config.KEY_SHARED_JSON_DIR, "管理者パスワード・控えのフォルダ",
             shared_settings.FILE_NAME),
}


@bp.post("/api/settings/shared-dir")
def set_shared_dir():
    """共有の設定フォルダを、**この端末だけ**差し替える。管理者パスワードが要る。

    `which` で3つのどれかを選ぶ(既定 `share`)。VER 0.13.10 で、明細の履歴
    (梱包明細履歴.sqlite3)と控えのJSON(梱包明細打ち出し.json)を**それぞれ**
    別の場所にできるようにした(現場の指摘)。空なら梱包資材マスタのフォルダと同じ。
    控えのJSONは管理者パスワードの置き場所なので、**いまの**パスワードで守る。

    既定の置き場所に書けない・届かない現場のための逃げ道。差し替えると
    刷る右上の文字の出どころが変わるので、守る。照合は**いまの**共有
    (届かなければこの端末の写し)のパスワードで行う ── 届かない既定を
    直すための口なので、届くことは求めない。

    空なら既定に戻す。**全ラインで同じ場所を指さないと共有にならない**
    ので、差し替えた端末は画面にそう出す。
    """
    body = request.get_json(silent=True) or {}
    which = str(body.get("which", "share") or "share")
    if which not in SHARED_PLACES:
        return jsonify(error_body("not_listed", "その置き場所は変えられません。", "which")), 400
    key, label, file_name = SHARED_PLACES[which]
    if not admin_password.verify(str(body.get("password", ""))):
        log.warning("共有の置き場所の変更を断りました(管理者パスワード): %s", label)
        return jsonify(error_body(
            "need_password", f"{label}を変えるには管理者パスワードが要ります。",
            "password")), 403
    value = str(body.get("value", "")).strip()
    if value:
        try:
            resolved = config.resolve_dir(value)
        except ValueError as exc:
            return jsonify(error_body("bad_path", str(exc), "value")), 400
    else:
        resolved = None
    if not user_settings.save(key, value):
        return jsonify(error_body(
            "write_failed", "設定ファイルに書けませんでした。")), 500
    if which != "share":
        return _other_place_saved(which, label, file_name, value)
    resolved = resolved or config.SHARED_DIR
    log.info("共有の置き場所を変えました(この端末): %s", resolved)
    state = _qa_state()
    if state["share_source"] == "shared":
        message = (f"梱包資材マスタのフォルダを {resolved} にしました。"
                   "右上の文字・「マスタ管理」・「履歴」は、いまからこの場所を見ます。")
    else:
        message = (f"梱包資材マスタのフォルダを {resolved} にしましたが、届きません"
                   f"（{state['share_problem']}）。")
    if value:
        message += "ほかのラインも同じ場所にしないと、共有になりません。"
    return jsonify({"ok": True, "message": message, **state})


def _other_place_saved(which: str, label: str, file_name: str, value: str):
    """明細の履歴・控えのJSONの置き場所を保存したあと。**そこに何があるか**を言う。

    新しい場所にファイルが無いと、履歴は次の送りで作られ(前の場所の履歴は前の
    場所に残る)、控えのJSONが無いと管理者パスワードは既定に戻る ── 黙らない。
    """
    folder = shared_settings.history_dir() if which == "history" else shared_settings.json_dir()
    same = folder == shared_settings.shared_dir()
    where = "梱包資材マスタのフォルダと同じ場所" if same and not value else str(folder)
    log.info("%sを変えました(この端末): %s", label, folder)
    if not folder.is_dir():
        note = (f"{folder} に届きません(フォルダが無いか、共有に届いていません)。"
                + ("届くまで、出力した明細の履歴はこのPCに残して、あとで送ります。"
                   if which == "history" else
                   "届くまで、右上の文字はこのPCの写しで刷ります。"))
    elif (folder / file_name).exists():
        note = f"{file_name} があります。いまからこれを使います。"
    elif which == "history":
        note = (f"まだ {file_name} がありません。次に出力したとき(または履歴を開いたとき)に"
                "作ります。前の場所の履歴は前の場所に残ります。")
    else:
        note = (f"まだ {file_name} がありません。管理者パスワードは既定に戻り、右上の文字は"
                "梱包資材マスタの値を使います。前の場所のファイルを写すと引き継げます。")
    message = f"{label}を {where} にしました。{note}"
    if value:
        message += "ほかのラインも同じ場所にしないと、共有になりません。"
    return jsonify({"ok": True, "message": message, **_qa_state(), **_local_state()})


@bp.post("/api/settings")
def write_settings():
    """設定を書き換える。

    **道が読めるかどうかはここで言う。** 打ち間違いを保存できて
    しまうと、次の取り込みで「見つかりません」とだけ出て、
    どこを直せばよいか分からない。
    """
    body = request.get_json(silent=True) or {}
    key = str(body.get("key", ""))
    if key not in EDITABLE:
        return jsonify(error_body("not_listed",
                                  "その設定は変えられません。", "key")), 400

    value = str(body.get("value", "")).strip()
    if value:
        try:
            resolved = config.resolve_dir(value)
        except ValueError as exc:
            return jsonify(error_body("bad_path", str(exc), "value")), 400
        note = ("アプリのフォルダからの相対として読みました: "
                f"{resolved}") if config.is_relative_setting(value) else ""
    else:
        resolved = None
        note = "既定の置き場所に戻しました。"

    if key == config.KEY_EXPORT_DIR:
        # **書く場所**なので、書けるかをその場で確かめる。取り込みは要らない
        target = resolved or config.EXPORT_DIR
        problem = _writable_problem(target)
        if problem:
            return jsonify(error_body("not_writable", problem, "value")), 400
        if not user_settings.save(key, value):
            return jsonify(error_body(
                "write_failed", "設定ファイルに書けませんでした。")), 500
        return jsonify({"ok": True, "note": (note + "\n" if note else "")
                        + f"CSV はここに書き出します: {target}",
                        "resolved": str(target)})

    if not user_settings.save(key, value):
        return jsonify(error_body(
            "write_failed", "設定ファイルに書けませんでした。")), 500

    conn = get_db()
    found = data_sync.find_sources()
    missing = data_sync.missing_sources(found)
    return jsonify({
        "ok": True,
        "note": note,
        "resolved": str(resolved) if resolved else "",
        "missing": missing,
        "stamps": [s.to_dict() for s in data_sync.stamps(conn)],
    })


def _writable_problem(folder) -> str:
    """そのフォルダに CSV を書けるか。書けなければ理由(無ければ作ってみる)。"""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"そのフォルダには書けません: {folder}({exc})"
    return ""


# ------------------------------------------------------------------
# 配布設定(`meisai/distribution.py`)。どれも管理者パスワードが要る
# ------------------------------------------------------------------
def _distribution_reply(result: distribution.Result):
    if not result.ok:
        status = 403 if result.reason == distribution.REFUSE_NEED_PASSWORD else 400
        if result.reason == distribution.REFUSE_FAILED:
            status = 500
        field = "password" if status == 403 else ""
        return jsonify(error_body(result.reason, result.message, field)), status
    # 読み込み直したら置き場所の欄も変わる。**まるごとの状態**を返す
    return jsonify({"ok": True, "message": result.message, **_local_state()})


@bp.post("/api/settings/distribution/export")
def export_distribution():
    """この端末のいまの設定を、配布設定として書き出す。"""
    body = request.get_json(silent=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        return jsonify(error_body("bad_input", "入れる項目の形が違います。")), 400
    return _distribution_reply(distribution.export(str(body.get("password", "")), items))


@bp.post("/api/settings/distribution/remove")
def remove_distribution():
    body = request.get_json(silent=True) or {}
    return _distribution_reply(distribution.remove(str(body.get("password", ""))))


@bp.post("/api/settings/distribution/reapply")
def reapply_distribution():
    """置いてある配布設定を読み込み直す(この端末の設定も上書き)。"""
    body = request.get_json(silent=True) or {}
    return _distribution_reply(distribution.reapply(str(body.get("password", ""))))


@bp.post("/api/settings/auto-import")
def set_auto_import():
    """起動時に取り込み直すかどうか。"""
    body = request.get_json(silent=True) or {}
    user_settings.save(config.KEY_AUTO_IMPORT, bool(body.get("enabled")))
    return jsonify({"ok": True,
                    "auto_import": user_settings.auto_import_enabled()})


# ------------------------------------------------------------------
# 説明書 (VBA `cmdManual_Click`)
# ------------------------------------------------------------------
# VBA は共有フォルダのHTMLを開いていた。
#
#     \\nlmfangyshrd\…\説明書\梱包明細システム説明書.html
#
# **アプリと一緒に配る**ほうに変えた。共有に届かない端末でも読めるし、
# 画面を直したときに説明書だけ古いまま残ることもない(同じ版として
# 配られる)。
#
# 変更履歴も同じ経路で出す。**「この版で何が変わったか」を端末の上で
# 読めないと、版を見せた意味が半分になる。**
DOC_FILES = {
    "説明書": config.BASE_DIR / "docs" / "説明書.md",
    "変更履歴": config.BASE_DIR / "docs" / "変更履歴.md",
}
DOC_PATH = DOC_FILES["説明書"]      # 既定

_DOC_CSS = """
body{max-width:52rem; margin:0 auto; padding:2rem 1.25rem 4rem;
  font-family:"Meiryo UI",Meiryo,system-ui,sans-serif; line-height:1.85;
  color:#13232b; background:#fff;}
h1{font-size:1.5rem; border-bottom:2px solid #0e6a72; padding-bottom:.4rem}
h2{font-size:1.15rem; margin-top:2.2rem; border-left:4px solid #0e6a72;
  padding-left:.6rem}
h3{font-size:1rem; margin-top:1.5rem; color:#0e6a72}
table{border-collapse:collapse; width:100%; margin:1rem 0; font-size:.92rem}
th,td{border:1px solid #cfdadf; padding:.45rem .6rem; text-align:left;
  vertical-align:top}
th{background:#eef2f4}
code{background:#eef2f4; padding:.1rem .35rem; border-radius:3px;
  font-family:Consolas,monospace; font-size:.9em}
blockquote{border-left:4px solid #b85400; background:#fbe8d8; margin:1rem 0;
  padding:.7rem 1rem; border-radius:0 4px 4px 0}
blockquote p{margin:0}
pre{background:#eef2f4; padding:.8rem 1rem; border-radius:4px;
  overflow-x:auto; font-size:.88rem}
pre code{background:none; padding:0}
hr{border:none; border-top:1px solid #cfdadf; margin:2rem 0}
@media print{ body{max-width:none} }
"""


@bp.get("/docs")
@bp.get("/docs/<doc>")
def manual(doc: str = "説明書") -> Response:
    """同梱の文書を出す。**トークンは要らない。**

    業務データを1つも含まないので、`/api/` と同じ守りは要らない
    (逆に、トークンの切れた画面から開けないほうが困る)。

    **名前は一覧に載っているものだけ。** 受け取った文字列でパスを
    組み立てると、`../` を書かれたときにアプリの外まで読める。

    Markdown をそのまま整形して出す。変換の仕組みを入れないのは、
    追加ライブラリを増やさないため ── 見出し・表・箇条書き・引用と
    いった、この文書が実際に使う記法だけを組む。
    """
    path = DOC_FILES.get(doc)
    if path is None:
        return Response(
            f"<h1>そのような文書はありません</h1><p>{printing.escape(doc)}</p>",
            mimetype="text/html", status=404)
    if not path.exists():
        return Response(
            f"<h1>{printing.escape(doc)}が見つかりません</h1>"
            f"<p>{printing.escape(str(path))}</p>",
            mimetype="text/html", status=404)
    body = _render_markdown(path.read_text(encoding="utf-8"))
    return Response(
        '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{printing.escape(doc)} — コイル梱包明細打ち出しシステム</title>"
        f"<style>{_DOC_CSS}</style></head><body>{body}</body></html>",
        mimetype="text/html")


def _inline(text: str) -> str:
    """行の中の記法。**強調** と `コード` だけ。

    **コードを先に取り分ける。** 中の文字は記法として読まない ──
    `` `**` `` と書いた記号を、離れた所の `**` と組にして強調にしない。
    取り分けたあとで強調を組み、最後に戻す(強調の中のコードも崩れない)。
    """
    codes: list[str] = []

    def keep(m: re.Match) -> str:
        codes.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    out = re.sub(r"`([^`]+)`", keep, printing.escape(text))
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], out)


def _render_markdown(text: str) -> str:
    """この文書が使う記法だけを組む。

    見出し / 表 / 箇条書き / 引用 / コード / 区切り線 / 段落。
    **足りなくなったらここへ足す** ── 汎用の変換器を入れるより、
    使う記法が一覧になっているほうが、崩れたときに直す先が分かる。

    【段落は行をまとめてから組む】
    1行ずつ組むと、**行をまたいだ強調**が壊れる。

        少しかかるときは「起動中」の画面が出ますが、**止まっている
        わけではありません** ── …

    上の行だけを見ると `**` が1つしかないので、開いたまま閉じない。
    Markdown の段落は「空行までの連続した行」なので、まとめてから
    組めばそのまま通る(この不具合は試験が見つけた)。

    **箇条書きと引用も同じ。** 箇条書きは字下げした続きの行を同じ項目に、
    引用は `>` だけの行までを1つの段落にまとめる。以前は1行ずつ組んで
    いたので、続きの行が箇条書きの外へ出て別の段落になり、行をまたいだ
    強調が `**` のまま出ていた(変更履歴で目に見えていた)。
    """
    html: list[str] = []
    rows: list[str] = []          # 表の行
    items: list[str] = []         # 箇条書き
    para: list[str] = []          # 段落の行
    quote: list[str] = []         # 引用の行
    code: list[str] = []          # コードブロックの行
    in_code = False

    def flush_para() -> None:
        if para:
            html.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    def flush_quote() -> None:
        if quote:
            # `>` だけの行(空文字)で段落を分ける
            paras, cur = [], []
            for q in quote + [""]:
                if q:
                    cur.append(q)
                elif cur:
                    paras.append(" ".join(cur))
                    cur = []
            html.append("<blockquote>"
                        + "".join(f"<p>{_inline(q)}</p>" for q in paras)
                        + "</blockquote>")
            quote.clear()

    def flush_list() -> None:
        if items:
            html.append("<ul>"
                        + "".join(f"<li>{_inline(i)}</li>" for i in items)
                        + "</ul>")
            items.clear()

    def flush_table() -> None:
        if not rows:
            return
        cells = [[c.strip() for c in r.strip().strip("|").split("|")]
                 for r in rows]
        # 2行目が `---` の区切りなら1行目が見出し
        head, body = (cells[0], cells[2:]) if (
            len(cells) > 1 and set("".join(cells[1])) <= set("-: ")
        ) else (None, cells)
        html.append("<table>")
        if head:
            html.append("<thead><tr>"
                        + "".join(f"<th>{_inline(c)}</th>" for c in head)
                        + "</tr></thead>")
        html.append("<tbody>")
        for row in body:
            html.append("<tr>"
                        + "".join(f"<td>{_inline(c)}</td>" for c in row)
                        + "</tr>")
        html.append("</tbody></table>")
        rows.clear()

    def flush_all() -> None:
        flush_para()
        flush_quote()
        flush_list()
        flush_table()

    for line in text.splitlines():
        stripped = line.strip()

        # --- コードブロック。中は何も解釈しない ---
        if stripped.startswith("```"):
            if in_code:
                html.append("<pre><code>"
                            + printing.escape("\n".join(code))
                            + "</code></pre>")
                code.clear()
            else:
                flush_all()
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue

        if not stripped:
            flush_all()
            continue

        if stripped.startswith("|"):
            flush_para()
            flush_quote()
            flush_list()
            rows.append(stripped)
            continue
        flush_table()

        if stripped.startswith("- "):
            flush_para()
            flush_quote()
            items.append(stripped[2:])
            continue
        # 字下げした行は、直前の項目の続き
        if items and line[:1] in (" ", "\t"):
            items[-1] += " " + stripped
            continue
        flush_list()

        if stripped.startswith(">"):
            flush_para()
            quote.append(stripped[1:].strip())
            continue
        flush_quote()

        if stripped.startswith(("# ", "## ", "### ")):
            flush_para()
            level = len(stripped) - len(stripped.lstrip("#"))
            html.append(f"<h{level}>{_inline(stripped[level + 1:])}</h{level}>")
            continue
        if set(stripped) <= set("-") and len(stripped) >= 3:
            flush_para()
            html.append("<hr>")
            continue

        para.append(stripped)

    if in_code and code:
        html.append("<pre><code>" + printing.escape("\n".join(code))
                    + "</code></pre>")
    flush_all()
    return "".join(html)
