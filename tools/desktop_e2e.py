#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""デスクトップ版(exe)を窓の中で操作する通し試験(開発用の PC・Linux)

    python tools/desktop_e2e.py --exe <CoilPackingTools のパス>

ブラウザ版の通し試験(`tools/e2e_scenarios.py`)のデスクトップ版。本物の exe を
仮想画面(Xvfb)で起動し、WebDriver(tauri-driver + WebKitWebDriver)で窓を操作する。
試験用の取り込み元は `e2e_scenarios.make_sources` と同じものを作る。

【確かめること】
1. 一連の流れ(3機能)と、**11 の印刷プレビューがすべて別の窓で開く**こと。
   どの窓でも「印刷する」が押せ(印刷の窓の代わりに呼ばれた回数を数える)、閉じられる
2. 画面の色が開いている別の窓にも届く・印刷の窓は白地のまま
3. 上の帯の「ログ」・包装仕様NOのコピー・閲覧システム(アプリの外)は窓の中で開かない
4. 画面を離れた合図(sendBeacon)が Python まで届く
5. どのプロセスもポートで待ち受けない(WebDriver を使わずに起動して確かめる。
   WebDriver で動かすあいだは、WebDriver の検査口が待ち受けるため)
6. 「終了」・窓の × は統合画面と同じ確かめを通る(キャンセルなら残る・OK で exe も Python も終わる)
7. 2つ目の exe は起動せずに終わる(1つ目はそのまま)
8. Python が見つからないとき・ブラウザ版が動いているときは、窓に理由が出る。
   デスクトップ版が動いているあいだ、ブラウザ版は起動しない

【要るもの】(現場の PC には要らない)
    Xvfb・WebKitWebDriver(webkit2gtk-driver)・tauri-driver(cargo install tauri-driver)
    exe は `cd src-tauri && cargo build`(target/debug/CoilPackingTools)
Windows の exe は GitHub Actions が `scripts/desktop_smoke.py` で確かめる。
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "scripts"))
import e2e_scenarios as E  # noqa: E402
from desktop_smoke import children, listening_ports  # noqa: E402

ELEM = "element-6066-11e4-a52e-4f735466cecf"
WD_PORT = 4444                               # 試験の道具だけが使う(アプリは使わない)
TITLE = "コイル梱包ツール"                      # 外枠の窓の名前(src-tauri/src/main.rs の TITLE)
results: list = []


def press_close_button(display: str, title: str) -> int:
    """窓の × を押したのと同じ合図(WM_DELETE_WINDOW)を X に送る。送った窓の数を返す。

    WebDriver の「窓を閉じる」は WebView を直接閉じるので、外枠の「閉じてよいか」
    (CloseRequested)を通らない。本物の × と同じ道を通すため、ウィンドウマネージャが
    送るのと同じ ClientMessage を ctypes で送る(Xvfb にはウィンドウマネージャが無い)。
    """
    found = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", f"^{title}$"],
                           env=dict(os.environ, DISPLAY=display),
                           capture_output=True, text=True).stdout.split()
    if not found:
        return 0

    class ClientMessage(ctypes.Structure):
        _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                    ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                    ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                    ("format", ctypes.c_int), ("l", ctypes.c_long * 5)]

    class XEvent(ctypes.Union):
        _fields_ = [("xclient", ClientMessage), ("pad", ctypes.c_long * 24)]

    x11 = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11"))
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                               ctypes.POINTER(XEvent)]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    dpy = x11.XOpenDisplay(display.encode())
    if not dpy:
        return 0
    try:
        protocols = x11.XInternAtom(dpy, b"WM_PROTOCOLS", 0)
        delete = x11.XInternAtom(dpy, b"WM_DELETE_WINDOW", 0)
        for wid in found:
            ev = XEvent()
            ev.xclient.type = 33                   # ClientMessage
            ev.xclient.window = int(wid)
            ev.xclient.message_type = protocols
            ev.xclient.format = 32
            ev.xclient.l[0] = delete
            ev.xclient.l[1] = 0                    # CurrentTime
            x11.XSendEvent(dpy, int(wid), 0, 0, ctypes.byref(ev))
        x11.XFlush(dpy)
    finally:
        x11.XCloseDisplay(dpy)
    return len(found)


