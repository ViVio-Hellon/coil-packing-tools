"""チェックリストと発注票"""
import pytest

from modules.packing_material_calculation.coil_tool import (calc_service, checklist_service, order_history,
                       order_sheet_service, special_rules)
from modules.packing_material_calculation.coil_tool.models import CalcState, ChecklistRow, OrderInfo
from modules.packing_material_calculation.tests import _fixture
from modules.packing_material_calculation.tests._web import client, sandbox  # noqa: F401


@pytest.fixture
def conn():
    c = _fixture.make_db()
    _fixture.standard_master(c)
    yield c
    c.close()


def calc(conn, *, spec_no="1C0001", 検入数=10, 枚数=5, 外径="900", **spec_over):
    spec_over.setdefault("枚数範囲", "以下")
    _fixture.add_spec(conn, spec_no, **spec_over)
    s = CalcState(LOT="A123456", 検入数=str(検入数), 外径=外径)
    s.order = OrderInfo(包装仕様NO=spec_no, 受注板厚="1.000", 受注板幅="100.0",
                        製品単重="100.00", 用途名="ﾃｽﾄ用途", 営業納期="04/01",
                        梱包単位_重量=OrderInfo.NO_DATA, 梱包単位_枚数=str(枚数))
    calc_service.calculate(conn, s)
    return s


# ==================================================================
# チェックリストの1行
# ==================================================================
def test_standard_row_packs_two_values_per_cell(conn):
    """リプラ長さ・本数は1セルに改行で2つ入る"""
    s = calc(conn, 下本数="3")
    row = checklist_service.standard_row(s, worker="高村")
    assert row.LotNo == "A123456"
    assert row.用途名 == "ﾃｽﾄ用途"
    assert row.営業納期 == "04/01"
    assert row.リプラサイズ == "30×40"
    assert row.リプラ長さ == "900\n800"      # 最下部 / コイル間
    assert row.リプラ本数 == "6\n16"         # 長い / (本数 + 短い本数)
    assert row.緩衝材 == "HB\n2枚"
    assert row.依頼者 == "高村"


def test_pallet_cell_includes_name_for_sukashi(conn):
    """スカシは種類 + 名称 + 改行 + サイズ"""
    s = calc(conn, パレット="スカシ")
    row = checklist_service.standard_row(s, worker="高村")
    assert row.パレット種類 == "スカシP09\n900"


def test_pallet_cell_omits_name_for_zenmen(conn):
    """全面は種類 + サイズ(名称を挟まない)"""
    s = calc(conn, パレット="全面")
    row = checklist_service.standard_row(s, worker="高村")
    assert row.パレット種類 == "全面900"


def test_unit_count_includes_spare(conn):
    """台数は別枠を足した数"""
    s = calc(conn, 検入数=13, 枚数=4, 枚数範囲="1不可")
    assert s.台数 == "2" and s.Re_台数 == "2"
    row = checklist_service.standard_row(s, worker="高村")
    assert row.台数 == "4"


def test_bottom_cleared_when_no_bottom_ripla(conn):
    """最下部本数が 0 なら、最下部の長さも本数も空にする"""
    s = calc(conn, 下本数="0")
    row = checklist_service.standard_row(s, worker="高村")
    assert s.最下部長さ == ""
    assert row.リプラ長さ == "\n800"


# ==================================================================
# 2行になる品
# ==================================================================
def test_imi_cover_makes_two_rows(conn):
    s = calc(conn, spec_no="1C1282", 下本数="3")
    assert s.特殊Flag == special_rules.IMI_COVER_FLAG
    rows = checklist_service.build_rows(s, worker="高村")
    assert len(rows) == 2
    # 1行目は短い本数を足さない (2025.9.8)
    assert rows[0].リプラ長さ == "900\n800"
    assert rows[0].リプラ本数 == "6\n16"
    # 2行目は最下部の短い + 中間
    assert rows[1].リプラ長さ == "600\n950"
    assert rows[1].リプラ本数 == "4\n4"
    assert rows[1].LotNo == "A123456"
    assert rows[1].台数 == ""          # 2行目に台数は出さない


