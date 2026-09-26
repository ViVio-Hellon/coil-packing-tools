#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3機能を現場と同じように使う通し試験(手動・開発用の PC)

    python tools/e2e_scenarios.py                 # 一連の流れ → 交互に使う → 放置する
    python tools/e2e_scenarios.py --quick         # 放置を短くする(動きの確認用)
    python tools/e2e_scenarios.py --only flow     # flow / alternate / idle のどれかだけ

【何をするか】
本物の起動(`start_app.py`)で統合アプリを立て、実ブラウザ(Chromium)で統合画面を開き、
**画面を操作して**3機能を使う。取り込み元(仕掛台帳・梱包課共有・梱包資材マスタ)は
試験用に作る。現場と同じく、梱包明細と資材計算は**同じ台帳フォルダ**を読み、
梱包明細の共有の設定は**梱包資材マスタのフォルダ**に置く。

1. 一連の流れ
   梱包明細  ロット → 重量 → 条番号表示 → 積み → 出力 → 帳票(別タブ)→ 書き足し
   ペナラベル サイズ → 検査番号・重量 → 重量反映 → 積み本数 → 風袋計算 → 小ラベル印刷(別タブ)
             → 全サイズ(反映 → 印刷ビュー)
   資材計算  担当者 → ロット → 検入数 → 計算 → チェックリストへ → 印刷(別タブ)→ 発注票 → 履歴
2. 交互に使う
   3機能を途中まで入れてはタブを替え、戻って続きをする。値が消えない・混ざらない・
   断られないこと。タブの行き来を繰り返し、梱包明細と資材計算の取り込みを同時に走らせる
3. 放置する
   a. 表のまま放置(心拍だけが流れる)
   b. 裏に回して放置(ブラウザがタブを凍結したのと同じ ── 裏に回った合図のあと
      ページの JavaScript を止める)
   c. スリープ(サーバのプロセスも止め、ブラウザも止める)
   d. 統合画面を閉じ、帳票の別タブだけを残して放置
   f. 本ツール以外のタブを開き、本ツールのタブが裏で捨てられる → 戻る(読み直し)
   e. 全部閉じて放置 → 自分で終わる → 翌朝と同じく開き直す(残るもの・消えるもの)
      (f で捨てられた前の画面が残っていても、自分で終わること)
   放置のあと、3機能とも「断られた」「接続なし」「開き直してください」が出ず、
   そのまま操作を続けられること

【要るもの】Playwright と Chromium(`tools/smoke_shell.py` と同じ)。現場の PC には要らない。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results: list = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("  %s %-52s %s" % ("OK " if ok else "NG ", name, str(detail)[:110]), flush=True)
    return ok


def wait_until(fn, timeout=15.0, step=0.2):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:                     # noqa: BLE001 - 待つあいだの失敗は続ける
            last = exc
        time.sleep(step)
    return None


# ======================================================================
# 取り込み元(現場の共有フォルダと同じ形)
# ======================================================================
def _source(path: Path, columns, rows, *, declare=True, created_at=None, table="仕掛"):
    conn = sqlite3.connect(str(path))
    decl = ", ".join(f'"{c}" TEXT' if declare else f'"{c}"' for c in columns)
    conn.execute(f'CREATE TABLE "{table}" ({decl})')
    marks = ", ".join("?" for _ in columns)
    cols = ", ".join(f'"{c}"' for c in columns)
    with conn:
        conn.executemany(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})',
                         [[r.get(c, "") for c in columns] for r in rows])
        if created_at:
            conn.execute('CREATE TABLE "_更新情報" ("項目" TEXT, "値" TEXT)')
            conn.execute('INSERT INTO "_更新情報" VALUES (?, ?)', ("作成日時", created_at))
    conn.close()


LOTS = ("L5160Z0", "A123456")
ORDER_NO = "12345678"
SPEC_NO = "1C0001"


