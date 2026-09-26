# -*- coding: utf-8 -*-
"""画面（HTML）。VBA の UserForm に対応する。

    テスラ指定サイズ -> /            (index.html)
    テスラ全サイズ   -> /all-size    (all_size.html)
    表記用           -> /breakdown   (breakdown.html)
    風袋計算シート   -> /tare        (tare.html)
    50まで シート    -> /list        (list.html)
    ラベル台紙       -> /labels      (labels.html)
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
import time
from typing import Dict, List

from ..config import code_stamp as _code_stamp
from ..services import size_master as SM
from ..view import Renderer, esc, nl2br

#: サイズごとの色クラス（VBA フォームの配色を踏襲）
_SIZE_CLASS = {
    "0.8mm×53.5mm": "sz-08-53",
    "1.0mm×33.0mm": "sz-10-33",
    "1.0mm×73.0mm": "sz-10-73",
    "0.6mm×82.5mm": "sz-06-825",
    "1.0mm×53.5mm": "sz-10-535",
    "1.0mm×63.0mm": "sz-10-63",
    "1.0mm×40.0mm": "sz-10-40",
}
#: フォーム上の赤字注記（VBA フォームのラベル）
_SIZE_NOTE = {
    "0.8mm×53.5mm": "New 2026.03.26追加",
    "1.0mm×53.5mm": "2019.04.25 43.5mm削除 53.5mm追加",
}
#: 画面に並べる順（VBA フォームの並び）
_SIZE_ORDER = ["0.8mm×53.5mm", "1.0mm×33.0mm", "1.0mm×73.0mm",
               "0.6mm×82.5mm", "1.0mm×53.5mm", "1.0mm×63.0mm",
               "1.0mm×40.0mm"]

#: サイズ名から (厚み, 巾) を取り出す
_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _describe_correction(cal) -> str:
    """いまの補正を、現場の言葉で言う（HTML）。"""
    from ..services import label_align as LA
    return LA.describe_html(cal.offset_x_mm, cal.offset_y_mm, cal.scale_pct)


def _dims_of(name: str):
    """サイズ名の寸法。表記が違っても同じものを同じと見なすため。

        "1.0mm×53.5mm"   -> (1.0, 53.5)
        "1.0×53.5×Coil"  -> (1.0, 53.5)
    """
    nums = [float(x) for x in _DIM_RE.findall(str(name or ""))]
    return (nums[0], nums[1]) if len(nums) >= 2 else ()


class PageRoutes:
    def __init__(self, wf, cfg, started_at: float = 0.0, screens=None):
        self.wf = wf
        self.cfg = cfg
        #: このプロセスが起動した時刻（診断画面で出す）
        self.started_at = started_at or time.time()
        #: 入力画面の使用権（server.AppContext.screens）
        self.screens = screens
        self.r = Renderer(cfg.templates_dir, cache=False)

    @property
    def base(self) -> str:
        """この機能の入口(統合版では ``/pena``)。単体で動かしているときは空文字。"""
        return getattr(self.cfg, "url_prefix", "") or ""

    # ------------------------------------------------------------ 共通
    #: 入力できる画面（= 同時に 1 つしか開かせない画面）
    _INPUT_NAVS = frozenset({"home", "all", "settings"})

    #: 「中身だけ返す」要求かどうか（リクエストごと・スレッドごと）
    _pane_flag = threading.local()

    @classmethod
    def pane_mode(cls, on: bool) -> None:
        """``?pane=1`` のときに中身だけ返すよう切り替える。

        起動ページの中でタブとして表示するために使う。
        別タブで開いた場合（印刷ビューなど）は従来どおり 1 枚の HTML。
        """
        cls._pane_flag.on = bool(on)

    def _shell(self, title: str, content: str, nav: str = "",
               page_script: str = "") -> str:
        if getattr(self._pane_flag, "on", False):
            # 起動ページのタブとして差し込むので、外枠は付けない
            return content + page_script

        src = self.wf.materials.last_source
        label = {"sqlite": "SQLite", "access": "Access(ACE)",
                 "csv": "CSV（検証用）"}.get(src, "未読込")
        # 取得元へ到達できず直前の内容で動いているときは、
        # 全画面のヘッダーで分かるようにする（診断画面だけでは気付けない）。
        stale = bool(getattr(self.wf.materials, "serving_stale", False))
        hint = "資材マスタの取得元"
        if stale:
            label += "（古い内容）"
            hint = ("資材マスタを読み直せないため、直前に読んだ内容で"
                    "計算しています。詳細は診断画面へ")
        ctx = {
            "title": title, "content": content, "pageScript": page_script,
            # 入口と起動トークン(統合版)。外枠の body に載せ、app.js が読む
            "base": self.base, "token": getattr(self.cfg, "app_token", "") or "",
            "appName": self.cfg.app_name, "appId": self.cfg.app_id,
            "version": self.cfg.version, "port": self.cfg.port,
            "build": getattr(self.cfg, "build", ""),
            "codeStamp": _code_stamp(),
            "materialSource": src or "none", "materialSourceLabel": label,
            "materialStale": "stale" if stale else "",
            "materialSourceHint": hint,
            # 入力画面は 1 つだけ。開いてよいか確認できるまで操作させない。
            "screenGuard": "1" if nav in self._INPUT_NAVS else "",
            "screenGuardHidden": "" if nav in self._INPUT_NAVS else "hidden",
            "screenTitle": title,
        }
        for key, name in (("navHome", "home"), ("navAll", "all"),
                          ("navTare", "tare"), ("navList", "list"),
                          ("navBreak", "breakdown"), ("navLabels", "labels"),
                          ("navSettings", "settings")):
            ctx[key] = "on" if nav == name else ""
        return self.r.render("_layout.html", ctx)

    # ============================================================ メイン
    def index(self) -> str:
        state = self.wf.load_state()
        rows = self._size_rows(state)
        ctx = {
            "sizeRows": rows,
            "take1Rows": self._take_rows(1, (1, 2), state),
            "take2Rows": self._take_rows(2, (3, 4), state),
            "cbModeChecked": "checked" if state.is_cb_mode else "",
            "modeHint": ("丈1・丈2 を同時に処理します（VBA の CheckBox モード）"
                         if state.is_cb_mode else
                         "丈1 または 丈2 を個別に処理します（VBA の OptionButton モード）"),
            "lblKensaNo": esc(state.lbl_kensa_no),
            "lblSize1": esc(state.lbl_size1), "lblWeight1": esc(state.lbl_weight1),
            "lblSize2": esc(state.lbl_size2), "lblWeight2": esc(state.lbl_weight2),
            "kensaNo": esc(state.kensa_no),
            "weight1": esc(state.weight1), "weight2": esc(state.weight2),
            "tip1000": "checked" if state.tip == "TIP1000" else "",
            "tip950": "checked" if state.tip == "TIP950" else "",
            "tip820": "checked" if state.tip == "TIP820" else "",
        }
        content = self.r.render("index.html", ctx)
        # 起動ページの中でタブを切り替えるための入れ物。
        # 入力面はここに残したまま、表示だけの面を差し替える。
        content = ('<div id="paneHome">%s</div>'
                   '<div id="paneView" class="pane-view" hidden></div>' % content)
        boot = ("<script>window.__PPL_STATE__=%s;</script>"
                % json.dumps(state.to_dict(), ensure_ascii=False))
        return self._shell("指定サイズ", content, "home", boot)

    def _visible_dims(self):
        """出してよいサイズを (厚み, 巾) の組で返す。設定が空なら None（全件）。"""
        want = [str(x).strip() for x in (self.cfg.visible_sizes or []) if str(x).strip()]
        if not want:
            return None
        return {_dims_of(b) for b in want}

    def visible_bases(self) -> List[str]:
        """画面に出すサイズ名。設定が空なら全件。

        現状ラインへ来ないサイズを並べておくと選び間違いのもとなので、
        出すものを設定（``visible_sizes``）で絞れるようにしてある。
        """
        want = [str(x).strip() for x in (self.cfg.visible_sizes or []) if str(x).strip()]
        known = [c.base_name for c in self.wf.master.all()]
        if not want:
            return known
        # 設定に書かれていても存在しないサイズは無視する
        return [b for b in want if b in known]

    def _size_rows(self, state) -> str:
        """サイズ選択欄。OB（丈1/丈2 個別）と CB（同時）の両方を描く。"""
        show = set(self.visible_bases())
        by_base: Dict[str, List] = {}
        for cfg in self.wf.master.all():
            if cfg.base_name not in show:
                continue
            by_base.setdefault(cfg.base_name, []).append(cfg)

        order = [b for b in _SIZE_ORDER if b in by_base]
        order += [b for b in by_base if b not in order]

        out = []
        for base in order:
            cfgs = sorted(by_base[base], key=lambda c: c.ob_idx)
            c1 = cfgs[0]
            c2 = cfgs[1] if len(cfgs) > 1 else cfgs[0]
            cls = _SIZE_CLASS.get(base, "")
            note = _SIZE_NOTE.get(base, "")

            if c1.cb_key == SM.NAMED_CB_KEY:
                cb_attr = 'data-kind="cbNamed"'
                cb_on = state.named_cb
            else:
                cb_idx = c1.cb_key.replace("CheckBox", "")
                cb_attr = 'data-kind="cb" data-cb="%s"' % esc(cb_idx)
                cb_on = (str(state.selected_cb) == cb_idx)

            ob_hidden = "" if not state.is_cb_mode else " hidden"
            cb_hidden = " hidden" if not state.is_cb_mode else ""

            out.append(
                f'<div class="size-row {cls}">'
                f'<div class="sz-head">{esc(base)}</div>'
                f'<div class="sz-opts">'
                f'<label{ob_hidden}><input type="radio" name="size" data-kind="ob" '
                f'data-ob="{c1.ob_idx}" {"checked" if state.selected_ob == c1.ob_idx else ""}>'
                f'丈1</label>'
                f'<label{ob_hidden}><input type="radio" name="size" data-kind="ob" '
                f'data-ob="{c2.ob_idx}" {"checked" if state.selected_ob == c2.ob_idx else ""}>'
                f'丈2</label>'
                f'<label{cb_hidden}><input type="radio" name="size" {cb_attr} '
                f'{"checked" if cb_on else ""}>丈1・丈2 同時</label>'
                f'</div>'
                + (f'<div class="size-note">{esc(note)}</div>' if note else "")
                + '</div>')
        return "\n".join(out)

    def _take_rows(self, take: int, slots, state) -> str:
        """GW 欄（本数・NW・GW・高さ）。VBA の CoilH/NW/GW/TA に対応。"""
        cls = "" if (take == 1 and state.take1_visible) or \
                    (take == 2 and state.take2_visible) else ""
        out = []
        for n, slot in enumerate(slots, start=1):
            out.append(
                f'<div class="pack">'
                f'<div class="plab">{n}梱包目</div>'
                f'<label><span class="cap">積み本数</span>'
                f'<input type="text" id="coilH{slot}" data-filter="coil" '
                f'value="{esc(state.coil_h.get(slot, ""))}" inputmode="numeric"></label>'
                f'<div><span class="cap">NW</span>'
                f'<div class="out" id="nw{slot}">{esc(state.nw.get(slot, ""))}</div></div>'
                f'<div><span class="cap">GW</span>'
                f'<div class="out" id="gw{slot}">{esc(state.gw.get(slot, ""))}</div></div>'
                f'</div>'
                f'<div class="pack">'
                f'<div class="plab"></div><div></div><div></div>'
                f'<div><span class="cap">高さ(mm)</span>'
                f'<div class="out" id="ta{slot}">{esc(state.ta.get(slot, ""))}</div></div>'
                f'</div>')
        return "\n".join(out)

    # ============================================================ 風袋計算
    def tare(self, print_view: bool = False) -> str:
        data = self.wf.store.all_tare()
        state = self.wf.load_state()
        if not data:
            body = ('<div class="card"><h2>風袋計算</h2>'
                    '<div class="empty">計算結果がありません。'
                    '「重量計算_DB」を実行してください。</div></div>')
            return self._shell("風袋計算", body, "tare")

        blocks = []
        for coil_no in sorted(data):
            p = data[coil_no]
            rows = []
            for r in p["rows"]:
                sum_cls = ' class="sum"' if r["name"] in ("風袋", "GW", "NW") else ""
                rows.append(
                    f'<tr{sum_cls}><th>{esc(r["name"])}</th>'
                    f'<td class="v">{esc(r["value"])}</td>'
                    f'<td class="f">{esc(r["formula"])}</td></tr>')
            blocks.append(
                f'<div class="block"><h3>{esc(p["title"])}'
                f'　<span style="font-weight:400">巾{esc(p["width"])}mm / '
                f'外径{esc(p["tempDiameter"])} / 外周{esc(p["gai"])}m / '
                f'梱包高さ{esc(p["taDisplay"])}mm</span></h3>'
                f'<table><tbody>{"".join(rows)}</tbody></table></div>')

        warn = ""
        missing = sorted({m for p in data.values() for m in p.get("missing", [])})
        if missing:
            warn = ('<div class="warnbox">資材マスタに存在しない資材があり、'
                    '0kg として計算しています: <b>%s</b></div>' % esc("、".join(missing)))
        head = self._report_head("風袋計算", state)
        today = datetime.date.today().strftime("%Y.%m.%d")
        foot = (f'<div class="printfoot"><span></span>'
                f'<span>{esc(today)}印刷</span></div>')
        tools = ('' if print_view else
                 '<div class="btn-row no-print" style="margin-bottom:10px">'
                 f'<a class="btn" href="{self.base}/tare/print" target="_blank" rel="noopener">'
                 '印刷ビューを開く</a>'
                 '<button class="btn" onclick="window.print()">このまま印刷</button>'
                 '</div>')
        body = head + warn + tools + '<div class="blocks">' + "".join(blocks) + '</div>' + foot
        script = "<script>window.addEventListener('load',function(){window.print();});</script>" \
            if print_view else ""
        return self._shell("風袋計算", body, "tare", script)

    def _report_head(self, title: str, state) -> str:
        sizes = " / ".join([s for s in (state.lbl_size1, state.lbl_size2) if s])
        return (f'<div class="report-head"><div class="ttl">{esc(title)}</div>'
                f'<div class="report-meta">検査番号: <b>{esc(state.lbl_kensa_no)}</b>'
                f'　ｺｲﾙｻｲｽﾞ: {esc(sizes)}　'
                f'チップボール: {esc(state.tip)}</div></div>')

    # ============================================================ 羅列計算
    def list_page(self, print_view: bool = False) -> str:
        data = self.wf.store.get_kv("list_result")
        state = self.wf.load_state()
        if not data:
            body = ('<div class="card"><h2>羅列計算（50まで）</h2>'
                    '<div class="empty">計算結果がありません。'
                    'メイン画面の「羅列計算」を実行してください。</div></div>')
            return self._shell("羅列計算", body, "list")

        heads = "".join("<th>%s</th>" % esc(h) for h in data["headers"])
        keys = data["columnKeys"]
        trs = []
        for row in data["rows"]:
            tds = []
            for i, k in enumerate(keys):
                cls = "" if i == 0 else ' class="num"'
                tds.append(f'<td{cls}>{esc(row[k])}</td>')
            trs.append("<tr>%s</tr>" % "".join(tds))

        warn = ""
        if data.get("missing"):
            warn = ('<div class="warnbox">資材マスタに存在しない資材があり、'
                    '0kg として計算しています: <b>%s</b></div>'
                    % esc("、".join(data["missing"])))

        note = ('<div class="hint" style="margin-bottom:8px">'
                '※ この表の「風袋」は VBA と同じく <b>EX-DRY を含まない 8 項目合計</b>、'
                '「梱包高さ」は <b>ハードボード 2.35mm×2 を加算しない</b> 値です'
                '（風袋計算画面の値とは定義が異なります）。</div>')

        tools = ('' if print_view else
                 '<div class="btn-row no-print" style="margin-bottom:10px">'
                 f'<a class="btn" href="{self.base}/list/print" target="_blank" rel="noopener">'
                 '印刷ビューを開く</a>'
                 '<button class="btn" onclick="window.print()">このまま印刷</button></div>')

        today = datetime.date.today().strftime("%Y.%m.%d")
        foot = (f'<div class="printfoot"><span>{esc(data.get("footer", ""))}</span>'
                f'<span>{esc(today)}印刷</span></div>')
        head = (f'<div class="report-head"><div class="ttl">羅列計算（1〜50本）</div>'
                f'<div class="report-meta">巾: {esc(data.get("width", ""))}mm　'
                f'{esc(data.get("footer", ""))}</div></div>')
        body = (head + warn + note + tools +
                f'<div class="scroll"><table class="tbl"><thead><tr>{heads}</tr></thead>'
                f'<tbody>{"".join(trs)}</tbody></table></div>' + foot)
        script = "<script>window.addEventListener('load',function(){window.print();});</script>" \
            if print_view else ""
        return self._shell("羅列計算", body, "list", script)

    # ============================================================ 計算内訳
    def breakdown(self) -> str:
        """VBA ``表記用`` UserForm。値と計算式の内訳。"""
        data = self.wf.store.all_tare()
        if not data:
            body = ('<div class="card"><h2>計算内容（表記用）</h2>'
                    '<div class="empty">計算結果がありません。'
                    '「重量計算_DB」を実行してください。</div></div>')
            return self._shell("計算内容", body, "breakdown")

        # VBA の表記用は「最後に処理した梱包」の内容を保持する。
        # VBA は 4→3→2→1 の順に処理するため、最後は最小番号の梱包になる。
        coil_no = min(data)
        p = data[coil_no]
        b = p["breakdown"]
        disp = p["display"]

        items = [
            ("ピン", disp["PI"], b.get("PIsiki", "")),
            ("チップボール", disp["TIP"], b.get("TIPsiki", "")),
            ("ストレッチフィルム", disp["SUT"], b.get("SUTsiki", "")),
            ("エサフォーム", disp["ESA"], b.get("ESAsiki", "")),
            ("ポリシート", disp["POR"], b.get("PORsiki", "")),
            ("ハードボード", disp["HB"], b.get("HBsiki", "")),
            ("樹脂パレット", disp["PAR"], b.get("PARsiki", "")),
            ("PETバンド", disp["PET"], b.get("PETsiki", "")),
            ("EX-DRY", disp["DRY"], b.get("DRYsiki", "")),
            ("NW", disp["NW"], b.get("NWsiki", "")),
            ("風袋", disp["HU"], b.get("HUsiki", "")),
            ("GW", disp["GW"], b.get("GWsiki", "")),
        ]
        rows = "".join(
            f'<tr><th>{esc(n)}</th><td class="v">{esc(v)}</td>'
            f'<td class="f">{nl2br(f_)}</td></tr>' for n, v, f_ in items)

        tabs = " ".join(
            f'<a class="btn btn-link" style="width:auto;display:inline-block" '
            f'href="{self.base}/breakdown?coil={k}">{esc(data[k]["title"])}</a>'
            for k in sorted(data))

        # 胴巻きハードボードに締結代（HB_BAND_CLEARANCE_M）を入れている場合だけ、
        # 積高との差を出す。現状は 0 なので出ない。
        diff_note = ""
        if p.get("coilHeightDiffMm") not in (None, "", "0.0"):
            diff_note = (
                f'<div class="hint note-diff">'
                f'コイルの積み高さ <b>{esc(p["coilHeightMm"])}mm</b> に対し、'
                f'胴巻きハードボードの高さは '
                f'<b>{esc(p["coilHeightHbMm"])}mm</b>'
                f'（<b>{esc(p["coilHeightDiffMm"])}mm 低い</b>）。<br>'
                f'バンドの締結代ぶん低くしています。</div>')

        body = (f'<div class="card"><h2>計算内容（表記用）— {esc(p["title"])}</h2>'
                f'<div class="hint">巾 {esc(p["width"])}mm / 外径 {esc(p["tempDiameter"])} '
                f'/ 半径 {esc(p["halfDiameter"])} / 外周 {esc(p["gai"])}m / '
                f'コイル高さ {esc(p["coilHeightMm"])}mm / 梱包高さ {esc(p["taDisplay"])}mm / '
                f'HBフラグ {esc(p["hbFlag"])}</div>'
                f'{diff_note}'
                f'<div class="btn-row no-print">{tabs}</div>'
                f'<table class="tbl"><thead><tr><th>資材</th><th>重量(kg)</th>'
                f'<th>計算式</th></tr></thead><tbody>{rows}</tbody></table></div>'
                f'<div class="card"><h2>資材マスタ</h2>'
                f'<div id="matList" class="hint">読込中…</div></div>')
        script = ("<script>fetch(pplUrl('/api/materials'),{headers:pplHeaders()})"
                  ".then(r=>r.json()).then(j=>{"
                  "var e=document.getElementById('matList');"
                  "if(!j.ok){e.textContent=j.message;return;}"
                  "var h='<table class=\"tbl\"><thead><tr><th>梱包資材名</th>"
                  "<th>単位質量</th></tr></thead><tbody>';"
                  "j.rows.forEach(function(r){h+='<tr><td>'+r.name+'</td>"
                  "<td class=\"num\">'+r.mass+'</td></tr>';});"
                  "e.innerHTML=h+'</tbody></table><p class=\"hint\">取得元: '"
                  "+j.source+' / '+j.path+'</p>';});</script>")
        return self._shell("計算内容", body, "breakdown", script)

    # ============================================================ ラベル台紙
    def labels(self, ob_list: List[int] | None = None,
               print_view: bool = False) -> str:
        # 一覧も印刷ビューも、開いた時点のマスタで出す
        self.wf.refresh_label_sheets(force=True)
        master = self.wf.master
        stale = self.wf.calc_is_stale()
        if not ob_list:
            cards = []
            for cfg in master.all():
                h = self.wf.store.get_sheet_header(cfg.ob_idx)
                filled = bool(h["kensaNo"] or h["weight"])
                cards.append(
                    # 一覧に出すのは **ラベルに刷られる名前**（マスタで直せる方）。
                    # cfg.sheet_name は JSON から組み立てた内部の名前で、
                    # マスタを直しても変わらないため食い違って見える。
                    f'<tr><td>{cfg.ob_idx}</td>'
                    f'<td>{esc(cfg.size_cell_label)}</td>'
                    f'<td>{esc(cfg.kataban)}</td><td class="num">{cfg.data_row}</td>'
                    f'<td class="num">{cfg.extra_row}</td>'
                    f'<td>{esc(h["kensaNo"])}</td><td class="num">{esc(h["weight"])}</td>'
                    f'<td>{esc(h["updatedAt"])}</td>'
                    f'<td><a href="{self.base}/labels?ob={cfg.ob_idx}">表示</a>'
                    + (f' / <a href="{self.base}/labels/print?ob={cfg.ob_idx}" target="_blank" '
                       f'rel="noopener">印刷</a>' if filled and not stale
                       else (' / <span class="off">印刷（停止中）</span>'
                             if filled else '')) + '</td></tr>')
            warn = ''
            if stale:
                warn = ('<div class="card stalebox">'
                        '<b>入力が変わっています。印刷を止めています。</b>'
                        'ラベルの <b>本数・高さ・NW・GW</b> は前回「計算」した'
                        'ときのものです。'
                        f'<a href="{self.base}/">メイン画面</a>で「計算」を実行してください。'
                        '</div>')
            body = (warn + '<div class="card"><h2>ラベル台紙一覧</h2>'
                    '<table class="tbl"><thead><tr><th>obIdx</th><th>シート名</th>'
                    '<th>型番</th><th>データ行</th><th>追加行</th><th>検査番号</th>'
                    '<th>重量</th><th>更新</th><th></th></tr></thead>'
                    f'<tbody>{"".join(cards)}</tbody></table></div>')
            return self._shell("ラベル台紙", body, "labels")

        sections = []
        for ob in ob_list:
            v = self.wf.label_view(ob)
            if not v:
                continue
            cfg = v["config"]
            if not v["labels"]:
                sections.append(
                    f'<div class="card"><h2>{esc(cfg["sheetName"])}</h2>'
                    f'<div class="empty">この台紙にはまだ転記されていません。</div></div>')
                continue
            lbls = "".join(
                f'<div class="lbl"><div class="bc">{esc(l["barcodeKataban"])}</div>'
                f'<div class="row"><span class="k">P.N.</span>'
                f'<span class="v">SEALING PLATE</span></div>'
                f'<div class="row"><span class="k">L.C.</span>'
                f'<span class="v">{esc(l["kensaNo"])}</span>'
                f'<span class="v" style="flex:0 0 auto">{esc(l["coilNo"])}</span></div>'
                f'<div class="row"><span class="k">P</span>'
                f'<span class="v">{esc(l["kataban"])}</span></div>'
                f'<div class="row"><span class="k">Q</span>'
                f'<span class="v">{esc(l["weight"])}</span>'
                f'<span class="v" style="flex:0 0 auto">{esc(l["unit"])}</span></div>'
                f'<div class="bc">{esc(l["barcodeKensa"])}</div>'
                f'<div class="cellref">行{l["baseRow"]} / {esc(l["side"])}面 '
                f'({esc(l["cells"]["coilNo"])})</div></div>'
                for l in v["labels"])
            ci = "".join(
                f'<tr><td class="num">{c["row"]}</td><td class="num">{esc(c["coilH"])}</td>'
                f'<td class="num">{esc(c["ta"])}</td><td class="num">{esc(c["nw"])}</td>'
                f'<td class="num">{esc(c["gw"])}</td></tr>' for c in v["coilInfo"])
            sections.append(
                f'<div class="card"><h2>{esc(cfg["sheetName"])}'
                f'（検番 {esc(v["header"]["kensaNo"])} / 重量 {esc(v["header"]["weight"])}kg '
                f'/ 型番 {esc(v["header"]["kataban"])}）</h2>'
                f'<table class="tbl" style="max-width:520px"><thead><tr><th>行</th>'
                f'<th>本数</th><th>高さ</th><th>NW</th><th>GW</th></tr></thead>'
                f'<tbody>{ci}</tbody></table>'
                f'<div class="labelsheet">{lbls}</div></div>')

        tools = ('' if print_view else
                 '<div class="btn-row no-print" style="margin-bottom:10px">'
                 f'<a class="btn" href="{self.base}/labels/print?ob={",".join(map(str, ob_list))}" '
                 'target="_blank" rel="noopener">印刷ビューを開く</a>'
                 f'<a class="btn" href="{self.base}/labels">一覧へ戻る</a></div>')
        body = tools + "".join(sections)
        script = "<script>window.addEventListener('load',function(){window.print();});</script>" \
            if print_view else ""
        return self._shell("ラベル台紙", body, "labels", script)

    # ============================================================ ラベル印刷
    def _calibration(self):
        from ..services.label_sheet import Calibration
        return Calibration(
            offset_x_mm=float(self.cfg.label_offset_x_mm or 0),
            offset_y_mm=float(self.cfg.label_offset_y_mm or 0),
            scale_pct=float(self.cfg.label_scale_pct or 100))

    def _stock(self):
        from ..services.label_sheet import get_stock
        return get_stock(self.cfg.label_stock_path, self.cfg.label_stock_id)

    def _fonts_dir(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.dirname(self.cfg.templates_dir)),
            "assets", "fonts")

    def _barcode_font_file(self) -> str:
        """font モードで実際に使うフォントファイルの絶対パス（無ければ空）。"""
        from ..services import barcode39 as BC

        if (self.cfg.barcode_mode or "svg") != "font":
            return ""
        d = self._fonts_dir()
        chosen = BC.pick_font_file(BC.list_fonts(d),
                                   self.cfg.barcode_font_family or "")
        return os.path.join(d, chosen) if chosen else ""

    def _barcode_font_css(self) -> str:
        """barcode_mode=font のとき、同梱フォントを読み込む CSS を返す。

        設定のフォント名と **ファイルの中身の名前** を突き合わせて選ぶ。
        ファイル名は中身と一致しない（``KsBarCodeCode39.ttf`` の中身は
        ``K's BarCodeFont Code39``）ため、ファイル名順の先頭を使うと
        名前と違うフォントで印刷してしまう。
        """
        from ..services import barcode39 as BC

        if (self.cfg.barcode_mode or "svg") != "font":
            return ""
        fam = (self.cfg.barcode_font_family or "").replace('"', "")
        fonts = BC.list_fonts(self._fonts_dir())
        chosen = BC.pick_font_file(fonts, fam)
        if not chosen:
            # フォントが無いと「ただの文字列」が印刷されてしまうので画面で知らせる
            if fonts:
                have = "／".join(f or fn for fn, f in fonts)
                msg = "フォント「%s」が見つかりません（配置済: %s）" % (fam, have)
            else:
                msg = "バーコードフォントが配置されていません"
            return ('<div class="card no-print warn"><b>%s</b><br>'
                    'このままでは <code>*...*</code> の文字列がそのまま印刷されます。'
                    '設定でバーコードの出し方を「SVG」に戻すか、'
                    'assets/fonts にフォントを置いてください。</div>'
                    '<style>.bcfont{outline:0.2mm dashed #b3261e;color:#b3261e}'
                    '</style>' % esc(msg))
        # style 要素の中では実体参照が戻らないため HTML エスケープしない。
        # 利用側（barcode39._css_family）と同じ関数を通して名前を一致させる。
        return ('<style>@font-face{font-family:"%s";src:url("%s/fonts/%s") '
                'format("truetype");font-display:block;}</style>'
                % (BC.css_family_name(fam), self.base, BC.css_url_path(chosen)))

    def label_print(self, ob_list: List[int], guides: bool = False) -> str:
        """シール台紙に合わせた実寸印刷。

        Excel 台紙の行高・列幅ではなく、``data/label_stock.json`` の
        寸法定義から mm 絶対配置で組む。バーコードはフォントに依存せず描く。
        """
        from ..services import label_sheet as LS

        stock = self._stock()
        cal = self._calibration()

        blocks: List[dict] = []
        total = 0
        stale = False
        for ob in ob_list:
            v = self.wf.label_view(ob)
            if not v or not v["labels"]:
                continue
            stale = stale or bool(v.get("stale"))
            labs = [{
                "kensaNo": l["kensaNo"], "coilNo": l["coilNo"],
                "kataban": l["kataban"], "weight": l["weight"],
                "barcodeKataban": l["barcodeKataban"],
                "barcodeKensa": l["barcodeKensa"],
            } for l in v["labels"]]
            total += len(labs)
            blocks.append({"header": v.get("printHeader") or {},
                           "labels": labs})

        if not blocks:
            body = ('<div class="card"><h2>ラベル印刷</h2>'
                    '<div class="empty">印刷するラベルがありません。'
                    'メイン画面で「重量反映」を実行してください。</div></div>')
            return self._shell("ラベル印刷", body, "labels")

        # 本数・高さ・NW・GW は「計算」でしか書き換わらない（VBA と同じ）。
        # 検番・重量だけ「重量反映」で変わるため、入力を変えて反映だけして
        # 印刷すると、**新しい検番と古い NW/GW が混ざったラベル**が出る。
        # 値は VBA どおりのままにし、刷る前に気付けるようにする。
        # 食い違っている間は **印刷させない**（2026.09 決定）。
        # 出荷ラベルなので、気付かずに刷れてしまう余地を残さない。
        stale_note = ""
        if stale:
            stale_note = (
                '<div class="card no-print stalebox">'
                '<b>入力が変わっています。印刷を止めています。</b><br>'
                'ヘッダー帯の <b>本数・高さ・NW・GW</b> は「計算」を押したときの'
                'ものです。検番・重量だけが新しくなっているため、'
                'このまま刷ると<b>別々のコイルの値が混ざったラベル</b>になります。'
                '<b>メイン画面で「計算」を実行してください。</b>'
                '<div class="btn-row">'
                f'<a class="btn primary" href="{self.base}/">メイン画面へ戻る</a>'
                '</div></div>'
                # ボタンを消すだけでは Ctrl+P / メニューから刷れてしまう。
                # 印刷そのものを空にして、理由を紙に出す。
                '<style>@media print{'
                '.sheet{display:none !important}'
                '.stopprint{display:block !important}'
                '}</style>'
                '<div class="stopprint">'
                '入力が変わっているため、このラベルは印刷できません。<br>'
                'メイン画面で「計算」を実行してから印刷してください。'
                '</div>')

        font_css = self._barcode_font_css()
        pages = LS.render_blocks(
            stock, blocks, cal, show_guides=guides,
            barcode_mode=self.cfg.barcode_mode,
            barcode_family=self.cfg.barcode_font_family,
            barcode_font_path=self._barcode_font_file())
        note = ('<div class="card no-print preview-note">'
                '<h2>ラベル印刷（実寸）</h2>'
                f'<p class="hint">台紙: <b>{esc(stock.name)}</b>　'
                f'ラベル {stock.label_w:.2f}×{stock.label_h:.2f}mm　'
                f'ピッチ {stock.pitch_x:.2f}×{stock.pitch_y:.2f}mm　'
                f'{total} 枚 / 1シート {stock.per_page} 枚（ヘッダー帯 {stock.header_rows} 段 + ラベル {stock.label_rows} 段）</p>'
                f'<p class="hint">位置: {_describe_correction(cal)}</p>'
                '<p class="hint"><b>印刷画面は何も変えずに、そのまま「印刷」で'
                '大丈夫です</b>（倍率「実際のサイズ」・余白「なし」が最初から選ばれています）。<br>'
                '刷ったものがラベルからずれるときは'
                f'<a href="{self.base}/labels/calibration">位置合わせ</a>で一度だけ直してください'
                '（「左へ 2.5mm ずれている」のように入れるだけ。以後ずっと効きます）。</p>'
                '<div class="btn-row">'
                + ('<button class="btn" disabled title="入力が変わっています。'
                   'メイン画面で「計算」を実行してください">印刷（停止中）</button>'
                   if stale else
                   '<button class="btn" onclick="window.print()">印刷</button>')
                + f'<a class="btn" href="{self.base}/labels/print?ob={",".join(map(str, ob_list))}'
                f'&guides=1">枠線を表示して確認</a>'
                f'<a class="btn" href="{self.base}/labels/calibration">位置合わせ（試し刷り）</a>'
                '</div></div>')
        return self._shell("ラベル印刷",
                           stale_note + note + font_css + pages, "labels")

    def label_calibration(self) -> str:
        """試し刷りで位置を合わせる画面。

        現場に計算をさせない。「どっちへ何 mm ずれているか」をそのまま
        入れてもらい、補正はこちらで足し引きして保存する。
        一度合わせれば以後の印刷はすべてその位置になる（毎回は要らない）。
        """
        from ..services import label_sheet as LS
        from ..services import label_align as LA

        stock = self._stock()
        cal = self._calibration()
        sheet = LS.render_ruler_page(stock, cal)

        # ---------------- いまの状態を言葉で ----------------
        state = _describe_correction(cal)

        intro = (
            '<div class="card no-print"><h2>ラベル位置合わせ</h2>'
            f'<p class="calstate" id="calNow">{state}</p>'
            '<p class="hint">一度合わせれば、<b>以後の印刷はずっとこの位置</b>で刷られます'
            '（毎回やる必要はありません）。値はこの端末に保存されます。'
            f'全ラインへ同じ値を配るときは <a href="{self.base}/settings">設定 → 配布設定</a>。</p>'
            '<ol class="calsteps">'
            '<li><b>試し刷り</b>を押して 1 枚刷る。'
            '<span class="hint">普通紙に刷って台紙と重ね、明るい所で透かして見ます'
            '（台紙に直接刷ってもかまいません）。'
            '印刷画面は<b>何も変えずに</b>そのまま「印刷」。</span></li>'
            '<li>点線の枠が、ラベルの切れ目から<b>どっちへ何 mm</b> ずれているか、'
            '定規で測る。</li>'
            '<li>下の「ずれを直す」に入れて<b>「直す」</b>を押す。</li>'
            '<li>もう一度刷って、合っていれば終わり。</li>'
            '</ol>'
            '<div class="btn-row">'
            '<button class="btn btn-primary" onclick="window.print()">試し刷り</button>'
            f'<a class="btn" href="{self.base}/labels">ラベル台紙へ戻る</a>'
            '</div></div>')

        # ---------------- ずれを直す ----------------
        fix = (
            '<div class="card no-print"><h2>ずれを直す</h2>'
            '<div class="gaprow">印刷が'
            '<select id="calDirX"><option value="left">左</option>'
            '<option value="right">右</option></select>へ'
            '<input type="text" id="calGapX" inputmode="decimal" placeholder="0">'
            'mm ずれている</div>'
            '<div class="gaprow">印刷が'
            '<select id="calDirY"><option value="up">上</option>'
            '<option value="down">下</option></select>へ'
            '<input type="text" id="calGapY" inputmode="decimal" placeholder="0">'
            'mm ずれている</div>'
            '<p class="hint">ずれていない向きは 0 か空欄のままで構いません。'
            '入れた量だけ<b>逆向きへ</b>動かして刷るようにします。</p>'
            '<div class="btn-row">'
            '<button type="button" class="btn btn-primary" id="calGap">直す</button>'
            '<button type="button" class="btn btn-danger" id="calReset">'
            '最初（ずらさない）に戻す</button>'
            '</div>'
            '<div id="calMsg" class="hint" style="margin-top:10px"></div>'

            '<h3 class="calsub">少しずつ動かす</h3>'
            '<p class="hint">押すたびに、<b>刷られる位置</b>がその向きへ動きます。</p>'
            '<div class="btn-row" style="align-items:center">'
            '<label for="calStep">1 回に動かす量</label>'
            '<select id="calStep">'
            '<option value="0.1">0.1mm</option>'
            '<option value="0.5" selected>0.5mm</option>'
            '<option value="1">1.0mm</option>'
            '</select></div>'
            '<div class="nudgepad">'
            '<button type="button" class="btn" data-nx="0" data-ny="-1">↑ 上へ</button>'
            '<button type="button" class="btn" data-nx="-1" data-ny="0">← 左へ</button>'
            '<button type="button" class="btn" data-nx="1" data-ny="0">右へ →</button>'
            '<button type="button" class="btn" data-nx="0" data-ny="1">↓ 下へ</button>'
            '</div>'
            '</div>')

        # ---------------- 大きさが合わないとき（めったに使わない） ----------------
        measure = (
            '<details class="card no-print caldetails">'
            '<summary>大きさそのものが合わないとき（下の段ほどずれが大きくなる）</summary>'
            '<p class="hint">上の段は合うのに<b>下の段へ行くほどずれていく</b>ときは、'
            '位置ではなく大きさ（倍率）がずれています。'
            '試し刷りの<b>青い線</b>と<b>赤い十字</b>を定規で測って入れてください。'
            '計算はこちらでやります。空欄の欄は触りません。</p>'
            '<div class="setgrid">'
            f'<div class="setrow"><label for="calSpanX">'
            f'横の青い線（{LA.SPAN_MM:.0f}mm のはず）を測ると</label>'
            '<input type="text" id="calSpanX" inputmode="decimal" '
            'placeholder="例 99.2"></div>'
            f'<div class="setrow"><label for="calSpanY">'
            f'縦の青い線（{LA.SPAN_MM:.0f}mm のはず）を測ると</label>'
            '<input type="text" id="calSpanY" inputmode="decimal" '
            'placeholder="例 99.2"></div>'
            f'<div class="setrow"><label for="calCrossX">'
            f'紙の左端から赤い十字まで（{LA.CROSS_X_MM:.0f}mm のはず）</label>'
            '<input type="text" id="calCrossX" inputmode="decimal" '
            'placeholder="例 22.5"></div>'
            f'<div class="setrow"><label for="calCrossY">'
            f'紙の上端から赤い十字まで（{LA.CROSS_Y_MM:.0f}mm のはず）</label>'
            '<input type="text" id="calCrossY" inputmode="decimal" '
            'placeholder="例 21.5"></div>'
            '</div>'
            '<div class="btn-row">'
            '<button type="button" class="btn btn-primary" id="calApply">'
            'この長さで合わせる</button>'
            '<button type="button" class="btn" data-ns="-0.5">大きさ −0.5%</button>'
            '<button type="button" class="btn" data-ns="0.5">大きさ +0.5%</button>'
            '</div>'
            '</details>')

        # ---------------- 印刷画面について ----------------
        dialog = (
            '<details class="card no-print caldetails">'
            '<summary>印刷画面（プリンターを選ぶ画面）について</summary>'
            '<p class="hint"><b>何も変えずに、そのまま「印刷」で大丈夫です。</b>'
            '倍率は<b>「実際のサイズ」</b>、余白は<b>「なし」</b>が最初から選ばれています'
            '（このアプリの印刷ページが余白 0mm・A4 ちょうどの大きさを指定しているため）。</p>'
            '<ul class="hint" style="line-height:1.9;padding-left:20px">'
            '<li>倍率を「ページに合わせる」などへ<b>変えてしまった</b>ときだけ、'
            '「実際のサイズ」に戻してください。</li>'
            '<li>プリンター側の設定で「用紙に合わせて拡大／縮小」が'
            '入っていると縮みます。その場合はプリンターの設定で切ってください'
            '（一度切れば残ります）。</li>'
            '<li>プリンターは紙の縁 約 4mm には刷れません。'
            'このため、画面では収まって見えても刷ると欠けることがあります。'
            'ラベルの文字・罫線・バーは、紙の端から 5mm 以上内側に置いてあります。</li>'
            '</ul>'
            '</details>')

        m = stock.measured or {}
        info = (
            '<details class="card no-print caldetails">'
            '<summary>台紙の寸法</summary>'
            f'<p class="hint">台紙: <b>{esc(stock.name)}</b>'
            f'{("　" + esc(stock.note)) if stock.note else ""}<br>'
            f'ラベル {stock.label_w:.2f}×{stock.label_h:.2f}mm　'
            f'ピッチ {stock.pitch_x:.2f}×{stock.pitch_y:.2f}mm　'
            f'原点 ({stock.origin_x:.2f}, {stock.origin_y:.2f})　'
            f'1ページ {stock.per_page} 枚<br>'
            f'補正の数値: X {cal.offset_x_mm:+.2f}mm / Y {cal.offset_y_mm:+.2f}mm / '
            f'倍率 {cal.scale_pct:.2f}%</p>'
            + (f'<p class="hint">Excel 台紙から採寸した行間隔は '
               f'{min(m["pitchYSamplesMm"]):.2f}〜{max(m["pitchYSamplesMm"]):.2f}mm と'
               'ばらついていました（手調整の累積）。ここでは等ピッチで組んでいます。</p>'
               if m.get("pitchYSamplesMm") else "")
            + '</details>')

        body = intro + fix + measure + dialog + info + sheet
        return self._shell("ラベル位置合わせ", body, "labels",
                           self._calibration_script())

    def _calibration_script(self) -> str:
        """位置合わせ画面のスクリプト。"""
        return """<script>
