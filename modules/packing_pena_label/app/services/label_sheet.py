# -*- coding: utf-8 -*-
"""ラベル台紙の印刷レイアウト。

== どうやって Excel の台紙を印刷フォーマットへ持ち込むか ==

Excel 台紙は「全列を極細（既定 1.625）にして結合セルで組む」方眼方式で、
行高・列幅をシール台紙に合わせて手で詰めてあった。
そのまま HTML へ移すのではなく、**寸法をデータへ起こして
mm 絶対配置で描く**方針を採る。

    1. 台紙の寸法は data/label_stock.json（原点・ピッチ・ラベル寸法・
       ラベル内のフィールド位置）。Excel 台紙から採寸した値を初期値にしてある。
    2. 描画は @page{size:A4; margin:0} の上に mm で絶対配置する。
       ブラウザーの既定余白・拡大縮小に左右されない。
    3. バーコードは **フォントに依存せず自分で描く**（barcode39）。
    4. プリンター差・台紙ロット差は **試し刷りによる較正**
       （offsetX / offsetY / scale）で吸収する。

Excel の行間隔は 28.02〜29.43mm とばらついていた（手調整の累積）。
実物のシール台紙は等ピッチのはずなので、ここでは **等ピッチで組み**、
ズレは較正で合わせる。
"""

from __future__ import annotations

import html
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import barcode39 as BC

log = logging.getLogger(__name__)


@dataclass
class Calibration:
    """試し刷りで合わせる補正値。"""
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    scale_pct: float = 100.0

    def to_dict(self) -> dict:
        return {"offsetXMm": self.offset_x_mm, "offsetYMm": self.offset_y_mm,
                "scalePct": self.scale_pct}


@dataclass
class LabelStock:
    """1 種類のシール台紙の寸法。"""
    id: str
    name: str
    note: str
    page_w: float
    page_h: float
    cols: int
    rows: int
    pages: int
    origin_x: float
    origin_y: float
    pitch_x: float
    pitch_y: float
    label_w: float
    label_h: float
    fields: List[dict] = field(default_factory=list)
    header_fields: List[dict] = field(default_factory=list)
    measured: dict = field(default_factory=dict)
    #: ヘッダー帯が占める段数（用紙幅いっぱいを使う）
    header_rows: int = 0
    #: ラベルに使う段数（残りは余り）
    label_rows: int = 0
    padding: float = 2.0
    header_padding: float = 3.0
    #: ラベル内の罫線（``{"x1","y1","x2","y2"}``、mm。ラベル内側の左上が原点）
    rules: List[dict] = field(default_factory=list)
    #: ヘッダー帯の罫線
    header_rules: List[dict] = field(default_factory=list)
    #: 罫線の太さ（mm）
    rule_mm: float = 0.2
    #: セル内の左右余白（mm）。罫線に文字が触れないようにする
    cell_pad: float = 0.6
    #: 紙の端からこれより内側にだけ刷る（mm）。プリンターは縁 約 4mm に刷れない
    safe_margin: float = 5.0

    @property
    def per_page(self) -> int:
        """1 シートに載るラベル枚数。"""
        rows = self.label_rows or (self.rows - self.header_rows)
        return self.cols * max(rows, 0)

    @property
    def capacity(self) -> int:
        """1 シートに載る枚数。シート数はラベルの件数で決まるので上限は設けない。"""
        return self.per_page

    def label_origin_y(self) -> float:
        """ラベル 1 段目の上端（ヘッダー帯のぶん下げる）。"""
        return self.origin_y + self.pitch_y * self.header_rows

    def position(self, index: int) -> Optional[tuple]:
        """通し番号 (0 始まり) -> (シート番号, x_mm, y_mm)。

        シート数は印刷するラベルの件数で決まるため、上限は設けない。
        """
        if index < 0 or self.per_page <= 0:
            return None
        page, rest = divmod(index, self.per_page)
        row, col = divmod(rest, self.cols)
        return (page,
                self.origin_x + self.pitch_x * col,
                self.label_origin_y() + self.pitch_y * row)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "note": self.note,
            "pageWidthMm": self.page_w, "pageHeightMm": self.page_h,
            "cols": self.cols, "rows": self.rows, "pages": self.pages,
            "originXMm": self.origin_x, "originYMm": self.origin_y,
            "pitchXMm": self.pitch_x, "pitchYMm": self.pitch_y,
            "labelWidthMm": self.label_w, "labelHeightMm": self.label_h,
            "perPage": self.per_page, "capacity": self.capacity,
            "headerRows": self.header_rows, "labelRows": self.label_rows,
            "measured": self.measured,
        }


