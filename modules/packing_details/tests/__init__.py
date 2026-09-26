"""試験の共通の下ごしらえ

**ログを静める。** アプリのロガーは DEBUG で回っていて、1件の試験に
つき数行が端末へ流れる。落ちた試験を探すのに、通った試験のログを
スクロールで飛ばすことになるので、警告から上だけにする。

順番が要点です。`configure_logging()` は自分で `setLevel(DEBUG)` を
するので、**先に呼んでから**下げないと、最初に `meisai` を import した
時点で DEBUG に戻されます(実際そうなっていました)。

ログそのものを試験したくなったら、その試験の中で
`logging.getLogger("meisai").setLevel(...)` を戻してください。

**設定ファイルを本物から離す。** 設定は利用者ごとの領域
(`%LOCALAPPDATA%\\PackingDetails\\data\\user_config.json`)に書かれる。
試験がそこへ紙面の右上の文字や管理者パスワードを書くと、**その端末で
次に刷る紙が変わる。** 右上の文字と管理者パスワードは全ラインで共有
なので、共有フォルダを指したままだと**全ラインの紙が変わる。**
試験のあいだはどちらも使い捨てのフォルダを指す(1件ごとの片付けは
`_db.settings_file`)。
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from modules.packing_details.meisai import config, logging_utils

_SETTINGS_DIR = tempfile.TemporaryDirectory(prefix="packing-details-tests-")
config.USER_CONFIG_PATH = Path(_SETTINGS_DIR.name) / "user_config.json"
# 共有も同じ。**本物の共有に右上の文字を書くと、全ラインの紙が変わる**
config.SHARED_DIR = Path(_SETTINGS_DIR.name) / "share" / "【■】_参照用ファイル"
config.EXPORT_DIR = Path(_SETTINGS_DIR.name) / "export"
# 手元のDBも。裏の送り(`slip_history`)は自分の接続で `config.DB_PATH` を開く
config.DB_PATH = Path(_SETTINGS_DIR.name) / "local" / "packing_details.db"
# 配布設定も。**本物のアプリのフォルダに `配布設定\` を作ると、配ったときに混ざる**
from modules.packing_details.meisai import distribution  # noqa: E402  (config を差し替えてから読む)
distribution.DIR = Path(_SETTINGS_DIR.name) / "app" / "配布設定"

logging_utils.configure_logging()
logging.getLogger("meisai").setLevel(logging.WARNING)
