"""起動確認・生存監視・停止 (基盤仕様書 2.3 / 2.8 / 2.9)

- `GET  /`             … 準備中なら起動待機画面、終わっていれば資材計算へ
- `GET  /api/health`   … 起動確認と生存監視。**トークン不要**
- `POST /api/alive`    … 画面の心拍。**トークン不要**
- `POST /api/shutdown` … 安全な停止。トークン必須
"""
from __future__ import annotations

import threading
import time

from flask import Blueprint, current_app, jsonify, redirect, request

from modules.packing_material_calculation.coil_tool import app_config, boot_screen, idle_exit
from modules.packing_material_calculation.coil_tool.logging_utils import get_logger

from .. import base, conf, screen

log = get_logger("app.routes.health")

bp = Blueprint("health", __name__)

# 停止要求を受けてから実際に落とすまでの猶予(秒)。
# 応答を返しきる前に止めると「押したのに何も起きなかった」に見える
SHUTDOWN_DELAY_SEC = 0.4

_shutdown_hook = None


def set_shutdown_hook(func) -> None:
    """サーバの止め方を登録する(`server.py` が差し込む)。"""
    global _shutdown_hook
    _shutdown_hook = func


@bp.get("/")
def index():
    """準備が終わるまでは起動待機画面を出す。

    **骨格は `boot_screen` に1つだけ。** 起動サーバ(Flask を読み込む前に
    出すほう)と同じものを出す ── 2枚持つと、直したほうと直していない
    ほうが場面によって出る。
    """
    if not conf()["READY"]:
        return boot_screen.render(
            display_name=conf()["DISPLAY_NAME"],
            version_label=app_config.version_label(),
            token=conf()["TOKEN"],
            app_id=conf()["APP_ID"],
            poll_ms=app_config.job_poll_ms(),
            home_url=f"{base()}/calc",
        )
    return redirect(f"{base()}/calc?t={conf()['TOKEN']}")


@bp.get("/api/health")
def health():
    """起動確認。**アプリ識別情報も返す**(基盤仕様書 2.3)。

    同じポートを別のアプリが使っている場合でも、誤って
    「起動成功」と判断しないようにするため。
    """
    c = conf()
    return jsonify(
        ok=True,
        app_id=c["APP_ID"],
        display_name=c["DISPLAY_NAME"],
        version=c["VERSION"],
        mode=c["MODE"],
        ready=c["READY"],
        stage=c["STAGE"],
        stage_key=c["STAGE_KEY"],
        error=c["STARTUP_ERROR"],
        uptime=round(time.time() - c["STARTED_AT"], 1),
    )


@bp.post("/api/alive")
def alive():
    """画面の心拍 (基盤仕様書 2.9)。

    業務データは含めないのでトークンを要求しない(要求すると、切れた
    画面が黙って死んだ扱いになる)。

    **ここが「まだ自分のものか」を返す唯一の場所。** 画面が取り上げ
    られたことは、次の心拍で本人に伝わる(`own=False`)。心拍に相乗り
    させるのは、**別に問い合わせを増やすと、片方だけ届いたときに
    「生きているが自分のものか分からない」が生まれる**ため。
    """
    body = request.get_json(silent=True) or {}
    closing = bool(body.get("closing"))
    name = str(body.get("screen", ""))
    # 画面が裏に回っているか。言ってこなければ(古い画面・試験)前のまま。
    # **裏の間は心拍が途切れても生きている扱い**(ブラウザが間引くため)
    hidden = bool(body["hidden"]) if "hidden" in body else None

    watch = idle_exit.get()
    if watch is not None:
        # 閉じた合図は猶予つき。**再読込でも `pagehide` は飛ぶ**ので、
        # 戻ってくれば次の心拍で取り消される
        watch.leaving() if closing else watch.beat(hidden=hidden)

    if closing:
        # 閉じたなら符牒を返す。**自分が持っているときだけ**手放すので、
        # 取り上げられた古い画面が閉じても、新しい画面は巻き添えにならない
        screen.release(name)
        return jsonify(ok=True, own=False)

    # 符牒を名乗らない相手(古い画面・試験・`--no-browser`)は素通し。
    # 名乗らないものを締め出すと、心拍が止まってアプリが終わる
    own = screen.beat(name, hidden=hidden) if name else True
    return jsonify(ok=True, own=own)


@bp.post("/api/shutdown")
def shutdown():
    """安全な停止 (基盤仕様書 2.8)。"""
    log.info("停止要求を受け取りました")
    if _shutdown_hook is None:
        return jsonify(ok=False, reason="no_hook",
                       message="停止の仕方が登録されていません"), 500

    def _later() -> None:
        time.sleep(SHUTDOWN_DELAY_SEC)
        _shutdown_hook()

    threading.Thread(target=_later, daemon=True).start()
    return jsonify(ok=True)
