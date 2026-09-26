"""起動確認・生存監視・停止 (基盤仕様書 2.3 / 2.8 / 2.9)

- `GET  /`             … 準備中なら起動待機画面、終わっていればアプリ本体へ
- `GET  /api/health`   … 起動確認と生存監視。**トークン不要**
- `POST /api/alive`    … 画面の心拍。**トークン不要**
- `POST /api/shutdown` … 安全な停止。トークン必須
"""
from __future__ import annotations

import os
import platform
import sys
import threading
import time

from flask import Blueprint, current_app, jsonify, redirect, request

# `config` は関数の中で `current_app.config` に使っているので、
# 別名で入れる
from modules.packing_details.meisai import app_config, screen
from modules.packing_details.meisai import config as app_paths
from modules.packing_details.meisai.logging_utils import get_logger

from .. import base, conf  # noqa: E402

log = get_logger("app.routes.health")

bp = Blueprint("health", __name__)

# 停止要求を受けてから実際に落とすまでの猶予(秒)。
# 応答を返しきる前にサーバを止めると、利用者側は「押したのに何も
# 起きなかった」ように見える
SHUTDOWN_DELAY_SEC = 0.4

# `POST /api/shutdown` が呼ばれたときに実行する後始末。
# `server.py` が起動時に差し込む(ここが直接 waitress を知らないようにする)
_shutdown_hook = None


def set_shutdown_hook(func) -> None:
    """サーバの止め方を登録する。"""
    global _shutdown_hook
    _shutdown_hook = func


@bp.get("/")
def index():
    """準備が終わるまでは起動待機画面を出す。

    基盤仕様書 2.3 は「ブラウザーだけが先に開き、接続エラーや未完成の
    画面が表示されることを防ぐ」ことを求めている。サーバ自身が待機画面を
    出すことで、**起動直後でも必ず何かが表示される**状態を作る。
    """
    if not conf()["READY"]:
        from modules.packing_details.meisai import boot_screen

        return boot_screen.render(
            display_name=conf()["DISPLAY_NAME"],
            version_label=app_config.version_label(),
            token=conf()["TOKEN"],
            app_id=conf()["APP_ID"],
            poll_ms=app_config.job_poll_ms(),
            home_url=f"{base()}/meisai",
        )
    # 準備ができたら業務画面へ送る。ここで止まる画面を出すと、
    # `Start.vbs` から起動した利用者がアプリに入れない
    return redirect(f"{base()}/meisai", code=302)


@bp.get("/api/health")
def health():
    """起動確認と生存監視。

    **`app_id` を返すのが要点。** 同じポートを別のアプリが使っていても、
    HTTPが返るだけでは「自分と同じアプリが起動している」とは言えない
    (基盤仕様書 2.3)。多重起動の判定はこの値の一致で行う。

    業務データは含めないので、トークン無しで答えてよい。
    """
    config = conf()
    return jsonify({
        "app_id": config["APP_ID"],
        "display_name": config["DISPLAY_NAME"],
        "version": config["VERSION"],
        # **どのフォルダの、どの版が動いているか。**
        # 「入れ替えたのに古いまま」を調べるとき、これが無いと
        # 端末に行って確かめることになる
        "app_root": str(app_config.APP_ROOT),
        # **どのPythonで動いているか。** 端末ごとに入っている版が
        # 違うことがあり、「あの端末だけ動かない」の原因になる
        "python": platform.python_version(),
        "python_exe": sys.executable,
        # 版の書き方が壊れていれば理由。正しければ空文字。
        # **起動は止めない**(版が読めなくても業務はできる)ので、
        # 気づける場所に出しておく必要がある
        "version_problem": app_config.version_problem(),
        # 手元DBが共有フォルダ/ネットワークドライブに置かれていないか。
        # SQLite はネットワーク越しだとロックが効かず壊れうる
        "db_path_problem": app_paths.db_path_problem(),
        "mode": config["MODE"],
        "port": config["PORT"],
        "pid": os.getpid(),
        "ready": bool(config["READY"]),
        "stage": config["STAGE"],
        "stage_key": config.get("STAGE_KEY", "prepare"),
        "startup_error": config["STARTUP_ERROR"],
        "uptime_sec": round(time.time() - config["STARTED_AT"], 1),
        # このアプリは監視レベル1(長時間処理なし)なので、走っている
        # ものは常に無い。**鍵は残す** ── 起動待機画面は移植元と
        # 同じものを使っていて、この鍵を読む
        "job": None,
    })


