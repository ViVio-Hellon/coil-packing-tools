"""梱包明細表の帳票 (VBA `mSheet.BuildOneSheet` / `AdjustLayout` / `WriteData`)

VBA は Excel を「レイアウトエンジン＋プリンタドライバ」として使い、
シートを1枚作って罫線と値を置いていた。Python版は `printing` の機構で
**HTML＋印刷用CSS**として同じ紙面を組む。

    VBA                                 -> CSS
    ---------------------------------------------------------------
    .Orientation = xlLandscape            @page { size: A4 landscape }
    .TopMargin = 0 ...                     @page { margin: 0 }
    .Merge (A1:D1 など)                      colspan
    .Borders(...).LineStyle = xlContinuous    border

【A4横の左半分に1枚。右半分は空ける】
**明細表1枚につき用紙1枚。** A4横の左半分に刷り、右半分は空けておく。
現場は右半分を切り取って使っている(用紙のサイズの運用上)。
**A4 1枚に2枚並べる運用はしていない**(現場に確認済み)。

VBA もこの形だった。

    .Orientation = xlLandscape / .PaperSize = xlPaperA4 / 余白 0
    .FitToPagesWide = 1 / .FitToPagesTall = 1 / .Zoom = False
    (中央寄せの指定なし → 左に寄る)

`FitToPages` は**縮めるだけで引き伸ばさない。** 1面(A1:F20)は

    行の高さ  23.25 + 42×3 + 18.75 + 28.5×15 = 595.5pt = 210.06mm
                                                = A4 の高さ ちょうど
    列の幅    10.375 + 12.375 + 12×4 = 70.75  → A4横の幅の半分

なので、等倍のまま左半分に出て、右半分が空く。

(以前ここに「VBA は A4 いっぱいに引き伸ばしていた」と書き、添付の
実ブックが A〜F / H〜M の2面で保存されていたことから2面付けにして
いた。**どちらも誤り。** 実ブックの2面は運用の形ではなかった)

【紙面の構造 (実ブックの A2:F21 = 1面ぶん)】
    行1     A1:D1 「<Lot>-No<N>  梱包明細表」 / E1:F1 「NLM.NAGOYA.QA」(赤)
            ※ E1:F1 の文字は設定画面で変えられる(`qa_mark`。全ラインで共有・
              管理者パスワード要)
    行2-4   A2:A4 「種類質別/寸法/検番」 / B2:F4 罫線なし(小ラベル貼付エリア)
            D3 「小ラベル添付」(橙・9pt)
    行5     A5:C5 「コイル副番」 / D5:F5 「重量(Kg)」
    行6-20  データ15行。左=副番、右=重量

**種類質別・寸法・検番は印字しない。** B2:F4 は現物のラベルを手で貼る
場所なので、VBA も枠線を消して空けてある(右端だけ縦線を引く)。

【印刷の前に書き足せる ── サイズと LOTNO】
VBA は紙がExcelシートだったので、刷る前にセルへ打ち込めた。同じことを
紙面の上でできるようにしてある(流用元の `printing.editable`)。

    A列          B〜C列             D列            E〜F列
    種類質別     (空けたまま)
    寸法         サイズ   ← 書き足し  小ラベル添付(D3)
    検番         LOTNO    ← 書き足し

**A列の文字の行に揃える。** A2:A4 は3行を1つに結合して中央寄せなので、
「寸法」「検番」の文字は2行目・4行目の真ん中には無い(中央に3行が
固まっている)。書き足しも同じ文字の大きさ・行の高さで3行組み、
中央に置く ── そうすると、読む人が並べて見る文字どうしが横に揃う。

**B〜C列に収める。** D〜F列は現物のラベルを貼る場所で、上に貼れば
隠れる。「小ラベル添付」は VBA の `Cells(3, 4)` ＝ D3 にそのまま置く。

**空なら何も印字しない。** 書き足さなければ、VBA の紙面と同じになる。

【寸法は割合で持つ】
行の高さも列の幅も**百分率**にしてある。プリンタの印字できない余白は
機種ごとに違い、`margin:0` でも実際には数ミリ削られる。固定のmmで
組むと、削られた分だけ最終行が落ちる ── 割合なら全体が縮むだけで、
紙面の比率は保たれる。
"""
from __future__ import annotations

from typing import Iterable, Optional

from . import config, printing, qa_mark
from .meisai_service import Output

