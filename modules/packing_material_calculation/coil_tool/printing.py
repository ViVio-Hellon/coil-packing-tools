"""帳票のHTML生成 (VBA の Excel帳票 + `PrintOut` の代替)

VBA版は Excel を「レイアウトエンジン + プリンタドライバ」として使っていた。

    Set ws = Worksheets.Add(...)          ' シートを新規作成して値と罫線を置く
    With ws.PageSetup
        .Orientation = xlLandscape
        .PaperSize   = xlPaperA4
        .Zoom = False : .FitToPagesWide = 1 : .FitToPagesTall = 1
        .TopMargin = 0 ...
        .PrintArea = "$A$1:$T$7"
    End With
    ws.PrintOut Copies:=1, Preview:=False

Python版はExcelに依存せず、**HTML + 印刷用CSS** で同じ紙面を作る。
ページ設定はCSSの `@page` がほぼ1対1で対応する。

    .Orientation = xlLandscape  ->  @page { size: A4 landscape; }
    .TopMargin = 0 等            ->  @page { margin: 0; } + 紙の中で内側へ寄せる
    .FitToPagesWide = 1          ->  幅100%のテーブルで組む
    .FitToPagesTall = 1          ->  `Report.fit_one_page`(はみ出す分だけ縮める)
    .PrintArea                   ->  .sheet ブロック1つ = 1ページ

【紙の端から 5mm 以上内側】
プリンタは紙の縁から約4mmには刷れない。ブラウザの印刷プレビューは紙の
端まで描くので、**画面で収まって見えても紙では欠ける**。そこで文字・罫線は
すべて紙の端から `SAFE_MARGIN_MM`(5mm)以上内側に置く。

その距離は `@page` の余白ではなく **紙の中(`.sheet` の内側の余白)** で取る。
`@page` の余白は印刷ダイアログの「余白」で上書きされ、「なし」を選ぶと
中身が紙の端まで出てしまうため(実際に測って確かめた)。`@page` を 0 に
しておけば、ダイアログが「デフォルト」でも「なし」でも同じ位置に刷れる。
ついでに、ブラウザが余白に入れるヘッダー/フッター(日付・URL)も出ない。

**ここが作るのは文字列だけ**で、ファイルにも書かないし印刷も起こさない。
Web版では `app/routes/selection.py` が `render_html()` の結果をそのまま
応答として返し、利用者がブラウザの印刷(Ctrl+P)で出す。
tkinter版のころは一時ファイルへ書いてOSの印刷動詞に渡していたが、
その経路はもう誰も通らないので落とした。

この方式の利点:
    - 追加インストール不要(標準ライブラリだけ)
    - Excelが入っていない端末でも印刷できる
    - ブラウザの「PDFとして保存」で控えが残せる
    - 帳票レイアウトの調整がCSSで完結する

貼付用ラベルのように「現物と寸法がぴったり合う」必要がある帳票は、
ブラウザの余白設定に左右されるため実機で確認すること。
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .logging_utils import get_logger

log = get_logger("printing")

# 用紙の向き(VBA `xlLandscape` / `xlPortrait`)
LANDSCAPE = "landscape"
PORTRAIT = "portrait"


# 紙の端から中身まで、最低これだけ空ける。
# プリンタは紙の縁から約4mmには刷れない(現場の話、2026-09-25)
SAFE_MARGIN_MM = 5.0
# プリンタが刷れない縁の幅。ダイアログの余白「最小」はこれになる
PRINTER_EDGE_MM = 4.0

# 紙の寸法(横向きのときは入れ替える)
PAPER_MM = {"A4": (210.0, 297.0)}


@dataclass
class PageSetup:
    """VBA `PageSetup` に対応する紙面設定。

    `margin_mm` は**紙の端から中身まで**の距離。`SAFE_MARGIN_MM` より
    小さくはできない(刷れない所に字や線が来るため)。
    """

    paper: str = "A4"
    orientation: str = LANDSCAPE
    margin_mm: float = 6.0
    # 追加のスタイル(帳票ごとの罫線・フォント等)
    extra_css: str = ""

    def __post_init__(self) -> None:
        if self.margin_mm < SAFE_MARGIN_MM:
            raise ValueError(
                f"紙の端から {SAFE_MARGIN_MM:g}mm 以上空けてください"
                f"(プリンタは縁の約4mmに刷れません): {self.margin_mm}mm")

    def page_mm(self) -> tuple[float, float]:
        """紙の幅と高さ(mm)。"""
        short, long = PAPER_MM[self.paper]
        return (long, short) if self.orientation == LANDSCAPE else (short, long)

    def to_css(self) -> str:
        # `@page` の余白は 0。距離は紙の中で取る(冒頭の【紙の端から…】)。
        # 紙を2枚にまたぐときも、次の紙の上端で同じだけ空ける(clone)
        return (
            f"@page {{ size: {self.paper} {self.orientation}; margin: 0; }}\n"
            f".sheet {{ padding: {self.margin_mm:g}mm; "
            f"box-decoration-break: clone; "
            f"-webkit-box-decoration-break: clone; }}"
        )


@dataclass
class Report:
    """1つの帳票。`sheets` の1要素が1ページ。

    `cut_in_half` を立てると、**紙の左半分に収めて真ん中に縦の切り取り線**を
    引く。右半分は空白のまま。紙を節約するためではなく、
    **その大きさの票が使いやすい**から(現場で切って使う)。

    A4横を縦に切るので、切り取った1枚は **A5縦**(148.5 × 210mm)になる。
    """

    title: str
    sheets: list[str] = field(default_factory=list)
    setup: PageSetup = field(default_factory=PageSetup)
    cut_in_half: bool = False
    # 1枚に収まらなければ縮めて1枚にする(VBA `FitToPagesTall = 1`)
    fit_one_page: bool = False

    def add_sheet(self, body_html: str) -> None:
        self.sheets.append(body_html)


BASE_CSS = """
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: "Meiryo UI", "Yu Gothic UI", sans-serif; color: #000; }
.sheet { page-break-after: always; }
.sheet:last-child { page-break-after: auto; }
/* 紙を半分に切って使うとき。**左半分に収める**(右半分は空白)。
   左右の余白は同じなので、刷る面の 50% は**紙そのものの真ん中**に
   あたる(A4横なら 297mm の 148.5mm)。そこに縦の切り取り線が来る。
   切り取った1枚は A5縦(148.5 × 210mm)。 */
