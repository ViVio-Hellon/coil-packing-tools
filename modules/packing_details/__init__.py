"""梱包明細(コイル梱包明細打ち出しシステム)── 統合アプリから見た入口

統合アプリ(`app.py` / `start_app.py`)が機能を扱うときの約束はこの5つだけ。

    KEY / PREFIX / LABEL / HOME      名前・入口・タブの見出し・最初に開く画面
    register(app)                    Flask に載せる(`app/__init__.py` へ取り次ぐ)
    initialize(report)               サーバが立ったあとの重い初期化
    busy()                           途中で止めると困る処理が走っているか
    set_shutdown_hook(func)          `/api/shutdown` の止め方を差し込む

`initialize` は移植元の `start_app._initialize` をそのまま持ってきたもの。
**順番が要点** ── 配布設定は取り込みより先に読む(置き場所が配布設定で
決まる端末で、既定の場所を取り込みに行かない)。
"""
from __future__ import annotations

import threading
from typing import Optional

KEY = "details"
PREFIX = "/details"
LABEL = "梱包明細"
HOME = f"{PREFIX}/meisai"

# 取り込みを待つ上限(秒)。共有に届かない端末では、応答が返るまで
# OS 側で数十秒かかることがある。**待たせきりにはしない**
IMPORT_WAIT_LIMIT_SEC = 120


def display_name() -> str:
    from .meisai import app_config
    return app_config.display_name()


def version() -> str:
    from .meisai import app_config
    return app_config.version()


def register(app):
    from .app import register as _register
    return _register(app, PREFIX)


def set_shutdown_hook(func) -> None:
    from .app.routes import health
    health.set_shutdown_hook(func)


def busy() -> bool:
    """**このアプリに「途中で止めると困る処理」は無い**(監視レベル1)。
    作業の途中経過は操作のたびにDBへ書いてあるので、いつ落ちても
    開き直せば同じところから続けられる。"""
    return False


def on_wake(gap_sec: float) -> None:
    """スリープから戻ったら、画面の空きの猶予も数え直す。"""
    from .meisai import screen
    screen.note_wake(gap_sec)


def initialize(report) -> Optional[threading.Event]:
    """重い初期化。サーバが立ってから行う。

    ここで失敗しても**サーバは落とさない**。落とすと利用者のブラウザには
    「接続できません」としか出ず、理由が伝わらない。画面に理由を出す。

    背景で取り込みを始めたら、それが終わったときに立つ `Event` を返す。
    始めなければ `None`(呼び手はその場で準備完了にしてよい)。
    """
    from .meisai import config, db
    from .meisai.logging_utils import get_logger

    log = get_logger("launcher")

    try:
        report.stage(f"{LABEL}: アプリを準備中")
        config.ensure_dirs()
        with db.connect() as conn:
            db.apply_schema(conn)
    except Exception as exc:                      # noqa: BLE001 - 画面に出して継続
        log.exception("初期化に失敗しました")
        report.error(f"{LABEL}: 初期化に失敗しました: {exc}")
        return None

    # **配布設定を、取り込みより先に読む。** 置き場所が配布設定で決まる端末では、
    # 読む前に取り込むと既定の置き場所を見に行ってしまう。読むのはその端末に
    # まだ無い項目だけ(`meisai/distribution.py`)。失敗しても起動は止めない
    try:
        from .meisai import distribution
        loaded = distribution.apply_on_start()
        if loaded.applied:
            log.info("配布設定を読み込みました: %s", ", ".join(loaded.applied))
    except Exception as exc:                      # noqa: BLE001 - 起動を止めない
        log.warning("配布設定を読み込めませんでした: %s", exc)

    # 明細の履歴を共有へ送り続ける(出力したらすぐ、届かなければ5分ごとに)
    from .meisai import slip_history
    slip_history.start_background()

    # 取り込み元の自己診断。**結果はログに残す。**
    _diagnose_sources(log)

    return _start_auto_import(report, log)


def _diagnose_sources(log) -> None:
    """取り込み元を1つずつ開いてみて、**結果をログに残す**。

    「マスタが読めません」の問い合わせが来たとき、これがあれば
    ログを見るだけで原因が分かる。**診断で起動を止めない。**
    """
    from .meisai import config, data_sync, source_db

    try:
        found = data_sync.find_sources()
        if not found:
            log.warning("取り込み元が1つも見つかりません: 台帳=%s 梱包課共有=%s",
                        config.lot_db_dir(), config.konpo_db_dir())
            return
        for table, path in found.items():
            probe = source_db.probe(path)
            if probe.ok:
                log.info("取り込み元 %s (%s): 開けました(%s / %s表 / %s / %s バイト%s)",
                         table, path.name, probe.opened_by, len(probe.tables),
                         probe.journal, f"{probe.size:,}",
                         " / 付き添い " + ",".join(probe.sidecars)
                         if probe.sidecars else "")
            else:
                log.warning("取り込み元 %s (%s): 開けません(%s バイト / 印=%s%s): %s",
                            table, path.name, f"{probe.size:,}",
                            "あり" if probe.is_sqlite else "なし",
                            " / 付き添い " + ",".join(probe.sidecars)
                            if probe.sidecars else "",
                            probe.error)
        for table, filename in data_sync.missing_sources(found).items():
            log.warning("取り込み元 %s (%s) が見つかりません", table, filename)
    except Exception as exc:                      # noqa: BLE001 - 診断で止めない
        log.warning("取り込み元の診断に失敗しました: %s", exc)


def _start_auto_import(report, log) -> Optional[threading.Event]:
    """起動時の自動取り込み(更新されたファイルだけ)を始める。

    設定でOFFなら何もしない。ここで例外を出してもサーバは落とさない
    ── 取り込めないことと、アプリが使えないことは別。
    """
    from .meisai import data_sync, db, user_settings

    if not user_settings.auto_import_enabled():
        log.info("起動時の自動取り込みは設定でOFFになっています")
        return None

    done = threading.Event()

    def work() -> None:
        try:
            # 作業スレッドで開き直す(`sqlite3` の接続はスレッドをまたげない)
            with db.connect() as conn:
                db.apply_schema(conn)
                result = data_sync.import_all(conn)
            log.info("起動時の自動取り込み: %s", result.summary())
            for note in result.errors:
                log.warning("取り込み: %s", note)
        except Exception as exc:              # noqa: BLE001 - 起動を止めない
            log.warning("起動時の自動取り込みに失敗しました: %s", exc)
        finally:
            # **必ず終わったことにする。** ここを通さないと、取り込みに
            # 失敗した端末が起動待機画面から一生進めない
            done.set()

    report.stage(f"{LABEL}: 取り込み中", "import")
    threading.Thread(target=work, name="details-auto-import", daemon=True).start()
    return done