# 紙面の色 (VBA `RGB(...)`)
COLOR_QA = "#ff0000"          # RGB(255, 0, 0)
COLOR_SMALL_LABEL = "#ff6600"  # RGB(255, 102, 0)

# 見出しの文字 (VBA `MergePut` に渡している文字列)
TITLE_SUFFIX = "  梱包明細表"
LEFT_CAPTION = "種類質別\n寸法\n検番"
SMALL_LABEL = "小ラベル添付"
HEAD_FUBAN = "コイル副番"
HEAD_WEIGHT = "重量(Kg)"

# 紙の端から、文字・罫線をどれだけ内に置くか(mm)。**上下左右とも。**
#
# プリンタは紙の縁から約4mmの所には印刷できない。プレビューは紙の端まで
# 描くので画面では収まって見えるが、**刷ると左と上が欠けていた**(現場の
# 実機。余白なし・100%)。その約4mmより内の 5mm に置く(機種ごとの差の
# ぶんを見込む。**実機で確かめるまでは「欠けないはず」**)。
#
# **右も 5mm。** 右半分は切り取るので、真ん中の切り目も紙の端になる。
# 手で切ってずれても枠線にかからない。明細は左半分の中で、上下左右
# 5mm ずつ空けた箱に収まる(約 138 × 199mm。線の太さぶんを足すため)。
SAFE_MARGIN_MM = 5.0

# E1:F1(右上の文字)の字の大きさ。長い文字はここから縮めて欄に合わせる
QA_FONT_PT = 8.0
# 縮めても、これより小さくはしない(`qa_mark.MAX_LEN` までなら届かない)
QA_MIN_FONT_PT = 5.0
# セルの左右の余白(`.meisai td` の padding)
_CELL_PAD_MM = 1.5

# 実ブックの列幅(1面ぶん)。A列 10.375 / B列 12.375 / C〜F列 12
_COL_UNITS = (10.375, 12.375, 12.0, 12.0, 12.0, 12.0)

# 実ブックの行高(pt)。ここから百分率を出す
_ROW_TITLE = 23.25
_ROW_LABEL = 42.0 * 3        # 3行を1つに結合してある
_ROW_HEAD = 18.75
_ROW_DATA = 28.5


def _qa_fit_mm() -> float:
    """刷ったときに E1:F1 の文字が使える幅(mm)。

    **画面の幅では測らない。** 紙面の画面はウィンドウの幅に合わせて伸び
    縮みするので、画面で収まっていても紙では収まらないことがある。
    紙の上の幅は決まっているので、ここで計算しておく:

        左半分 148.5mm − 両側の余白(5mm + 線1px) → 明細の幅
        × E〜F列の割合 − セルの左右の余白 − 線や丸めのぶん 1mm
    """
    px = 25.4 / 96
    slip = 297.0 / 2 - 2 * (SAFE_MARGIN_MM + px)
    share = sum(_COL_UNITS[4:6]) / sum(_COL_UNITS)
    return slip * share - 2 * _CELL_PAD_MM - 1.0


def _col_percents() -> list[float]:
    total = sum(_COL_UNITS)
    return [w / total * 100 for w in _COL_UNITS]


def _row_percents() -> tuple[float, float, float, float]:
    total = _ROW_TITLE + _ROW_LABEL + _ROW_HEAD + _ROW_DATA * config.SHEET_ROWS
    return (_ROW_TITLE / total * 100, _ROW_LABEL / total * 100,
            _ROW_HEAD / total * 100, _ROW_DATA / total * 100)


