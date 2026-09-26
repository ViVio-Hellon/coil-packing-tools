"""帳票 ── 資材発注管理チェックリストと発注票

元は Excel の原紙シートに値を貼って `PrintOut` していた。ここでは
**同じ紙面を HTML + 印刷用CSS で組む**(`printing` 参照)。

添付の原紙から実セル位置を確かめてある。

    資材発注管理チェックリスト原紙
        E1        表題(ラインで変わる)
        3行目     見出し(A〜N)
        4行目     A4 = "確定"
        5行目〜   データ15行

    注文票原紙
        A4        リプラ / LVS      B4  角サイズ
        C2/D2     見出し            C3  提出日付   D3  依頼者
        A5:A12    倉庫在庫の定尺8種
        A13:A16   書き足し用の空欄4行
        B列 LotNo  C列 数量  D列 "本"
"""
from __future__ import annotations

from typing import Sequence

from . import config, printing
from .order_sheet_service import OrderSheet

# ==================================================================
# 資材発注管理チェックリスト
# ==================================================================
# 見出し(A〜N)。原紙の3行目そのまま
CHECKLIST_HEADER: tuple[str, ...] = (
    "サイズ\n確定", "依頼日", "LotNo", "用途名", "パレット種類", "台数",
    "営業納期", "ｺｲﾙ間", "ﾘﾌﾟﾗ長さ", "ﾘﾌﾟﾗ本数", "ﾘﾌﾟﾗｻｲｽﾞ", "緩衝材",
    "依頼者", "入荷日",
)

# 列幅。A4横1ページに収める
CHECKLIST_WIDTHS: tuple[str, ...] = (
    "4%", "6%", "9%", "11%", "11%", "5%", "7%", "7%", "8%", "8%", "7%",
    "8%", "6%", "6%",
)

_CHECKLIST_CSS = """
table.form { font-size: 10pt; }
table.form th, table.form td { height: 30px; vertical-align: middle; }
table.form td { white-space: pre-line; }
h1.title { font-size: 16pt; text-align: center; margin: 4px 0 8px; }
"""

# 原紙(注文票.xlsx)の見た目に合わせた寸法。
#
# **写真(2026-09-25、現行VBAで刷った発注票)と注文票.xlsx から取った。**
# 原紙は大きな字を 80%縮小で刷っている(`Zoom = 80`)ので、ここの pt と mm は
# 「紙に出る大きさ」= 原紙の値 × 0.8 で書いてある。字はすべて細字・Meiryo UI。
#
#   行の高さ  1行目 40.5pt / 2行目 16.5pt / 3行目 56.25pt / 4行目 43.5pt /
#             5〜16行目 39pt   (× 0.8)
#   字        発注票 26pt / 提出日付・依頼者の見出し 12pt / 日付 16pt /
#             依頼者 22pt / ﾘﾌﾟﾗ 22pt / 角サイズ 30pt / 長さ・数量 22pt /
#             LotNo・本 12pt  (× 0.8)
#   罫線      種類の行(4行目)は**太線(medium)で囲み**、ﾘﾌﾟﾗと角サイズの
#             あいだも太線。明細の縦線は A の左右と D の右だけ
#             (**LotNo と数量のあいだに線は無い**)
#   切り取り線 E列の右罫線。**細い実線**で、表から E列ぶん離れ、1行目の上から
#             16行目の下まで(ハサミの印は無い)
_PT = 0.8                      # 原紙の縮小率
_THIN = "1px solid #000"
_MEDIUM = "2px solid #000"     # Excel の medium
_SHEET_CSS = f"""
body {{ font-family: "Meiryo UI", "Meiryo", "Yu Gothic UI", sans-serif; }}
table.form, table.form th, table.form td {{ font-weight: normal; }}
table.form th, table.form td {{ border: 0; padding: 0 1.2mm; vertical-align: middle;
                                white-space: nowrap; overflow: hidden; }}
table.form th {{ background: none; }}

/* 1〜3行目: 見出し。「発注票」は枠なし、提出日付/依頼者だけ C・D列で囲む */
table.form tr.r1 td {{ height: {40.5 * _PT:.1f}pt; }}
table.form tr.r2 th {{ height: {16.5 * _PT:.1f}pt; font-size: {12 * _PT:.1f}pt;
                       text-align: center; border: {_THIN}; }}
table.form tr.r3 td {{ height: {56.25 * _PT:.1f}pt; }}
table.form td.title {{ font-size: {26 * _PT:.1f}pt; text-align: left; }}
table.form td.date, table.form td.name {{
  text-align: center; border-left: {_THIN}; border-right: {_THIN};
  border-top: {_THIN}; }}
table.form td.date {{ font-size: {16 * _PT:.1f}pt; }}
table.form td.name {{ font-size: {22 * _PT:.1f}pt; }}

/* 4行目: 種類 | 角サイズ。**太線で囲む**(ﾘﾌﾟﾗと角サイズのあいだも太線) */
table.form tr.kindrow td {{ height: {43.5 * _PT:.1f}pt; text-align: center;
                            border-top: {_MEDIUM}; border-bottom: {_MEDIUM}; }}
table.form td.kind {{ font-size: {22 * _PT:.1f}pt; border-left: {_MEDIUM}; }}
table.form td.size {{ font-size: {30 * _PT:.1f}pt; border-left: {_MEDIUM};
                      border-right: {_MEDIUM}; }}

/* 5〜16行目: 明細 */
table.form tr.item td {{ height: {39 * _PT:.1f}pt; border-bottom: {_THIN}; }}
table.form td.len  {{ border-left: {_THIN}; border-right: {_THIN};
                      font-size: {22 * _PT:.1f}pt; text-align: right; }}
table.form td.lot  {{ font-size: {12 * _PT:.1f}pt; text-align: left; }}
table.form td.qty  {{ font-size: {22 * _PT:.1f}pt; text-align: right; }}
table.form td.unit {{ border-right: {_THIN}; font-size: {12 * _PT:.1f}pt;
                      text-align: center; }}

@media print {{
  /* 切り取り線は**表の高さだけ**(1行目の上から16行目の下まで)。
     E列ぶん表から離す(原紙 E=2.75 / A〜E=75.75 ≒ 3.6%) */
  .sheet--half {{ height: auto; align-items: stretch; }}
  .sheet--half .slip {{ padding-right: 1.8%; }}   /* 紙の幅に対して。票の 3.6% */
  .cut {{ border-left: {_THIN}; }}
  .cut::before {{ content: none; }}
}}
@media screen {{
  /* 画面の見本も印刷と同じ寸法で。線は票の右の縁(表の高さだけ) */
  .sheet--half .slip {{ padding-right: 1.8%; border-right: {_THIN}; }}
}}
"""


