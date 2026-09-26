"""配布設定 ── 1台で決めた設定を、配った先の端末でそのまま使う

梱包資材総合ツールの配布設定(`packaging_tool/distribution.py`)を移したもの。

【なぜ要るのか】
置き場所(仕掛台帳・梱包課共有・梱包資材マスタ)は端末ごとの
`user_config.json` に入ります。配った先で1台ずつ設定画面を開いて打ち直すのは
手間で、打ち間違えると**別のファイルを読む端末**ができてしまいます。

【流れ】
    1. 1台で起動して設定し、設定画面の「配布設定」で書き出す
    2. アプリのフォルダの直下に `配布設定\\` ができる
    3. フォルダごと配る(手元のDB・端末ごとの設定・ログは、もともとアプリの
       フォルダの外 ── `%LOCALAPPDATA%\\PackingDetails` ── にあるので紛れない。
       `export\\`(書き出した CSV)はアプリのフォルダにあるので、配る前に外す)
    4. 配った先は起動したとき `配布設定\\` を見つけて読み込む

【`配布設定\\` の中身】

    配布設定\\
      設定.json          置き場所3つ・起動時の自動取り込み
      はじめに読む.txt   何が入っているか・配った先で何が起きるか

【総合ツールと違うところ】
- **管理者パスワードと紙面の右上の文字は入れません。** このアプリでは
  どちらも**全ラインで共有**(梱包資材マスタのフォルダ)しているので、
  梱包資材マスタのフォルダを配れば、配った先も同じものを使います
- 配置図・拠点はこのアプリにありません

【読み込むときの決まり】(総合ツールと同じ)**その端末にすでにある設定は
読み込みません。** 値が入っている項目はそのまま、無い項目だけ埋めます。
**空の値は「無い」と同じに扱います**(総合ツールと違うところ)── 以前の設定画面は
「保存して取り込み」を押すたびに空の欄も空のまま保存していたので、空を「ある」と
見ると、配った設定がその端末では黙って効かない。
起動のたびに見に行きますが、埋まった項目は次から「すでにある」ので、端末で
直した値が戻されることはありません。狙って揃えたいときは、設定画面の
「配布設定を読み込み直す」(管理者パスワード)で上書きします。

【パスワード】書き出す・消す・読み込み直すには管理者パスワードが要ります。
起動時の読み込みには要りません ── `配布設定\\` を置いたのは、フォルダを
配った管理者本人だからです。
"""
from __future__ import annotations

import json
import os
import platform
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from common import dist_settings as _dist_settings

from . import admin_password, app_config, config, user_settings
from .logging_utils import get_logger

log = get_logger("distribution")

# `配布設定\\` の置き場所。**統合版では統合アプリのフォルダの直下
# `配布設定\\packing_details\\`**(機能ごとに分ける。設定の鍵が機能ごとに違うため)
DIR = Path(os.environ.get("PACKING_DETAILS_DISTRIBUTION_DIR",
                          str(_dist_settings.default_dir("packing_details"))))
SETTINGS_NAME = "設定.json"
README_NAME = "はじめに読む.txt"

FORMAT = 1

# この端末が最後に読み込んだとき(設定画面に出すだけ)
KEY_APPLIED = "distribution_applied"

# 入れられるもの: (鍵, 画面の名前, 既定で入れるか)。鍵は `user_config.json` の鍵そのもの
ITEMS: tuple[tuple[str, str, bool], ...] = (
    (config.KEY_LOT_DB_DIR, "仕掛台帳のフォルダ", True),
    (config.KEY_KONPO_DB_DIR, "梱包課共有の仕掛フォルダ", True),
    (config.KEY_SHARED_DIR, "梱包資材マスタのフォルダ（共有）", True),
    (config.KEY_AUTO_IMPORT, "起動時の自動取り込み", True),
)
ITEM_KEYS = frozenset(key for key, _, _ in ITEMS)
ITEM_LABELS = {key: label for key, label, _ in ITEMS}

