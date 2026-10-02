r"""配布設定(共通) ── ログの出力先・残す日数を、配った先でもそのまま使う(統合 1.0.14)

現場の指摘: 「ログの出力先を配布設定に入れてください」。

ログの出力先と残す日数は**3機能で1つ**(`common/local_settings.py`。このPCに保存)。
3機能の配布設定(`配布設定\<機能>\`)のどれかに入れると、どの機能の画面から
書き出すのか・どれが正なのかが分かれるので、**統合アプリの配布設定**を1つ足す:

    配布設定\
      common\                        共通(ログ)    ← ここ
        設定.json
        はじめに読む.txt
      packing_details\ …             3機能はこれまでどおり

【流れ】(3機能の配布設定と同じ)
    1. 1台で上の帯の「ログ」→「出力先の設定」で出力先を決めて保存する
    2. 同じ面の「配布設定に書き出す」(管理者パスワード)で `配布設定\common\` に書き出す
    3. アプリのフォルダごと配る(ペナラベルの「配布用フォルダを作る」も一緒に入れる)
    4. 配った先は起動したとき読み込む

【読み込むときの決まり】(3機能と同じ)
**そのPCですでに設定してある項目は読み込まない**(空は「無い」と同じ)。
揃えたいときは「配布設定を読み込み直す」(管理者パスワード)で上書きする。
読み込んだら、ログはすぐ新しい出力先へ出る(再起動は要らない)。

【パスワード】書き出す・消す・読み込み直すには**梱包明細の管理者パスワード**
(全ラインで共有)。照合は呼ぶ側(`app.py`)がする ── ここは機能に寄りかからない。
起動時の読み込みには要らない(`配布設定\` を置いたのは、フォルダを配った管理者本人)。
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

from . import dist_settings, local_settings

#: 書き出す場所。**統合アプリのフォルダの直下 `配布設定\common\`**(環境変数で変えられる)
DIR = Path(os.environ.get("COIL_PACKING_TOOLS_DISTRIBUTION_DIR",
                          str(dist_settings.default_dir("common"))))
SETTINGS_NAME = "設定.json"
README_NAME = "はじめに読む.txt"
FORMAT = 1

#: このPCが最後に読み込んだ・書き出したとき(画面に出すだけ)
KEY_APPLIED = "distribution_applied"

#: 入れられるもの: (鍵, 画面の名前)。鍵は `local_settings` の鍵そのもの
ITEMS: tuple[tuple[str, str], ...] = (
    (local_settings.KEY_LOG_DIR, "ログの出力先"),
    (local_settings.KEY_LOG_KEEP_DAYS, "ログとエラーの記録を残す日数"),
)
ITEM_KEYS = frozenset(key for key, _ in ITEMS)
ITEM_LABELS = {key: label for key, label in ITEMS}

#: 出力先の長さの上限(画面の保存と同じ)
MAX_DIR_LENGTH = 400

REFUSE_BAD_INPUT = "bad_input"
REFUSE_FAILED = "failed"


@dataclass
class Result:
    ok: bool = True
    message: str = ""
    reason: str = ""
    applied: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)      # すでにあったので読まなかったもの


@dataclass
class Bundle:
    """置いてある `配布設定\\common\\` の中身。"""

    settings: dict[str, Any]
    created_at: str = ""
    created_on: str = ""
    skipped: list[str] = field(default_factory=list)   # 形が違うので読まなかったもの


def settings_path(base: Optional[Path] = None) -> Path:
    return (base or DIR) / SETTINGS_NAME


def is_set(value: Any) -> bool:
    """このPCで値が入っているか。**空の文字は入っていない**(既定のまま)と同じ。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _check(key: str, value: Any) -> str:
    """値の形が合っていなければ理由。合っていれば空(**手で直した 設定.json を通さない**)。"""
    if key == local_settings.KEY_LOG_DIR:
        if not isinstance(value, str) or not value.strip():
            return "フォルダを文字で書いてください（既定のままにするなら、項目ごと消してください）"
        if len(value.strip()) > MAX_DIR_LENGTH:
            return f"長すぎます（{MAX_DIR_LENGTH} 文字まで）"
    if key == local_settings.KEY_LOG_KEEP_DAYS:
        if isinstance(value, bool) or not isinstance(value, int):
            return "日数は数字で書いてください（引用符なし）"
        if not (local_settings.LOG_KEEP_DAYS_MIN <= value <= local_settings.LOG_KEEP_DAYS_MAX):
            return (f"{local_settings.LOG_KEEP_DAYS_MIN}〜{local_settings.LOG_KEEP_DAYS_MAX} 日"
                    "で書いてください")
    return ""


