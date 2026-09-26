"""配布設定 ── 1台で決めた設定を、配った先の端末でそのまま使う

姉妹ツール python-web-tools の `packaging_tool/distribution.py` を移植したもの。
考え方・フォルダの形・読み込むときの決まりは同じにしてある(両方を配る
管理者が、同じ手順で扱えるように)。違うのは次の2つだけ。

    - 配置図は無い(このツールは配置図を持たない)
    - パスワードは**設定画面のいちばん上の認証**で通す(このツールの
      置き場所・マスタと同じ関門。面ごとにパスワード欄を置かない)

【なぜ要るのか】
設定(取り込み元の置き場所・書き出し先・自動取り込み・管理者パスワード)は
端末ごと・利用者ごとの `user_config.json` に入ります(`config.USER_CONFIG_PATH`)。
配った先で1台ずつ設定画面を開いて打ち直すのは手間で、打ち間違えると
**別のファイルを読む端末**ができてしまいます(置き場所は取り込みの相手そのもの)。

【流れ】
    1. 1台で起動して設定し、設定画面の「配布設定」で書き出す
    2. ツールのフォルダの直下に `配布設定\\` ができ、配るものが全部そこに入る
    3. フォルダごと配る
    4. 配った先は起動したとき `配布設定\\` を見つけて読み込む

【`配布設定\\` の中身】**配布先に関わるものはここだけ**に置きます。

    配布設定\\
      設定.json            置き場所・書き出し先・自動取り込み・発注票の定尺・
                           管理者パスワード(撹拌した値)・ライン(選んだときだけ)
      はじめに読む.txt     何が入っているか・配った先で何が起きるか

【読み込むときの決まり】**その端末にすでにある設定は読み込みません。**
その端末で値が入っている項目はそのまま。無い項目だけ埋めます。

起動のたびに見に行きますが、埋まった項目は次から「すでにある」ので、
端末で直した値が戻されることはありません。狙って揃えたいときは、
設定画面の「配布設定を読み込み直す」(認証が要る)で上書きします。

【パスワード】書き出す・消す・読み込み直すには認証が要ります(呼ぶ側の
`app/routes/settings.py` が見る)。起動時の読み込みには要りません ──
`配布設定\\` を置いたのは、フォルダを配った管理者本人だからです。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from common import dist_settings as _dist_settings

from . import admin_password, config, user_settings
from .sqlite_toolkit import now_db_string
from .logging_utils import get_logger

log = get_logger("distribution")

# `配布設定\\` の置き場所。**統合版では統合アプリのフォルダの直下
# `配布設定\\packing_material_calculation\\`**(機能ごとに分ける。設定の鍵が機能ごとに違うため)
DIR = Path(os.environ.get("COIL_TOOL_DISTRIBUTION_DIR",
                          str(_dist_settings.default_dir("packing_material_calculation"))))
SETTINGS_NAME = "設定.json"
README_NAME = "はじめに読む.txt"

FORMAT = 1

# この端末が最後に読み込んだとき(設定画面に出すだけ)
KEY_APPLIED = "distribution_applied"

# 入れられるもの: (鍵, 画面の名前, 既定で入れるか)
#
# 鍵は `user_config.json` の鍵そのもの。画面の名前は設定画面の欄と揃える。
# **ラインだけは既定で外す**(端末ごとに LS4 / NS1 が違う)。
#
# **担当者は入れられない。** 人ごとのもので、入れたまま配ると全端末の
# 依頼者欄が同じ人になる(姉妹ツールの「拠点」より害が大きいので、
# 選べるようにもしない)。
ITEMS: tuple[tuple[str, str, bool], ...] = (
    (config.KEY_MASTER_DB_DIR, "梱包資材マスタのフォルダ", True),
    (config.KEY_LOT_DB_DIR, "仕掛台帳のフォルダ", True),
    (config.KEY_LOT_DB_DIR_2, "仕掛台帳の予備フォルダ", True),
    (config.KEY_EXPORT_DIR, "書き出し先", True),
    (config.KEY_AUTO_IMPORT, "起動時の自動取り込み", True),
    (config.KEY_STOCK_LENGTHS, "発注票の定尺", True),
    (admin_password.KEY, "管理者パスワード", True),
    (config.KEY_LINE, "ライン(端末ごとに違う)", False),
)
ITEM_KEYS = frozenset(key for key, _, _ in ITEMS)
ITEM_LABELS = {key: label for key, label, _ in ITEMS}

REFUSE_BAD_INPUT = "bad_input"
REFUSE_FAILED = "failed"


def settings_path(base: Optional[Path] = None) -> Path:
    return (base or DIR) / SETTINGS_NAME


@dataclass
class Result:
    ok: bool = True
    message: str = ""
    reason: str = ""
    applied: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)   # すでにあったので読まなかったもの


# ------------------------------------------------------------------
# 読む
# ------------------------------------------------------------------
@dataclass
class Bundle:
    """置いてある `配布設定\\` の中身。"""

    settings: dict[str, Any]
    created_at: str = ""
    created_on: str = ""


def read() -> Optional[Bundle]:
    """置いてある配布設定。無い・読めない・形が違う・空なら None。"""
    path = settings_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        log.warning("配布設定を読めませんでした: %s", exc)
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        log.warning("配布設定の形が違うため読みません: %s", path)
        return None
    raw = data.get("settings") or {}
    if not isinstance(raw, dict):
        log.warning("配布設定の形が違うため読みません: %s", path)
        return None
    settings = {k: v for k, v in raw.items() if k in ITEM_KEYS}   # 知らない鍵は捨てる
    if not settings:
        return None
    return Bundle(settings=settings,
                  created_at=str(data.get("created_at", "")),
                  created_on=str(data.get("created_on", "")))


def _show(key: str, value: Any) -> str:
    """画面とメモに出す形。**パスワードの値は出さない。**"""
    if key == admin_password.KEY:
        return "(設定済み)" if value else "(既定)"
    if isinstance(value, bool):
        return "する" if value else "しない"
    if isinstance(value, list):
        return " / ".join(str(v) for v in value)
    text = str(value)
    return text if text else "(既定)"


def summary() -> dict[str, Any]:
    """設定画面に出す、配布設定のいま。**パスワードの値は出さない。**

    `items` には、この端末でいま入っている値も添える ── この端末で
    変えていない項目は書き出しても入らない(既定のまま)ので、押す前に
    それが分かるように。
    """
    bundle = read()
    current = user_settings.load_all()
    applied = current.get(KEY_APPLIED)
    out: dict[str, Any] = {
        "exists": bundle is not None,
        "path": str(DIR),
        "items": [{"key": k, "label": label, "default": default,
                   "set": k in current,
                   "value": _show(k, current[k]) if k in current else ""}
                  for k, label, default in ITEMS],
        "applied_at": applied.get("at", "") if isinstance(applied, dict) else "",
        "contents": [],
        "created_at": "",
        "created_on": "",
    }
    if bundle is None:
        return out
    out.update(
        contents=[{"label": ITEM_LABELS[key], "value": _show(key, value)}
                  for key, value in bundle.settings.items()],
        created_at=bundle.created_at, created_on=bundle.created_on)
    return out


# ------------------------------------------------------------------
# 書き出す(配る側)
# ------------------------------------------------------------------
def export(items: list[str]) -> Result:
    """**この端末のいまの設定**を `配布設定\\` に書き出す(前の中身は置き換える)。

    この端末で一度も変えていない項目は入れない ── 配った先も同じ既定で
    動くので要らない(空で上書きしないためにも入れない)。
    """
    unknown = [k for k in items if k not in ITEM_KEYS]
    if unknown:
        return Result(False, f"知らない項目です: {', '.join(unknown)}",
                      REFUSE_BAD_INPUT)
    if not items:
        return Result(False, "入れる項目を1つ以上選んでください。", REFUSE_BAD_INPUT)

    current = user_settings.load_all()
    # 並びは ITEMS の順に揃える(選んだ順に左右されない)
    chosen = [k for k, _, _ in ITEMS if k in items]
    settings = {k: current[k] for k in chosen if k in current}
    defaults = [ITEM_LABELS[k] for k in chosen if k not in current]
    if not settings:
        return Result(False, "書き出せる設定がありません(" + "・".join(defaults)
                      + " はこの端末で変えていないため既定のままです)。",
                      REFUSE_BAD_INPUT)

    # **作ってから入れ替える。** 途中で失敗して、半分だけ新しい配布設定を
    # 残さない(配った先がそれを読んでしまう)
    staging = DIR.with_name(DIR.name + ".作成中")
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        meta = {"format": FORMAT, "created_at": now_db_string(),
                "created_on": platform.node(), "settings": settings}
        settings_path(staging).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / README_NAME).write_text(_readme(meta), encoding="utf-8-sig")
        if DIR.exists():
            shutil.rmtree(DIR)
        staging.rename(DIR)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return Result(False, f"{DIR} に書けませんでした: {exc}", REFUSE_FAILED)
    _mark_applied()

    names = [ITEM_LABELS[k] for k in settings]
    message = (f"配布設定を書き出しました({len(names)}項目)。ツールのフォルダの"
               f"「{_dist_settings.where(DIR)}」に入っています。ツールのフォルダごと配ってください"
               "(このフォルダも一緒に入ります)。")
    if defaults:
        message += (" 既定のままなので入れていないもの(配った先も既定で動きます): "
                    + "・".join(defaults) + "。")
    log.info("配布設定を書き出しました: %s", ", ".join(names))
    return Result(True, message, applied=names)


def _readme(meta: dict[str, Any]) -> str:
    lines = [
        "コイル梱包資材計算 配布設定",
        "",
        f"作成: {meta['created_at']}({meta['created_on']})",
        "",
        "このフォルダに入っているもの",
    ]
    for key, value in meta["settings"].items():
        lines.append(f"  {ITEM_LABELS[key]}: {_show(key, value)}")
    lines += [
        "",
        "配った先で起きること",
        "  起動したときにこのフォルダを読み込みます。",
        "  その端末にすでにある設定は、読み込みません(上書きしない)。",
        "  揃えたいときは、設定画面の「配布設定」→「配布設定を読み込み直す」。",
        "",
        "担当者は入っていません(人ごとに違うため)。配った先で選んでください。",
        "",
    ]
    return "\n".join(lines)


def remove() -> Result:
    try:
        if DIR.exists():
            shutil.rmtree(DIR)
    except OSError as exc:
        return Result(False, f"消せませんでした: {exc}", REFUSE_FAILED)
    log.info("配布設定を消しました")
    return Result(True, "配布設定を消しました。この端末の設定はそのままです。")


# ------------------------------------------------------------------
# 読み込む(配られた側)
# ------------------------------------------------------------------
def _mark_applied() -> None:
    user_settings.save(KEY_APPLIED, {"at": now_db_string()})


def _apply(bundle: Bundle, *, overwrite: bool) -> Result:
    result = Result()
    current = user_settings.load_all()
    values: dict[str, Any] = {}
    for key, value in bundle.settings.items():
        if key in current and not overwrite:
            result.kept.append(ITEM_LABELS[key])        # すでにある
            continue
        values[key] = value
    # **まとめて1回で書く**(半分だけ変わった端末を作らない)
    if values:
        if not user_settings.save_many(values):
            return Result(False, f"設定ファイルに書けません: {config.USER_CONFIG_PATH}",
                          REFUSE_FAILED, kept=result.kept)
        result.applied = [ITEM_LABELS[k] for k in values]
        _mark_applied()
    return result


def apply_on_start() -> Result:
    """起動時に呼ぶ。`配布設定\\` があれば、**その端末に無いものだけ**読む。"""
    bundle = read()
    if bundle is None:
        return Result(True, "")
    result = _apply(bundle, overwrite=False)
    if result.applied:
        log.info("配布設定を読み込みました: %s(すでにあったので読まなかったもの: %s)",
                 ", ".join(result.applied), ", ".join(result.kept) or "なし")
        result.message = "配布設定を読み込みました"
    return result


def reapply() -> Result:
    """設定画面から。**すでにあるものも上書きして**読み込み直す。"""
    bundle = read()
    if bundle is None:
        return Result(False, "配布設定が置かれていません。", REFUSE_BAD_INPUT)
    result = _apply(bundle, overwrite=True)
    if result.ok:
        result.message = f"配布設定を読み込みました({len(result.applied)}項目)"
    return result