def make_sources(home: Path) -> None:
    """仕掛台帳(梱包明細・資材計算が同じフォルダを読む)・梱包課共有・梱包資材マスタ。"""
    lot, konpo, master = home / "lot", home / "konpo", home / "master"
    for d in (lot, konpo, master):
        d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y/%m/%d %H:%M:%S")
    _source(lot / "SIKALOT.sqlite3",
            ("ﾛｯﾄ番号", "用途ｺｰﾄﾞ", "用途名", "製造材質", "製造調質", "製造板厚", "製造板幅",
             "ｵｰﾀﾞｰ板厚", "ｵｰﾀﾞｰ板幅", "ｵｰﾀﾞｰ板丈", "設計_設備ｺｰｽ", "実績_設備ｺｰｽ"),
            [{"ﾛｯﾄ番号": n, "用途ｺｰﾄﾞ": "R192", "用途名": "ｱﾝｾﾞﾝﾀｲｻﾝｺｰ", "製造材質": "52S",
              "製造調質": "H34", "製造板厚": "1.985", "製造板幅": "104.0", "ｵｰﾀﾞｰ板厚": "2.0",
              "ｵｰﾀﾞｰ板幅": "104.0", "ｵｰﾀﾞｰ板丈": "0.0", "設計_設備ｺｰｽ": "HOT L-1 LS4",
              "実績_設備ｺｰｽ": "HOT L-1"} for n in LOTS], created_at=stamp)
    # 引当・受注は2つのツールが読む列を合わせ持つ(本物の台帳は全部の列を持っている)
    _source(lot / "SIKAHIKI.sqlite3", ("ﾛｯﾄ番号", "引当番号", "受注番号", "出荷日"),
            [{"ﾛｯﾄ番号": n, "引当番号": "1", "受注番号": ORDER_NO, "出荷日": "03/28"} for n in LOTS],
            created_at=stamp)
    _source(lot / "SIKAODR.sqlite3",
            ("受注番号", "受注材質", "受注調質", "受注板厚", "受注板幅", "用途ｺｰﾄﾞ", "用途名",
             "包装仕様NO", "納入先名称", "取引先名称", "製品単重", "梱包単位_重量", "梱包単位_枚数",
             "ｺｲﾙ外径_MAX", "材質_比重", "コイル内径_目標", "営業納期"),
            [{"受注番号": ORDER_NO, "受注材質": "52S", "受注調質": "H34", "受注板厚": "1.0",
              "受注板幅": "100", "用途ｺｰﾄﾞ": "R192", "用途名": "ﾃｽﾄ用途", "包装仕様NO": SPEC_NO,
              "納入先名称": "納入先A", "取引先名称": "取引先A", "製品単重": "100",
              "梱包単位_重量": "", "梱包単位_枚数": "5", "ｺｲﾙ外径_MAX": "900", "材質_比重": "2.71",
              "コイル内径_目標": "508", "営業納期": "04/01"}], created_at=stamp)
    _source(konpo / "LS4LOT.sqlite3",
            ("ﾛｯﾄ番号", "当工程設計_設備名", "当工程設計_横割数", "当工程設計_縦割数",
             "当工程設計_枚本数", "製造板厚", "製造板幅", "製造板丈"),
            [{"ﾛｯﾄ番号": "L5160Z0", "当工程設計_設備名": "LS4", "当工程設計_横割数": "12",
              "当工程設計_縦割数": "2", "当工程設計_枚本数": "24", "製造板厚": "1.985",
              "製造板幅": "104.0", "製造板丈": "0.0"},
             {"ﾛｯﾄ番号": "A123456", "当工程設計_設備名": "LS4", "当工程設計_横割数": "6",
              "当工程設計_縦割数": "2", "当工程設計_枚本数": "12", "製造板厚": "1.985",
              "製造板幅": "104.0", "製造板丈": "0.0"}], declare=False)
    m = master / "梱包資材マスタ.sqlite3"
    spec = {"包装仕様NO": SPEC_NO, "納入先名称": "", "用途名": "", "パレット": "スカシ", "サイズ": "",
            "種類": "", "リプラサイズ": "30", "コイル間スペーサー": "リプラ",
            "最下部スペーサー": "リプラ", "下本数": "3", "緩衝材": "HB", "ｺｲﾙ間は間紙入": "",
            "梱包単位_重量": "", "重量範囲": "", "梱包単位_枚数": "", "枚数範囲": "以下",
            "梱包総高さ": "", "高さ範囲": ""}
    _source(m, tuple(spec), [spec], table="包装仕様")
    pallets = []
    for w, mark in ((800, "P08"), (900, "P09"), (1000, "P10"), (1100, "P11"),
                    (1200, "P12"), (1300, "P13"), (1350, "P135")):
        for kind, wide in (("スカシ", True), ("全面", True), ("EXPS", False), ("強度UP", None)):
            pallets.append({"種類": kind, "巾下限": "0",
                            "巾上限": str(w) if wide is not False else "9999",
                            "丈下限": "0", "丈上限": str(w) if wide else "9999",
                            "新記号": mark, "Ｗ": str(w)})
    _source(m, ("種類", "巾下限", "巾上限", "丈下限", "丈上限", "新記号", "Ｗ"), pallets,
            table="パレット")
    _source(m, ("リプラ長さ", "長さ"),
            [{"リプラ長さ": str(n), "長さ": str(n)} for n in (700, 800, 900, 950, 1000, 1050, 1100, 1150)],
            table="リプラサイズ")
    _source(m, ("管理番号", "苗字", "班", "名前", "読み", "担当ライン"),
            [{"管理番号": "001", "苗字": "高村", "班": "1班", "名前": "高村 敏幸", "読み": "たかむら",
              "担当ライン": "機側"},
             {"管理番号": "002", "苗字": "山田", "班": "2班", "名前": "山田 太郎", "読み": "やまだ",
              "担当ライン": "機側"}], table="班員名簿")


# ======================================================================
# 統合アプリ(本物の起動)
# ======================================================================
class App:
    def __init__(self, home: Path):
        self.home = home
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.conf = home / "app.json"
        self.conf.write_text(json.dumps({
            "app_id": "nlm.coil-packing-tools", "display_name": "コイル梱包ツール",
            "version": "1.0.0", "local_dir_name": "CoilPackingTools",
            "server": {"host": "127.0.0.1", "port_retry": 3, "roles": {"main": {"port": self.port}}},
        }, ensure_ascii=False), encoding="utf-8")
        self.local = home / "local"
        self.env = dict(os.environ,
                        COIL_PACKING_TOOLS_APP_CONFIG=str(self.conf),
                        COIL_PACKING_TOOLS_LOCAL_DIR=str(self.local),
                        XDG_DATA_HOME=str(home / "xdg-data"), XDG_STATE_HOME=str(home / "xdg-state"),
                        LOCALAPPDATA=str(home / "localappdata"),
                        PACKING_DETAILS_SHARED_DIR=str(home / "master"),
                        PACKING_DETAILS_LOT_DB_DIR=str(home / "lot"),
                        PACKING_DETAILS_KONPO_DB_DIR=str(home / "konpo"),
                        COIL_TOOL_MASTER_DB_DIR=str(home / "master"),
                        COIL_TOOL_LOT_DB_DIR=str(home / "lot"),
                        PPL_PREFER_ACCESS="0")
        for name in ("PYTHONDONTWRITEBYTECODE",):
            self.env.pop(name, None)
        self.proc = None
        self.token = ""

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def start(self, timeout=150):
        self.proc = subprocess.Popen([sys.executable, str(ROOT / "start_app.py"), "--no-browser"],
                                     cwd=str(self.home), env=self.env, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        lock = self.local / "runtime" / "main.lock"
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc.poll() is not None:
                return None
            try:
                info = json.loads(lock.read_text(encoding="utf-8"))
                if info.get("pid") == self.proc.pid:
                    self.port, self.token = info["port"], info["token"]
                    h = self.health()
                    if h and h.get("ready"):
                        return h
            except (OSError, ValueError):
                pass
            time.sleep(0.3)
        return None

    def health(self):
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open(self.base + "/api/health", timeout=3) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:                            # noqa: BLE001
            return None

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def logs(self):
        return "".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in sorted((self.local / "logs").glob("*.log")))

    def stop(self):
        if self.alive():
            subprocess.run([sys.executable, str(ROOT / "process_manager.py")], cwd=str(self.home),
                           env=self.env, capture_output=True, text=True, timeout=60)
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ======================================================================
# 画面の操作
# ======================================================================
OVERLAYS = {
    None: ("#offline", "#restarted", "#stopped"),
    "details": ("#secondScreen", "#takenOver", "#restarted", "#offline"),
    "pena": ("#sgDeny", "#sgLost", "#sgWait"),
    "material": ("#offline",),
}