def test_kotobuki_splits_by_spacer_kind(conn):
    s = calc(conn, spec_no="1C1297", 下本数="3",
             コイル間スペーサー="リプラ", 最下部スペーサー="LVS")
    rows = checklist_service.build_rows(s, worker="高村")
    assert len(rows) == 2
    assert rows[0].コイル間 == "リプラ"      # 間リプラ種類
    assert rows[0].リプラ長さ == "800"
    assert rows[1].コイル間 == "LVS"         # 最下部リプラ種類
    assert rows[1].リプラ長さ == "900"


def test_standard_makes_one_row(conn):
    s = calc(conn)
    assert len(checklist_service.build_rows(s, worker="高村")) == 1


# ==================================================================
# 15行の表
# ==================================================================
def test_save_and_load_rows(conn):
    s = calc(conn)
    checklist_service.save_rows(conn, 1, checklist_service.build_rows(s, worker="高村"))
    rows = checklist_service.load_all(conn)
    assert len(rows) == 1
    assert rows[0]["行番号"] == 1 and rows[0]["枝番"] == 0


def test_two_row_item_keeps_one_row_number(conn):
    s = calc(conn, spec_no="1C1282", 下本数="3")
    checklist_service.save_rows(conn, 3, checklist_service.build_rows(s, worker="高村"))
    rows = checklist_service.load_all(conn)
    assert [r["枝番"] for r in rows] == [0, 1]
    assert {r["行番号"] for r in rows} == {3}
    assert checklist_service.next_free_row(conn) == 1


def test_row_number_is_bounded(conn):
    s = calc(conn)
    rows = checklist_service.build_rows(s, worker="高村")
    with pytest.raises(ValueError):
        checklist_service.save_rows(conn, 16, rows)


def test_size_fixed_writes_the_mark(conn):
    s = calc(conn)
    checklist_service.save_rows(
        conn, 1, checklist_service.build_rows(s, worker="高村", size_fixed=True))
    assert checklist_service.load_all(conn)[0]["サイズ確定"] == "〆"


# ==================================================================
# 発注票
# ==================================================================
def test_expand_splits_two_line_cells(conn):
    rows = [{"コイル間": "リプラ", "LotNo": "A1", "リプラサイズ": "30×40",
             "リプラ長さ": "900\n800", "リプラ本数": "6\n16"}]
    details = order_sheet_service.expand(rows)
    assert len(details) == 2
    assert (details[0].長さ, details[0].本数) == ("900", "6")
    assert (details[1].長さ, details[1].本数) == ("800", "16")


def test_expand_keeps_single_line_as_one(conn):
    """改行が無ければ1件のまま(以前は2重に数えていた)"""
    rows = [{"コイル間": "リプラ", "LotNo": "A1", "リプラサイズ": "30×40",
             "リプラ長さ": "800", "リプラ本数": "16"}]
    assert len(order_sheet_service.expand(rows)) == 1


def test_expand_ignores_non_ripla_kinds(conn):
    rows = [{"コイル間": "ザラ板", "LotNo": "A1", "リプラサイズ": "30×40",
             "リプラ長さ": "800", "リプラ本数": "16"}]
    assert order_sheet_service.expand(rows) == []


def test_aggregate_sums_same_size_and_length(conn):
    details = [
        order_sheet_service.Detail("A1", "リプラ", "30×40", "800", "16"),
        order_sheet_service.Detail("A2", "リプラ", "30×40", "800", "8"),
        order_sheet_service.Detail("A3", "リプラ", "30×40", "900", "6"),
    ]
    got = order_sheet_service.aggregate(details, "リプラ")
    by_len = {a.長さ: a for a in got}
    assert by_len["800"].本数 == 24
    assert by_len["800"].lots == ["A1", "A2"]
    assert by_len["900"].本数 == 6


