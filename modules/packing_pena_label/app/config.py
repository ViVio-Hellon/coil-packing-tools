# -*- coding: utf-8 -*-
"""アプリ固有値の集約。

基盤仕様書「ステップ2: アプリ固有値を決める」に従い、
アプリID / 表示名 / ポート / ローカル保存先 / 外部パス を
複数ファイルへ散らさずここへ集める。
``config/app.json`` があれば上書きできる。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

#: アプリケーションID（ローカル領域のフォルダ名、多重起動判定、/api/health の識別子）
APP_ID = "PackingPenaLabel"
APP_NAME = "梱包ペナ ラベル・風袋計算"
APP_VERSION = "1.5.12"
#: 版を切った日。画面のバージョン表示に添える。
#: **中身を直したら必ずここを上げる**。上げ忘れると、
#: 現場が「新しいファイルに差し替わったか」を画面から判断できない。
APP_BUILD = "2026-10-05"
#: 設定ファイルで上書きさせない項目（版はコード side の事実）
CODE_ONLY_KEYS = frozenset({"version", "build"})
DEFAULT_PORT = 8731

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_CODE_STAMP = ""


def code_stamp() -> str:
    """動いているコードの指紋（8 桁）。

    バージョン番号は人が上げるものなので、上げ忘れると
    「ファイルを差し替えたのに古いまま動いている」のか
    「差し替えは効いていて版番号だけ据え置き」なのかが分からない。
    実ファイルの中身から作る指紋を添えておけば、
    **数字が変わった＝中身が入れ替わった**と現場が判断できる。
    """
    global _CODE_STAMP
    if _CODE_STAMP:
        return _CODE_STAMP
    import hashlib
    h = hashlib.sha1()
    names = []
    for base, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", "tests", "tools",
                                "runtime", "logs", "data")]
        for f in files:
            if os.path.splitext(f)[1].lower() in (".py", ".html", ".css", ".js"):
                names.append(os.path.join(base, f))
    for path in sorted(names):
        try:
            with open(path, "rb") as fh:
                h.update(os.path.relpath(path, ROOT_DIR).encode("utf-8", "replace"))
                h.update(fh.read())
        except OSError:
            continue
    _CODE_STAMP = h.hexdigest()[:8]
    return _CODE_STAMP


def local_app_dir(app_id: str = APP_ID) -> str:
    """ユーザー別ローカル領域（Windows: ``%LOCALAPPDATA%\\<AppId>``）。

    共有フォルダーへログやキャッシュを作らないための分離。
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, app_id)