@bp.post("/api/alive")
def alive():
    """画面が生きていることの心拍 (基盤仕様書 2.8「自動終了」)。

    **トークンは要らない。** ここで返すのは「受け取った」だけで、
    業務データは1つも含まない。心拍にトークンを要求すると、
    トークンが切れた画面が黙って死んだ扱いになり、開いているのに
    終了してしまう。

    `leaving=true` はタブを閉じた合図(`sendBeacon`)。猶予のあとで
    終わるが、そのあいだに心拍が戻れば取り消される。

    `state` は画面が今どちらに居るか(`hidden` 裏 / `visible` 前)。
    **裏に回ったと言ってきたら、心拍が止まっても終わらない・空きに
    しない。** ブラウザは裏のタブのタイマーを間引く(Chrome は1分に1回、
    Edge のスリープタブは止める)ので、心拍が途切れるのは当たり前になる。
    `reason` は送った理由(`timer` / `hidden` / `foreground` / `sleep` 等)。
    ログに残して、何が起きていたかを後から追えるようにする。

    **画面の持ち主もここで扱う。** 閉じる合図は `sendBeacon` で必ず
    届くので、同じ便に乗せる ── 別に1本立てると、閉じる瞬間にどちらか
    が捨てられて「閉じたのに空かない」が起きる。
    `holds` は「まだこの画面が持ち主か」で、false なら画面側は操作を
    止める(別のタブに引き継がれている)。
    """
    from modules.packing_details.meisai import idle_exit

    body = request.get_json(silent=True) or {}
    screen_id = str(body.get("screen", ""))
    leaving = bool(body.get("leaving"))
    state = str(body.get("state", ""))
    hidden = {"hidden": True, "visible": False}.get(state)   # 名乗らなければ None
    reason = str(body.get("reason", ""))[:20]
    # 定期の心拍と窓に戻っただけ(focus)は多いので残さない
    if reason and reason not in ("timer", "focus"):
        log.info("心拍: %s (%s)", reason, state or "-")

    if screen_id:
        if leaving:
            screen.release(screen_id)
            holds = False
        else:
            holds = screen.beat(screen_id, hidden=hidden)
    else:
        # 画面を名乗らない相手(起動待機画面・試験)は持ち主の話に
        # 関わらない。**持っている扱いにする** ── false を返すと、
        # 名乗らないだけの画面が締め出される
        holds = True

    # `pid` を返すのは、**前に戻った画面が「裏で開き直されていないか」を
    # 確かめるため。** 違っていたら、その画面のトークンはもう通らない
    out = {"ok": True, "holds": holds, "pid": os.getpid()}
    watch = idle_exit.get()
    if watch is None:
        return jsonify({**out, "watching": False})

    if leaving:
        watch.leaving()
    else:
        # 裏表は**持ち主の画面**が決める。引き継がれた古いタブが裏で
        # 何を言っても、終わる・終わらないの判断を揺らさない。
        #
        # **「裏に回った」は閉じた合図を取り消さない。** タブを閉じると
        # ブラウザは「裏に回った」と「閉じた」を両方送り、届く順は決まって
        # いない。後から来た「裏に回った」で取り消すと、閉じたのに終わらない
        watch.beat(hidden=hidden if holds else None,
                   keep_leaving=hidden is True)
    return jsonify({**out, "watching": True})


@bp.post("/api/shutdown")
def shutdown():
    """安全な停止 (基盤仕様書 2.8)。

    このアプリは長時間処理を持たない(監視レベル1)ので、中断の確認は
    要らない。**作業の途中経過はDBに書いてある**ので、いつ落としても
    次に開いたときに復元できる。
    """
    if _shutdown_hook is None:
        # サーバ抜きで組み立てた場合(試験など)
        return jsonify({"stopped": False, "reason": "no_hook",
                        "message": "このプロセスは停止操作に対応していません"}), 501

    log.info("停止要求を受け付けました")
    threading.Timer(SHUTDOWN_DELAY_SEC, _shutdown_hook).start()
    return jsonify({"stopped": True, "message": "終了します"})
