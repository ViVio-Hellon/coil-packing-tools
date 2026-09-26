"""版と変更履歴を食い違わせない

【なぜ要るのか】
現場から「ずっと VER0.1.0 ですけどおかしくないですか」と上がった。
そのとおりで、**版を上げるのを忘れたまま13回変更していた**。

版は「いまどれが入っているか」を電話越しに答えるための番号なので、
中身が変わっているのに番号が同じだと、**番号が役に立たなくなる**。
「直ったはずなのに直っていない」の切り分けができない。

機械は「上げるべきときか」を知れないが、**上げたのに書き忘れた**
(その逆も)は見つけられる。そこだけ見張る。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from modules.packing_material_calculation.coil_tool import app_config

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "docs" / "変更履歴.md"
APP_JSON = ROOT / "config" / "app.json"


def _changelog_versions() -> list[str]:
    """変更履歴に出てくる版(`### 0.2.0` の形)を、書いてある順に。"""
    return re.findall(r"^###\s+(\d+\.\d+\.\d+)\s*$",
                      CHANGELOG.read_text(encoding="utf-8"), re.M)


def test_いまの版が変更履歴にある():
    """**上げたのに書き忘れた**を見つける。

    番号だけ動いて中身が分からないと、現場は何が変わったか聞けない。
    """
    versions = _changelog_versions()
    assert app_config.version() in versions, (
        f"config/app.json の版 {app_config.version()} が "
        f"docs/変更履歴.md にありません(あるのは {versions})")


def test_変更履歴のいちばん上がいまの版():
    """**書いたのに上げ忘れた**を見つける。新しい順に並べる。"""
    versions = _changelog_versions()
    assert versions, "変更履歴に版が1つもありません"
    assert versions[0] == app_config.version(), (
        f"変更履歴のいちばん上は {versions[0]} ですが、"
        f"config/app.json は {app_config.version()} です")


def test_版は新しい順に並んでいる():
    def key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))
    versions = _changelog_versions()
    assert versions == sorted(versions, key=key, reverse=True), \
        f"変更履歴の版の並びが新しい順ではありません: {versions}"


def test_版の書き方が正しい():
    """数字3つ。崩れていると「どれが新しいか」を並べて比べられない。"""
    assert app_config.version_problem() == "", app_config.version_problem()


def test_出どころは1つ():
    """版を持つのは `config/app.json` だけ。

    帯・設定画面・`/api/health`・起動待機画面・診断起動は、すべて
    ここを読む。2か所に持つと、片方だけ上げたときに食い違う。
    """
    raw = json.loads(APP_JSON.read_text(encoding="utf-8"))
    assert raw["version"] == app_config.version()


def test_0_1_0_から上がっている():
    """最初の版のまま配り続けない、という戒め。"""
    assert app_config.version() != "0.1.0", (
        "版が最初のままです。中身を変えたら上げてください"
        "(上げ方は docs/変更履歴.md)")
