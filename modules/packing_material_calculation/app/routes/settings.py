"""設定 ── 取り込み元の置き場所、ライン、担当者

VBA はパスを標準モジュールの `Public Const` に直接書いていた。置き場所が
変わるとブックを開いてコードを直す必要があり、端末ごとに写しがずれる
原因になっていた。ここでは**画面から変えて、その場で届くか確かめられる**。

    打つ → 「確かめる」で到達を見る → 「保存」 → 「取り込み」

保存と取り込みを分けてあるのは、**打ち間違いで前の写しを壊さない**ため。
保存しただけでは手元のデータは変わらない。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from common import app_config as _integrated_app_config
from common import logging_utils as _common_logging
from common import storage_places
from modules.packing_material_calculation.coil_tool import (admin_session, app_config, config, data_sync,
                       distribution, source_db, staff, user_settings)
from modules.packing_material_calculation.coil_tool.presenters import master as master_presenter
from modules.packing_material_calculation.coil_tool.logging_utils import get_logger

from .. import base, conf, get_db, shell

log = get_logger("app.routes.settings")

bp = Blueprint("settings", __name__)

# 画面に出す欄。**この表が唯一の出どころ** ── 欄を足すときはここに1行。
# `key` は `user_settings` の鍵、`resolve` はいま実際に使っている場所
FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": config.KEY_MASTER_DB_DIR,
        "label": "梱包資材マスタのフォルダ",
        "detail": "包装仕様・パレット・リプラサイズを読む先",
        "vba": "PATH_AIM_参照",
        "expect": config.MATERIAL_DB_NAME,
        "default": str(config.DEFAULT_MASTER_DB_DIR),
    },
    {
        "key": config.KEY_LOT_DB_DIR,
        "label": "仕掛台帳のフォルダ",
        "detail": "ロット検索と受注情報を読む先",
        "vba": "PATH_仕掛_参照",
        "expect": ", ".join(config.LOT_DB_FILES.values()),
        "default": str(config.DEFAULT_LOT_DB_DIR),
    },
    {
        "key": config.KEY_LOT_DB_DIR_2,
        "label": "仕掛台帳の予備フォルダ",
        "detail": "本命が読めないときに見る先。空でよい",
        "vba": "PATH_梱包_仕掛",
        "expect": ", ".join(config.LOT_DB_FILES.values()),
        "default": "",
        "optional": True,
    },
    {
        "key": config.KEY_EXPORT_DIR,
        "label": "書き出し先",
        "detail": "CSV を出す場所。**書ける場所**にすること",
        "vba": "",
        "expect": "",
        "default": str(config.DEFAULT_EXPORT_DIR),
        "writable": True,
    },
)

# **置き場所はパスワードが要る。** ライン・担当者・自動取り込みは
# 使う人ごとの持ち物なので要らない。
#
# 分けるのは、効く範囲が違うから ── 置き場所を間違えると
# **その端末が計算できなくなる**(マスタを読めない)。担当者を間違えても
# 出てくる票の名前が違うだけで、気づいたら選び直せばよい。
PATH_KEYS = frozenset({
    config.KEY_MASTER_DB_DIR, config.KEY_LOT_DB_DIR,
    config.KEY_LOT_DB_DIR_2, config.KEY_EXPORT_DIR,
})

REFUSE_NEEDS_PASSWORD = "needs_password"
NEEDS_PASSWORD_MESSAGE = (
    "置き場所を変えるにはパスワードが要ります。"
    "設定のいちばん上でパスワードを入れてください。")


def _path_gate(keys) -> bool:
    """置き場所を触ってよいか。触らないなら聞かない。"""
    if not (PATH_KEYS & set(keys)):
        return True
    return admin_session.is_open()


_RESOLVERS = {
    config.KEY_MASTER_DB_DIR: config.master_db_dir,
    config.KEY_LOT_DB_DIR: config.lot_db_dir,
    config.KEY_LOT_DB_DIR_2: lambda: config.lot_db_dir_fallback() or "",
    config.KEY_EXPORT_DIR: config.export_dir,
}


# ==================================================================
# 画面
# ==================================================================
@bp.get("/settings")
def page():
    conn = get_db()
    return render_template(
        "settings.html",
        page=shell.page("settings"),
        pages=shell.PAGES,
        ribbon=shell.ribbon(conn),
        # マスタ管理の面。**共有フォルダには触らない分だけ**渡して、
        # 中身は画面が開いてから読む
        master=master_presenter.to_dict(master_presenter.frame(conn)),
        token=conf()["TOKEN"],
    )


# ==================================================================
# いまの設定
# ==================================================================
def _field_view(field: dict[str, Any]) -> dict[str, Any]:
    """1欄ぶんの表示用データ。

    **「打った値」と「実際に見に行く場所」を両方出す。** 相対で打たれた
    ときにどこを指すのかが分からないと、届かない理由を追えない。
    """
    key = field["key"]
    raw = user_settings.get(key)
    raw = raw if isinstance(raw, str) else ""
    resolved = _RESOLVERS[key]()
    return {
        "key": key,
        "label": field["label"],
        "detail": field["detail"],
        "vba": field.get("vba", ""),
        "expect": field.get("expect", ""),
        "optional": bool(field.get("optional")),
        "value": raw,
        "default": field["default"],
        "using_default": not raw,
        "resolved": str(resolved),
        "relative": config.is_relative_setting(raw),
    }


def _about() -> dict[str, Any]:
    """このアプリ自身の素性。

    **版は `config/app.json` の `version` ただ1つが出どころ**で、帯の
    バッジも `/api/health` もここも同じものを読む。二重に持たない。

    番号だけでなく、置き場所・ポート・Python まで出すのは、現場から
    「動かない」と言われたときに最初に聞くことがこの一式だから。
    画面で読めれば、端末に入って調べなくても電話で確かめられる。
    """
    # 【統合版】ポートとアプリ本体は**統合アプリのもの**を出す。機能の config/app.json の
    # ポートは使われない(統合アプリが決める)ので、出すと「8740 (設定は 8733)」と
    # 繰り上がったように見えていた。版・アプリID・設定ファイルは機能のもの
    mode = str(conf().get("MODE", ""))
    port = int(conf().get("PORT", 0) or 0)
    return {
        "display_name": app_config.display_name(),
        "version": app_config.version(),
        "version_label": app_config.version_label(),
        "app_id": app_config.app_id(),
        "mode": mode,
        "port": port,
        "actual_port": int(conf().get("PORT", 0) or 0),
        "python": sys.version.split()[0],
        "python_exe": sys.executable,
        "app_root": str(_integrated_app_config.APP_ROOT),
        "module_root": str(app_config.APP_ROOT),
        "app_config_path": str(app_config.CONFIG_PATH),
        "local_root": str(app_config.local_root()),
        # 版の書き方が壊れていても起動は止めない。**ここに出して気づかせる**
        "version_problem": app_config.version_problem(),
        "config_error": app_config.load_error(),
    }


def _view(conn) -> dict[str, Any]:
    worker = user_settings.get_worker()
    return {
        "about": _about(),
        "fields": [_field_view(f) for f in FIELDS],
        "line": user_settings.get_line(),
        "lines": list(config.LINES),
        "worker": worker,
        # 梱包資材マスタの「班員名簿」から、担当ラインが機側の人だけ。
        # 班ごとに束ねて返す
        "workers": staff.choices(conn, current=worker),
        "auto_import": user_settings.get_auto_import(),
        "imported": data_sync.last_import(conn),
        "imported_at": data_sync.imported_at(conn),
        "stale": data_sync.is_stale(conn),
        "stale_why": data_sync.stale_message(conn),
        # 置き場所を触れるか。**画面はこれを見て、押す前に断る**
        "can_edit_paths": admin_session.peek(),
        # 配布設定(`coil_tool/distribution.py`)。パスワードの値は出さない
        "distribution": distribution.summary(),
        "config_path": str(config.USER_CONFIG_PATH),
        "db_path": str(config.DB_PATH),
        # いま書いている場所(上の帯の「ログ」で変えられる。統合 1.0.12)
        "log_dir": str(_common_logging.log_dir()),
        "storage": _storage(),
    }


def _storage() -> dict[str, Any]:
    """設定・データの保存先。**このPCに残るもの**と**複数のPCで共有するもの**を分けて出す。

    現場の指摘: このPCで引き継いで使うものと、複数のPCで共有するものは違う。
    設定部にそういうファイルがあることを明記してほしい。
    **パスワードはこのPCのもの**(梱包明細は全ラインで共有)。取り違えないよう、ここでも言う。
    """
    P = storage_places.Place
    config_path = config.USER_CONFIG_PATH
    lot_dir = config.lot_db_dir()
    return storage_places.Places(
        local=[
            P("設定ファイル", str(config_path),
              "取り込み元の置き場所（梱包資材マスタ・仕掛台帳・予備・書き出し先をどこにするか）・"
              "ライン・担当者・起動時の自動取り込み・パスワード（撹拌した値。このPCだけのもの）",
              "" if config_path.exists() else "まだありません（設定を保存すると作ります）"),
            P("作業用DB", str(config.DB_PATH),
              "取り込んだマスタと仕掛台帳の写し（包装仕様・パレット・リプラサイズ・班員名簿・"
              "仕掛引当・仕掛受注）・発注チェックリスト・発注履歴・取り込みの記録"),
            storage_places.log_place(),
        ],
        shared=[
            P("梱包資材マスタ", str(config.master_db_dir() / config.MATERIAL_DB_NAME),
              "取り込み元。「マスタ管理」で直すとここへ書きます（手元の写しではなく元のファイル）"),
            P("仕掛台帳", str(lot_dir),
              "取り込み元（" + " / ".join(config.LOT_DB_FILES.values()) + "）",
              "読むだけ（このツールは書き換えません）"),
        ],
        dist=[
            P("配布設定", str(distribution.settings_path()),
              "「配布設定」の面で書き出した値（"
              + "・".join(label for _, label, _ in distribution.ITEMS) + "）"),
        ],
        shared_note=("取り込み元の場所（どこを見るか）は、このPCの設定ファイルに入っています。"
                     "ここで変えても、ほかのPCは変わりません（ほかのPCもそろえるときは配布設定）。"),
    ).to_dict()


@bp.get("/api/settings")
def get_settings():
    return jsonify(ok=True, view=_view(get_db()))


# ==================================================================
# 確かめる ── 保存する前に、そこに届くか見る
# ==================================================================
def _probe_dir(field: dict[str, Any], text: str) -> dict[str, Any]:
    """打たれた場所を見に行く。**保存はしない。**"""
    out: dict[str, Any] = {"key": field["key"], "ok": False,
                           "resolved": "", "message": "", "found": []}
    text = (text or "").strip()
    if not text:
        if field.get("optional"):
            out.update(ok=True, message="空のままにします(使いません)")
        else:
            out.update(ok=True, resolved=field["default"],
                       message="空なので既定の場所を使います")
        return out

    try:
        path = config.resolve_dir(text)
    except ValueError as exc:
        out["message"] = f"読めない書き方です: {exc}"
        return out
    out["resolved"] = str(path)

    if not path.exists():
        out["message"] = "そこにフォルダがありません(綴りと共有への接続を確認してください)"
        return out
    if not path.is_dir():
        out["message"] = "フォルダではなくファイルを指しています"
        return out

    if field.get("writable"):
        # 書く場所は**書けることまで**確かめる。読めるだけでは足りない
        probe = path / ".coil_tool_write_test"
        try:
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            out["message"] = f"フォルダはありますが書けません: {exc}"
            return out
        out.update(ok=True, message="書けます")
        return out

    # 読む場所は**期待するファイルが在るか**まで見る。フォルダだけ在って
    # 中身が無い、が現場でいちばん多い
    try:
        files = [p.name for p in source_db.list_source_files(path)]
    except OSError as exc:
        out["message"] = f"フォルダを見られません: {exc}"
        return out

    out["found"] = files
    if not files:
        out["message"] = "フォルダはありますが、sqlite3 のファイルが見つかりません"
        return out
    out.update(ok=True, message=f"{len(files)} 件見つかりました")
    return out


@bp.post("/api/settings/probe")
def probe():
    """打たれた場所に届くかを確かめる。保存はしない。"""
    payload = request.get_json(silent=True) or {}
    results = []
    for field in FIELDS:
        if field["key"] in payload:
            results.append(_probe_dir(field, str(payload[field["key"]])))
    if not results:
        return jsonify(ok=False, reason="no_field",
                       message="確かめる欄がありません"), 400
    return jsonify(ok=True, results=results)


# ==================================================================
# 保存
# ==================================================================
@bp.post("/api/settings")
def save_settings():
    """設定を保存する。**まとめて1回で書く**(半分だけ変わらないように)。"""
    payload = request.get_json(silent=True) or {}
    if not _path_gate(payload):
        # 403。**入力は正しく、いまこの操作をする資格が無い**という断り方
        return jsonify(ok=False, reason=REFUSE_NEEDS_PASSWORD,
                       message=NEEDS_PASSWORD_MESSAGE), 403

    values: dict[str, Any] = {}

    for field in FIELDS:
        key = field["key"]
        if key in payload:
            values[key] = str(payload[key] or "").strip()

    if "line" in payload:
        line = str(payload["line"] or "")
        if line not in config.LINES:
            return jsonify(ok=False, reason="bad_line",
                           message=f"ラインは {' / '.join(config.LINES)} です"), 422
        values[config.KEY_LINE] = line

    if "worker" in payload:
        worker = str(payload["worker"] or "")
        # 名簿(担当ラインが機側の人)と突き合わせる。
        # **いま選ばれている名前は通す** ── 異動で名簿から消えた人や
        # 機側から外れた人を選んだままの端末が、設定を開いただけで
        # 弾かれないようにする
        if not staff.is_known(get_db(), worker,
                              current=user_settings.get_worker()):
            return jsonify(ok=False, reason="bad_worker",
                           message="担当者に選べない名前です"
                                   "(名簿にない/担当ラインが機側ではない)"), 422
        values[config.KEY_WORKER] = worker

    if "auto_import" in payload:
        values[config.KEY_AUTO_IMPORT] = bool(payload["auto_import"])

    if not values:
        return jsonify(ok=False, reason="nothing_to_save",
                       message="保存するものがありません"), 400

    if not user_settings.save_many(values):
        return jsonify(ok=False, reason="write_failed",
                       message=f"設定ファイルに書けません: {config.USER_CONFIG_PATH}"), 500

    return jsonify(ok=True, view=_view(get_db()))


@bp.post("/api/settings/reset")
def reset_field():
    """1つの欄を既定へ戻す。"""
    payload = request.get_json(silent=True) or {}
    key = str(payload.get("key", ""))
    if key not in {f["key"] for f in FIELDS}:
        return jsonify(ok=False, reason="unknown_key",
                       message="知らない設定です"), 400
    # 既定へ戻すのも**置き場所を変えること**。同じ関門を通す
    if not _path_gate([key]):
        return jsonify(ok=False, reason=REFUSE_NEEDS_PASSWORD,
                       message=NEEDS_PASSWORD_MESSAGE), 403
    user_settings.clear(key)
    return jsonify(ok=True, view=_view(get_db()))


# ==================================================================
# 取り込み
# ==================================================================
@bp.post("/api/settings/import")
def run_import():
    """共有フォルダから手元へ写す。

    **1つ失敗しても残りは続ける。** 仕掛台帳に届かない端末でも、
    マスタさえ読めれば包装仕様の閲覧はできる。
    """
    conn = get_db()
    result = data_sync.import_all(conn)
    return jsonify(
        ok=result.ok,
        total=result.total_rows,
        tables=[{
            "table": t.table, "ok": t.ok, "rows": t.rows,
            "source": t.source, "error": t.error, "filled": t.filled,
            # 鍵が重なっていて飛ばした行。**黙って捨てない**
            "duplicates": t.duplicates,
        } for t in result.tables],
        view=_view(conn),
    )


# ==================================================================
# 配布設定(`coil_tool/distribution.py`)
# ==================================================================
# 書き出す・読み込み直す・消すは、どれも**置き場所を変えるのと同じ関門**
# (設定のいちばん上の認証)を通す。書き出すものには置き場所と
# 管理者パスワードが入り、読み込み直すとこの端末の置き場所が変わるため
DIST_NEEDS_PASSWORD_MESSAGE = (
    "配布設定を扱うにはパスワードが要ります。"
    "設定のいちばん上でパスワードを入れてください。")


def _distribution_reply(result):
    if not result.ok:
        status = 500 if result.reason == distribution.REFUSE_FAILED else 400
        return jsonify(ok=False, reason=result.reason or "bad_input",
                       message=result.message), status
    return jsonify(ok=True, message=result.message, view=_view(get_db()))


def _distribution_gate():
    """認証が開いていなければ断りの応答、開いていれば None。"""
    if admin_session.is_open():
        return None
    return jsonify(ok=False, reason=REFUSE_NEEDS_PASSWORD,
                   message=DIST_NEEDS_PASSWORD_MESSAGE), 403


@bp.post("/api/settings/distribution/export")
def export_distribution():
    """この端末のいまの設定を、配布設定として書き出す。"""
    refused = _distribution_gate()
    if refused:
        return refused
    items = (request.get_json(silent=True) or {}).get("items")
    if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
        return jsonify(ok=False, reason="bad_input",
                       message="入れる項目の形が違います。"), 400
    return _distribution_reply(distribution.export(items))


@bp.post("/api/settings/distribution/reapply")
def reapply_distribution():
    """置いてある配布設定を読み込み直す(この端末の値も上書き)。"""
    refused = _distribution_gate()
    if refused:
        return refused
    return _distribution_reply(distribution.reapply())


@bp.post("/api/settings/distribution/remove")
def remove_distribution():
    refused = _distribution_gate()
    if refused:
        return refused
    return _distribution_reply(distribution.remove())