def test_build_sheet_places_quantities_on_stock_lengths(conn):
    s = calc(conn, 下本数="3")
    checklist_service.save_rows(conn, 1, checklist_service.build_rows(s, worker="高村"))
    r = order_sheet_service.build(conn, worker="高村")
    assert r.ok
    sheet = r.sheets[0]
    assert sheet.種類 == "リプラ" and sheet.角サイズ == "30×40"
    by_len = {row.長さ: row for row in sheet.rows}
    assert by_len["900"].数量 == "6"
    assert by_len["800"].数量 == "16"
    assert by_len["900"].LotNo == "A123456"


def test_non_stock_length_is_appended(conn):
    """定尺に無い長さは空欄へ書き足す"""
    _fixture.add_ripla_lengths(conn, 1234)
    s = calc(conn, パレット="全面", 外径="1234", 下本数="3")
    checklist_service.save_rows(conn, 1, checklist_service.build_rows(s, worker="高村"))
    r = order_sheet_service.build(conn, worker="高村")
    assert r.ok
    lengths = [row.長さ for row in r.sheets[0].rows]
    # 定尺8種のあとに書き足されている
    assert lengths[:8] == ["700", "800", "900", "950", "1000", "1050", "1100", "1150"]
    assert any(x and x not in lengths[:8] for x in lengths[8:])


def test_too_many_extra_lengths_is_refused(conn):
    """書き足しが5件以上なら断る(原紙の空欄は4行しかない)"""
    rows = [
        {"コイル間": "リプラ", "LotNo": f"B00000{i}", "リプラサイズ": "30×40",
         "リプラ長さ": str(n), "リプラ本数": "4"}
        for i, n in enumerate((1211, 1222, 1233, 1244, 1255), start=1)
    ]
    for i, row in enumerate(rows, start=1):
        checklist_service.save_rows(conn, i, [ChecklistRow(
            LotNo=row["LotNo"], コイル間=row["コイル間"],
            リプラサイズ=row["リプラサイズ"], リプラ長さ=row["リプラ長さ"],
            リプラ本数=row["リプラ本数"])])
    r = order_sheet_service.build(conn, worker="高村")
    assert not r.ok
    assert r.reason == order_sheet_service.REASON_TOO_MANY_SIZES
    assert len(r.extra_lengths) == 5


def test_four_extra_lengths_still_fit(conn):
    """4件までなら空欄に収まる"""
    for i, n in enumerate((1211, 1222, 1233, 1244), start=1):
        checklist_service.save_rows(conn, i, [ChecklistRow(
            LotNo=f"B00000{i}", コイル間="リプラ", リプラサイズ="30×40",
            リプラ長さ=str(n), リプラ本数="4")])
    r = order_sheet_service.build(conn, worker="高村")
    assert r.ok
    lengths = [row.長さ for row in r.sheets[0].rows]
    assert lengths[8:] == ["1211", "1222", "1233", "1244"]


def test_empty_checklist_is_refused(conn):
    r = order_sheet_service.build(conn, worker="高村")
    assert not r.ok
    assert r.reason == order_sheet_service.REASON_EMPTY


# ==================================================================
# 発注履歴
# ==================================================================
def test_duplicate_within_two_weeks_is_reported(conn):
    order_history.save(conn, 処理日="04/01", 担当者="浜崎", 品名="リプラ",
                       サイズ="30×40", 長さ="800", LotNo="A123456", 数量="16")
    dups = order_history.find_duplicates(conn, ["A123456"])
    assert len(dups) == 1
    assert dups[0].日数表示 == "本日"
    assert "浜崎" in dups[0].message()


def test_no_duplicate_for_other_lot(conn):
    order_history.save(conn, 処理日="04/01", 担当者="浜崎", 品名="リプラ",
                       サイズ="30×40", 長さ="800", LotNo="A123456", 数量="16")
    assert order_history.find_duplicates(conn, ["B999999"]) == []


