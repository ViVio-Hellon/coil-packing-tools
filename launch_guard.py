"""多重起動の防止とインスタンス管理 (基盤仕様書 2.4)

同じアプリが二重に起動すると、ポート競合・二重処理・設定ファイル競合が
起きる。そこで起動ごとに**ロックファイル**(PID・ポート・起動時刻・mode)を
書き、次の起動はそれを見て判断する。

    生きている同じアプリが居る → 新しく起動せず、ブラウザだけ開く
    死んだロックが残っている   → 消して続行する(記録は残す)

「生きているか」の判定は2段構えにする:

    1. PIDのプロセスが存在するか
    2. そのポートの `/api/health` が**同じ app_id と mode** を返すか

1だけでは足りない。PIDは使い回されるので、無関係なプロセスが同じ番号を
持っていることがある。2だけでも足りない。別のアプリがHTTPを返している
場合があるので、`app_id` の照合が要る(基盤仕様書 2.3)。

このモジュールは Flask に依存しない。起動の判断だけを持つので、
サーバが立たない状況でも動く。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from common import app_config, modes
from common.logging_utils import get_logger

log = get_logger("coil_packing_tools", "launch_guard")

# `/api/health` を叩くときの待ち時間(秒)。ローカルなので短くてよい。
# 長いと、死んだロックの掃除に毎回この分だけ待たされる
HEALTH_TIMEOUT_SEC = 1.5


@dataclass
class LockInfo:
    """`runtime/<mode>.lock` の中身。"""

    app_id: str
    mode: str
    pid: int
    port: int
    url: str
    started_at: float
    python: str = ""
    app_root: str = ""
    # 起動トークン。`stop.bat` が「正常終了を要求する」ために要る
    # (基盤仕様書 2.8 は、PIDで落とす前にまずアプリ自身へ頼むことを
    # 求めている)。ロックは利用者ごとのローカル領域にあり、
    # 読めるのは同じ利用者だけ。そこまで入れる相手はプロセスを直接
    # 落とせるので、ここに置いても守りの強さは変わらない。
    # 逆に、悪意あるWebページはローカルのファイルを読めないため、
    # トークンによる防御(別オリジンからの操作を弾く)はそのまま効く。
    token: str = ""

    @property
    def started_text(self) -> str:
        return time.strftime("%Y/%m/%d %H:%M:%S", time.localtime(self.started_at))


# ------------------------------------------------------------------
# 起動の順番待ち (基盤仕様書 2.4)
# ------------------------------------------------------------------
# **ロックファイルを見るだけでは、同時に押されたときに防げません。**
#
# 判定から書き込みまでは「調べる → ポートを決める → 待ち受ける →
# ロックを書く」の4段で、ここに1秒弱かかります。その間に2つ目が
# 起動すると、**どちらもロックが無いと判断して両方が立ち上がります**
# (実測: 2つとも同じポートを選び、ロックを2回上書きして、
#  ロックの pid が実際に待ち受けているプロセスと食い違った)。
#
# そこで、この4段を**1つずつしか通れない道**にします。2つ目は少し待ち、
# 通れるようになった時点でもう一度調べるので、1つ目のロックを見つけて
# 合流します。
#
# 締め方は環境で違うので、使えるものを順に試します。
#   Windows … `msvcrt.locking`
#   その他   … `fcntl.flock`
# どちらも使えなければ、締めずに通します ── **起動できないより、
# まれに2つ立ち上がるほうがまし**です(理由は下の `startup_gate`)。
try:                                              # pragma: no cover - 環境依存
    import fcntl
except ImportError:                               # pragma: no cover - Windows
    fcntl = None
try:                                              # pragma: no cover - 環境依存
    import msvcrt
except ImportError:                               # pragma: no cover - POSIX
    msvcrt = None

# 順番待ちの上限(秒)。1つ目が待ち受けを始めてロックを書くまでの
# 実測は1秒弱なので、その数倍を見ておく
GATE_WAIT_SEC = 15.0
GATE_POLL_SEC = 0.1


def gate_path(mode: str) -> Path:
    """順番待ちの印。**ロック本体とは別のファイル。**

    同じファイルを使うと、締めるために開いたことでロックの中身を
    読み書きする処理とぶつかります。
    """
    if mode not in modes.KEYS:
        raise ValueError(f"未知のmode: {mode!r}")
    return app_config.local_dir("runtime") / f"{mode}.start"


def _try_lock(handle) -> bool:
    """締められたら True。すでに誰かが通っていれば False。"""
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    if msvcrt is not None:                        # pragma: no cover - Windows
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    return True                                   # 締められない環境は素通し


def _unlock(handle) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:                           # pragma: no cover - 解放し損ね
            pass
    elif msvcrt is not None:                      # pragma: no cover - Windows
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


@contextmanager
def startup_gate(mode: str, *, wait_sec: float = GATE_WAIT_SEC):
    """起動の4段を1つずつしか通さない。

    2つ目はここで待ち、通れるようになってから `check_existing` を
    やり直します ── **待っているあいだに1つ目がロックを書き終える**ので、
    そこで合流できます。

    【待ちきれなくても通します】
    締められない環境(ファイルロックが効かない共有フォルダ等)や、
    1つ目が固まっている場合に、ここで起動そのものを止めると
    **アプリが二度と開けなくなります**。まれに2つ立ち上がるほうが、
    確実に開けないより害が小さいので、理由を残して通します。
    """
    path = gate_path(mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    held = False
    try:
        handle = open(path, "a+b")
        deadline = time.monotonic() + wait_sec
        waited = False
        while True:
            if _try_lock(handle):
                held = True
                break
            if time.monotonic() >= deadline:
                log.warning("起動の順番待ちが %.0f秒を過ぎました。"
                            "そのまま続けます", wait_sec)
                break
            if not waited:
                log.info("ほかの起動が進んでいます。順番を待ちます")
                waited = True
            time.sleep(GATE_POLL_SEC)
        yield held
    except OSError as exc:                        # noqa: BLE001 - 締められなくても続ける
        log.warning("起動の順番待ちを用意できませんでした(そのまま続けます): %s", exc)
        yield False
    finally:
        if handle is not None:
            if held:
                _unlock(handle)
            try:
                handle.close()
            except OSError:                       # pragma: no cover
                pass


def lock_path(mode: str) -> Path:
    """mode ごとに別のロック。現場と資材は同時に起動してよい。"""
    if mode not in modes.KEYS:
        raise ValueError(f"未知のmode: {mode!r}")
    return app_config.local_dir("runtime") / f"{mode}.lock"


# ------------------------------------------------------------------
# ロックファイル
# ------------------------------------------------------------------
def write_lock(info: LockInfo) -> Path:
    path = lock_path(info.mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(info), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    # トークンを含むので、本人だけが読める権限にする。
    # Windows には chmod が効かないが、`%LOCALAPPDATA%` 自体が
    # 利用者ごとのフォルダなので実質同じ扱いになる
    try:
        os.chmod(path, 0o600)
    except OSError as exc:                        # noqa: BLE001 - 権限設定は best effort
        log.debug("ロックの権限を変更できませんでした: %s", exc)
    log.info("ロックを書きました: %s (pid=%s port=%s)", path, info.pid, info.port)
    return path


def read_lock(mode: str) -> Optional[LockInfo]:
    """壊れていれば `None`。読めないことを理由に起動を止めない。"""
    path = lock_path(mode)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return LockInfo(**{k: raw[k] for k in LockInfo.__dataclass_fields__ if k in raw})
    except FileNotFoundError:
        return None
    except Exception as exc:                      # noqa: BLE001 - 壊れたロックは無視する
        log.warning("ロックを読めませんでした (%s): %s", path, exc)
        return None


def remove_lock(mode: str) -> None:
    try:
        lock_path(mode).unlink()
        log.info("ロックを消しました: %s", mode)
    except FileNotFoundError:
        pass
    except OSError as exc:                        # noqa: BLE001
        log.warning("ロックを消せませんでした: %s", exc)


# ------------------------------------------------------------------
# プロセスの生死
# ------------------------------------------------------------------
def is_process_alive(pid: int) -> bool:
    """そのPIDのプロセスが存在するか。中身までは見ない。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _is_alive_windows(pid)
    try:
        os.kill(pid, 0)          # シグナル0は存在確認だけ
    except ProcessLookupError:
        return False
    except PermissionError:
        return True              # 別ユーザーのプロセス = 生きている
    return True


