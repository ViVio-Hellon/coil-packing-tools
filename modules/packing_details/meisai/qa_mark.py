"""紙面の右上に刷る文字 (VBA `MergePut ws, 1, 5, 1, 6, "NLM.NAGOYA.QA", True`)

VBA は `BuildOneSheet` に文字を書き込んでいて、変えるにはコードを直す
しかなかった。Web版は**設定画面から変えられる**ようにした(現場の要望)。
変えていなければ VBA と同じ `NLM.NAGOYA.QA` のまま。

【全ラインで1つ ── 正は梱包資材マスタ】
**どのラインPCで変えても、全ラインの次の紙面に出る**(`shared_settings`)。
正は梱包資材マスタの表「梱包明細打ち出し」の ID=1 の行(現場が足した表。
総合ツールのマスタ管理から直しても効く)。表が無ければ控えのJSONを見る。

【管理者パスワードが要る】
品質の印として刷っている文字なので、設定画面を開いた人が押し間違いで
変えられる、にはしない。変えるときも既定に戻すときも、そのたびに聞く
(`admin_password`。これも全ラインで1つ)。

【いつ効くか】
**次に紙面を開いたときから**(どのラインでも)。紙面は開くたびに共有を
読んで組むので、刷り直し(前に出力した明細)も新しい文字で出る。
開いたままの紙面にも、前に戻したときに聞き直して差し替える
(`report._QA_SCRIPT`)。

【共有に届かないとき】
紙は止めない。この端末が最後に読んだ値で刷り、紙面の画面にそう出す
(`notice`)。変えるほうは断る。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Optional

from . import admin_password, shared_settings
from .logging_utils import get_logger
from .sqlite_toolkit import sanitize_for_db

log = get_logger("qa_mark")

# VBA のまま
DEFAULT = "NLM.NAGOYA.QA"

# 長さの上限。**全角20文字でも欄に収まる**ように、刷るときは字を
# 小さくして合わせる(`report._QA_SCRIPT`)。それより長いと読めない
# 大きさまで縮むので、受け付けない
MAX_LEN = 20

# 共有に入れる名前
KEY = "qa_mark"

# 断りの種類。**文言から推し量らない**
REFUSE_NEED_PASSWORD = "need_password"
REFUSE_EMPTY = "empty"
REFUSE_TOO_LONG = "too_long"
REFUSE_SHARED = "shared_unreachable"


@dataclass
class Result:
    ok: bool
    message: str
    reason: str = ""
    value: str = ""


def clean(value: object) -> str:
    """刷れる形に整える。改行・タブは空白に、見えない制御文字は落とす。"""
    text = sanitize_for_db(str(value if value is not None else ""))
    return "".join(ch for ch in text
                   if unicodedata.category(ch) not in ("Cc", "Cf")).strip()


def _from(snapshot: shared_settings.Snapshot) -> str:
    """共有(か写し)の値から、刷る文字を決める。

    共有を手で書き換えて**刷れない値**(長すぎる・文字列でない)に
    なっていたら、既定で刷って警告を残す ── はみ出した紙を黙って出さない。
    """
    stored = snapshot.get(KEY)
    if stored is None or stored == "":
        return DEFAULT
    value = clean(stored) if isinstance(stored, str) else ""
    if not value or len(value) > MAX_LEN:
        log.warning("紙面の右上の文字が刷れない値なので既定で刷ります: %r", stored)
        return DEFAULT
    return value


def resolve(snapshot: Optional[shared_settings.Snapshot] = None
            ) -> tuple[str, shared_settings.Snapshot]:
    """いま刷る文字と、それをどこから読んだか。"""
    snap = snapshot if snapshot is not None else shared_settings.read()
    return _from(snap), snap


def current() -> str:
    """いま刷る文字。変えていなければ `DEFAULT`。"""
    return resolve()[0]


def is_custom(snapshot: Optional[shared_settings.Snapshot] = None) -> bool:
    return resolve(snapshot)[0] != DEFAULT


def notice(snapshot: shared_settings.Snapshot) -> str:
    """紙面の画面に出す断り書き(紙には出ない)。共有から読めていれば空。

    **古い値で刷っているかもしれない、を黙らない。** ほかのラインで
    変えていても、ここには届いていない。マスタが読めず控えで刷るときも
    同じ(マスタのほうが直されていれば、控えは古い)。
    """
    if snapshot.source == "shared" and snapshot.master.problem:
        where = ("控えのJSONの値" if snapshot.qa_origin == shared_settings.ORIGIN_JSON
                 else f"既定の「{DEFAULT}」")
        return (f"梱包資材マスタを読めないため、右上の文字は{where}で刷ります"
                f"({snapshot.master.problem})。")
    if snapshot.source == "cache":
        return ("共有の設定に届かないため、右上の文字はこの端末が"
                f"{snapshot.read_at or '前に'}に読んだ値で刷ります。"
                "ほかのラインで変えていても、まだ反映されていません。")
    if snapshot.source == "none":
        return ("共有の設定に届かないため、右上の文字は既定の"
                f"「{DEFAULT}」で刷ります。")
    return ""


def check(value: object) -> Result:
    """変えてよい値か。**値の形だけ**を見る。"""
    text = clean(value)
    if not text:
        return Result(False, "空にはできません。既定に戻すときは"
                      "「既定に戻す」を押してください。", REFUSE_EMPTY)
    if len(text) > MAX_LEN:
        return Result(False, f"{MAX_LEN}文字までです(いま{len(text)}文字)。",
                      REFUSE_TOO_LONG)
    return Result(True, "", value=text)


def _gate(password: str) -> tuple[Optional[Result], shared_settings.Snapshot]:
    """変える前の関門。共有に届くか → パスワード、の順。

    **届かなければ断る。** 手元だけ変えると、ラインごとに違う紙が出る。
    パスワードを値より先に見る ── 値の誤りを先に言うと、パスワードを
    知らない人にも「どこまで通ったか」が分かる。
    """
    snap = shared_settings.read()
    if not snap.reachable:
        return Result(False, "共有の設定に届かないので変えられません"
                      f"(全ラインで同じ文字にするため)。{snap.problem}",
                      REFUSE_SHARED), snap
    if not admin_password.verify(str(password or ""), snap):
        log.warning("紙面の右上の文字の変更を断りました(管理者パスワード)")
        return Result(False, "紙面の右上の文字を変えるには管理者パスワードが要ります。",
                      REFUSE_NEED_PASSWORD), snap
    return None, snap


def _write(value: str) -> tuple[Optional[Result], str]:
    """共有へ書く。断りか、**どこに書いたか**の一言を返す。"""
    try:
        written = shared_settings.update({KEY: value})
    except shared_settings.SharedError as exc:
        log.warning("紙面の右上の文字を共有へ書けませんでした: %s", exc)
        return Result(False, f"共有に書けないので変えていません。{exc}", REFUSE_SHARED), ""
    where = ("梱包資材マスタ(表「梱包明細打ち出し」)に書きました。" if written.to_master
             else "梱包資材マスタに表が無いので、控えのJSONに書きました。")
    return None, where + written.note


def change(value: object, password: str) -> Result:
    """変える(全ライン)。**管理者パスワードが要る。**"""
    refused, snap = _gate(password)
    if refused:
        return refused
    checked = check(value)
    if not checked.ok:
        return checked
    before = _from(snap)
    if checked.value == before:
        return Result(True, "いまと同じです(変えていません)。", value=before)
    refused, where = _write(checked.value)
    if refused:
        return refused
    log.info("紙面の右上の文字を変えました(全ライン): %r → %r", before, checked.value)
    return Result(True, f"「{checked.value}」に変えました。全ラインで、"
                  f"次に開く紙面から効きます。{where}", value=checked.value)


def reset(password: str) -> Result:
    """既定(`NLM.NAGOYA.QA`)に戻す(全ライン)。**これも管理者パスワードが要る。**"""
    refused, snap = _gate(password)
    if refused:
        return refused
    before = _from(snap)
    # **既定の文字をそのまま書く。** マスタの表を空にすると、表が無いのと
    # 見分けがつかず控えのJSONを見に行く(JSONに古い値が残っていればそれが出る)
    refused, where = _write(DEFAULT)
    if refused:
        return refused
    log.info("紙面の右上の文字を既定に戻しました(全ライン): %r → %r", before, DEFAULT)
    return Result(True, f"既定の「{DEFAULT}」に戻しました(全ライン)。{where}",
                  value=DEFAULT)