_VISIBLE = """s => { const e = document.querySelector(s); if (!e || e.hidden) return false;
  for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
    if (n.hidden) return false;
    const st = getComputedStyle(n);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
  }
  return true; }"""


def visible(fr, sel) -> bool:
    return bool(fr.evaluate(_VISIBLE, sel))


def text(fr, sel) -> str:
    return (fr.evaluate("s => { const e = document.querySelector(s); return e ? "
                        "(e.value !== undefined && e.tagName !== 'BUTTON' ? e.value : e.textContent) : null }",
                        sel) or "").strip()


class Shell:
    """統合画面(1枚のタブ)と、その中の3機能の iframe。"""

    def __init__(self, ctx, app: App):
        self.ctx, self.app = ctx, app
        self.page = ctx.new_page()
        self.page.goto(app.base + "/?t=" + app.token)
        wait_until(lambda: all(self.frame(k) for k in ("details", "pena", "material")), 20)

    def frame(self, key):
        return self.page.frame(name=f"cpt-{key}")

    def tab(self, key):
        self.page.click(f"#tab-{key}")
        self.page.wait_for_timeout(250)
        return self.frame(key)

    def trouble(self) -> list:
        """画面に出ている「断られた」「接続なし」などの覆い・帯。"""
        found = []
        for key, sels in OVERLAYS.items():
            fr = self.page if key is None else self.frame(key)
            for sel in sels:
                try:
                    if visible(fr, sel):
                        found.append(f"{key or '外枠'}{sel}")
                except Exception as exc:              # noqa: BLE001
                    found.append(f"{key or '外枠'}{sel}? {exc}")
        mat = self.frame("material")
        if mat is not None and visible(mat, "#gate"):
            found.append("material#gate:" + text(mat, "#gateTitle"))
        if text(self.page, "#conn") != "接続OK":
            found.append("外枠#conn:" + text(self.page, "#conn"))
        return found

    def ready(self, timeout=20):
        return wait_until(lambda: not self.trouble(), timeout)


def pena_toast(fr, want, timeout=10):
    return wait_until(lambda: want in text(fr, "#toast .toast-body")
                      and visible(fr, "#toast"), timeout)


def material_toast(fr, want, timeout=10):
    return wait_until(lambda: want in text(fr, "#toast") and visible(fr, "#toast"), timeout)


# ---- 梱包明細 --------------------------------------------------------
def details_open_lot(fr, lot):
    fr.fill("#lotNo", "")
    fr.type("#lotNo", lot, delay=20)
    return wait_until(lambda: visible(fr, "#lotInfo") and text(fr, "#warisu"), 10)


def details_weights(fr, w1, w2):
    fr.fill("#weight1", str(w1))
    fr.fill("#weight2", str(w2))
    fr.press("#weight2", "Tab")
    fr.wait_for_timeout(400)


def details_stack_and_output(fr, keys):
    fr.click("#btnStrands")
    if not wait_until(lambda: visible(fr, "#strandCard"), 8):
        return "条番号が出ない: " + text(fr, "#notice")
    fr.fill("#stackMax", str(len(keys)))
    fr.press("#stackMax", "Tab")
    wait_until(lambda: fr.evaluate("document.querySelectorAll('#stack button.slot').length") == len(keys), 5)
    for k in keys:
        fr.click(f'#grid button.coil[data-key="{k}"]')
        fr.wait_for_timeout(150)
    if not wait_until(lambda: fr.evaluate("!document.querySelector('#btnOutput').disabled"), 5):
        return "出力が押せない"
    fr.click("#btnOutput")
    # 重なり・上書きなどの確かめが出たら「はい」
    fr.wait_for_timeout(500)
    if visible(fr, "#confirmDialog"):
        fr.click('#confirmDialog button[value="yes"]')
    ok = wait_until(lambda: "出力しました" in text(fr, "#notice"), 8)
    return "" if ok else "出力できない: " + text(fr, "#notice")


# ---- ペナラベル ------------------------------------------------------
def pena_ready(fr, timeout=15):
    """画面の受付(使ってよい画面か)が済み、接続OK になるまで待つ。"""
    return wait_until(lambda: fr.evaluate("document.readyState") == "complete"
                      and not visible(fr, "#sgWait") and not visible(fr, "#sgDeny")
                      and text(fr, "#conn") == "接続OK", timeout)


PENA_EXPECT = {"#nw1": "110.0", "#gw1": "147.0", "#ta1": "910.0"}


def pena_weights(fr, kensa="w111111", w1="10", w2="12"):
    pena_ready(fr)
    if not fr.is_checked("#cbMode"):
        fr.check("#cbMode")
    fr.click('input[name=size][data-kind=cb][data-cb="5"]')
    wait_until(lambda: text(fr, "#lblSize1") and not fr.is_disabled("#weight1"), 8)
    fr.fill("#kensaNo", kensa)
    fr.fill("#weight1", w1)
    fr.fill("#weight2", w2)
    fr.click("button[data-act=applyWeight]")
    return pena_toast(fr, "重量を反映しました")