@dataclass
class Config:
    """アプリ固有値。``sources`` と ``app_config_used`` は
    ``asdict()`` に載せないため dataclass のフィールドにはしない。"""

    app_id: str = APP_ID
    app_name: str = APP_NAME
    version: str = APP_VERSION
    build: str = APP_BUILD
    port: int = DEFAULT_PORT
    host: str = "127.0.0.1"

    #: VBA 定数 PATH_AIM_参照 に相当（梱包資材マスタ.accdb の親フォルダ）
    #: ※VBA 本体に定義が無く別モジュール由来のため、実値は運用側で設定する
    aim_ref_path: str = ""
    #: VBA 定数 PATH_梱包_日報DebugPrint に相当（共有ログ出力先）
    debug_print_path: str = ""

    # ---- 差し替え可能なファイル・フォルダー ----
    # いずれも空文字なら「アプリ同梱の既定値」を使う。
    # 設定画面（/settings）から端末ごとに差し替えられる。
    #: サイズ構成マスタ JSON（既定: data/size_master.json）
    size_master_file: str = ""
    #: 資材マスタ CSV（Access 代替。既定: data/資材重量.csv）
    material_csv_file: str = ""
    #: 梱包資材マスタ SQLite。設定するとこれを最優先で読む
    #: （Access からの移行先。読取専用で開く）
    material_db_file: str = ""
    #: 型番マスタ CSV（既定: data/型番.csv）
    kataban_csv_file: str = ""
    #: 帳票の保存先（既定: ローカル領域の work/export）
    export_dir_path: str = ""
    #: ラベル台紙の寸法定義（既定: data/label_stock.json）
    label_stock_file: str = ""
    #: 使う台紙の id（空なら定義ファイルの先頭）
    label_stock_id: str = ""

    # ---- 印刷の較正（試し刷りで合わせる）----
    #: 印刷位置の補正 X(mm)。右へずらすなら +
    label_offset_x_mm: float = 0.0
    #: 印刷位置の補正 Y(mm)。下へずらすなら +
    label_offset_y_mm: float = 0.0
    #: 印刷倍率(%)。実測が小さいなら 100 より大きく
    label_scale_pct: float = 100.0
    #: バーコードの出し方。"svg"=自前描画（既定・フォント不要） / "font"=フォント
    barcode_mode: str = "svg"
    #: font モードで使うフォント名
    barcode_font_family: str = "K's BarCodeFont Code39"

    #: Access を優先して読むか（False なら常に CSV）
    prefer_access: bool = True
    access_timeout_sec: int = 60
    #: 資材マスタの鮮度チェック間隔(秒)。0 で毎回チェック。
    #: 業務中にマスタが更新されても古い単重で計算し続けないための設定。
    material_refresh_sec: int = 300

    #: 状態DB の接続を無操作で閉じるまでの秒数（0 で閉じない）。
    #: ツールを起動したまま放置したときに DB ファイルのハンドルを解放する。
    db_idle_close_sec: int = 120
    #: 状態DB のロック待ち時間(ms)
    db_busy_timeout_ms: int = 15000

    #: 積み高さクイック計算の 53.5mm 巾（VBA は 43.5 だった。解析書 13章 #2）
    quick_height_width_override: Dict[str, float] = field(default_factory=dict)

    #: 画面に出すコイルサイズ。空なら全件。
    #:
    #: 現状ラインへ来ないサイズを並べておくと、**選び間違い**が起きる。
    #: 来ないものは最初から出さない。
    #: 名前は `data/size_master.json` の baseName（表記統一後）で書く。
    visible_sizes: List[str] = field(
        default_factory=lambda: ["1.0mm×53.5mm", "0.8mm×53.5mm"])

    #: 監視レベル（基盤仕様書 3章）。入力・計算・帳票アプリなので 1
    monitor_level: int = 1

    # ---- 統合版で足したもの(dataclass のフィールドには**しない**) ----
    # 設定として保存・比較・配布させないため、注釈なしのクラス属性にしてある。
    #: この機能の入口。統合アプリでは ``/pena``。画面のリンクと JS が付ける
    url_prefix = ""
    #: 起動トークン。統合アプリがプロセスに1つ持つ値を、画面(_layout.html)へ渡す
    app_token = ""

    # -------------------------------------------------- 派生パス
    @property
    def local_dir(self) -> str:
        return local_app_dir(self.app_id)

    @property
    def runtime_dir(self) -> str:
        return os.path.join(self.local_dir, "runtime")

    @property
    def logs_dir(self) -> str:
        """統合版では3機能で1つのフォルダ(``%LOCALAPPDATA%\\CoilPackingTools\\logs``)。"""
        from common import logging_utils as _common_logging
        return str(_common_logging.log_dir())

    @property
    def cache_dir(self) -> str:
        return os.path.join(self.local_dir, "cache")

    @property
    def work_dir(self) -> str:
        return os.path.join(self.local_dir, "work")

    @property
    def backup_dir(self) -> str:
        return os.path.join(self.local_dir, "backup")

    @property
    def pycache_dir(self) -> str:
        return os.path.join(self.local_dir, "pycache")

    @property
    def db_path(self) -> str:
        return os.path.join(self.runtime_dir, "state.sqlite3")

    @property
    def runtime_info_path(self) -> str:
        return os.path.join(self.runtime_dir, "instance.json")

    # -- 同梱の既定値（設定が空のときに使う）--
    @property
    def default_size_master_path(self) -> str:
        return os.path.join(ROOT_DIR, "data", "size_master.json")

    @property
    def default_material_csv_path(self) -> str:
        return os.path.join(ROOT_DIR, "data", "資材重量.csv")

    @property
    def default_kataban_csv_path(self) -> str:
        return os.path.join(ROOT_DIR, "data", "型番.csv")

    @property
    def default_export_dir(self) -> str:
        return os.path.join(self.work_dir, "export")

    @property
    def default_label_stock_path(self) -> str:
        return os.path.join(ROOT_DIR, "data", "label_stock.json")

    @property
    def label_stock_path(self) -> str:
        return self.label_stock_file or self.default_label_stock_path

    # -- 実際に使うパス（上書きがあればそちら）--
    @property
    def size_master_path(self) -> str:
        return self.size_master_file or self.default_size_master_path

    @property
    def material_csv_path(self) -> str:
        return self.material_csv_file or self.default_material_csv_path

    @property
    def kataban_csv_path(self) -> str:
        return self.kataban_csv_file or self.default_kataban_csv_path

    @property
    def accdb_path(self) -> str:
        """梱包資材マスタ.accdb のフルパス（参照先フォルダー + 固定ファイル名）。

        VBA の ``PATH_AIM_参照`` は末尾に ``\\`` を付けて文字列連結していた。
        その値をそのまま貼り付けられるよう、**末尾の区切りは落としてから**
        繋ぐ。落とさないと ``...ファイル\\/梱包資材マスタ.accdb`` のように
        区切りが重なる（UNC の先頭 ``\\\\`` は消さない）。
        """
        base = (self.aim_ref_path or "").rstrip()
        if not base:
            return ""
        stripped = base.rstrip("\\/")
        # 区切りだけの指定（"\\" や "/"）で親を消してしまわない
        base = stripped if stripped else base
        return os.path.join(base, "梱包資材マスタ.accdb")

    @property
    def templates_dir(self) -> str:
        return os.path.join(ROOT_DIR, "app", "templates")

    @property
    def static_dir(self) -> str:
        return os.path.join(ROOT_DIR, "app", "static")

    @property
    def export_dir(self) -> str:
        """帳票のサーバー側保存先（ブラウザーのダウンロードは使わない）。"""
        return self.export_dir_path or self.default_export_dir

    # -- 設定ファイルの場所（2 層）--
    @property
    def app_config_path(self) -> str:
        """同梱の既定。アプリ本体側（共有配置のことがある）。管理者が編集する。"""
        return os.path.join(ROOT_DIR, "config", "app.json")

    @property
    def local_config_dir(self) -> str:
        return os.path.join(self.local_dir, "config")

    @property
    def local_config_path(self) -> str:
        """端末ごとの上書き。設定画面はこちらへ書く。

        アプリ本体を共有フォルダーへ置いた場合、そこへ書き戻すと
        書込権限が無かったり、他の利用者へ影響してしまうため分ける。
        """
        return os.path.join(self.local_config_dir, "local.json")

    def ensure_dirs(self) -> None:
        for d in (self.local_dir, self.runtime_dir, self.logs_dir, self.cache_dir,
                  self.work_dir, self.backup_dir, self.pycache_dir, self.export_dir,
                  self.local_config_dir):
            os.makedirs(d, exist_ok=True)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


