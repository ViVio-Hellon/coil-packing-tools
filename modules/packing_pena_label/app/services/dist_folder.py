# -*- coding: utf-8 -*-
r"""配布用フォルダを作る（``ViVio-Hellon/python-web-tools`` の ``scripts/make_dist.py`` の移植）

【統合版】配るのは**統合アプリのフォルダ一式**（3機能ぶん）。``ROOT`` は統合アプリの
根で、``INCLUDE`` は統合アプリの起動ファイル・共通基盤・3機能（``modules``）。
機能ごとの ``tests`` / ``tools`` や、手元DBの写し（``*.db`` ``*.sqlite3``）は
どの階層にあっても写さない（``EXCLUDE_NAMES``）。

【なぜ要るのか】
配るときに手でフォルダをコピーすると、**配ってはいけないもの**が紛れる。

    tests\ / tools\ / .git\ / __pycache__ … 開発にしか使わない
    *.sqlite3                             … 資材マスタの写しなど。班員名簿・
                                            取引先名が入っていることがあり、
                                            古い写しを配ると古いマスタで計算する
    runtime\instance.lock / instance.json … 起動中の印（配ると起動を誤判定する）
    *.log / logs\                         … その端末の記録

ここは **配るものだけ** を新しいフォルダへ写す。設定も一緒に配りたいときは、
先に各機能の設定画面の「配布設定」で書き出しておく（アプリの直下の
``配布設定\<機能>\``。配った先が起動時に読み込む）。

【統合版: 配布設定は3機能ぶんまとめて】
梱包明細・ペナラベル・資材計算の配布設定を、書き出してあるものは全部入れる。
共通（ログの出力先・残す日数。上の帯の「ログ」から書き出す ``配布設定\common\``）も
書き出してあれば入れる（統合 1.0.14）。
入れないと決めたとき（``with_settings=False``）は**どれも入れない**。
値は機能ごとに分けたまま（``配布設定\packing_details\`` など。
``common/dist_settings.py``）。機能のフォルダの中に古い置き場所の
``配布設定\`` が残っていても、それは写さない（入れる・入れないの選択が
効かなくなるため）。

python-web-tools との違い
    * 端末ごとの設定・作業状態は ``%LOCALAPPDATA%\PackingPenaLabel`` にあり、
      アプリのフォルダには無い。そのため ``data\``（同梱のマスタ・台紙定義）は
      **配ってよい**。入れ替えるときに古いフォルダから写すものも無い。
    * ``runtime\`` は同梱の Python（あれば一緒に配る）。
    * 作り直し（force）で消すのは、**前にこの仕組みで作ったフォルダだけ**
      （``配布メモ.txt`` があるもの）。打ち間違えた場所を消さないため。
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import importlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import APP_BUILD, ROOT_DIR, code_stamp
from common import app_config as _integrated
from common import dist_settings as _dist_settings
from common import versions as _versions

log = logging.getLogger(__name__)

#: 統合アプリの根（``modules/packing_pena_label`` の2つ上）
ROOT = Path(ROOT_DIR).parent.parent

#: 配るものの名前（統合アプリのもの）
APP_ID = _integrated.load()["app_id"]
APP_NAME = _integrated.display_name()
APP_VERSION = _integrated.version()

#: 直下で **配るもの**（統合アプリの起動ファイル・共通基盤・3機能）
INCLUDE: Tuple[str, ...] = (
    "README.md", "requirements.txt",
    "Start.vbs", "start.bat", "stop.bat",
    "start_app.py", "run.py", "app.py", "server.py", "boot_server.py",
    "launch_guard.py", "process_manager.py",
    "common", "modules", "config", "templates", "static", "docs", "scripts",
)

#: あれば配るもの（無くても作れる）
OPTIONAL: Tuple[str, ...] = (
    "runtime",          # 同梱の Python（runtime\python.exe / pythonw.exe）
)

#: 中にあっても写さないもの（名前で見る。フォルダならその下ごと）
EXCLUDE_NAMES: Tuple[str, ...] = (
    "__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".coverage", ".coverage.*",
    "htmlcov", "*.tmp", "*.bak", "*.bak-*", ".DS_Store", "Thumbs.db", "desktop.ini",
    "*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm", "*.sqlite3-journal",
    "*.log", "*.log.*", "logs",
    "instance.lock", "instance.json", "*.lock",
    "local.json",       # 端末の上書き設定（本来 %LOCALAPPDATA% にある）
    # 統合版: 機能ごとの試験・開発用ツール・手元DBの写しは、どの階層にあっても写さない
    "tests", "tools", "*.db", "*.db-wal", "*.db-shm", "user_config.json",
)

#: 配布設定のフォルダ（``common/dist_settings.py`` の決まり。中は機能ごと）。
#: **INCLUDE には入れない** ── 入れるかどうかは with_settings で決める
SETTINGS = _dist_settings.ROOT_NAME

#: できたフォルダに **入っていてはいけない** もの（最後に確かめる）
FORBIDDEN: Tuple[str, ...] = ("tests", "tools", ".git", "config/local.json")

MEMO_NAME = "配布メモ.txt"


class BuildRefused(Exception):
    """作るのを断った（理由の文つき）。"""


@dataclass
class BuildResult:
    out: str
    lines: List[str] = field(default_factory=list)
    files: int = 0
    with_settings: bool = False
    zip_path: str = ""

    def to_dict(self) -> dict:
        return {"out": self.out, "lines": list(self.lines), "files": self.files,
                "withSettings": self.with_settings, "zipPath": self.zip_path}


def default_out() -> Path:
    """既定の置き場所: アプリの隣に ``PackingPenaLabel_v1.5.0``。"""
    return ROOT.parent / ("%s_v%s" % (APP_ID, APP_VERSION))


def _excluded(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_NAMES)


def _ignore(directory: str, names: List[str]) -> set:
    return {n for n in names if _excluded(n)}


def _stray_settings(name: str) -> bool:
    """配るものの中に紛れた配布設定（古い置き場所・書き出しの途中）。"""
    return name == SETTINGS or name.startswith(SETTINGS + ".")


def _ignore_app(directory: str, names: List[str]) -> set:
    """アプリ本体を写すとき。配布設定は **選んだときだけ、決まった場所へ** 入れるので、
    機能のフォルダの中に残っている ``配布設定\\`` は写さない。"""
    return {n for n in names if _excluded(n) or _stray_settings(n)}


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _label_of(module, key: str) -> str:
    """設定の項目の呼び名（機能ごとの一覧から）。"""
    item_label = getattr(module, "item_label", None)
    if callable(item_label):
        return item_label(key)
    return (getattr(module, "ITEM_LABELS", None) or {}).get(key, key)


def _settings_lines(folder: Path, module=None) -> List[str]:
    """配布設定の中身を、メモと画面に出す形で。"""
    if module is None:
        from . import distribution as module
    path = folder / module.SETTINGS_NAME
    if not path.is_file():
        return ["  （設定.json がありません）"]
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return ["  （読めませんでした: %s）" % exc]
    lines = []
    for key, value in (data.get("settings") or {}).items():
        shown = ("する" if value else "しない") if isinstance(value, bool) else value
        lines.append("  %s: %s" % (_label_of(module, key), shown))
    if data.get("created_at"):
        lines.append("  （作成 %s / %s）" % (data.get("created_at"),
                                            data.get("created_on", "")))
    return lines or ["  （中身がありません）"]


def _settings_sources(src_root: Path, root: Optional[Path]) -> list:
    """配布設定のうち、書き出してあるもの: ``[(機能, 呼び名, 置き場所, モジュール)]``。

    3機能と、共通（ログの出力先・残す日数。統合 1.0.14）。
    読むのは **各機能が書き出した場所そのもの**（各機能の ``distribution.DIR``）。
    名前で探すと、置き場所を変えている場合に「書き出したのに入らない」になる。
    ``root`` を渡したとき（試験・写しから作るとき）は、その下の決まった場所。
    """
    found = []
    for key, label, modname in _dist_settings.ALL:
        module = importlib.import_module(modname)
        src = Path(module.DIR) if root is None else src_root / SETTINGS / key
        if src.is_dir() and (src / module.SETTINGS_NAME).is_file():
            found.append((key, label, src, module))
    return found


def build(out: Optional[Path] = None, *, with_settings: bool = True,
          force: bool = False, make_zip: bool = False,
          root: Optional[Path] = None) -> BuildResult:
    """配布用フォルダを作る。断るときは ``BuildRefused``（理由の文つき）。"""
    src_root = (root or ROOT).resolve()
    out = Path(out) if out else default_out()
    if not out.is_absolute():
        raise BuildRefused("作る場所はフルパスで指定してください: %s" % out)
    out = out.resolve()
    if _inside(out, src_root) or _inside(src_root, out):
        raise BuildRefused("アプリのフォルダの中（またはその上）には作れません: %s\n"
                           "（次に作るとき、前に作ったものまで写してしまいます）" % out)
    if out.exists() and not out.is_dir():
        raise BuildRefused("同じ名前のファイルがあります: %s" % out)
    if out.exists() and any(out.iterdir()):
        if not force:
            raise BuildRefused("%s はもうあって、中身があります。\n"
                               "別の場所を指定するか、作り直してください。" % out)
        # 打ち間違えた場所を消さない: 前にここで作ったフォルダだけ消す
        if not (out / MEMO_NAME).is_file():
            raise BuildRefused("%s は配布用フォルダではない（%s がない）ので、"
                               "消して作り直すことはしません。別の場所を指定してください。"
                               % (out, MEMO_NAME))
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        missing = []
        for name in INCLUDE + OPTIONAL:
            src = src_root / name
            if not src.exists():
                if name not in OPTIONAL:
                    missing.append(name)
                continue
            if src.is_dir():
                shutil.copytree(str(src), str(out / name), ignore=_ignore_app,
                                dirs_exist_ok=True)
            else:
                shutil.copy2(str(src), str(out / name))
        if missing:
            raise BuildRefused("配るはずのファイルがありません: " + ", ".join(missing))

        lines = ["配布用フォルダを作りました: %s" % out,
                 # 統合ツールの版と3機能の版(分けて持つ。common/versions.py)
                 "版: %s" % _versions.describe(),
                 "   （ペナラベルの中身の指紋 %s / %s）" % (code_stamp(), APP_BUILD)]
        if (out / "runtime").is_dir():
            lines.append("同梱の Python（runtime フォルダ）を入れました。")
        else:
            lines.append("同梱の Python（runtime フォルダ）はありません。"
                         "配った先に Python が要ります。")

        # 配布設定: 入れる/入れないをはっきり決める。**3機能ぶん（と共通）まとめて**。
        # 配った先では各機能が 配布設定\<機能>\ を読むので、入れる先は決まった名前。
        found = _settings_sources(src_root, root)
        included = False
        if with_settings and found:
            lines.append("%s フォルダを入れました（配った先が起動時に読み込みます）:"
                         % SETTINGS)
            for key, label, src, module in found:
                dest = out / SETTINGS / key
                shutil.copytree(str(src), str(dest), ignore=_ignore)
                lines.append("  [%s] %s\\%s" % (label, SETTINGS, key))
                lines += ["  " + ln for ln in _settings_lines(dest, module)]
            have = {key for key, _, _, _ in found}
            rest = [label for key, label, _ in _dist_settings.MODULES if key not in have]
            if rest:
                lines.append("  書き出していない機能: %s（配った先で 1 台ずつ設定します）"
                             % "・".join(rest))
            included = True
        else:
            lines.append("配布設定は入れていません。配った先で 1 台ずつ"
                         "各機能の設定画面から設定してください。")
            if with_settings:
                lines.append("  （各機能の設定画面の「配布設定」で書き出すと、次からは一緒に配れます）")

        # 入っていてはいけないものが無いか、最後に確かめる
        leaked = [p for p in FORBIDDEN if (out / p).exists()]
        leaked += [str(p.relative_to(out)) for p in out.rglob("*") if _excluded(p.name)]
        # 配布設定は決まった場所（直下の 配布設定\）だけ。選ばなければそれも無い
        leaked += [str(p.relative_to(out)) for p in out.rglob("*")
                   if _stray_settings(p.name) and p != out / SETTINGS]
        if not included and (out / SETTINGS).exists():
            leaked.append(SETTINGS)
        if leaked:
            raise BuildRefused("配ってはいけないものが入ったため、作るのをやめました: "
                               + ", ".join(sorted(set(leaked))))

        files = sum(1 for p in out.rglob("*") if p.is_file())
        lines.append("ファイル数: %d" % files)
        # Windows のメモ帳でも崩れないよう BOM 付き・CRLF
        (out / MEMO_NAME).write_text(_memo(lines).replace("\n", "\r\n"),
                                     encoding="utf-8-sig", newline="")
    except BuildRefused:
        shutil.rmtree(str(out), ignore_errors=True)
        raise
    except OSError as exc:
        shutil.rmtree(str(out), ignore_errors=True)
        raise BuildRefused("作れませんでした: %s" % exc)

    result = BuildResult(out=str(out), lines=lines, files=files,
                         with_settings=included)
    if make_zip:
        archive = shutil.make_archive(str(out), "zip", root_dir=str(out.parent),
                                      base_dir=out.name)
        result.zip_path = archive
        result.lines.append("zip も作りました: %s" % archive)
    log.info("配布用フォルダを作りました: %s（%d ファイル、配布設定 %s）",
             out, files, "あり" if included else "なし")
    return result


def _memo(lines: List[str]) -> str:
    today = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return "\n".join([
        "%s 配布メモ（%s）" % (APP_NAME, today),
        "",
        *lines,
        "",
        "配った先ですること",
        "  1. このフォルダを好きな場所に置く（以前の版のフォルダに上書きしない）",
        "  2. runtime フォルダが無い場合は、Python が入っているか確かめる",
        "  3. Start.vbs で起動する。配布設定があれば、このとき読み込みます",
        "     （その端末ですでに入れてある設定は上書きしません）",
        "",
        "以前の版から入れ替えるとき",
        "  1. 古い版を止める（stop.bat）",
        "  2. 新しいフォルダの Start.vbs で起動する",
        "     端末ごとの設定・作業状態は %LOCALAPPDATA% の下（PackingDetails・",
        "     PackingPenaLabel・CoilMaterialTool。統合アプリのログは CoilPackingTools）にあるので、",
        "     古いフォルダから写すものはありません。そのまま前の続きから使えます",
        "  3. 古いフォルダは、新しい版で動くのを確かめてから消す",
        "",
        "入れていないもの: tests・tools・.git・*.sqlite3（マスタの写しなど）・ログ・起動中の印",
        "",
    ])