def pena_tare(fr, counts=("11", "11", "10", "10")):
    for i, n in enumerate(counts, 1):
        fr.fill(f"#coilH{i}", n)
    fr.click("button[data-act=calcTare]")
    if not pena_toast(fr, "計算しました"):
        return "計算できない: " + text(fr, "#toast .toast-body")
    got = {sel: text(fr, sel) for sel in PENA_EXPECT}
    return "" if got == PENA_EXPECT else f"結果が違う: {got}"


# ---- 資材計算 --------------------------------------------------------
def material_ready(fr, timeout=15):
    return wait_until(lambda: not visible(fr, "#gate") and visible(fr, ".shell"), timeout)


def material_worker(fr):
    opts = fr.evaluate("[...document.querySelectorAll('#rb-worker option')].map(o => o.value).filter(Boolean)")
    if not opts:
        return ""
    if text(fr, "#rb-worker") != opts[0]:
        fr.select_option("#rb-worker", opts[0])
        material_toast(fr, "担当者を")
    return opts[0]


def material_ins(fr, value: str) -> None:
    """検入数を入れて、サーバへ送られるまで待つ(`change` で送る。同じ値だと送られない)。"""
    if text(fr, "#ins") in (value, value + ".00"):
        tmp = "1" if value != "1" else "2"
        fr.fill("#ins", tmp)
        with fr.page.expect_response(lambda r: "/api/calc/input" in r.url):
            fr.press("#ins", "Tab")
    fr.fill("#ins", value)
    with fr.page.expect_response(lambda r: "/api/calc/input" in r.url):
        fr.press("#ins", "Tab")


def material_calc(fr, lot, ins="10"):
    fr.fill("#lot", "")
    fr.type("#lot", lot, delay=20)
    if not wait_until(lambda: text(fr, "#o-包装") == SPEC_NO, 10):
        return "ロットが引けない: " + text(fr, "#toast")
    material_ins(fr, ins)
    fr.click("#run")
    if not material_toast(fr, "台数"):
        return "計算できない: " + text(fr, "#toast")
    got = (text(fr, "#r-種類"), text(fr, "#r-台数"))
    return "" if got == ("スカシ", "2") else f"結果が違う: {got}"


# ======================================================================
# 1. 一連の流れ
# ======================================================================
def scenario_flow(app: App, ctx, sh: Shell, note: dict) -> None:
    print("■ 1. 一連の流れ")
    check("統合画面が開き、3機能とも使える状態", sh.ready(), sh.trouble())

    # ---- 梱包明細 ----
    fr = sh.tab("details")
    check("梱包明細: 取り込まれた台帳でロットが引ける", details_open_lot(fr, "L5160Z0"),
          text(fr, "#warisu") or text(fr, "#notice"))
    check("梱包明細: 割数(縦割2 × 横割12)", "縦割2" in text(fr, "#warisu") and "横割12" in text(fr, "#warisu"),
          text(fr, "#warisu"))
    details_weights(fr, 250, 248)
    err = details_stack_and_output(fr, ["1-12", "1-11", "2-12", "2-11"])
    check("梱包明細: 重量 → 条番号表示 → 積み → 出力", not err, err or text(fr, "#notice"))
    with ctx.expect_page() as info:
        fr.click("#btnPrint")
    report = info.value
    report.wait_for_load_state()
    note["report"] = report
    check("梱包明細: 帳票が別タブで開く", "L5160Z0-No1" in report.title(), report.url.split("?")[0])
    rows = report.evaluate("[...document.querySelectorAll('tr.r-data td')].map(t => t.textContent.trim()).filter(Boolean)")
    check("梱包明細: 帳票に積んだ4本と重量", sum(1 for r in rows if r.endswith("Kg")) == 4, rows[:8])
    report.click('span.edit[data-placeholder="サイズ"]')
    report.keyboard.type("1.0×104")
    report.keyboard.press("Enter")
    check("梱包明細: 帳票の書き足しが保存される",
          wait_until(lambda: "保存しました" in text(report, "#editSaved"), 8), text(report, "#editSaved"))

    # ---- ペナラベル ----
    fr = sh.tab("pena")
    check("ペナラベル: 重量反映", pena_weights(fr), text(fr, "#toast .toast-body"))
    check("ペナラベル: 検査番号・重量が表に出る",
          text(fr, "#lblKensaNo") == "W111111" and text(fr, "#lblWeight1") == "10kg",
          (text(fr, "#lblKensaNo"), text(fr, "#lblWeight1")))
    err = pena_tare(fr)
    check("ペナラベル: 風袋計算(正味・総重量・高さ)", not err, err)
    with ctx.expect_page() as info:
        fr.click("button[data-act=printLabels]")
    labels = info.value
    labels.wait_for_load_state()
    check("ペナラベル: 小ラベルの印刷ビューが別タブで開く",
          "/pena/labels/print" in labels.url and labels.locator("section.sheet").count() >= 1, labels.url)
    labels.close()
    # 全サイズ(同じ iframe の中で開く)
    fr.goto(app.base + "/pena/all-size")
    fr.wait_for_load_state()
    combos = fr.evaluate("[...document.querySelectorAll('#asCombo option')].map(o => o.value).filter(Boolean)")
    if combos:
        fr.select_option("#asCombo", combos[0])
        fr.wait_for_load_state()
        wait_until(lambda: sh.frame("pena").evaluate("document.readyState") == "complete" and
                   visible(sh.frame("pena"), "#asKen"), 10)
        fr = sh.frame("pena")
        pena_ready(fr)
        fr.fill("#asKen", "W123456")
        fr.check('input[name=asDigit][value="2"]')
        fr.fill("#asW1", "10")
        fr.fill("#asW2", "12")
        fr.click("#asSubmit")
        fr.wait_for_load_state()
        ok = wait_until(lambda: "反映値" in sh.frame("pena").evaluate("document.body.innerText"), 10)
        check("ペナラベル: 全サイズの反映", ok, combos[0])
        fr = sh.frame("pena")
        pena_ready(fr)
        with ctx.expect_page() as info:
            fr.click("#asPrint")
        sheet = info.value
        sheet.wait_for_load_state()
        check("ペナラベル: 全サイズの印刷ビューが別タブで開く",
              "/pena/all-size/print" in sheet.url and sheet.locator(".lbl").count() > 0, sheet.url)
        sheet.close()
    else:
        check("ペナラベル: 全サイズの候補がある", False, "候補なし")
    fr.goto(app.base + "/pena/")
    fr.wait_for_load_state()
    pena_ready(sh.frame("pena"))
    check("ペナラベル: 戻ると入力と結果が残っている",
          wait_until(lambda: text(sh.frame("pena"), "#nw1") == "110.0", 8), text(sh.frame("pena"), "#nw1"))

    # ---- 資材計算 ----
    fr = sh.tab("material")
    check("資材計算: 画面が使える(受付を通る)", material_ready(fr), text(fr, "#gateTitle"))
    note["worker"] = material_worker(fr)
    check("資材計算: 担当者を選べる(名簿の機側の人)", note["worker"], note["worker"])
    err = material_calc(fr, "A123456")
    check("資材計算: ロット → 検入数 → 計算", not err, err)
    fr.check("#size-fixed")
    fr.click("#add")
    check("資材計算: チェックリストへ積む", material_toast(fr, "行目に積みました"), text(fr, "#toast"))
    fr.click('nav.rail a.rail__item[href*="/checklist"]')
    fr.wait_for_load_state()
    fr = sh.frame("material")
    material_ready(fr)
    check("資材計算: チェックリストに1行", wait_until(lambda: text(fr, "#sub").startswith("1 /"), 8),
          text(fr, "#sub"))
    with ctx.expect_page() as info:
        fr.click("a#print")
    rep = info.value
    rep.wait_for_load_state()
    check("資材計算: チェックリストの印刷が別タブで開く",
          "A123456" in rep.content() and "チェックリスト" in rep.title() + rep.content(), rep.url.split("?")[0])
    rep.close()
    fr.click("a#to-order")
    fr.wait_for_load_state()
    fr = sh.frame("material")
    material_ready(fr)
    fr.click("#build")
    check("資材計算: 発注票を作る", material_toast(fr, "枚作りました"), text(fr, "#toast") or text(fr, "#note"))
    with ctx.expect_page() as info:
        fr.click("a#print")
    rep = info.value
    rep.wait_for_load_state()
    check("資材計算: 発注票の印刷が別タブで開く", "発注票" in rep.title(), rep.url.split("?")[0])
    rep.close()
    fr.click("#commit")
    check("資材計算: 発注を履歴に残す", material_toast(fr, "履歴に残しました"), text(fr, "#toast"))
    fr.click('nav.rail a.rail__item[href*="/calc"]')
    fr.wait_for_load_state()
    material_ready(sh.frame("material"))
    trouble = [] if sh.ready() else sh.trouble()
    check("一連の流れのあと、どの画面にも断り・接続なしが出ていない", not trouble, trouble)