def checklist_title(line: str) -> str:
    """表題。ラインの選択で変わる (VBA `Cells(1, 5)`)。"""
    return f"{line}資材発注管理チェックリスト"


def build_checklist_report(rows: Sequence[dict], *, line: str) -> printing.Report:
    """チェックリスト1枚。

    行が空でも**15行ぶんの枠を出す**。現場は印刷してから手で書き足す
    ことがあるので、枠が無いと使えない。
    """
    body: list[list[str]] = []
    by_row: dict[tuple[int, int], dict] = {
        (int(r["行番号"]), int(r["枝番"])): r for r in rows}

    for n in range(1, config.CHECKLIST_ROWS + 1):
        branches = sorted(b for (r, b) in by_row if r == n)
        if not branches:
            body.append([""] * len(CHECKLIST_HEADER))
            continue
        for b in branches:
            r = by_row[(n, b)]
            body.append([
                str(r.get("サイズ確定", "")),
                str(r.get("依頼日", "")),
                str(r.get("LotNo", "")),
                str(r.get("用途名", "")),
                str(r.get("パレット種類", "")),
                str(r.get("台数", "")),
                str(r.get("営業納期", "")),
                str(r.get("コイル間", "")),
                str(r.get("リプラ長さ", "")),
                str(r.get("リプラ本数", "")),
                str(r.get("リプラサイズ", "")),
                str(r.get("緩衝材", "")),
                str(r.get("依頼者", "")),
                str(r.get("入荷日", "")),
            ])

    title = checklist_title(line)
    report = printing.Report(
        title=title,
        fit_one_page=True,          # VBA `FitToPagesTall = 1`(30行でも1枚)
        setup=printing.PageSetup(
            paper="A4", orientation=printing.LANDSCAPE, margin_mm=6,
            extra_css=_CHECKLIST_CSS))
    report.add_sheet(
        f'<h1 class="title">{printing.escape(title)}</h1>'
        + printing.table(body, header=CHECKLIST_HEADER,
                         widths=CHECKLIST_WIDTHS))
    return report


# ==================================================================
# 発注票
# ==================================================================
# 原紙(注文票.xlsx)の列幅。Excel の幅を足し合わせて割った比率
#   A=20.125(長さ) B=30.875(LotNo) C=11(数量) D=11(単位) … 計73
SHEET_WIDTHS: tuple[str, ...] = ("27.6%", "42.3%", "15.1%", "15.0%")