# 入れないものと、その理由(画面に出す。**黙って外さない**)
NOT_INCLUDED: tuple[tuple[str, str], ...] = (
    ("管理者パスワード", "全ラインで共有しています（梱包資材マスタのフォルダ）。"
                        "そのフォルダを配れば、配った先も同じパスワードです"),
    ("紙面の右上の文字", "同じく全ラインで共有しています"),
)

# 項目ごとの値の形。**手で直した 設定.json の型違いを通さない**(文字の "false" を
# 自動取り込みに入れると、`bool("false")` で「する」になる)
_PATH_KEYS = frozenset({config.KEY_LOT_DB_DIR, config.KEY_KONPO_DB_DIR, config.KEY_SHARED_DIR})
_BOOL_KEYS = frozenset({config.KEY_AUTO_IMPORT})

REFUSE_NEED_PASSWORD = "need_password"
REFUSE_BAD_INPUT = "bad_input"
REFUSE_FAILED = "failed"


def settings_path(base: Optional[Path] = None) -> Path:
    return (base or DIR) / SETTINGS_NAME


def is_set(value: Any) -> bool:
    """その端末で値が入っているか。**空の文字は入っていない**(既定のまま)と同じ。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _check(key: str, value: Any) -> str:
    """値の形が合っていなければ理由。合っていれば空。"""
    if key in _PATH_KEYS:
        if not isinstance(value, str):
            return "フォルダは文字で書いてください"
        if not value.strip():
            return "空です（既定のままにするなら、項目ごと消してください）"
    if key in _BOOL_KEYS and not isinstance(value, bool):
        return "true か false で書いてください（引用符なし）"
    return ""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
    # 読まなかった項目と理由(形が違う)。**黙って捨てない**(画面とログに出す)
    skipped: list[str] = field(default_factory=list)


def _load() -> tuple[Optional[Bundle], str]:
    """置いてある配布設定と、読めないときの理由。どちらも無ければ `(None, "")`。"""
    path = settings_path()
    if not path.is_file():
        if DIR.is_dir():
            return None, f"{DIR.name} フォルダはありますが、{SETTINGS_NAME} がありません。"
        if settings_path(_previous()).is_file():
            return None, (f"前回の書き出しが途中で止まりました。前の配布設定は {_previous()} に"
                          "残っています。もう一度「配布設定を書き出す」を押してください。")
        return None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return None, f"{SETTINGS_NAME} を読めません（{exc}）。書き出し直してください。"
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None, (f"{SETTINGS_NAME} の形が違います（format が {FORMAT} ではありません）。"
                      "書き出し直してください。")
    raw = data.get("settings") or {}
    if not isinstance(raw, dict):
        return None, f"{SETTINGS_NAME} の settings の形が違います。書き出し直してください。"
    settings: dict[str, Any] = {}
    skipped: list[str] = []
    for key, value in raw.items():
        if key not in ITEM_KEYS:
            continue                                        # 知らない鍵は捨てる
        why = _check(key, value)
        if why:
            skipped.append(f"{ITEM_LABELS[key]}: {why}")
            continue
        settings[key] = value.strip() if isinstance(value, str) else value
    if not settings:
        return None, ("入っている項目がありません"
                      + (f"（読まなかったもの: {' / '.join(skipped)}）" if skipped else "")
                      + "。書き出し直してください。")
    return Bundle(settings=settings, created_at=str(data.get("created_at", "")),
                  created_on=str(data.get("created_on", "")), skipped=skipped), ""


def read() -> Optional[Bundle]:
    """置いてある配布設定。無い・読めない・形が違うなら None(理由はログへ)。"""
    bundle, problem = _load()
    if problem:
        log.warning("配布設定を読みません: %s", problem)
    elif bundle is not None and bundle.skipped:
        log.warning("配布設定の一部を読みません: %s", " / ".join(bundle.skipped))
    return bundle


def summary() -> dict[str, Any]:
    """設定画面に出す、配布設定のいま。"""
    bundle, problem = _load()
    applied = user_settings.get(KEY_APPLIED)
    current = user_settings.load_all()
    out: dict[str, Any] = {
        "exists": bundle is not None,
        # 置いてあるのに読めない(壊れた・形が違う)。**「まだありません」と言わない**
        "problem": problem,
        "skipped": bundle.skipped if bundle is not None else [],
        "path": str(DIR),
        "items": [{"key": k, "label": label, "default": default,
                   "value": _show(current[k]) if is_set(current.get(k)) else "（既定のまま）",
                   "set": is_set(current.get(k))}
                  for k, label, default in ITEMS],
        "not_included": [{"label": label, "why": why} for label, why in NOT_INCLUDED],
        "applied_at": applied.get("at", "") if isinstance(applied, dict) else "",
        "contents": [],
        "created_at": "",
        "created_on": "",
    }
    if bundle is None:
        return out
    out.update(contents=[{"label": ITEM_LABELS[key], "value": _show(value)}
                         for key, value in bundle.settings.items()],
               created_at=bundle.created_at, created_on=bundle.created_on)
    return out


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "する" if value else "しない"
    text = str(value)
    return text if text else "（既定）"


# ------------------------------------------------------------------
# 書き出す(配る側)
# ------------------------------------------------------------------
def export(password: str, items: list[str]) -> Result:
    """**この端末のいまの設定**を `配布設定\\` に書き出す(前の中身は置き換える)。

    この端末で一度も変えていない項目は入れない ── 配った先も同じ既定で
    動くので要らない(空で上書きしないためにも入れない)。
    """
    if not admin_password.verify(str(password or "")):
        return Result(False, "配布設定を書き出すには管理者パスワードが要ります。",
                      REFUSE_NEED_PASSWORD)
    unknown = [k for k in items if k not in ITEM_KEYS]
    if unknown:
        return Result(False, f"知らない項目です: {', '.join(unknown)}", REFUSE_BAD_INPUT)
    if not items:
        return Result(False, "入れる項目を1つ以上選んでください。", REFUSE_BAD_INPUT)

    current = user_settings.load_all()
    settings = {k: current[k] for k in items if is_set(current.get(k))}
    defaults = [ITEM_LABELS[k] for k in items if not is_set(current.get(k))]
    if not settings:
        return Result(False, "書き出せる設定がありません（" + "・".join(defaults)
                      + " はこの端末で変えていないため既定のままです）。", REFUSE_BAD_INPUT)

    meta = {"format": FORMAT, "created_at": _now(),
            "created_on": platform.node(), "settings": settings}
    try:
        _replace(meta)
    except OSError as exc:
        return Result(False, f"{DIR} に書けませんでした（前の配布設定はそのままです）: {exc}",
                      REFUSE_FAILED)
    _mark_applied()

    names = [ITEM_LABELS[k] for k in settings]
    message = (f"配布設定を書き出しました（{len(names)}項目）。アプリのフォルダの"
               f"「{_dist_settings.where(DIR)}」に入っています。アプリのフォルダごと配ってください。")
    if defaults:
        message += (" 既定のままなので入れていないもの（配った先も既定で動きます）: "
                    + "・".join(defaults) + "。")
    log.info("配布設定を書き出しました: %s", ", ".join(names))
    return Result(True, message, applied=names)


def _replace(meta: dict[str, Any]) -> None:
    """`配布設定\\` を入れ替える。**失敗しても前の配布設定を壊さない。**

    作ってから(`.作成中`)、前のものを名前を変えてよけ(`.前`)、新しいものを
    置き、よけたものを消す。途中で失敗したら前のものを戻す ── 前のものを
    先に消してしまうと、消しきれなかったとき(フォルダを開いている等)に
    前も今も無い状態が残る。
    """
    staging = _staging()
    previous = _previous()
    # 前回ここで止まって、前のものが `.前` にだけ残っていたら、まず戻す
    # (戻さずに `.前` を消すと、前も今も無くなる)
    if not DIR.exists() and previous.exists():
        previous.rename(DIR)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True)
        settings_path(staging).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (staging / README_NAME).write_text(_readme(meta), encoding="utf-8-sig")
        shutil.rmtree(previous, ignore_errors=True)
        moved = False
        try:
            if DIR.exists():
                DIR.rename(previous)
                moved = True
            staging.rename(DIR)
        except OSError as exc:
            if moved and not DIR.exists():
                try:
                    previous.rename(DIR)                 # 前のものを戻す
                except OSError as again:
                    raise OSError(f"{exc}。前の配布設定は {previous} に残っています"
                                  f"（戻せませんでした: {again}）") from again
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)


def _staging() -> Path:
    return DIR.with_name(DIR.name + ".作成中")


def _previous() -> Path:
    return DIR.with_name(DIR.name + ".前")


def _readme(meta: dict[str, Any]) -> str:
    lines = [
        f"{app_config.display_name()} 配布設定",
        "",
        f"作成: {meta['created_at']}（{meta['created_on']}）",
        "",
        "このフォルダに入っているもの（設定.json）",
    ]
    for key, value in meta["settings"].items():
        lines.append(f"  {ITEM_LABELS[key]}: {_show(value)}")
    lines += [
        "",
        "入れていないもの",
    ]
    for label, why in NOT_INCLUDED:
        lines.append(f"  {label}: {why}")
    lines += [
        "",
        "配った先で起きること",
        "  起動したときにこのフォルダを読み込みます。",
        "  その端末にすでにある設定は、読み込みません（上書きしない）。",
        "  揃えたいときは、設定画面の「配布設定」→「配布設定を読み込み直す」。",
        "",
    ]
    return "\r\n".join(lines)


def remove(password: str) -> Result:
    if not admin_password.verify(str(password or "")):
        return Result(False, "配布設定を消すには管理者パスワードが要ります。",
                      REFUSE_NEED_PASSWORD)
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
    user_settings.save(KEY_APPLIED, {"at": _now()})


def _apply(bundle: Bundle, *, overwrite: bool) -> Result:
    result = Result()
    current = user_settings.load_all()
    for key, value in bundle.settings.items():
        # **空の値は「無い」と同じ**(以前の画面が空のまま保存していた端末でも効かせる)
        if is_set(current.get(key)) and not overwrite:
            result.kept.append(ITEM_LABELS[key])        # すでにある
            continue
        if user_settings.save(key, value):
            result.applied.append(ITEM_LABELS[key])
    if result.applied:
        _mark_applied()
    return result


def apply_on_start() -> Result:
    """起動時に呼ぶ。`配布設定\\` があれば、**その端末に無いものだけ**読む。"""
    bundle = read()
    if bundle is None:
        return Result(True, "")
    result = _apply(bundle, overwrite=False)
    if result.applied:
        log.info("配布設定を読み込みました: %s（すでにあったので読まなかったもの: %s）",
                 ", ".join(result.applied), ", ".join(result.kept) or "なし")
        result.message = "配布設定を読み込みました"
    return result


def reapply(password: str) -> Result:
    """設定画面から。**すでにあるものも上書きして**読み込み直す。"""
    if not admin_password.verify(str(password or "")):
        return Result(False, "配布設定を読み込み直すには管理者パスワードが要ります。",
                      REFUSE_NEED_PASSWORD)
    bundle, problem = _load()
    if bundle is None:
        return Result(False, problem or "配布設定が置かれていません。", REFUSE_BAD_INPUT)
    result = _apply(bundle, overwrite=True)
    result.message = (f"配布設定を読み込みました（{len(result.applied)}項目: "
                      f"{'・'.join(result.applied)}）。置き場所を変えたときは、"
                      "「置き場所・取り込み」で取り込み直してください。")
    if bundle.skipped:
        result.message += f" 形が違うので読まなかったもの: {' / '.join(bundle.skipped)}。"
    log.info("配布設定を読み込み直しました: %s", ", ".join(result.applied))
    return result