# ======================================================================
# 2. 交互に使う
# ======================================================================
def _api(fr, path, body=None, *, header="X-Tool-Token", screen_key=None):
    """その画面(iframe)から、画面の JS と同じ送り方で API を叩く。"""
    return fr.evaluate("""([path, body, header, screenKey]) => {
        const h = {'Content-Type': 'application/json'};
        const tok = (window.APP && window.APP.token) || window.APP_TOKEN || document.body.dataset.token || '';
        h[header] = tok;
        if (screenKey) h[screenKey[0]] = sessionStorage.getItem(screenKey[1]) || '';
        return fetch(path, {method: 'POST', headers: h, body: JSON.stringify(body || {})})
          .then(r => r.json().then(j => ({status: r.status, body: j})));
    }""", [path, body or {}, header, screen_key])


def scenario_alternate(app: App, ctx, sh: Shell, note: dict) -> None:
    print("■ 2. 交互に使う")
    # --- 途中まで入れてはタブを替える ---
    d = sh.tab("details")
    ok = details_open_lot(d, "A123456")
    details_weights(d, 300, 302)
    check("交互: 梱包明細を途中まで(別のロット・重量)", ok and "横割6" in text(d, "#warisu"), text(d, "#warisu"))
    p = sh.tab("pena")
    check("交互: ペナラベルで別の検査番号・重量を反映", pena_weights(p, "x222222", "20", "22"),
          text(p, "#toast .toast-body"))
    m = sh.tab("material")
    material_ready(m)
    m.fill("#lot", "")
    m.type("#lot", "L5160Z0", delay=20)
    wait_until(lambda: text(m, "#o-包装") == SPEC_NO, 10)
    material_ins(m, "10")
    d = sh.tab("details")
    check("交互: 梱包明細に戻るとロット・重量が残っている",
          text(d, "#lotNo") == "A123456" and text(d, "#weight1") == "300" and text(d, "#weight2") == "302",
          (text(d, "#lotNo"), text(d, "#weight1"), text(d, "#weight2")))
    err = details_stack_and_output(d, ["1-6", "2-6"])
    check("交互: 梱包明細の続き(条番号表示 → 積み → 出力)", not err, err or text(d, "#notice"))
    m = sh.tab("material")
    check("交互: 資材計算に戻るとロット・検入数が残っている",
          text(m, "#lot") == "L5160Z0" and text(m, "#ins").startswith("10"), (text(m, "#lot"), text(m, "#ins")))
    m.click("#run")
    check("交互: 資材計算の続き(計算)", material_toast(m, "台数") and text(m, "#r-台数") == "2",
          (text(m, "#r-種類"), text(m, "#r-台数")))
    p = sh.tab("pena")
    check("交互: ペナラベルに戻ると反映した検査番号が残っている", text(p, "#lblKensaNo") == "X222222",
          text(p, "#lblKensaNo"))
    for i, n in enumerate(("11", "11", "10", "10"), 1):
        p.fill(f"#coilH{i}", n)
    p.click("button[data-act=calcTare]")
    check("交互: ペナラベルの続き(風袋計算。重量 20kg × 11本 = 220.0)",
          pena_toast(p, "計算しました") and text(p, "#nw1") == "220.0", text(p, "#nw1"))
    d = sh.tab("details")
    with ctx.expect_page() as info:
        d.click("#btnPrint")
    r2 = info.value
    r2.wait_for_load_state()
    check("交互: 梱包明細の帳票(2つ目のロット)", "A123456-No1" in r2.title(), r2.title())
    m = sh.tab("material")
    m.check("#size-fixed")
    m.click("#add")
    check("交互: 資材計算のチェックリストに2行目", material_toast(m, "2 行目に積みました"), text(m, "#toast"))

    # --- 帳票の別タブと統合画面を行き来する ---
    r2.bring_to_front()
    r2.click('span.edit[data-placeholder="LOTNO"]')
    r2.keyboard.type("LT-001")
    r2.keyboard.press("Enter")
    check("交互: 帳票の別タブに行って書き足し(LOTNO)",
          wait_until(lambda: "保存しました" in text(r2, "#editSaved"), 8), text(r2, "#editSaved"))
    # 1つ目のロットの帳票: 別のロットを開くと前のロットの明細は片付けられる
    # (移植元・VBA の `txtLotNo_Change` と同じ)。書き足しは**はっきり断られる**
    old = note.get("report")
    if old is not None and not old.is_closed():
        old.bring_to_front()
        old.click('span.edit[data-placeholder="LOTNO"]')
        old.keyboard.type("X")
        old.keyboard.press("Enter")
        check("交互: 前のロットの帳票の書き足しは「開き直して」と断られる(移植元どおり)",
              wait_until(lambda: "開き直してください" in text(old, "#editSaved"), 8), text(old, "#editSaved"))
        old.close()
    note["report"] = r2
    sh.page.bring_to_front()

    # --- タブをすばやく何度も行き来する ---
    for i in range(36):
        sh.page.click("#tab-" + ("details", "pena", "material")[i % 3])
        sh.page.wait_for_timeout(80)
    sh.page.wait_for_timeout(1500)
    trouble = [] if sh.ready(10) else sh.trouble()
    check("交互: タブを36回行き来しても断り・接続なしが出ない", not trouble, trouble)

    # --- 取り込みを同時に走らせる(同じ台帳フォルダを2つのツールが読む) ---
    now = time.time()
    for f in (app.home / "lot").glob("*.sqlite3"):
        os.utime(f, (now, now))
    p = sh.tab("pena")
    d, m = sh.frame("details"), sh.frame("material")
    d.evaluate("""() => { window.__imp = fetch(APP.base + '/api/import', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Tool-Token': APP.token,
                  'X-Tool-Screen': APP.screen}, body: JSON.stringify({force: true})})
        .then(r => r.json().then(j => ({status: r.status, body: j}))); }""")
    m.evaluate("""() => { window.__imp = fetch(APP_BASE + '/api/settings/import', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-App-Token': APP_TOKEN,
                  'X-App-Screen': sessionStorage.getItem('coil.screen') || ''}, body: '{}'})
        .then(r => r.json().then(j => ({status: r.status, body: j}))); }""")
    p.click("button[data-act=calcTare]")
    pena_ok = pena_toast(p, "計算しました")
    di = d.evaluate("window.__imp")
    mi = m.evaluate("window.__imp")
    check("交互: 梱包明細の取り込み(資材計算と同時)", di["status"] == 200 and di["body"].get("ok"),
          (di["body"].get("summary") or di["body"])) 
    check("交互: 資材計算の取り込み(梱包明細と同時)", mi["status"] == 200 and mi["body"].get("ok"),
          mi["body"].get("total", mi["body"]))
    check("交互: 取り込みの最中もペナラベルの計算ができる", pena_ok, text(p, "#toast .toast-body"))
    d = sh.tab("details")
    check("交互: 取り込みのあとも梱包明細の開いているロットはそのまま",
          text(d, "#lotNo") == "A123456" and visible(d, "#outputCard"), text(d, "#lotNo"))

    # --- 統合画面をもう1枚開いてしまった ---
    sh2 = Shell(ctx, app)
    sh2.page.wait_for_timeout(3000)
    second = [k for k, sel in (("details", "#secondScreen"), ("pena", "#sgDeny"))
              if visible(sh2.frame(k), sel)]
    if visible(sh2.frame("material"), "#gate") and "別の画面" in text(sh2.frame("material"), "#gateTitle"):
        second.append("material")
    check("交互: 統合画面の2枚目では3機能とも「別の画面で開いています」", len(second) == 3, second)
    sh2.page.close(run_before_unload=True)
    sh.page.bring_to_front()
    sh.page.wait_for_timeout(2500)
    trouble = [] if sh.ready(10) else sh.trouble()
    check("交互: 2枚目を閉じても、1枚目はそのまま使える", not trouble, trouble)
    p = sh.tab("pena")
    p.click("button[data-act=calcTare]")
    check("交互: 1枚目のペナラベルで計算できる(断られない)", pena_toast(p, "計算しました"),
          text(p, "#toast .toast-body"))


