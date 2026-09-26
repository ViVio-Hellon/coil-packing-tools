"""画面の外枠 ── どの画面にも共通の帯とレール

画面ごとの中身はテンプレートが持ち、ここは**どの画面が在るか**と、
帯に出す事実(版・取り込み日時・担当者)だけを組む。
"""
from __future__ import annotations

from dataclasses import dataclass

from modules.packing_material_calculation.coil_tool import app_config, config, data_sync, staff, user_settings


@dataclass(frozen=True)
class Page:
    key: str
    path: str
    label: str
    detail: str


# レールに並ぶ画面。**この並びが業務の順番**でもある
PAGES: tuple[Page, ...] = (
    Page("calc", "/calc", "資材計算",
         "ロットから資材の員数を出す"),
    Page("checklist", "/checklist", "チェックリスト",
         "計算結果を15行に積む"),
    Page("order", "/order", "発注票",
         "チェックリストから倉庫へ出す票を作る"),
    Page("settings", "/settings", "設定",
         "取り込み元の置き場所・マスタ管理・ライン・担当者"),
)


def page(key: str) -> Page:
    for p in PAGES:
        if p.key == key:
            return p
    raise ValueError(f"未知の画面: {key!r}")


def ribbon(conn) -> dict:
    """帯に出す事実。

    **取り込み日時をいつも出す。** マスタは取り込んだ時点の写しなので、
    古いまま計算していると気づけない(基盤仕様書の言う「古いデータを
    保持したままにならないか」への答え)。
    """
    imported = data_sync.imported_at(conn)
    worker = user_settings.get_worker()
    return {
        "display_name": app_config.display_name(),
        "version_label": app_config.version_label(),
        "line": user_settings.get_line(),
        "lines": list(config.LINES),
        "worker": worker,
        # 担当者は梱包資材マスタの「班員名簿」から、**担当ラインが
        # 機側の人だけ**。名簿が取り込めていなければ予備の一覧へ落ちる
        # (担当者が無いとチェックリストへ積めないので、名簿が読めない
        # だけで仕事を止めない)
        "workers": staff.choices(conn, current=worker),
        "imported_at": imported,
        "stale": data_sync.is_stale(conn),
        # **なぜ古いのか**も出す。20分前に取り込んだ人が日付を
        # またいだだけで「古い」と言われると、壊れたように見える
        "stale_why": data_sync.stale_message(conn),
    }