(function(){
  "use strict";
  function el(id){ return document.getElementById(id); }
  function msg(text, cls){
    var e = el("calMsg");
    if (e) { e.className = cls || "hint"; e.textContent = text || ""; }
  }
  function show(j){
    var now = el("calNow");
    if (now && j.stateText) { now.innerHTML = j.stateText; }
    var text = j.plainMessage || j.message || "";
    (j.notes || []).forEach(function(n){ text += "\\n" + n; });
    msg(text + "\\nもう一度「試し刷り」で確かめてください。", "okbox");
    pplToast(j.plainMessage || j.message, "info");
    // 試し刷りは補正込みで描いているので、見た目を合わせるため読み直す
    setTimeout(function(){ location.reload(); }, 1800);
  }
  function send(payload){
    msg("直しています…", "hint");
    return pplPost("/api/label/align", payload).then(function(j){
      if (!j || !j.ok) {
        msg((j && j.message) || "うまくいきませんでした", "errbox");
        if (j && j.message) { pplToast(j.message, "warn"); }
        return;
      }
      show(j);
    }).catch(function(e){ msg(String(e), "errbox"); });
  }
  function num(id){
    var t = String(el(id).value || "").trim()
      .replace(/[０-９．]/g, function(c){
        return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); });
    if (t === "") { return 0; }
    var v = parseFloat(t);
    return isNaN(v) ? null : v;
  }
  var gap = el("calGap");
  if (gap) {
    gap.addEventListener("click", function(){
      var gx = num("calGapX"), gy = num("calGapY");
      if (gx === null || gy === null) { msg("数字で入れてください。", "errbox"); return; }
      if (!gx && !gy) { msg("ずれている量を入れてください。", "warnbox"); return; }
      // 印刷が左へずれている → 右へ動かす（補正 X を +）
      var dx = (el("calDirX").value === "left") ? gx : -gx;
      // 印刷が上へずれている → 下へ動かす（補正 Y を +）
      var dy = (el("calDirY").value === "up") ? gy : -gy;
      send({mode:"nudge", dx: dx, dy: dy});
    });
  }
  function step(){
    var s = el("calStep");
    return s ? parseFloat(s.value) : 0.5;
  }
  document.querySelectorAll("[data-nx]").forEach(function(b){
    b.addEventListener("click", function(){
      var k = step();
      send({mode:"nudge", dx: parseFloat(b.dataset.nx) * k,
            dy: parseFloat(b.dataset.ny) * k});
    });
  });
  document.querySelectorAll("[data-ns]").forEach(function(b){
    b.addEventListener("click", function(){
      send({mode:"nudge", dscale: parseFloat(b.dataset.ns)});
    });
  });
  var reset = el("calReset");
  if (reset) {
    reset.addEventListener("click", function(){
      if (!window.confirm("ずらしをやめて、最初の位置・大きさで刷るように戻します。よろしいですか？")) {
        return;
      }
      send({mode:"reset"});
    });
  }
  var apply = el("calApply");
  if (apply) {
    apply.addEventListener("click", function(){
      send({mode:"measure",
            spanX: el("calSpanX").value, spanY: el("calSpanY").value,
            crossX: el("calCrossX").value, crossY: el("calCrossY").value});
    });
  }
})();
</script>"""

    # ============================================================ 全サイズ
    def all_size(self, query=None) -> str:
        """VBA ``テスラ全サイズ`` UserForm。"""
        svc = self.wf.all_size
        st = svc.load_state()
        combo = st.get("comboText", "")
        three = bool(st.get("threeDigit"))

        opts = ['<option value="">（選択してください）</option>']
        allow = self._visible_dims()
        for r in svc.master.rows:
            # 現状来ないサイズは出さない（指定サイズ画面と同じ絞り込み）。
            # 型番マスタの名前は "1.0×53.5×Coil" で表記が違うため、
            # 文字列ではなく **寸法の数値** で突き合わせる。
            if allow is not None and _dims_of(r["name"]) not in allow:
                if r["name"] != combo:          # 選択済みのものは残す
                    continue
            sel = " selected" if r["name"] == combo else ""
            note = ("　" + r["note"]) if r["note"] else ""
            opts.append(f'<option value="{esc(r["name"])}"{sel}>'
                        f'{esc(r["name"])}（{esc(r["kataban"])}）{esc(note)}</option>')

        sheets_html = ""
        if combo:
            from ..services.all_size import sheet_names
            n1, n2 = sheet_names(combo)
            cards = []
            for name in (n1, n2):
                data = svc.get_sheet(name)
                if not data:
                    cards.append(f'<div class="block"><h3>{esc(name)}</h3>'
                                 f'<div class="empty">未作成</div></div>')
                    continue
                cells = data.get("cells", {})

                def cell(r, c):
                    return cells.get("%d,%d" % (r, c), "")

                from ..services.all_size import (ROW_H1, ROW_H2, COL_KEN,
                                                 COL_SIZE, COL_WT,
                                                 SUB_ROWS_BLOCK1,
                                                 SUB_ROWS_BLOCK2,
                                                 SUB_COL_L, SUB_COL_R)
                subs = []
                for rows, blk in ((SUB_ROWS_BLOCK1, "上段"), (SUB_ROWS_BLOCK2, "下段")):
                    left = [cell(r, SUB_COL_L) for r in rows]
                    right = [cell(r, SUB_COL_R) for r in rows]
                    subs.append(
                        f'<tr><th>{esc(blk)}</th>'
                        f'<td class="mono">{esc(" ".join(left))}</td>'
                        f'<td class="mono">{esc(" ".join(right))}</td></tr>')
                cards.append(
                    f'<div class="block"><h3>{esc(name)}'
                    f'　<span style="font-weight:400">印刷範囲 '
                    f'{esc(data.get("printArea") or "（なし）")}</span></h3>'
                    f'<table><tbody>'
                    f'<tr><th>検査番号</th><td colspan="2">{esc(cell(ROW_H1, COL_KEN))}'
                    f' / {esc(cell(ROW_H2, COL_KEN))}</td></tr>'
                    f'<tr><th>サイズ</th><td colspan="2">{esc(cell(ROW_H1, COL_SIZE))}'
                    f' / {esc(cell(ROW_H2, COL_SIZE))}</td></tr>'
                    f'<tr><th>重量</th><td colspan="2">{esc(cell(ROW_H1, COL_WT))}'
                    f' / {esc(cell(ROW_H2, COL_WT))}</td></tr>'
                    f'<tr><th>型番</th><td colspan="2">{esc(cell(4, 4))}</td></tr>'
                    f'<tr><th></th><th>左面 副番</th><th>右面 副番</th></tr>'
                    f'{"".join(subs)}'
                    f'</tbody></table></div>')
            sheets_html = ('<div class="report-head"><div class="ttl">反映値</div>'
                           f'<div class="report-meta">{esc(st.get("katabanLabel",""))}'
                           f'　{esc(st.get("countLabel",""))}</div></div>'
                           '<div class="blocks">' + "".join(cards) + '</div>')

        w45_style = "" if three else ' style="opacity:.4"'
        body = f'''
<div class="card">
  <h2>テスラ全サイズ</h2>
  <div class="inputs-3">
    <label>サイズ選択（型番マスタ）
      <select id="asCombo">{"".join(opts)}</select>
    </label>
    <label>検番
      <input type="text" id="asKen" value="{esc(st.get("kensaNo",""))}"
             maxlength="7" data-filter="alnum" placeholder="例: W111111">
    </label>
    <div>
      <span class="cap">コイル副番</span>
      <div style="display:flex;gap:14px;padding-top:4px">
        <label style="display:flex;gap:4px;align-items:center;color:var(--ink)">
          <input type="radio" name="asDigit" value="2" {"" if three else "checked"}>2ケタ</label>
        <label style="display:flex;gap:4px;align-items:center;color:var(--ink)">
          <input type="radio" name="asDigit" value="3" {"checked" if three else ""}>3ケタ</label>
      </div>
    </div>
  </div>
  <p class="hint">例：1-1→2ケタ　例：2-2-1→3ケタ</p>

  <div class="inputs-3" style="margin-top:8px">
    <label>重量 {"丈1-1-?" if three else "丈1"}
      <input type="text" id="asW1" value="{esc(st.get("w1",""))}" data-filter="num"></label>
    <label>重量 {"丈2-1-?" if three else "丈2"}
      <input type="text" id="asW2" value="{esc(st.get("w2",""))}" data-filter="num"></label>
    <div></div>
  </div>
  <div class="inputs-3" id="asW45"{w45_style}>
    <label>重量 1-2-?
      <input type="text" id="asW4" value="{esc(st.get("w4",""))}" data-filter="num"></label>
    <label>重量 2-2-?
      <input type="text" id="asW5" value="{esc(st.get("w5",""))}" data-filter="num"></label>
    <div></div>
  </div>

  <div class="btn-row" style="margin-top:10px">
    <button type="button" class="btn btn-primary" id="asSubmit">決定</button>
    <button type="button" class="btn" id="asPrint">印刷</button>
    <button type="button" class="btn btn-danger" id="asClear">クリア</button>
  </div>
</div>
{sheets_html}
'''
        script = """<script>
