"""多重起動の防止 (基盤仕様書 2.4)

【なぜ順番待ちが要るか】
ロックファイルを見るだけでは、**同時に押されたとき**に防げません。
判定からロックの書き込みまでは「調べる → ポートを決める → 待ち受ける →
ロックを書く」の4段で1秒弱かかり、その間に2つ目が起動すると、どちらも
「ロックなし」と判断して両方が立ち上がります。

実際に再現しました ── 2つとも同じポートを選び、ロックを2回上書きして、
**ロックの pid が実際に待ち受けているプロセスと食い違い**ました。

(資材計算の移植元の試験を、統合アプリの起動基盤に当てたもの)
"""
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import launch_guard
from common import app_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def local(tmp_path, monkeypatch):
    """ローカル領域をテスト専用にする。**本物のロックを触らない。**"""
    monkeypatch.setenv("COIL_PACKING_TOOLS_LOCAL_DIR", str(tmp_path))
    app_config.load(force=True)
    yield tmp_path
    app_config.load(force=True)


# ==================================================================
# 順番待ちそのもの
# ==================================================================
def test_順番待ちは1つずつしか通さない(local):
    with launch_guard.startup_gate("main") as first:
        assert first is True
        # 2つ目は入れない。待って、諦めて通る(起動を止めない)
        start = time.monotonic()
        with launch_guard.startup_gate("main", wait_sec=0.3) as second:
            assert second is False
        assert time.monotonic() - start >= 0.3


def test_順番待ちは解けばまた通れる(local):
    with launch_guard.startup_gate("main") as first:
        assert first is True
    with launch_guard.startup_gate("main") as again:
        assert again is True


def test_待ちきれなくても起動は止めない(local):
    """締められない環境で起動できなくなる、を作らない"""
    with launch_guard.startup_gate("main"):
        with launch_guard.startup_gate("main", wait_sec=0.1) as second:
            # False(締められなかった)でも、`with` の中は実行される
            assert second is False


def test_順番待ちの印はロック本体と別のファイル(local):
    """同じファイルだと、締めるための open が中身の読み書きとぶつかる"""
    assert launch_guard.gate_path("main") != launch_guard.lock_path("main")
    assert launch_guard.gate_path("main").name == "main.start"


def test_知らないモードは断る(local):
    with pytest.raises(ValueError):
        launch_guard.gate_path("そんなモード")


# ==================================================================
# 本当に2つ立ち上がらないか(別プロセスで確かめる)
# ==================================================================
# 同じプロセスの中の `flock` では、OS をまたぐ本当の排他は確かめられない。
# **別プロセスを2つ**立てて、片方しか通れないことを見る。
_WORKER = textwrap.dedent("""
    import json, sys, time
    sys.path.insert(0, {root!r})
    import launch_guard
    with launch_guard.startup_gate("main", wait_sec=5) as held:
        # 通った側は少し居座る。その間に相手が入れたら排他が効いていない
        if held:
            time.sleep({hold})
        print(json.dumps({{"held": held, "at": time.time()}}))
""")


def _run_worker(hold: float, env: dict) -> subprocess.Popen:
    code = _WORKER.format(root=str(ROOT), hold=hold)
    return subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8", errors="replace", env=dict(env, PYTHONIOENCODING="utf-8"))


def test_別プロセスでも1つずつしか通らない(local):
    env = dict(os.environ, COIL_PACKING_TOOLS_LOCAL_DIR=str(local))
    a = _run_worker(1.0, env)
    time.sleep(0.15)          # 少しずらして2つ目を出す
    b = _run_worker(0.0, env)

    out_a = a.communicate(timeout=30)[0].strip()
    out_b = b.communicate(timeout=30)[0].strip()

    got_a = json.loads(out_a.splitlines()[-1])
    got_b = json.loads(out_b.splitlines()[-1])

    # 先に入ったほうが通り、あとから来たほうは**待たされてから**通る
    assert got_a["held"] is True
    assert got_b["held"] is True
    # あとから来たほうは、先のほうが出るまで入れていない
    assert got_b["at"] >= got_a["at"] - 0.05


def test_モードが違えば同時に通れる(local):
    """現場と資材を同時に起動してよい、という設計を壊さない

    このツールはモードが1つなので、いまは効きませんが、増えたときに
    互いを待たせない形であることを押さえておきます。
    """
    from common import modes
    assert launch_guard.gate_path(modes.MAIN).name.startswith(modes.MAIN)
