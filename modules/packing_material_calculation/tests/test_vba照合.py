"""現行VBAの画面と、この移植の計算を突き合わせる

【なぜ要るのか】
移植の正しさは、いまのところ**コードの読み比べ**でしか確かめて
いません。判定は5段の優先順位・高さの再計算・1本積み不可の振り分け
など枝が多く、読み比べだけでは「ここは通らないはず」の思い込みが
そのまま残ります。**現物の答え合わせ**がいります。

【どうやるか】
1. `tests/data/照合データ.sql` … 実物の仕掛台帳・梱包資材マスタから
   12件ぶんだけ切り出して凍らせたもの。台帳は日々入れ替わるので、
   共有フォルダを見に行く形だと**同じ条件で二度と走らせられない**
2. `docs/検証/VBA実績.csv` … 現行VBAの画面に出た値を書き写す表。
   入力(LotNo・検入数・受注番号・外径)は埋めてあるので、
   **結果の欄を埋めるだけ**
3. この試験が、埋まっている欄だけを突き合わせる

**まだ埋まっていない行は飛ばします**(値が無いのに落ちると、
埋める前から赤くなって意味が無い)。埋めた行から順に効いてきます。

やり方は `docs/検証/照合のやり方.md`。
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from modules.packing_material_calculation.coil_tool import calc_service, db, lot_service
from modules.packing_material_calculation.coil_tool.models import CalcState

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "data" / "照合データ.sql"
# 最初の12件のあとに足したもの。**先に入った行が勝つ**(INSERT OR IGNORE)
EXTRA_FIXTURES = (
    ROOT / "tests" / "data" / "照合_包装仕様マスタ.sql",      # 包装仕様ぜんぶ
    ROOT / "tests" / "data" / "照合データ_書き出しから.sql",  # 現場の書き出しから
)
SHEET = ROOT / "docs" / "検証" / "VBA実績.csv"

# 画面の欄 ── CSV の列名は `CalcState` の欄名と同じにしてある
RESULT_COLUMNS = (
    "パレット種類", "パレット名称", "パレットサイズ",
    "積数", "台数", "Re_積数", "Re_台数", "総高さ",
    "コイル間", "リプラサイズ", "最下部本数", "最下部長さ", "最下部長さ_短",
    "長い本数", "短い本数", "リプラ長さ", "本数",
    "IMI中間長さ", "IMI中間本数", "緩衝材", "HB枚数", "TotalC",
)


# 画面の色 ── VBA は「どの規則で決まったか」を色で出していた。
# 現場が見て判断に使っているので、値と同じだけ大事
RED_FIELDS = {"重量": "weight", "枚数": "count",
              "高さ": "height", "包装仕様": "spec"}
BORDER_COLORS = {"水色": "no_single", "赤": "single_split"}


def rows() -> list[dict]:
    with SHEET.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def conn():
    """凍らせた実データを読み込んだDB。"""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.apply_schema(c)
    c.executescript(FIXTURE.read_text(encoding="utf-8"))
    for extra in EXTRA_FIXTURES:
        if extra.exists():
            c.executescript(extra.read_text(encoding="utf-8"))
    c.commit()
    yield c
    c.close()


def 計算(conn, row: dict) -> tuple[CalcState, object]:
    state = CalcState(LOT=row["LotNo"], 検入数=row["検入数"])
    assert lot_service.load_into(conn, state, row["受注番号"]), \
        f'{row["LotNo"]}: 受注 {row["受注番号"]} を展開できません'
    # 外径は**VBAの画面に出ていた値**をそのまま使う。既定(ｺｲﾙ外径_MAX)
    # のままのことが多いが、現場は打ち直すこともある ── 同じ入力で
    # 比べないと答え合わせにならない
    if row.get("外径", "").strip():
        state.外径 = row["外径"].strip()
    return state, calc_service.calculate(conn, state)


# ==================================================================
# 表そのものの見張り
# ==================================================================
def test_表の行はすべて凍らせたデータで引ける(conn):
    """台帳の切り出しと表がずれていないか。"""
    for row in rows():
        lot = row["LotNo"]
        candidates = lot_service.find_order_numbers(conn, lot)
        assert candidates, f"{lot}: 仕掛引当に居ません"
        assert row["受注番号"] in candidates, (
            f'{lot}: 表の受注 {row["受注番号"]} が候補 {candidates} にありません')


def test_どの行も計算が落ちない(conn):
    """答え合わせの前に、**例外で止まらない**ことだけは確かめる。"""
    for row in rows():
        計算(conn, row)


# ==================================================================
# 答え合わせ
# ==================================================================
def 既知の違い(row: dict) -> tuple[set[str], str]:
    """`既知の違い` の欄。`欄,欄|理由` か `断る|理由`。

    **VBA のほうがおかしい**と分かっていて、移植はあえて合わせないもの。
    黙って飛ばさず、理由を表に書いたうえで、その欄だけ比べない。
    """
    text = row.get("既知の違い", "").strip()
    if not text:
        return set(), ""
    columns, _, reason = text.partition("|")
    return {c.strip() for c in columns.split(",") if c.strip()}, reason.strip()


@pytest.mark.parametrize("row", rows(), ids=lambda r: r["LotNo"])
def test_VBAの画面と同じ値になる(conn, row):
    """**空欄も比べる。** 表は書き出しから作っているので、VBA の画面が
    空だったことも答えのうち(空と見て比べずにいたら、VBA が途中で
    止まって空のままの欄を見落としていた)。"""
    written = any(row.get(c, "").strip() for c in RESULT_COLUMNS)
    refused = row.get("断られた", "").strip()
    if not written and not refused:
        pytest.skip(f'{row["LotNo"]}: まだVBAの値を書き写していません'
                    "(docs/検証/照合のやり方.md)")
    known, why = 既知の違い(row)
    filled = {c: row.get(c, "").strip() for c in RESULT_COLUMNS if c not in known}

    state, result = 計算(conn, row)

    if "断る" in known:
        # VBA は計算を続けてしまうが、移植は断る(理由は表に)
        assert not result.ok, f'{row["LotNo"]}: 断るはずが台数 {state.台数} を出しました'
        assert state.messages, f'{row["LotNo"]}: 断った理由を出していません'
        return

    if refused:
        assert not result.ok, (
            f'{row["LotNo"]}: VBAは「{refused}」と断ったのに、こちらは'
            f"台数 {state.台数} を出しました")
        assert state.messages, f'{row["LotNo"]}: 断った理由を出していません'
        return

    assert result.ok, (
        f'{row["LotNo"]}: こちらは計算できませんでした'
        f"({result.reason} / {state.messages})")

    違い = {c: (期待, getattr(state, c)) for c, 期待 in filled.items()
            if str(getattr(state, c)).strip() != 期待}
    assert not 違い, f'{row["LotNo"]} が VBA と違います(VBA, こちら): ' + str(違い)

    # 色 ── 書いてあれば突き合わせる。**どの規則で決まったか**が
    # 合っていないと、値がたまたま合っているだけかもしれない
    赤 = row.get("赤い欄", "").strip()
    if 赤:
        期待 = {RED_FIELDS[name.strip()] for name in 赤.replace("、", ",").split(",")
                if name.strip()}
        いま = {f for f in state.flags if f in set(RED_FIELDS.values())}
        assert いま == 期待, (
            f'{row["LotNo"]}: 赤くなる欄が違います(VBA {sorted(期待)} / '
            f"こちら {sorted(いま)})")

    枠 = row.get("枠の色", "").strip()
    if 枠:
        期待枠 = BORDER_COLORS.get(枠)
        assert 期待枠 is not None, f'{row["LotNo"]}: 枠の色は「水色」か「赤」か空で'
        assert 期待枠 in state.flags, (
            f'{row["LotNo"]}: 枠が {枠} になるはずですが、こちらは '
            f"{sorted(state.flags)} です")


# ==================================================================
# 台帳の写しが無くても、書き出しだけで照合できる
# ==================================================================
DUMPS = ROOT / "tests" / "data" / "VBA書き出し_20260923"


def test_書き出しだけで組み立てても_VBAと同じ答えになる():
    """珍しい仕様のロットは「来ないときは来ない」。

    来た日に台帳を写して送ってもらうのは手間なので、**マクロの書き出し
    だけ**で照合できるようにしてある。書き出しには受注の中身(板厚・板幅・
    製品単重…)も画面の欄として入っていて、VBA はまさにその値で計算して
    いるから。

    ここでは台帳の写し(仕掛引当・仕掛受注)を**使わずに**、実物の
    書き出しから受注を組み立て、VBA の画面と同じ答えになることを見る。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "照合取り込み", ROOT / "tools" / "照合取り込み.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.apply_schema(c)
    # パレット・リプラサイズ・包装仕様だけ入れる(台帳の行は入れない)
    base = [line for line in FIXTURE.read_text(encoding="utf-8").splitlines()
            if not line.startswith(('INSERT INTO "仕掛引当"', 'INSERT INTO "仕掛受注"'))]
    c.executescript("\n".join(base))
    c.executescript((ROOT / "tests/data/照合_包装仕様マスタ.sql").read_text(encoding="utf-8"))

    dumped = []
    for path in sorted(DUMPS.glob("*.csv")):
        dumped.extend(tool.read_dump(path))
    runs = tool.latest_per_lot(dumped)
    assert len(runs) >= 7

    for lot, controls in runs.items():
        c.executescript(tool.order_sql(lot, controls))
        vba = tool.to_sheet_row(controls)
        state = CalcState(LOT=lot, 検入数=vba["検入数"])
        assert lot_service.load_into(c, state, vba["受注番号"]), lot
        state.外径 = vba["外径"]
        result = calc_service.calculate(c, state)
        assert result.ok, (lot, state.messages)
        違い = {k: (v, getattr(state, k)) for k, v in vba.items()
                if k in RESULT_COLUMNS and v and str(getattr(state, k)).strip() != v}
        assert not 違い, f"{lot}: {違い}"
    c.close()


# ==================================================================
# 疑似ロットのマクロが、本物の計算Start と同じ計算をしているか
# ==================================================================
def test_対照の疑似ロットは_実物のL6052G0と同じ答えになる():
    """疑似ロットは、計算Start ボタンの中身を写したマクロで計算する
    (ボタンが Private でマクロから押せないため)。

    **写し間違いがあると、疑似ロットの答えは VBA の答えではなくなる。**
    そこで先頭に、実物で答え合わせ済みの L6052G0 と同じ値の疑似ロット
    (Q0118A0)を入れてある。2つの画面が同じなら、写しは本物と同じ。
    """
    by_lot = {r["LotNo"]: r for r in rows()}
    real, pseudo = by_lot.get("L6052G0"), by_lot.get("Q0118A0")
    if not pseudo or not any(pseudo[c].strip() for c in RESULT_COLUMNS):
        pytest.skip("疑似ロットの書き出しがまだ届いていません")
    columns = (*RESULT_COLUMNS, "赤い欄", "枠の色")
    違い = {c: (real[c], pseudo[c]) for c in columns if real[c].strip() != pseudo[c].strip()}
    assert not 違い, ("疑似ロットのマクロの計算が、本物の計算Start と違います"
                      f"(実物, 疑似): {違い}")