# 種類の書き方。VBA は発注票に `CoilWhile`(既定 "ﾘﾌﾟﾗ")をそのまま書く。
# 手元の種類は全角「リプラ」なので、**発注票に刷るときだけ**半角にする
# (チェックリスト・発注履歴は今のまま)
_KIND_ON_SHEET = {"リプラ": "ﾘﾌﾟﾗ"}


def kind_label(kind: str) -> str:
    return _KIND_ON_SHEET.get(kind, kind)


def order_sheet_date(day: str) -> str:
    """提出日付の書き方。**原紙に刷られるのは「9/25」**(先頭の0なし)。

    VBA は `Format$(Now, "mm/dd")` で "09/25" を書くが、Excel がそれを日付と
    みなしてセルの書式(月/日)で出すので、紙には 9/25 と出る。
    """
    parts = day.split("/")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        return f"{int(parts[0])}/{int(parts[1])}"
    return day


def name_style(name: str) -> str:
    """依頼者の欄は原紙では「高村」の2文字ぶんの幅しかない。
    名簿はフルネームなので、**長い名前は字を小さくして枠に収める**
    (はみ出すと隣の欄や切り取り線にかかる)。"""
    n = len(name)
    if n <= 2:
        return ""
    size = {3: 14.0, 4: 11.0}.get(n, 9.0)
    return f' style="font-size:{size}pt"'


def build_order_sheet_report(sheets: Sequence[OrderSheet]) -> printing.Report:
    """発注票。**原紙(注文票.xlsx)の姿に合わせてある。**

    原紙から読み取った作り:

        用紙        A4 横 (`paperSize=9` / `orientation=landscape`)
        見出し      「発注票」は**枠なし**(B3・26pt)
                    提出日付と依頼者は**2×2の囲み枠**(C2:D3)
        種類の行    `ﾘﾌﾟﾗ | 角サイズ` (A4 と B4:D4 の結合・30pt)
        明細        12行(定尺8 + 書き足し用4)。**見出し行は無い**
        縦の罫線    A の左右と、D の右だけ。
                    **LotNo と数量のあいだに線は無い**(B に右罫線が無く、
                    C に左罫線も右罫線も無い)

    1票 = 1枚。A4横の左半分に収めて、真ん中に縦の切り取り線を引く
    (切ると **A5縦**)。原紙も E列の右罫線で同じところに線が引いてある。

    VBA は複数枚あると両面になることがあったので、シートごとに別ジョブで
    印刷していた (2025.7.8)。HTML では `page-break-after` で紙ごとに
    区切るので、両面か片面かはブラウザの印刷設定で決まる。
    """
    report = printing.Report(
        title="発注票",
        cut_in_half=True,
        setup=printing.PageSetup(
            paper="A4", orientation=printing.LANDSCAPE, margin_mm=8,
            extra_css=_SHEET_CSS))

    for sheet in sheets:
        esc = printing.escape
        name = sheet.依頼者
        rows = [
            # 1〜3行目。原紙と同じく表の中に置く(列の幅が揃う)
            '<tr class="r1"><td colspan="4"></td></tr>',
            '<tr class="r2"><td></td><td></td>'
            '<th>提出日付</th><th>依頼者</th></tr>',
            '<tr class="r3"><td></td><td class="title">発注票</td>'
            f'<td class="date">{esc(order_sheet_date(sheet.提出日付))}</td>'
            f'<td class="name"{name_style(name)}>{esc(name)}</td></tr>',
            # 4行目。種類は VBA と同じ書き方(`CoilWhile = "ﾘﾌﾟﾗ"`)
            '<tr class="kindrow">'
            f'<td class="kind">{esc(kind_label(sheet.種類))}</td>'
            f'<td class="size" colspan="3">{esc(sheet.角サイズ)}</td></tr>',
        ]
        # 5〜16行目。単位は**全行に出す**。原紙は空の4行にも「本」が刷って
        # ある ── そこは現場が手で書き足す行なので、単位が無いと書き足した
        # ときだけ体裁が違ってしまう
        for r in sheet.rows:
            rows.append(
                f'<tr class="item"><td class="len">{esc(r.長さ)}</td>'
                f'<td class="lot">{esc(r.LotNo)}</td>'
                f'<td class="qty">{esc(r.数量)}</td>'
                '<td class="unit">本</td></tr>')

        cols = "".join(f'<col style="width:{w}">' for w in SHEET_WIDTHS)
        report.add_sheet(
            f'<table class="form"><colgroup>{cols}</colgroup>'
            f'<tbody>{"".join(rows)}</tbody></table>')
    return report