# ======================================================================
# 3. 放置する
# ======================================================================
_HIDE = """() => {
  for (const [k, v] of [['visibilityState', 'hidden'], ['hidden', true]])
    Object.defineProperty(document, k, {configurable: true, get: () => v});
  document.dispatchEvent(new Event('visibilitychange'));
  document.dispatchEvent(new Event('freeze'));
}"""
_SHOW = """() => {
  // 書き換えを外して、ブラウザ本来の値に戻す(残すと、閉じるときの「裏に回った」まで
  // 「前に戻った」として送られてしまう)
  delete document.visibilityState;
  delete document.hidden;
  document.dispatchEvent(new Event('resume'));
  document.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new Event('focus'));
}"""


class Frozen:
    """ページの JavaScript を止める(ブラウザがタブを凍結したのと同じ)。

    ブラウザは裏に回したタブのタイマーを間引き、長く裏にあるタブは凍結する。
    ここでは `hide=True` なら先に「裏に回った」合図(visibilitychange・freeze)を出してから、
    デバッガでページの実行を止める。止めているあいだは心拍も何も出ない。
    """

    def __init__(self, pages, *, hide: bool):
        self.pages, self.hide, self.sessions = list(pages), hide, []

    def __enter__(self):
        for pg in self.pages:
            if self.hide:
                for fr in pg.frames:
                    try:
                        fr.evaluate(_HIDE)
                    except Exception:            # noqa: BLE001 - 読み込み中の枠は飛ばす
                        pass
            cdp = pg.context.new_cdp_session(pg)
            cdp.send("Debugger.enable")
            cdp.send("Debugger.pause")
            self.sessions.append(cdp)
        return self

    def __exit__(self, *exc):
        for pg, cdp in zip(self.pages, self.sessions):
            cdp.send("Debugger.resume")
            cdp.send("Debugger.disable")
            cdp.detach()
            if self.hide:
                for fr in pg.frames:
                    try:
                        fr.evaluate(_SHOW)
                    except Exception:            # noqa: BLE001
                        pass
        return False


