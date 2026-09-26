"""担当者(依頼者)の名簿 ── 梱包資材マスタの「班員名簿」から読む

VBA `UF_Material` は担当者を **W1〜W7 の7個のボタン**として画面に
直書きしていた。異動のたびにコードを直す必要があり、端末ごとに写しが
ずれる原因になっていた(置き場所を `Public Const` に書いていたのと
同じ問題)。

【名簿はすでにある】
姉妹ツール(`vba-daily-report-python-migration`)の人員フォームが、
**同じ梱包資材マスタの中の「班員名簿」**を読んでいる
(VBA `人員()` / 標準モジュール ~4758行)。列は
`管理番号 / 苗字 / 班 / 名前 / 読み / 担当ライン`。

このツールも同じ表を読む。**名簿を二重に持たない** ── 持つと、
異動があったときに片方だけ古くなり、どちらが本当か分からなくなる。
書くのは日報ツール側で、こちらは**読むだけ**。

【並び】
姉妹ツールの `group_by_team()` と同じにする。
「読み」で五十音順に並べてから「班」で安定ソートする ── 同じ班の中では
読み順が保たれる(VBA のバブルソートが安定ソートだったことに由来)。

【誰を出すか ── 担当ラインが「機側」の人だけ】(不明点 Q18 / Q19 の回答)
名簿には機側以外の人も載っている。資材を発注するのは機側なので、
**担当ラインが「機側」の人だけ**を担当者の一覧に出す。

班では絞らない。姉妹ツール(日報)は知らない班の人を落とすが、これは
`GenerateOptionButtons` が班ごとに画面の列(left座標)を割り当てていて
7つめの班を置く場所が無かったためで、**画面の作りの都合**。こちらは
一覧から選ぶだけなので場所の制約が無く、知らない班の人は末尾の
「班なし」に残す ── ただし機側であれば、が付く。

【機側が1人も居ないときは全員出す】
名簿の担当ラインがまだ埋まっていない(または書き方が違う)端末で
絞ると、**一覧が空になって誰も選べず、チェックリストへ積めない**。
絞れなかったことは画面が言う。黙って空にするより害が小さい。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from . import config
from .sqlite_toolkit import fetch_all

# 姉妹ツールの `TEAM_ORDER` と同じ並び。画面左から右へ A/B/C/D/昼/丸
TEAM_ORDER: tuple[str, ...] = ("A", "B", "C", "D", "昼", "丸")

# 上の並びに無い班の人をまとめる先。**落とさずに残す**ための入れ物
OTHER_TEAM = "班なし"

# 担当者の一覧に出す担当ライン。資材を発注するのは機側なので、
# **名簿のうち機側の人だけ**を出す
MACHINE_SIDE = "機側"


def _machine_side(line: str) -> bool:
    """担当ラインが機側か。

    空白を取り除いて**含むか**で見る ── 「機側」「機側A」「1号機側」の
    ような書き方の揺れで人を落とすと、**その人は依頼者になれない**。
    落とし過ぎるほうが害が大きいので、広めに拾う。
    """
    return MACHINE_SIDE in line.replace(" ", "").replace("\u3000", "")


@dataclass(frozen=True)
class Staff:
    name: str
    team: str = ""
    reading: str = ""
    line: str = ""

    @property
    def on_machine_side(self) -> bool:
        """担当者の一覧に出してよい人か(担当ラインが機側)。"""
        return _machine_side(self.line)

    @property
    def group(self) -> str:
        """画面で束ねる見出し。知らない班は「班なし」へ。"""
        return self.team if self.team in TEAM_ORDER else OTHER_TEAM


def _team_key(team: str) -> int:
    try:
        return TEAM_ORDER.index(team)
    except ValueError:
        return len(TEAM_ORDER)      # 知らない班は末尾


def load(conn: Any) -> list[Staff]:
    """名簿を並べ替えて返す。取り込めていなければ空。

    **例外を投げない。** 名簿が無くても計算はできるので、ここで
    止めると読めないだけで仕事が止まる。
    """
    if conn is None:
        return []
    # `fetch_all` は読めなければ **None を返す**(投げない)。表がまだ
    # 無い端末(取り込み前・スキーマが古い)はここを通る
    rows = fetch_all(
        conn, f"SELECT 名前, 班, 読み, 担当ライン FROM {config.TBL_STAFF}")
    if not rows:
        return []

    members = [
        Staff(name=str(r["名前"]).strip(), team=str(r["班"] or "").strip(),
              reading=str(r["読み"] or "").strip(),
              line=str(r["担当ライン"] or "").strip())
        for r in rows if str(r["名前"]).strip()
    ]
    # 読み順 → 班順。**班の中では読み順が残る**(安定ソート)
    members.sort(key=lambda m: m.reading)
    members.sort(key=lambda m: _team_key(m.team))
    return members


def selectable(conn: Any) -> list[Staff]:
    """名簿のうち**担当ラインが機側**の人。

    絞った結果が空なら、絞らずに全員を返す ── 担当ラインが埋まって
    いない端末で一覧が空になると、**誰も選べずチェックリストへ
    積めない**。絞れたかどうかは `choices()` が画面へ渡す。
    """
    everyone = load(conn)
    narrowed = [m for m in everyone if m.on_machine_side]
    return narrowed or everyone


def names(conn: Any) -> list[str]:
    """担当者に出す名前だけ。同じ名前は1つにまとめる(並びは保つ)。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in selectable(conn):
        if m.name not in seen:
            seen.add(m.name)
            out.append(m.name)
    return out


