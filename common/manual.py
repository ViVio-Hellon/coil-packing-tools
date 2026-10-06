"""操作説明書(統合 1.2.0)── 上の帯の「説明書」から開く、写真入りの説明

    /manual/            目次(4冊の一覧と、いま動いている版)
    /manual/<key>       shell(はじめに・統合画面) / details / pena / material

【写真】
`tools/make_manual_shots.py` が、試験用のデータで統合アプリを本当に起動し、
本物の画面を撮って `static/manual/img/` に置く。撮ったときの版・大きさは
`static/manual/shots.json` に書く。画面を変えたら撮り直す(人の手で切り抜かない)。

【版の表示】
説明書の頭に**いま動いている版**を出す(統合ツールと、その機能)。写真を撮った版と
違うときは「写真は VER○○ の画面」と断る(画面が少し違うことがある)。

【開き方】
**ダウンロードにしない。** HTML のまま、統合画面の中(上の帯の「説明書」のダイアログ)か、
別の窓(デスクトップ版)・別のタブ(ブラウザ版)で開く。写真や CSS は静的ファイルなので、
デスクトップ版では外枠(Rust)が直接返す。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import app_config, versions

APP_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_ROOT / "static"
SHOTS_META = STATIC_DIR / "manual" / "shots.json"

#: 説明書の並び。(キー, 見出し, 版のキー(`versions.all_versions()`), 一言)
MANUALS = (
    ("shell", "はじめに・統合画面", "app",
     "起動と終了・タブの切り替え・画面の色・ログ・版の見方。3つの機能に共通のこと"),
    ("details", "梱包明細", "details",
     "ロットを開いて重量を入れ、条番号を積んで明細表を出す。履歴からの作り直し・設定"),
    ("pena", "ペナラベル", "pena",
     "重量を反映して小ラベルを刷る・風袋計算・全サイズ・印刷の位置合わせ・設定"),
    ("material", "資材計算", "material",
     "ロットから資材の員数を出し、チェックリストに積んで発注票を作る・設定"),
)
KEYS = tuple(m[0] for m in MANUALS)


def shots_meta() -> dict:
    """写真の控え(撮った日・撮ったときの版・写真ごとの大きさ)。無ければ空。"""
    try:
        data = json.loads(SHOTS_META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def manual_list(current: dict) -> list:
    """目次と、説明書どうしの行き来に使う一覧(いまの版つき)。"""
    return [{"key": key, "title": title, "version": current.get(vkey, ""),
             "version_key": vkey, "summary": summary}
            for key, title, vkey, summary in MANUALS]


def find(key: str) -> Optional[dict]:
    current = versions.all_versions()
    for item in manual_list(current):
        if item["key"] == key:
            return item
    return None


def context(key: str = "") -> dict:
    """説明書の画面に渡すもの。`key` が空なら目次。"""
    current = versions.all_versions()
    meta = shots_meta()
    shot_versions = meta.get("versions") or {}
    item = None
    stale = []
    for entry in manual_list(current):
        if entry["key"] == key:
            item = entry
    # 写真を撮ったときの版と、いま動いている版の食い違い(この説明書の写真に関わる版だけ)。
    # 統合ツールの版は配るたびに上がるので、各ツールの説明書はそのツールの版だけを比べる
    # (ツールの画面が変わるのは、そのツールの版が上がったときだけ)。目次には写真が無い
    check = (item["version_key"],) if item else ()
    for vkey in check:
        then = shot_versions.get(vkey)
        if then and then != current.get(vkey):
            stale.append({"key": vkey, "then": then, "now": current.get(vkey, "")})
    return {
        "display_name": app_config.display_name(),
        "app_version": current.get("app", ""),
        "versions": current,
        "manuals": manual_list(current),
        "manual": item,
        "images": meta.get("images") or {},
        "taken": meta.get("taken", ""),
        "stale": stale,
    }