#: 設定値がどの層から来たか
LAYER_DEFAULT = "default"    # コード内の既定値
LAYER_APP = "app"            # config/app.json（同梱の既定・共有側）
LAYER_LOCAL = "local"        # %LOCALAPPDATA%\<AppId>\config\local.json（この端末）
LAYER_ENV = "env"            # 環境変数（診断・検証用）

LAYER_LABEL = {
    LAYER_DEFAULT: "既定値",
    LAYER_APP: "同梱の既定",
    LAYER_LOCAL: "この端末",
    LAYER_ENV: "環境変数",
}


def _read_json(path: str) -> Dict[str, Any]:
    """設定 JSON を読む。壊れていても起動は続ける。"""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print("設定ファイルの読込に失敗しました（無視して続行）: %s / %s"
              % (path, exc), file=sys.stderr)
        return {}


def load_local_overrides(cfg: Optional[Config] = None) -> Dict[str, Any]:
    """端末ごとの上書き設定を読む。"""
    cfg = cfg or Config()
    data = _read_json(cfg.local_config_path)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def save_local_overrides(values: Dict[str, Any],
                         cfg: Optional[Config] = None) -> str:
    """端末ごとの上書き設定を保存する（アプリ本体側へは書かない）。

    ``config/app.json`` はアプリ本体（共有配置のことがある）にあるため、
    設定画面からの保存先は常にこちらのローカル側にする。
    保存前に 1 世代バックアップを取り、書込は一時ファイル経由で行う。
    """
    cfg = cfg or Config()
    os.makedirs(cfg.local_config_dir, exist_ok=True)
    path = cfg.local_config_path

    if os.path.exists(path):
        try:
            bak = path + ".bak"
            with open(path, encoding="utf-8") as src:
                content = src.read()
            with open(bak, "w", encoding="utf-8") as dst:
                dst.write(content)
        except OSError as exc:
            print("設定のバックアップに失敗しました（続行）: %s" % exc, file=sys.stderr)

    payload = {
        "_comment": [
            "この端末だけの設定。設定画面（/settings）が書き換える。",
            "同梱の既定は アプリ本体側の config/app.json にあり、こちらが優先される。",
            "このファイルを削除すると同梱の既定へ戻る。",
        ],
    }
    payload.update(values)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path


