# -*- coding: utf-8 -*-
"""配布設定 ── 1 台で決めた設定を、配った先の端末でそのまま使う

``ViVio-Hellon/python-web-tools`` の ``packaging_tool/distribution.py`` と
同じ考え方・同じ流れにしてある。

【なぜ要るのか】
設定（資材マスタの置き場所・印刷の補正など）は端末ごとの
``%LOCALAPPDATA%\\PackingPenaLabel\\config\\local.json`` に入る。
配った先で 1 台ずつ設定画面を開いて打ち直すのは手間で、打ち間違えると
**別のマスタを読む端末**や**ずれて刷る端末**ができてしまう。

【流れ】
    1. 1 台で起動して設定し、設定画面の「配布設定」で書き出す
    2. アプリのフォルダーの直下に ``配布設定\\packing_pena_label\\`` ができ、
       配るものが全部そこに入る（統合版。3機能とも ``配布設定\\<機能>\\``）
    3. アプリのフォルダーごと配る
    4. 配った先は起動したとき ``配布設定\\`` を見つけて読み込む

【``配布設定\\`` の中身】

    配布設定\\
      設定.json          書き出した項目と値
      はじめに読む.txt   何が入っているか・配った先で何が起きるか

【読み込むときの決まり】**その端末にすでにある設定は読み込まない。**
その端末で値が入っている項目はそのまま。無い項目だけ埋める。
起動のたびに見に行くが、埋まった項目は次から「すでにある」ので、
端末で直した値が戻されることはない。狙って揃えたいときは、
設定画面の「配布設定を読み込み直す」（合言葉）で上書きする。

【合言葉】書き出す・消す・読み込み直すには合言葉が要る。
起動時の読み込みには要らない ── ``配布設定\\`` を置いたのは、
フォルダーを配った管理者本人だから。

【``config/app.json`` との違い】
``app.json`` はアプリに同梱する**既定値**で、ファイルを直接直すもの。
配布設定は**画面で決めた値を書き出す**もので、端末の設定へ写し込む。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common import dist_settings as _dist_settings
from ..config import Config, load_local_overrides, save_local_overrides
from . import settings as S

log = logging.getLogger(__name__)

#: ``配布設定\\`` の置き場所。**統合版では統合アプリのフォルダの直下
#: ``配布設定\\packing_pena_label\\``**（梱包明細・資材計算と同じ決まり。
#: 設定の項目が機能ごとに違うので、機能ごとに分ける。common/dist_settings.py）
DIR = Path(os.environ.get("PACKING_PENA_DISTRIBUTION_DIR",
                          str(_dist_settings.default_dir("packing_pena_label"))))
SETTINGS_NAME = "設定.json"
README_NAME = "はじめに読む.txt"
#: この端末が最後に読み込んだとき（設定画面に出すだけ）
APPLIED_NAME = "distribution_applied.json"

FORMAT = 1

#: 既定では入れない項目と、その理由
#: （全端末で同じにすると困るもの）
_DEFAULT_OFF = {
    "port": "使用ポートは端末で別のアプリと取り合うことがあるため",
}

REFUSE_NEED_PASSWORD = "need_password"
REFUSE_BAD_INPUT = "bad_input"
REFUSE_FAILED = "failed"


def items() -> List[Tuple[str, str, str, bool]]:
    """入れられる項目: ``(鍵, 画面の名前, 群, 既定で入れるか)``。

    鍵は設定画面の項目そのもの（``local.json`` の鍵と同じ）。
    """
    out = []
    for spec in S.SETTINGS:
        out.append((spec.key, spec.label, spec.group,
                    spec.key not in _DEFAULT_OFF))
    return out


def item_keys() -> frozenset:
    return frozenset(k for k, _, _, _ in items())


def item_label(key: str) -> str:
    spec = S.SETTINGS_BY_KEY.get(key)
    return spec.label if spec else key


def settings_path(base: Optional[Path] = None) -> Path:
    return (base or DIR) / SETTINGS_NAME


@dataclass
class Result:
    ok: bool = True
    message: str = ""
    reason: str = ""
    applied: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)   # すでにあったので読まなかったもの
    skipped: List[str] = field(default_factory=list)  # 値が正しくないので読まなかったもの

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "reason": self.reason,
                "applied": list(self.applied), "kept": list(self.kept),
                "skipped": list(self.skipped)}


# ------------------------------------------------------------------
# 読む
# ------------------------------------------------------------------
@dataclass
class Bundle:
    """置いてある ``配布設定\\`` の中身。"""
    settings: Dict[str, Any]
    created_at: str = ""
    created_on: str = ""


def read() -> Optional[Bundle]:
    """置いてある配布設定。無い・読めない・形が違うなら None。"""
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
        return None
    keys = item_keys()
    settings = {k: v for k, v in raw.items() if k in keys}     # 知らない鍵は捨てる
    if not settings:
        return None
    return Bundle(settings=settings,
                  created_at=str(data.get("created_at", "")),
                  created_on=str(data.get("created_on", "")))


def _applied_path(cfg: Config) -> str:
    return os.path.join(cfg.local_config_dir, APPLIED_NAME)


def applied_at(cfg: Config) -> str:
    try:
        with open(_applied_path(cfg), encoding="utf-8") as fh:
            data = json.load(fh)
        return str(data.get("at", "")) if isinstance(data, dict) else ""
    except (OSError, ValueError):
        return ""


def _mark_applied(cfg: Config) -> None:
    try:
        os.makedirs(cfg.local_config_dir, exist_ok=True)
        with open(_applied_path(cfg), "w", encoding="utf-8") as fh:
            json.dump({"at": _now()}, fh, ensure_ascii=False)
    except OSError as exc:                       # 表示用なので失敗しても続ける
        log.warning("配布設定の読み込み日時を残せません: %s", exc)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _default_out() -> str:
    from .dist_folder import default_out
    return str(default_out())


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "する" if value else "しない"
    text = str(value)
    return text if text != "" else "（空欄）"


def summary(cfg: Config) -> Dict[str, Any]:
    """設定画面に出す、配布設定のいま。"""
    bundle = read()
    groups: List[Dict[str, Any]] = []
    for key, label, group, default in items():
        if not groups or groups[-1]["group"] != group:
            groups.append({"group": group, "items": []})
        groups[-1]["items"].append({"key": key, "label": label,
                                    "default": default,
                                    "why": _DEFAULT_OFF.get(key, "")})
    local = load_local_overrides(cfg)
    out: Dict[str, Any] = {
        "exists": bundle is not None,
        "path": str(DIR),
        "groups": groups,
        "appliedAt": applied_at(cfg),
        "contents": [],
        "createdAt": "",
        "createdOn": "",
        "localKeys": sorted(local.keys()),
        "defaultOut": _default_out(),
    }
    if bundle is None:
        return out
    out.update(
        contents=[{"key": k, "label": item_label(k), "value": _show(v)}
                  for k, v in bundle.settings.items()],
        createdAt=bundle.created_at, createdOn=bundle.created_on)
    return out


# ------------------------------------------------------------------
# 書き出す（配る側）
# ------------------------------------------------------------------
def export(password: str, keys: List[str], cfg: Config) -> Result:
    """**この端末のいまの設定**を ``配布設定\\`` に書き出す（前の中身は置き換える）。

    この端末で一度も変えていない項目は入れない ── 配った先も同じ既定で
    動くので要らない（空で上書きしないためにも入れない）。
    """
    if not S.password_ok(password):
        return Result(False, "配布設定を書き出すには合言葉が要ります。",
                      REFUSE_NEED_PASSWORD)
    known = item_keys()
    unknown = [k for k in keys if k not in known]
    if unknown:
        return Result(False, "知らない項目です: %s" % ", ".join(unknown),
                      REFUSE_BAD_INPUT)
    if not keys:
        return Result(False, "入れる項目を 1 つ以上選んでください。",
                      REFUSE_BAD_INPUT)

    current = load_local_overrides(cfg)
    settings = {k: current[k] for k in keys if k in current}
    defaults = [item_label(k) for k in keys if k not in current]
    if not settings:
        return Result(False, "書き出せる設定がありません（" + "・".join(defaults)
                      + " はこの端末で変えていないため既定のままです）。",
                      REFUSE_BAD_INPUT)

    # **作ってから入れ替える。** 途中で失敗して、半分だけ新しい配布設定を
    # 残さない（配った先がそれを読んでしまう）
    staging = DIR.with_name(DIR.name + ".作成中")
    try:
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        meta = {"format": FORMAT, "created_at": _now(),
                "created_on": platform.node(), "settings": settings}
        settings_path(staging).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        # Windows のメモ帳でも崩れないよう BOM 付き・CRLF で書く
        (staging / README_NAME).write_text(
            _readme(meta).replace("\n", "\r\n"), encoding="utf-8-sig",
            newline="")
        if DIR.exists():
            shutil.rmtree(DIR)
        staging.rename(DIR)
    except OSError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return Result(False, "%s に書けませんでした: %s" % (DIR, exc),
                      REFUSE_FAILED)
    _mark_applied(cfg)

    names = [item_label(k) for k in settings]
    message = ("配布設定を書き出しました（%d 項目）。アプリのフォルダーの"
               "「%s」に入っています。アプリのフォルダーごと配ってください。"
               % (len(names), _dist_settings.where(DIR)))
    if defaults:
        # 全部並べると長すぎて読まれないので、多いときは数で言う
        shown = "・".join(defaults[:3]) + (
            " ほか %d 項目" % (len(defaults) - 3) if len(defaults) > 3 else "")
        message += ("\n既定のままなので入れていないもの（配った先も既定で動きます）: "
                    + shown + "。")
    log.info("配布設定を書き出しました: %s", ", ".join(names))
    return Result(True, message, applied=names)


def _readme(meta: Dict[str, Any]) -> str:
    lines = [
        "梱包ペナ ラベル・風袋計算 配布設定",
        "",
        "作成: %s（%s）" % (meta["created_at"], meta["created_on"]),
        "",
        "このフォルダーに入っているもの",
    ]
    for key, value in meta["settings"].items():
        lines.append("  %s: %s" % (item_label(key), _show(value)))
    lines += [
        "",
        "配った先で起きること",
        "  起動したときにこのフォルダーを読み込みます。",
        "  その端末にすでにある設定は、読み込みません（上書きしない）。",
        "  揃えたいときは、設定画面の「配布設定」→「配布設定を読み込み直す」。",
        "",
        "このフォルダーを消すと",
        "  配った先はこれ以上読み込みません。すでに読み込んだ端末の設定はそのままです。",
        "",
    ]
    return "\n".join(lines)


def remove(password: str) -> Result:
    if not S.password_ok(password):
        return Result(False, "配布設定を消すには合言葉が要ります。",
                      REFUSE_NEED_PASSWORD)
    try:
        if DIR.exists():
            shutil.rmtree(DIR)
    except OSError as exc:
        return Result(False, "消せませんでした: %s" % exc, REFUSE_FAILED)
    log.info("配布設定を消しました")
    return Result(True, "配布設定を消しました。この端末の設定はそのままです。")


# ------------------------------------------------------------------
# 読み込む（配られた側）
# ------------------------------------------------------------------
def _apply(bundle: Bundle, cfg: Config, *, overwrite: bool) -> Result:
    result = Result()
    current = load_local_overrides(cfg)
    changed = False
    for key, value in bundle.settings.items():
        spec = S.SETTINGS_BY_KEY.get(key)
        if spec is None:
            continue
        if key in current and not overwrite:
            result.kept.append(spec.label)              # すでにある
            continue
        try:
            value = S.coerce(spec, value)
        except ValueError as exc:
            # 手で書き換えられた等で値が壊れている。その項目だけ読まない
            log.warning("配布設定の %s を読みません: %s", spec.label, exc)
            result.skipped.append(spec.label)
            continue
        if current.get(key) != value:
            current[key] = value
            changed = True
        result.applied.append(spec.label)
    if changed:
        save_local_overrides(current, cfg)
    if result.applied:
        _mark_applied(cfg)
    return result


def apply_on_start(cfg: Config) -> Result:
    """起動時に呼ぶ。``配布設定\\`` があれば、**その端末に無いものだけ**読む。"""
    bundle = read()
    if bundle is None:
        return Result(True, "")
    try:
        result = _apply(bundle, cfg, overwrite=False)
    except OSError as exc:
        log.warning("配布設定を読み込めませんでした: %s", exc)
        return Result(False, "配布設定を読み込めませんでした: %s" % exc,
                      REFUSE_FAILED)
    if result.applied:
        result.message = "配布設定を読み込みました"
    return result


def reapply(password: str, cfg: Config) -> Result:
    """設定画面から。**すでにあるものも上書きして**読み込み直す。"""
    if not S.password_ok(password):
        return Result(False, "配布設定を読み込み直すには合言葉が要ります。",
                      REFUSE_NEED_PASSWORD)
    bundle = read()
    if bundle is None:
        return Result(False, "配布設定が置かれていません。", REFUSE_BAD_INPUT)
    try:
        result = _apply(bundle, cfg, overwrite=True)
    except OSError as exc:
        return Result(False, "読み込めませんでした: %s" % exc, REFUSE_FAILED)
    result.message = "配布設定を読み込みました（%d 項目）" % len(result.applied)
    if result.skipped:
        result.message += "。値が正しくないため読まなかったもの: " \
            + "・".join(result.skipped)
    return result