def _sheet_css() -> str:
    cols = _col_percents()
    title, label, head, data = _row_percents()
    cols_css = "".join(
        f".meisai col:nth-child({i + 1}){{ width:{w:.3f}%; }}\n"
        for i, w in enumerate(cols))
    return f"""
/* `printing.render_html` が1ページを `.sheet` で包む。ページ送りと
   画面での見た目はそちらが持っているので、ここでは飾りだけ外して
   素通しの器にする(二重の枠と余白が出ないように) */
.sheet{{ padding:0 !important; background:none !important;
        box-shadow:none !important; margin:0 auto 12px !important; }}

/* 1枚の紙 = A4横。**左半分に明細1枚、右半分は空ける**(現場で切り取る)。

   **紙の端から {SAFE_MARGIN_MM:g}mm 内に置く(上下左右)。** プリンタは紙の縁から
   約4mmを刷れない。プレビューは端まで描くので収まって見えるが、刷ると
   左と上が欠けていた(実機)。

   **余白は `@page` ではなく中身で取る。** 現場は印刷ダイアログで
   「余白なし」にして刷る。そうすると `@page` の余白は0に上書きされる
   ので、そちらに頼ると元に戻ってしまう。

   (以前は `max-height:100vh` で「削られた分だけ縮む」と書いていたが、
   **縮まない。** ブラウザはプリンタの刷れない幅を知らない) */
.page{{
  display:grid; grid-template-columns:1fr 1fr;
  width:100%; height:210mm; max-height:100vh;
}}
/* **線の太さ(1px)を足す。** 表の外枠の線は、表の箱から少しはみ出して
   描かれる(実測で右に 0.1mm)。箱を {SAFE_MARGIN_MM:g}mm に置くだけだと、刷った
   線が 4.9mm になる。足しておけば**インク**が {SAFE_MARGIN_MM:g}mm 内に収まる */
.half{{ padding:calc({SAFE_MARGIN_MM:g}mm + 1px); height:100%; overflow:hidden; }}
/* 右半分は**何も置かない**。VBA の紙面も空いている(切り取る側) */

.meisai{{
  width:100%; height:100%; border-collapse:collapse; table-layout:fixed;
  font-family:"Meiryo UI",Meiryo,sans-serif; font-size:9pt;
  border:1px solid #000;
}}
.meisai td{{ border:none; padding:0 1.5mm; vertical-align:middle; }}
/* 行1: タイトル。VBA の `"-No1" & "  梱包明細表"` の**空白2つ**を
   HTML に畳ませない(畳むと詰まって読みにくい) */
.meisai .r-title td{{ height:{title:.3f}%; border-bottom:1px solid #000;
  text-align:center; font-weight:bold; white-space:pre; }}
/* E1:F1。文字は設定で変えられる(`qa_mark`)。**欄からはみ出させない** ──
   長い文字は刷る前に字を小さくして合わせる(`_QA_SCRIPT`)。`overflow` は
   その手前で止める最後の囲い(5mm の内側を越えて刷らない) */
.meisai .r-title .qa{{ color:{COLOR_QA}; font-weight:bold; font-size:{QA_FONT_PT:g}pt;
  overflow:hidden; }}
/* 行2-4: 小ラベル貼付エリア。**罫線を引かない**(現物を手で貼る) */
.meisai .r-label td{{ height:{label:.3f}%; border-bottom:1px solid #000; }}
.meisai .r-label .cap{{ border-right:1px solid #000;
  text-align:center; white-space:pre-line; line-height:1.7; }}
/* D3。VBA `Cells(3, 4)` の中央寄せ */
.meisai .r-label .paste{{ text-align:center; color:{COLOR_SMALL_LABEL};
  font-size:8pt; white-space:nowrap; }}
/* B〜C列の書き足し。**A列の文字と同じ大きさ・同じ行の高さで3行**組む
   ので、「寸法」「検番」の文字と横に揃う */
.meisai .r-label .hand{{ padding:0 1mm; }}
.meisai .r-label .hand .hl{{ display:block; height:1.7em; line-height:1.7;
  white-space:nowrap; overflow:hidden; font-weight:bold; text-align:center; }}
/* 紙面の上で直すときの的。欄の幅いっぱいを押せるようにする */
.meisai .r-label .hand .edit{{ display:block; min-width:0; width:100%; }}
/* 行5: 見出し */
.meisai .r-head td{{ height:{head:.3f}%; text-align:center; font-weight:bold;
  border-bottom:1px solid #000; }}
.meisai .r-head .w{{ border-left:1px solid #000; }}
/* 行6-20: データ15行。**副番も重量も中央。** VBA は A〜C・D〜F を
   `MergePut` で結合して `xlCenter` にしてから値を入れている(以前は
   副番を左・重量を右に寄せていて、VBA と違っていた) */
.meisai .r-data td{{ height:{data:.3f}%; font-size:11pt; text-align:center;
  border-bottom:1px solid #000; }}
.meisai .r-data td:last-child{{ border-left:1px solid #000; }}
.meisai .r-data:last-child td{{ border-bottom:none; }}

@media screen{{
  .page{{ background:#fff; box-shadow:0 1px 4px rgba(0,0,0,.3);
         max-height:none; }}
  /* 紙面の上の「印刷する」。アプリの主ボタンと同じ色にして、
     **次に押すのはこれ**と分かるようにする(紙には出ない) */
  .editbar .print{{ margin-left:.6em; padding:3px 14px; font:inherit;
    font-weight:bold; color:#fff; background:#0e6a72;
    border:1px solid #0e6a72; border-radius:4px; cursor:pointer; }}
  .editbar .print:hover{{ filter:brightness(1.08); }}
}}
{cols_css}"""


