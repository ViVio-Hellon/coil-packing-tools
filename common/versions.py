"""統合ツールの版と、3機能それぞれの版(**分けて持つ**)

版は3種類あり、混ぜない。

=================  ==========================================  ==========================
種類               出どころ                                    いつ上げるか
=================  ==========================================  ==========================
統合ツールの版     ``config/app.json`` の ``version``          配るたび。統合画面・共通部分・
                                                               起動/停止/配布を変えたとき、
                                                               または機能の版が1つでも
                                                               上がったとき(配る単位は
                                                               統合ツール)
機能の版           梱包明細・資材計算は                         その機能の中身(画面・計算・
                   ``modules/<機能>/config/app.json``、        印刷・DB)を変えたとき。
                   ペナラベルは ``app/config.py`` の            番号の付け方は機能ごとのまま
                   ``APP_VERSION``(移植元のまま)
移植元の版         各機能の ``PORTED_FROM``                    変えない(取り込んだときの記録)
=================  ==========================================  ==========================

**起動時の入れ替え判定は、統合ツールの版と3機能の版の組(`version_set`)で比べる。**
機能の版だけを上げて配っても、古いプロセスに合流せずに入れ替わる
(統合ツールの版だけで比べると、新しい中身がいつまでも動かない)。

ここは Flask を読まない(起動の早いうちに `launch_guard` が使う)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import app_config

#: 統合ツールそのものを表す鍵(`version_set` の中の名前)
APP_KEY = "app"


def _modules():
    # 3機能の入口。`__init__` は軽い(重いものは関数の中で読む)
    from modules import packing_details, packing_material_calculation, packing_pena_label
    return (packing_details, packing_pena_label, packing_material_calculation)


def module_versions() -> List[dict]:
    """3機能の版。タブの並び順。"""
    out = []
    for m in _modules():
        ported = dict(getattr(m, "PORTED_FROM", {}) or {})
        out.append({"key": m.KEY, "label": m.LABEL, "version": str(m.version()),
                    "ported_from": ported})
    return out


def all_versions() -> Dict[str, str]:
    """``{"app": 統合ツールの版, "details": …, "pena": …, "material": …}``。"""
    versions = {APP_KEY: app_config.version()}
    for m in module_versions():
        versions[m["key"]] = m["version"]
    return versions


def version_set(versions: Optional[Dict[str, str]] = None) -> str:
    """版の組を1行に。起動時の入れ替え判定で比べる値。

    例 ``app=1.0.0;details=0.13.1;pena=1.5.0;material=0.2.0``
    """
    versions = versions if versions is not None else all_versions()
    keys = [APP_KEY] + sorted(k for k in versions if k != APP_KEY)
    return ";".join(f"{k}={versions.get(k, '')}" for k in keys)


def parse_set(text: str) -> Dict[str, str]:
    """``version_set`` を読み戻す。読めない部分は捨てる。"""
    out: Dict[str, str] = {}
    for part in (text or "").split(";"):
        key, sep, value = part.partition("=")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def _labels() -> Dict[str, str]:
    labels = {APP_KEY: app_config.display_name()}
    for m in _modules():
        labels[m.KEY] = m.LABEL
    return labels


def describe(versions: Optional[Dict[str, str]] = None) -> str:
    """人に見せる形。例 ``コイル梱包ツール 1.0.0(梱包明細 0.13.1 / ペナラベル 1.5.0 / 資材計算 0.2.0)``"""
    versions = versions if versions is not None else all_versions()
    labels = _labels()
    parts = [f"{labels.get(k, k)} {v}" for k, v in versions.items() if k != APP_KEY]
    return f"{labels[APP_KEY]} {versions.get(APP_KEY, '')}(" + " / ".join(parts) + ")"


def differences(running: Dict[str, str], mine: Dict[str, str]) -> List[str]:
    """違う版の並び。例 ``["梱包明細 0.13.1 → 0.13.2"]``。無ければ空。"""
    labels = _labels()
    out = []
    for key in [APP_KEY] + [k for k in mine if k != APP_KEY]:
        old, new = running.get(key, ""), mine.get(key, "")
        if old != new:
            out.append(f"{labels.get(key, key)} {old or '(不明)'} → {new or '(なし)'}")
    for key in running:
        if key not in mine:
            out.append(f"{labels.get(key, key)} {running[key]} → (なし)")
    return out