def check(label, ok, detail=""):
    results.append((label, bool(ok)))
    print(f"  {'OK' if ok else 'NG'}  {label}  {str(detail)[:150]}", flush=True)


def until(fn, sec=10.0, step=0.25):
    end = time.time() + sec
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:                          # noqa: BLE001 - 読み込み中は待つ
            pass
        time.sleep(step)
    return False


# ======================================================================
# WebDriver(標準ライブラリだけで話す)
# ======================================================================
def wd(method, path, body=None, timeout=60):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{WD_PORT}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as res:
            return json.loads(res.read().decode() or "{}").get("value")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path}: {exc.read().decode()[:300]}") from None


class Window:
    """1つの WebDriver の窓口(exe を1つ起動する)。"""

    def __init__(self, exe: str):
        caps = {"capabilities": {"alwaysMatch": {"tauri:options": {"application": exe}}}}
        self.sid = wd("POST", "/session", caps, timeout=180)["sessionId"]
        self.main = self.handles()[0]

    def end(self):
        try:
            wd("DELETE", f"/session/{self.sid}", timeout=15)
        except Exception:                          # noqa: BLE001 - 先に終わっていてもよい
            pass

    def js(self, script, *args):
        return wd("POST", f"/session/{self.sid}/execute/sync", {"script": script, "args": list(args)})

    def el(self, css):
        return wd("POST", f"/session/{self.sid}/element", {"using": "css selector", "value": css})[ELEM]

    def click(self, css):
        wd("POST", f"/session/{self.sid}/element/{self.el(css)}/click", {})

    def type(self, css, text, clear=True):
        eid = self.el(css)
        if clear:
            wd("POST", f"/session/{self.sid}/element/{eid}/clear", {})
        wd("POST", f"/session/{self.sid}/element/{eid}/value", {"text": text})

    def frame(self, css=None):
        wd("POST", f"/session/{self.sid}/frame", {"id": None if css is None else {ELEM: self.el(css)}})

    def handles(self):
        return wd("GET", f"/session/{self.sid}/window/handles")

    def switch(self, handle):
        wd("POST", f"/session/{self.sid}/window", {"handle": handle})

    def close_current(self):
        return wd("DELETE", f"/session/{self.sid}/window")

    def accept(self):
        try:
            wd("POST", f"/session/{self.sid}/alert/accept", {})
            return True
        except RuntimeError:
            return False

    def dismiss(self):
        try:
            wd("POST", f"/session/{self.sid}/alert/dismiss", {})
            return True
        except RuntimeError:
            return False

    def shot(self, path: Path):
        path.write_bytes(base64.b64decode(wd("GET", f"/session/{self.sid}/screenshot")))

    def text(self, css):
        return self.js("var e=document.querySelector(arguments[0]);"
                       "return e?(e.value!==undefined&&e.tagName!=='BUTTON'?e.value:e.textContent).trim():''",
                       css) or ""

    def tab(self, key):
        self.switch(self.main)
        self.frame(None)
        self.click(f"#tab-{key}")
        self.frame(f"#pane-{key} iframe")