.sheet--half { display: flex; height: 100%; }
.sheet--half .slip { width: 50%; flex: none; overflow: hidden; }
/* 切り取り線。ここをハサミで縦に切る */
.cut {
  border-left: 1px dashed #888;
  position: relative; width: 0; flex: none;
}
.cut::before {
  content: "✂"; position: absolute; top: 0; left: -0.45em;
  font-size: 9pt; color: #888; background: #fff; padding: 2px 0;
}
table.form { width: 100%; border-collapse: collapse; table-layout: fixed; }
table.form th, table.form td { border: 1px solid #000; padding: 2px 4px; }
table.form th { background: #f0f0f0; font-weight: bold; }
.right { text-align: right; }
.center { text-align: center; }
.big { font-size: 28pt; font-weight: bold; }
@media screen {
  body { background: #e5e7eb; padding: 12px; }
  .sheet { background: #fff; margin: 0 auto 12px;
           box-shadow: 0 1px 4px rgba(0,0,0,.3); }
  /* 縮めて1枚に収める帳票も、紙の寸法で見せる(縮み具合が紙と同じになる) */
  .sheet--fit { width: __W__mm; min-height: __H__mm; }
  /* 半分に切る帳票は、画面でも**紙1枚の姿**で見せる(A4横・左半分に票・
     右半分は白紙)。縦に並べて伸ばすと「下にもう1枚ある」ように見えるため。
     切り取り線は票の右の縁に引く(表の高さだけ ── 印刷と同じ) */
  .sheet--half { width: __W__mm; min-height: __H__mm; align-items: flex-start; }
  .sheet--half .slip { border-right: 1px dashed #888; }
  .cut { display: none; }
  .screen-only { margin: 0 auto 12px; max-width: 900px; color: #374151;
                 font-size: 12px; }
  .sheet-no { max-width: __W__mm; margin: 0 auto 4px; font-weight: bold; }
}
@media print { .screen-only { display: none; } }
"""

# 画面で見たときだけ出る操作案内(印刷はされない)
_PRINT_HINT = (
    '<p class="screen-only">この画面で <b>Ctrl+P</b> を押すと印刷できます。'
    "余白は「デフォルト」「なし」のどちらでも同じ位置に刷れます"
    "(紙の端から5mm以上内側)。<b>倍率は100%</b>のままにしてください"
    "(「背景のグラフィック」を有効にすると網掛けも印刷されます)。</p>"
)

# はみ出す分だけ縮めて1枚に収める(VBA `FitToPagesTall = 1`)。
# 紙と同じ幅で組んだ中身の高さを測り、`.fit` に zoom をかける。
# 紙の中の余白(`.sheet` の padding)は縮めない ── 端から 5mm を守るため
_FIT_SCRIPT = """
<script>
(function () {
  var avail = %(avail_mm)s * 96 / 25.4 - 2;   // 刷れる高さ(px)。端数のぶん 2px 控える
  function fit() {
    document.querySelectorAll(".sheet--fit .fit").forEach(function (n) {
      n.style.zoom = "";
      for (var i = 0; i < 3; i++) {          // 縮めると折り返しが減るので詰め直す
        var h = n.getBoundingClientRect().height;
        var z = parseFloat(n.style.zoom || "1");
        if (h <= avail) break;
        n.style.zoom = String(z * avail / h);
      }
    });
  }
  fit();
  window.addEventListener("load", fit);
  window.addEventListener("beforeprint", fit);
  document.addEventListener("input", fit);
}());
</script>
"""


def escape(value: object) -> str:
    """帳票に値を埋めるときのエスケープ。"""
    return html.escape("" if value is None else str(value))


def editable(key: str, value: object, *, placeholder: str = "") -> str:
    """**その場で直せる値**にする。

    VBA版は帳票をシートに出していたので、気に入らなければシートを
    直してから印刷できました。台数が決まらない・寸法を微調整したい・
    拠点名を頭に入れたい・期日を書きたい ── どれも紙に出す前に人が
    決めることで、選定の計算とは別物です(現場の声)。

    直した内容は `data-edit` の名前で送り返し、**別のロットを検索する
    まで**そのまま残します。名前は帳票の中で一意にしてください。

    印刷には何も足しません(点線も背景も画面のときだけ)。
    """
    ph = f' data-placeholder="{escape(placeholder)}"' if placeholder else ""
    return (f'<span class="edit" contenteditable="true" spellcheck="false"'
            f' data-edit="{escape(key)}"{ph}>{escape(value)}</span>')


# 直せる欄の見た目と、直した内容を送り返す仕掛け。
# **画面のときだけ**([@media screen])── 紙には点線も案内も出さない
_EDIT_CSS = """
@media screen {
  .edit { outline: none; border-bottom: 1px dashed #9aa3ad;
          min-width: 2em; display: inline-block; cursor: text; }
  .edit:hover { background: #eef4ff; }
  .edit:focus { background: #fff7d6; border-bottom-color: #1d4ed8; }
  .edit:empty::before { content: attr(data-placeholder); color: #9aa3ad; }
  .editbar { margin: 0 auto 12px; max-width: 900px; font-size: 12px;
             color: #374151; }
  .editbar b { color: #1d4ed8; }
  .editbar .saved { color: #15803d; margin-left: .5em; }
}
@media print { .edit { border: 0; } .editbar { display: none; } }
"""

_EDIT_HINT = (
    '<p class="editbar">点線の欄は<b>その場で直せます</b>'
    "(台数・寸法・担当者・期限日・見出しの頭など)。"
    "直した内容は<b>別のロットを検索するまで</b>残ります。"
    '<span class="saved" id="editSaved"></span></p>'
)

# 直した内容をサーバへ送り返す。**帳票は独立したページ**(別窓で開く)
# なので、アプリ本体のJSは読み込まれていない。ここだけで完結させる
_EDIT_SCRIPT = """
<script>
(function () {
  var url = %(url)s, note = document.getElementById("editSaved"), timer = 0;
  if (!url) return;
  function collect() {
    var out = {};
    document.querySelectorAll("[data-edit]").forEach(function (n) {
      out[n.dataset.edit] = n.textContent.trim();
    });
    return out;
  }
  function save() {
    fetch(url, { method: "POST", headers: { "Content-Type": "application/json" },
                 body: JSON.stringify({ edits: collect() }) })
      .then(function (r) {
        note.textContent = r.ok ? "保存しました" : "保存できませんでした";
        window.setTimeout(function () { note.textContent = ""; }, 2000);
      })
      .catch(function () { note.textContent = "保存できませんでした"; });
  }
  document.addEventListener("input", function (e) {
    if (!e.target.closest("[data-edit]")) return;
    window.clearTimeout(timer);
    timer = window.setTimeout(save, 600);
  });
  // 打ち終わってすぐ印刷しても取りこぼさない
  document.addEventListener("blur", function (e) {
    if (e.target.closest("[data-edit]")) { window.clearTimeout(timer); save(); }
  }, true);
  // 改行は入れさせない(1行の欄なので、入ると印刷でずれる)
  document.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.closest("[data-edit]")) {
      e.preventDefault(); e.target.blur();
    }
  });
}());
</script>
"""


def render_html(report: Report, *, edit_url: str = "") -> str:
    """帳票をHTML文字列にする。

    `edit_url` を渡すと、`editable()` で作った欄がその場で直せるように
    なる(直した内容はそのURLへ送り返す)。渡さなければ読むだけ。
    """
    if report.cut_in_half:
        # 左半分に収めて、真ん中に切り取り線。右半分は空けておく。
        # 画面では各紙の上に「何枚目か」を添える(印刷はされない)
        total = len(report.sheets)
        sheets = "\n".join(
            f'<p class="screen-only sheet-no">{i}枚目 / 全{total}枚</p>'
            f'<div class="sheet sheet--half">'
            f'<div class="slip">{s}</div><div class="cut"></div></div>'
            for i, s in enumerate(report.sheets, 1))
    elif report.fit_one_page:
        sheets = "\n".join(f'<div class="sheet sheet--fit"><div class="fit">{s}</div></div>'
                            for s in report.sheets)
    else:
        sheets = "\n".join(f'<div class="sheet">{s}</div>'
                            for s in report.sheets)
    width, height = report.setup.page_mm()
    base_css = (BASE_CSS.replace("__W__", f"{width:g}")
                .replace("__H__", f"{height:g}"))
    extra_css = _EDIT_CSS if edit_url else ""
    hint = _EDIT_HINT if edit_url else ""
    script = (_EDIT_SCRIPT % {"url": json.dumps(edit_url)}) if edit_url else ""
    if report.fit_one_page:
        # ダイアログで余白を「最小」(プリンタの刷れない幅)にされても
        # 1枚に収まるよう、その分も控えて縮める
        avail = height - 2 * (report.setup.margin_mm + PRINTER_EDGE_MM)
        script += _FIT_SCRIPT % {"avail_mm": f"{avail:g}"}
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja"><head><meta charset="utf-8">'
        f"<title>{escape(report.title)}</title>"
        f"<style>{report.setup.to_css()}\n{base_css}\n{extra_css}\n"
        f"{report.setup.extra_css}</style>"
        f"</head><body>{_PRINT_HINT}{hint}{sheets}{script}</body></html>"
    )


# ------------------------------------------------------------------
# 帳票を組み立てるときの小道具
# ------------------------------------------------------------------
def table(rows: Sequence[Sequence[object]], *, header: Optional[Sequence[object]] = None,
          widths: Optional[Sequence[str]] = None, css_class: str = "form") -> str:
    """罫線付きの表を作る(Excelのセル罫線に相当)。"""
    parts = [f'<table class="{css_class}">']
    if widths:
        parts.append("<colgroup>")
        parts.extend(f'<col style="width:{w}">' for w in widths)
        parts.append("</colgroup>")
    if header:
        parts.append("<thead><tr>")
        parts.extend(f"<th>{escape(c)}</th>" for c in header)
        parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>")
        parts.extend(f"<td>{escape(c)}</td>" for c in row)
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def label_pairs(pairs: Sequence[tuple[str, object]], *, columns: int = 2) -> str:
    """「項目名: 値」の並びを表で組む(VBAのラベル+値のセル配置に相当)。"""
    rows: list[list[object]] = []
    current: list[object] = []
    for caption, value in pairs:
        current.extend([caption, value])
        if len(current) >= columns * 2:
            rows.append(current)
            current = []
    if current:
        current.extend([""] * (columns * 2 - len(current)))
        rows.append(current)
    return table(rows)
