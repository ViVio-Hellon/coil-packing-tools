"""手元のDB(SQLite)が壊れていたときの立て直し(統合 1.2.1 の再点検で足した)

【起きていたこと】
手元のDB(このPCの `%LOCALAPPDATA%` の下)が壊れていると(書いている最中の停電・
ディスクの不調など)、開いたところで `file is not a database` などになり、

    ペナラベルの状態DB   … 統合アプリごと起動しなかった
    梱包明細の作業用DB   … 梱包明細の画面が 500(エラー)になった
    資材計算の作業用DB   … 「初期化に失敗しました」のまま使えなかった

別のリポジトリ(移行の手本の python-web-tools)でも、移行後に同じ不具合が見つかった。

【すること】
壊れていたら**横へ退けて(消さずに)**新しく作り直し、起動を続ける。中身は取り込み元から
取り込み直す。退けたファイル(`名前.壊れていた_日時.db`)は残す ── まだどこにも
送っていない値があれば、そこにある。

**掴まれている・混んでいる(`database is locked` など)は壊れているとみなさない**(退けない)。
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

#: 壊れているときの SQLite の言い方(小文字で比べる)
BROKEN_MARKS = (
    "file is not a database",
    "file is encrypted or is not a database",
    "database disk image is malformed",
    "malformed database schema",
)
#: 壊れているのではなく、ほかが使っているだけのときの言い方
BUSY_MARKS = ("locked", "busy")
#: 一緒に退ける付き物
SIDE_FILES = ("-wal", "-shm", "-journal")


def is_broken(exc: BaseException) -> bool:
    """その例外が「DBが壊れている」ことを言っているか。"""
    if not isinstance(exc, sqlite3.DatabaseError):
        return False
    text = str(exc).lower()
    if any(mark in text for mark in BUSY_MARKS):
        return False
    return any(mark in text for mark in BROKEN_MARKS)


def set_aside(path, label: str, log) -> Optional[Path]:
    """壊れたDBを横へ退ける(消さない)。退けた先を返す。無ければ None。

    **呼ぶ前に、そのDBの接続を閉じておくこと**(Windows は開いているファイルを動かせない)。
    動かせないときは `OSError` をそのまま投げる(呼び手が理由を画面に出す)。
    """
    path = Path(path)
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}.壊れていた_{stamp}{path.suffix}")
    n = 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.stem}.壊れていた_{stamp}_{n}{path.suffix}")
    os.replace(path, target)
    for side in SIDE_FILES:
        extra = Path(str(path) + side)
        if extra.exists():
            try:
                os.replace(extra, Path(str(target) + side))
            except OSError:
                pass
    log.error("%s: 手元のDBが壊れていたので、横へ退けて作り直します(消していません。"
              "中身は取り込み元から取り込み直します): %s → %s", label, path, target.name)
    return target


def open_or_rebuild(path, label: str, log, prepare) -> Optional[Path]:
    """`prepare()`(DBを開いて表を整える)を呼ぶ。壊れていたら退けて1度だけやり直す。

    退けたときはその先を、そうでなければ None を返す。壊れている以外の失敗は
    そのまま投げる。`prepare` は失敗したとき接続を閉じてから投げること。
    """
    try:
        prepare()
        return None
    except Exception as exc:                      # noqa: BLE001 - 壊れている以外は投げ直す
        if not is_broken(exc):
            raise
        log.warning("%s: 手元のDBを開けません(壊れています): %s", label, exc)
    moved = set_aside(path, label, log)
    prepare()
    return moved
