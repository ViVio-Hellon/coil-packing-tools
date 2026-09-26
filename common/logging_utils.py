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

【置き場所】`%LOCALAPPDATA%\\CoilPackingTools\\logs\\`
(環境変数 `COIL_PACKING_TOOLS_LOG_DIR` で差し替えられる)

【呼び方】各機能の `logging_utils` は、ここへの薄い取り次ぎ。
`get_logger(root, name)` でロガーを取る。`configure_logging()` は
何度呼んでも1度しか効かない(冪等)。
"""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

from . import app_config

# 3機能のロガーの根。DEBUG まで通す(移植元と同じ)。
# ペナラベルは `logging.getLogger(__name__)` なので、パッケージ名が根になる
MODULE_ROOTS: tuple[str, ...] = (
    "meisai", "coil_tool", "modules.packing_pena_label", "coil_packing_tools",
)

# ファイル名の前置き。**出どころはここ1つ**
FILE_PREFIX = "coil_packing_tools"

_configured = False


def log_dir() -> Path:
    """ログの置き場所。環境変数で差し替えられる(検証・CI 用)。"""
    override = os.environ.get("COIL_PACKING_TOOLS_LOG_DIR")
    if override and override.strip():
        return Path(override.strip())
    return app_config.local_dir("logs")


def log_path_for(day: date) -> Path:
    """その日のログファイル。**名前の作り方はここ1か所**。"""
    return log_dir() / f"{FILE_PREFIX}_{day:%Y%m%d}.log"


class DailyFileHandler(logging.FileHandler):
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
        # **書く直前に見る。** 日付が変わったことは、次の1行で気づく
        today = date.today()
        if today != self._day:
            self._day = today
            self.close()
            self.baseFilename = str(log_path_for(today))
            self.stream = None                    # 次の emit で開き直す
        super().emit(record)


def configure_logging() -> None:
    """アプリ起動時に一度だけ呼ぶ。二重呼び出しは無害(冪等)。

    ハンドラは Python の根のロガーに1つ付ける。3機能のロガーは
    そこへ流れる(propagate)ので、**ファイルは1つ**になる。
    """
    global _configured
    if _configured:
        return

    log_dir().mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    # 根は INFO。3機能の根(下)は DEBUG まで通す ── 移植元と同じ量を残す
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    for name in MODULE_ROOTS:
        logging.getLogger(name).setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s",
                                   datefmt="%Y/%m/%d %H:%M:%S")

    file_handler = DailyFileHandler(date.today(), encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # コンソールが無いときは付けない。
    # **`pythonw.exe` では `sys.stderr` が `None`** になる(Start.vbs は
    # コンソールを出さないために pythonw を使う)。`StreamHandler()` は
    # そのとき `stream = None` を抱え、1行出すたびに `None.write` で
    # 例外を起こす。`logging` が握りつぶすので表には出ないが、
    # DEBUG を大量に出す処理では、誰にも見えない例外をその回数ぶん払う
    if _has_console():
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
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
