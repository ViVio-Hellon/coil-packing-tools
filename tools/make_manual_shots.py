#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""操作説明書の写真を撮る(開発用の PC。統合 1.2.0)

    python tools/make_manual_shots.py               # 全部撮り直す
    python tools/make_manual_shots.py --only details  # shell / details / pena / material のどれかだけ

【何をするか】
通し試験(`tools/e2e_scenarios.py`)と同じ試験用のデータ(取り込み元・名簿。人の名前は作り物)で
統合アプリを本当に起動し(ブラウザ版)、実ブラウザ(Chromium)で画面を操作しながら撮る。
写真には、説明書の手順と同じ番号の**赤い丸数字と枠**を描き込む(押す場所・見る場所)。

    static/manual/img/<名前>.png    写真(説明書の `m.fig("<名前>", …)` が使う)
    static/manual/shots.json        撮った日・撮ったときの版・写真ごとの大きさ

説明書は `shots.json` の版と、いま動いている版が違えば「写真は○○の版の画面」と断る。
**画面を変えたら撮り直す**(人の手で切り抜いたり描いたりしない ── 撮り直せなくなるため)。

【要るもの】Playwright と Chromium・Pillow(現場の PC には要らない)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import e2e_scenarios as E  # noqa: E402

OUT = ROOT / "static" / "manual"
VIEW = {"width": 1280, "height": 900}
MARK = (217, 72, 15)                     # manual.css の --mark と同じ色
FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf")