#: data/label_stock.json が無い場合の内蔵定義（最小限）
_FALLBACK = {
    "id": "fallback", "name": "内蔵既定", "note": "",
    "page": {"widthMm": 210.0, "heightMm": 297.0},
    "grid": {"cols": 2, "rows": 8, "pages": 2,
             "originXMm": 2.08, "originYMm": 25.70,
             "pitchXMm": 108.11, "pitchYMm": 28.90,
             "labelWidthMm": 97.97, "labelHeightMm": 24.49},
    "fields": [],
}


def load_stocks(path: str) -> List[LabelStock]:
    """台紙定義を読む。読めなければ内蔵既定 1 件を返す。"""
    raw = None
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as exc:
            log.warning("ラベル台紙定義を読めません（内蔵既定を使用）: %s", exc)
    entries = (raw or {}).get("stocks") or [_FALLBACK]

    out: List[LabelStock] = []
    for e in entries:
        page = e.get("page", {})
        grid = e.get("grid", {})
        try:
            out.append(LabelStock(
                id=str(e.get("id") or "stock"),
                name=str(e.get("name") or e.get("id") or "台紙"),
                note=str(e.get("note") or ""),
                page_w=float(page.get("widthMm", 210.0)),
                page_h=float(page.get("heightMm", 297.0)),
                cols=int(grid.get("cols", 2)),
                rows=int(grid.get("rows", 8)),
                pages=int(grid.get("pages", 1)),
                origin_x=float(grid.get("originXMm", 0.0)),
                origin_y=float(grid.get("originYMm", 0.0)),
                pitch_x=float(grid.get("pitchXMm", 0.0)),
                pitch_y=float(grid.get("pitchYMm", 0.0)),
                label_w=float(grid.get("labelWidthMm", 0.0)),
                label_h=float(grid.get("labelHeightMm", 0.0)),
                fields=list(e.get("fields") or []),
                header_fields=list(e.get("headerFields") or []),
                measured=dict(e.get("measured") or {}),
                header_rows=int(grid.get("headerRows", 0)),
                label_rows=int(grid.get("labelRows", 0)),
                padding=float(grid.get("paddingMm", 2.0)),
                header_padding=float(grid.get("headerPaddingMm", 3.0)),
                rules=list(e.get("rules") or []),
                header_rules=list(e.get("headerRules") or []),
                rule_mm=float(grid.get("ruleMm", 0.2)),
                cell_pad=float(grid.get("cellPadMm", 0.6)),
                safe_margin=float(grid.get("safeMarginMm", 5.0)),
            ))
        except (TypeError, ValueError) as exc:
            log.warning("ラベル台紙定義を解釈できません（読み飛ばし）: %s", exc)
    return out or [load_stocks("")[0]] if out else [
        LabelStock("fallback", "内蔵既定", "", 210, 297, 2, 8, 1,
                   2.08, 25.7, 108.11, 28.9, 97.97, 24.49)]


def get_stock(path: str, stock_id: str = "") -> LabelStock:
    stocks = load_stocks(path)
    if stock_id:
        for s in stocks:
            if s.id == stock_id:
                return s
    return stocks[0]