(function(){
  function v(id){var e=document.getElementById(id);return e?e.value:"";}
  function digit(){var r=document.querySelector("input[name=asDigit]:checked");
    return r? r.value==="3" : null;}
  function payload(){return {comboText:v("asCombo"),kensaNo:v("asKen"),
    threeDigit:digit(),w1:v("asW1"),w2:v("asW2"),w4:v("asW4"),w5:v("asW5")};}
  document.addEventListener("change",function(e){
    if(e.target.name==="asDigit"){
      document.getElementById("asW45").style.opacity = e.target.value==="3" ? "" : ".4";
    }
    if(e.target.id==="asCombo"){
      pplPost("/api/all-size/prepare",{comboText:v("asCombo")}).then(function(j){
        pplToast(j.message,j.ok?"info":"warn"); location.reload();});
    }
  });
  document.getElementById("asSubmit").addEventListener("click",function(){
    pplPost("/api/all-size/submit",payload()).then(function(j){
      pplToast(j.message,j.ok?"info":(j.level||"warn"));
      if(j.ok){location.reload();}});
  });
  document.getElementById("asPrint").addEventListener("click",function(){
    pplPost("/api/all-size/print-targets",{comboText:v("asCombo")}).then(function(j){
      if(!j.ok){pplToast(j.message,"warn");return;}
      if(window.confirm(j.message)){
        window.open(pplUrl("/all-size/print?combo="+encodeURIComponent(v("asCombo"))),
                    "_blank","noopener");}});
  });
  document.getElementById("asClear").addEventListener("click",function(){
    if(!window.confirm("この台紙の反映内容を消します。よろしいですか？")){return;}
    pplPost("/api/all-size/prepare",{comboText:v("asCombo")}).then(function(j){
      pplToast("クリアしました","info"); location.reload();});
  });
})();
</script>"""
        return self._shell("全サイズ", body, "all", script)

    def all_size_print(self, combo: str) -> str:
        """全サイズ台紙の印刷ビュー。"""
        from ..services.all_size import sheet_names
        svc = self.wf.all_size
        if not combo:
            return self._shell("全サイズ印刷",
                               '<div class="empty">サイズが指定されていません。</div>', "all")
        n1, n2 = sheet_names(combo)
        blocks = []
        for name in (n1, n2):
            data = svc.get_sheet(name)
            if not data or not (data.get("weightTop") or data.get("weightBottom")):
                continue
            cells = data.get("cells", {})
            labels = []
            from ..services.all_size import (SUB_ROWS_BLOCK1, SUB_ROWS_BLOCK2,
                                             SUB_COL_L, SUB_COL_R)
            for rows, wt, size in ((SUB_ROWS_BLOCK1, data.get("weightTop", ""),
                                    data.get("sizeTop", "")),
                                   (SUB_ROWS_BLOCK2, data.get("weightBottom", ""),
                                    data.get("sizeBottom", ""))):
                if wt == "":
                    continue
                for r in rows:
                    for col in (SUB_COL_L, SUB_COL_R):
                        sub = cells.get("%d,%d" % (r, col), "")
                        if not sub:
                            continue
                        ken = data.get("kensaNo", "")
                        labels.append(
                            f'<div class="lbl">'
                            f'<div class="bc">*{esc(data.get("kataban",""))}*</div>'
                            f'<div class="row"><span class="k">P.N.</span>'
                            f'<span class="v">SEALING PLATE</span></div>'
                            f'<div class="row"><span class="k">L.C.</span>'
                            f'<span class="v">{esc(ken)}</span>'
                            f'<span class="v" style="flex:0 0 auto">{esc(sub)}</span></div>'
                            f'<div class="row"><span class="k">P</span>'
                            f'<span class="v">{esc(data.get("kataban",""))}</span></div>'
                            f'<div class="row"><span class="k">Q</span>'
                            f'<span class="v">{esc(wt)}</span>'
                            f'<span class="v" style="flex:0 0 auto">kg</span></div>'
                            f'<div class="bc">*{esc(ken)}{esc(sub)} {esc(wt)}*</div>'
                            f'<div class="cellref">{esc(size)}</div></div>')
            blocks.append(f'<div class="card"><h2>{esc(name)}（印刷範囲 '
                          f'{esc(data.get("printArea") or "なし")}）</h2>'
                          f'<div class="labelsheet">{"".join(labels)}</div></div>')
        if not blocks:
            return self._shell("全サイズ印刷",
                               '<div class="empty">重量インプットがありません。</div>', "all")
        script = ("<script>window.addEventListener('load',"
                  "function(){window.print();});</script>")
        return self._shell("全サイズ印刷", "".join(blocks), "all", script)

    # ============================================================ 設定
    def settings(self) -> str:
        """ファイルパスなどの設定画面。

        VBA では参照先がコード内の定数に埋まっていて、現場ごとに変えるには
        VBE を開く必要があった。ここから変更できるようにする。
        """
        from ..services import settings as S

        items = S.describe(self.cfg)
        by_group = {}
        for it in items:
            by_group.setdefault(it["group"], []).append(it)

        blocks = []
        for group in S.GROUP_ORDER:
            rows = by_group.get(group)
            if not rows:
                continue
            fields = []
            for it in rows:
                fields.append(self._setting_field(it))
            blocks.append(
                f'<div class="card"><h2>{esc(group)}</h2>'
                f'<div class="setgrid">{"".join(fields)}</div></div>')

        if S.integrated(self.cfg):
            # 統合版: 統合アプリが決める項目は、ここでは扱わないことを知らせる
            owned = "".join(f'<li>{esc(why)}</li>' for why in S.INTEGRATED_OWNED.values())
            blocks.append(f'<div class="card"><h2>コイル梱包ツールで決まる項目</h2>'
                          f'<ul class="hint">{owned}</ul></div>')

        local_path = self.cfg.local_config_path
        app_path = getattr(self.cfg, "app_config_used", self.cfg.app_config_path)
        has_local = os.path.exists(local_path)
        from ..services import distribution as D
        dist_path = str(D.settings_path())

        head = (
            '<div class="card"><h2>設定の保存先</h2>'
            '<table class="tbl"><thead><tr><th>層</th><th>ファイル</th>'
            '<th>役割</th></tr></thead><tbody>'
            f'<tr><td>同梱の既定</td><td class="mono">{esc(app_path)}</td>'
            '<td>全員に配る既定値。管理者がファイルを直接編集する</td></tr>'
            f'<tr><td>配布設定</td><td class="mono">{esc(dist_path)}</td>'
            '<td>「配布設定」の面で書き出す。起動したとき、'
            'この端末に<b>無い項目だけ</b>読み込む</td></tr>'
            f'<tr><td><b>この端末</b></td><td class="mono">{esc(local_path)}</td>'
            f'<td><b>この画面が書き込む先。</b>同梱の既定より優先される'
            f'{"" if has_local else "（まだありません）"}</td></tr>'
            '</tbody></table>'
            '<p class="hint">アプリ本体を共有フォルダーへ置いている場合でも、'
            'この画面の保存はローカル領域だけに書き込むため、'
            '他の利用者へ影響しません。</p></div>')

        tools_inner = (
            '<div class="btn-row">'
            '<button type="button" class="btn btn-primary" id="setSave">保存する</button>'
            '<button type="button" class="btn" id="setTest">資材マスタへの接続を試す</button>'
            '<button type="button" class="btn btn-danger" id="setReset">'
            'この端末の設定を消して同梱の既定へ戻す</button>'
            '</div>'
            '<div id="setResult" class="hint" style="margin-top:10px"></div>')

        # 設定の中もページを分ける。
        # 「パス設定」＝参照先の書き換え（合言葉が要る）
        # 「マスタ管理」＝資材マスタの中身を直す
        # 合言葉は **サブタブより上**、どちらの面でも見える場所に置く。
        #
        # パス設定の面の中に入れると、マスタ管理の面を開いている間は
        # 隠れてしまい、**マスタを直すときに合言葉を入れられない**。
        # 一番上に固定して、どちらの操作でもそのまま使えるようにする。
        pw = (
            '<div class="card"><h2>合言葉</h2>'
            '<div class="pwrow">'
            '<label for="setPassword">合言葉</label>'
            '<input type="password" id="setPassword" autocomplete="off" '
            'placeholder="パスの変更・マスタの書き換えに必要">'
            '<button type="button" class="btn" id="setPwCheck">確認する</button>'
            '<span id="setPwState" class="pwstate">未確認</span>'
            '</div>'
            '<p class="hint">参照先を変えたりマスタを直したりすると'
            '<b>全員の計算・印刷に影響します</b>。'
            'うっかり防止のため、そのときだけ確認します。<br>'
            '合言葉は<b>入れておくだけ</b>で、'
            'このあと押す「保存する」「書き込む」のときに一緒に送られます。'
            '（合っているかだけ先に見たいときは「確認する」）<br>'
            '未入力のまま実行すると、その場で聞きます。</p>'
            '</div>')

        # 操作ボタンはパス設定の面の**先頭**に置く。
        # 設定カードが 9 枚あるので、下に置くと画面外になってしまう。
        ops = '<div class="card"><h2>パス設定の操作</h2>' + tools_inner + '</div>'
        paths = ops + head + "".join(blocks)
        master = self._master_admin_body()

        tabs = (
            '<div class="subnav" role="tablist">'
            '<button type="button" class="subtab on" data-sub="paths">パス設定</button>'
            '<button type="button" class="subtab" data-sub="master">マスタ管理</button>'
            '<button type="button" class="subtab" data-sub="dist">配布設定</button>'
            '</div>')
        body = (pw + tabs
                + f'<div class="subpane" data-sub="paths">{paths}</div>'
                + f'<div class="subpane" data-sub="master" hidden>{master}</div>'
                + f'<div class="subpane" data-sub="dist" hidden>'
                  f'{self._distribution_body()}</div>')
        return self._shell("設定", body, "settings", self._settings_script())

    def _distribution_body(self) -> str:
        """配布設定の面（python-web-tools の「配布設定」と同じ作り）。

        1 台で決めた設定を書き出し、アプリのフォルダーごと配った先で
        そのまま使う。書き出し・読み込み直し・消すは合言葉が要る。
        """
        from ..services import distribution as D
        dist = D.summary(self.cfg)
        local = set(dist["localKeys"])

        rows = "".join(
            f'<tr><td>{esc(c["label"])}</td><td class="mono">{esc(c["value"])}</td></tr>'
            for c in dist["contents"])
        meta = (f'{esc(dist["createdAt"])} に {esc(dist["createdOn"])} で作成'
                if dist["exists"] else
                'まだありません。下で書き出すと、アプリのフォルダーの'
                '「配布設定\\packing_pena_label」にできます。')
        applied = (f'この端末が最後に読み込んだ／書き出したとき: '
                   f'<b>{esc(dist["appliedAt"])}</b>' if dist["appliedAt"] else
                   'この端末はまだ配布設定を読み込んでいません。')

        now_card = (
            '<div class="card"><h2>いま置いてある配布設定 '
            f'<span id="distState" class="pill {"ok" if dist["exists"] else "warn"}">'
            f'{"あり" if dist["exists"] else "なし"}</span></h2>'
            f'<p class="hint" id="distMeta">{meta}</p>'
            '<table class="tbl" style="max-width:820px"><thead><tr>'
            '<th>項目</th><th>値</th></tr></thead>'
            f'<tbody id="distRows">{rows}</tbody></table>'
            f'<p class="hint">置き場所: <span class="mono" id="distPath">'
            f'{esc(dist["path"])}</span><br>'
            '<b>配布先に関わるものはすべてこのフォルダー</b>に入ります'
            '（設定.json・はじめに読む.txt）。</p>'
            f'<p class="hint" id="distApplied">{applied}</p>'
            '</div>')

        groups = []
        for g in dist["groups"]:
            boxes = []
            for it in g["items"]:
                has = it["key"] in local
                note = ("" if has else
                        '<span class="distnote">（この端末では既定のまま）</span>')
                why = (f'<span class="distnote">{esc(it["why"])}</span>'
                       if it["why"] else "")
                cls = ' class="has"' if has else ""
                boxes.append(
                    f'<label{cls}>'
                    '<input type="checkbox" '
                    f'data-dist-item="{esc(it["key"])}"'
                    f'{" checked" if it["default"] else ""}> '
                    f'{esc(it["label"])}{note}{why}</label>')
            groups.append(
                f'<fieldset class="distchecks"><legend>{esc(g["group"])}</legend>'
                + "".join(boxes) + '</fieldset>')

        export_card = (
            '<div class="card"><h2>この端末の設定を配布設定にする</h2>'
            '<p class="hint">いまこの端末に入っている値をそのまま書き出します'
            '（前の中身は置き換えます）。配った先は<b>起動したときに読み込みます</b>。'
            'ただし<b>その端末にすでにある設定は読み込みません</b>（上書きしない）。<br>'
            '<b>印刷位置の補正</b>（ラベル台紙 → 位置合わせ で決めた値）も'
            'ここで一緒に配れます。</p>'
            '<div class="btn-row">'
            '<button type="button" class="btn" id="distAll">すべて選ぶ</button>'
            '<button type="button" class="btn" id="distNone">すべて外す</button>'
            '</div>'
            + "".join(groups) +
            '<div class="btn-row" style="margin-top:10px">'
            '<button type="button" class="btn btn-primary" id="distExport">'
            '配布設定を書き出す</button>'
            '<button type="button" class="btn" id="distReapply">'
            '配布設定を読み込み直す</button>'
            '<button type="button" class="btn btn-danger" id="distRemove">'
            '配布設定を消す</button>'
            '</div>'
            '<div id="distResult" class="hint" style="margin-top:10px"></div>'
            '<p class="hint">合言葉は画面の一番上の欄を使います'
            '（空のまま押すと、その場で聞きます）。<br>'
            '<b>書き出されるのは太字の項目</b>（この端末で値を入れたもの）だけです。'
            '「この端末では既定のまま」の項目は、配った先も同じ既定で動くので入れません。<br>'
            '「読み込み直す」は、その端末にすでにある設定も上書きします。</p>'
            '</div>')
        build_card = (
            '<div class="card"><h2>配布用フォルダを作る</h2>'
            '<p class="hint">配る<b>ものだけ</b>を新しいフォルダへ写します'
            '（python-web-tools の make_dist と同じ）。'
            'テスト・開発用の道具・資材マスタの写し（*.sqlite3）・ログ・起動中の印は入りません。'
            '上で書き出した<b>配布設定も一緒に入れられます</b>。<br>'
            'できたフォルダには、版と入れた配布設定、配った先ですることを書いた'
            '<b>配布メモ.txt</b> が入ります。</p>'
            '<div class="setgrid"><div class="setrow">'
            '<label for="distOut">作る場所（フルパス）</label>'
            f'<input type="text" id="distOut" placeholder="{esc(dist["defaultOut"])}">'
            '</div></div>'
            '<div class="distopts">'
            '<label><input type="checkbox" id="distWithSettings" checked> '
            '配布設定を入れる</label>'
            '<label><input type="checkbox" id="distZip"> zip も作る</label>'
            '<label><input type="checkbox" id="distForce"> '
            '前に作った配布用フォルダがあれば消して作り直す</label>'
            '</div>'
            '<div class="btn-row" style="margin-top:8px">'
            '<button type="button" class="btn btn-primary" id="distBuild">'
            '配布用フォルダを作る</button></div>'
            '<div id="distBuildMsg" class="hint" style="margin-top:10px"></div>'
            '<pre id="distBuildResult" class="distlog" hidden></pre>'
            '<p class="hint">空欄なら、アプリの隣に '
            f'<span class="mono">{esc(dist["defaultOut"])}</span> を作ります。'
            'コマンドからは <span class="mono">scripts\\make_dist.bat</span>。<br>'
            '作り直しで消すのは、前にこの仕組みで作ったフォルダ（配布メモ.txt があるもの）だけです。</p>'
            '</div>')
        return now_card + export_card + build_card

    def _master_admin_body(self) -> str:
        """マスタ管理の面。

        中身は画面が開かれてから取りに行く（設定画面を開くたびに
        共有マスタを読みに行かないため）。
        """
        from ..services import master_admin as MA
        repo = self.wf.materials
        why = MA.editable_why_not(repo)
        src = MA.source_path(repo) or "（未設定）"

        head = (
            '<div class="card"><h2>書き先</h2>'
            f'<p class="hint">直すのは<b>共有の資材マスタ</b>です:'
            f' <span class="mono">{esc(src)}</span><br>'
            'このツールは手元に写しを持たないので、書いた内容は'
            '<b>次の計算からそのまま使われます</b>。'
            '書く前に自動で控え（<span class="mono">.bak</span>）を取ります。</p>'
            + (f'<div class="warnbox">{esc(why)}</div>' if why else "")
            + '</div>')

        body = (
            '<div class="card"><h2>マスタ管理</h2>'
            '<div class="masterbar">'
            '<select id="mTable"></select>'
            '<input type="text" id="mKeyword" placeholder="絞り込み（どの列でも）">'
            '<button type="button" class="btn" id="mFind">探す</button>'
            '<button type="button" class="btn" id="mNew">行を足す</button>'
            '</div>'
            '<p class="hint" id="mNote"></p>'
            '<div class="scroll"><table class="tbl" id="mTbl">'
            '<thead><tr></tr></thead><tbody></tbody></table></div>'
            '</div>'

            '<div class="card dismissable" id="mEdit" hidden>'
            '<h2><span id="mEditTitle">行を直す</span>'
            '<button type="button" class="x" id="mClose" title="閉じる">×</button></h2>'
            '<div class="setgrid" id="mFields"></div>'
            '<div class="btn-row">'
            '<button type="button" class="btn btn-primary" id="mSave">書き込む</button>'
            '<button type="button" class="btn btn-danger" id="mDelete">この行を消す</button>'
            '</div>'
            '<div id="mResult" class="hint" style="margin-top:10px"></div>'
            '</div>')
        return head + body

    def _setting_field(self, it: dict) -> str:
        """設定 1 項目の入力欄。"""
        key = it["key"]
        badge = ""
        if it["overridden"]:
            badge = '<span class="layer layer-local">この端末</span>'
        elif it["layer"] == "app":
            badge = '<span class="layer layer-app">同梱の既定</span>'
        elif it["layer"] == "env":
            badge = '<span class="layer layer-env">環境変数</span>'
        else:
            badge = '<span class="layer layer-def">既定値</span>'
        if it["restart"]:
            badge += '<span class="layer layer-restart">要再起動</span>'

        if it["kind"] == "bool":
            checked = "checked" if it["value"] else ""
            control = (f'<label class="switch"><input type="checkbox" id="set_{esc(key)}" '
                       f'data-key="{esc(key)}" data-kind="bool" {checked}>'
                       f'<span>有効にする</span></label>')
        elif it["kind"] == "int":
            control = (f'<input type="number" id="set_{esc(key)}" data-key="{esc(key)}" '
                       f'data-kind="int" value="{esc(it["value"])}">')
        else:
            control = (f'<input type="text" id="set_{esc(key)}" data-key="{esc(key)}" '
                       f'data-kind="{esc(it["kind"])}" value="{esc(it["value"])}" '
                       f'placeholder="{esc(it["placeholder"])}" spellcheck="false">')

        check = ""
        if "check" in it:
            c = it["check"]
            cls = {"ok": "okbox", "warn": "warnbox", "error": "errbox"}.get(
                c["level"], "hint")
            check = (f'<div class="{cls}" style="margin:6px 0 0;padding:5px 9px;'
                     f'font-size:11.5px">{esc(c["message"])}</div>')

        resolved = ""
        if it.get("resolved") and not it["value"]:
            resolved = (f'<div class="hint" style="margin-top:4px">'
                        f'実際に使うパス: <span class="mono">{esc(it["resolved"])}</span></div>')
        elif it.get("resolved") and it["key"] == "aim_ref_path":
            resolved = (f'<div class="hint" style="margin-top:4px">'
                        f'探すファイル: <span class="mono">{esc(it["resolved"])}</span></div>')

        return (f'<div class="setrow" data-field="{esc(key)}">'
                f'<div class="setlab"><label for="set_{esc(key)}">{esc(it["label"])}</label>'
                f'{badge}</div>'
                f'<div class="setctl">{control}'
                f'<div class="err" id="err_{esc(key)}" hidden></div>'
                f'{check}{resolved}'
                f'<div class="hint">{esc(it["help"])}</div></div></div>')

    @staticmethod
    def _settings_script() -> str:
        return """<script>