def test_build_reports_duplicates_without_blocking(conn):
    order_history.save(conn, 処理日="04/01", 担当者="浜崎", 品名="リプラ",
                       サイズ="30×40", 長さ="800", LotNo="A123456", 数量="16")
    s = calc(conn, 下本数="3")
    checklist_service.save_rows(conn, 1, checklist_service.build_rows(s, worker="高村"))
    r = order_sheet_service.build(conn, worker="高村")
    assert r.ok                       # 止めない
    assert len(r.duplicates) == 1     # 知らせるだけ


def test_commit_records_only_filled_rows(conn):
    s = calc(conn, 下本数="3")
    checklist_service.save_rows(conn, 1, checklist_service.build_rows(s, worker="高村"))
    r = order_sheet_service.build(conn, worker="高村", check_history=False)
    n = order_sheet_service.commit(conn, r.sheets, worker="高村")
    assert n == 2                     # 900 と 800 の2行だけ
    assert len(order_history.recent(conn)) == 2


def test_split_lots():
    assert order_history.split_lots("A1 B2  C3") == ["A1", "B2", "C3"]
    assert order_history.split_lots("") == []


# ==================================================================
# 発注票の紙面
#
# **1票 = 1枚。A4縦の上半分に収めて、真ん中に切り取り線。**
# 下半分は空白のまま ── 紙を節約するためではなく、その大きさの票が
# 使いやすいから(現場で切って使う)。
# ==================================================================
def _sheet(size: str, **over):
    from modules.packing_material_calculation.coil_tool.order_sheet_service import OrderSheet, SheetRow
    rows = [SheetRow(長さ=str(n), LotNo="", 数量="")
            for n in (700, 800, 900, 950, 1000, 1050, 1100, 1150)]
    rows += [SheetRow(長さ="", LotNo="", 数量="") for _ in range(4)]
    values = dict(種類="リプラ", 角サイズ=size, 提出日付="09/22",
                  依頼者="高村", rows=rows)
    values.update(over)
    return OrderSheet(**values)


def test_発注票はA4横で半分に切る():
    """横向きは VBA と同じ(`xlLandscape`)。

    縦にすると刷り面の幅が 194mm しかなく、字を詰めないと収まらない。
    横なら 281mm あるので、読める大きさのまま入る。
    """
    from modules.packing_material_calculation.coil_tool import reports
    rep = reports.build_order_sheet_report([_sheet("30×40")])
    assert rep.cut_in_half is True
    assert rep.setup.orientation == "landscape"
    assert rep.setup.paper == "A4"


def test_票の数だけ紙が出る():
    """**1票 = 1枚。** 詰めない ── 票の出る場所が上だったり下だったり
    すると、切る手が止まる。"""
    from modules.packing_material_calculation.coil_tool import printing, reports
    for n in (1, 2, 3):
        html = printing.render_html(reports.build_order_sheet_report(
            [_sheet(f"s{i}") for i in range(n)]))
        assert html.count('class="sheet sheet--half"') == n


def test_切り取り線は紙ごとに1本():
    from modules.packing_material_calculation.coil_tool import printing, reports
    html = printing.render_html(reports.build_order_sheet_report(
        [_sheet("30×40"), _sheet("40×60")]))
    assert html.count('class="cut"') == 2      # 紙2枚 × 1本


def test_票は左半分に収める():
    """切り取り線より右は空白。線は紙そのものの真ん中に来る
    (左右の余白が同じなので、刷る面の50%が A4横 297mm の 148.5mm)。

    切り取った1枚は **A5縦**(148.5 × 210mm)。票は12行を縦に並べる形
    なので、この向きが原紙(注文票.xlsx)と同じ姿になる。
    """
    from modules.packing_material_calculation.coil_tool import printing, reports
    html = printing.render_html(reports.build_order_sheet_report([_sheet("30×40")]))
    assert ".sheet--half .slip { width: 50%" in html
    # 票 → 切り取り線 の順(線の右には何も置かない)
    assert html.index('class="slip"') < html.index('class="cut"')


