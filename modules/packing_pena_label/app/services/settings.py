# -*- coding: utf-8 -*-
"""設定画面（/settings）の定義・検証・反映。

VBA では参照先パスがコード内の定数（``PATH_AIM_参照`` など）や
各所のリテラルに散らばっており、現場ごとに書き換えるには
VBE を開いて該当行を探す必要があった。
移植版では **設定として外へ出し、画面から変更できる** ようにする。

保存先は **端末ごとのローカル領域**（``%LOCALAPPDATA%\\<AppId>\\config\\local.json``）。
アプリ本体を共有フォルダーへ置いた場合でも、そこへ書き戻さない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..config import (LAYER_LABEL, Config, clear_local_overrides,
                      load_local_overrides, save_local_overrides)

#: 資材マスタのファイル名（VBA でハードコードされていたもの）
ACCDB_NAME = "梱包資材マスタ.accdb"


@dataclass
class SettingSpec:
    """設定 1 項目の定義。画面はこの定義から生成する。"""
    key: str
    label: str
    kind: str                  # dir / file / text / bool / int
    group: str
    help: str = ""
    placeholder: str = ""
    #: 空欄のときに実際に使われる値の説明
    default_hint: str = ""
    #: 変更に再起動が必要か
    restart: bool = False
    #: 空を許さないか
    required: bool = False
    #: int の範囲
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    #: dir/file のとき、書込できる必要があるか
    need_write: bool = False
    #: このフォルダーの中に存在すべきファイル名（aim_ref_path 用）
    must_contain: str = ""


GROUP_REF = "参照先（外部ファイル）"
GROUP_MASTER = "マスタの差し替え"
GROUP_OUTPUT = "出力先"
GROUP_LABEL = "ラベル印刷"
GROUP_BEHAVIOR = "動作（Access(.accdb) → SQLite(.sqlite3) 移行）"
GROUP_STARTUP = "起動（変更後に再起動が必要）"

#: 画面に出す設定の一覧。ここへ 1 行足せば画面にも出る。
SETTINGS: List[SettingSpec] = [
    # ---------------- 参照先 ----------------
    SettingSpec(
        key="aim_ref_path", label="資材マスタの参照先フォルダー", kind="dir",
        group=GROUP_REF, must_contain=ACCDB_NAME,
        placeholder=r"\\サーバー名\共有\...",
        help=("VBA の定数 PATH_AIM_参照 に相当。"
              "このフォルダーの直下に " + ACCDB_NAME + " があること。"
              "空欄のままだと Access を読めず、CSV（検証用）で動作する。"),
        default_hint="CSV へフォールバック"),
    SettingSpec(
        key="debug_print_path", label="共有ログの出力先フォルダー", kind="dir",
        group=GROUP_REF, need_write=True,
        placeholder=r"\\サーバー名\共有\DebugPrint",
        help=("VBA の定数 PATH_梱包_日報DebugPrint に相当。"
              "空欄ならローカル領域の logs のみに出力する。"),
        default_hint="ローカルの logs のみ"),

    # ---------------- マスタ ----------------
    SettingSpec(
        key="size_master_file", label="サイズ構成マスタ（JSON）", kind="file",
        group=GROUP_MASTER, placeholder=r"C:\...\size_master.json",
        help=("VBA の GetSizeConfig に相当。サイズ・型番・データ行・巾の定義。"
              "空欄ならアプリ同梱のものを使う。"),
        default_hint="同梱 data/size_master.json"),
    SettingSpec(
        key="material_db_file", label="梱包資材マスタ（SQLite）", kind="file",
        group=GROUP_MASTER, placeholder=r"\\サーバー名\共有\梱包資材マスタ.sqlite3",
        help=("設定すると **Access より優先** して読む（読取専用で開く）。"
              "テーブル 資材重量 の 梱包資材名 / 単位質量 / 係数 を使う。"
              "Access からの移行先。"),
        default_hint="未設定（Access → CSV の順に読む）"),
    SettingSpec(
        key="material_csv_file", label="資材マスタ CSV（Access の代替）", kind="file",
        group=GROUP_MASTER, placeholder=r"C:\...\資材重量.csv",
        help=("Access へ到達できないときに使う代替。"
              "列は 管理番号 / 梱包資材名 / 単位質量 / 係数。"),
        default_hint="同梱 data/資材重量.csv"),
    SettingSpec(
        key="kataban_csv_file", label="型番マスタ CSV", kind="file",
        group=GROUP_MASTER, placeholder=r"C:\...\型番.csv",
        help="全サイズ画面のサイズ一覧。VBA の 型番 シートに相当。",
        default_hint="同梱 data/型番.csv"),

    # ---------------- ラベル印刷 ----------------
    SettingSpec(
        key="label_stock_file", label="ラベル台紙の寸法定義（JSON）", kind="file",
        group=GROUP_LABEL, placeholder=r"C:\...\label_stock.json",
        help=("原点・ピッチ・ラベル寸法・ラベル内の配置。"
              "空欄ならアプリ同梱のものを使う。"),
        default_hint="同梱 data/label_stock.json"),
    SettingSpec(
        key="label_stock_id", label="使う台紙の id", kind="text",
        group=GROUP_LABEL, placeholder="tesla-default",
        help="定義ファイルに複数の台紙がある場合に選ぶ。空欄なら先頭。",
        default_hint="定義ファイルの先頭"),
    SettingSpec(
        key="barcode_mode", label="バーコードの出し方", kind="text",
        group=GROUP_LABEL, placeholder="svg",
        help=("svg = 自前でバーを描く（既定・フォント不要）。"
              "font = バーコードフォントを使う（現行 Excel と同じ字形）。"
              "font にする場合は assets/fonts/ に TTF を置き、"
              "そのフォントが読取機で実績のあるものか確認すること。"),
        default_hint="svg"),
    SettingSpec(
        key="barcode_font_family", label="バーコードフォント名", kind="text",
        group=GROUP_LABEL, placeholder="K's BarCodeFont Code39",
        help="font モードのときに使う font-family。",
        default_hint="K's BarCodeFont Code39"),
    SettingSpec(
        key="label_offset_x_mm", label="印刷位置の補正 X（mm）", kind="float",
        group=GROUP_LABEL, minimum=-50, maximum=50,
        help=("印刷が左へずれていれば + を入れる（右へ動く）。0.1mm 単位。"
              "ラベル台紙 → 位置合わせ の画面からも押すだけで入れられる。")),
    SettingSpec(
        key="label_offset_y_mm", label="印刷位置の補正 Y（mm）", kind="float",
        group=GROUP_LABEL, minimum=-50, maximum=50,
        help=("印刷が上へずれていれば + を入れる（下へ動く）。0.1mm 単位。"
              "ラベル台紙 → 位置合わせ の画面からも押すだけで入れられる。")),
    SettingSpec(
        key="label_scale_pct", label="印刷倍率（%）", kind="float",
        group=GROUP_LABEL, minimum=50, maximum=150,
        help=("印刷が縮む端末で使う（ブラウザーが用紙に合わせて縮める場合）。"
              "自分で割り算をしなくても、ラベル台紙 → 位置合わせ の画面で"
              "測った mm を入れれば、ここへ自動で入る。")),

    # ---------------- 出力 ----------------
    SettingSpec(
        key="export_dir_path", label="帳票の保存先フォルダー", kind="dir",
        group=GROUP_OUTPUT, need_write=True,
        placeholder=r"C:\...\export",
        help="サーバー側へ保存する場合の出力先。ブラウザーのダウンロードは使わない。",
        default_hint="ローカル領域の work/export"),

    # ---------------- 動作 ----------------
    SettingSpec(
        key="prefer_access", label="旧 Access(.accdb) も試す（移行中のみ）",
        kind="bool", group=GROUP_BEHAVIOR,
        help=("資材マスタは **SQLite(.sqlite3) が本命** で、設定されていれば"
              "この項目に関係なく先に読みます。"
              "ここをオンにすると、SQLite が読めなかったときに限り"
              "旧 Access(.accdb) も試します。"
              "オフにすると Access を飛ばして CSV（検証用）へ落ちます。"),
        default_hint="SQLite → (ここがオンなら Access) → CSV の順"),
    SettingSpec(
        key="access_timeout_sec", label="旧 Access 読取のタイムアウト（秒）",
        kind="int", group=GROUP_BEHAVIOR, minimum=5, maximum=600,
        help=("上をオンにしているときだけ使います。"
              "cscript 経由の読取を打ち切るまでの時間。")),
    SettingSpec(
        key="material_refresh_sec", label="資材マスタの鮮度チェック間隔（秒）",
        kind="int", group=GROUP_BEHAVIOR, minimum=0, maximum=86400,
        help=("0 で毎回チェックする。業務中にマスタが更新されても"
              "古い単重で計算し続けないための設定。")),
    SettingSpec(
        key="db_idle_close_sec", label="状態DB の接続を閉じるまでの無操作時間（秒）",
        kind="int", group=GROUP_BEHAVIOR, minimum=0, maximum=86400,
        help=("0 で閉じない。ツールを起動したまま放置したときに "
              "DB ファイルのハンドルを解放する。")),

    # ---------------- 起動 ----------------
    SettingSpec(
        key="port", label="使用ポート", kind="int", group=GROUP_STARTUP,
        minimum=1024, maximum=65535, restart=True, required=True,
        help="他のアプリと重なる場合に変更する。"),
    SettingSpec(
        key="db_busy_timeout_ms", label="状態DB のロック待ち時間（ミリ秒）",
        kind="int", group=GROUP_STARTUP, minimum=1000, maximum=120000,
        restart=True, help="接続時に適用するため、変更後は再起動が必要。"),
]

SETTINGS_BY_KEY = {s.key: s for s in SETTINGS}

#: 統合版(コイル梱包ツールの中で動くとき)は、この画面で扱わない項目と、その理由。
#: 使用ポートは統合アプリが決める(`modules/packing_pena_label/server.py` が要求のたびに
#: 統合アプリのポートを入れ直す)。画面に残すと、変えても効かず、由来の表示も誤る
#: (移植漏れの点検で見つかった)。単体で動かす試験(入口なし)では従来どおり扱う
INTEGRATED_OWNED = {
    "port": "使用ポートはコイル梱包ツール(統合アプリ)の config/app.json で決まります。",
}


def integrated(cfg) -> bool:
    """統合版の中で動いているか(入口 `/pena` が付いている)。"""
    return bool(getattr(cfg, "url_prefix", ""))

#: パス系の変更に要る合言葉。
#:
#: 参照先を書き換えると**全員の計算に影響する**（別のマスタを読み始める）。
#: 現場で誤って触られないよう、パス系だけ合言葉を求める。
#: 秘密を守る仕組みではなく、**うっかり防止**のための手掛かり。
PATH_PASSWORD = "nisk"

#: 合言葉が要る種別（フォルダー・ファイルのパス）
PATH_KINDS = frozenset({"dir", "file"})


def is_path_key(key: str) -> bool:
    """その設定がパス系（合言葉が要る）か。"""
    spec = SETTINGS_BY_KEY.get(key)
    return bool(spec and spec.kind in PATH_KINDS)


def path_keys() -> List[str]:
    return [s.key for s in SETTINGS if s.kind in PATH_KINDS]


def changed_path_keys(values: Dict[str, Any], cfg) -> List[str]:
    """送られてきた値のうち、**実際に変わる**パス系の項目。

    値が今と同じなら合言葉は要らない（保存ボタンを押しただけのとき）。
    """
    out = []
    for key, raw in (values or {}).items():
        if not is_path_key(key):
            continue
        now = getattr(cfg, key, "")
        if str(raw or "").strip() != str(now or "").strip():
            out.append(key)
    return out


def password_ok(given: str) -> bool:
    return str(given or "") == PATH_PASSWORD
GROUP_ORDER = [GROUP_REF, GROUP_MASTER, GROUP_LABEL, GROUP_OUTPUT,
               GROUP_BEHAVIOR, GROUP_STARTUP]


# ============================================================ 検証
@dataclass
class CheckResult:
    ok: bool
    level: str          # ok / warn / error
    message: str


def check_path(spec: SettingSpec, value: str) -> CheckResult:
    """パスの実在・種別・書込可否を確かめる。"""
    v = (value or "").strip()
    if not v:
        if spec.required:
            return CheckResult(False, "error", "必須です")
        return CheckResult(True, "warn", "未設定（%s）" % (spec.default_hint or "既定値"))

    if not os.path.exists(v):
        return CheckResult(False, "error", "見つかりません")

    if spec.kind == "dir":
        if not os.path.isdir(v):
            return CheckResult(False, "error", "フォルダーではありません")
        if spec.must_contain:
            target = os.path.join(v, spec.must_contain)
            if not os.path.exists(target):
                return CheckResult(False, "error",
                                   "このフォルダーに %s がありません" % spec.must_contain)
        if spec.need_write and not os.access(v, os.W_OK):
            return CheckResult(False, "warn", "書込できません（読取のみ）")
        return CheckResult(True, "ok", "フォルダーを確認しました")

    if not os.path.isfile(v):
        return CheckResult(False, "error", "ファイルではありません")
    if not os.access(v, os.R_OK):
        return CheckResult(False, "error", "読取できません")
    return CheckResult(True, "ok", "ファイルを確認しました")


def coerce(spec: SettingSpec, raw: Any) -> Any:
    """画面から来た値を設定の型へ直す。範囲外・型違いは ValueError。"""
    if spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).lower() in ("1", "true", "on", "yes")

    if spec.kind == "float":
        t = str(raw).strip()
        if t == "":
            raise ValueError("数値を入力してください")
        try:
            f = float(t)
        except ValueError:
            raise ValueError("数値を入力してください")
        if spec.minimum is not None and f < spec.minimum:
            raise ValueError("%s 以上にしてください" % spec.minimum)
        if spec.maximum is not None and f > spec.maximum:
            raise ValueError("%s 以下にしてください" % spec.maximum)
        return f

    if spec.kind == "int":
        s = str(raw).strip()
        if s == "":
            raise ValueError("数値を入力してください")
        try:
            n = int(float(s))
        except ValueError:
            raise ValueError("数値を入力してください")
        if spec.minimum is not None and n < spec.minimum:
            raise ValueError("%d 以上にしてください" % spec.minimum)
        if spec.maximum is not None and n > spec.maximum:
            raise ValueError("%d 以下にしてください" % spec.maximum)
        return n

    s = "" if raw is None else str(raw).strip()
    # 末尾の区切り記号は落としておく（\\server\share\ のような入力に備える）
    if spec.kind == "dir" and len(s) > 3:
        s = s.rstrip("\\/") or s
    if spec.required and s == "":
        raise ValueError("必須です")
    return s


def validate_all(values: Dict[str, Any]) -> Dict[str, str]:
    """保存前の検証。``{キー: エラー文}``（空なら問題なし）。"""
    errors: Dict[str, str] = {}
    for key, raw in values.items():
        spec = SETTINGS_BY_KEY.get(key)
        if spec is None:
            errors[key] = "不明な設定です"
            continue
        try:
            coerce(spec, raw)
        except ValueError as exc:
            errors[key] = str(exc)
    return errors


# ============================================================ 画面用
def describe(cfg: Config) -> List[dict]:
    """設定画面へ渡す一覧を組み立てる。"""
    overrides = load_local_overrides(cfg)
    sources = getattr(cfg, "sources", {}) or {}
    out = []
    for spec in SETTINGS:
        if integrated(cfg) and spec.key in INTEGRATED_OWNED:
            continue
        value = getattr(cfg, spec.key, "")
        layer = sources.get(spec.key, "default")
        item = {
            "key": spec.key, "label": spec.label, "kind": spec.kind,
            "group": spec.group, "help": spec.help,
            "placeholder": spec.placeholder, "defaultHint": spec.default_hint,
            "restart": spec.restart, "required": spec.required,
            "value": value,
            "layer": layer,
            "layerLabel": LAYER_LABEL.get(layer, layer),
            "overridden": spec.key in overrides,
        }
        if spec.kind in ("dir", "file"):
            r = check_path(spec, str(value))
            item["check"] = {"ok": r.ok, "level": r.level, "message": r.message}
            item["resolved"] = _resolved_path(cfg, spec.key)
        out.append(item)
    return out


def _resolved_path(cfg: Config, key: str) -> str:
    """空欄のときに実際に使われるパスを見せる。"""
    mapping = {
        "size_master_file": cfg.size_master_path,
        "material_csv_file": cfg.material_csv_path,
        "kataban_csv_file": cfg.kataban_csv_path,
        "export_dir_path": cfg.export_dir,
        "aim_ref_path": cfg.accdb_path,
        "material_db_file": cfg.material_db_file,
        "label_stock_file": cfg.label_stock_path,
    }
    return mapping.get(key, str(getattr(cfg, key, "")))


# ============================================================ 保存・反映
def save(values: Dict[str, Any], cfg: Config) -> Dict[str, Any]:
    """検証して端末ごとの設定へ保存する。

    戻り値には「再起動が必要な項目」を入れて、画面で知らせる。
    統合版では、統合アプリが決める項目(`INTEGRATED_OWNED`)は受け取っても保存しない。
    """
    if integrated(cfg):
        values = {k: v for k, v in values.items() if k not in INTEGRATED_OWNED}
    errors = validate_all(values)
    if errors:
        return {"ok": False, "level": "warn", "errors": errors,
                "message": "入力を確認してください"}

    current = load_local_overrides(cfg)
    changed: List[str] = []
    restart: List[str] = []

    # 「既定と同じか」は **同梱の既定（app.json まで）** と比べる。
    # 現在有効な値（＝端末の上書き込み）と比べると、設定画面は毎回
    # 全項目を送るため、同じ内容でもう一度保存しただけで
    # 上書きがすべて消えて同梱の既定へ戻ってしまう。
    from ..config import load_base_config
    # 読み直しは cfg と同じ app.json を見る（検証時の差し替えに追従するため）
    base = load_base_config(getattr(cfg, "app_config_used", "") or "")

    for key, raw in values.items():
        spec = SETTINGS_BY_KEY[key]
        new = coerce(spec, raw)
        effective = getattr(cfg, key, None)
        default = getattr(base, key, None)
        if new == default:
            # 同梱の既定と同じ値なら、端末側の上書きは残さない
            if key in current:
                changed.append(spec.label)
                if spec.restart:
                    restart.append(spec.label)
            current.pop(key, None)
            continue
        current[key] = new
        if new != effective:
            changed.append(spec.label)
            if spec.restart:
                restart.append(spec.label)

    path = save_local_overrides(current, cfg)

    # パスの実在は「保存を止める理由」にはしない。
    # 共有フォルダーが一時的に落ちていても設定はできるべきなので、
    # 保存はしたうえで警告として返す。
    warnings: List[str] = []
    for key, raw in values.items():
        spec = SETTINGS_BY_KEY[key]
        if spec.kind not in ("dir", "file"):
            continue
        r = check_path(spec, str(coerce(spec, raw)))
        if not r.ok and r.level == "error":
            warnings.append("%s: %s" % (spec.label, r.message))

    msg = ("変更はありません" if not changed
           else "保存しました（%d 項目）" % len(changed))
    if warnings:
        msg += " ※到達できないパスがあります"

    return {"ok": True, "level": "warn" if warnings else "info", "path": path,
            "changed": changed, "restart": restart, "warnings": warnings,
            "message": msg}


def reset(cfg: Config) -> Dict[str, Any]:
    """端末ごとの上書きを消して同梱の既定へ戻す。"""
    removed = clear_local_overrides(cfg)
    return {"ok": True, "level": "info",
            "message": ("同梱の既定へ戻しました。再起動すると反映されます。"
                        if removed else "この端末の上書きはありません")}


def apply_live(cfg: Config, ctx) -> List[str]:
    """再起動せずに反映できるものを、動いているオブジェクトへ適用する。

    戻り値は反映した内容の説明。
    """
    applied: List[str] = []

    # 資材マスタの参照先・読み方
    ctx.materials.reconfigure(
        accdb_dir=cfg.aim_ref_path,
        csv_path=cfg.material_csv_path,
        prefer_access=cfg.prefer_access,
        timeout_sec=cfg.access_timeout_sec,
        refresh_sec=cfg.material_refresh_sec,
        db_path=cfg.material_db_file)
    applied.append("資材マスタの参照先")

    # サイズ構成マスタ
    if ctx.master.reload(cfg.size_master_path):
        applied.append("サイズ構成マスタ")

    # 型番マスタ
    if ctx.wf.all_size.reload_master(cfg.kataban_csv_path):
        applied.append("型番マスタ")

    # 状態DB のアイドル解放
    ctx.store.set_idle_close_sec(cfg.db_idle_close_sec)
    applied.append("状態DB のアイドル解放")

    return applied
