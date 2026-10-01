"""ログ出力 ── 3機能ぶんを**1つのファイル**に

移植元の梱包明細(`meisai/logging_utils.py`)と資材計算
(`coil_tool/logging_utils.py`)は、名前(`meisai_YYYYMMDD.log` /
`coil_tool_YYYYMMDD.log`)が違うだけで同じ作りだった。ペナラベルは
`RotatingFileHandler`(`app.log`)で、**日付で切らない**作りだった。

【統合版で採るもの】日付で切るほう(`DailyFileHandler`)。

- 「13日の不具合を追う」ときに13日のファイルを開けばよい。
  容量で切ると、どのファイルに何日が入っているか分からない
- 3機能を1つのプロセスで動かすので、**ファイルも1つ**にする。
  どの機能の行かは、ロガーの名前(`meisai.*` / `coil_tool.*` /
  `modules.packing_pena_label.*`)で分かる。3つに分けると、
  「梱包明細で出力した直後に資材計算が固まった」のような、
  機能をまたぐ経緯を突き合わせられない

【置き場所】既定は `%LOCALAPPDATA%\\CoilPackingTools\\logs\\`(このPC)。
**上の帯の「ログ」で出力先を変えられる**(統合 1.0.12。現場の指摘: エラーの後追いが
できない。ログ出力は設定でパス指定できるようにする)。変えた先には**PC の名前のフォルダ**を
作って書く ── 共有フォルダを指して全ラインのPCが同じファイルへ書くと、Windows の
ファイルの鍵で行が欠ける。出力先に書けない(共有が切れた等)ときは、黙って捨てずに
**このPCの既定の場所へ切り替えて**書き続け、そのことをログと画面に残す。
環境変数 `COIL_PACKING_TOOLS_LOG_DIR` があればそちらが勝つ(試験・CI 用)。

【後追いのための仕掛け】
- 1行ごとに、どの要求の中の行かを `[要求 R-xxxxxx]` で付ける(並んで来る要求の行が
  混ざっても、1つの操作の流れを拾える)
- 直近の行を手元に持っておく(`recent_lines`)。エラーの記録(`common/incidents.py`)が
  「エラーまでの流れ」として写す
- 古いログとエラーの記録は、残す日数(既定 180 日)を過ぎたら起動時に消す

【呼び方】各機能の `logging_utils` は、ここへの薄い取り次ぎ。
`get_logger(root, name)` でロガーを取る。`configure_logging()` は
何度呼んでも1度しか効かない(冪等)。
"""
from __future__ import annotations

import collections
import logging
import os
import re
import socket
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import app_config

# 3機能と統合アプリのロガーの根と、ふだん残す細かさ。**移植元と同じ量を残す**:
# 梱包明細・資材計算は DEBUG まで、ペナラベルは INFO まで(ペナラベルの移植元は
# `--diagnostic` で起動したときだけ DEBUG を残した。統合版も `start_app.py --diagnostic`)。
# ペナラベルは `logging.getLogger(__name__)` なので、パッケージ名が根になる
MODULE_LEVELS: dict[str, int] = {
    "meisai": logging.DEBUG,
    "coil_tool": logging.DEBUG,
    "modules.packing_pena_label": logging.INFO,
    "coil_packing_tools": logging.DEBUG,
}
MODULE_ROOTS: tuple[str, ...] = tuple(MODULE_LEVELS)

# ファイル名の前置き。**出どころはここ1つ**
FILE_PREFIX = "coil_packing_tools"

_configured = False
_diagnostic = False


def set_diagnostic(on: bool = True) -> None:
    """診断のために、3機能とも DEBUG まで残す(`start_app.py --diagnostic`)。"""
    global _diagnostic
    _diagnostic = on
    for name, level in MODULE_LEVELS.items():
        logging.getLogger(name).setLevel(logging.DEBUG if on else level)


ENV_LOG_DIR = "COIL_PACKING_TOOLS_LOG_DIR"

# いま書いている置き場所と、設定どおりに書けていない理由(画面に出す)
_where: dict = {"dir": None, "problem": "", "since": ""}
_where_lock = threading.RLock()


def pc_name() -> str:
    """このPCの名前(Windows の COMPUTERNAME)。共有の出力先でフォルダを分けるのに使う。"""
    name = os.environ.get("COMPUTERNAME") or socket.gethostname() or "PC"
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "PC"