def _is_alive_windows(pid: int) -> bool:
    """Windows では `tasklist` で確認する。

    `OpenProcess` を ctypes で叩く手もあるが、権限やハンドルの後始末を
    誤ると別の不具合を招く。起動時に1回だけの判定なので、
    外部コマンドの数十msは問題にならない。
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("tasklist を実行できませんでした: %s", exc)
        return True              # 分からないときは「生きている」に倒す
    return f'"{pid}"' in out.stdout


def process_command_line(pid: int) -> str:
    """そのPIDが何を実行しているか。**停止前の確認に使う**。

    基盤仕様書 2.8 は「Pythonをプロセス名だけで一括終了しない」ことを
    求めている。無関係なPythonアプリを巻き添えにしないため、
    落とす前にコマンドラインとアプリの場所を照合する。
    取れなければ空文字(呼び出し側は安全側に倒して落とさない)。
    """
    if pid <= 0:
        return ""
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get",
                 "CommandLine", "/format:list"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in out.stdout.splitlines():
                if line.startswith("CommandLine="):
                    return line.split("=", 1)[1].strip()
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("wmic を実行できませんでした: %s", exc)
        return ""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


# ------------------------------------------------------------------
# 起動確認API
# ------------------------------------------------------------------
# 自分自身(127.0.0.1)への通信に**プロキシを通さない**ための送信口。
#
# `urllib.request.urlopen()` の既定は、環境変数 `HTTP_PROXY` や
# Windowsのインターネット設定からプロキシを拾う。社内PCではたいてい
# プロキシが入っており、除外一覧に `127.0.0.1` が無いと、**自分自身への
# 通信までプロキシへ送られて失敗する**。
#
# 実際に踏んだ: サーバは待ち受けを始めているのに `/api/health` が
# 返らず、15秒待って「サーバを起動できませんでした」になった。
# ループバックにプロキシを挟む理由は無いので、ここで明示的に外す。
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def local_request(url: str, *, timeout: float, data: Optional[bytes] = None,
                  method: Optional[str] = None,
                  headers: Optional[dict] = None):
    """127.0.0.1 への要求。プロキシを経由しない。

    このアプリが外に出す通信はこれだけなので、送信口を1つに集約しておく。
    """
    request = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
    return _LOCAL_OPENER.open(request, timeout=timeout)


def probe_health(port: int, *, timeout: float = HEALTH_TIMEOUT_SEC) -> Optional[dict]:
    """`GET /api/health` を叩く。応答しなければ `None`。"""
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with local_request(url, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def is_port_accepting(port: int, *, timeout: float = 0.5) -> bool:
    """TCPで繋がるか。HTTPまでは見ない。

    「待ち受けていない」のか「待ち受けてはいるが応答が返らない」のかを
    分けるために使う。後者はプロキシやセキュリティ製品が挟まっている
    ことが多く、直し方がまったく違う。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def proxy_settings() -> dict[str, str]:
    """いま効いているプロキシ設定。原因調査のためだけに使う。"""
    try:
        return {k: v for k, v in urllib.request.getproxies().items()
                if k in ("http", "https")}
    except Exception:                    # noqa: BLE001 - 調査用なので握る
        return {}