def _hand_lines(output: Output, edit: bool) -> str:
    """B〜C列の3行。上から 種類質別(空) / 寸法 / 検番。

    `edit` なら紙面の上で直せる欄にする。欄の名前は `<管理番号>.<欄>`
    ── まとめて開くと何枚ぶんもの欄が1つの画面に並ぶので、明細ごとに
    分ける。
    """
    esc = printing.escape
    blank = '<span class="hl"></span>'
    if not edit:
        return (blank
                + f'<span class="hl">{esc(output.hand_size)}</span>'
                + f'<span class="hl">{esc(output.hand_lotno)}</span>')
    size = printing.editable(f"{output.row_id}.size", output.hand_size,
                             placeholder="サイズ")
    lotno = printing.editable(f"{output.row_id}.lotno", output.hand_lotno,
                              placeholder="LOTNO")
    return (blank
            + f'<span class="hl">{size}</span>'
            + f'<span class="hl">{lotno}</span>')


def build_slip(output: Output, *, edit: bool = False,
               qa: Optional[str] = None) -> str:
    """明細1面ぶんのHTML (VBA `BuildOneSheet` + `WriteData`)。

    `edit` なら「寸法」「検番」の行へ、紙面の上で書き足せる。
    """
    esc = printing.escape
    title = f"{output.sheet_name}{TITLE_SUFFIX}"

    parts = [
        '<table class="meisai"><colgroup>',
        *["<col>" for _ in _COL_UNITS],
        "</colgroup><tbody>",
        # 行1
        f'<tr class="r-title"><td colspan="4">{esc(title)}</td>'
        f'<td colspan="2" class="qa"><span class="qa-text">'
        f'{esc(qa_mark.current() if qa is None else qa)}</span></td></tr>',
        # 行2-4 (A列は3行ぶんを1セルに。B〜F列は罫線なしの貼付エリア)。
        # B〜C列に書き足し、D列(D3)に「小ラベル添付」、E〜F列は空けておく
        f'<tr class="r-label"><td class="cap">{esc(LEFT_CAPTION)}</td>'
        f'<td colspan="2" class="hand">{_hand_lines(output, edit)}</td>'
        f'<td class="paste">{esc(SMALL_LABEL)}</td>'
        f'<td colspan="2"></td></tr>',
        # 行5
        f'<tr class="r-head"><td colspan="3">{esc(HEAD_FUBAN)}</td>'
        f'<td colspan="3" class="w">{esc(HEAD_WEIGHT)}</td></tr>',
    ]

    # 行6-20。**行数は必ず15行**。VBA は空行も枠だけ引いて出す
    rows = output.rows()[:config.SHEET_ROWS]
    for index in range(config.SHEET_ROWS):
        key, weight = rows[index] if index < len(rows) else ("", "")
        parts.append(
            f'<tr class="r-data"><td colspan="3">{esc(key)}</td>'
            f'<td colspan="3">{esc(weight)}</td></tr>')

    parts.append("</tbody></table>")
    return "".join(parts)


def _page(slip: str) -> str:
    """紙1枚。**左半分に明細1枚、右半分は空ける。**

    右半分は現場で切り取る(用紙のサイズの運用上)。VBA の紙面も同じ。

    **右半分を何かで埋めない。** 空いているのがもったいなく見えるので
    埋めたくなるが、
      ・別の明細を並べる … **その運用はしていない**(現場に確認済み)
      ・同じ明細を写す  … 明細表は1梱包に1枚貼るものなので、2枚出れば
                          同じ梱包の紙が2枚あることになり、貼り間違いの種
    """
    return (f'<div class="page"><div class="half">{slip}</div>'
            '<div class="half blank"></div></div>')


