"""管理者パスワード ── 設定の一部を、押し間違いで変えさせない

流用元(python-web-tools `packaging_tool/admin_password.py`)をそのまま
持ってきた。照合・撹拌・変更・既定に戻す、の手順は変えていない。

【何を守るか】
紙面の右上に刷る文字(既定 `NLM.NAGOYA.QA`)。**品質の印として刷って
いる文字**なので、設定画面を開いた人が触れば変わる、にはしない
(`report.save_qa_mark`)。

【何のためのものか】
**誤って変えられない**ためのUIガードです。本来の意味でのアクセス制御
ではありません(流用元と同じ。VBA版 `MaterialMasterForm` の
`ADMIN_PASSWORD` から引き継いだ役目)。このアプリの VBA にはパスワードが
無かったので、**Web版で足したもの**です。

【変えられるようにする】
変えられないパスワードは、実質「変えない」と同じです。人が入れ替わっても
直せず、結局みんなが同じ値を知っている状態が続きます。

【全ラインで1つ】
守っている右上の文字が全ラインで共有なので、**パスワードも共有**です
(`shared_settings`)。端末ごとに違うと、あるPCのパスワードを知っている
人が全ラインの紙を変えられてしまう。共有に届かないときは、この端末が
最後に読んだ写しで照合します。**変えるのは、共有に届くときだけ。**

【平文で持たない】
共有フォルダは誰でも開けます。UIガードとはいえ平文で置く理由が無いので、
PBKDF2 で撹拌して持ちます。

【変えていないときは】
一度も変えていない端末では `config.ADMIN_PASSWORD`(環境変数
`PACKING_DETAILS_ADMIN_PASSWORD` で上書き可)がそのまま通ります。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional

from . import config, shared_settings
from .logging_utils import get_logger

log = get_logger("admin_password")

# 共有に入れる名前。値は撹拌済みの文字列で、平文は入らない
KEY = "admin_password"

# 撹拌の仕様。`pbkdf2$<繰り返し>$<塩>$<結果>` の形で1つの文字列に畳む
SCHEME = "pbkdf2"
ITERATIONS = 200_000
SALT_BYTES = 16

# 短すぎるものは断る。**UIガードなので厳しくはしない** ── 長さの規則を
# 増やすほど、現場は紙に書いて画面に貼る
MIN_LENGTH = 4

# 断りの種類。**文言から推し量らない**
REFUSE_WRONG = "wrong_password"     # いまのパスワードが違う
REFUSE_TOO_SHORT = "too_short"      # 新しいパスワードが短い
REFUSE_MISMATCH = "mismatch"        # 確認用と一致しない
REFUSE_SAME = "same"                # 変わっていない
REFUSE_SHARED = "shared_unreachable"  # 共有に届かない・書けない


@dataclass
class Result:
    ok: bool = True
    message: str = ""
    reason: str = ""


# ==================================================================
# 撹拌
# ==================================================================
def _encode(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt, iterations)
    return "$".join([SCHEME, str(iterations),
                     base64.b64encode(salt).decode("ascii"),
                     base64.b64encode(digest).decode("ascii")])


def _matches(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != SCHEME:
            return False
        expected = _encode(password, base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError):               # 壊れた値
        return False
    return hmac.compare_digest(expected, stored)


# ==================================================================
# 使う
# ==================================================================
def _stored(snapshot: Optional[shared_settings.Snapshot] = None) -> Optional[str]:
    snap = snapshot if snapshot is not None else shared_settings.read()
    value = snap.get(KEY)
    return value if isinstance(value, str) and value.strip() else None


def is_custom(snapshot: Optional[shared_settings.Snapshot] = None) -> bool:
    """変えてあるか(全ライン)。設定画面に出す(値そのものは出さない)。"""
    return _stored(snapshot) is not None


def verify(password: str,
           snapshot: Optional[shared_settings.Snapshot] = None) -> bool:
    """合っているか。**照合はここでしかしない。**"""
    stored = _stored(snapshot)
    if stored is not None:
        return _matches(str(password), stored)
    # 一度も変えていない端末。`config` の値で通す
    return hmac.compare_digest(str(password).encode("utf-8"),
                               config.ADMIN_PASSWORD.encode("utf-8"))


def _save(value: str) -> Optional[Result]:
    """共有へ書く。書けなければ断りを返す(**手元だけ変えない**)。"""
    try:
        shared_settings.update({KEY: value})
    except shared_settings.SharedError as exc:
        log.warning("管理者パスワードを共有へ書けませんでした: %s", exc)
        return Result(False, f"共有に書けないので変えていません。{exc}", REFUSE_SHARED)
    return None


def change(current: str, new: str, confirm: str) -> Result:
    """変える。**いまのパスワードを知っている人だけ。**

    肩越しに見ていた人が勝手に変えられる、を作らないための確認です。
    """
    snap = shared_settings.read()
    if not snap.reachable:
        return Result(False, f"共有に届かないので変えられません。{snap.problem}",
                      REFUSE_SHARED)
    if not verify(current, snap):
        # **何が違うのかは言わない。** 総当たりの手がかりになる
        log.warning("管理者パスワードの変更に失敗しました(いまの値が違う)")
        return Result(False, "いまのパスワードが違います。", REFUSE_WRONG)
    new = str(new)
    if len(new) < MIN_LENGTH:
        return Result(False, f"新しいパスワードは{MIN_LENGTH}文字以上にしてください。",
                      REFUSE_TOO_SHORT)
    if new != str(confirm):
        return Result(False, "確認用と一致しません。", REFUSE_MISMATCH)
    if verify(new, snap):
        return Result(False, "いまと同じパスワードです。", REFUSE_SAME)

    refused = _save(_encode(new, os.urandom(SALT_BYTES)))
    if refused:
        return refused
    log.info("管理者パスワードを変更しました(全ライン)")
    return Result(True, "管理者パスワードを変えました(全ラインで共有)。")


def reset(current: str) -> Result:
    """既定に戻す(`config.ADMIN_PASSWORD` / 環境変数)。

    忘れたときの逃げ道は**共有の `梱包明細打ち出し.json` の
    `admin_password` の行を消す**ことです。画面から「忘れた」で戻せる
    ようにすると、確認そのものが意味を失います。
    """
    snap = shared_settings.read()
    if not snap.reachable:
        return Result(False, f"共有に届かないので変えられません。{snap.problem}",
                      REFUSE_SHARED)
    if not verify(current, snap):
        return Result(False, "いまのパスワードが違います。", REFUSE_WRONG)
    refused = _save("")
    if refused:
        return refused
    log.info("管理者パスワードを既定に戻しました(全ライン)")
    return Result(True, "既定のパスワードに戻しました。")
