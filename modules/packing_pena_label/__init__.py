"""ペナラベル(梱包ペナ ラベル・風袋計算)── 統合アプリから見た入口

統合アプリ(`app.py` / `start_app.py`)が機能を扱うときの約束は
梱包明細と同じ(`modules/packing_details/__init__.py`)。

移植元は Flask ではなく標準ライブラリの `http.server` で動いていた。
Flask への取り次ぎは `server.py`(この機能の中)にあり、画面と業務
(`app/routes` / `app/services`)は移植元のまま。
"""
from __future__ import annotations

import threading
from typing import Optional

KEY = "pena"
PREFIX = "/pena"
LABEL = "ペナラベル"
HOME = f"{PREFIX}/"

#: 取り込んだときの移植元と版(記録。**この機能の版とは別**。common/versions.py)
PORTED_FROM = {"repo": "ViVio-Hellon/packing-pena-label-python-web", "version": "1.5.0"}


def display_name() -> str:
    from .app.config import load_config
    return load_config().app_name


def version() -> str:
    from .app.config import load_config
    return load_config().version


def register(app):
    from . import server
    return server.register(app, PREFIX)


def set_shutdown_hook(func) -> None:
    from . import server
    ctx = server.context()
    if ctx is not None:
        ctx.shutdown_hook = func


def close() -> None:
    """止めるときの片付け ── 状態DB を閉じる(移植元 `serve()` の `finally`)。"""
    from . import server
    ctx = server.context()
    if ctx is not None:
        ctx.close()


def busy() -> bool:
    """入力・計算・帳票だけ(監視レベル1)。途中で止めると困る処理は無い。"""
    return False


def initialize(report) -> Optional[threading.Event]:
    """重い初期化 ── 資材マスタの初回読込。

    共有に届かない端末では Access の応答待ちに数十秒かかることがあるので、
    起動待機画面が出たあとでやる(移植元は待機画面より前にやっていた)。
    読めなくても CSV(検証用)へ退避して続けるので、起動は止めない。
    """
    from . import server

    ctx = server.context()
    if ctx is None:
        return None
    report.stage(f"{LABEL}: 資材マスタを読み込み中")
    ctx.load_materials()
    return None