# ============================================================ 描画
def _esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


#: 1 文字あたりの送り幅（em）。ラベルの書体
#: （Yu Gothic / 游ゴシック / Meiryo / sans-serif）で実測した値に
#: 余裕を持たせたもの。実測の最大は半角英数大文字で 0.623em。
_ADVANCE_EM = 0.65
#: 全角（漢字・かな・全角記号）
_ADVANCE_EM_WIDE = 1.0
#: これより小さくはしない（読めなくなるため）
_MIN_FONT_MM = 1.2


def _text_em(text: str) -> float:
    """文字列の送り幅を em で見積もる。"""
    import unicodedata

    total = 0.0
    for ch in text:
        # 'A'（曖昧）は環境依存だが、この用途（× や ℃）では半角幅で出る
        wide = unicodedata.east_asian_width(ch) in ("W", "F")
        total += _ADVANCE_EM_WIDE if wide else _ADVANCE_EM
    return total


def fit_font_mm(text: str, size_mm: float, box_w_mm: float) -> float:
    """枠に収まる文字サイズを返す。収まるならそのまま。

    ``.lf`` は ``overflow:hidden`` なので、はみ出すと**黙って切れる**。
    検査番号や型番が切れたラベルは現場で使えないため、
    入りきらない場合は縮めて **全部見せる** ことを優先する。
    """
    if not text or box_w_mm <= 0 or size_mm <= 0:
        return size_mm
    em = _text_em(text)
    if em <= 0:
        return size_mm
    need = em * size_mm
    if need <= box_w_mm:
        return size_mm
    return max(box_w_mm / em, _MIN_FONT_MM)


def _rules_html(rules: List[dict], line_mm: float) -> str:
    """罫線。線分を 1 本ずつ矩形として置く。

    セルごとに枠線を付けると隣り合う辺が二重になって太さが変わるため、
    **線分で指定**する。座標は枠の内側（padding の内側）の左上が原点。
    """
    if not rules or line_mm <= 0:
        return ""
    out: List[str] = []
    for r in rules:
        try:
            x1 = float(r.get("x1", 0)); y1 = float(r.get("y1", 0))
            x2 = float(r.get("x2", x1)); y2 = float(r.get("y2", y1))
        except (TypeError, ValueError):
            continue
        w = float(r.get("w", line_mm))
        if w <= 0:
            continue
        x, y = min(x1, x2), min(y1, y2)
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if dx <= 0 and dy <= 0:
            continue
        # 線の太さのぶん、線が中心に来るよう半分ずらす
        if dy <= 0:                                  # 横線
            out.append('<div class="rl" style="left:%.3fmm;top:%.3fmm;'
                       'width:%.3fmm;height:%.3fmm;"></div>'
                       % (x, y - w / 2, dx, w))
        elif dx <= 0:                                # 縦線
            out.append('<div class="rl" style="left:%.3fmm;top:%.3fmm;'
                       'width:%.3fmm;height:%.3fmm;"></div>'
                       % (x - w / 2, y, w, dy))
        else:
            log.warning("斜めの罫線は引けません: %r", r)
    return "".join(out)


#: バーコードの下に添える文字の大きさ（mm）の既定。
#: 以前は 2.2mm で、現場から「読みにくい、もう少し大きく」と言われた。
#: 台紙定義（label_stock.json）の各バーコード欄で ``textSize`` を書けば変えられる。
BARCODE_TEXT_MM = 3.0


def _barcode_html(text: str, w: float, h: float, mode: str,
                  family: str, font_path: str = "",
                  text_mm: float = BARCODE_TEXT_MM) -> str:
    """バーコード 1 本。モードにより SVG かフォントで出す。"""
    if not text:
        return ""
    if mode == "font":
        return BC.font_html(text, w, h, family, font_path=font_path,
                            text_mm=text_mm)
    try:
        return BC.svg(text, w, h, text_mm=text_mm)
    except BC.Code39Error as exc:
        log.warning("バーコードを描けません: %s", exc)
        return '<span class="bcerr">%s</span>' % _esc(text)


