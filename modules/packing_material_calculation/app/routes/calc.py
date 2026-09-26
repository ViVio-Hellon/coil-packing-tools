"""資材計算 ── VBA `UF_Material` の1ページ目

    LotNo(7桁) → 受注番号の候補 → 検入数・外径 → 計算Start → 結果

【断り方】
    400 … 入力の形が違う。**サーバの状態は動いていない**
    422 … 形は正しいが業務として断る(引当データなし・積数0 など)

どちらも `reason` 定数で運ぶ。文言から推し量らない。
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from modules.packing_material_calculation.coil_tool import (burr_split, calc_service, coil_weight, config,
                       lot_service, ripla_service, user_settings, vba)
from modules.packing_material_calculation.coil_tool.logging_utils import get_logger
from modules.packing_material_calculation.coil_tool.models import CalcState, OrderInfo

from .. import base, conf, get_db, session, shell

log = get_logger("app.routes.calc")

bp = Blueprint("calc", __name__)


# ==================================================================
# ビューモデル ── 更新後の一式を返す(差分ではない)
# ==================================================================
def view_of(state: CalcState, *, shape: ripla_service.ShapeView = None) -> dict[str, Any]:
    """画面に出すものを全部組む。

    **フラグも一緒に返す。** 「どの規則で決まったか」は現場が見て判断に
    使っているもので、色だけの飾りではない。
    """
    order = state.order
    shape = shape or ripla_service.shape_view(
        state.パレット種類, int(vba.val(state.最下部本数)))

    return {
        "input": {
            "LOT": state.LOT,
            "検入数": state.検入数,
            "外径": state.外径,
            "積数": state.積数,
        },
        "order": {
            "受注番号": order.受注番号,
            "受注材質": order.受注材質,
            "受注調質": order.受注調質,
            "受注板厚": order.受注板厚,
            "受注板幅": order.受注板幅,
            "用途コード": order.用途コード,
            "用途名": order.用途名,
            "包装仕様NO": order.包装仕様NO,
            "納入先名称": order.納入先名称,
            "取引先名称": order.取引先名称,
            "製品単重": order.製品単重,
            "梱包単位_重量": order.梱包単位_重量,
            "梱包単位_枚数": order.梱包単位_枚数,
            "コイル外径_MAX": order.コイル外径_MAX,
            "コイル外径_目標": order.コイル外径_目標,
            "コイル外径_MIN": order.コイル外径_MIN,
            "コイル内径_目標": order.コイル内径_目標,
            "工場用コメント": order.工場用コメント,
            "営業納期": order.営業納期,
            "比重": order.比重,
            "梱包コード": order.梱包コード,
        },
        "result": {
            "パレット種類": state.パレット種類,
            "パレット名称": state.パレット名称,
            "パレットサイズ": state.パレットサイズ,
            "積数": state.積数,
            "台数": state.台数,
            "Re_積数": state.Re_積数,
            "Re_台数": state.Re_台数,
            "総高さ": state.総高さ,
            "コイル間": state.コイル間,
            "リプラサイズ": state.リプラサイズ,
            "リプラサイズ表示": ripla_service.ripla_size_label(state.リプラサイズ),
            "最下部本数": state.最下部本数,
            "最下部長さ": state.最下部長さ,
            "最下部長さ_短": state.最下部長さ_短,
            "長い本数": state.長い本数,
            "短い本数": state.短い本数,
            "リプラ長さ": state.リプラ長さ,
            "本数": state.本数,
            "緩衝材": state.緩衝材,
            "HB枚数": state.HB枚数,
            "間リプラ種類": state.間リプラ種類,
            "最下部リプラ種類": state.最下部リプラ種類,
            "IMI中間長さ": state.IMI中間長さ,
            "IMI中間本数": state.IMI中間本数,
            "TotalC": state.TotalC,
            "特殊Flag": state.特殊Flag,
            "PFlag": state.PFlag,
            "単重再計算": state.単重再計算,
        },
        "flags": sorted(state.flags),
        "messages": list(state.messages),
        "shape": {"kind": shape.kind, "bars": list(shape.bars)},
        "worker": user_settings.get_worker(),
    }


def _ok(state: CalcState, **extra) -> Any:
    payload = {"ok": True, "view": view_of(state, shape=extra.pop("shape", None))}
    payload.update(extra)
    return jsonify(payload)


# ==================================================================
# 画面
# ==================================================================
@bp.get("/calc")
def page():
    conn = get_db()
    return render_template(
        "calc.html",
        page=shell.page("calc"),
        pages=shell.PAGES,
        ribbon=shell.ribbon(conn),
        token=conf()["TOKEN"],
        url_hoso=config.URL_HOSO_SHIYOSHO,
    )


@bp.get("/api/calc")
def current():
    return _ok(session.get())


@bp.post("/api/calc/clear")
def clear():
    """はじめから (VBA `フォームクリア`)。"""
    return _ok(session.reset())


# ==================================================================
# ロットと受注
# ==================================================================
@bp.post("/api/calc/lot")
def find_lot():
    """LotNo から受注番号の候補を引く (VBA `LOT検索`)。

    VBA は7桁打ち終わった時点で自動で走らせていた。画面も同じにして
    あるが、**サーバ側は桁数で断らない** ── 打ち途中で呼ばれても
    候補が空で返るだけで、害が無い。
    """
    payload = request.get_json(silent=True) or {}
    lot_no = lot_service.normalize_lot_no(str(payload.get("LOT", "")))

    conn = get_db()
    state = session.get()
    # LotNo を打ち直したら、前の結果は全部消す(VBA `フォームクリア`)
    state.form_clear()
    state.LOT = lot_no

    candidates = lot_service.find_order_numbers(conn, lot_no)
    if not candidates:
        return jsonify(ok=True, view=view_of(state), candidates=[],
                       message="そのロットに紐づく受注がありません"
                               if lot_no else "")
    return jsonify(ok=True, view=view_of(state), candidates=candidates)


@bp.post("/api/calc/order")
def load_order():
    """受注番号から情報を展開する (VBA `フォーム展開`)。"""
    payload = request.get_json(silent=True) or {}
    order_no = str(payload.get("受注番号", "")).strip()
    if not order_no:
        return jsonify(ok=False, reason="no_order_no",
                       message="受注番号を選んでください"), 400

    conn = get_db()
    state = session.get()
    state.soft_clear()
    if not lot_service.load_into(conn, state, order_no):
        return jsonify(ok=False, reason="order_not_found",
                       message=f"受注番号 {order_no} が仕掛受注にありません"), 422
    return _ok(state)


# ==================================================================
# 入力
# ==================================================================
@bp.post("/api/calc/input")
def set_input():
    """検入数・外径・積数を受ける。

    VBA は入力欄の `Change` / `BeforeUpdate` で書式を当てていた
    (検入数は整数、外径は小数2桁)。同じ整形をここでする ──
    **画面側ではしない**(業務判断は Python 側、の約束)。
    """
    payload = request.get_json(silent=True) or {}
    state = session.get()

    if "検入数" in payload:
        raw = str(payload["検入数"]).strip()
        state.検入数 = vba.int_text(raw) if vba.is_numeric(raw) else ""

    if "外径" in payload:
        raw = str(payload["外径"]).strip()
        state.外径 = vba.fmt(raw, 2) if vba.is_numeric(raw) else ""

    if "積数" in payload:
        raw = str(payload["積数"]).strip()
        state.積数 = vba.int_text(raw) if vba.is_numeric(raw) else ""

    return _ok(state)


# ==================================================================
# 計算
# ==================================================================
@bp.post("/api/calc/run")
def run():
    """計算Start (VBA `CommandButton5_Click`)。"""
    conn = get_db()
    state = session.get()
    result = calc_service.calculate(conn, state)
    if not result.ok:
        return jsonify(ok=False, reason=result.reason,
                       view=view_of(state, shape=result.shape),
                       message=state.messages[-1] if state.messages else ""), 422
    return _ok(state, shape=result.shape, total_ripla=result.total_ripla)


@bp.post("/api/calc/restack")
def restack():
    """積数を手で直したときの再計算 (VBA `積数_AfterUpdate`)。

    **`台数計算` は呼ばない。** 手で入れた積数を規則で上書きしない。
    """
    conn = get_db()
    state = session.get()
    result = calc_service.recalculate_from_stack(conn, state)
    if not result.ok:
        return jsonify(ok=False, reason=result.reason,
                       view=view_of(state, shape=result.shape),
                       message=state.messages[-1] if state.messages else ""), 422
    return _ok(state, shape=result.shape, total_ripla=result.total_ripla)


# ==================================================================
# 単重再計算 (VBA `単重再計算_Click`)
# ==================================================================
@bp.post("/api/calc/weight")
def recalc_weight():
    """外径・内径・幅・比重からコイル1本の重量を出し直す。

    引当の製品単重が当てにならないときに使う。**製品単重そのものは
    書き換えない** ── VBA も別の欄(`単重再計算`)に出すだけだった。
    """
    state = session.get()
    order = state.order

    outer = vba.val(state.外径)
    inner = vba.val(order.コイル内径_目標)
    width = vba.val(order.受注板幅)
    gravity = vba.val(order.比重)

    missing = []
    if not (vba.is_numeric(order.コイル内径_目標) and inner > 0):
        missing.append("内径")
    if not (vba.is_numeric(state.外径) and outer > 0):
        missing.append("外径")
    if not (vba.is_numeric(order.受注板幅) and width > 0):
        missing.append("幅")
    if not (vba.is_numeric(order.比重) and gravity > 0):
        missing.append("比重")
    if missing:
        return jsonify(ok=False, reason="missing_values",
                       view=view_of(state),
                       message=f"指定項目に入力がありません({'、'.join(missing)})"), 422

    state.単重再計算 = coil_weight.coil_weight_text(outer, inner, width, gravity)
    return _ok(state)


# ==================================================================
# バリ揃え梱包 (VBA `UFalmo`)
# ==================================================================
@bp.post("/api/calc/burr")
def burr():
    """上バリ・下バリに振り分けて台数を出す。**本体はまだ書き換えない。**"""
    payload = request.get_json(silent=True) or {}
    conn = get_db()
    state = session.get()

    upper = int(vba.val(payload.get("上本数", 0)))
    lower = int(vba.val(payload.get("下本数", 0)))

    result = burr_split.calculate(conn, state, upper=upper, lower=lower)
    if not result.ok:
        return jsonify(ok=False, reason=result.reason, view=view_of(state),
                       message="上下の台数を出せません(単重・検入数・包装仕様を確認してください)"), 422

    return jsonify(ok=True, view=view_of(state), burr={
        "上本数": result.上本数, "下本数": result.下本数,
        "残り検入数": result.残り検入数, "積数": result.積数,
        "上台数": result.上台数, "下台数": result.下台数, "台数": result.台数,
    })


@bp.post("/api/calc/burr/transfer")
def burr_transfer():
    """結果転送 ── 出した積数と台数を本体へ送り、リプラを計算し直す。"""
    payload = request.get_json(silent=True) or {}
    conn = get_db()
    state = session.get()

    upper = int(vba.val(payload.get("上本数", 0)))
    lower = int(vba.val(payload.get("下本数", 0)))
    result = burr_split.calculate(conn, state, upper=upper, lower=lower)
    if not result.ok:
        return jsonify(ok=False, reason=result.reason, view=view_of(state),
                       message="上下の台数を出せません"), 422

    burr_split.transfer(state, result)
    total = ripla_service.calculate(conn, state)
    return _ok(state, total_ripla=total)
