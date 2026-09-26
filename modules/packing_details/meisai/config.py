"""置き場所と業務定数

VBA版(`mDB` / `mUtil` の `Public Const`)が持っていた定数のうち、
Python/SQLite版で意味を持つものを移植する。

【移植方針】
VBA版はDBファイル(.accdb)を社内ネットワーク共有(UNCパス)に置き、
ADO＋ACE で直接開いていた。取り込み元が sqlite3 になったので、
**標準ライブラリだけで、どの端末でも同じように読める**。

実パスはソースに埋め込みきらず、環境変数と設定画面で差し替えられる
ようにする(共有に届かない端末では手元の写しを指せる)。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from common import logging_utils as _common_logging
from . import app_config

APP_NAME = "PackingDetails"

# ------------------------------------------------------------------
# パス設定
# ------------------------------------------------------------------
# プロジェクトルート (このファイルの2つ上の階層)
BASE_DIR = Path(__file__).resolve().parent.parent

# 手元の作業用DB。**端末ごとに1つ**。
#
# 共有フォルダへ置かないこと ── SQLiteはネットワーク越しのファイル共有だと
# ファイルロックが正しく効かず、複数台から同時に使うと壊れることがある
# (移植元の手引きが同じ理由で禁じている)。副番履歴を全ライン共有したい
# 場合は、このファイルを共有するのではなくサーバを1台に寄せる。
DB_PATH = Path(os.environ.get("PACKING_DETAILS_DB_PATH",
                              str(app_config.local_dir("data") / "packing_details.db")))


def db_path_problem(path: Optional[Path] = None) -> str:
    """手元DBの置き場所がまずければ理由。よければ空文字。

    **禁じているだけでは止まらない。** 上の断り書きは読む人が読めば
    伝わるが、環境変数1つで共有の上へ置けてしまう。置かれたことに
    気づける場所を作る(起動時のログと、バージョン情報の画面)。

    起動は止めない ── 共有の上でも1台だけで使っているうちは動くので、
    ここで落とすと「昨日まで動いていたのに」になる。
    """
    target = Path(path if path is not None else DB_PATH)
    text = str(target)
    if text.startswith("\\\\") or text.startswith("//"):
        return ("手元DBが共有フォルダ(UNCパス)にあります: "
                f"{text} ── SQLite はネットワーク越しだとファイルロックが"
                "正しく効かず、複数台から同時に使うと壊れることがあります。"
                "端末ごとのローカル領域へ移してください。")
    # Windows の割り当てドライブ。`net use` で共有に繋がっていることが多い
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        try:
            import ctypes
            kind = ctypes.windll.kernel32.GetDriveTypeW(f"{text[0]}:\\")
            if kind == 4:               # DRIVE_REMOTE
                return ("手元DBがネットワークドライブにあります: "
                        f"{text} ── 端末ごとのローカル領域へ移してください。")
        except Exception:               # 判定できなければ黙って通す
            pass
    return ""

# ログ出力先。**統合版では3機能で1つのフォルダ**
# (`%LOCALAPPDATA%\\CoilPackingTools\\logs`。`common/logging_utils.py`)。
# 移植元は機能ごとの領域(`PackingDetails\\logs`)だった
LOG_DIR = Path(os.environ.get("PACKING_DETAILS_LOG_DIR",
                              str(_common_logging.log_dir())))

# 利用者ごとの設定(取り込み元の置き場所など)
USER_CONFIG_PATH = Path(os.environ.get(
    "PACKING_DETAILS_CONFIG_PATH",
    str(app_config.local_dir("data") / "user_config.json")))

# ------------------------------------------------------------------
# 取り込み元 (sqlite3) の置き場所
# ------------------------------------------------------------------
# 仕掛台帳。VBA `mDB.PATH_仕掛_台帳` と同じ場所。
#   \\nlmsrvngy03\Read\【New】仕掛\台帳\
LOT_DB_DIR = Path(os.environ.get(
    "PACKING_DETAILS_LOT_DB_DIR", r"\\nlmsrvngy03\Read\【New】仕掛\台帳"))

# 梱包課共有の仕掛。VBA `mDB.PATH_梱包課共有仕掛` と同じ場所。
# **仕掛台帳とは別フォルダ**なので設定キーも分ける
KONPO_DB_DIR = Path(os.environ.get(
    "PACKING_DETAILS_KONPO_DB_DIR",
    r"\\nlmfangyshrd\各課共有\0130_日軽稲沢\梱包課\梱包課共有\仕掛"))

# 全ラインで共有する設定(紙面の右上の文字・管理者パスワード)の置き場所。
# **梱包資材マスタのフォルダ**(梱包資材総合ツールと同じ既定)。
#
#     梱包資材マスタ.sqlite3 の表「梱包明細打ち出し」 … 正(ID=1 が右上の文字)
#     梱包明細打ち出し.json                         … 控え・管理者パスワード
#
# 総合ツールのラインPCが読み書きしている場所なので、届く・書ける実績がある。
# 書けない場合は、設定画面(端末ごと)か環境変数で書ける場所へ差し替える
# (`shared_settings`)
SHARED_DIR = Path(os.environ.get(
    "PACKING_DETAILS_SHARED_DIR",
    r"\\nlmfangyshrd\各課共有\0130_日軽稲沢\梱包課\AIM\【■】_参照用ファイル"))

# 梱包資材マスタのファイル名(総合ツールと同じ)。拡張子違い(.db)も拾う
MASTER_DB_NAME = os.environ.get("PACKING_DETAILS_MASTER_DB", "梱包資材マスタ.sqlite3")

# 明細の履歴(全ライン)。梱包資材マスタと**同じフォルダの別ファイル**
# (`slip_history`)。マスタの中に入れないのは、1年で数十倍に膨らみ、
# 総合ツールがマスタに書く前に取る控え(ファイルまるごと)が重くなるため
HISTORY_DB_NAME = os.environ.get("PACKING_DETAILS_HISTORY_DB", "梱包明細履歴.sqlite3")

# 明細の履歴を残す年数(現場の指定)。これより古いものは1日1回整理する
HISTORY_KEEP_YEARS = 3

# 手元の履歴を共有へ送り直す間隔(秒)。出力・書き足し・紙面を開いた
# ときはその場で送るので、これは届かなかった分を拾い直すためのもの
HISTORY_SEND_INTERVAL_SEC = 300

# CSVの書き出し先。**読む場所ではなく書く場所**なので、共有ではなく
# このアプリのフォルダの下(総合ツールと同じ考え)。ブラウザでの
# ダウンロードはしない ── ファイルに書いて、場所を出す(現場の指定)
# 統合版では統合アプリのフォルダの直下 `export\\packing_details\\`(機能ごとに分ける)
EXPORT_DIR = Path(os.environ.get("PACKING_DETAILS_EXPORT_DIR",
                                 str(BASE_DIR.parent.parent / "export" / "packing_details")))

# 仕掛台帳の3ファイル(手元のテーブル名 → 取り込み元のファイル名)。
# 拡張子違い(.db)も `source_db.find` が拾う。
#
# VBA は `SIKALOTNOW.accdb` 等を開いていたが、上流が sqlite3 化された
# ことで `NOW` の付かない名前になった(実データで確認済み)
LOT_DB_FILES = {
    "仕掛ロット": "SIKALOT.sqlite3",
    "仕掛引当": "SIKAHIKI.sqlite3",
    "仕掛受注": "SIKAODR.sqlite3",
}

# 梱包課共有の1ファイル。VBA `DB_KONPO_SIKAKARI`。
# **前工程実績数と丈数はここにしか無い** ── 仕掛台帳にも
# `BOX設計_縦割数/横割数` はあるが、実データでは532件中340件が
# `当工程設計_*` と食い違う。代用してはいけない
KONPO_DB_FILES = {
    "仕掛当工程": "LS4LOT.sqlite3",
}

# 取り込み元すべて(表示・診断用)
ALL_SOURCE_FILES = {**LOT_DB_FILES, **KONPO_DB_FILES}

# ------------------------------------------------------------------
# 外部リンク
# ------------------------------------------------------------------
# 包装仕様書の閲覧システム(VBA `mUtil.URL_HOUSOU`)。
# VBA は包装仕様NOをクリップボードへ写してからこのURLを開き、
# 利用者が貼り付けて検索していた。Web版も同じ手順にする
URL_HOSO_SHIYOSHO = os.environ.get(
    "PACKING_DETAILS_HOSO_URL",
    "http://nlmfangysysv:9084/NgyPkgWeb/#!/home?mode=hosoShiyoshoEtsuran")

# `user_settings` に入れるキー
KEY_LOT_DB_DIR = "lot_db_dir"
KEY_KONPO_DB_DIR = "konpo_db_dir"
KEY_AUTO_IMPORT = "auto_import_on_start"
# VER 0.7.0 で端末ごとに持っていた値。**いまは使わない**(全ラインで共有。
# `shared_settings`)。残っていたらログで1度だけ知らせる
KEY_QA_MARK = "qa_mark"
KEY_ADMIN_PASSWORD = "admin_password"
KEY_SHARED_DIR = "shared_dir"           # 共有の設定フォルダ(端末ごとに差し替え)
KEY_SHARED_CACHE = "shared_cache"       # 共有から最後に読んだ値の写し

# 管理者パスワードの既定(一度も変えていない端末で通る値)。
#
# 【注意】**UIガードです。** 紙面の右上の文字を、設定画面を開いた人が
# 押し間違いで変えないためのもので、本来の意味でのアクセス制御ではない。
# 流用元(梱包資材総合ツール)と同じ既定値にしてある ── 同じ現場の人が
# 使うので、覚える値を増やさない。
# 画面から変えられる(撹拌して `user_settings` に持つ)。配るときに
# 決めておきたければ、環境変数 PACKING_DETAILS_ADMIN_PASSWORD で上書きする
ADMIN_PASSWORD = os.environ.get("PACKING_DETAILS_ADMIN_PASSWORD", "nisk")

# 起動時の自動取り込みを既定でONにするか。
# 仕掛台帳は日々更新されるので、元が変わっていれば読み直す
AUTO_IMPORT_DEFAULT = True

# ------------------------------------------------------------------
# 業務定数 (VBA の Private Const をそのまま移す)
# ------------------------------------------------------------------
# ロット番号の桁数。VBA `txtLotNo.MaxLength = 7` / `If Len(...) <> 7`
LOT_NO_LENGTH = 7

# 丈数の上限。VBA `JOUSU_MAX`
JOUSU_MAX = 20

# 積み条数の上限。VBA `STACK_LIMIT`
STACK_LIMIT = 15

# 明細表のデータ行数。VBA `For r = 6 To 20`(A6:F20 の15行)。
# `STACK_LIMIT` と同じ値だが**別の理由で決まっている**ので別に持つ
#   STACK_LIMIT … 1梱包に積める本数(業務の上限)
#   SHEET_ROWS  … 紙に収まる行数(帳票の都合)
SHEET_ROWS = 15

# スナップショットを何ロット分もっておくか。VBA `SNAP_MAX`
# (元はシートのG〜K列という物理的な制約から来た数)
SNAPSHOT_KEEP = 5

# DB書き込みのリトライ設定
MAX_RETRY = 3
RETRY_BASE_WAIT_SEC = 1.0


def resolve_dir(text: str) -> Path:
    r"""打たれた道を、**実際に見に行く道**にする。

    2通りの書き方を受ける。

        絶対  `\\サーバ\共有\…` / `C:\data\…` / `/mnt/share/…`
              打たれたまま使う
        相対  `data\src` / `..\共有`
              **アプリのフォルダから**たどる(`BASE_DIR`)

    相対を「いまの作業フォルダ」から見ないのが要点。作業フォルダは
    どこから起動したかで変わるので、同じ設定でも端末ごとに違う場所を
    指すことになる。
    """
    trimmed = (text or "").strip().strip('"')
    if not trimmed:
        raise ValueError("道が空です")
    path = Path(trimmed).expanduser()
    if path.is_absolute():
        return path
    return BASE_DIR / path


def is_relative_setting(text: str) -> bool:
    """その書き方は相対か。画面に「どちらとして読んだか」を出すため。"""
    trimmed = (text or "").strip().strip('"')
    if not trimmed:
        return False
    return not Path(trimmed).expanduser().is_absolute()


def lot_db_dir() -> Path:
    """仕掛台帳のフォルダ。設定画面の値を優先する。

    `user_settings` を遅延importするのは、`config` を先に読む
    モジュールとの循環参照を避けるため。
    """
    from . import user_settings
    configured = user_settings.get(KEY_LOT_DB_DIR)
    if isinstance(configured, str) and configured.strip():
        return resolve_dir(configured)
    return LOT_DB_DIR


def konpo_db_dir() -> Path:
    """梱包課共有の仕掛フォルダ。設定画面の値を優先する。"""
    from . import user_settings
    configured = user_settings.get(KEY_KONPO_DB_DIR)
    if isinstance(configured, str) and configured.strip():
        return resolve_dir(configured)
    return KONPO_DB_DIR


def ensure_dirs() -> None:
    """DB・ログ・設定ファイル用のフォルダが無ければ作る。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