def _field_html_mode(spec: dict, data: Dict[str, str],
                     mode: str, family: str, font_path: str = "",
                     cell_pad: float = 0.0) -> str:
    x = float(spec.get("x", 0)); y = float(spec.get("y", 0))
    w = float(spec.get("w", 0)); h = float(spec.get("h", 0))
    style = "left:%.3fmm;top:%.3fmm;width:%.3fmm;height:%.3fmm;" % (x, y, w, h)

    kind = spec.get("type", "field")
    if kind == "barcode":
        text = data.get(spec.get("source", ""), "")
        try:
            text_mm = float(spec.get("textSize", BARCODE_TEXT_MM))
        except (TypeError, ValueError):
            text_mm = BARCODE_TEXT_MM
        inner = _barcode_html(text, w, h, mode, family, font_path, text_mm)
        if not inner:
            return ""
        return '<div class="lf bc" style="%s">%s</div>' % (style, inner)

    text = spec.get("text", "") if kind == "static" \
        else data.get(spec.get("source", ""), "")
    pad = float(spec.get("pad", cell_pad))
    if pad > 0:
        style += "padding:0 %.3fmm;" % pad
    # 字詰めは**余白を引いた幅**で見る（余白ぶん食い込ませない）
    style += "font-size:%.2fmm;" % fit_font_mm(
        text, float(spec.get("size", 2.2)), max(w - pad * 2, 0.1))
    if spec.get("bold"):
        style += "font-weight:700;"
    align = spec.get("align", "left")
    if align != "left":
        style += "justify-content:%s;" % ("flex-end" if align == "right"
                                          else "center")
    return '<div class="lf" style="%s">%s</div>' % (style, _esc(text))


def _label_html(stock: LabelStock, lab: Dict[str, str], x: float, y: float,
                mode: str, family: str, guides: bool,
                font_path: str = "") -> str:
    pad = stock.padding
    inner = (_rules_html(stock.rules, stock.rule_mm)
             + "".join(_field_html_mode(f, lab, mode, family, font_path,
                                        stock.cell_pad)
                       for f in stock.fields))
    return ('<div class="label%s" style="left:%.3fmm;top:%.3fmm;'
            'width:%.3fmm;height:%.3fmm;">'
            '<div class="lpad" style="inset:%.3fmm;">%s</div></div>'
            % (" guide" if guides else "", x, y,
               stock.label_w, stock.label_h, pad, inner))


def _header_html(stock: LabelStock, head: Dict[str, str],
                 guides: bool) -> str:
    """ヘッダー帯。用紙幅いっぱいを使う。"""
    if not stock.header_rows or not stock.header_fields:
        return ""
    w = stock.pitch_x * stock.cols
    h = stock.pitch_y * stock.header_rows
    pad = stock.header_padding
    inner = (_rules_html(stock.header_rules, stock.rule_mm)
             + "".join(_field_html_mode(f, head, "svg", "", "",
                                        stock.cell_pad)
                       for f in stock.header_fields))
    return ('<div class="header%s" style="left:%.3fmm;top:%.3fmm;'
            'width:%.3fmm;height:%.3fmm;">'
            '<div class="lpad" style="inset:%.3fmm;">%s</div></div>'
            % (" guide" if guides else "", stock.origin_x, stock.origin_y,
               w, h, pad, inner))


def _sheet_html(stock: LabelStock, body: str,
                cal: Calibration) -> str:
    tf = ""
    if cal.offset_x_mm or cal.offset_y_mm or cal.scale_pct != 100.0:
        tf = ('transform:translate(%.3fmm,%.3fmm) scale(%.5f);'
              'transform-origin:0 0;'
              % (cal.offset_x_mm, cal.offset_y_mm, cal.scale_pct / 100.0))
    return ('<section class="sheet" style="width:%.3fmm;height:%.3fmm;">'
            '<div class="sheet-inner" style="%s">%s</div></section>'
            % (stock.page_w, stock.page_h, tf, body))