# ==================================================================
# 写真(2026-09-25、現行VBAで刷った発注票)に合わせたところ
# ==================================================================
def _sheet_html(**over):
    from modules.packing_material_calculation.coil_tool import printing, reports
    return printing.render_html(reports.build_order_sheet_report([_sheet("30×40", **over)]))


def test_切り取り線は細い実線で表から離す():
    """写真の線は E列の右罫線。**細い実線**で、表から E列ぶん離れ、
    表の上から下まで。ハサミの印は無い。"""
    html = _sheet_html()
    assert ".cut { border-left: 1px solid #000; }" in html
    assert ".cut::before { content: none; }" in html
    assert ".sheet--half .slip { padding-right: 1.8%; }" in html
    assert ".sheet--half { height: auto; align-items: stretch; }" in html


def test_種類はVBAと同じ半角のﾘﾌﾟﾗ():
    """VBA は発注票に `CoilWhile`(既定 "ﾘﾌﾟﾗ")をそのまま書く。"""
    html = _sheet_html()
    assert '<td class="kind">ﾘﾌﾟﾗ</td>' in html
    assert "リプラ" not in html.split("<body>")[-1]


def test_提出日付は先頭の0を付けない():
    """VBA は "09/25" を書くが、Excel が日付として 9/25 と出す。"""
    from modules.packing_material_calculation.coil_tool import reports
    assert reports.order_sheet_date("09/05") == "9/5"
    assert reports.order_sheet_date("12/25") == "12/25"
    assert '<td class="date">9/25</td>' in _sheet_html(提出日付="09/25")


def test_字はすべて細字():
    """原紙の字は太字を使っていない(角サイズの 30×40 も細字)。"""
    from modules.packing_material_calculation.coil_tool import reports
    assert "bold" not in reports._SHEET_CSS
    assert "table.form, table.form th, table.form td { font-weight: normal; }" in _sheet_html()


def test_種類の行は太線で囲む():
    html = _sheet_html()
    assert "border-top: 2px solid #000; border-bottom: 2px solid #000;" in html


def test_長い名前は枠に収まるよう字を小さくする():
    """依頼者の欄は原紙では「高村」の2文字ぶん。名簿はフルネーム。"""
    from modules.packing_material_calculation.coil_tool import reports
    assert reports.name_style("高村") == ""
    assert "11.0pt" in reports.name_style("高村敏幸")
    assert "font-size" in _sheet_html(依頼者="長谷川直樹").split('class="name"')[1][:40]


def test_単位は空の行にも出る():
    """原紙(注文票.xlsx)は空の4行にも「本」が刷ってある。

    そこは現場が手で書き足す行なので、単位が無いと書き足したときだけ
    体裁が違ってしまう。
    """
    from modules.packing_material_calculation.coil_tool import printing, reports
    html = printing.render_html(reports.build_order_sheet_report([_sheet("30×40")]))
    # 定尺8行 + 空4行 = 12行ぶん
    assert html.count('<td class="unit">本</td>') == 12


# ==================================================================
# VBA で放置されていた発注票の不具合2件(2026-09-25 の現場の指摘)
# ==================================================================
def _lot(conn, lot: str, row_no: int, **spec):
    s = calc(conn, 下本数="3", **spec)
    s.LOT = lot
    checklist_service.save_rows(conn, row_no,
                                checklist_service.build_rows(s, worker="高村"))


def _lots_on_sheets(result) -> set[str]:
    return {lot for sheet in result.sheets for r in sheet.rows
            for lot in r.LotNo.split() if lot}