# ======================================================================
# 撮る・描き込む
# ======================================================================
def _font(size):
    from PIL import ImageFont
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def box_of(target, sel=None):
    """要素の枠(ページの左上から。iframe の中の要素も外枠の座標で返る)。"""
    loc = target.locator(sel).first if sel else target
    loc.wait_for(state="visible", timeout=10000)
    b = loc.bounding_box()
    if not b:
        raise RuntimeError(f"枠が取れない: {sel}")
    return (b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"])


class Camera:
    def __init__(self, out: Path):
        self.out = out
        (out / "img").mkdir(parents=True, exist_ok=True)
        self.images: dict = {}

    def take(self, name, page, marks=(), clip=None, pad=5):
        """`page` を撮って、`marks` を描き込む。

        `marks` は [(番号, (x1, y1, x2, y2)[, 置き場所]), …](ページの座標)。置き場所は丸数字の位置:
        "tl"(枠の左上・既定) / "b"(枠の下) / "l"(枠の左) / "r"(枠の右) / "t"(枠の上)。
        `clip` は撮る範囲(ページの座標。None なら見えている全体)。
        """
        from PIL import Image, ImageDraw
        import io
        page.wait_for_timeout(300)
        if clip:
            x1, y1, x2, y2 = (round(v) for v in clip)
            raw = page.screenshot(clip={"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1})
            ox, oy = x1, y1
        else:
            raw = page.screenshot()
            ox = oy = 0
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        draw = ImageDraw.Draw(im)
        font = _font(17)
        r = 14
        circles = []
        for mark in marks:
            number, (bx1, by1, bx2, by2) = mark[0], mark[1]
            where = mark[2] if len(mark) > 2 else "tl"
            a = (bx1 - ox - pad, by1 - oy - pad, bx2 - ox + pad, by2 - oy + pad)
            draw.rounded_rectangle(a, radius=6, outline=MARK, width=3)
            midx, midy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
            cx, cy = {"tl": (a[0] - 2, a[1] - 2), "b": (midx, a[3] + r + 2),
                      "t": (midx, a[1] - r - 2), "l": (a[0] - r - 3, midy),
                      "r": (a[2] + r + 3, midy)}[where]
            cx = max(r + 2, min(im.width - r - 2, cx))
            cy = max(r + 2, min(im.height - r - 2, cy))
            circles.append((number, cx, cy))
        # 丸数字は枠を描き終えてから(ほかの枠の線に隠れない)
        for number, cx, cy in circles:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=MARK, outline=(255, 255, 255), width=2)
            draw.text((cx, cy), str(number), fill=(255, 255, 255), font=font, anchor="mm")
        im = trim_bottom(im)
        path = self.out / "img" / f"{name}.png"
        im = im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        im.save(path, optimize=True)
        self.images[name] = {"w": im.width, "h": im.height}
        print(f"  撮った: {name} ({im.width}x{im.height}, {path.stat().st_size // 1024}KB)", flush=True)
        return path


def trim_bottom(im, keep=24, least=120):
    """下の何も無い部分(いちばん下の行と同じ色が続くところ)を切り詰める。

    画面の中身が短いと、写真の下半分が空になる。読む人には要らないので落とす
    (`least` px 以上空いているときだけ。`keep` px は余白として残す)。
    """
    from PIL import Image, ImageChops
    bg = im.getpixel((im.width // 2, im.height - 1))
    diff = ImageChops.difference(im, Image.new("RGB", im.size, bg)).convert("L").point(
        lambda v: 255 if v > 12 else 0)
    box = diff.getbbox()
    if not box:
        return im
    bottom = min(im.height, box[3] + keep)
    return im.crop((0, 0, im.width, bottom)) if im.height - bottom >= least else im


def write_meta(cam: Camera, app_versions: dict, merge: bool) -> None:
    meta_path = cam.out / "shots.json"
    meta = {}
    if merge and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    images = dict(meta.get("images") or {})
    images.update(cam.images)
    meta.update({
        "taken": dt.date.today().isoformat(),
        "versions": app_versions,
        "images": dict(sorted(images.items())),
        "note": "tools/make_manual_shots.py が撮った写真の控え。手で直さない",
    })
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ======================================================================
# 場面(説明書の章ごと)
# ======================================================================
SCENES: dict = {}


def scene(key):
    def deco(fn):
        SCENES[key] = fn
        return fn
    return deco


# ---- ここから下に各機能の場面(shell / details / pena / material) ----


@scene("shell")
def shell_scene(app, ctx, sh, cam):
    pg = sh.page
    sh.tab("details")
    sh.ready()
    # 統合画面の全体: 帯とタブ
    cam.take("shell-overview", pg, pad=3, marks=[
        (1, box_of(pg, ".tabs"), "l"),
        (2, box_of(pg, "#verBtn"), "b"),
        (3, box_of(pg, "#themeSwitch"), "b"),
        (4, box_of(pg, "#manualBtn"), "b"),
        (5, box_of(pg, "#logBtn"), "b"),
        (6, box_of(pg, "#conn"), "b"),
        (7, box_of(pg, "#quit"), "b"),
    ])
    # タブ(3つのツールと、それぞれの版)
    tabs = box_of(pg, ".tabs")
    cam.take("shell-tabs", pg, clip=(0, 0, VIEW["width"], tabs[3] + 34), pad=2, marks=[
        (1, box_of(pg, "#tab-details"), "b"), (2, box_of(pg, "#tab-pena"), "b"),
        (3, box_of(pg, "#tab-material"), "b")])
    # 版の一覧(帯の版を押す)
    pg.click("#verBtn")
    pg.wait_for_selector("#verPanel:not([hidden])")
    panel = box_of(pg, "#verPanel")
    cam.take("shell-versions", pg, clip=(0, 0, VIEW["width"], min(VIEW["height"], panel[3] + 16)),
             marks=[(1, box_of(pg, "#verBtn"), "r"), (2, panel, "l")])
    pg.keyboard.press("Escape")
    # 画面の色: ダークを選んだところ
    pg.click('#themeSwitch [data-theme-set="dark"]')
    pg.wait_for_timeout(800)
    cam.take("shell-theme-dark", pg, marks=[(1, box_of(pg, '#themeSwitch [data-theme-set="dark"]'), "b")])
    pg.click('#themeSwitch [data-theme-set="auto"]')
    pg.wait_for_timeout(800)
    # ログとエラーの記録
    pg.click("#logBtn")
    pg.wait_for_selector("#logDialog[open]")
    pg.wait_for_timeout(1500)
    cam.take("shell-log", pg, marks=[(1, box_of(pg, "#logClose"), "l")])
    pg.click("#logClose")


def shell_manual_shot(app, ctx, sh, cam):
    """説明書のダイアログ。**最後に撮る** ── 写真の中の説明書に、この回に撮った写真が写るように。"""
    pg = sh.page
    sh.tab("details")
    pg.click("#manualBtn")
    pg.wait_for_selector("#manualDialog[open]")
    pg.wait_for_timeout(1500)
    cam.take("shell-manual", pg, marks=[(1, box_of(pg, "#manualPop"), "l"),
                                        (2, box_of(pg, "#manualClose"), "b")])
    pg.click("#manualClose")



def frame_box(sh, key):
    """機能の画面(iframe)の枠。機能の写真はこの範囲だけを撮る(上の帯を除く)。"""
    return box_of(sh.page, f'iframe[name="cpt-{key}"]')


def into_view(fr, sel, block="center"):
    """要素を画面の真ん中(block)へ。機能の画面は中の枠ごと動くことがあるので scrollIntoView で。"""
    fr.locator(sel).first.evaluate("(e, b) => e.scrollIntoView({block: b})", block)
    fr.page.wait_for_timeout(300)


def new_page(ctx, click):
    """押すと別のタブ(デスクトップ版では別の窓)で開く画面を受け取る。"""
    with ctx.expect_page() as info:
        click()
    pg = info.value
    pg.set_viewport_size(VIEW)
    pg.wait_for_load_state()
    pg.wait_for_timeout(500)
    return pg


@scene("details")
def details_scene(app, ctx, sh, cam):
    fr = sh.tab("details")
    sh.ready()
    clip = frame_box(sh, "details")
    pg = sh.page
    cam.take("details-start", pg, clip=clip, marks=[
        (1, box_of(fr, "#lotNo")), (2, box_of(fr, "#btnImport"), "b"),
        (3, box_of(fr, "#btnHistory"), "b"), (4, box_of(fr, "#btnSettings"), "b"),
        (5, box_of(fr, "#btnAbout"), "r")])
    E.details_open_lot(fr, "L5160Z0")
    fr.wait_for_timeout(500)
    cam.take("details-lot", pg, clip=clip, marks=[
        (1, box_of(fr, "#lotNo")), (2, box_of(fr, "#warisu")), (3, box_of(fr, "#lotInfo"), "l")])
    E.details_weights(fr, 250, 248)
    cam.take("details-weights", pg, clip=clip, marks=[
        (1, box_of(fr, "#jouSu")), (2, box_of(fr, "#weights")), (3, box_of(fr, "#btnStrands"))])
    fr.click("#btnStrands")
    E.wait_until(lambda: E.visible(fr, "#strandCard"), 8)
    fr.fill("#stackMax", "4")
    fr.press("#stackMax", "Tab")
    E.wait_until(lambda: fr.evaluate("document.querySelectorAll('#stack button.slot').length") == 4, 5)
    for k in ("1-12", "1-11"):
        fr.click(f'#grid button.coil[data-key="{k}"]')
        fr.wait_for_timeout(150)
    into_view(fr, "#strandCard")
    cam.take("details-stack", pg, clip=clip, marks=[
        (1, box_of(fr, "#stackMax")), (2, box_of(fr, "#grid"), "l"), (3, box_of(fr, "#stack"), "l")])
    for k in ("2-12", "2-11"):
        fr.click(f'#grid button.coil[data-key="{k}"]')
        fr.wait_for_timeout(150)
    E.wait_until(lambda: fr.evaluate("!document.querySelector('#btnOutput').disabled"), 5)
    into_view(fr, "#btnOutput")
    cam.take("details-ready", pg, clip=clip, marks=[(1, box_of(fr, "#stack"), "l"),
                                                   (2, box_of(fr, "#btnOutput"))])
    fr.click("#btnOutput")
    fr.wait_for_timeout(500)
    if E.visible(fr, "#confirmDialog"):
        fr.click('#confirmDialog button[value="yes"]')
    E.wait_until(lambda: "出力しました" in E.text(fr, "#notice"), 8)
    fr.evaluate("window.scrollTo(0, 0)")
    cam.take("details-output", pg, clip=clip, marks=[
        (1, box_of(fr, "#notice")), (2, box_of(fr, "#outputCard"), "l"), (3, box_of(fr, "#btnPrint"))])
    report = new_page(ctx, lambda: fr.click("#btnPrint"))
    report.click('span.edit[data-placeholder="サイズ"]')
    report.keyboard.type("1.0×104")
    report.keyboard.press("Enter")
    E.wait_until(lambda: "保存しました" in E.text(report, "#editSaved"), 8)
    cam.take("details-report", report, marks=[
        (1, box_of(report, 'span.edit[data-placeholder="サイズ"]')),
        (2, box_of(report, "p.editbar button.print"), "r")])
    report.close()
    # 履歴
    fr.click("#btnHistory")
    E.wait_until(lambda: E.visible(fr, "#historyDialog"), 8)
    fr.wait_for_timeout(800)
    fr.click("#btnHisSearch")
    E.wait_until(lambda: fr.locator("#hisRows tr").count() > 0, 10)
    fr.locator("#hisRows tr").first.click()
    fr.wait_for_timeout(300)
    cam.take("details-history", pg, clip=clip, marks=[
        (1, box_of(fr, "#hisFrom")), (2, box_of(fr, "#btnHisSearch")),
        (3, box_of(fr, "#hisRows tr"), "l"), (4, box_of(fr, "#btnHisReprint"))])
    fr.click("#btnHisClose")
    # 設定
    fr.click("#btnSettings")
    E.wait_until(lambda: E.visible(fr, "#settingsDialog"), 8)
    E.wait_until(lambda: not E.visible(fr, "#setLoading"), 20)
    fr.wait_for_timeout(500)
    cam.take("details-settings", pg, clip=clip, marks=[
        (1, box_of(fr, "#tabPlace"), "b"), (2, box_of(fr, "#tabQa"), "b"),
        (3, box_of(fr, "#tabAdmin"), "b"), (4, box_of(fr, "#tabDist"), "b"),
        (5, box_of(fr, "#tabStore"), "b")])
    fr.click("#tabQa")
    fr.wait_for_timeout(600)
    cam.take("details-settings-qa", pg, clip=clip, marks=[
        (1, box_of(fr, "#setQaNow")), (2, box_of(fr, "#setQaValue")),
        (3, box_of(fr, "#setQaPassword")), (4, box_of(fr, "#btnQaSave"))])
    fr.click("#tabStore")
    E.wait_until(lambda: fr.evaluate("document.querySelectorAll('#storeGroups [data-store]').length") == 3, 10)
    cam.take("details-settings-store", pg, clip=clip)
    fr.click("#btnSettingsClose")
    # バージョン情報
    fr.click("#btnAbout")
    E.wait_until(lambda: E.visible(fr, "#aboutDialog"), 8)
    fr.wait_for_timeout(800)
    cam.take("details-about", pg, clip=clip, marks=[(1, box_of(fr, "#abVer"))])
    fr.click("#btnAboutClose")


@scene("material")
def material_scene(app, ctx, sh, cam):
    fr = sh.tab("material")
    E.material_ready(fr)
    pg = sh.page
    clip = frame_box(sh, "material")
    E.material_worker(fr)
    cam.take("material-start", pg, clip=clip, marks=[
        (1, box_of(fr, "header.ribbon a.ver"), "b"), (2, box_of(fr, "#rb-imported"), "b"),
        (3, box_of(fr, "#rb-line"), "b"), (4, box_of(fr, "#rb-worker"), "b"),
        (5, box_of(fr, "nav.rail"), "r")])
    fr.fill("#lot", "")
    fr.type("#lot", "A123456", delay=20)
    E.wait_until(lambda: E.text(fr, "#o-包装") == E.SPEC_NO, 10)
    E.material_ins(fr, "10")
    cam.take("material-input", pg, clip=clip, marks=[
        (1, box_of(fr, "#lot")), (2, box_of(fr, "#order-no")), (3, box_of(fr, "#ins")),
        (4, box_of(fr, "#outer")), (5, box_of(fr, "#run"))])
    fr.click("#run")
    E.material_toast(fr, "台数")
    into_view(fr, "#r-種類", "start")
    cam.take("material-result", pg, clip=clip, marks=[
        (1, box_of(fr, "#r-種類")), (2, box_of(fr, "#r-台数"))])
    into_view(fr, "#add")
    fr.check("#size-fixed")
    cam.take("material-add", pg, clip=clip, marks=[
        (1, box_of(fr, "#r-total")), (2, box_of(fr, "#size-fixed")), (3, box_of(fr, "#add"))])
    fr.click("#add")
    E.material_toast(fr, "行目に積みました")
    into_view(fr, "#o-包装")
    cam.take("material-spec", pg, clip=clip, marks=[(1, box_of(fr, "#o-包装"))])
    fr.click('nav.rail a.rail__item[href*="/checklist"]')
    fr.wait_for_load_state()
    fr = sh.frame("material")
    E.material_ready(fr)
    E.wait_until(lambda: E.text(fr, "#sub").startswith("1 /"), 8)
    cam.take("material-checklist", pg, clip=clip, marks=[
        (1, box_of(fr, "table tbody tr"), "l"), (2, box_of(fr, "a#print"), "b"),
        (3, box_of(fr, "a#to-order"), "b"), (4, box_of(fr, "#clear"), "b")])
    rep = new_page(ctx, lambda: fr.click("a#print"))
    cam.take("material-checklist-print", rep, marks=[(1, box_of(rep, "#printNow"), "r")])
    rep.close()
    fr.click("a#to-order")
    fr.wait_for_load_state()
    fr = sh.frame("material")
    E.material_ready(fr)
    fr.click("#build")
    E.material_toast(fr, "枚作りました")
    cam.take("material-order", pg, clip=clip, marks=[
        (1, box_of(fr, "#build"), "b"), (2, box_of(fr, "a#print"), "b"),
        (3, box_of(fr, "#commit"), "b"), (4, box_of(fr, "#sheets"), "l")])
    rep = new_page(ctx, lambda: fr.click("a#print"))
    cam.take("material-order-print", rep, marks=[(1, box_of(rep, "#printNow"), "r")])
    rep.close()
    fr.click("#commit")
    E.material_toast(fr, "履歴に残しました")
    # 設定
    fr.click('nav.rail a.rail__item[href*="/settings"]')
    fr.wait_for_load_state()
    fr = sh.frame("material")
    E.material_ready(fr)
    fr.click("#tab-source")
    fr.wait_for_timeout(800)
    cam.take("material-settings", pg, clip=clip, marks=[
        (1, box_of(fr, "#authPass"), "b"), (2, box_of(fr, "#tab-source"), "b"),
        (3, box_of(fr, "#fields .setrow"), "l")])
    into_view(fr, "#import")
    cam.take("material-import", pg, clip=clip, marks=[
        (1, box_of(fr, "#auto-import")), (2, box_of(fr, "#import")), (3, box_of(fr, "#imported"), "l")])
    fr.fill("#authPass", "nisk")
    fr.click("#authOpen")
    E.material_toast(fr, "直せるように")
    fr.click("#tab-master")
    fr.locator("#mTables button", has_text="パレット").first.click()
    E.wait_until(lambda: fr.locator("#mRows tr").count() >= 2, 15)
    fr.wait_for_timeout(500)
    cam.take("material-master", pg, clip=clip, marks=[
        (1, box_of(fr, "#authState"), "b"), (2, box_of(fr, "#mTables"), "l"),
        (3, box_of(fr, "#mQuery")), (4, box_of(fr, "#mHead"), "l")])
    fr.locator("#mRows tr").first.click()
    E.wait_until(lambda: E.visible(fr, "#mEdit"), 8)
    fr.wait_for_timeout(400)
    cam.take("material-master-row", pg, clip=clip, marks=[(1, box_of(fr, "#mSave"))])
    fr.locator("#mEdit button", has_text="閉じる").first.click()
    fr.click("#authClose")
    fr.click("#tab-team")
    fr.wait_for_timeout(600)
    cam.take("material-team", pg, clip=clip, marks=[(1, box_of(fr, "#line")), (2, box_of(fr, "#worker"))])
    fr.click("#tab-about")
    fr.wait_for_timeout(600)
    cam.take("material-about", pg, clip=clip, marks=[(1, box_of(fr, "#a-version"))])
    fr.click('nav.rail a.rail__item[href*="/calc"]')
    fr.wait_for_load_state()



@scene("pena")
def pena_scene(app, ctx, sh, cam):
    fr = sh.tab("pena")
    E.pena_ready(fr)
    pg = sh.page
    clip = frame_box(sh, "pena")
    cam.take("pena-start", pg, clip=clip, marks=[
        (1, box_of(fr, ".appbar-nav"), "b"), (2, box_of(fr, ".appbar-title .appver"), "b"),
        (3, box_of(fr, "#conn"), "b")])
    if not fr.is_checked("#cbMode"):
        fr.check("#cbMode")
    fr.click('input[name=size][data-kind=cb][data-cb="5"]')
    E.wait_until(lambda: E.text(fr, "#lblSize1") and not fr.is_disabled("#weight1"), 8)
    fr.fill("#kensaNo", "w111111")
    fr.fill("#weight1", "10")
    fr.fill("#weight2", "12")
    cam.take("pena-weight", pg, clip=clip, marks=[
        (1, box_of(fr, "#cbMode")), (2, box_of(fr, 'input[name=size][data-kind=cb][data-cb="5"]')),
        (3, box_of(fr, "#kensaNo")), (4, box_of(fr, "#weight1")), (5, box_of(fr, "#weight2")),
        (6, box_of(fr, "button[data-act=applyWeight]"))])
    fr.click("button[data-act=applyWeight]")
    E.pena_toast(fr, "重量を反映しました")
    for i, n in enumerate(("11", "11", "10", "10"), 1):
        fr.fill(f"#coilH{i}", n)
    into_view(fr, "button[data-act=calcTare]")
    fr.click("button[data-act=calcTare]")
    E.pena_toast(fr, "計算しました")
    fr.wait_for_timeout(400)
    cam.take("pena-tare", pg, clip=clip, marks=[
        (1, box_of(fr, 'input[name=tip][value=TIP1000]')), (2, box_of(fr, "#coilH1")),
        (3, box_of(fr, "button[data-act=calcTare]")), (4, box_of(fr, "#nw1")),
        (5, box_of(fr, "button[data-act=printLabels]"))])
    labels = new_page(ctx, lambda: fr.click("button[data-act=printLabels]"))
    cam.take("pena-labels-print", labels, marks=[
        (1, box_of(labels, ".printbar [data-print-now]"), "r")])
    cam.take("pena-labels-print-note", labels, marks=[
        (1, box_of(labels, ".printbar [data-print-now]"), "r"),
        (2, box_of(labels, 'a.btn[href$="/labels/calibration"]'))])
    # 位置合わせ(同じタブで移る)
    labels.click('a.btn[href$="/labels/calibration"]')
    labels.wait_for_load_state()
    labels.wait_for_timeout(500)
    cal = labels
    cam.take("pena-calibration", cal, marks=[
        (1, box_of(cal.locator("button, a", has_text="試し刷り").first)),
        (2, box_of(cal.locator("button, a", has_text="ラベル印刷へ戻る").first))])
    if cal.locator("#calGap").count():
        into = cal.locator("#calGap")
        into.scroll_into_view_if_needed()
        cam.take("pena-calibration-fix", cal, marks=[
            (1, box_of(cal, "#calDirX")), (2, box_of(cal, "#calGapX")),
            (3, box_of(cal, "#calDirY")), (4, box_of(cal, "#calGapY")), (5, box_of(cal, "#calGap"))])
    labels.close()
    # 風袋計算の面(帯の「風袋計算」)
    fr.click('a[data-pane="tare"]')
    E.wait_until(lambda: fr.locator('a[href$="/tare/print"]').count() > 0, 10)
    fr.wait_for_timeout(500)
    cam.take("pena-tare-view", pg, clip=clip, marks=[
        (1, box_of(fr, 'a[data-pane="tare"]'), "b"), (2, box_of(fr, 'a[href$="/tare/print"]'))])
    tare = new_page(ctx, lambda: fr.click('a[href$="/tare/print"]'))
    cam.take("pena-tare-print", tare, marks=[(1, box_of(tare, ".printbar [data-print-now]"), "r")])
    tare.close()
    fr.click('a[data-pane="home"]')
    fr.wait_for_timeout(500)
    # 全サイズ
    fr.click('.appbar-nav a[href$="/all-size"]')
    fr.wait_for_load_state()
    fr = sh.frame("pena")
    E.pena_ready(fr)
    combos = fr.evaluate("[...document.querySelectorAll('#asCombo option')].map(o => o.value).filter(Boolean)")
    fr.select_option("#asCombo", combos[0])
    E.wait_until(lambda: sh.frame("pena").evaluate("document.readyState") == "complete"
                 and E.visible(sh.frame("pena"), "#asKen"), 10)
    fr = sh.frame("pena")
    E.pena_ready(fr)
    fr.fill("#asKen", "W123456")
    fr.check('input[name=asDigit][value="2"]')
    fr.fill("#asW1", "10")
    fr.fill("#asW2", "12")
    cam.take("pena-allsize", pg, clip=clip, marks=[
        (1, box_of(fr, "#asCombo")), (2, box_of(fr, "#asKen")),
        (3, box_of(fr, 'input[name=asDigit][value="2"]')), (4, box_of(fr, "#asW1")),
        (5, box_of(fr, "#asSubmit")), (6, box_of(fr, "#asPrint"))])
    fr.click("#asSubmit")
    fr.wait_for_load_state()
    E.wait_until(lambda: "反映値" in sh.frame("pena").evaluate("document.body.innerText"), 10)
    fr = sh.frame("pena")
    E.pena_ready(fr)
    sheet = new_page(ctx, lambda: fr.click("#asPrint"))
    cam.take("pena-allsize-print", sheet, marks=[(1, box_of(sheet, ".printbar [data-print-now]"), "r")])
    sheet.close()
    # 設定
    fr.click('.appbar-nav a[href$="/settings"]')
    fr.wait_for_load_state()
    fr = sh.frame("pena")
    E.wait_until(lambda: E.visible(fr, "#setPassword"), 10)
    fr.wait_for_timeout(600)
    cam.take("pena-settings", pg, clip=clip, marks=[
        (1, box_of(fr, "#setPassword")), (2, box_of(fr, ".subtab[data-sub=paths]"), "b"),
        (3, box_of(fr, ".subtab[data-sub=master]"), "b"), (4, box_of(fr, ".subtab[data-sub=dist]"), "b")])
    fr.locator("#storage").scroll_into_view_if_needed()
    fr.wait_for_timeout(300)
    cam.take("pena-settings-storage", pg, clip=clip, marks=[(1, box_of(fr, "#storage"), "l")])
    fr.click('a[data-pane="home"]') if fr.locator('a[data-pane="home"]').count() else None
    fr.wait_for_timeout(500)


# ======================================================================
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=("shell", "details", "pena", "material"), action="append")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    cam = Camera(out)
    home = Path(tempfile.mkdtemp(prefix="cpt-manual-"))
    E.make_sources(home)
    app = E.App(home)
    print("統合アプリを起動しています …", flush=True)
    if not app.start():
        print("起動できませんでした", file=sys.stderr)
        return 1
    versions = {k: str(v) for k, v in (app.health() or {}).get("versions", {}).items()}
    try:
        with sync_playwright() as p:
            exe = os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium")
            browser = p.chromium.launch(executable_path=exe if os.path.exists(exe) else None)
            ctx = browser.new_context(viewport=VIEW, locale="ja-JP", color_scheme="light")
            # 確かめ(window.confirm)は「OK」で進める(通し試験と同じ)
            ctx.on("page", lambda pg: pg.on("dialog", lambda d: d.accept(d.default_value or None)))
            # 開くと自動で印刷の窓を出す画面(風袋計算の印刷ビューなど)で止まらないように
            ctx.add_init_script("window.print = function () {};")
            sh = E.Shell(ctx, app)
            sh.ready()
            keys = args.only or list(SCENES)
            for key in keys:
                print(f"■ {key}", flush=True)
                SCENES[key](app, ctx, sh, cam)
            if "shell" in keys:
                write_meta(cam, versions, merge=True)      # 説明書が今回の写真を出すように先に書く
                shell_manual_shot(app, ctx, sh, cam)
            browser.close()
    finally:
        app.stop()
    write_meta(cam, versions, merge=bool(args.only))
    print(f"写真 {len(cam.images)} 枚 → {out / 'img'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