class Rig:
    """仮想画面・tauri-driver・試験用の置き場所。"""

    def __init__(self, exe: str, display: str, shots: Path):
        self.exe, self.display, self.shots = exe, display, shots
        self.home = Path(tempfile.mkdtemp(prefix="cpt-desk-e2e-"))
        E.make_sources(self.home)
        self.app = E.App(self.home)               # ブラウザ版と同じ置き場所・取り込み元
        self.xvfb = subprocess.Popen(["Xvfb", display, "-screen", "0", "1600x1000x24"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        self.driver = None

    def env(self, **extra):
        env = dict(self.app.env, DISPLAY=self.display, COIL_PACKING_TOOLS_ROOT=str(ROOT),
                   COIL_PACKING_TOOLS_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE="1",
                   WEBKIT_DISABLE_COMPOSITING_MODE="1")
        env.update(extra)
        return env

    def start_driver(self, **extra):
        self.stop_driver()
        self.driver = subprocess.Popen(
            ["tauri-driver", "--port", str(WD_PORT), "--native-driver", shutil.which("WebKitWebDriver")],
            env=self.env(**extra), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        until(lambda: urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            f"http://127.0.0.1:{WD_PORT}/status", timeout=2), 15)

    def stop_driver(self):
        if self.driver and self.driver.poll() is None:
            self.driver.terminate()
            try:
                self.driver.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.driver.kill()
        self.driver = None

    def exe_pids(self):
        return [int(p) for p in os.listdir("/proc") if p.isdigit()
                and Path(f"/proc/{p}/comm").exists()
                and Path(f"/proc/{p}/comm").read_text().strip().startswith("CoilPacking")]

    def bridge_pids(self):
        out = []
        for p in os.listdir("/proc"):
            if p.isdigit():
                try:
                    if b"bridge.py" in Path(f"/proc/{p}/cmdline").read_bytes():
                        out.append(int(p))
                except OSError:
                    pass
        return out

    def log_text(self):
        logs = sorted((self.home / "local" / "logs").glob("*.log"))
        return logs[-1].read_text(encoding="utf-8", errors="replace") if logs else ""

    def close(self):
        self.stop_driver()
        self.xvfb.terminate()


# ======================================================================
# 印刷プレビュー(11)
# ======================================================================
STUB = "window.__printed=0; window.print=function(){window.__printed++}"


def print_window(w: Window, rig: Rig, label: str, opener, path_part: str, button: str, shot=""):
    """`opener()` で別の窓が開き、「印刷する」が押せ、閉じられる。"""
    before = len(w.handles())
    opener()
    opened = until(lambda: len(w.handles()) == before + 1, 12)
    if not opened:
        check(f"印刷プレビュー: {label}", False, "窓が開かない")
        return
    new = [h for h in w.handles() if h != w.main][-1]
    w.switch(new)
    until(lambda: path_part in w.js("return location.pathname") and
          w.js("return document.readyState") == "complete", 12)
    w.js(STUB)                                     # 開いたとき自分で刷る画面もある。それは数えない
    found = w.js("return document.querySelectorAll(arguments[0]).length", button)
    name = w.text(button)
    clicked = 0
    if found == 1:
        w.click(button)
        clicked = w.js("return window.__printed")
    if shot:
        w.shot(rig.shots / f"desktop_{shot}.png")
    w.js("window.close()")
    closed = until(lambda: len(w.handles()) == before, 8)
    check(f"印刷プレビュー: {label}",
          found == 1 and name == "印刷する" and clicked == 1 and closed,
          {"道": w_path(path_part), "名前": name, "印刷": clicked, "閉じた": closed})
    if not closed:
        w.close_current()
    w.switch(w.main)


def w_path(part):
    return part


# ======================================================================
# 段
# ======================================================================
def scenario_main(rig: Rig):
    print("■ 1. 窓の中で3機能を使う(印刷プレビュー11・画面の色・ログ・終了)")
    rig.start_driver()
    w = Window(rig.exe)
    try:
        ok = until(lambda: w.js("return document.querySelectorAll('iframe.pane__frame').length") == 3
                   and w.text("#conn") == "接続OK", 60)
        check("窓に統合画面と3機能・接続OK", ok, w.text("#conn"))
        token = w.js("return window.SHELL && window.SHELL.token")

        # ---- 静的ファイルは外枠(Rust)が返す
        w.frame("#pane-details iframe")
        served = w.js("""return Promise.all([...document.querySelectorAll('link[rel=stylesheet],script[src]')]
            .map(e=>e.href||e.src).filter(u=>u.includes('/static/'))
            .map(u=>fetch(u).then(r=>r.headers.get('X-Served-By'))))""")
        check("CSS・JS は外枠(Rust)が返す", served and all(s == "desktop" for s in served), served)

        # ---- 梱包明細
        w.type("#lotNo", "L5160Z0")
        check("梱包明細: ロットが引ける", until(lambda: w.text("#warisu"), 15), w.text("#warisu"))
        w.type("#weight1", "250")
        w.type("#weight2", "248\t")
        time.sleep(0.6)
        w.click("#btnStrands")
        until(lambda: w.js("return !document.querySelector('#strandCard').hidden"), 8)
        w.type("#stackMax", "4\t")
        until(lambda: w.js("return document.querySelectorAll('#stack button.slot').length") == 4, 5)
        for key in ("1-12", "1-11", "2-12", "2-11"):
            w.click(f'#grid button.coil[data-key="{key}"]')
            time.sleep(0.2)
        w.click("#btnOutput")
        time.sleep(0.6)
        if w.js("var d=document.querySelector('#confirmDialog');return !!(d&&d.open)"):
            w.click('#confirmDialog button[value="yes"]')
        check("梱包明細: 出力できる", until(lambda: "出力しました" in w.text("#notice"), 10),
              w.text("#notice"))
        print_window(w, rig, "梱包明細 明細表(1枚)", lambda: w.click("#btnPrint"),
                     "/details/report/L5160Z0/1", "p.editbar button.print", "details_report")
        w.tab("details")
        print_window(w, rig, "梱包明細 明細表(まとめて)",
                     lambda: w.js(f"window.open('/details/report/L5160Z0?nos=1&t={token}','_blank','noopener')"),
                     "/details/report/L5160Z0", "p.editbar button.print")
        hid = ""
        for db in rig.home.rglob("packing_details.db"):
            with sqlite3.connect(str(db)) as conn:
                row = conn.execute("SELECT 履歴ID FROM 明細出力 WHERE 履歴ID != '' LIMIT 1").fetchone()
            if row:
                hid = row[0]
        w.tab("details")
        print_window(w, rig, "梱包明細 履歴から作り直した紙面",
                     lambda: w.js(f"window.open('/details/report/history?ids={hid}&t={token}','_blank','noopener')"),
                     "/details/report/history", "p.editbar button.print")

        # ---- ペナラベル
        w.tab("pena")
        until(lambda: w.text("#conn") == "接続OK", 15)
        if not w.js("return document.querySelector('#cbMode').checked"):
            w.click("#cbMode")
        w.click('input[name=size][data-kind=cb][data-cb="5"]')
        until(lambda: w.text("#lblSize1") and not w.js("return document.querySelector('#weight1').disabled"), 8)
        w.type("#kensaNo", "w111111")
        w.type("#weight1", "10")
        w.type("#weight2", "12")
        w.click("button[data-act=applyWeight]")
        check("ペナラベル: 重量反映", until(lambda: "重量を反映しました" in w.text("#toast .toast-body"), 10))
        for i, n in enumerate(("11", "11", "10", "10"), 1):
            w.type(f"#coilH{i}", n)
        w.click("button[data-act=calcTare]")
        check("ペナラベル: 風袋計算", until(lambda: w.text("#nw1") == "110.0", 10), w.text("#nw1"))

        def labels():
            w.click("button[data-act=printLabels]")
            time.sleep(0.8)
            w.accept()
        print_window(w, rig, "ペナラベル 小ラベルの印刷(実寸)", labels, "/pena/labels/print",
                     ".printbar [data-print-now]", "pena_labels")
        for label, path in (("風袋計算の印刷ビュー", "/pena/tare/print"),
                            ("羅列計算の印刷ビュー", "/pena/list/print"),
                            ("ラベル台紙の印刷ビュー", "/pena/labels/sheet?ob=5"),
                            ("位置合わせ(試し刷り)", "/pena/labels/calibration"),
                            ("全サイズの印刷ビュー", "/pena/all-size/print?combo=x")):
            w.tab("pena")
            print_window(w, rig, f"ペナラベル {label}",
                         lambda p=path: w.js(f"window.open('{p}','_blank','noopener')"),
                         path.split("?")[0], ".printbar [data-print-now]")

        # ---- 画面の色: 別の窓にも届く・印刷の窓は白地のまま
        w.tab("pena")
        before = len(w.handles())
        w.js("window.open('/pena/tare/print','_blank','noopener')")
        until(lambda: len(w.handles()) == before + 1, 10)
        other = [h for h in w.handles() if h != w.main][-1]
        w.switch(w.main)
        w.frame(None)
        w.click('[data-theme-set="dark"]')
        time.sleep(0.6)
        dark_main = w.js("return document.documentElement.getAttribute('data-theme')")
        w.switch(other)
        got = w.js("return document.documentElement.getAttribute('data-theme')")
        bg = w.js("return getComputedStyle(document.body).backgroundColor")
        check("画面の色: 開いている別の窓にも届く", dark_main == "dark" and got == "dark", got)
        check("画面の色: 印刷の窓は白地のまま", bg == "rgb(255, 255, 255)", bg)
        w.js("window.close()")
        until(lambda: len(w.handles()) == before, 8)
        w.switch(w.main)
        w.frame(None)
        w.click('[data-theme-set="auto"]')

        # ---- 資材計算
        w.tab("material")
        until(lambda: w.js("var g=document.querySelector('#gate');return !g||g.hidden"), 15)
        opts = w.js("return [...document.querySelectorAll('#rb-worker option')].map(o=>o.value).filter(Boolean)")
        if opts:
            w.js("var e=document.querySelector('#rb-worker');e.value=arguments[0];"
                 "e.dispatchEvent(new Event('change',{bubbles:true}))", opts[0])
            time.sleep(0.8)
        w.type("#lot", "A123456")
        check("資材計算: ロットが引ける", until(lambda: w.text("#o-包装") == E.SPEC_NO, 12))
        w.type("#ins", "10\t")
        time.sleep(0.8)
        w.click("#run")
        check("資材計算: 計算できる", until(lambda: w.text("#r-台数") == "2", 10),
              (w.text("#r-種類"), w.text("#r-台数")))
        before = len(w.handles())
        href = w.js("return location.href")
        w.click("#o-包装")
        time.sleep(1.5)
        check("資材計算: 閲覧システム(アプリの外)は窓の中で開かない",
              len(w.handles()) == before and w.js("return location.href") == href)
        check("資材計算: 包装仕様NOのコピー", "コピーしました" in w.text("#toast"), w.text("#toast"))
        w.click("#size-fixed")
        w.click("#add")
        time.sleep(1.0)
        w.click('nav.rail a.rail__item[href*="/checklist"]')
        until(lambda: "/checklist" in w.js("return location.pathname") and w.text("#sub").startswith("1 /"), 12)
        print_window(w, rig, "資材計算 チェックリスト", lambda: w.click("a#print"),
                     "/material/report/checklist", "#printNow", "material_checklist")
        w.tab("material")
        w.click("a#to-order")
        until(lambda: "/order" in w.js("return location.pathname"), 10)
        until(lambda: w.js("var g=document.querySelector('#gate');return !g||g.hidden"), 15)
        w.click("#build")
        until(lambda: not w.js("return document.querySelector('a#print').hidden"), 10)
        print_window(w, rig, "資材計算 発注票", lambda: w.click("a#print"),
                     "/material/report/order", "#printNow")

        # ---- ログ・画面を離れた合図
        w.switch(w.main)
        w.frame(None)
        w.click("#logBtn")
        check("上の帯の「ログ」が開く", until(lambda: w.js(
            "var f=document.querySelector('#logBody iframe');"
            "return !!(f&&f.contentDocument&&f.contentDocument.querySelector('#incRows'))"), 10))
        w.click("#logClose")
        w.tab("material")
        sent = w.js("return navigator.sendBeacon('/api/client-log', new Blob([JSON.stringify("
                    "{kind:'offline', message:'desktop-beacon', source:'/probe', count:1})],"
                    "{type:'application/json'}))")
        check("画面を離れた合図(sendBeacon)が Python まで届く",
              sent and until(lambda: "desktop-beacon" in rig.log_text(), 5))
        w.switch(w.main)
        w.frame(None)
        w.shot(rig.shots / "desktop_main.png")

        # ---- 「終了」: 確かめで「キャンセル」なら終わらない・「OK」で終わる
        exe = rig.exe_pids()
        w.click("#quit")
        time.sleep(0.8)
        dismissed = w.dismiss()
        time.sleep(1.5)
        check("「終了」の確かめで「キャンセル」なら終わらない",
              dismissed and all(Path(f"/proc/{p}").exists() for p in exe)
              and w.text("#conn") == "接続OK", w.text("#conn"))
        w.click("#quit")
        time.sleep(0.8)
        w.accept()
        gone = until(lambda: not any(Path(f"/proc/{p}").exists() for p in exe), 15)
        check("「終了」で exe も Python も終わる", gone and not rig.bridge_pids(), rig.bridge_pids())
    finally:
        w.end()
    bad = [line for line in rig.log_text().splitlines() if "| ERROR |" in line or "Traceback" in line]
    check("ログに ERROR・Traceback が無い", not bad, bad[:2])


def scenario_close_and_second(rig: Rig):
    print("■ 2. 窓の × ・2つ目の起動")
    rig.start_driver()
    w = Window(rig.exe)
    try:
        until(lambda: w.text("#conn") == "接続OK", 60)
        first = rig.exe_pids()
        # 2つ目: 起動せずに終わる(1つ目はそのまま)
        second = subprocess.Popen([rig.exe], env=rig.env(), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        try:
            code = second.wait(timeout=20)
        except subprocess.TimeoutExpired:
            second.kill()
            code = "終わらない"
        check("2つ目の exe は起動せずに終わる", code == 0, code)
        check("1つ目はそのまま使える", all(Path(f"/proc/{p}").exists() for p in first)
              and w.text("#conn") == "接続OK", w.text("#conn"))
        # 窓の ×: 統合画面の「終了」と同じ確かめ(キャンセルなら残る・OK で終わる)
        sent = press_close_button(rig.display, TITLE)
        dismissed = until(w.dismiss, 10)
        time.sleep(4.0)                           # 外枠は3秒待って、受け取られなければ終える
        check("窓の × で確かめが出て「キャンセル」なら終わらない",
              dismissed and all(Path(f"/proc/{p}").exists() for p in first)
              and w.text("#conn") == "接続OK", {"送った窓": sent, "確かめ": dismissed})
        sent = press_close_button(rig.display, TITLE)
        accepted = until(w.accept, 10)
        gone = until(lambda: not any(Path(f"/proc/{p}").exists() for p in first), 15)
        check("窓の × で「OK」なら exe も Python も終わる",
              accepted and gone and not rig.bridge_pids(),
              {"確かめ": accepted, "残り": rig.bridge_pids()})
    finally:
        w.end()


def scenario_ports(rig: Rig):
    print("■ 4. ポートで待ち受けない(WebDriver を使わずに起動して確かめる)")
    work_log = rig.home / "local" / "logs"
    mark = len(rig.log_text())
    proc = subprocess.Popen([rig.exe], env=rig.env(), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        seen = until(lambda: '"GET /details/meisai' in rig.log_text()[mark:], 60)
        check("画面が Python から届く(統合画面の中の梱包明細まで)", seen)
        tree = {proc.pid, *children(proc.pid)}
        ports = listening_ports(tree)
        check("どのプロセスもポートで待ち受けない(exe・Python・窓)", tree and not ports,
              {"プロセス": len(tree), "待ち受け": ports})
        others = [p for p in tree if p != proc.pid]
    finally:
        proc.terminate()
        proc.wait(timeout=30)
    left = until(lambda: not any(Path(f"/proc/{p}").exists() for p in others), 20)
    check("exe を止めたら Python も終わる(取り残さない)", left)
    assert work_log


def scenario_failures(rig: Rig):
    print("■ 3. 起動できないとき・ブラウザ版とのすみ分け")
    # Python が見つからない
    rig.start_driver(COIL_PACKING_TOOLS_PYTHON="/nonexistent/python3")
    w = Window(rig.exe)
    try:
        ok = until(lambda: "起動できませんでした" in w.js("return document.body.innerText"), 30)
        body = w.js("return document.body.innerText")
        check("Python が見つからないとき、理由と入れ方が窓に出る",
              ok and "Python" in body and "python.org" in body, body[:120])
        w.shot(rig.shots / "desktop_no_python.png")
    finally:
        w.end()
    # ブラウザ版が動いている → デスクトップ版は理由を出す
    h = rig.app.start()
    check("(準備)ブラウザ版を起動", h, (h or {}).get("version_set"))
    rig.start_driver()
    w = Window(rig.exe)
    try:
        ok = until(lambda: "ブラウザ版" in w.js("return document.body.innerText"), 40)
        check("ブラウザ版が動いていれば、デスクトップ版は理由を出して使わせない", ok,
              w.js("return document.body.innerText")[:120])
    finally:
        w.end()
    rig.app.stop()
    until(lambda: not rig.app.alive(), 20)
    # デスクトップ版が動いている → ブラウザ版は起動しない
    rig.start_driver()
    w = Window(rig.exe)
    try:
        until(lambda: w.text("#conn") == "接続OK", 60)
        h = rig.app.start(timeout=40)
        check("デスクトップ版が動いているあいだ、ブラウザ版は起動しない",
              not h and "デスクトップ版のコイル梱包ツールが動いています" in rig.log_text(), h)
        w.click("#quit")
        time.sleep(0.8)
        w.accept()
    finally:
        w.end()
        rig.app.stop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exe", required=True, help="CoilPackingTools(cargo build で作ったもの)")
    ap.add_argument("--only", choices=("main", "close", "ports", "failures"))
    ap.add_argument("--shots", default="", help="画面の写しを置くフォルダ")
    ap.add_argument("--display", default=":97")
    args = ap.parse_args(argv)
    for tool in ("Xvfb", "tauri-driver", "WebKitWebDriver"):
        if not shutil.which(tool):
            print(f"{tool} がありません(説明はこの台本の先頭)")
            return 2
    shots = Path(args.shots) if args.shots else Path(tempfile.mkdtemp(prefix="cpt-desk-shots-"))
    shots.mkdir(parents=True, exist_ok=True)
    rig = Rig(str(Path(args.exe).resolve()), args.display, shots)
    print("=" * 80)
    print("置き場所:", rig.home)
    try:
        if args.only in (None, "main"):
            scenario_main(rig)
        if args.only in (None, "close"):
            scenario_close_and_second(rig)
        if args.only in (None, "ports"):
            scenario_ports(rig)
        if args.only in (None, "failures"):
            scenario_failures(rig)
    finally:
        rig.close()
    print("=" * 80)
    ng = [r for r in results if not r[1]]
    print("結果: %d 項目中 %d 件 NG" % (len(results), len(ng)))
    if not ng:
        shutil.rmtree(rig.home, ignore_errors=True)
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