def clear_local_overrides(cfg: Optional[Config] = None) -> bool:
    """端末ごとの上書きを削除し、同梱の既定へ戻す。"""
    cfg = cfg or Config()
    path = cfg.local_config_path
    if not os.path.exists(path):
        return False
    try:
        os.replace(path, path + ".bak")
    except OSError:
        os.remove(path)
    return True


def load_base_config(path: str = "") -> Config:
    """**同梱の既定まで**の設定（コード内既定 + config/app.json）。

    設定画面の保存で「入力値が同梱の既定と同じか」を見るために使う。
    ここで現在有効な値（＝端末の上書きを含む）と比べてしまうと、
    **同じ値をもう一度保存しただけで上書きが消えて既定へ戻る**。
    """
    return load_config(path, include_local=False)


def load_config(path: str = "", include_local: bool = True) -> Config:
    """設定を組み立てる。

    優先順（後のものが勝つ）::

        コード内の既定値
          -> config/app.json          （同梱の既定・アプリ本体側／管理者が編集）
          -> local.json               （この端末だけの上書き／設定画面が書く）
          -> 環境変数                  （診断・検証用）

    どの層で決まったかは ``cfg.sources`` に残す（設定画面で表示する）。

    ``include_local=False`` にすると **同梱の既定までで止める**
    （local.json と環境変数を読まない）。設定画面が
    「この値は同梱の既定と同じか」を判定するために使う。
    """
    cfg = Config()
    sources: Dict[str, str] = {k: LAYER_DEFAULT for k in asdict(cfg)}

    def apply(data: Dict[str, Any], layer: str) -> None:
        for k, v in data.items():
            if k.startswith("_"):
                continue
            if k in CODE_ONLY_KEYS:
                # 版はコードのものなので、設定ファイルに書かれていても使わない。
                # 以前は app.json の "version" が勝っていたため、
                # 中身を差し替えても画面の版番号が動かなかった。
                continue
            if not hasattr(cfg, k):
                continue
            # 型を既定値に合わせる（JSON の取り違えで落ちないように）
            cur = getattr(cfg, k)
            try:
                if isinstance(cur, bool):
                    v = bool(v)
                elif isinstance(cur, float):
                    v = float(v)
                elif isinstance(cur, int) and not isinstance(v, bool):
                    v = int(v)
                elif isinstance(cur, str):
                    v = "" if v is None else str(v)
            except (TypeError, ValueError):
                print("設定 %r の値を解釈できません（無視）: %r" % (k, v),
                      file=sys.stderr)
                continue
            setattr(cfg, k, v)
            sources[k] = layer

    # 1) 同梱の既定
    app_path = path or cfg.app_config_path
    apply(_read_json(app_path), LAYER_APP)

    if not include_local:
        cfg.sources = sources
        cfg.app_config_used = app_path
        return cfg

    # 2) この端末の上書き（app_id が変わっていれば新しい方の場所を見る）
    apply(_read_json(cfg.local_config_path), LAYER_LOCAL)

    # 3) 環境変数（診断・検証用）
    env_map = {
        "PPL_PORT": ("port", int),
        "PPL_AIM_PATH": ("aim_ref_path", str),
        "PPL_PREFER_ACCESS": ("prefer_access",
                              lambda v: str(v).lower() in ("1", "true", "yes")),
    }
    for env_key, (attr, conv) in env_map.items():
        raw = os.environ.get(env_key)
        if raw in (None, ""):
            continue
        try:
            setattr(cfg, attr, conv(raw))
            sources[attr] = LAYER_ENV
        except (TypeError, ValueError):
            print("環境変数 %s の値を解釈できません（無視）: %r" % (env_key, raw),
                  file=sys.stderr)

    cfg.sources = sources
    cfg.app_config_used = app_path
    return cfg