def _hold(app: App, seconds: float, label: str) -> bool:
    """放置する。サーバが途中で終わらないかを見ながら待つ。"""
    print(f"   … {label} {seconds:.0f}秒", flush=True)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not app.alive():
            return False
        time.sleep(min(5.0, max(0.1, end - time.monotonic())))
    return app.alive()


def _after_idle(sh: Shell, label: str) -> None:
    """放置のあと: 断り・接続なしが出ず、3機能とも続けて操作できる。"""
    sh.page.wait_for_timeout(3000)
    trouble = [] if sh.ready(15) else sh.trouble()
    check(f"{label}: 断り・接続なし・開き直しが出ない", not trouble, trouble)
    p = sh.tab("pena")
    p.click("button[data-act=calcTare]")
    check(f"{label}: ペナラベルで計算できる", pena_toast(p, "計算しました"), text(p, "#toast .toast-body"))
    m = sh.tab("material")
    m.click("#run")
    check(f"{label}: 資材計算で計算できる", material_toast(m, "台数"), text(m, "#toast"))
    d = sh.tab("details")
    codes = []
    for _ in range(2):                          # 反転して、元に戻す
        with d.page.expect_response(lambda r: "/details/api/reverse" in r.url) as info:
            d.click("#btnReverse")
        codes.append(info.value.status)
    check(f"{label}: 梱包明細の画面を操作できる(断られない)",
          codes == [200, 200] and not visible(d, "#takenOver"), codes)