def default_log_dir() -> Path:
    """このPCの既定の置き場所(`%LOCALAPPDATA%\\CoilPackingTools\\logs`)。"""
    return app_config.local_dir("logs")


def configured_log_dir() -> str:
    """設定に書かれた出力先(空なら既定)。"""
    from . import local_settings
    raw = local_settings.get(local_settings.KEY_LOG_DIR, "")
    return raw.strip() if isinstance(raw, str) else ""


def target_for(text: str) -> Path:
    """設定に書く値から、実際に書くフォルダ(その下の PC の名前のフォルダ)。

    相対で書くとアプリのフォルダからたどる(3機能の置き場所の設定と同じ決まり)。
    """
    base = Path(os.path.expandvars(text.strip()))
    if not base.is_absolute():
        base = Path(app_config.APP_ROOT) / base
    return base / pc_name()


def writable_problem(folder: Path) -> str:
    """そのフォルダに書けるか。書けなければ理由(作れなければ作れない理由)。"""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".write_test_{os.getpid()}_{threading.get_ident()}"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return f"{folder} に書けません({exc.strerror or exc})"
    return ""


def resolve_log_dir() -> tuple[Path, str]:
    """(書く場所, 設定どおりに書けない理由)。環境変数 > 設定 > このPCの既定。"""
    override = os.environ.get(ENV_LOG_DIR)
    if override and override.strip():
        return Path(override.strip()), ""
    configured = configured_log_dir()
    if configured:
        target = target_for(configured)
        problem = writable_problem(target)
        if not problem:
            return target, ""
        return default_log_dir(), problem + " ── このPCの既定の場所へ出しています"
    return default_log_dir(), ""


def log_dir() -> Path:
    """いまログを書いている場所。"""
    override = os.environ.get(ENV_LOG_DIR)
    if override and override.strip():
        return Path(override.strip())
    with _where_lock:
        if _where["dir"] is None:
            _where["dir"], _where["problem"] = resolve_log_dir()
            _where["since"] = time.strftime("%Y/%m/%d %H:%M:%S")
        return _where["dir"]


def log_problem() -> str:
    """設定どおりに書けていないとき、その理由。書けていれば空。"""
    log_dir()
    return _where["problem"]


def log_path_for(day: date) -> Path:
    """その日のログファイル。**名前の作り方はここ1か所**。"""
    return log_dir() / f"{FILE_PREFIX}_{day:%Y%m%d}.log"


class DailyFileHandler(logging.FileHandler):  # noqa: D101 - 下に説明
    """日付が変わったら、その日のファイルへ書き換える。

    **開いたままの端末のため。** 書き出し先を起動時に1度だけ決めると、
    夜勤をまたいだ端末は翌日ぶんを前日のファイルに書き続ける ── 13日の
    不具合を追うときに、13日のログが12日のファイルに入っている。

    `TimedRotatingFileHandler` を使わないのは、あちらが「いまのファイルを
    日付付きの名前へ退ける」作りで、こちらの名前の付け方
    (`<prefix>_YYYYMMDD.log` にそのまま書く)と噛み合わないため。
    日付を見て開き直すだけで足りる。
    """

    def __init__(self, day: date, **kwargs) -> None:
        self._day = day
        super().__init__(log_path_for(day), **kwargs)

    def emit(self, record: logging.LogRecord) -> None:
        # **書く直前に見る。** 日付が変わったこと・置き場所が変わったことは、次の1行で気づく
        today = date.today()
        wanted = os.path.abspath(str(log_path_for(today)))
        if today != self._day or wanted != self.baseFilename:
            self._day = today
            self.close()
            self.baseFilename = wanted
            self.stream = None                    # 次の emit で開き直す
        super().emit(record)

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - logging の名前
        """書けなかった(共有が切れた・権限が無い)。**黙って捨てない。**

        設定した出力先に書けなくなったら、このPCの既定の場所へ切り替えて、
        その行と「切り替えた」ことを書く。既定の場所でも書けなければ、
        logging の既定どおり(標準エラーへ。pythonw では見えない)。
        """
        here = Path(self.baseFilename).parent
        fallback = default_log_dir()
        if here == fallback or os.environ.get(ENV_LOG_DIR):
            super().handleError(record)
            return
        with _where_lock:
            _where["dir"] = fallback
            _where["problem"] = (f"{here} に書けなくなりました ── このPCの既定の場所へ"
                                 f"切り替えました({time.strftime('%H:%M:%S')})")
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            self.close()
            self.baseFilename = str(log_path_for(date.today()))
            self.stream = None
            note = logging.LogRecord("coil_packing_tools.log", logging.WARNING, __file__, 0,
                                     "ログの出力先に書けなくなりました: %s ── このPCの既定の"
                                     "場所へ切り替えました", (str(here),), None)
            note.ctx = ""
            logging.FileHandler.emit(self, note)
            logging.FileHandler.emit(self, record)
        except Exception:                          # noqa: BLE001 - 最後の手
            super().handleError(record)