(function(){
  function collect(){
    var out={};
    document.querySelectorAll("[data-key]").forEach(function(el){
      out[el.dataset.key] = (el.dataset.kind==="bool") ? el.checked : el.value;
    });
    return out;
  }
  function showErrors(errs){
    document.querySelectorAll(".err").forEach(function(e){
      e.hidden=true; e.textContent="";
    });
    Object.keys(errs||{}).forEach(function(k){
      var e=document.getElementById("err_"+k);
      if(e){ e.textContent=errs[k]; e.hidden=false; }
    });
  }
  function result(html, cls){
    var e=document.getElementById("setResult");
    e.className = cls || "hint";
    e.innerHTML = html;
  }
  // --- 設定の中のページ切替（パス設定 / マスタ管理）---
  document.querySelectorAll(".subtab").forEach(function(b){
    b.addEventListener("click", function(){
      var want = b.dataset.sub;
      document.querySelectorAll(".subtab").forEach(function(x){
        x.classList.toggle("on", x === b);
      });
      document.querySelectorAll(".subpane").forEach(function(p){
        p.hidden = (p.dataset.sub !== want);
      });
      if (want === "master" && window.pplMasterInit) { window.pplMasterInit(); }
    });
  });

  // --- 合言葉の確認 ---
  // 入れただけでは合っているか分からないので、その場で ○× を出す。
  function pwState(text, cls){
    var e = document.getElementById("setPwState");
    if (e) { e.textContent = text; e.className = "pwstate " + (cls || ""); }
  }
  (function(){
    var box = document.getElementById("setPassword");
    var btn = document.getElementById("setPwCheck");
    if (!box || !btn) { return; }
    function check(){
      if (!box.value) { pwState("未入力", "ng"); return; }
      pwState("確認しています…", "");
      pplPost("/api/settings/check-password", {password: box.value})
        .then(function(j){
          pwState(j.valid ? "合っています" : "違います", j.valid ? "ok" : "ng");
          pplToast(j.message, j.valid ? "info" : "warn");
        }).catch(function(e){ pwState("確認できません", "ng");
                              pplToast(String(e), "error"); });
    }
    btn.addEventListener("click", check);
    box.addEventListener("keydown", function(ev){
      if (ev.key === "Enter") { ev.preventDefault(); check(); }
    });
    box.addEventListener("input", function(){ pwState("未確認", ""); });
  })();

  function save(password){
    var body = {values: collect()};
    if (password) { body.password = password; }
    return pplPost("/api/settings/save", body).then(function(j){
      // パスを変えようとしたのに合言葉が無い／違う → その場で聞く
      if (j.needPassword) {
        var pw = window.prompt(j.message + "\\n\\n合言葉を入力してください", "");
        if (pw === null) {
          result("パスの変更は取り消しました。", "warnbox");
          return null;
        }
        var box = document.getElementById("setPassword");
        if (box) { box.value = pw; }
        return save(pw);
      }
      return j;
    });
  }

  document.getElementById("setSave").addEventListener("click", function(){
    var box = document.getElementById("setPassword");
    save(box ? box.value : "").then(function(j){
      if (!j) { return; }
      showErrors(j.errors);
      pplToast(j.message, j.ok ? "info" : "warn");
      if(!j.ok){ result("入力を確認してください。", "errbox"); return; }
      var html = "保存先: <span class='mono'>" + j.path + "</span>";
      if(j.changed && j.changed.length){
        html += "<br>変更: " + j.changed.join(" / ");
      }
      if(j.applied && j.applied.length){
        html += "<br>反映済み: " + j.applied.join(" / ");
      }
      if(j.warnings && j.warnings.length){
        html += "<br><b>到達できないパス:</b><br>" + j.warnings.join("<br>");
      }
      if(j.warnings && j.warnings.length && !(j.restart||[]).length){
        result(html, "warnbox");
      } else if(j.restart && j.restart.length){
        html += "<br><b>再起動が必要: " + j.restart.join(" / ") +
                "</b>（stop.bat で止めてから Start.vbs で起動し直してください）";
        result(html, "warnbox");
      } else {
        result(html, "okbox");
      }
      setTimeout(function(){ location.reload(); }, 1200);
    }).catch(function(e){ pplToast(String(e), "error"); });
  });
  document.getElementById("setTest").addEventListener("click", function(){
    result("確認しています…", "hint");
    pplPost("/api/settings/test-master", {values: collect()}).then(function(j){
      pplToast(j.message, j.ok ? "info" : "warn");
      result(j.detail || j.message, j.ok ? "okbox" : "errbox");
    }).catch(function(e){ pplToast(String(e), "error"); });
  });
  // ================================================ 配布設定
  // 選んだ項目と合言葉を送り、返ってきた状態を写すだけ（python-web-tools と同じ）。
  // 合言葉は一番上の欄を読み、空・違うならその場で聞く。
  function dEl(id){ return document.getElementById(id); }
  function dRender(d){
    if (!d || !dEl("distRows")) { return; }
    var st = dEl("distState");
    st.textContent = d.exists ? "あり" : "なし";
    st.className = "pill " + (d.exists ? "ok" : "warn");
    dEl("distMeta").textContent = d.exists
      ? (d.createdAt + " に " + d.createdOn + " で作成")
      : "まだありません。下で書き出すと、アプリのフォルダーの「配布設定\\\\packing_pena_label」にできます。";
    var tb = dEl("distRows");
    tb.innerHTML = "";
    (d.contents || []).forEach(function(c){
      var tr = document.createElement("tr");
      [c.label, c.value].forEach(function(t, i){
        var td = document.createElement("td");
        if (i === 1) { td.className = "mono"; }
        td.textContent = t;
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    dEl("distPath").textContent = d.path;
    dEl("distApplied").textContent = d.appliedAt
      ? ("この端末が最後に読み込んだ／書き出したとき: " + d.appliedAt)
      : "この端末はまだ配布設定を読み込んでいません。";
  }
  function dResult(text, cls){
    var e = dEl("distResult");
    if (e) { e.className = cls || "hint"; e.textContent = text || ""; }
  }
  function dPost(url, body, password){
    var b = {};
    Object.keys(body).forEach(function(k){ b[k] = body[k]; });
    var box = dEl("setPassword");
    var pw = (password === undefined) ? (box ? box.value : "") : password;
    if (pw) { b.password = pw; }
    return pplPost(url, b).then(function(j){
      if (!j.needPassword) { return j; }
      var got = window.prompt(j.message + "\\n\\n合言葉を入力してください", "");
      if (got === null) { return null; }
      if (box) { box.value = got; }
      return dPost(url, body, got);
    });
  }
  function dSend(url, body, confirmText){
    if (confirmText && !window.confirm(confirmText)) { return; }
    dResult("処理しています…", "hint");
    dPost(url, body).then(function(j){
      if (!j) { dResult("取り消しました。", "warnbox"); return; }
      dRender(j.distribution);
      var text = j.message || (j.ok ? "済みました" : "できませんでした");
      if (j.kept && j.kept.length) {
        text += "\\nすでにあったので読まなかったもの: " + j.kept.join("・");
      }
      dResult(text, j.ok ? "okbox" : "errbox");
      pplToast(j.message, j.ok ? "info" : "warn");
      if (j.ok && url.indexOf("reapply") >= 0) {
        // 読み込み直した値をパス設定の面にも出すため、読み直す
        setTimeout(function(){ location.reload(); }, 1500);
      }
    }).catch(function(e){ dResult(String(e), "errbox"); });
  }
  function dChecked(){
    return Array.prototype.slice.call(
      document.querySelectorAll("[data-dist-item]"))
      .filter(function(b){ return b.checked; })
      .map(function(b){ return b.getAttribute("data-dist-item"); });
  }
  if (dEl("distBuild")) {
    dEl("distBuild").addEventListener("click", function(){
      var log = dEl("distBuildResult");
      var msg = dEl("distBuildMsg");
      function bResult(text, cls){ msg.className = cls || "hint"; msg.textContent = text || ""; }
      bResult("配布用フォルダを作っています…（数秒かかります）", "hint");
      if (log) { log.hidden = true; }
      dPost("/api/settings/distribution/build", {
        out: dEl("distOut").value,
        withSettings: dEl("distWithSettings").checked,
        zip: dEl("distZip").checked,
        force: dEl("distForce").checked
      }).then(function(j){
        if (!j) { bResult("取り消しました。", "warnbox"); return; }
        bResult(j.message || "", j.ok ? "okbox" : "errbox");
        pplToast(j.message, j.ok ? "info" : "warn");
        if (log) {
          log.hidden = !(j.lines && j.lines.length);
          log.textContent = (j.lines || []).join("\\n");
        }
      }).catch(function(e){ bResult(String(e), "errbox"); });
    });
  }
  if (dEl("distExport")) {
    dEl("distExport").addEventListener("click", function(){
      dSend("/api/settings/distribution/export", {items: dChecked()},
            "この端末の設定を配布設定として書き出します（前の中身は置き換えます）。");
    });
    dEl("distReapply").addEventListener("click", function(){
      dSend("/api/settings/distribution/reapply", {},
            "配布設定を読み込み直します。この端末の設定も上書きされます。");
    });
    dEl("distRemove").addEventListener("click", function(){
      dSend("/api/settings/distribution/remove", {},
            "配布設定を消します。この端末の設定はそのままです。");
    });
    dEl("distAll").addEventListener("click", function(){
      document.querySelectorAll("[data-dist-item]").forEach(function(b){ b.checked = true; });
    });
    dEl("distNone").addEventListener("click", function(){
      document.querySelectorAll("[data-dist-item]").forEach(function(b){ b.checked = false; });
    });
  }

  // ================================================ マスタ管理
  // 行は「__行」(取り込み元の rowid) で指す。管理番号は業務の列であって
  // 行の名前ではない（重複も欠番もありうる）。ここを取り違えると
  // 「直したつもりが別の行だった」になる。
  var M = {table:"", columns:[], editableColumns:[], fixedRows:false,
           rowKey:"__行", rows:[], editable:false, cur:null};

  // マスタを直すと全員の計算・印刷に影響するので、合言葉を使う。
  // パス設定と同じ欄を読み、空なら必要になった時点で聞く。
  function mPassword(){
    var box = document.getElementById("setPassword");
    return box ? box.value : "";
  }
  function mPost(url, body, password){
    var b = {};
    Object.keys(body).forEach(function(k){ b[k] = body[k]; });
    var pw = (password === undefined) ? mPassword() : password;
    if (pw) { b.password = pw; }
    return pplPost(url, b).then(function(j){
      if (!j.needPassword) { return j; }
      var got = window.prompt(j.message + "\\n\\n合言葉を入力してください", "");
      if (got === null) { return null; }
      var box = document.getElementById("setPassword");
      if (box) { box.value = got; }
      return mPost(url, body, got);
    });
  }

  function mEl(id){ return document.getElementById(id); }
  function mResult(text, cls){
    var e = mEl("mResult");
    if(e){ e.className = cls || "hint"; e.textContent = text || ""; }
  }

  function mLoadTables(){
    return pplPost("/api/master/tables", {}).then(function(j){
      var sel = mEl("mTable");
      sel.innerHTML = "";
      (j.tables||[]).forEach(function(t){
        var o = document.createElement("option");
        o.value = t.table;
        o.textContent = t.mark + " " + t.label + (t.note ? "（" + t.note + "）" : "")
                      + (t.missing ? " ※この資材マスタには無い表" : "");
        sel.appendChild(o);
      });
      mEl("mNew").disabled = !j.editable;
      if(sel.options.length){ return mLoadRows(); }
    });
  }

  function mLoadRows(){
    var table = mEl("mTable").value;
    return pplPost("/api/master/rows",
                   {table: table, keyword: mEl("mKeyword").value})
      .then(function(j){
        var head = mEl("mTbl").querySelector("thead tr");
        var body = mEl("mTbl").querySelector("tbody");
        head.innerHTML = ""; body.innerHTML = "";
        if(!j.ok){
          mEl("mNote").textContent = j.message || "読めませんでした";
          return;
        }
        M.table = j.table; M.columns = j.columns; M.rowKey = j.rowKey;
        M.rows = j.rows; M.editable = j.editable;
        M.editableColumns = j.editableColumns || j.columns;
        M.fixedRows = !!j.fixedRows;
        mEl("mNew").disabled = !j.editable || M.fixedRows;
        mEl("mNote").textContent =
          (j.viewOnlyWhy ? j.viewOnlyWhy + " " : "") +
          (j.whyNot ? j.whyNot + " " : "") +
          (j.note || ("全 " + j.total + " 件"));

        j.columns.forEach(function(c){
          var th = document.createElement("th");
          th.textContent = c; head.appendChild(th);
        });
        var thx = document.createElement("th");
        thx.textContent = ""; head.appendChild(thx);

        j.rows.forEach(function(r){
          var tr = document.createElement("tr");
          j.columns.forEach(function(c){
            var td = document.createElement("td");
            td.textContent = r[c]; tr.appendChild(td);
          });
          var td = document.createElement("td");
          if(j.editable){
            var b = document.createElement("button");
            b.type = "button"; b.className = "btn"; b.textContent = "直す";
            b.style.width = "auto"; b.style.margin = "0"; b.style.padding = "2px 8px";
            b.addEventListener("click", function(){ mOpen(r); });
            td.appendChild(b);
          }
          tr.appendChild(td);
          body.appendChild(tr);
        });
      });
  }

  function mOpen(row){
    M.cur = row || null;
    var wrap = mEl("mFields");
    wrap.innerHTML = "";
    M.columns.forEach(function(c){
      var d = document.createElement("div");
      d.className = "setrow";
      var lab = document.createElement("label");
      lab.textContent = c;
      var inp = document.createElement("input");
      inp.type = "text";
      inp.value = row ? (row[c] || "") : "";
      if (M.editableColumns.indexOf(c) < 0) {
        // 直せない列は隠さずに出す。隠すと画面が壊れて見える
        inp.readOnly = true;
        lab.textContent = c + "（直せません）";
      } else {
        inp.dataset.col = c;
      }
      d.appendChild(lab); d.appendChild(inp);
      wrap.appendChild(d);
    });
    mEl("mEditTitle").textContent =
      row ? ("行を直す（" + M.rowKey + "=" + row[M.rowKey] + "）") : "行を足す";
    mEl("mDelete").hidden = !row || M.fixedRows;
    mEl("mEdit").hidden = false;
    mResult("");
  }

  function mValues(){
    var out = {};
    mEl("mFields").querySelectorAll("input[data-col]").forEach(function(i){
      out[i.dataset.col] = i.value;
    });
    return out;
  }

  function mBind(){
    if (mBind.done) { return; }
    mBind.done = true;
    mEl("mTable").addEventListener("change", mLoadRows);
    mEl("mFind").addEventListener("click", mLoadRows);
    mEl("mKeyword").addEventListener("keydown", function(e){
      if(e.key === "Enter"){ e.preventDefault(); mLoadRows(); }
    });
    mEl("mNew").addEventListener("click", function(){ mOpen(null); });
    mEl("mClose").addEventListener("click", function(){ mEl("mEdit").hidden = true; });
    mEl("mSave").addEventListener("click", function(){
      var id = M.cur ? M.cur[M.rowKey] : null;
      mPost("/api/master/save",
            {table: M.table, rowId: id, values: mValues()}).then(function(j){
        if (!j) { return; }
        pplToast(j.message, j.ok ? "info" : "warn");
        mResult(j.message + (j.backup ? "（控え: " + j.backup + "）" : ""),
                j.ok ? "okbox" : "errbox");
        if(j.ok){ mEl("mEdit").hidden = true; mLoadRows(); }
      }).catch(function(e){ pplToast(String(e), "error"); });
    });
    mEl("mDelete").addEventListener("click", function(){
      if(!M.cur){ return; }
      if(!window.confirm("この行を消します。よろしいですか？")){ return; }
      mPost("/api/master/delete",
            {table: M.table, rowId: M.cur[M.rowKey]}).then(function(j){
        if (!j) { return; }
        pplToast(j.message, j.ok ? "info" : "warn");
        mResult(j.message, j.ok ? "okbox" : "errbox");
        if(j.ok){ mEl("mEdit").hidden = true; mLoadRows(); }
      }).catch(function(e){ pplToast(String(e), "error"); });
    });
  }

  window.pplMasterInit = function(){
    mBind();
    if (!M.columns.length) { mLoadTables(); }
  };

  document.getElementById("setReset").addEventListener("click", function(){
    if(!window.confirm("この端末の設定を消して、同梱の既定へ戻します。よろしいですか？")){
      return;
    }
    pplPost("/api/settings/reset", {}).then(function(j){
      pplToast(j.message, "info");
      setTimeout(function(){ location.reload(); }, 900);
    });
  });
})();
</script>"""

    # ============================================================ 診断
    def _started_at_text(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S",
                             time.localtime(self.started_at))

    def _uptime_text(self) -> str:
        sec = int(time.time() - self.started_at)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return "稼働 %d時間%02d分%02d秒" % (h, m, s)

    def _screen_text(self) -> str:
        """入力画面の使用状況（タブを 2 枚開かせない仕組み）。"""
        if self.screens is None:
            return "制限なし"
        i = self.screens.info()
        if not i.get("held"):
            return "未使用"
        return "使用中（1 画面・裏に回っている）" if i.get("hidden") else "使用中（1 画面）"

    def _screen_hint(self) -> str:
        if self.screens is None:
            return "画面の使用権を管理していません"
        i = self.screens.info()
        if not i.get("held"):
            return "いま入力画面を開いている人はいません"
        if i.get("hidden"):
            return ("%s から使用中／%s から裏に回っている／最終応答 %s 秒前"
                    "（裏にある間は %.0f 時間まで空き扱いにしない。"
                    "ブラウザーが裏のタブのタイマーを間引くため）"
                    % (i["since"], i.get("hiddenSince", "-"), i["idleSec"],
                       i["ttlSec"] / 3600.0))
        return ("%s から使用中／最終応答 %s 秒前（%s 秒で自動解放）"
                % (i["since"], i["idleSec"], i["ttlSec"]))

    def _launch_guard_info(self):
        """統合アプリの起動基盤(`launch_guard.py`)のロック。無ければ None。"""
        try:
            import launch_guard
            from common import modes
            return launch_guard.lock_path(modes.MAIN), launch_guard.read_lock(modes.MAIN)
        except Exception:                                   # 単体起動・試験
            return None, None

    def _launch_guard_text(self) -> str:
        """いま多重起動を防げている根拠を出す(統合アプリの起動基盤が受け持つ)。"""
        _path, info = self._launch_guard_info()
        if info is not None and info.pid == os.getpid():
            return "有効（統合アプリのロックをこのプロセスが保持）"
        return "ロックなし（ポートの二重確保はサーバの bind が防ぐ）"

    def _launch_guard_hint(self) -> str:
        mode = ("SO_EXCLUSIVEADDRUSE（Windows：ポートを自分だけで占有）"
                if os.name == "nt"
                else "SO_REUSEADDR（POSIX：待ち受け中のポートは奪われない）")
        path, info = self._launch_guard_info()
        if info is not None and info.pid == os.getpid():
            return "ロック: %s ／ 待ち受け: %s" % (path, mode)
        return ("start_app.py を経由していないためロックは未使用。"
                "待ち受け: %s" % mode)

    def diag(self) -> str:
        """基盤仕様書 2.6: 障害時に何を見ればよいか分かる画面。"""
        import platform
        import sys
        c = self.cfg
        checks = [
            ("Python", sys.version.split()[0], sys.executable),
            ("OS", platform.platform(), ""),
            ("アプリID", c.app_id, ""),
            ("バージョン", c.version, "画面左上にも出している"),
            ("版を切った日", getattr(c, "build", ""),
             "配布したファイル一式の日付"),
            ("中身の指紋", _code_stamp(),
             "動いているファイルの中身から作った値。"
             "ファイルを差し替えるとここが変わる"),
            ("ポート", str(c.port), ""),
            ("プロセスID", str(os.getpid()),
             "いま画面を出しているプロセス。多重起動の切り分けに使う"),
            ("起動時刻", self._started_at_text(), self._uptime_text()),
            ("多重起動の防止", self._launch_guard_text(), self._launch_guard_hint()),
            ("入力画面", self._screen_text(), self._screen_hint()),
            ("アプリ配置", c.__class__.__module__, os.path.dirname(c.templates_dir)),
            ("ローカル領域", c.local_dir, "存在" if os.path.isdir(c.local_dir) else "未作成"),
            ("ログ", c.logs_dir, "存在" if os.path.isdir(c.logs_dir) else "未作成"),
            ("状態DB", c.db_path, "存在" if os.path.exists(c.db_path) else "未作成"),
            ("資材マスタ(Access)", self.wf.materials.accdb_path or "(未設定)",
             "存在" if (self.wf.materials.accdb_path and
                       os.path.exists(self.wf.materials.accdb_path)) else "到達不可"),
            ("資材マスタ(SQLite)", self.wf.materials.db_path or "(未設定)",
             "存在" if (self.wf.materials.db_path and
                       os.path.exists(self.wf.materials.db_path)) else "未設定/到達不可"),
            ("資材マスタ(CSV)", self.wf.materials.csv_path,
             "存在" if os.path.exists(self.wf.materials.csv_path) else "なし"),
            ("最後の取得元", self.wf.materials.last_source or "(未読込)",
             self.wf.materials.last_error or ""),
            ("サイズマスタ", c.size_master_path,
             "存在" if os.path.exists(c.size_master_path) else "内蔵定義を使用"),
            ("監視レベル", str(c.monitor_level), ""),
        ]

        # --- 状態DB の接続状況（長時間起動・ハンドル保持の確認用）---
        try:
            st = self.wf.store.stats()
            checks += [
                ("状態DB 接続", "開いている" if st["open"] else "閉じている（アイドル解放）",
                 "無操作 %s 秒で自動的に閉じる" % st["idleCloseSec"]),
                ("状態DB 無操作", "%s 秒" % st["idleSec"] if st["idleSec"] is not None else "-",
                 "journal=%s / busy_timeout=%sms" % (st["journalMode"], st["busyTimeoutMs"])),
                ("状態DB サイズ", "%s バイト" % st["sizeBytes"],
                 "ネットワーク上" if st["networkPath"] else "ローカル領域"),
            ]
        except Exception as exc:
            checks.append(("状態DB 接続", "取得できません", str(exc)))

        # --- 資材マスタのキャッシュ鮮度 ---
        try:
            ci = self.wf.materials.cache_info()
            checks += [
                ("マスタ読込時刻", ci["loadedAt"] or "(未読込)",
                 ("経過 %s 秒" % ci["ageSec"]) if ci["ageSec"] is not None else ""),
                ("マスタ鮮度チェック",
                 "毎回" if ci["refreshSec"] == 0 else "%s 秒ごと" % ci["refreshSec"],
                 "更新日時・サイズが変われば自動で読み直す"),
            ]
            if ci["servingStale"]:
                checks.append(("マスタ状態", "取得元に到達できません",
                               "直前に読んだ内容で継続中。値が古い可能性があります"))
        except Exception as exc:
            checks.append(("マスタ状態", "取得できません", str(exc)))

        # --- 係数が 1 以外の資材（解析書 C-3）---
        # 「単位質量 × 係数」の 係数 が何を表すかはコードから読み取れない。
        # 全件 1 なら実質未使用と分かるので、ここで見えるようにしておく。
        try:
            table = self.wf.materials.load()
            odd = ["%s=%s" % (name, coef)
                   for name, coef in table.coefficients()
                   if (coef or "").strip() not in ("", "1", "1.0", "1.00")]
            checks.append((
                "資材の係数",
                "すべて 1" if not odd else "1 以外あり（%d 件）" % len(odd),
                "単位質量 × 係数 で乗算している"
                if not odd else "、".join(odd[:8])))
        except Exception as exc:
            checks.append(("資材の係数", "取得できません", str(exc)))
        rows = "".join(
            f'<tr><th>{esc(k)}</th><td>{esc(v)}</td><td class="hint">{esc(n)}</td></tr>'
            for k, v, n in checks)
        logs = ""
        if os.path.isdir(c.logs_dir):
            names = sorted(os.listdir(c.logs_dir), reverse=True)[:10]
            logs = "".join(f"<li>{esc(os.path.join(c.logs_dir, n))}</li>" for n in names)
        body = (f'<div class="card"><h2>診断</h2>'
                f'<table class="tbl"><tbody>{rows}</tbody></table></div>'
                f'<div class="card"><h2>ログ</h2><ul class="hint">{logs or "<li>なし</li>"}</ul>'
                f'<div class="btn-row no-print">'
                f'<button class="btn" onclick="pplPost(\'/api/reload-materials\',{{}})'
                f'.then(j=>pplToast(j.ok?(\'資材マスタを読み直しました: \'+j.source):j.message,'
                f'j.ok?\'info\':\'error\'))">資材マスタを読み直す</button></div></div>')
        return self._shell("診断", body, "")
