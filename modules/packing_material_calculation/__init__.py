"""資材計算(コイル梱包資材計算ツール)── 統合アプリから見た入口

統合アプリ(`app.py` / `start_app.py`)が機能を扱うときの約束は
梱包明細と同じ(`modules/packing_details/__init__.py`)。

`initialize` は移植元の `start_app._initialize` をそのまま持ってきたもの。
**順番が要点** ── 旧い置き場所からの移し替え → 配布設定 → 取り込み。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

KEY = "material"
PREFIX = "/material"
LABEL = "資材計算"
HOME = f"{PREFIX}/calc"

# 取り込みを待つ上限(秒)。共有に届かない端末では、応答が返るまで
# OS 側で数十秒かかることがある。**待たせきりにはしない** ── ここを
# 過ぎたら準備完了にして、取り込みは背景で続ける
IMPORT_WAIT_LIMIT_SEC = 120


def display_name() -> str:
    from .coil_tool import app_config
    return app_config.display_name()


def version() -> str:
    from .coil_tool import app_config
    return app_config.version()


def register(app):
    from .app import register as _register
    return _register(app, PREFIX)


def set_shutdown_hook(func) -> None:
    from .app.routes import health
    health.set_shutdown_hook(func)


def busy() -> bool:
    """取り込み・書き戻しが走っている間は、途中で止めると DB が中途半端に残る。"""
    from .coil_tool import jobs
    return bool(jobs.get_registry().busy_labels())


def busy_labels() -> list[str]:
    from .coil_tool import jobs
    return list(jobs.get_registry().busy_labels())


def initialize(report) -> Optional[threading.Event]:
    """重い初期化。サーバが立ってから行う。

    ここで失敗しても**サーバは落とさない**。画面に理由を出す。
    背景で取り込みを始めたら、それが終わった(または上限を過ぎた)ときに
    立つ `Event` を返す。始めなければ `None`。
    """
    from .coil_tool import config, db
    from .coil_tool.logging_utils import get_logger

    log = get_logger("launcher")

    try:
        report.stage(f"{LABEL}: アプリを準備中")
        config.ensure_dirs()
        # 旧い置き場所(アプリ本体の隣)に残っていれば、利用者ごとの
        # 領域へ写す。**DBを開く前に**やる ── 先に開くと空のDBが
        # できてしまい、「新しい側が無いとき」の条件に当たらなくなる
        for moved in config.migrate_local():
            log.info("%s", moved)
        # 配布設定があれば、**取り込みより先に**読む ── 置き場所が入っている
        # ので、読む前に取り込むと既定の場所を見る。移し替えのあとに読む
        from .coil_tool import distribution
        loaded = distribution.apply_on_start()
        if loaded.applied:
            log.info("配布設定を読み込みました: %s", ", ".join(loaded.applied))
        with db.connect() as conn:
            db.apply_schema(conn)
    except Exception as exc:                      # noqa: BLE001 - 画面に出して継続
        log.exception("初期化に失敗しました")
        report.error(f"{LABEL}: 初期化に失敗しました: {exc}")
        return None

    # 取り込み元の自己診断。**結果はログに残す。**
    _diagnose_sources(log)

    return _start_auto_import(report, log)


def _diagnose_sources(log) -> None:
    """取り込み元を1つずつ開いてみて、**結果をログに残す**。"""
    from .coil_tool import config, data_sync, source_db

    try:
        found: dict[str, object] = {}
        material = data_sync.find_master_db()
        if material is not None:
            found[config.MATERIAL_DB_NAME] = material
        found.update(data_sync.find_lot_dbs())
        if not found:
            log.warning("取り込み元が1つも見つかりません: マスタ=%s 台帳=%s",
                        config.master_db_dir(), config.lot_db_dir())
            return
        for name, path in found.items():
            probe = source_db.probe(path)
            if probe.ok:
                log.info("取り込み元 %s: 開けました(%s / %s表 / %s / %s バイト%s)",
                         name, probe.opened_by, len(probe.tables),
                         probe.journal, f"{probe.size:,}",
                         " / 付き添い " + ",".join(probe.sidecars)
                         if probe.sidecars else "")
            else:
                log.warning("取り込み元 %s: 開けません(%s バイト / 印=%s%s): %s",
                            name, f"{probe.size:,}",
                            "あり" if probe.is_sqlite else "なし",
                            " / 付き添い " + ",".join(probe.sidecars)
                            if probe.sidecars else "",
                            probe.error)
    except Exception as exc:                      # noqa: BLE001 - 診断で止めない
        log.warning("取り込み元の診断に失敗しました: %s", exc)


def _start_auto_import(report, log) -> Optional[threading.Event]:
    """起動時の自動取り込み(更新されたファイルだけ)を始める。

    移植元は進捗を持つ「ジョブ」の仕組み(`coil_tool/jobs.py`)に載せている。
    統合版でもそのまま ── 走っている間は `busy()` が真になり、停止・自動終了が
    待つ。
    """
    from .coil_tool import config, data_sync, db, jobs, user_settings

    if not user_settings.get(config.KEY_AUTO_IMPORT, config.AUTO_IMPORT_DEFAULT):
        log.info("起動時の自動取り込みは設定でOFFになっています")
        return None

    def work(progress):
        # 作業スレッドで開き直す(`sqlite3` の接続はスレッドをまたげない)
        with db.connect() as conn:
            db.apply_schema(conn)
            return data_sync.auto_import(conn, progress=progress)

    try:
        job = jobs.get_registry().start("import", "起動時の自動取り込み", work)
    except jobs.JobBusy:
        log.warning("すでに処理が走っているため自動取り込みは見送りました")
        return None

    done = threading.Event()

    def watch() -> None:
        """取り込みの終わりを見る。**打ち切りを持つ。**"""
        waited = 0.0
        while job.is_running and waited < IMPORT_WAIT_LIMIT_SEC:
            time.sleep(0.3)
            waited += 0.3
        if job.is_running:
            log.warning("取り込みが %s秒 で終わらないので、先に画面を開きます",
                        IMPORT_WAIT_LIMIT_SEC)
        done.set()

    report.stage(f"{LABEL}: 取り込み中", "import")
    threading.Thread(target=watch, name="material-ready-watch", daemon=True).start()
    return done