def is_our_app(health: Optional[dict], mode: str) -> bool:
    """その応答が**自分と同じアプリの同じmode**か。

    `app_id` を見ないと、たまたま同じポートを使っている別のアプリを
    自分だと誤認する(基盤仕様書 2.3)。mode まで見るのは、現場と資材が
    別ポートで動く設計なので、取り違えると権限の分離が崩れるため。
    """
    if not health:
        return False
    return (health.get("app_id") == app_config.app_id()
            and health.get("mode") == mode)


# ------------------------------------------------------------------
# 既存インスタンスの判定
# ------------------------------------------------------------------
@dataclass
class GuardResult:
    """起動してよいか。"""

    should_start: bool
    url: str = ""
    existing: Optional[LockInfo] = None
    reason: str = ""
    # 動いているのが**古い版**だった場合、その版。合流してはいけない
    stale_version: str = ""


# 古い版が動いていたとき、終わるのを待つ上限(秒)
REPLACE_WAIT_SEC = 10.0


def check_existing(mode: str) -> GuardResult:
    """すでに同じアプリが動いていないか調べる。

    動いていれば `should_start=False` と、開くべきURLを返す。

    **ただし版が違えば合流しません**(統合ツールの版と3機能の版の組で比べる。
    `_stale_versions`)。入れ替えたのに古いプロセスが
    残っていると、ここが「すでに起動しています」と答えて古いほうの
    ブラウザを開き、**新しい版がいつまでも動きません**(画面の版バッジも
    古いままになる)。版が違うときは古いほうを終わらせて、こちらで
    立て直します。
    """
    info = read_lock(mode)
    if info is None:
        return GuardResult(True, reason="ロックなし")

    if not is_process_alive(info.pid):
        log.info("死んだロックを掃除します (pid=%s は不在)", info.pid)
        remove_lock(mode)
        return GuardResult(True, reason=f"ロックは残っていたがpid {info.pid} は不在")

    health = probe_health(info.port)
    if not is_our_app(health, mode):
        # プロセスは居るがアプリではない(PIDの使い回し、または
        # 起動途中で落ちた)。ロックを消して新しく起動する
        log.info("pid=%s は生きているが %s:%s は自分ではない",
                 info.pid, mode, info.port)
        remove_lock(mode)
        return GuardResult(True, reason="ロックのプロセスは別物だった")

    running = str((health or {}).get("version", ""))
    stale = _stale_versions(health or {})
    if stale:
        return _replace_stale(mode, info, running or "(不明)", " / ".join(stale))

    log.info("すでに起動しています: %s (pid=%s port=%s 版=%s)",
             mode, info.pid, info.port,
             str((health or {}).get("version_set", "")) or running or "(不明)")
    return GuardResult(False, url=info.url, existing=info,
                       reason="同じアプリが起動中")


