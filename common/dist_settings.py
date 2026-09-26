r"""配布設定の置き場所(統合版で1つにした決まり)

3機能はどれも「1台で決めた設定を書き出し、アプリのフォルダごと配ると、
配った先が起動時に読み込む」仕組み(配布設定)を持っている。統合版では

* **値は機能ごとのまま。** 項目・合言葉・読み込みの決まりが機能ごとに
  違うので、1つに混ぜない。書き出すのも各機能の設定画面から。
* **置き場所の決まりだけを1つにする。** 統合アプリのフォルダの直下の
  ``配布設定\<機能>\``。中身はどの機能も ``設定.json`` と ``はじめに読む.txt``。

::

    配布設定\
      packing_details\               梱包明細
      packing_pena_label\            ペナラベル
      packing_material_calculation\  資材計算

配布用フォルダを作る処理(``scripts/make_dist.py``)は、ここに並べた3機能の
配布設定をまとめて入れる(入れないと決めたら、どれも入れない)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple, Union

from .app_config import APP_ROOT

#: 統合アプリのフォルダの直下に作るフォルダの名前
ROOT_NAME = "配布設定"

#: 配布設定を持つ機能: (フォルダ名, 画面での呼び名, 書き出す場所を知っているモジュール)
#: **書き出す場所は各機能の ``distribution.DIR`` が正本**(環境変数で変えられる)。
MODULES: Tuple[Tuple[str, str, str], ...] = (
    ("packing_details", "梱包明細",
     "modules.packing_details.meisai.distribution"),
    ("packing_pena_label", "ペナラベル",
     "modules.packing_pena_label.app.services.distribution"),
    ("packing_material_calculation", "資材計算",
     "modules.packing_material_calculation.coil_tool.distribution"),
)


def default_dir(module: str) -> Path:
    """その機能の配布設定の既定の置き場所(``<統合アプリ>\\配布設定\\<機能>``)。"""
    if module not in {key for key, _, _ in MODULES}:
        raise ValueError(f"配布設定を持たない機能です: {module}")
    return APP_ROOT / ROOT_NAME / module


def where(path: Union[str, Path]) -> str:
    """案内の文に出す置き場所。アプリのフォルダの中なら、そこからの道筋。

    外(環境変数で変えた場合)なら丸ごと。
    """
    p = Path(path)
    try:
        rel = p.resolve().relative_to(APP_ROOT.resolve())
    except (OSError, ValueError):
        return str(p)
    return "\\".join(rel.parts)