def scenario_idle(app: App, ctx, sh: Shell, note: dict, minutes: dict) -> None:
    print("■ 3. 放置する")
    pid = (app.health() or {}).get("pid")

    ok = _hold(app, minutes["visible"], "a. 表のまま放置")
    check("放置a(表のまま): サーバが終わらない", ok, "")
    _after_idle(sh, "放置a(表のまま)")

    pages = [p for p in ctx.pages if not p.is_closed()]
    with Frozen(pages, hide=True):
        ok = _hold(app, minutes["hidden"], "b. 裏に回して凍結")
    check("放置b(裏に回して凍結): サーバが終わらない", ok, f"{len(pages)} 枚を凍結")
    _after_idle(sh, "放置b(裏→凍結→戻る)")

    with Frozen(pages, hide=False):
        os.kill(app.proc.pid, signal.SIGSTOP)
        try:
            time.sleep(minutes["sleep"])
        finally:
            os.kill(app.proc.pid, signal.SIGCONT)
    ok = _hold(app, 20, "c. スリープから戻った直後")
    check("放置c(スリープ): 戻ってもサーバが終わらない", ok, "")
    _after_idle(sh, "放置c(スリープから戻る)")
    check("放置a〜c: サーバは同じプロセスのまま(開き直しになっていない)",
          (app.health() or {}).get("pid") == pid, pid)

    report = note.get("report")
    if report is not None and not report.is_closed():
        # 帳票の別タブを開いたまま統合画面を閉じてしまい、すぐ開き直す
        sh.page.close(run_before_unload=True)
        time.sleep(1.5)
        sh.page = ctx.new_page()
        sh.page.goto(app.base + "/?t=" + app.token)
        wait_until(lambda: all(sh.frame(k) for k in ("details", "pena", "material")), 20)
        sh.page.wait_for_timeout(4000)
        trouble = sh.trouble()
        check("統合画面を閉じてすぐ開き直しても、3機能とも断られない", not trouble, trouble)
        sh.page.close(run_before_unload=True)
        ok = _hold(app, minutes["report"], "d. 統合画面を閉じ、帳票の別タブだけで放置")
        check("放置d(帳票の別タブだけ): サーバが終わらない", ok, "")
        report.bring_to_front()
        report.click('span.edit[data-placeholder="サイズ"]')
        report.keyboard.press("End")
        report.keyboard.type("-2")
        report.keyboard.press("Enter")
        check("放置d: 放置のあと帳票の書き足しが保存できる",
              wait_until(lambda: "保存しました" in text(report, "#editSaved"), 8), text(report, "#editSaved"))
        sh.page = ctx.new_page()
        sh.page.goto(app.base + "/?t=" + app.token)
        wait_until(lambda: all(sh.frame(k) for k in ("details", "pena", "material")), 20)
        sh.page.wait_for_timeout(2500)
        trouble = [] if sh.ready(15) else sh.trouble()
        check("放置d: 統合画面を開き直すと3機能とも使える", not trouble, trouble)
        d = sh.tab("details")
        check("放置d: 梱包明細は開いていたロットのまま", text(d, "#lotNo") == "A123456", text(d, "#lotNo"))

    # f. 本ツール以外のタブを開いて、本ツールのタブを裏へ。長く裏にあるとブラウザは
    #    タブを**捨てる**(Chrome のメモリセーバー・Edge のタブのスリープ)。捨てるときは
    #    何の合図も出さない。戻ると読み直される
    other = ctx.new_page()
    other.set_content("<h1>別のサイト</h1><script>setInterval(() => { let x = 0; "
                      "for (let i = 0; i < 2e5; i++) x += i; }, 200)</script>")
    for fr in sh.page.frames:
        try:
            fr.evaluate(_HIDE)
        except Exception:                          # noqa: BLE001
            pass
    time.sleep(1.5)
    for fr in sh.page.frames:                      # 捨てる = もう何も送れない
        try:
            fr.evaluate("() => { navigator.sendBeacon = () => true;"
                        " window.fetch = () => new Promise(() => {}); }")
        except Exception:                          # noqa: BLE001
            pass
    with Frozen([sh.page], hide=False):
        ok = _hold(app, minutes["hidden"], "f. 本ツールのタブが裏で捨てられる")
    check("放置f(ほかのタブを開き、裏のタブが捨てられる): サーバが終わらない", ok, "")
    sh.page.reload()
    wait_until(lambda: all(sh.frame(k) for k in ("details", "pena", "material")), 20)
    sh.page.wait_for_timeout(3000)
    trouble = [] if sh.ready(15) else sh.trouble()
    check("放置f: 戻る(読み直される)と3機能とも続けて使える", not trouble, trouble)
    d = sh.tab("details")
    check("放置f: 梱包明細は開いていたロットのまま", text(d, "#lotNo") == "A123456", text(d, "#lotNo"))
    other.close()

    # e. 全部閉じて放置 → 自分で終わる → 翌朝と同じく開き直す
    for pg in list(ctx.pages):
        pg.close(run_before_unload=True)
    try:
        app.proc.wait(timeout=40)
        ended = True
    except subprocess.TimeoutExpired:
        ended = False
    check("放置e: 全部閉じたら自分で終わる", ended, "")
    check("放置e: 終わったあと多重起動の印(ロック)が残らない",
          not (app.local / "runtime" / "main.lock").exists(), "")
    h = app.start()
    check("放置e: 翌朝と同じく起動し直せる", h, (h or {}).get("pid"))
    sh.page = ctx.new_page()
    sh.page.goto(app.base + "/?t=" + app.token)
    wait_until(lambda: all(sh.frame(k) for k in ("details", "pena", "material")), 20)
    sh.page.wait_for_timeout(2500)
    trouble = [] if sh.ready(15) else sh.trouble()
    check("放置e: 開き直した統合画面で3機能とも使える", not trouble, trouble)
    p = sh.tab("pena")
    check("放置e(残るもの): ペナラベルの検査番号と風袋計算の結果",
          text(p, "#lblKensaNo") == "X222222" and text(p, "#nw1") == "220.0",
          (text(p, "#lblKensaNo"), text(p, "#nw1")))
    m = sh.tab("material")
    material_ready(m)
    m.click('nav.rail a.rail__item[href*="/checklist"]')
    m.wait_for_load_state()
    m = sh.frame("material")
    material_ready(m)
    check("放置e(残るもの): 資材計算のチェックリスト2行", wait_until(lambda: text(m, "#sub").startswith("2 /"), 8),
          text(m, "#sub"))
    d = sh.tab("details")
    details_open_lot(d, "A123456")
    check("放置e(残るもの): 梱包明細の出力済みの明細(No1)",
          wait_until(lambda: visible(d, "#outputCard") and "A123456-No1" in text(d, "#outputCard"), 8),
          text(d, "#outputCard")[:40])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", choices=("flow", "alternate", "idle"))
    ap.add_argument("--quick", action="store_true", help="放置を短くする")
    ap.add_argument("--keep", action="store_true", help="試験用の置き場所を残す")
    args = ap.parse_args(argv)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright がありません(tools/smoke_shell.py と同じものが要ります)")
        return 2

    home = Path(tempfile.mkdtemp(prefix="cpt-e2e-"))
    make_sources(home)
    app = App(home)
    print("=" * 80)
    print("置き場所:", home)
    h = app.start()
    check("起動して取り込みまで済む(本物の start_app)", h, (h or {}).get("version_set", "起動しない"))
    if not h:
        return 1
    note: dict = {}
    errors: list = []
    with sync_playwright() as pw:
        exe = os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium")
        br = pw.chromium.launch(executable_path=exe if os.path.exists(exe) else None)
        ctx = br.new_context(viewport={"width": 1400, "height": 900})
        ctx.on("page", lambda p: p.on("pageerror", lambda e: errors.append(f"{p.url.split('?')[0]}: {e}")))
        ctx.on("page", lambda p: p.on("dialog", lambda d: d.accept()))
        sh = Shell(ctx, app)
        if args.only in (None, "flow", "alternate", "idle"):
            scenario_flow(app, ctx, sh, note)
        if args.only in (None, "alternate", "idle"):
            scenario_alternate(app, ctx, sh, note)
        if args.only in (None, "idle"):
            minutes = ({"visible": 125, "hidden": 125, "sleep": 30, "report": 110} if args.quick else
                       {"visible": 360, "hidden": 300, "sleep": 120, "report": 150})
            scenario_idle(app, ctx, sh, note, minutes)
        check("画面の JavaScript のエラーが無い", not errors, errors[:3])
        br.close()
    app.stop()
    logs = app.logs()
    bad = [l for l in logs.splitlines() if "| ERROR |" in l or "Traceback" in l]
    check("ログに ERROR・Traceback が無い", not bad, bad[:2])
    print("=" * 80)
    ng = [r for r in results if not r[1]]
    print("結果: %d 項目中 %d 件 NG" % (len(results), len(ng)))
    if not args.keep and not ng:
        import shutil
        shutil.rmtree(home, ignore_errors=True)
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
