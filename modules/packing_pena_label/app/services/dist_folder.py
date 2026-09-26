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
先に設定画面の「配布設定」で書き出しておく（アプリの直下の ``配布設定\``。
配った先が起動時に読み込む）。

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
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from ..config import APP_BUILD, ROOT_DIR, code_stamp
from common import app_config as _integrated

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

#: 配布設定のフォルダ（``services/distribution.py`` の置き場所と同じ名前）。
#: **INCLUDE には入れない** ── 入れるかどうかは with_settings で決める
SETTINGS = "配布設定"

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


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _settings_lines(folder: Path) -> List[str]:
    """配布設定の中身を、メモと画面に出す形で。"""
    from . import distribution as D
    path = folder / D.SETTINGS_NAME
    if not path.is_file():
        return ["  （設定.json がありません）"]
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return ["  （読めませんでした: %s）" % exc]
    lines = []
    for key, value in (data.get("settings") or {}).items():
        shown = ("する" if value else "しない") if isinstance(value, bool) else value
        lines.append("  %s: %s" % (D.item_label(key), shown))
    if data.get("created_at"):
        lines.append("  （作成 %s / %s）" % (data.get("created_at"),
                                            data.get("created_on", "")))
    return lines or ["  （中身がありません）"]


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
                shutil.copytree(str(src), str(out / name), ignore=_ignore,
                                dirs_exist_ok=True)
            else:
                shutil.copy2(str(src), str(out / name))
        if missing:
            raise BuildRefused("配るはずのファイルがありません: " + ", ".join(missing))

        lines = ["配布用フォルダを作りました: %s" % out,
                 "版: v%s（%s）／中身の指紋 %s" % (APP_VERSION, APP_BUILD, code_stamp())]
        if (out / "runtime").is_dir():
            lines.append("同梱の Python（runtime フォルダ）を入れました。")
        else:
            lines.append("同梱の Python（runtime フォルダ）はありません。"
                         "配った先に Python が要ります。")

        # 配布設定: 入れる/入れないをはっきり決める。
        # 読むのは **書き出した場所そのもの**（distribution.DIR）。名前で
        # 探すと、置き場所を変えている場合に「書き出したのに入らない」になる。
        # 配った先ではアプリ直下の 配布設定\ を読むので、入れる先は既定の名前。
        if root is None:
            from . import distribution as D
            settings_src = Path(D.DIR)
        else:
            settings_src = src_root / SETTINGS
        included = False
        if with_settings and settings_src.is_dir():
            shutil.copytree(str(settings_src), str(out / SETTINGS), ignore=_ignore)
            included = True
            lines.append("%s フォルダを入れました（配った先が起動時に読み込みます）:"
                         % SETTINGS)
            lines += _settings_lines(out / SETTINGS)
        else:
            lines.append("配布設定は入れていません。配った先で 1 台ずつ"
                         "設定画面から参照先を設定してください。")
            if with_settings:
                lines.append("  （設定画面の「配布設定」で書き出すと、次からは一緒に配れます）")

        # 入っていてはいけないものが無いか、最後に確かめる
        leaked = [p for p in FORBIDDEN if (out / p).exists()]
        leaked += [str(p.relative_to(out)) for p in out.rglob("*") if _excluded(p.name)]
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