def test_1行目が空でも2行目以降から発注票を出せる(conn):
    """VBA: 資材発注管理の1行目にデータが無いと、2行目以降にデータが
    あっても発注票を出せなかった(`転記` が表の範囲を行の並びから決める
    ため)。

    移植はチェックリストを**行番号順にあるだけ**読む。空の行は飛ばす。
    """
    _lot(conn, "B000002", 2)
    _lot(conn, "B000005", 5)
    _lot(conn, "B000015", 15)
    r = order_sheet_service.build(conn, worker="高村", check_history=False)
    assert r.ok, r.reason
    assert _lots_on_sheets(r) == {"B000002", "B000005", "B000015"}


def test_最後の15行目だけでも発注票を出せる(conn):
    _lot(conn, "B000015", 15)
    r = order_sheet_service.build(conn, worker="高村", check_history=False)
    assert r.ok, r.reason
    assert _lots_on_sheets(r) == {"B000015"}


def test_空いた行を挟んでも数量は合う(conn):
    """行が飛んでいても、同じ長さは足し合わせる(取りこぼさない)。"""
    _lot(conn, "B000001", 1)
    one = order_sheet_service.build(conn, worker="高村", check_history=False)
    per_lot = {r.長さ: int(r.数量) for s in one.sheets for r in s.rows if r.数量}
    _lot(conn, "B000009", 9)
    two = order_sheet_service.build(conn, worker="高村", check_history=False)
    both = {r.長さ: int(r.数量) for s in two.sheets for r in s.rows if r.数量}
    assert both == {k: v * 2 for k, v in per_lot.items()}


def test_チェックリストを印刷しなくても発注票を出せる(client, sandbox):
    """VBA: 資材発注管理を発行(印刷)せずに発注票を発行するとエラーに
    なっていた。

    移植の発注票はチェックリストの中身(手元のDB)だけから作るので、
    **印刷したかどうかに関係なく**作れる。ここでは印刷のページに一度も
    触れずに、積む → 発注票を作る → 印刷用の紙面、まで通す。
    """
    from modules.packing_material_calculation.tests._web import seed
    seed(sandbox)
    client.post("/api/settings", json={"worker": _fixture_worker()})
    r = client.post("/api/calc/lot", json={"LOT": "A123456"})
    order_no = r.get_json()["candidates"][0]
    client.post("/api/calc/order", json={"受注番号": order_no})
    client.post("/api/calc/input", json={"検入数": "10"})
    assert client.post("/api/calc/run", json={}).get_json()["ok"]
    assert client.post("/api/checklist/add", json={}).get_json()["ok"]

    built = client.post("/api/order/build", json={}).get_json()
    assert built["ok"], built
    page = client.get("/report/order?t=test-token")
    assert page.status_code == 200
    assert "発注票" in page.data.decode()


def _fixture_worker() -> str:
    from modules.packing_material_calculation.coil_tool import config
    return config.WORKERS[0]


def test_画面の見本も紙1枚ずつに見せる():
    """画面で縦に伸ばして並べると「下にもう1枚ある」ように見える(現場の声)。
    画面でも A4横の紙の姿にして、各紙の上に何枚目かを添える。
    添え書きは印刷されない(`screen-only`)ので、紙は角サイズごとに1枚のまま。"""
    from modules.packing_material_calculation.coil_tool import printing, reports
    html = printing.render_html(reports.build_order_sheet_report(
        [_sheet("30×40"), _sheet("40×50")]))
    assert ".sheet--half { width: 297mm; min-height: 210mm;" in html
    assert html.count('class="sheet sheet--half"') == 2
    assert '<p class="screen-only sheet-no">1枚目 / 全2枚</p>' in html
    assert '<p class="screen-only sheet-no">2枚目 / 全2枚</p>' in html
    assert "@media print { .screen-only { display: none; } }" in html
    # 最後の要素は紙そのもの(`.sheet:last-child` で最後の改ページを止める)
    body = html.split("<body>")[1]
    assert body.rstrip().endswith("</div></div></body></html>")