def render_blocks(stock: LabelStock, blocks: List[dict],
                  cal: Optional[Calibration] = None,
                  show_guides: bool = False,
                  barcode_mode: str = "svg",
                  barcode_family: str = "",
                  barcode_font_path: str = "") -> str:
    """台紙 1 枚ぶんずつ（ヘッダー帯 + ラベル）を並べて印刷ページにする。

    ``blocks`` の 1 件は ``{"header": {...}, "labels": [{...}, ...]}``。
    ラベルが 1 シートに収まらない場合はシートを足し、
    **ヘッダー帯は各シートに繰り返す**（Excel 台紙が行 3 と行 92 の
    両方にヘッダーを持っていたのと同じ）。
    """
    cal = cal or Calibration()
    per = stock.per_page or 1
    out: List[str] = []

    for blk in blocks:
        labels = blk.get("labels") or []
        head = blk.get("header") or {}
        if not labels:
            continue
        for p in range((len(labels) + per - 1) // per):
            chunk = labels[p * per:(p + 1) * per]
            parts = [_header_html(stock, head, show_guides)]
            for i, lab in enumerate(chunk):
                row, col = divmod(i, stock.cols)
                x = stock.origin_x + stock.pitch_x * col
                y = stock.label_origin_y() + stock.pitch_y * row
                parts.append(_label_html(stock, lab, x, y,
                                         barcode_mode, barcode_family,
                                         show_guides, barcode_font_path))
            out.append(_sheet_html(stock, "".join(parts), cal))

    if not out:
        return '<div class="empty">印刷するラベルがありません。</div>'
    return "".join(out)


def render_pages(stock: LabelStock, labels: List[Dict[str, str]],
                 cal: Optional[Calibration] = None,
                 show_guides: bool = False,
                 barcode_mode: str = "svg",
                 barcode_family: str = "") -> str:
    """ヘッダー無しでラベルだけ並べる（後方互換・テスト用）。"""
    if not labels:
        return '<div class="empty">印刷するラベルがありません。</div>'
    return render_blocks(stock, [{"header": {}, "labels": labels}],
                         cal, show_guides, barcode_mode, barcode_family)


#: 刷れる範囲の確認に使う、実際に入る最長の値
_SAMPLE_VALUES = {
    "barcodeKataban": "*BJB7610400QR*",
    "barcodeKensa": "*Z987654-0216 1234.5*",
}


def ink_outside_safe_area(stock: LabelStock) -> List[str]:
    """紙の端から ``safe_margin`` より外側へ出る刷り物を返す（補正なしの位置で）。

    プレビューでは収まって見えても、プリンターは紙の縁 約 4mm に刷れないため
    **刷ると欠ける**。文字の枠・罫線・バーコードのバー（白い静止帯は除く）を
    用紙上の位置に直して確かめる。空なら問題なし。
    """
    m = stock.safe_margin
    lo_x, hi_x = m, stock.page_w - m
    lo_y, hi_y = m, stock.page_h - m
    out: List[str] = []

    def check(name, x0, y0, x1, y1):
        if x0 < lo_x - 1e-6 or x1 > hi_x + 1e-6 or y0 < lo_y - 1e-6 or y1 > hi_y + 1e-6:
            out.append("%s (%.1f〜%.1f, %.1f〜%.1f mm)" % (name, x0, x1, y0, y1))

    def ink_x(f, x0):
        w = float(f.get("w", 0))
        if f.get("type") == "barcode":
            data = BC.normalize(_SAMPLE_VALUES.get(f.get("source", ""), "X" * 12))
            try:
                q = BC.quiet_zone_mm(w, BC.modules(data))
            except BC.Code39Error:
                q = 0.0
            return x0 + q, x0 + w - q
        return x0, x0 + w

    # ヘッダー帯（1 段目・用紙幅いっぱい）
    if stock.header_rows:
        hx = stock.origin_x + stock.header_padding
        hy = stock.origin_y + stock.header_padding
        for f in stock.header_fields:
            x0, x1 = ink_x(f, hx + float(f.get("x", 0)))
            y0 = hy + float(f.get("y", 0))
            check("ヘッダー %s" % f.get("key"), x0, y0, x1, y0 + float(f.get("h", 0)))
        for r in stock.header_rules:
            check("ヘッダーの罫線", hx + min(r["x1"], r["x2"]), hy + min(r["y1"], r["y2"]),
                  hx + max(r["x1"], r["x2"]), hy + max(r["y1"], r["y2"]))

    # ラベル（左右の列 × 最初と最後の段）
    rows = stock.label_rows or (stock.rows - stock.header_rows)
    for col in range(stock.cols):
        for row in sorted({0, max(rows - 1, 0)}):
            lx = stock.origin_x + stock.pitch_x * col + stock.padding
            ly = stock.label_origin_y() + stock.pitch_y * row + stock.padding
            where = "%d列目%d段目" % (col + 1, row + 1)
            for f in stock.fields:
                x0, x1 = ink_x(f, lx + float(f.get("x", 0)))
                y0 = ly + float(f.get("y", 0))
                check("%s %s" % (where, f.get("key")), x0, y0, x1,
                      y0 + float(f.get("h", 0)))
            for r in stock.rules:
                check("%s 罫線" % where, lx + min(r["x1"], r["x2"]),
                      ly + min(r["y1"], r["y2"]), lx + max(r["x1"], r["x2"]),
                      ly + max(r["y1"], r["y2"]))
    return out


#: 較正シートの段の見出しの高さ（mm。.rowlab の font-size 2.6mm の行の箱。実測 3.4mm）
ROWLAB_H = 3.8


def _last_rowlab_top(stock: LabelStock, ry: float) -> float:
    """最後の段の見出し（「10段ぶん = 297.0mm」）の上端。

    段の線のすぐ上に置くが、**紙の下端から safe_margin より内側**に収める
    （紙の下端に重なる線の見出しは、線のすぐ上だと縁 約 4mm に入って刷れない）。
    """
    return min(ry - 4.2, stock.page_h - stock.safe_margin - ROWLAB_H)


#: 較正シートの目盛りの帯（紙の上端から safe_margin + 7mm まで。大きい目盛りの長さ）
TICK_BAND = 7.0


def _rowlab_top(stock: LabelStock, ry: float) -> float:
    """段の見出し（「2段目の上端 = 29.7mm」）の上端。段の線のすぐ下。

    紙の上端に重なる 1 段目の線の見出しは、**目盛りの帯の下**まで下げる
    （紙の上端から safe_margin より内側、かつ目盛りと重ならない）。
    """
    return max(ry + 0.4, stock.safe_margin + TICK_BAND + 0.5)


def render_ruler_page(stock: LabelStock,
                      cal: Optional[Calibration] = None) -> str:
    """較正用の試し刷り。

    ラベル枠・目盛りに加えて、**測定線**と**基準十字**を刷る。
    定規で測った値を画面へ入れれば、倍率と補正X/Yが決まる。

    目盛りも枠も **補正を掛けた状態** で刷る。
    補正の外に置くと、補正を変えても試し刷りの見た目が変わらず
    「効いていない」と誤解する（実際には効いている）ため。
    """
    from . import label_align as LA

    cal = cal or Calibration()
    marks: List[str] = []

    # 10mm ごとの目盛り（上端・左端）。**紙の端から safe_margin より内側だけ**
    # （端の 0mm・210mm の目盛りは縁に重なって刷れない。統合版で直した）
    m = stock.safe_margin
    x = 0
    while x <= stock.page_w:
        if x < m or x > stock.page_w - m:
            x += 10
            continue
        big = (x % 50 == 0)
        marks.append('<div class="tick h%s" style="left:%.3fmm;"></div>'
                     % (" big" if big else "", x))
        if big:
            marks.append('<div class="tlab" style="left:%.3fmm;top:6mm;">%d</div>'
                         % (x + 0.6, x))
        x += 10
    y = 0
    while y <= stock.page_h:
        if y < m or y > stock.page_h - m:
            y += 10
            continue
        big = (y % 50 == 0)
        marks.append('<div class="tick v%s" style="top:%.3fmm;"></div>'
                     % (" big" if big else "", y))
        if big:
            marks.append('<div class="tlab" style="left:6mm;top:%.3fmm;">%d</div>'
                         % (y + 0.6, y))
        y += 10

    # --- 測定線（設計 100.0mm）---
    # 紙の端は定規を当てにくく、プリンターが刷れない縁もあるため、
    # **紙の内側に引いた 2 本の線の間**を測ってもらう。
    # 置き場所は段の境目から離す（段の線と見間違えないように）。
    x0, y0 = LA.CROSS_X_MM, LA.CROSS_Y_MM
    span = LA.SPAN_MM

    hx = max(6.0, (stock.page_w - span) / 2.0)
    hy = stock.page_h * 0.565
    if stock.pitch_y > 6.0:                       # 段の真ん中あたりへ寄せる
        n = int((hy - stock.origin_y) / stock.pitch_y)
        hy = stock.origin_y + stock.pitch_y * (n + 0.5)
    vx = min(stock.page_w - 45.0, stock.page_w - 12.0)
    vy = max(6.0, (stock.page_h - span) / 2.0)

    marks.append('<div class="mline h" style="left:%.3fmm;top:%.3fmm;'
                 'width:%.3fmm;"></div>' % (hx, hy, span))
    for xx in (hx, hx + span):
        marks.append('<div class="mend v" style="left:%.3fmm;top:%.3fmm;">'
                     '</div>' % (xx, hy - 3.0))
    marks.append('<div class="mlab" style="left:%.3fmm;top:%.3fmm;">'
                 '横の青い線｜この 2 本の間が %.1fmm</div>'
                 % (hx + 1.0, hy - 8.0, span))

    marks.append('<div class="mline v" style="left:%.3fmm;top:%.3fmm;'
                 'height:%.3fmm;"></div>' % (vx, vy, span))
    for yy in (vy, vy + span):
        marks.append('<div class="mend h" style="left:%.3fmm;top:%.3fmm;">'
                     '</div>' % (vx - 3.0, yy))
    # 縦の測定線の見出しは線の**左**へ。右端は段の見出しと重なる。
    marks.append('<div class="mlab" style="left:%.3fmm;top:%.3fmm;">'
                 '縦の青い線｜この 2 本の間が %.1fmm</div>'
                 % (max(4.0, vx - 50.0), vy + span / 2.0 - 6.0, span))

    # --- 基準十字（紙の左上角から x0, y0）---
    marks.append('<div class="cross" style="left:%.3fmm;top:%.3fmm;">'
                 '<div class="cv"></div><div class="ch"></div></div>'
                 % (x0, y0))
    marks.append('<div class="crosslab" style="left:%.3fmm;top:%.3fmm;">'
                 '赤い十字｜紙の左端から %.1fmm ／ 上端から %.1fmm</div>'
                 % (x0 + 7.0, y0 + 2.0, x0, y0))
    # 紙の端から十字までを測る道しるべ
    marks.append('<div class="edgemark h" style="left:0;top:%.3fmm;'
                 'width:%.3fmm;"></div>' % (y0, x0))
    marks.append('<div class="edgemark v" style="left:%.3fmm;top:0;'
                 'height:%.3fmm;"></div>' % (x0, y0))

    # 段の境目（用紙の全段）。台紙に重ねて、下の段ほど大きくなるズレを見る。
    # 1 段だけでは 29.7mm と 30mm の違い（0.3mm）は測れないが、
    # **10 段の合計**なら 297mm と 300mm の 3mm 差になり、目で分かる。
    for r in range(stock.rows + 1):
        ry = stock.origin_y + stock.pitch_y * r
        if ry > stock.page_h + 0.01:
            break
        marks.append('<div class="rowline" style="top:%.3fmm;"></div>' % ry)
        marks.append('<div class="rowlab" style="top:%.3fmm;">%d段目の上端 '
                     '= %.1fmm</div>'
                     % (_rowlab_top(stock, ry), r + 1, ry) if r < stock.rows else
                     '<div class="rowlab" style="top:%.3fmm;">%d段ぶん '
                     '= %.1fmm</div>' % (_last_rowlab_top(stock, ry), stock.rows, ry))

    frames: List[str] = []
    for i in range(stock.per_page):
        row, col = divmod(i, stock.cols)
        fx = stock.origin_x + stock.pitch_x * col
        fy = stock.label_origin_y() + stock.pitch_y * row
        frames.append(
            '<div class="label guide" style="left:%.3fmm;top:%.3fmm;'
            'width:%.3fmm;height:%.3fmm;">'
            '<div class="gno">%d</div>'
            '<div class="gxy">x=%.1f y=%.1f</div></div>'
            % (fx, fy, stock.label_w, stock.label_h, i + 1, fx, fy))
    if stock.header_rows:
        frames.append(
            '<div class="label guide hdr" style="left:%.3fmm;top:%.3fmm;'
            'width:%.3fmm;height:%.3fmm;"><div class="gno">ヘッダー帯</div>'
            '</div>'
            % (stock.origin_x, stock.origin_y,
               stock.pitch_x * stock.cols,
               stock.pitch_y * stock.header_rows))

    tf = ""
    if cal.offset_x_mm or cal.offset_y_mm or cal.scale_pct != 100.0:
        tf = ('transform:translate(%.3fmm,%.3fmm) scale(%.5f);'
              'transform-origin:0 0;'
              % (cal.offset_x_mm, cal.offset_y_mm, cal.scale_pct / 100.0))

    total_y = stock.pitch_y * stock.rows
    info = ('<div class="calinfo">'
            '<b>見かた</b>: この紙を台紙に重ねて明るい所で透かし、'
            '<b>点線の枠</b>がラベルの切れ目から<b>どっちへ何 mm</b> ずれているかを測って、'
            '画面の「ずれを直す」へ入れてください。<br>'
            '<b>下の段ほどずれが大きくなる</b>ときは大きさのずれです。'
            '青い線（%.0fmm のはず）と赤い十字（紙の端から %.0fmm のはず）を測って、'
            '画面の「大きさそのものが合わないとき」へ入れてください。<br>'
            '<b>段ピッチの確認</b>: 台紙の<b>一番下の段</b>の切れ目と赤い線が'
            '合っているか見てください（%d 段ぶんで %.1fmm）。<br>'
            # 台紙の名前は別の行に。**最後の行を短く**して、右下の「N段ぶん」の
            # 見出しと重ならないようにする（補正を入れると 1 行目が長くなる）
            '%s（この紙もその状態で刷っています）<br>台紙 %s'
            '</div>'
            % (span, x0, stock.rows, total_y,
               LA.describe(cal.offset_x_mm, cal.offset_y_mm, cal.scale_pct),
               _esc(stock.name)))

    return ('<section class="sheet ruler" style="width:%.3fmm;height:%.3fmm;">'
            '<div class="sheet-inner" style="%s">%s%s</div>%s</section>'
            % (stock.page_w, stock.page_h, tf,
               "".join(marks), "".join(frames), info))
