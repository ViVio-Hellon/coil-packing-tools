"""利用者ごとの設定の永続化

取り込み元の置き場所など、**端末ごとに変わる値**をJSONファイル
(`config.USER_CONFIG_PATH`)へ保存する。

読み書きのたびにファイルを開くので、外部から書き換えても次回の
読み取りで反映される。置き場所は利用者ごとのローカル領域なので、
アプリ本体を共有フォルダに置いて複数端末から使っても混ざらない。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from . import config
from .logging_utils import get_logger

log = get_logger("user_settings")

# **読み書きは1つずつ。** 紙面を開くたびに共有の写しを書き直すことが
# あり(`shared_settings`)、画面の要求は並んで来る。書いている途中の
# ファイルを別の要求が読むと、壊れたとみなして空で読み、それを保存して
# **ほかの設定(置き場所など)を消してしまう。**
_lock = threading.RLock()


def load_all() -> dict[str, Any]:
    """設定ファイル全体を読む。壊れていても例外にせず空扱いにする。

    壊れた設定で**起動そのものを失敗させない**。設定を直す画面へ
    辿り着けなくなるほうが困る(基盤仕様書 ステップ5)。
    """
    path = config.USER_CONFIG_PATH
    try:
        with _lock:
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("設定ファイルを読めませんでした(既定値で続行): %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def get(key: str, default: Any = None) -> Any:
    """設定を1つ読む。"""
    return load_all().get(key, default)


# ログに値を出さない鍵。伏せるだけで、保存はふつうに行う。
# 共有の写しには撹拌した管理者パスワードが入っている
HIDDEN_IN_LOG = (config.KEY_ADMIN_PASSWORD, config.KEY_SHARED_CACHE)


def save(key: str, value: Any) -> bool:
    """設定を1つ書く。書き込めなければ False を返す。

    **別名で書いてから置き換える。** 途中で落ちても、書きかけの設定
    ファイルが残らない(残ると次の起動で全部既定に戻る)。
    """
    path = config.USER_CONFIG_PATH
    with _lock:
        data = load_all()
        data[key] = value
        tmp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("設定ファイルに書けませんでした: %s", exc)
            return False
    # **値を出さない鍵がある。** 管理者パスワードは撹拌済みだが、
    # ログに残す理由が無い(残せば持ち出せる)
    log.info("設定を保存しました: %s=%s", key,
             "(伏せます)" if key in HIDDEN_IN_LOG else value)
    return True


def auto_import_enabled() -> bool:
    """起動時に取り込み直すか。"""
    value = get(config.KEY_AUTO_IMPORT)
    return config.AUTO_IMPORT_DEFAULT if value is None else bool(value)
