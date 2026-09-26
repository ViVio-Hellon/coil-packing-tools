"""アプリ設定・定数

VBA 版はパスを標準モジュールの `Public Const`(`PATH_AIM_参照` /
`PATH_仕掛_参照` / `PATH_梱包_仕掛`)に直接書いていた。置き場所が変わると
ブックを開いてコードを直す必要があり、端末ごとに写しがずれる原因になる。

ここでは**3段で決める**。

    1. 設定画面で入れた値 (`user_settings` の JSON)   ← いちばん強い
    2. 環境変数                                        ← 検証・CI 用
    3. 既定値(このファイル)                           ← 何も無いとき

設定画面から変えられるので、**このファイルの既定値を現場が直す必要は無い**。

【実行中に変わるものはローカルへ】
ログ・キャッシュ・一時ファイルは `%LOCALAPPDATA%\\CoilMaterialTool\\` 配下に
置く(基盤仕様書 2.7)。アプリ本体を共有フォルダに置いて複数端末から
使っても、端末どうしで混ざらない。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from common import app_config as _integrated_app_config
from common import logging_utils as _common_logging
from . import app_config

# `logging_utils` は `config` を読むので、ここから読むと輪になる。
# 名前だけ `get_logger` と揃えておけば、設定は向こうが済ませてくれる
log = logging.getLogger("coil_tool.config")

APP_NAME = "CoilMaterialTool"

# ------------------------------------------------------------------
# パス設定
# ------------------------------------------------------------------
# 機能のフォルダ (このファイルの2つ上の階層。統合版では modules\\packing_material_calculation)
BASE_DIR = Path(__file__).resolve().parent.parent

# **アプリのフォルダ** = 統合アプリの根(`Start.vbs` があるフォルダ)。
# 利用者にとっての「アプリのフォルダ」はここ。設定で置き場所を相対で書いたときの
# 基準(`resolve_dir`)と、共有に届かない端末で写しを置く場所はこちらを使う。
# 移植元では `BASE_DIR` と同じだったが、統合版では `BASE_DIR` が機能のフォルダ
# (`modules\\<機能>`)になったため分けた(統合版で直した。相対で書いた設定が
# 黙って別の場所を指していた)。`BASE_DIR` は説明書など機能の中身の置き場所にだけ使う
APP_DIR = Path(_integrated_app_config.APP_ROOT)

# 手元の作業用 SQLite。取り込んだマスタの写しと、このツールが書くもの
# (チェックリスト・発注履歴)が入る
# 作業用DBと利用者設定は**利用者ごとのローカル領域**へ置く
# (基盤仕様書 2.7 / `app_config.LOCAL_SUBDIRS` の `data`)。
#
# 以前はアプリ本体の隣(`BASE_DIR/data/`)だった。本体を共有フォルダに
# 置いて複数の端末から使う運用だと、**作業用DBと担当者・置き場所の
# 設定まで全端末で共有**され、あとから保存した端末の値で上書きされる。
# ログだけがローカル領域に移してあり、DBと設定は移し忘れていた。
#
# 旧い場所にファイルが残っていれば、起動時に1度だけ写す(`migrate_local`)。
DB_PATH = Path(os.environ.get("COIL_TOOL_DB_PATH",
                              str(app_config.local_dir("data") / "coil_tool.db")))
# 旧い置き場所。移し替えの元としてだけ見る
LEGACY_DB_PATH = APP_DIR / "data" / "coil_tool.db"

# ログ出力先。**統合版では3機能で1つのフォルダ**
# (`%LOCALAPPDATA%\\CoilPackingTools\\logs`。`common/logging_utils.py`)。
# 移植元は機能ごとの領域(`CoilMaterialTool\\logs`)だった
LOG_DIR = Path(os.environ.get("COIL_TOOL_LOG_DIR",
                              str(_common_logging.log_dir())))

# 設定画面の値を保存する JSON(VBA のレジストリ `SaveSetting` の代替)
USER_CONFIG_PATH = Path(os.environ.get("COIL_TOOL_CONFIG_PATH",
                                       str(app_config.local_dir("data") / "user_config.json")))
LEGACY_USER_CONFIG_PATH = APP_DIR / "data" / "user_config.json"

# ------------------------------------------------------------------
# 取り込み元の置き場所(既定値)
# ------------------------------------------------------------------
# 梱包資材マスタのフォルダ。VBA `PATH_AIM_参照` に相当。
#
# 既定値は姉妹ツール(python-web-tools)が使っている共有フォルダと同じ。
# **設定画面で変えられる**ので、現場でここを書き換える必要は無い。
DEFAULT_MASTER_DB_DIR = Path(os.environ.get(
    "COIL_TOOL_MASTER_DB_DIR",
    r"\\nlmfangyshrd\各課共有\0130_日軽稲沢\梱包課\AIM\【■】_参照用ファイル"))

# 梱包資材マスタのファイル名。見つからない場合はフォルダ内の sqlite3 を探す
MATERIAL_DB_NAME = os.environ.get("COIL_TOOL_MATERIAL_DB", "梱包資材マスタ.sqlite3")

# 仕掛台帳のフォルダ。VBA `PATH_仕掛_参照` に相当
DEFAULT_LOT_DB_DIR = Path(os.environ.get(
    "COIL_TOOL_LOT_DB_DIR", r"\\nlmsrvngy03\Read\【New】仕掛\台帳"))

# 仕掛台帳の**予備**フォルダ。VBA `PATH_梱包_仕掛` に相当。
#
# VBA の `LOT検索` / `フォーム展開` は2つの置き場所を交互に試していた
# (片方が上流の更新中で読めないことがあるため)。同じ考え方を残すが、
# **予備は空でよい**。空なら1系統だけを見る。
DEFAULT_LOT_DB_DIR_FALLBACK = Path(os.environ.get(
    "COIL_TOOL_LOT_DB_DIR_2", "")) if os.environ.get("COIL_TOOL_LOT_DB_DIR_2") else None

# 仕掛台帳の3ファイル(手元のテーブル名 → 取り込み元のファイル名)。
# 拡張子違い(.db)も `source_db.find` が拾う
#
# **SIKALOT は、このツールでは使いません。** VBA が開くのは
# `SIKAHIKINOW.accdb` と `SIKAODRNOW.accdb` の2つだけで、SIKALOT を
# 開く行はありません(姉妹ツールが使っています)。ここに名前が
# あるのは、起動時の診断で「同じフォルダに何があるか」を記録する
# ためで、取り込みはしません(`import_specs.LOT_IMPORT_SPECS` に
# 定義が無いので回りません)。
LOT_DB_FILES = {
    "仕掛ロット": "SIKALOT.sqlite3",
    "仕掛引当": "SIKAHIKI.sqlite3",
    "仕掛受注": "SIKAODR.sqlite3",
}

# CSV などの書き出し先。**書く場所**なので、既定は共有ではなくツールの下。
# 共有に届かない端末でも書き出しそのものは必ず成功させる
# 統合版では統合アプリのフォルダの直下 `export\\packing_material_calculation\\`
DEFAULT_EXPORT_DIR = Path(os.environ.get(
    "COIL_TOOL_EXPORT_DIR",
    str(APP_DIR / "export" / "packing_material_calculation")))


# ------------------------------------------------------------------
# 打たれた道を、実際に見に行く道にする
# ------------------------------------------------------------------
def resolve_dir(text: str) -> Path:
    r"""設定画面に打たれた文字列を Path にする。

    2通りの書き方を受ける。

        絶対  `\\サーバ\共有\…` / `C:\data\…` / `/mnt/share/…`
              打たれたまま使う
        相対  `data\src` / `..\共有` / `src`
              **アプリのフォルダから**たどる(`APP_DIR`。統合アプリの根)

    相対を「いまの作業フォルダ」から見ないのが要点。作業フォルダは
    どこから起動したかで変わるので、同じ設定でも端末ごとに違う場所を
    指すことになる。アプリのフォルダなら、フォルダごとコピーして配る
    運用でも写しの中の同じ場所を指し続ける。

    (`~` は利用者のフォルダに開く。共有に届かない端末で手元の写しを
     指すのに使える)
    """
    trimmed = (text or "").strip().strip('"')
    if not trimmed:
        raise ValueError("道が空です")
    path = Path(trimmed).expanduser()
    if path.is_absolute():
        return path
    return APP_DIR / path


def is_relative_setting(text: str) -> bool:
    """その書き方は相対か。画面に「どちらとして読んだか」を出すため。"""
    trimmed = (text or "").strip().strip('"')
    if not trimmed:
        return False
    return not Path(trimmed).expanduser().is_absolute()


def _configured_dir(key: str, default: Path) -> Path:
    """設定画面の値を優先して取る。

    `user_settings` を遅延 import するのは、`config` を先に読む
    モジュールとの循環参照を避けるため。
    """
    from . import user_settings
    configured = user_settings.get(key)
    if isinstance(configured, str) and configured.strip():
        try:
            return resolve_dir(configured)
        except ValueError:
            pass
    return default


def master_db_dir() -> Path:
    """いま使う梱包資材マスタのフォルダ(VBA `PATH_AIM_参照`)。"""
    return _configured_dir(KEY_MASTER_DB_DIR, DEFAULT_MASTER_DB_DIR)


def lot_db_dir() -> Path:
    """いま使う仕掛台帳のフォルダ(VBA `PATH_仕掛_参照`)。"""
    return _configured_dir(KEY_LOT_DB_DIR, DEFAULT_LOT_DB_DIR)


def lot_db_dir_fallback() -> Path | None:
    """仕掛台帳の予備フォルダ(VBA `PATH_梱包_仕掛`)。未設定なら None。

    **未設定を「無し」として返す**のが要点。既定の共有パスを勝手に
    入れてしまうと、届かない端末で毎回そこへ取りに行って待たされる。
    """
    from . import user_settings
    configured = user_settings.get(KEY_LOT_DB_DIR_2)
    if isinstance(configured, str) and configured.strip():
        try:
            return resolve_dir(configured)
        except ValueError:
            return None
    return DEFAULT_LOT_DB_DIR_FALLBACK


def lot_db_dirs() -> list[Path]:
    """仕掛台帳を探す順。本命 → 予備。

    VBA は「片方が読めなければもう片方」を毎回書いていたが、
    ここで順番の列にしておけば呼ぶ側は回すだけでよい。
    """
    dirs = [lot_db_dir()]
    spare = lot_db_dir_fallback()
    if spare is not None and spare != dirs[0]:
        dirs.append(spare)
    return dirs


def export_dir() -> Path:
    """CSV の書き出し先。"""
    return _configured_dir(KEY_EXPORT_DIR, DEFAULT_EXPORT_DIR)


# ------------------------------------------------------------------
# `user_settings` に入れるキー
# ------------------------------------------------------------------
KEY_MASTER_DB_DIR = "master_db_dir"
KEY_LOT_DB_DIR = "lot_db_dir"
KEY_LOT_DB_DIR_2 = "lot_db_dir_2"
KEY_EXPORT_DIR = "export_dir"
KEY_AUTO_IMPORT = "auto_import_on_start"
KEY_LINE = "line"
KEY_WORKER = "worker"
KEY_ADMIN_PASSWORD = "admin_password"
# 発注票の定尺(`order_sheet_service.stock_lengths`)。画面には欄が無く、
# 設定ファイルを直したときだけ入る
KEY_STOCK_LENGTHS = "stock_lengths"

# 起動時の自動取り込みを既定で ON にするか。
# 元ファイルが更新されているときだけ取り込むので毎回全部は読み直さない
AUTO_IMPORT_DEFAULT = True

# ------------------------------------------------------------------
# マスタを直すときのパスワード
# ------------------------------------------------------------------
# **これは誰かを見分けるものではありません。** 共有フォルダのマスタを
# 誤って書き換えないための関門です(VBA 版 `MaterialMasterForm` の
# `ADMIN_PASSWORD` から引き継いだ役目)。端末に鍵が掛かる仕組みではない
# ので、本当に守りたいものをこれで守らないこと。
#
# 一度も変えていない端末ではこの値が通ります。設定画面から変えると、
# 撹拌した値が `user_settings` に入り、そちらが優先されます
# (`admin_password`)。環境変数でも上書きできます。
ADMIN_PASSWORD = os.environ.get("COIL_TOOL_ADMIN_PASSWORD", "nisk")

# 通してから、何もしないまま放っておける時間(秒)。
#
# **現場の端末は誰でも触れます。** 1度通したらアプリを閉じるまで開いた
# ままだと、席を離れたあいだに共有のマスタを書き換えられます。
# 短すぎると打ち直しばかりになるので、間を取って30分。
ADMIN_SESSION_IDLE_SEC = 30 * 60

# ------------------------------------------------------------------
# 業務の固定値
# ------------------------------------------------------------------
# ライン。VBA `UF_Material` のオプションボタン LS4 / NS1。
# チェックリストの表題に出る
LINES: tuple[str, ...] = ("LS4", "NS1")
DEFAULT_LINE = "LS4"

# 担当者の**予備**。VBA `UF_Material` の W1〜W7(発注票・チェックリストの
# 依頼者欄)を画面に直書きしていたときの一覧。
#
# いまは梱包資材マスタの「班員名簿」から読む(`coil_tool/staff.py`)。
# ここが残っているのは**名簿が取り込めていないときの逃げ道**として ──
# 空の一覧を出すと担当者を選べず、担当者が無いとチェックリストへ
# 積めないので、名簿が読めないだけで仕事が止まる。
#
# **名簿の書き方に合わせてある。** VBA は苗字だけ(しかも「浜崎」)で
# 持っていたが、名簿は「濱崎俊之」のようにフルネーム。ここを苗字の
# ままにすると、取り込む前に選んだ人が、取り込んだ後に
# 「名簿にありません」へ化ける。実物の名簿(担当ライン=機側)で
# 突き合わせて、同じ7人を名簿の書き方に直した。
WORKERS: tuple[str, ...] = (
    "高村敏幸", "濱崎俊之", "山野井功扶", "大竹慎治",
    "石田初雄", "高橋敏明", "黒川雄二",
)

# 包装仕様書の閲覧システム(姉妹ツール python-web-tools の
# `presenters.lot.URL_HOSO_SHIYOSHO` と同じ先。VBA `URL_HOSO_SHIYOSHO`)。
#
# **固定URLで開くだけで、包装仕様NOは渡らない**(先方で検索し直す)。
# なので画面は、押したときに包装仕様NOをクリップボードへコピーしてから
# 開く ── 開いた先でそのまま貼れる
URL_HOSO_SHIYOSHO = ("http://nlmfangysysv:9084/NgyPkgWeb/"
                     "#!/home?mode=hosoShiyoshoEtsuran")

# 取り込むマスタのテーブル名(梱包資材マスタ側)
TBL_SPEC = "包装仕様"
TBL_PALLET = "パレット"
TBL_RIPLA_SIZE = "リプラサイズ"
TBL_STAFF = "班員名簿"

# このツールが書くテーブル
TBL_CHECKLIST = "発注チェックリスト"
TBL_ORDER_HISTORY = "発注履歴"

# チェックリストの行数。VBA はスピンボタンで 1〜15 に制限していた
CHECKLIST_ROWS = 15

# 発注履歴の重複警告を出す日数。VBA `CheckHistoryLotNo` の `diffDays <= 14`
ORDER_HISTORY_WARN_DAYS = 14

# DB 書き込みのリトライ (VBA `ExecuteSQLWithRetry` 相当)
MAX_RETRY = 3
RETRY_BASE_WAIT_SEC = 1.0


def ensure_dirs() -> None:
    """DB/ログ/設定ファイル用のディレクトリが無ければ作る。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def migrate_local() -> list[str]:
    """旧い置き場所(アプリ本体の隣)から、利用者ごとの領域へ写す。

    **1度だけ、新しい側が無いときだけ。** 既にこちらで使い始めて
    いるなら、古いほうで上書きしてはいけない。

    旧ファイルは**消さない** ── 写し損ねていたときに戻せなくなる。
    共有フォルダに置いてあった場合、他の端末がまだ旧い側を見ている
    可能性もある。

    DBは `backup()` で写す。ファイルをコピーすると、WAL にだけ書かれて
    まだ本体へ落ちていない分を取りこぼす。
    """
    done: list[str] = []
    ensure_dirs()

    if LEGACY_USER_CONFIG_PATH.exists() and not USER_CONFIG_PATH.exists():
        try:
            USER_CONFIG_PATH.write_bytes(LEGACY_USER_CONFIG_PATH.read_bytes())
            done.append(f"設定: {LEGACY_USER_CONFIG_PATH} → {USER_CONFIG_PATH}")
        except OSError as exc:
            log.warning("設定を写せませんでした: %s", exc)

    if LEGACY_DB_PATH.exists() and not DB_PATH.exists():
        import sqlite3
        try:
            src = sqlite3.connect(str(LEGACY_DB_PATH))
            dst = sqlite3.connect(str(DB_PATH))
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
            done.append(f"作業用DB: {LEGACY_DB_PATH} → {DB_PATH}")
        except sqlite3.Error as exc:
            log.warning("作業用DBを写せませんでした: %s", exc)
            # 中途半端な写しを残さない ── 次の起動でやり直せるように
            DB_PATH.unlink(missing_ok=True)

    for line in done:
        log.info("利用者ごとの領域へ写しました ── %s", line)
    return done