def _stale_versions(health: dict) -> list:
    """動いているプロセスと、このファイルの版が違えば、その違い。同じなら空。

    **統合ツールの版と3機能の版の組で比べる**(common/versions.py)。機能の版
    だけを上げて配ったときも入れ替える ── 統合ツールの版だけで比べると、
    古いプロセスに合流して、新しい機能の中身がいつまでも動かない。
    組を答えない古いプロセスは、統合ツールの版だけで比べる。
    """
    from common import versions

    running = str(health.get("version", ""))
    running_set = str(health.get("version_set", ""))
    if running_set:
        try:
            mine = versions.all_versions()
        except Exception as exc:                    # noqa: BLE001 - 判定で起動を止めない
            log.warning("機能の版を読めませんでした(統合ツールの版だけで比べます): %s", exc)
            mine = {versions.APP_KEY: app_config.version()}
            theirs = {versions.APP_KEY: running}
        else:
            theirs = versions.parse_set(running_set)
        return versions.differences(theirs, mine)
    if running and running != app_config.version():
        return [f"{app_config.display_name()} {running} → {app_config.version()}"]
    return []


def _replace_stale(mode: str, info: "LockInfo",
                   running: str, changes: str) -> GuardResult:
    """動いているのが違う版。**終わらせてから立て直す。**

    `running` は動いているプロセスの統合ツールの版、`changes` は違いの説明
    (例 `梱包明細 0.13.1 → 0.13.2`)。

    処理の途中(取り込みなど)なら終わらせません ── 中途半端なデータを
    残すほうが害が大きいので、そのときは合流して、次の起動に任せます。
    """
    log.warning("動いているのは別の版です(%s)。古いほうを終わらせます", changes)
    if not request_shutdown(info.port, info.token):
        log.warning("古い版を終わらせられませんでした。合流します")
        return GuardResult(False, url=info.url, existing=info,
                           stale_version=running,
                           reason=f"別の版が動いていますが止められません({changes})")

    deadline = time.monotonic() + REPLACE_WAIT_SEC
    while time.monotonic() < deadline:
        if probe_health(info.port, timeout=0.3) is None:
            remove_lock(mode)
            log.info("古い版を終わらせました(%s)。新しい版で立て直します", changes)
            return GuardResult(True, stale_version=running,
                               reason=f"別の版を終わらせました({changes})")
        time.sleep(0.2)

    log.warning("古い版が %.0f秒 で終わりませんでした。合流します", REPLACE_WAIT_SEC)
    return GuardResult(False, url=info.url, existing=info,
                       stale_version=running,
                       reason=f"別の版が動いています({changes})")


