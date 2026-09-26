#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通しの動作確認(手動)── 起動から停止までを本物の経路で通す

自動テスト(`python -m pytest tests modules/*/tests`)は中身の正しさを見るもので、
`start_app.py` → waitress → ブラウザ → 3つの iframe → 心拍 → 停止 を
本物の道筋で通すことはしない。配布前に一度これを流す。

    python tools/smoke_shell.py            # Chromium(playwright)があれば画面も確かめる
    python tools/smoke_shell.py --no-browser

確かめること
    起動(start_app.py)/ 2重起動は既存へ合流 / 統合画面に3タブ /
    各機能の画面が iframe で開く / タブを切り替えても入力が残る /
    外枠と各機能の心拍が届く / 停止(process_manager.py)
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("  %s %-44s %s" % ("OK " if ok else "NG ", name, detail))


def main(argv=None) -> int:
    no_browser = "--no-browser" in (argv or sys.argv[1:])
    home = Path(tempfile.mkdtemp(prefix="cpt-smoke-"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    conf = home / "app.json"
    conf.write_text(json.dumps({
        "app_id": "nlm.coil-packing-tools", "display_name": "コイル梱包ツール",
        "version": "1.0.0", "local_dir_name": "CoilPackingTools",
        "server": {"host": "127.0.0.1", "port_retry": 0, "roles": {"main": {"port": port}}},
    }, ensure_ascii=False), encoding="utf-8")
    local = home / "local"
    env = dict(os.environ,
               COIL_PACKING_TOOLS_APP_CONFIG=str(conf),
               COIL_PACKING_TOOLS_LOCAL_DIR=str(local),
               XDG_DATA_HOME=str(home / "xdg-data"), XDG_STATE_HOME=str(home / "xdg-state"),
               LOCALAPPDATA=str(home / "localappdata"),
               PACKING_DETAILS_SHARED_DIR=str(home / "share"),
               PACKING_DETAILS_LOT_DB_DIR=str(home / "lot"), PACKING_DETAILS_KONPO_DB_DIR=str(home / "konpo"),
               COIL_TOOL_MASTER_DB_DIR=str(home / "master"), COIL_TOOL_LOT_DB_DIR=str(home / "lot"),
               PPL_PREFER_ACCESS="0")
    base = "http://127.0.0.1:%d" % port
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path, headers=None):
        req = urllib.request.Request(base + path, headers=headers or {})
        with opener.open(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")

    def post(path, payload, headers=None):
        """(HTTP番号, 本文)。断り(4xx)も本文ごと返す。"""
        req = urllib.request.Request(base + path, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with opener.open(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except Exception:
                return exc.code, {}

    print("=" * 76)
    print("■ 起動(Start.vbs と同じ経路 = start_app.py)")
    proc = subprocess.Popen([sys.executable, str(ROOT / "start_app.py"), "--no-browser"],
                            cwd=str(home), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    health = None
    for _ in range(300):
        try:
            _, body = get("/api/health"); health = json.loads(body)
            if health.get("ready"): break
        except Exception:
            pass
        time.sleep(0.3)
    check("起動して準備完了になる", bool(health and health.get("ready")),
          "%.0f秒" % 0 if not health else "stage=%s" % health.get("stage"))
    check("3機能が載っている", bool(health) and [m["key"] for m in health.get("modules", [])] == ["details", "pena", "material"])
    lock = json.loads((local / "runtime" / "main.lock").read_text(encoding="utf-8"))
    token = lock["token"]
    check("ロックにトークンと pid がある", bool(token) and lock["pid"] == proc.pid, "pid=%s" % lock["pid"])

    print("■ 2重起動は既存へ合流する")
    p2 = subprocess.run([sys.executable, str(ROOT / "start_app.py"), "--no-browser"], cwd=str(home), env=env,
                        capture_output=True, text=True, timeout=120)
    check("2台目は既存画面へ渡す", p2.returncode == 0 and "すでに起動" in p2.stdout, p2.stdout.strip().splitlines()[-1][:60] if p2.stdout.strip() else p2.stderr[-200:])

    print("■ 画面と API")
    st, html = get("/?t=" + token)
    check("統合画面が出る", st == 200 and html.count("<iframe") == 3)
    for path, mark in (("/details/meisai?t=" + token, "コイル梱包明細"), ("/pena/", "梱包ペナ"), ("/material/calc?t=" + token, "資材計算")):
        st, html = get(path); check("開ける: " + path.split("?")[0], st == 200 and mark in html)
    hdr = {"X-Tool-Token": token, "X-Tool-Screen": "smoke"}
    st, body = post("/details/api/screen/claim", {"screen": "smoke"}, hdr)
    check("梱包明細の画面を名乗れる", st == 200, str(body)[:60])
    st, body = post("/details/api/lot", {"lot_no": "L5160Z0"}, hdr)
    check("梱包明細 API がトークンで通る(業務の断りは可)", st not in (401, 403, 409), "HTTP %s %s" % (st, str(body)[:50]))
    st, body = post("/details/api/lot", {"lot_no": "L5160Z0"})
    check("梱包明細 API はトークン無しで断る(401)", st == 401, "HTTP %s" % st)
    st, body = post("/material/api/calc/lot", {"LOT": "A123456"})
    check("資材計算 API はトークン無しで断る(403)", st == 403, "HTTP %s" % st)
    st, body = post("/material/api/calc/lot", {"LOT": "A123456"}, {"X-App-Token": token})
    check("資材計算 API がトークンで通る", st == 200, "HTTP %s" % st)
    st, body = post("/pena/api/state", {})
    check("ペナラベル API はトークン無しで断る(403)", st == 403, "HTTP %s" % st)
    st, body = post("/pena/api/state", {}, {"X-Tool-Token": token})
    check("ペナラベル API がトークンで通る", st == 200 and body.get("ok"))
    st, body = post("/api/alive", {"state": "visible", "reason": "smoke"})
    check("外枠の心拍が見張りへ届く", body.get("watching") is True)
    post("/details/api/screen/release", {"screen": "smoke"}, hdr)     # ブラウザに譲る

    if not no_browser:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("  (playwright が無いので画面の確認は飛ばします)")
        else:
            print("■ 画面(実ブラウザ)")
            with sync_playwright() as pw:
                exe = os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium")
                br = pw.chromium.launch(executable_path=exe if os.path.exists(exe) else None)
                ctx = br.new_context(viewport={"width": 1366, "height": 900})
                pg = ctx.new_page()
                errs = []
                pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                alive_calls = []
                pg.on("request", lambda r: alive_calls.append(r.url) if r.url.endswith("/api/alive") else None)
                pg.goto(base + "/?t=" + token); pg.wait_for_timeout(2500)
                tabs = pg.eval_on_selector_all(".tab .tab__label", "e=>e.map(x=>x.textContent.trim())")
                check("タブは 梱包明細・ペナラベル・資材計算", tabs == ["梱包明細", "ペナラベル", "資材計算"], str(tabs))
                f_d = pg.frame(name="cpt-details"); f_p = pg.frame(name="cpt-pena"); f_m = pg.frame(name="cpt-material")
                check("梱包明細の画面が iframe に出る", f_d is not None and f_d.locator("#lotNo").count() == 1)
                check("ペナラベルの画面が iframe に出る", f_p is not None and f_p.locator("#mainForm").count() == 1)
                check("資材計算の画面が iframe に出る", f_m is not None and f_m.locator(".ribbon").count() == 1)
                check("梱包明細は1枚目として通る(2枚目の断りが出ない)", f_d.evaluate("()=>document.getElementById('secondScreen').hidden"))
                check("ペナラベルの覆いが外れる", f_p.evaluate("()=>document.getElementById('screenguard').hidden"))
                check("資材計算の帯が出る", f_m.evaluate("()=>!document.querySelector('.ribbon').hidden"))
                # ペナラベルのタブを見せてから入力し、別のタブへ切り替え、戻る
                pg.click("#tab-pena"); pg.wait_for_timeout(300)
                check("ペナラベルのタブに切り替わる", pg.evaluate("()=>!document.getElementById('pane-pena').hidden && document.getElementById('pane-details').hidden"))
                f_p.fill("#kensaNo", "W123456")
                pg.click("#tab-material"); pg.wait_for_timeout(300)
                check("資材計算のタブに切り替わる", pg.evaluate("()=>!document.getElementById('pane-material').hidden && document.getElementById('pane-pena').hidden"))
                pg.click("#tab-pena"); pg.wait_for_timeout(300)
                check("戻っても入力が残る", f_p.input_value("#kensaNo") == "W123456", f_p.input_value("#kensaNo"))
                pg.reload(); pg.wait_for_timeout(1500)
                check("読み直すと前のタブ(ペナラベル)が開く", pg.evaluate("()=>!document.getElementById('pane-pena').hidden"))
                before = len(alive_calls); pg.wait_for_timeout(1500)
                check("外枠と各機能が心拍を送る", len(alive_calls) >= 1, "%d 回" % len(alive_calls))
                check("接続表示が接続OK", pg.inner_text("#conn") == "接続OK", pg.inner_text("#conn"))
                # 裏に回った合図が届く(裏では心拍が止まっても終わらない)
                pg.evaluate("()=>{Object.defineProperty(document,'visibilityState',{get:()=>'hidden',configurable:true});document.dispatchEvent(new Event('visibilitychange'));}")
                pg.wait_for_timeout(800)
                log_text = "".join(p.read_text(encoding="utf-8") for p in (local / "logs").glob("*.log"))
                check("裏に回った合図がサーバに届く", "hidden" in log_text and "裏に回りました" in log_text)
                check("コンソールにエラーが無い", not errs, "; ".join(errs)[:200])
                br.close()

    print("■ 停止(stop.bat と同じ経路 = process_manager.py)")
    p3 = subprocess.run([sys.executable, str(ROOT / "process_manager.py")], cwd=str(home), env=env,
                        capture_output=True, text=True, timeout=60)
    try:
        proc.wait(timeout=15); gone = True
    except subprocess.TimeoutExpired:
        gone = False; proc.kill()
    check("止まる", p3.returncode == 0 and gone, p3.stdout.strip().splitlines()[-1][:60] if p3.stdout.strip() else "")
    check("ロックが消える", not (local / "runtime" / "main.lock").exists())
    out = proc.communicate(timeout=10)[0]
    logs = list((local / "logs").glob("coil_packing_tools_*.log"))
    check("ログが1つのファイルに集まる", len(logs) == 1 and all(k in logs[0].read_text(encoding="utf-8") for k in ("meisai", "coil_tool", "packing_pena_label")), str([l.name for l in logs]))

    print("=" * 76)
    ng = [r for r in results if not r[1]]
    print("結果: %d 項目中 %d 件 NG" % (len(results), len(ng)))
    if out.strip():
        print("--- start_app の出力 ---"); print(out[-1500:])
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
