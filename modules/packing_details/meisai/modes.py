"""このアプリのモード

**モードは1つしかありません。**

移植元(`python-web-tools` / 梱包資材総合ツール)は現場と資材の2モードを
持っていて、起動基盤(`launch_guard` / `process_manager` / `server`)は
どれを起動・停止するのかをモード名で受け渡します。このアプリは
**梱包明細を打ち出す1つの仕事しかしない**ので、モードで分ける必要が
ありません。

それでも名前を1つ置いてあるのは、起動基盤をそのまま流用するためです。
あちらは「ロックファイルの名前」「ポートの引き方」「`/api/health` が
名乗る身元」をモード名で決めており、ここを取り去ると3ファイルを
書き換えることになります。**実績のある起動・停止の手順に手を入れない**
ほうが安全なので、1つだけ持つ形にしました。

モードを増やす予定はありません。増やしたくなったら、それは
**別のアプリ**である可能性を先に疑ってください ── 梱包明細と資材選定を
1つのプロセスに同居させないことが、この分割の出発点です。
"""
from __future__ import annotations

# 唯一のモード。ロックファイル名(`runtime/meisai.lock`)とポートの
# 引き当てキー(`config/app.json` の `server.roles`)に使う
MEISAI = "meisai"

DEFAULT = MEISAI

# 起動基盤が「使えるモードの一覧」として参照する
KEYS: tuple[str, ...] = (MEISAI,)
ALL = KEYS

# 旧名の読み替え表。移植元は `warehouse` → `material` の改名を抱えて
# いたのでこの仕組みがある。こちらは改名の歴史が無いので空
LEGACY_NAMES: dict[str, str] = {}


def normalize(mode: str) -> str:
    """モード名をそろえる。空や未知の名前は既定のモードにする。

    **断らないのが要点です。** モードが1つしかない以上、どんな値を
    渡されても行き先は1つに決まります。ここで例外を投げると、
    コマンドラインの打ち間違い(`--mode field` など、移植元の名前を
    そのまま打った場合)がアプリの起動失敗として現れ、**直す先が
    分からない**形で止まります。
    """
    key = (mode or "").strip().lower()
    return LEGACY_NAMES.get(key, key) if key in KEYS or key in LEGACY_NAMES else DEFAULT


def label(mode: str = MEISAI) -> str:
    """画面やログに出す名前。"""
    return "梱包明細"
