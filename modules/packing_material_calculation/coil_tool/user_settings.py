"""利用者ごとの設定の永続化 (VBA `GetSetting` / `SaveSetting` の代替)

VBA 版は Windows レジストリに保存していた。Python 版はレジストリに依存
しないよう JSON ファイル(`config.USER_CONFIG_PATH`)に保存する。

読み書きのたびにファイルを開く。外部から書き換えても次回の読み取りで
反映されるので、レジストリと同じ感覚で使える。

**設定画面が書き、`config` の `*_dir()` が読む。** 置き場所の唯一の
出どころをここ1か所にしておくことで、「画面では変えたのに取り込みは
古い場所を見ている」が起きない。
"""
from __future__ import annotations

import json
from typing import Any

from . import config
from .logging_utils import get_logger

log = get_logger("user_settings")


def load_all() -> dict[str, Any]:
    """設定ファイル全体を読む。壊れていても例外にせず空扱いにする。

    壊れたファイルで起動そのものを止めない。既定値で動いて、
    画面に「読めなかった」と出すほうが原因を追いやすい。
    """
    path = config.USER_CONFIG_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("設定ファイルを読めませんでした(既定値で続行): %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def get(key: str, default: Any = None) -> Any:
    """VBA `GetSetting` 相当。"""
    return load_all().get(key, default)


def save(key: str, value: Any) -> bool:
    """VBA `SaveSetting` 相当。書き込めなければ False を返す。"""
    return save_many({key: value})


def save_many(values: dict[str, Any]) -> bool:
    """まとめて保存する。

    設定画面は複数の欄を1回の「保存」で受けるので、1つずつ書くと
    途中で失敗したときに**半分だけ変わった**状態が残る。まとめて
    1回書けばそれが起きない。
    """
    data = load_all()
    data.update(values)
    try:
        config.USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.USER_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("設定ファイルに書けませんでした: %s", exc)
        return False
    log.info("設定を保存しました: %s", ", ".join(f"{k}={v!r}" for k, v in values.items()))
    return True


def clear(key: str) -> bool:
    """設定を消して既定値へ戻す。"""
    data = load_all()
    if key not in data:
        return True
    data.pop(key)
    try:
        config.USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.USER_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("設定ファイルに書けませんでした: %s", exc)
        return False
    log.info("設定を既定へ戻しました: %s", key)
    return True


# ------------------------------------------------------------------
# ライン (VBA `UF_Material` の LS4 / NS1)
# ------------------------------------------------------------------
def get_line() -> str:
    value = get(config.KEY_LINE)
    if isinstance(value, str) and value in config.LINES:
        return value
    return config.DEFAULT_LINE


def set_line(value: str) -> bool:
    if value not in config.LINES:
        raise ValueError(f"未知のライン: {value!r}")
    return save(config.KEY_LINE, value)


# ------------------------------------------------------------------
# 担当者 (VBA `WorkerSelect`)
# ------------------------------------------------------------------
def get_worker() -> str:
    """選んである担当者。未選択なら空文字。

    **既定で誰かを選んでおかない。** 依頼者欄は誰が出したかの記録なので、
    選び忘れに気づけるほうがよい(VBA も初期状態は未選択だった)。

    **名簿と突き合わせない。** 担当者は梱包資材マスタの「班員名簿」から
    来るので、ここで照合すると**名簿を読むために DB 接続が要る**ことに
    なる(この層は設定ファイルだけを見る)。それに、異動で名簿から消えた
    人の名前をここで黙って落とすと、**選んだはずの担当者が空に戻る**。
    名簿に在るかどうかは `coil_tool/staff.py` が見て、画面が言う。
    """
    value = get(config.KEY_WORKER)
    return value if isinstance(value, str) else ""


def set_worker(value: str) -> bool:
    """担当者を書く。**許してよい名前かは呼ぶ側が見る**(`staff.is_known`)。"""
    return save(config.KEY_WORKER, value)


# ------------------------------------------------------------------
# 起動時の自動取り込み
# ------------------------------------------------------------------
def get_auto_import() -> bool:
    value = get(config.KEY_AUTO_IMPORT)
    return bool(value) if isinstance(value, bool) else config.AUTO_IMPORT_DEFAULT


def set_auto_import(value: bool) -> bool:
    return save(config.KEY_AUTO_IMPORT, bool(value))
