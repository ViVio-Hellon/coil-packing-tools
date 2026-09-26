"""印刷の「紙の端から 5mm 以上内側」を、統合版の本物の紙面で測る(開発用・手動)。

    python tools/print_edge_check.py            # 測って表を出す。5mm 未満があれば 1 で終わる
    python tools/print_edge_check.py --keep DIR # 作った PDF を DIR に残す

【なぜ要るのか】
プリンターは紙の縁 約 4mm に刷れない。ブラウザの印刷プレビューは紙の端まで描くので、
画面で収まって見えても紙では欠ける。**文字・罫線・バー・目盛りはすべて紙の端から
5mm 以上内側に置く**(3機能の決まり)。CSS を読むだけでは、`@page` の打ち消し合い
(ペナラベルで実際に起きていた)や、文章の折り返しで端へ出る文字は分からないので、
刷ったものを測る。

【どう測るか】
統合アプリをこのプロセスで立て(置き場所は試験用に隔離)、3機能の紙面に中身を入れて
から Chromium で PDF にし、300dpi の画像にして**インク(白でない点)**が紙の端から
何 mm にあるかを測る。

- 印刷ダイアログは「余白なし」と同じ条件(`@page` の余白を 0 に上書き)── いちばん厳しい
- 背景のグラフィックも刷る(背景で描いた線・バーも拾う)
- 左半分に刷って右半分を切り取る紙(梱包明細の帳票・資材計算の発注票)は、
  **真ん中の切り目も紙の端**として測る(真ん中の切り取り線そのものは数えない)
- 位置合わせの試し刷りは、台紙の切れ目を示す点線の枠・段の赤い線・端からの道しるべを
  除いて測る(紙の端と重なる位置そのものが意味を持つ目印。縁の分は刷れなくてよい)

【要るもの(開発用の PC だけ)】
Playwright(`tools/smoke_shell.py` と同じ)と PyMuPDF(`pip install pymupdf`)。
現場の PC には要らない(`tools\\` は配布用フォルダに入らない)。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DPI = 300
MM_PER_PX = 25.4 / DPI
SAFE = 5.0
TOL = 0.1          # 線の縁のにじみ(アンチエイリアス)ぶん
CUT_BAND_MM = 1.5  # 真ん中の切り取り線とみなす幅(±)

#: 試し刷りで位置を変えない目印(紙の端と重なる)
CALIBRATION_GUIDES = (".label.guide{outline:none !important; border:none !important}"
                      ".rowline,.edgemark{display:none !important}")


def _need(module: str, hint: str):
    try:
        return __import__(module)
    except ImportError:
        print("%s がありません。%s" % (module, hint))
        sys.exit(2)


class _Case:
    def addCleanup(self, *a, **k):
        pass


def _build_app():
    import tests._env  # noqa: F401  (置き場所を試験用に隔離する)
    from tests.test_app import TOKEN, _make_app
    app = _make_app(_Case())
    app.config["READY"] = True
    from modules import packing_pena_label as pena

    class _Report:
        def stage(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass
    pena.initialize(_Report())
    return app, TOKEN


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(base, path, body, token):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Tool-Token": token})
    with _opener().open(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _seed(base, token):
    """3機能の紙面に中身を入れる。戻り値は後片付け。"""
    # ペナラベル: 画面と同じ API を順に(丈1・丈2 → 風袋 → 羅列 → ラベルの対象)
    cur = {"selectedCb": 5, "tip": "TIP1000", "kensaNo": "w111111",
           "weight1": "10", "weight2": "12", "coilH": {}}
    _post(base, "/pena/api/select-checkbox", {"cbIdx": 5, "current": cur}, token)
    _post(base, "/pena/api/apply-weight", {"current": cur}, token)
    cur["coilH"] = {"1": "11", "2": "11", "3": "10", "4": "10"}
    _post(base, "/pena/api/calc-tare", {"current": cur}, token)
    _post(base, "/pena/api/calc-list", {"current": cur}, token)
    targets = _post(base, "/pena/api/print-targets", {"current": cur}, token)["targets"]
    # ペナラベル: 全サイズ(選べるサイズの最初のもの)
    html = _opener().open(base + "/pena/all-size?t=" + token, timeout=30).read().decode("utf-8")
    at = html.index('id="asCombo"')
    combos = [c for c in re.findall(r'<option value="([^"]*)"',
                                    html[at:html.index("</select>", at)]) if c]
    combo = combos[0] if combos else ""
    if combo:
        _post(base, "/pena/api/all-size/prepare", {"comboText": combo}, token)
        _post(base, "/pena/api/all-size/submit",
              {"comboText": combo, "kensaNo": "W123456", "threeDigit": False,
               "w1": "10", "w2": "12", "w4": "11", "w5": "13"}, token)
    # 梱包明細: 出力済みの明細1枚(条×本を多めに)
    from modules.packing_details.app.routes import meisai as details_routes
    from modules.packing_details.meisai.meisai_service import Output
    keys = ["%d-%d" % (j, k) for j in range(1, 5) for k in range(1, 11)]
    out = Output(lot_no="L5160Z0", seq_no=1, keys=keys, weights=[250, 248, 251, 249])
    patch = mock.patch.object(details_routes.meisai_service, "find_output", return_value=out)
    patch.start()
    # 資材計算: 発注票2枚(試験と同じ作り方)
    from modules.packing_material_calculation.app.routes import order as order_routes
    from modules.packing_material_calculation.tests.test_checklist_and_order import _sheet
    order_routes._pending["sheets"] = [_sheet("30×40"), _sheet("40×60")]
    pages = [
        ("梱包明細 帳票", f"/details/report/L5160Z0/1?t={token}", True, ""),
        ("資材計算 チェックリスト", f"/material/report/checklist?t={token}", False, ""),
        ("資材計算 発注票", f"/material/report/order?t={token}", True, ""),
        ("ペナラベル ラベル台紙", "/pena/labels/print?ob=" + ",".join(map(str, targets)), False, ""),
        ("ペナラベル 風袋計算の印刷ビュー", "/pena/tare/print", False, ""),
        ("ペナラベル 羅列計算の印刷ビュー", "/pena/list/print", False, ""),
        ("ペナラベル 全サイズの印刷ビュー",
         "/pena/all-size/print?combo=" + urllib.parse.quote(combo), False, ""),
        ("ペナラベル 風袋計算(このまま印刷)", "/pena/tare", False, ""),
        ("ペナラベル 羅列計算(このまま印刷)", "/pena/list", False, ""),
        ("ペナラベル 位置合わせの試し刷り(目印の線を除く)", "/pena/labels/calibration", False,
         CALIBRATION_GUIDES),
    ]
    return pages, patch.stop


def _ink(pdf: Path, fitz) -> list:
    """各ページのインクの端からの距離(mm)。"""
    table = bytes(0 if v < 245 else 255 for v in range(256))
    out = []
    for page in fitz.open(str(pdf)):
        pix = page.get_pixmap(dpi=DPI, alpha=False)
        w, h, n = pix.width, pix.height, pix.n
        data = pix.samples.translate(table)
        stride = w * n
        mid, band = w // 2, int(CUT_BAND_MM / MM_PER_PX)
        top = bottom = None
        left, right, left_half_right = w, -1, -1
        for y in range(h):
            row = data[y * stride:(y + 1) * stride]
            i = row.find(b"\x00")
            if i < 0:
                continue
            top = y if top is None else top
            bottom = y
            left = min(left, i // n)
            right = max(right, row.rfind(b"\x00") // n)
            k = row.rfind(b"\x00", 0, (mid - band) * n)
            if k >= 0:
                left_half_right = max(left_half_right, k // n)
        pw, ph = page.rect.width * 25.4 / 72, page.rect.height * 25.4 / 72
        if top is None:
            out.append(None)
            continue
        out.append({
            "左": left * MM_PER_PX, "上": top * MM_PER_PX,
            "右": pw - (right + 1) * MM_PER_PX, "下": ph - (bottom + 1) * MM_PER_PX,
            "切り目": pw / 2 - (left_half_right + 1) * MM_PER_PX,
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", help="作った PDF を残すフォルダ")
    args = ap.parse_args(argv)
    fitz = _need("fitz", "開発用の PC で pip install pymupdf してください(現場の PC には要りません)")
    _need("playwright", "tools/smoke_shell.py と同じく Playwright が要ります")
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    out_dir = Path(args.keep).resolve() if args.keep else Path(tempfile.mkdtemp(prefix="cpt-edge-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    app, token = _build_app()
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % srv.server_port
    pages, cleanup = _seed(base, token)

    bad = 0
    try:
        with sync_playwright() as pw:
            exe = os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium")
            br = pw.chromium.launch(executable_path=exe if os.path.exists(exe) else None)
            ctx = br.new_context()
            ctx.add_init_script("window.print = function () {};")   # 印刷ダイアログは出さない
            for n, (name, path, half, hide) in enumerate(pages):
                pg = ctx.new_page()
                res = pg.goto(base + path, wait_until="load")
                pg.wait_for_timeout(1200)
                pg.add_style_tag(content="@page { margin: 0 !important; }" + hide)   # 余白なし
                pdf = out_dir / ("edge_%02d.pdf" % n)
                pg.pdf(path=str(pdf), prefer_css_page_size=True, print_background=True, format="A4")
                pg.close()
                print("■ %s  %s  HTTP %s" % (name, path.split("?")[0], res.status if res else "-"))
                for i, p in enumerate(_ink(pdf, fitz)):
                    if p is None:
                        print("   %d枚目: 白紙" % (i + 1))
                        continue
                    keys = ("左", "上", "切り目", "下") if half else ("左", "上", "右", "下")
                    worst = min(p[k] for k in keys)
                    ok = worst >= SAFE - TOL
                    bad += 0 if ok else 1
                    print("   %d枚目  %s  → 最小 %.2fmm %s" % (
                        i + 1, " ".join("%s %.2f" % (k, p[k]) for k in keys), worst,
                        "OK" if ok else "★ 5mm 未満"))
            br.close()
    finally:
        cleanup()
        srv.shutdown()
    print("=" * 60)
    print("5mm 未満: %d 枚" % bad + ("" if args.keep else "(PDF は %s)" % out_dir))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
