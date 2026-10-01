r"""エラーの記録 ── なぜなぜ分析のために「そのときの事実」を1件1ファイルで残す

現場の指摘: エラー等の後追いが現状できない。ログを残し、なぜなぜで分析できるように
しておいてほしい(統合 1.0.12)。

ログ(1日1ファイル)には何でも流れてくるので、あとから「13日の 10時ごろのあのエラー」を
探すのは骨が折れる。そこで、**エラーが起きたらその場で1件のファイル**を作る:

    <ログの置き場所>\incidents\E20261001-104512-3F9A.md

中身はなぜなぜ分析の順に並べる:

    1. 何が起きたか(現象)  画面に出した文言・いつ・どのPC・どの機能のどの操作
    2. 直接の原因          例外とその場所(プログラムが止まった行)・送られた値
    3. エラーまでの流れ      そのときのログの直前の行(同じ要求の行には印)
    4. そのときの状態        版・PC・Python・ログの置き場所
    5. なぜなぜ分析(記入用) なぜ1〜5・対策・再発防止の欄

**エラー番号は画面にも出す**(「処理中にエラーが起きました(エラー番号 E…)」)。
現場の人がその番号を伝えれば、このファイルにすぐ辿り着ける。

【決まり】
- ここで例外を出さない(記録に失敗しても、元の処理の断り方は変えない)
- パスワード・合言葉・トークンは書かない(`***` に伏せる)
- 同じ画面のエラーが続けて来たら、1件にまとめて回数を足す(`DEDUP_SEC`)
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import secrets
import sys
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from . import logging_utils

log = logging.getLogger("coil_packing_tools.incidents")

FOLDER = "incidents"
ID_RE = re.compile(r"^E\d{8}-\d{6}-[0-9A-F]{4}$")

#: 同じ内容の画面のエラーを1件にまとめる時間(秒)
DEDUP_SEC = 600
#: 「エラーまでの流れ」に写す行数
TRAIL_LINES = 200
#: 送られた値を写す上限(文字)
VALUE_LIMIT = 4000

#: 伏せる鍵(小文字で含んでいれば伏せる)
SECRET_WORDS = ("password", "passwd", "token", "secret", "パスワード", "合言葉")

KIND_LABEL = {
    "server": "サーバの処理が止まった",
    "screen": "画面(ブラウザ)のエラー",
    "write": "書き込めなかった",
}

_lock = threading.Lock()
_recent: dict[str, tuple[str, float]] = {}       # まとめる鍵 → (番号, 最後の時刻)


def folder() -> Path:
    return logging_utils.log_dir() / FOLDER


def new_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return f"E{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2).upper()}"


# ------------------------------------------------------------------
# 伏せる
# ------------------------------------------------------------------
def is_secret(key: str) -> bool:
    k = str(key).lower()
    return any(w in k for w in SECRET_WORDS)


def mask(value: Any) -> Any:
    """パスワード・合言葉・トークンを `***` に伏せる(入れ子も)。"""
    if isinstance(value, dict):
        return {k: ("***" if is_secret(k) and v not in ("", None) else mask(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


def mask_url(text: str) -> str:
    """URL の中のトークン(`t=` など)を伏せる。"""
    return re.sub(r"([?&](?:t|token)=)[^&#\s]*", r"\1***", str(text or ""))


def _short(value: Any, limit: int = VALUE_LIMIT) -> str:
    try:
        text = json.dumps(mask(value), ensure_ascii=False, indent=1, default=str)
    except Exception:                               # noqa: BLE001 - 書けなければ文字で
        text = str(value)
    if len(text) > limit:
        text = text[:limit] + f"\n…(以下 {len(text) - limit} 文字は省きました)"
    return text


# ------------------------------------------------------------------
# そのときの状態
# ------------------------------------------------------------------
def environment() -> dict:
    """版・PC・Python・ログの置き場所。**例外を出さない。**"""
    env: dict = {
        "PC": logging_utils.pc_name(),
        "Windows の利用者": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "Python": sys.version.split()[0],
        "OS": platform.platform(),
        "ログの置き場所": str(logging_utils.log_dir()),
    }
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            conf = current_app.config
            env["版"] = " / ".join(f"{k} {v}" for k, v in (conf.get("VERSIONS") or {}).items())
            env["起動してから"] = f"{round(time.time() - conf.get('STARTED_AT', time.time()))} 秒"
            env["ポート"] = conf.get("PORT", "")
    except Exception:                               # noqa: BLE001
        pass
    return env


def request_facts() -> dict:
    """要求の中なら、その要求の事実(どの画面から・何を・どの値で)。"""
    try:
        from flask import g, has_request_context, request
    except Exception:                               # noqa: BLE001
        return {}
    if not has_request_context():
        return {}
    facts: dict = {
        "要求": f"{request.method} {mask_url(request.full_path.rstrip('?'))}",
        "要求の印": getattr(g, "request_id", ""),
        "機能": _module_label(request.path),
        "画面の名乗り": (request.headers.get("X-Tool-Screen") or request.args.get("screen")
                       or ""),
        "開いていた画面": mask_url(request.headers.get("Referer", "")),
        "ブラウザ": request.headers.get("User-Agent", "")[:200],
    }
    body = request.get_json(silent=True) if request.is_json else None
    if body is None and request.form:
        body = request.form.to_dict()
    if body not in (None, {}, ""):
        facts["送られた値"] = body
    return {k: v for k, v in facts.items() if v not in ("", None)}


def _module_label(path: str) -> str:
    for prefix, label in (("/details", "梱包明細"), ("/pena", "ペナラベル"),
                          ("/material", "資材計算")):
        if path == prefix or path.startswith(prefix + "/"):
            return label
    return "統合画面"


# ------------------------------------------------------------------
# 書く
# ------------------------------------------------------------------
def record(kind: str, title: str, *, shown: str = "", exc: Optional[BaseException] = None,
           facts: Optional[dict] = None, dedup_key: str = "", eid: str = "") -> str:
    """1件書いて、エラー番号を返す。**例外を出さない。**

    `kind`  … server(サーバの処理が止まった)/ screen(画面のエラー)/ write(書けなかった)
    `title` … 1行の要約(一覧に出す)
    `shown` … 画面に出した(出す)文言
    `exc`   … 例外(あれば、場所とトレースバックを写す)
    `facts` … 要求の事実など。省けば、要求の中なら `request_facts()` を使う
    `dedup_key` … 同じものを1件にまとめる鍵(画面のエラー)
    `eid` … 先に決めた番号(画面に出す文言に番号を入れてから記録するとき。`new_id()`)
    """
    now = time.time()
    if dedup_key:
        with _lock:
            hit = _recent.get(dedup_key)
            if hit and now - hit[1] < DEDUP_SEC:
                _recent[dedup_key] = (hit[0], now)
                _append_repeat(hit[0])
                return hit[0]
    eid = eid if ID_RE.match(eid or "") else new_id()
    if dedup_key:
        with _lock:
            _recent[dedup_key] = (eid, now)
            for k in [k for k, (_i, t) in _recent.items() if now - t > DEDUP_SEC]:
                _recent.pop(k, None)
    facts = facts if facts is not None else request_facts()
    path = folder() / f"{eid}.md"
    try:
        text = _render(eid, kind, title, shown, exc, facts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        log.error("エラーの記録 %s: %s ── %s", eid, title, path)
    except Exception as err:                        # noqa: BLE001 - 記録できなくても続ける
        log.error("エラーの記録 %s を書けませんでした(%s): %s", eid, err, title)
    return eid


def _append_repeat(eid: str) -> None:
    path = folder() / f"{eid}.md"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"- {time.strftime('%Y/%m/%d %H:%M:%S')} 同じエラーがもう一度起きました\n")
    except OSError:
        pass


def _render(eid: str, kind: str, title: str, shown: str, exc: Optional[BaseException],
            facts: dict) -> str:
    now = datetime.now()
    rid = str(facts.get("要求の印", ""))
    lines: list[str] = []
    w = lines.append
    w(f"# エラーの記録 {eid}")
    w("")
    w(f"- 種類: {KIND_LABEL.get(kind, kind)}")
    w(f"- 要約: {title}")
    w("")
    w("## 1. 何が起きたか(現象)")
    w("")
    w(f"- 日時: {now:%Y/%m/%d %H:%M:%S}")
    w(f"- PC: {logging_utils.pc_name()}")
    if facts.get("機能"):
        w(f"- 機能: {facts['機能']}")
    if shown:
        w(f"- 画面に出した文言: {shown}")
    for key in ("要求", "開いていた画面", "画面の名乗り", "画面", "操作"):
        if facts.get(key):
            w(f"- {key}: {facts[key]}")
    w("")
    w("## 2. 直接の原因(プログラムが止まった場所)")
    w("")
    if exc is not None:
        frames = traceback.extract_tb(exc.__traceback__)
        where = frames[-1] if frames else None
        w(f"- 例外: {type(exc).__name__}: {exc}")
        if where is not None:
            w(f"- 場所: {_rel(where.filename)}:{where.lineno}({where.name})")
            if where.line:
                w(f"- その行: `{where.line.strip()}`")
        w("")
        w("```")
        w("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip())
        w("```")
    elif facts.get("例外"):
        w(f"- 例外: {facts['例外']}")
        if facts.get("場所"):
            w(f"- 場所: {facts['場所']}")
        if facts.get("スタック"):
            w("")
            w("```")
            w(str(facts["スタック"])[:VALUE_LIMIT])
            w("```")
    else:
        w("- (例外はありません。下の流れを見てください)")
    if "送られた値" in facts:
        w("")
        w("送られた値(パスワード・合言葉・トークンは伏せています):")
        w("")
        w("```json")
        w(_short(facts["送られた値"]))
        w("```")
    w("")
    w(f"## 3. エラーまでの流れ(ログの直前 {TRAIL_LINES} 行まで)")
    w("")
    if rid:
        w(f"`>>` の行はこの操作(要求 {rid})の中の行です。")
        w("")
    w("```")
    for line in logging_utils.recent_lines(TRAIL_LINES):
        w((">> " if rid and f"[要求 {rid}]" in line else "   ") + mask_url(line))
    w("```")
    w("")
    w("## 4. そのときの状態")
    w("")
    for key, value in environment().items():
        w(f"- {key}: {value}")
    extra = {k: v for k, v in facts.items()
             if k not in ("要求", "要求の印", "機能", "画面の名乗り", "開いていた画面", "画面",
                          "操作", "送られた値", "例外", "場所", "スタック")}
    for key, value in extra.items():
        w(f"- {key}: {mask_url(str(value))[:500]}")
    w("")
    w("## 5. なぜなぜ分析(記入用)")
    w("")
    w("1〜4 の事実から、「なぜそうなったか」を5回くり返して、仕組みの原因まで下ります。")
    w("人のせいで止めず、「なぜその間違いができてしまったか」まで書きます。")
    w("")
    for n in range(1, 6):
        w(f"- なぜ{n}: ")
    w("- 真の原因: ")
    w("- 対策(すぐやること): ")
    w("- 再発防止(仕組みで防ぐこと): ")
    w("- 記入者・日付: ")
    w("")
    w("## 経過")
    w("")
    w(f"- {now:%Y/%m/%d %H:%M:%S} 記録しました")
    return "\n".join(lines) + "\n"


def _rel(filename: str) -> str:
    """ソースの場所をアプリのフォルダからの相対で(PC ごとの置き場所の違いを消す)。"""
    try:
        from . import app_config
        return str(Path(filename).resolve().relative_to(Path(app_config.APP_ROOT).resolve()))
    except Exception:                               # noqa: BLE001
        return filename


# ------------------------------------------------------------------
# 読む(画面の一覧と中身)
# ------------------------------------------------------------------
def list_recent(limit: int = 100) -> list[dict]:
    """新しい順。1件 = 番号・日時・種類・要約・機能。"""
    try:
        names = sorted((n for n in os.listdir(folder()) if n.endswith(".md")), reverse=True)
    except OSError:
        return []
    out = []
    for name in names[:limit]:
        eid = name[:-3]
        if not ID_RE.match(eid):
            continue
        info = {"id": eid, "when": _when(eid), "kind": "", "title": "", "module": "",
                "repeats": 0}
        try:
            with (folder() / name).open(encoding="utf-8") as f:
                for line in f:
                    if line.startswith("- 種類: ") and not info["kind"]:
                        info["kind"] = line[6:].strip()
                    elif line.startswith("- 要約: ") and not info["title"]:
                        info["title"] = line[6:].strip()
                    elif line.startswith("- 機能: ") and not info["module"]:
                        info["module"] = line[6:].strip()
                    elif "同じエラーがもう一度" in line:
                        info["repeats"] += 1
        except OSError:
            pass
        out.append(info)
    return out


def _when(eid: str) -> str:
    d, t = eid[1:9], eid[10:16]
    return f"{d[:4]}/{d[4:6]}/{d[6:]} {t[:2]}:{t[2:4]}:{t[4:]}"


def read(eid: str) -> Optional[str]:
    """1件の中身。番号の形が違えば `None`(パスを組み立てるので、形を確かめる)。"""
    if not ID_RE.match(str(eid or "")):
        return None
    try:
        return (folder() / f"{eid}.md").read_text(encoding="utf-8")
    except OSError:
        return None


def cleanup_old(keep_days: Optional[int] = None, *, today: Optional[date] = None) -> list[str]:
    """残す日数を過ぎた記録を消す。**番号の形のファイルだけ**消す。"""
    from . import local_settings
    days = keep_days if keep_days is not None else local_settings.log_keep_days()
    limit = (today or date.today()) - timedelta(days=days)
    removed = []
    for base in {logging_utils.log_dir(), logging_utils.default_log_dir()}:
        target = Path(base) / FOLDER
        try:
            names = os.listdir(target)
        except OSError:
            continue
        for name in names:
            if not (name.endswith(".md") and ID_RE.match(name[:-3])):
                continue
            try:
                day = date(int(name[1:5]), int(name[5:7]), int(name[7:9]))
            except ValueError:
                continue
            if day < limit:
                try:
                    (target / name).unlink()
                    removed.append(name)
                except OSError:
                    pass
    return removed