def _show(key: str, value: Any) -> str:
    if key == local_settings.KEY_LOG_KEEP_DAYS:
        return f"{value} 日"
    return str(value)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------
# 読む
# ------------------------------------------------------------------
def _staging() -> Path:
    return DIR.with_name(DIR.name + ".作成中")


def _previous() -> Path:
    return DIR.with_name(DIR.name + ".前")


def _load() -> tuple[Optional[Bundle], str]:
    """置いてある配布設定と、読めないときの理由。どちらも無ければ `(None, "")`。"""
    path = settings_path()
    if not path.is_file():
        if DIR.is_dir():
            return None, f"{DIR.name} フォルダはありますが、{SETTINGS_NAME} がありません。"
        if settings_path(_previous()).is_file():
            return None, (f"前回の書き出しが途中で止まりました。前の配布設定は {_previous()} に"
                          "残っています。もう一度「配布設定に書き出す」を押してください。")
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


def summary() -> dict[str, Any]:
    """画面に出す、配布設定(共通)のいま。"""
    bundle, problem = _load()
    current = local_settings.load_all()
    applied = current.get(KEY_APPLIED)
    out: dict[str, Any] = {
        "exists": bundle is not None,
        # 置いてあるのに読めない(壊れた・形が違う)。**「まだありません」と言わない**
        "problem": problem,
        "skipped": bundle.skipped if bundle is not None else [],
        "path": str(DIR),
        "where": dist_settings.where(DIR),
        "items": [{"key": k, "label": label, "set": is_set(current.get(k)),
                   "value": _show(k, current[k]) if is_set(current.get(k)) else "（既定のまま）"}
                  for k, label in ITEMS],
        "applied_at": applied.get("at", "") if isinstance(applied, dict) else "",
        "contents": [],
        "created_at": "",
        "created_on": "",
    }
    if bundle is not None:
        out.update(contents=[{"label": ITEM_LABELS[k], "value": _show(k, v)}
                             for k, v in bundle.settings.items()],
                   created_at=bundle.created_at, created_on=bundle.created_on)
    return out


# ------------------------------------------------------------------
# 書き出す(配る側)。パスワードは呼ぶ側が確かめる
# ------------------------------------------------------------------
def export() -> Result:
    """**このPCのいまの設定**を `配布設定\\common\\` に書き出す(前の中身は置き換える)。

    このPCで変えていない項目(既定のまま)は入れない ── 配った先も同じ既定で動く。
    """
    current = local_settings.load_all()
    settings: dict[str, Any] = {}
    defaults: list[str] = []
    for key, label in ITEMS:
        value = current.get(key)
        if is_set(value) and not _check(key, value):
            settings[key] = value.strip() if isinstance(value, str) else value
        else:
            defaults.append(label)
    if not settings:
        return Result(False, "書き出せる設定がありません（" + "・".join(defaults)
                      + " はこのPCで変えていないため既定のままです）。先に出力先を保存してください。",
                      REFUSE_BAD_INPUT)
    meta = {"format": FORMAT, "created_at": _now(), "created_on": platform.node(),
            "settings": settings}
    try:
        _replace(meta)
    except OSError as exc:
        return Result(False, f"{DIR} に書けませんでした（前の配布設定はそのままです）: {exc}",
                      REFUSE_FAILED)
    _mark_applied()
    names = [ITEM_LABELS[k] for k in settings]
    message = (f"配布設定に書き出しました（{'・'.join(names)}）。アプリのフォルダの"
               f"「{dist_settings.where(DIR)}」に入っています。アプリのフォルダごと配ってください。")
    if defaults:
        message += f" 既定のままなので入れていないもの（配った先も既定）: {'・'.join(defaults)}。"
    return Result(True, message, applied=names)