def build_report(outputs: Iterable[Output], *,
                 title: Optional[str] = None,
                 edit: bool = False,
                 qa: Optional[str] = None,
                 qas: Optional[list[str]] = None) -> printing.Report:
    """明細をまとめた帳票。**明細1枚につき用紙1枚。**

    `printing.Report.sheets` の1要素が1ページ。まとめて刷っても1枚ずつ
    別の用紙に出る(A4 1枚に2枚並べる運用はしていない)。
    """
    items = list(outputs)
    name = title or (items[0].sheet_name if items else "梱包明細表")
    report = printing.Report(
        title=f"{name} 梱包明細表",
        setup=printing.PageSetup(
            paper="A4",
            orientation=printing.LANDSCAPE,
            margin_mm=0,                 # VBA: 全マージン0
            extra_css=_sheet_css(),
        ),
    )
    # **開くたびにその場で読む。** 設定で変えたら、次に開く紙面から効く
    # (どのラインでも。前に出力した明細の刷り直しも)。1回の紙面の中では揃える
    # `qas` は紙ごとの右上の文字(履歴から作り直すとき。紙ごとに記録が違う)
    if qas is None:
        if qa is None:
            qa = qa_mark.current()
        qas = [qa] * len(items)
    for item, item_qa in zip(items, qas):
        report.add_sheet(_page(build_slip(item, edit=edit, qa=item_qa)))
    return report


# 紙面の上に出す案内。**画面のときだけ**で、紙には出ない
EDIT_HINT = (
    "点線の欄に <b>サイズ・LOTNO</b> を書き足せます"
    "（紙面の「寸法」「検番」の行）。空のままなら何も印字しません。"
    "書き足した内容はこの明細に残ります。"
    ' <button type="button" class="print" onclick="window.print()">印刷する</button>'
)


def render(outputs: Iterable[Output], *, title: Optional[str] = None,
           edit_url: str = "", resolved: Optional[tuple] = None) -> str:
    """印刷用のHTML。ブラウザの印刷(Ctrl+P)でそのまま出せる。

    `edit_url` を渡すと、印刷の前にサイズと LOTNO を書き足せる
    (書き足した内容はそのURLへ送り返す)。渡さなければ読むだけ。
    """
    # 右上の文字は全ラインで共有。**届かず写しで刷るときは、画面にそう出す**。
    # `resolved` は呼び手が先に読んだもの(`qa_mark.resolve()`)── 同じ値を
    # 明細の履歴にも残すので、2度読んで食い違わせない
    qa, snapshot = resolved if resolved is not None else qa_mark.resolve()
    return printing.render_html(
        build_report(outputs, title=title, edit=bool(edit_url), qa=qa),
        edit_url=edit_url, edit_hint=EDIT_HINT, script=_qa_script(),
        notice=qa_mark.notice(snapshot))


# ------------------------------------------------------------------
# 履歴から紙面を作り直す (`slip_history`)
# ------------------------------------------------------------------
HISTORY_BAR = ('履歴から作り直した紙面です（書き足しはできません）。'
               ' <button type="button" class="print" onclick="window.print()">印刷する</button>')


def _from_history(slip: dict) -> Output:
    """履歴の紙1枚を `Output` に戻す。**記録のとおりに**(積んだ順・重量・書き足し)。"""
    head, coils = slip["head"], slip["coils"]
    jou_count = max((int(c["丈"] or 0) for c in coils), default=0)
    weights = [0] * jou_count
    for coil in coils:
        jou = int(coil["丈"] or 0)
        if 1 <= jou <= jou_count:
            weights[jou - 1] = int(round(float(coil["重量"] or 0)))
    return Output(lot_no=str(head["ロット番号"]), seq_no=int(head["No"]),
                  keys=[str(c["副番"]) for c in coils], weights=weights,
                  printed_at=str(head["出力日時"] or ""),
                  hand_size=str(head["手入力サイズ"] or ""),
                  hand_lotno=str(head["手入力ロット番号"] or ""),
                  history_id=str(head["送信ID"]))


def render_history(slips: list[dict], *, current_qa: str, source: str = "shared") -> str:
    """履歴から明細票を作り直す。**読むだけ**(履歴も明細も書き換えない)。

    右上の文字は**その紙を開いたときに刷られた文字**(履歴の記録)。記録が
    無い紙(一度も開いていない)は、いまの文字で刷って、画面にそう出す。
    作り直された(No指定で上書きされた)紙は、画面にそう出す ── 新しい紙が
    別にある。
    """
    outputs = [_from_history(slip) for slip in slips]
    qas, no_record, replaced = [], [], []
    for slip, out in zip(slips, outputs):
        recorded = str(slip["head"]["右上の文字"] or "")
        qas.append(recorded or current_qa)
        if not recorded:
            no_record.append(out.sheet_name)
        if slip["head"]["状態"] == "作り直し":
            replaced.append(out.sheet_name)
    where = {"shared": "全ラインの履歴", "local": "この PC の履歴",
             "mixed": "全ラインの履歴(一部はこの PC の履歴)"}.get(source, "履歴")
    notes = [f"{where}から作り直した紙面です（{len(outputs)}枚）。記録のとおりに組んでいます。"]
    if replaced:
        notes.append(f"{'・'.join(replaced)} は、このあと作り直されています（新しい紙があります）。")
    if no_record:
        notes.append(f"{'・'.join(no_record)} は右上の文字の記録が無いため、"
                     f"いまの文字（{current_qa}）で刷ります。")
    title = outputs[0].sheet_name if len(outputs) == 1 else f"{outputs[0].lot_no} ほか"
    report = build_report(outputs, title=f"履歴 {title}", qas=qas)
    return printing.render_html(report, script=_qa_script(sync=False),
                                notice=" ".join(notes), bar=HISTORY_BAR)


