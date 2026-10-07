#!/usr/bin/env python3
"""デスクトップ版(exe)を本当に起動して確かめる(GitHub Actions の Windows でも流す)

python-web-tools の同名の台本を移した。確かめること:
    1. 窓の画面(WebView)が Python から届く
       ── ログに `"GET /details/meisai" 200` のような行が出る
          (WebView → 外枠(Rust)→ Python → 外枠 → WebView が1周した証拠。
           統合画面の中の梱包明細のタブまで読めている)
    2. **どのプロセスもポートで待ち受けていない**(exe・Python・WebView の子プロセス)
    3. exe を止めると Python も終わる(取り残さない)

使い方:
    python scripts/desktop_smoke.py --exe src-tauri/target/release/CoilPackingTools.exe
    (Linux では DISPLAY が要る。例: xvfb-run python scripts/desktop_smoke.py --exe ...)

作業用のフォルダ(手元DB・設定・ログ)は一時フォルダに作る。本番の領域は触らない。
取り込み元は無い(取り込みは失敗するが、画面は出る)。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"


def isolated_env(work: Path) -> dict:
    """3機能と統合アプリの置き場所を、使い捨てのフォルダへ。"""
    env = dict(os.environ)
    env.update(
        COIL_PACKING_TOOLS_ROOT=str(ROOT),
        COIL_PACKING_TOOLS_LOCAL_DIR=str(work / "local"),
        COIL_PACKING_TOOLS_LOG_DIR=str(work / "logs"),
        COIL_PACKING_TOOLS_SETTINGS_PATH=str(work / "local" / "settings.json"),
        COIL_PACKING_TOOLS_DISTRIBUTION_DIR=str(work / "dist" / "common"),
        PACKING_DETAILS_DB_PATH=str(work / "details" / "packing_details.db"),
        PACKING_DETAILS_CONFIG_PATH=str(work / "details" / "user_config.json"),
        PACKING_DETAILS_SHARED_DIR=str(work / "details" / "share"),
        PACKING_DETAILS_DISTRIBUTION_DIR=str(work / "details" / "dist"),
        COIL_TOOL_DB_PATH=str(work / "material" / "coil_tool.db"),
        COIL_TOOL_CONFIG_PATH=str(work / "material" / "user_config.json"),
        COIL_TOOL_DISTRIBUTION_DIR=str(work / "material" / "dist"),
        COIL_TOOL_MASTER_DB_DIR=str(work / "material" / "master"),
        COIL_TOOL_LOT_DB_DIR=str(work / "material" / "lot"),
        PACKING_PENA_DISTRIBUTION_DIR=str(work / "pena" / "dist"),
        PPL_PREFER_ACCESS="0",
    )
    if WINDOWS:
        env["LOCALAPPDATA"] = str(work / "localappdata")
    else:
        env["XDG_DATA_HOME"] = str(work / "xdg-data")
        env["XDG_STATE_HOME"] = str(work / "xdg-state")
    if os.environ.get("SMOKE_PYTHON"):
        env["COIL_PACKING_TOOLS_PYTHON"] = os.environ["SMOKE_PYTHON"]
    return env


def processes() -> dict:
    """いま動いているプロセス: pid → (親の pid, 起動した時刻, 名前)。

    **pid だけで見ない。** 終わったプロセスの番号は、Windows ではすぐ別のプロセスに
    使い回される(この台本が起こす powershell 自身のことも)。番号だけで「まだ動いている」
    と見ると、残っていないのに残っていると読み違える(GitHub Actions で一度あった)。
    起動した時刻が同じものだけを同じプロセスとみなす。
    """
    found: dict = {}
    if WINDOWS:
        script = ("Get-CimInstance Win32_Process | ForEach-Object { $t = 0; "
                  "if ($_.CreationDate) { $t = $_.CreationDate.ToFileTimeUtc() }; "
                  "\"$($_.ProcessId)|$($_.ParentProcessId)|$t|$($_.Name)\" }")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, errors="replace").stdout
        for line in out.splitlines():
            cols = line.strip().split("|", 3)
            if len(cols) == 4 and cols[0].isdigit() and cols[1].isdigit():
                found[int(cols[0])] = (int(cols[1]), cols[2], cols[3])
        return found
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                stat = Path(f"/proc/{name}/stat").read_text()
                head, rest = stat.rsplit(")", 1)
                cols = rest.split()
                found[int(name)] = (int(cols[1]), cols[19], head.split("(", 1)[1])
            except (OSError, ValueError, IndexError):
                pass
    return found


def children(pid: int, procs: dict) -> list[int]:
    """pid の子孫(孫も)。"""
    found, frontier = [], [pid]
    while frontier:
        parent = frontier.pop()
        for child, (ppid, _started, _name) in procs.items():
            if ppid == parent and child not in found:
                found.append(child)
                frontier.append(child)
    return found


def still_running(before: dict, pids: list) -> list:
    """`before` で見たプロセスのうち、同じもの(番号と起動した時刻が同じ)がまだ動いているもの。"""
    now = processes()
    return [p for p in pids if p in now and p in before and now[p][1] == before[p][1]]


def listening_ports(pids: set) -> dict:
    """その pid たちが待ち受けている TCP ポート。"""
    result: dict = {}
    if WINDOWS:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        out += subprocess.run(["netstat", "-ano", "-p", "TCPv6"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            cols = line.split()
            if len(cols) >= 5 and cols[3].upper() == "LISTENING" and cols[4].isdigit():
                pid = int(cols[4])
                if pid in pids:
                    result.setdefault(pid, []).append(int(cols[1].rsplit(":", 1)[1]))
        return result
    inodes: dict = {}
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        if os.path.exists(name):
            for line in Path(name).read_text().splitlines()[1:]:
                cols = line.split()
                if cols[3] == "0A":
                    inodes[cols[9]] = int(cols[1].split(":")[1], 16)
    for pid in pids:
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                result.setdefault(pid, []).append(inodes[target[8:-1]])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--timeout", type=float, default=150)
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="desktop_smoke_"))
    env = isolated_env(work)
    started = time.monotonic()
    app = subprocess.Popen([args.exe], env=env, cwd=str(ROOT))
    print(f"起動しました: pid={app.pid} 作業={work}", flush=True)

    ok = True
    footprint = re.compile(r'"GET /details/meisai[^"]*" 200')
    seen = ""
    while time.monotonic() - started < args.timeout:
        if app.poll() is not None:
            print(f"[NG] exe が先に終わりました(終了コード {app.returncode})")
            ok = False
            break
        logs = sorted((work / "logs").glob("coil_packing_tools_*.log"))
        text = logs[-1].read_text(encoding="utf-8", errors="replace") if logs else ""
        match = footprint.search(text)
        if match:
            seen = match.group(0)
            break
        time.sleep(0.5)
    logs = sorted((work / "logs").glob("*.log"))
    if seen:
        print(f"[OK] 画面が Python から届きました({time.monotonic() - started:.1f}秒): {seen}")
        text = logs[-1].read_text(encoding="utf-8", errors="replace")
        for key in ("外枠からの要求を受け付けます", "待機画面まで"):
            line = next((l for l in text.splitlines() if key in l), "")
            print(f"     {line[:160]}")
    elif ok:
        print("[NG] 時間内に画面が届きませんでした。ログの末尾:")
        for log in logs:
            print(f"--- {log.name}")
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
        ok = False

    if app.poll() is None:
        before = processes()
        tree = {app.pid, *children(app.pid, before)}
        ports = listening_ports(tree)
        if ports:
            print(f"[NG] 待ち受けているプロセスがあります: {ports}")
            ok = False
        else:
            names = ", ".join(sorted(before[p][2] for p in tree if p in before))
            print(f"[OK] 待ち受けはありません(調べたプロセス {len(tree)} 個: {names})")
        others = [pid for pid in tree if pid != app.pid]
        app.terminate()
        app.wait(timeout=30)
        deadline = time.monotonic() + 20
        left = still_running(before, others)
        while left and time.monotonic() < deadline:
            time.sleep(1)
            left = still_running(before, others)
        if left:
            print("[NG] exe を止めても残ったプロセスがあります: "
                  + ", ".join(f"{p}({before[p][2]})" for p in left))
            ok = False
        else:
            print("[OK] exe を止めたら子プロセス(Python など)も終わりました")
    bad = []
    for log in logs:
        bad += [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()
                if "| ERROR |" in l or "Traceback" in l]
    if bad:
        print("[注意] ログに ERROR があります(取り込み元が無いことによるものは問題なし):")
        print("\n".join(bad[:10]))
    print("結果:", "OK" if ok else "NG")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
