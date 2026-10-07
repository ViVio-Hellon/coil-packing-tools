r"""設定・データの保存先 ── 「このPCに残るもの」と「複数のPCで共有するもの」

3機能の設定画面に、**どの値がどこに保存されるか**を出すための共通の言葉と形
(現場の指摘: このPCで引き継いで使うものと、複数のPCで共有するものは違う。
設定部にそういうファイルがあることを明記してほしい)。

分け方は3つ::

    このPCに保存(このPCで引き継ぐ)
        %LOCALAPPDATA%\... の設定ファイル・手元のDB・ログ。閉じても、新しい版に
        入れ替えても(アプリのフォルダを置き換えても)残る。ほかのPCへは移らない。
    複数のPCで共有(全ライン)
        共有フォルダのマスタ・履歴など。変えると、ほかのPCにもすぐ効く。
    配布設定(アプリのフォルダ)
        1台で書き出し、フォルダごと配る。配った先は起動したときに、
        そのPCに**まだ無い項目だけ**「このPCに保存」へ写す。

**値の中身は機能ごと**(何を持っているかは機能が知っている)。ここは言葉と形だけを
1つにする ── 3つの画面で同じことを別の言い方で書くと、読む人が違うものだと思う。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from . import app_config

LOCAL = "local"
SHARED = "shared"
DIST = "dist"

TITLE = "保存先（このPC／複数のPCで共有）"
INTRO = ("設定とデータには、このPCに残して引き継ぐものと、複数のPCで共有するものがあります。"
         "どちらに入っているかで、変えたときに効く範囲が違います。")

LOCAL_TITLE = "このPCに保存（このPCで引き継ぐ）"
LOCAL_LEAD = ("このPCの中だけにあります。ツールを閉じても、新しい版に入れ替えても"
              "（アプリのフォルダを置き換えても）残り、次に起動したときにそのまま引き継ぎます。"
              "ほかのPCへは移りません（Windows の利用者ごとに別です）。"
              "ほかのPCにも同じ設定を入れたいときは「配布設定」を使います。")

SHARED_TITLE = "複数のPCで共有（全ライン）"
SHARED_LEAD = ("共有フォルダにあり、全ラインのPCが同じものを見ます。"
               "ここを変えると、ほかのPCにもすぐ効きます。")

DIST_TITLE = "配布設定（アプリのフォルダ）"
DIST_LEAD = ("1台で書き出し、アプリのフォルダごと配ります。配った先は起動したときに、"
             "そのPCにまだ無い項目だけを「このPCに保存」へ写し、以後はそのPCの設定として"
             "引き継ぎます（すでにある値は上書きしません）。")

#: 設定の欄に付ける短い札。見出しの横に出す
BADGE = {
    LOCAL: "このPCに保存",
    SHARED: "全ラインで共有",
    DIST: "アプリのフォルダ",
}


@dataclass(frozen=True)
class Place:
    """保存先1つ。"""
    name: str            # 呼び名(設定ファイル・手元のDB…)
    path: str            # 場所。画面には実際に置かれている場所で出す(`to_dict`)
    holds: str           # 何が入っているか(利用者の言葉で)
    note: str = ""       # 補足(読むだけ・まだ無い…)


@dataclass
class Places:
    """1機能ぶんの保存先。"""
    local: List[Place] = field(default_factory=list)
    shared: List[Place] = field(default_factory=list)
    dist: List[Place] = field(default_factory=list)
    #: 共有の話の補足(「どこを見るか」という設定そのものはこのPCに保存、など)
    shared_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """画面へ渡す形。**見出し・説明も一緒に渡す**(3画面で同じ言葉にする)。"""
        def rows(items: List[Place]) -> List[Dict[str, str]]:
            # 画面に出すのは実際に置かれている場所(Microsoft Store の Python は
            # %LOCALAPPDATA% の下を別の場所に置く。`app_config.real_location`)
            return [dict(asdict(p), path=app_config.real_location(p.path)) for p in items]
        return {
            "title": TITLE,
            "intro": INTRO,
            "badge": dict(BADGE),
            "groups": [
                {"kind": LOCAL, "title": LOCAL_TITLE, "lead": LOCAL_LEAD,
                 "rows": rows(self.local), "note": ""},
                {"kind": SHARED, "title": SHARED_TITLE, "lead": SHARED_LEAD,
                 "rows": rows(self.shared), "note": self.shared_note},
                {"kind": DIST, "title": DIST_TITLE, "lead": DIST_LEAD,
                 "rows": rows(self.dist), "note": ""},
            ],
        }


def log_place() -> Place:
    """ログ(3機能で1つのフォルダ)。どの機能も同じ行を出す。"""
    from . import logging_utils
    note = logging_utils.log_problem()
    if not note and logging_utils.configured_log_dir():
        note = "出力先を設定しています（その下の PC の名前のフォルダに書きます）"
    return Place("ログ", str(logging_utils.log_dir()),
                 "動いた記録とエラーの記録（3機能共通。なぜなぜ分析に使う）。"
                 "出力先・残す日数は上の帯の「ログ」で変えられます", note)


def log_dist_place() -> Place:
    """配布設定(共通: ログの出力先・残す日数。統合 1.0.14)。どの機能も同じ行を出す。"""
    from . import log_distribution
    path = log_distribution.settings_path()
    return Place("配布設定（共通）", str(path),
                 "上の帯の「ログ」→「出力先の設定」で書き出した値（"
                 + "・".join(label for _, label in log_distribution.ITEMS) + "）。3機能共通",
                 "" if path.is_file() else "まだありません（書き出すと作ります）")