def choices(conn: Any, *, current: str = "") -> dict[str, Any]:
    """画面に出す担当者の一覧。

    **名簿が空でも選べる形で返す。** 担当者が無いとチェックリストへ
    積めないので、名簿が読めないだけで仕事が止まらないようにする
    (`config.WORKERS` の予備へ落ちる)。

    いま選ばれている人が一覧に居ない場合(異動・退職のあと、あるいは
    機側から外れたあと)も、その名前だけは残す ── 黙って別人に変わる
    より、出したうえで気づかせる。
    """
    everyone = load(conn)
    from_master = bool(everyone)
    members = [m for m in everyone if m.on_machine_side]
    # **機側が1人も居ないときは絞らない。** 一覧が空だと誰も選べない
    narrowed = bool(members)
    if not narrowed:
        members = everyone

    if from_master:
        groups: list[dict[str, Any]] = []
        for team in (*TEAM_ORDER, OTHER_TEAM):
            in_team = [m.name for m in members if m.group == team]
            if in_team:
                groups.append({"team": team, "names": in_team})
        flat = [m.name for m in members]
    else:
        groups = [{"team": "", "names": list(config.WORKERS)}]
        flat = list(config.WORKERS)

    stale = bool(current) and current not in flat
    # なぜ一覧に無いのかを分けて言う。「名簿から消えた」と
    # 「名簿には居るが機側ではない」は、現場の次の動きが違う
    in_roster = any(m.name == current for m in everyone)
    stale_why = ""
    if stale:
        stale_why = "not_machine_side" if in_roster else "not_in_roster"
        label = (f"担当ラインが{MACHINE_SIDE}ではありません"
                 if in_roster else "名簿にありません")
        # 末尾に置く。**消さない**が、一覧に無い理由は画面が言う
        groups.append({"team": label, "names": [current]})
        flat.append(current)

    return {
        "groups": groups,
        "names": flat,
        "from_master": from_master,
        # 担当ラインで絞れたか。絞れていないことに気づけないと、
        # 「機側以外の人が出ている」を不具合だと思われる
        "narrowed": narrowed,
        "line": MACHINE_SIDE,
        "hidden": len(everyone) - len(members) if narrowed else 0,
        "stale": stale,
        "stale_why": stale_why,
        "count": len(flat),
    }


def is_known(conn: Any, name: str, *, current: str = "") -> bool:
    """その名前を依頼者にしてよいか。

    名簿が取り込めていないときは予備の一覧で見る。いま選ばれている
    名前は、一覧に無くても通す ── 通さないと、異動のあった端末や
    機側から外れた人の端末で、**設定を開いただけで弾かれる**。
    """
    if not name:
        return True                                # 未選択は許す
    return name in set(choices(conn, current=current)["names"])


def default_for(conn: Any, current: str = "") -> Optional[str]:
    """いま選ばれている名前が使えるか。使えなければ `None`。"""
    return current if is_known(conn, current, current=current) else None
