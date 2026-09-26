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
import shutil
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

                print("■ 印刷(3機能とも)")
                f_p = pg.frame(name="cpt-pena")       # 読み直したので枠を取り直す
                # 前に戻す(裏に回った印を外す)
                pg.evaluate("()=>{Object.defineProperty(document,'visibilityState',{get:()=>'visible',configurable:true});document.dispatchEvent(new Event('visibilitychange'));}")
                # 刷る関数を記録に差し替える(本当の印刷ダイアログは出さない)
                spy = ("()=>{window.__printed=[];window.print=()=>window.__printed.push('外枠');"
                       "document.querySelectorAll('iframe').forEach(f=>{f.contentWindow.print=()=>window.__printed.push(f.name)})}")
                pg.evaluate(spy)
                pg.click("#tab-material"); pg.wait_for_timeout(200)
                pg.click(".brand"); pg.keyboard.press("Control+p"); pg.wait_for_timeout(200)
                check("外枠で Ctrl+P → 見せている資材計算の画面を刷る",
                      pg.evaluate("()=>window.__printed") == ["cpt-material"], str(pg.evaluate("()=>window.__printed")))
                pg.click("#tab-pena"); pg.wait_for_timeout(200)
                f_p.click("#kensaNo"); pg.keyboard.press("Control+p"); pg.wait_for_timeout(200)
                check("ペナラベルの画面の中で Ctrl+P → ペナラベルの画面を刷る(外枠は刷らない)",
                      pg.evaluate("()=>window.__printed") == ["cpt-material", "cpt-pena"], str(pg.evaluate("()=>window.__printed")))
                # 画面の中の印刷ボタン(ペナラベルの「試し刷り」)は、その画面(iframe)の文書を刷る。
                # 位置合わせの画面は統合画面の中で開く(別のタブではない)
                f_p.goto(base + "/pena/labels/calibration"); pg.wait_for_timeout(800)
                f_p = pg.frame(name="cpt-pena")
                pg.evaluate(spy)
                f_p.click("button:has-text('試し刷り')"); pg.wait_for_timeout(200)
                check("ペナラベルの画面の中の「試し刷り」は、その画面だけを刷る",
                      pg.evaluate("()=>window.__printed") == ["cpt-pena"], str(pg.evaluate("()=>window.__printed")))
                f_p.focus("button:has-text('試し刷り')"); pg.keyboard.press("Control+p"); pg.wait_for_timeout(200)
                check("画面を移ったあとも Ctrl+P はその画面を刷る",
                      pg.evaluate("()=>window.__printed") == ["cpt-pena", "cpt-pena"], str(pg.evaluate("()=>window.__printed")))
                f_p.goto(base + "/pena/"); pg.wait_for_timeout(500)
                # 別のタブで開く印刷ページ: 読み込みの失敗・エラーが無く、心拍を送る
                for path, key in (("/pena/tare/print", "pena"), ("/pena/list/print", "pena"),
                                  ("/pena/labels/print?ob=1", "pena"),
                                  ("/material/report/checklist?t=" + token, "material")):
                    tab = ctx.new_page()
                    bad, perrs, beats = [], [], []
                    tab.on("response", lambda r, bad=bad: bad.append("%s %s" % (r.status, r.url)) if r.status >= 400 else None)
                    tab.on("console", lambda m, perrs=perrs: perrs.append(m.text) if m.type == "error" else None)
                    tab.on("request", lambda r, beats=beats: beats.append(r.post_data or "") if r.url.endswith("/api/alive") else None)
                    tab.add_init_script("window.print=function(){}")
                    res = tab.goto(base + path); tab.wait_for_timeout(1200)
                    check("印刷ページが開く: " + path.split("?")[0], res is not None and res.status == 200 and not bad and not perrs,
                          "; ".join(bad + perrs)[:120])
                    check("印刷ページが心拍を送る: " + path.split("?")[0],
                          any(('"client":"tab-%s-' % key) in b.replace(" ", "") for b in beats), "%d 回" % len(beats))
                    tab.close(run_before_unload=True)
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

    if not no_browser:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            sync_playwright = None
        if sync_playwright is not None:
            print("■ 自動終了: 外枠を閉じても、印刷の別タブが開いていれば終わらない")
            # 止めた直後は前のポートがまだ空かないことがある(試験の都合)。別の番号で立てる
            with socket.socket() as s2:
                s2.bind(("127.0.0.1", 0)); port = s2.getsockname()[1]
            data = json.loads(conf.read_text(encoding="utf-8"))
            data["server"]["roles"]["main"]["port"] = port
            conf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            base = "http://127.0.0.1:%d" % port
            proc2 = subprocess.Popen([sys.executable, str(ROOT / "start_app.py"), "--no-browser"],
                                     cwd=str(home), env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
            ready = False
            for _ in range(300):
                try:
                    _, body = get("/api/health")
                    if json.loads(body).get("ready"):
                        ready = True; break
                except Exception:
                    pass
                time.sleep(0.3)
            if not ready and proc2.poll() is not None:
                print(proc2.communicate(timeout=10)[0][-1500:])
            check("もう一度起動できる", ready)
            if not ready:
                proc2.kill(); proc2.communicate(timeout=10)
                sync_playwright = None
        if sync_playwright is not None:
            token2 = json.loads((local / "runtime" / "main.lock").read_text(encoding="utf-8"))["token"]
            # 猶予(8秒)+見張りの刻み(2秒)を越えて待つ
            settle = 14
            with sync_playwright() as pw:
                exe = os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium")
                br = pw.chromium.launch(executable_path=exe if os.path.exists(exe) else None)
                ctx = br.new_context(viewport={"width": 1366, "height": 900})
                beats = []
                ctx.on("request", lambda r: beats.append((r.post_data or "").replace(" ", ""))
                       if r.url.endswith("/api/alive") else None)
                shell = ctx.new_page()
                shell.goto(base + "/?t=" + token2); shell.wait_for_timeout(2500)
                check("統合画面の中の画面は別タブの心拍を送らない(外枠が送る)",
                      not any('"client":"tab-' in b for b in beats) and any('"client":"shell-' in b for b in beats))
                ctx.add_init_script("window.print=function(){}")
                sheet = ctx.new_page()             # ペナラベルの印刷ビュー(ふだんは別タブで開く)
                sheet.goto(base + "/pena/tare/print"); sheet.wait_for_timeout(1500)
                paper = ctx.new_page()             # 資材計算のチェックリスト(同じく別タブ)
                paper.goto(base + "/material/report/checklist?t=" + token2); paper.wait_for_timeout(1500)
                check("別タブの印刷ページは心拍を送る",
                      any('"client":"tab-pena-' in b for b in beats) and any('"client":"tab-material-' in b for b in beats))
                shell.close(run_before_unload=True)
                time.sleep(settle)
                check("外枠を閉じても、印刷ページが開いていれば終わらない", proc2.poll() is None)
                st, _ = get("/api/health")
                check("印刷ページはまだサーバに届く", st == 200)
                sheet.close(run_before_unload=True)
                time.sleep(settle)
                check("ペナラベルの印刷ビューを閉じても、資材計算のが開いていれば終わらない", proc2.poll() is None)
                paper.close(run_before_unload=True)
                try:
                    proc2.wait(timeout=settle + 10); ended = True
                except subprocess.TimeoutExpired:
                    ended = False
                check("印刷ページを全部閉じたら自分で終わる", ended)
                br.close()
            if proc2.poll() is None:
                subprocess.run([sys.executable, str(ROOT / "process_manager.py")], cwd=str(home),
                               env=env, capture_output=True, text=True, timeout=60)
                try:
                    proc2.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc2.kill()
            proc2.communicate(timeout=10)
            log_text = "".join(p.read_text(encoding="utf-8") for p in (local / "logs").glob("*.log"))
            check("ログに「ほかに開いている画面がある」と残る", "ほかに" in log_text and "終了しません" in log_text)
            check("ログに閉じられたので終了したと残る", "誰も見ていないので終了します(画面が閉じられました)" in log_text)
            check("自動終了でもロックが消える", not (local / "runtime" / "main.lock").exists())

    print("■ 版: 機能の版だけを上げて配っても、古いプロセスに合流せず入れ替わる")
    copy = home / "app_copy"
    shutil.copytree(str(ROOT), str(copy), ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".pytest_cache", "配布設定", "export"))
    with socket.socket() as s3:
        s3.bind(("127.0.0.1", 0)); port = s3.getsockname()[1]
    data = json.loads(conf.read_text(encoding="utf-8"))
    data["server"]["roles"]["main"]["port"] = port
    # 本番(config/app.json)と同じく予備の番号を使う。止めた直後は前の番号が
    # すぐには空かないことがあり、そのときは次の番号で立つ
    data["server"]["port_retry"] = 3
    conf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    base = "http://127.0.0.1:%d" % port

    def wait_ready(want=None, pid=None):
        url_base = None
        for _ in range(300):
            try:
                if pid is not None:
                    lk = json.loads((local / "runtime" / "main.lock").read_text(encoding="utf-8"))
                    if lk.get("pid") != pid:
                        raise ValueError("まだ前のロック")
                    url_base = "http://127.0.0.1:%d" % lk["port"]
                req = urllib.request.Request((url_base or base) + "/api/health")
                with opener.open(req, timeout=5) as r:
                    h = json.loads(r.read().decode("utf-8"))
                if h.get("ready") and (want is None or want in h.get("version_set", "")):
                    return h
            except Exception:
                pass
            time.sleep(0.3)
        return None

    old = subprocess.Popen([sys.executable, str(copy / "start_app.py"), "--no-browser"], cwd=str(home),
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    h_old = wait_ready()
    check("写しが起動し、版の組を答える", bool(h_old) and "details=" in h_old.get("version_set", ""),
          (h_old or {}).get("version_set", ""))
    app_json = copy / "modules" / "packing_details" / "config" / "app.json"
    meta = json.loads(app_json.read_text(encoding="utf-8"))
    before = meta["version"]; meta["version"] = "9.9.9"
    app_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    new = subprocess.Popen([sys.executable, str(copy / "start_app.py"), "--no-browser"], cwd=str(home),
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    h_new = wait_ready("details=9.9.9", pid=new.pid)
    try:
        old.wait(timeout=20); old_gone = True
    except subprocess.TimeoutExpired:
        old_gone = False; old.kill()
    check("統合ツールの版は同じでも、梱包明細の版が違えば入れ替わる",
          bool(h_new) and old_gone and h_new.get("pid") == new.pid and h_new.get("version") == h_old.get("version"),
          (h_new or {}).get("version_set", ""))
    subprocess.run([sys.executable, str(copy / "process_manager.py")], cwd=str(home), env=env,
                   capture_output=True, text=True, timeout=60)
    try:
        new.wait(timeout=15)
    except subprocess.TimeoutExpired:
        new.kill()
    out_old = old.communicate(timeout=10)[0]; out_new = new.communicate(timeout=10)[0]
    if not (h_new and old_gone):
        print("--- 古いほう ---"); print(out_old[-1200:]); print("--- 新しいほう ---"); print(out_new[-1500:])
    log_text = "".join(p.read_text(encoding="utf-8") for p in (local / "logs").glob("*.log"))
    check("ログに何の版が違ったかが残る", "別の版を終わらせました(梱包明細 %s → 9.9.9)" % before in log_text)
    check("ログに起動した版の一覧が残る", "版: コイル梱包ツール" in log_text and "梱包明細 9.9.9" in log_text)

    print("=" * 76)
    ng = [r for r in results if not r[1]]
    print("結果: %d 項目中 %d 件 NG" % (len(results), len(ng)))
    if out.strip():
        print("--- start_app の出力 ---"); print(out[-1500:])
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