class _ContextFilter(logging.Filter):
    """1行ごとに「どの要求の中の行か」を付ける(`[要求 R-xxxxxx]`)。

    並んで来る要求(心拍・別のタブ・取り込み)の行が混ざっても、1つの操作の流れを
    拾えるようにする。要求の外(起動・取り込みの裏の仕事)では何も付けない。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "ctx"):
            record.ctx = ""
            try:
                from flask import g, has_request_context
                if has_request_context():
                    rid = getattr(g, "request_id", "")
                    if rid:
                        record.ctx = f" [要求 {rid}]"
            except Exception:                      # noqa: BLE001 - 印が付かないだけ
                pass
        return True


class RecentHandler(logging.Handler):
    """直近の行を手元に持っておく(エラーの記録が「エラーまでの流れ」として写す)。"""

    def __init__(self, capacity: int = 600) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: collections.deque = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:                          # noqa: BLE001 - 持てないだけ
            pass


_recent: Optional[RecentHandler] = None
_file_handler: Optional[DailyFileHandler] = None

LINE_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s%(ctx)s"


def recent_lines(limit: int = 300) -> list[str]:
    """直近の行(古い順)。ログの設定より前なら空。"""
    if _recent is None:
        return []
    lines = list(_recent.lines)
    return lines[-limit:]


def apply_settings() -> dict:
    """設定を読み直して、書く場所を切り替える(画面で保存したとき)。**再起動は要らない。**"""
    old = log_dir()
    new, problem = resolve_log_dir()
    with _where_lock:
        _where["dir"], _where["problem"] = new, problem
        _where["since"] = time.strftime("%Y/%m/%d %H:%M:%S")
    log = logging.getLogger("coil_packing_tools.log")
    if new != old:
        # 前の場所にも「どこへ移ったか」を残す(後から追う人が迷わない)
        log.info("ログの出力先を変えます: %s → %s", old, new)
        if _file_handler is not None:
            with _file_handler.lock:
                _file_handler.close()
                _file_handler.baseFilename = str(log_path_for(date.today()))
                _file_handler.stream = None
        log.info("ログの出力先: %s(前は %s)", new, old)
    if problem:
        log.warning("ログの出力先: %s", problem)
    return status()


def status() -> dict:
    """画面に出す、いまのログの様子。"""
    from . import local_settings
    override = os.environ.get(ENV_LOG_DIR, "").strip()
    here = log_dir()
    return {
        "dir": str(here),
        "default_dir": str(default_log_dir()),
        "configured": configured_log_dir(),
        "problem": log_problem(),
        "env_override": override,
        "pc_name": pc_name(),
        "today_file": str(log_path_for(date.today())),
        "keep_days": local_settings.log_keep_days(),
        "keep_days_default": local_settings.LOG_KEEP_DAYS_DEFAULT,
        "keep_days_min": local_settings.LOG_KEEP_DAYS_MIN,
        "keep_days_max": local_settings.LOG_KEEP_DAYS_MAX,
    }


_LOG_NAME = re.compile(rf"^{FILE_PREFIX}_(\d{{8}})\.log$")


def cleanup_old(keep_days: Optional[int] = None, *, today: Optional[date] = None) -> list[str]:
    """残す日数を過ぎたログを消す(いまの場所と、このPCの既定の場所)。消した名前を返す。

    **名前の決まった物だけ**消す(`coil_packing_tools_YYYYMMDD.log`)。同じフォルダに
    人が置いたファイルには触らない。エラーの記録は `incidents.cleanup_old` が消す。
    """
    from . import local_settings
    days = keep_days if keep_days is not None else local_settings.log_keep_days()
    limit = (today or date.today()) - timedelta(days=days)
    removed: list[str] = []
    for folder in {log_dir(), default_log_dir()}:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            m = _LOG_NAME.match(name)
            if not m:
                continue
            try:
                day = date(int(m.group(1)[:4]), int(m.group(1)[4:6]), int(m.group(1)[6:]))
            except ValueError:
                continue
            if day < limit:
                try:
                    (Path(folder) / name).unlink()
                    removed.append(name)
                except OSError:
                    pass
    return removed


def configure_logging() -> None:
    """アプリ起動時に一度だけ呼ぶ。二重呼び出しは無害(冪等)。

    ハンドラは Python の根のロガーに1つ付ける。3機能のロガーは
    そこへ流れる(propagate)ので、**ファイルは1つ**になる。
    """
    global _configured
    if _configured:
        return

    global _recent, _file_handler
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        # 設定の出力先に書けない ── このPCの既定の場所へ
        with _where_lock:
            _where["dir"] = default_log_dir()
            _where["problem"] = "ログの出力先を作れません ── このPCの既定の場所へ出しています"
        log_dir().mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    # 根は INFO。3機能の根(上の MODULE_LEVELS)は移植元と同じ量を残す
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    for name, level in MODULE_LEVELS.items():
        logging.getLogger(name).setLevel(logging.DEBUG if _diagnostic else level)

    formatter = logging.Formatter(LINE_FORMAT, datefmt="%Y/%m/%d %H:%M:%S")
    context = _ContextFilter()

    file_handler = DailyFileHandler(date.today(), encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context)
    root.addHandler(file_handler)
    _file_handler = file_handler

    recent = RecentHandler()
    recent.setFormatter(formatter)
    recent.addFilter(context)
    root.addHandler(recent)
    _recent = recent

    # コンソールが無いときは付けない。
    # **`pythonw.exe` では `sys.stderr` が `None`** になる(Start.vbs は
    # コンソールを出さないために pythonw を使う)。`StreamHandler()` は
    # そのとき `stream = None` を抱え、1行出すたびに `None.write` で
    # 例外を起こす。`logging` が握りつぶすので表には出ないが、
    # DEBUG を大量に出す処理では、誰にも見えない例外をその回数ぶん払う
    if _has_console():
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.addFilter(context)
        root.addHandler(console_handler)

    _configured = True


def _has_console() -> bool:
    """標準エラー出力に書けるか。

    `pythonw.exe` は `sys.stderr` が `None`。リダイレクト先が閉じている
    場合に備えて `write` を持つかどうかまで見る。
    """
    import sys
    return getattr(sys, "stderr", None) is not None and hasattr(sys.stderr, "write")


def get_logger(root: str, name: str = "") -> logging.Logger:
    """モジュール別ロガーを取得する(未初期化なら自動で初期化する)。

    `root` は機能の根(`meisai` など)。`name` が空か根と同じなら根そのもの。
    """
    configure_logging()
    if not name or name == root:
        return logging.getLogger(root)
    return logging.getLogger(f"{root}.{name}")


def silence_console() -> None:
    """コンソールへのログ出力だけを止める(ファイルへの出力は残す)。

    テストや診断スクリプトのように、**そのプログラム自身の出力が主役**で
    あって業務ログが主役ではない場面のためのもの。

    `get_logger()` はインポート時に呼ばれることがあり、その時点で既に
    コンソール用ハンドラが付いている。あとから `configure_logging()` が
    走る場合もあるので、既存のハンドラを外すだけでなく、
    以降の追加も弾くように `addHandler` を包む。
    """
    logger = logging.getLogger()

    def _is_console(handler: logging.Handler) -> bool:
        # FileHandler は StreamHandler の派生なので先に除外する
        return (isinstance(handler, logging.StreamHandler)
                and not isinstance(handler, logging.FileHandler))

    for handler in list(logger.handlers):
        if _is_console(handler):
            logger.removeHandler(handler)

    if getattr(logger, "_console_silenced", False):
        return                                  # 二重に包まない(冪等)

    original_add = logger.addHandler

    def add_handler(handler: logging.Handler) -> None:
        if _is_console(handler):
            return
        original_add(handler)

    logger.addHandler = add_handler             # type: ignore[method-assign]
    logger._console_silenced = True             # type: ignore[attr-defined]
