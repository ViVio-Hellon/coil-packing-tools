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


def children(pid: int) -> list[int]:
    """pid の子孫(孫も)。"""
    if WINDOWS:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
            capture_output=True, text=True).stdout
        pairs = [tuple(map(int, line.split())) for line in out.splitlines() if line.strip()]
    else:
        pairs = []
        for name in os.listdir("/proc"):
            if name.isdigit():
                try:
                    stat = Path(f"/proc/{name}/stat").read_text()
                    pairs.append((int(name), int(stat.rsplit(")", 1)[1].split()[1])))
                except (OSError, ValueError, IndexError):
                    pass
    found, frontier = [], [pid]
    while frontier:
        parent = frontier.pop()
        for child, ppid in pairs:
            if ppid == parent and child not in found:
                found.append(child)
                frontier.append(child)
    return found


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


def alive(pid: int) -> bool:
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    return os.path.exists(f"/proc/{pid}")


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
        tree = {app.pid, *children(app.pid)}
        ports = listening_ports(tree)
        if ports:
            print(f"[NG] 待ち受けているプロセスがあります: {ports}")
            ok = False
        else:
            print(f"[OK] 待ち受けはありません(調べたプロセス {len(tree)} 個)")
        others = [pid for pid in tree if pid != app.pid]
        app.terminate()
        app.wait(timeout=30)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and any(alive(p) for p in others):
            time.sleep(0.5)
        left = [p for p in others if alive(p)]
        if left:
            print(f"[NG] exe を止めても残ったプロセスがあります: {left}")
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