def request_shutdown(port: int, token: str) -> bool:
    """`POST /api/shutdown` を送る。受け付けられたら `True`。

    **`force` は付けません。** 取り込みの途中なら 409 が返り、
    そのときは止めません(中途半端なデータを残さない)。
    `process_manager` にも同じ処理がありますが、あちらは停止そのものが
    仕事で、こちらは**起動の途中で古い版をどけるだけ**です。
    こちらから `process_manager` を読むと、起動の入口が停止側の
    モジュールに依存します(待機画面より前なので import を増やさない)。
    """
    body = json.dumps({"force": False}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Tool-Token"] = token
    try:
        with local_request(f"http://{app_config.host()}:{port}/api/shutdown",
                           timeout=5, data=body, method="POST",
                           headers=headers) as res:
            return bool(json.loads(res.read().decode("utf-8")).get("stopped"))
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            log.info("古い版は処理の途中なので止めません")
        else:
            log.info("古い版の停止要求が %s で断られました", exc.code)
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.info("古い版へ停止要求を送れませんでした: %s", exc)
        return False


# ------------------------------------------------------------------
# ポート
# ------------------------------------------------------------------
def is_port_free(port: int, host: str = "") -> bool:
    """そのポートで待ち受けを開始できるか。

    `SO_REUSEADDR` は**付けない**。付けると TIME_WAIT のポートまで
    「空いている」と判定してしまい、実際の bind で失敗する。
    """
    host = host or app_config.host()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(mode: str) -> Optional[int]:
    """使えるポートを1つ選ぶ。全部塞がっていれば `None`。

    候補が mode どうしで重ならないことは `app_config.port_range_conflicts()`
    が保証する(現場が繰り上がって資材のポートを奪わないようにするため)。
    """
    for port in app_config.port_candidates(mode):
        if is_port_free(port):
            return port
        log.info("ポート %s は使用中です", port)
    return None


# ------------------------------------------------------------------
# 起動時の記録
# ------------------------------------------------------------------
def build_lock_info(mode: str, port: int, token: str = "") -> LockInfo:
    return LockInfo(
        app_id=app_config.app_id(),
        mode=mode,
        pid=os.getpid(),
        port=port,
        url=f"http://{app_config.host()}:{port}/",
        started_at=time.time(),
        python=sys.executable,
        app_root=str(app_config.APP_ROOT),
        token=token,
    )