def _replace(meta: dict[str, Any]) -> None:
    """`配布設定\\common\\` を入れ替える。**失敗しても前の配布設定を壊さない**(3機能と同じ手順)。

    作ってから(`.作成中`)、前のものをよけ(`.前`)、新しいものを置き、よけたものを消す。
    """
    staging, previous = _staging(), _previous()
    if not DIR.exists() and previous.exists():
        previous.rename(DIR)                        # 前回ここで止まっていたら、まず戻す
    shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True)
        settings_path(staging).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
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
                    previous.rename(DIR)            # 前のものを戻す
                except OSError as again:
                    raise OSError(f"{exc}。前の配布設定は {previous} に残っています"
                                  f"（戻せませんでした: {again}）") from again
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)


def _readme(meta: dict[str, Any]) -> str:
    lines = ["コイル梱包ツール 配布設定（共通: ログ）", "",
             f"作成: {meta['created_at']}（{meta['created_on']}）", "",
             "このフォルダに入っているもの（設定.json）"]
    for key, value in meta["settings"].items():
        lines.append(f"  {ITEM_LABELS[key]}: {_show(key, value)}")
    lines += ["",
              "ログの出力先に共有フォルダを指すと、その下に PC の名前のフォルダを作って書きます。",
              "",
              "配った先で起きること",
              "  起動したときにこのフォルダを読み込みます。",
              "  そのPCですでに設定してある項目は、読み込みません（上書きしない）。",
              "  揃えたいときは、上の帯の「ログ」→「出力先の設定」→「配布設定を読み込み直す」。",
              ""]
    return "\r\n".join(lines)


def remove() -> Result:
    try:
        if DIR.exists():
            shutil.rmtree(DIR)
    except OSError as exc:
        return Result(False, f"消せませんでした: {exc}", REFUSE_FAILED)
    return Result(True, "配布設定（共通）を消しました。このPCの設定はそのままです。")


# ------------------------------------------------------------------
# 読み込む(配られた側)
# ------------------------------------------------------------------
def _mark_applied() -> None:
    try:
        local_settings.save(KEY_APPLIED, {"at": _now()})
    except OSError:
        pass                                        # 画面に出すだけの印。書けなくても止めない


def _apply(bundle: Bundle, *, overwrite: bool) -> Result:
    result = Result()
    current = local_settings.load_all()
    for key, value in bundle.settings.items():
        if is_set(current.get(key)) and not overwrite:
            result.kept.append(ITEM_LABELS[key])    # すでにある(空は「無い」と同じ)
            continue
        local_settings.save(key, value)
        result.applied.append(ITEM_LABELS[key])
    if result.applied:
        _mark_applied()
    return result


def apply_on_start() -> Result:
    """起動時に呼ぶ。`配布設定\\common\\` があれば、**このPCに無いものだけ**読む。

    ここではログを出さない(ログの出力先を決める前に呼ぶため)。結果を見て呼ぶ側が出す。
    書けなかったときは `ok=False`(起動は止めない)。
    """
    bundle, problem = _load()
    if bundle is None:
        return Result(not problem, problem)
    try:
        result = _apply(bundle, overwrite=False)
    except OSError as exc:
        return Result(False, f"このPCの設定に書けませんでした: {exc}", REFUSE_FAILED)
    if bundle.skipped:
        result.message = f"形が違うので読まなかったもの: {' / '.join(bundle.skipped)}"
    return result


def reapply() -> Result:
    """画面から。**すでにあるものも上書きして**読み込み直す。"""
    bundle, problem = _load()
    if bundle is None:
        return Result(False, problem or "配布設定（共通）が置かれていません。", REFUSE_BAD_INPUT)
    try:
        result = _apply(bundle, overwrite=True)
    except OSError as exc:
        return Result(False, f"このPCの設定に書けませんでした: {exc}", REFUSE_FAILED)
    result.message = f"配布設定を読み込みました（{'・'.join(result.applied)}）。"
    if bundle.skipped:
        result.message += f" 形が違うので読まなかったもの: {' / '.join(bundle.skipped)}。"
    return result