# 紙面の右上の文字を、刷る前に整える。**帳票は独立したページ**で、
# アプリ本体のJSは動いていない。ここだけで完結させる。
#
# 1. **欄に収める。** 文字の幅を測って、刷ったときの欄の幅
#    (`_qa_fit_mm`)を越えるなら字を小さくする。幅は画面ではなく
#    紙の上の値で比べる(画面の紙面はウィンドウに合わせて伸び縮みする)。
#    字の大きさは pt で決めるので、画面でも紙でも同じ幅になる
# 2. **開いたままの紙面にも効かせる。** 同じPCで設定を変えると、アプリの
#    画面が `BroadcastChannel` で知らせてくる。その場で差し替える。
#    ほかのラインで変えたとき・知らせを取りこぼしたとき(裏で凍結されて
#    いた等)は、前に戻したときに共有の今の文字を聞き直す ── 古い文字の
#    まま刷らせない。共有に届かなくなった・戻ったときの断り書きも差し替える
_QA_SCRIPT = """
<script>
(function () {
  var MM = 96 / 25.4, BASE = __BASE__, MIN = __MIN__, FIT = __FIT__ * MM;
  // 履歴から作り直した紙面は、**記録した文字のまま**刷る(いまの文字へ差し替えない)
  var SYNC = __SYNC__;
  var CHANNEL = "meisai.print.v1";
  function fit(cell) {
    var text = cell.querySelector(".qa-text");
    if (!text) return;
    cell.style.fontSize = "";
    // 折り返さない1行なので、欄より長くても文字そのものの幅が返る
    var width = text.getBoundingClientRect().width;
    if (width > FIT) {
      cell.style.fontSize = Math.max(MIN, BASE * FIT / width).toFixed(2) + "pt";
    }
  }
  function fitAll() { document.querySelectorAll(".meisai .qa").forEach(fit); }
  function note(text) {
    var el = document.getElementById("reportNotice");
    if (!el) return;
    el.textContent = text || "";
    el.hidden = !text;
  }
  function apply(value) {
    if (typeof value !== "string" || !value) return;
    document.querySelectorAll(".meisai .qa").forEach(function (cell) {
      var text = cell.querySelector(".qa-text");
      if (text && text.textContent !== value) text.textContent = value;
    });
    fitAll();
  }
  function sync() {
    var t = new URLSearchParams(location.search).get("t") || "";
    // 紙面は `<入口>/report/<lot>/<no>` で開く。問い合わせ先も同じ入口の下
    // (統合版では機能ごとに入口が付く。紙面のURLから組めば入口を知らなくてよい)
    var root = location.pathname.split("/report/")[0];
    fetch(root + "/report/qa-mark?t=" + encodeURIComponent(t), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (b) { if (b) { apply(b.qa_mark); note(b.notice); } })
      .catch(function () {});
  }
  fitAll();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAll);
  window.addEventListener("beforeprint", fitAll);
  if (!SYNC) return;
  if ("BroadcastChannel" in window) {
    new BroadcastChannel(CHANNEL).addEventListener("message", function (e) {
      if (e.data && e.data.type === "qa_mark") apply(e.data.value);
    });
  }
  window.addEventListener("focus", sync);
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") sync();
  });
}());
</script>
"""


def _qa_script(*, sync: bool = True) -> str:
    return (_QA_SCRIPT.replace("__SYNC__", "true" if sync else "false")
            .replace("__BASE__", f"{QA_FONT_PT:g}")
            .replace("__MIN__", f"{QA_MIN_FONT_PT:g}")
            .replace("__FIT__", f"{_qa_fit_mm():.2f}"))
