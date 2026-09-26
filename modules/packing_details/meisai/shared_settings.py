"""全ラインで共有する設定 (紙面の右上の文字・管理者パスワード)

【何を共有するか】
- 紙面の右上に刷る文字(既定 `NLM.NAGOYA.QA`。`qa_mark`)
- それを守る管理者パスワード(撹拌済み。`admin_password`)

**どのラインPCで変えても、全ラインの次の紙面に出る。**

【どこに置くか ── 正は梱包資材マスタ、無ければJSON】
梱包資材マスタのフォルダ(`config.SHARED_DIR`)に2つ:

    梱包資材マスタ.sqlite3 の表「梱包明細打ち出し」   … **正**
        ID   設定文字列
        1    NLM.NAGOYA.QA        ← ID=1 の行が右上の文字

    梱包明細打ち出し.json                            … 控え
        {"format": 1, "qa_mark": "...", "admin_password": "pbkdf2$...",
         "updated_at": "...", "updated_by": "PC名 / 利用者"}

右上の文字は**梱包資材マスタの表を正とする**(現場の指定)。表は現場が
Access で作り、梱包資材総合ツールの「表を持ってくる」で足したもので、
総合ツールのマスタ管理からも直せる。

**マスタに表(か ID=1 の行)が無ければ、JSONを見る。** 表を消された・
マスタを置き換えた、でも紙は止めない。JSON は右上の文字の控えとして、
このアプリが変えるたびに一緒に書き、マスタのほうが直されていたら
それに合わせる(`_sync_mirror`)。

**管理者パスワードは JSON だけ。** 表は値の列が1つで、撹拌した値を入れると
総合ツールのマスタ管理の画面に出て、そこで直せてしまう。

【書き方】
- マスタ: 1行を書き換えるだけ(無ければ ID=1 で1行足す)。**表は作らない・
  形は変えない**(表を足すのは総合ツールの「表を持ってくる」の役目)。
  マスタは DELETE で置かれている(WAL だと共有の上では書けない)
- JSON: 「別名で書いて、置き換える」(`os.replace`)── 読む側は古い中身か
  新しい中身のどちらかを見る。書きかけは見ない
- どちらも、共有に置いた鍵(`梱包明細打ち出し.json.lock`)を取ってから書く
  (このアプリどうしで1台ずつ)

【届かないとき】
紙を止めない。**この端末が最後に読んだ値**(`user_settings` の写し)で刷り、
紙面の画面に「共有に届かないので、いつ時点の値で刷っている」と出す
(紙には出ない)。写しも無ければ既定で刷る。

**変えるほうは断る。** 届かないまま手元だけ変えると、ラインごとに違う紙が
出る ── 共有した意味がなくなる。マスタが読めない(掴まれている・壊れて
いる)ときも、JSONにだけ書いたりはしない(次にマスタが読めたとき、黙って
古い値に戻る)。

【待たせない】
共有が落ちていると、Windows はファイルを開くだけで数十秒待つことがある。
紙面を開くたびにそれを待たせるわけにいかないので、**読むのは
`READ_TIMEOUT_SEC` まで**。待ちきれなければ写しで刷る(読みは裏で続く)。
"""
from __future__ import annotations

import getpass
import json
import os
import secrets
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config, source_db, user_settings
from .logging_utils import get_logger

log = get_logger("shared_settings")

# 控えのJSON。**表と同じ名前**にして、並べたときに対だと分かるようにする
FILE_NAME = "梱包明細打ち出し.json"
FORMAT = 1

# 梱包資材マスタの表(現場が足したもの)。**ID=1 の行が右上の文字**
MASTER_TABLE = "梱包明細打ち出し"
MASTER_ID_COLUMN = "ID"
MASTER_VALUE_COLUMN = "設定文字列"
MASTER_QA_ID = 1

# 共有を読むのに待つ上限。**紙面を開くたびに読む**ので短く
READ_TIMEOUT_SEC = 2.0

# 書き込みの順番待ち。2台が同時に変えると、後から書いたほうが先の変更
# (右上の文字とパスワードの片方)を消してしまう。**1台ずつ**書かせる
LOCK_NAME = FILE_NAME + ".lock"
LOCK_WAIT_SEC = 5.0
# これより古い鍵は、書いた端末が落ちて残ったものとみなして外す
LOCK_STALE_SEC = 30.0

# 置き換えは、ほかの端末がちょうど読んでいると断られることがある
# (Windows は開いているファイルを置き換えさせない)。読むのは一瞬なので
# 少し待って繰り返す
REPLACE_TRIES = 20
REPLACE_WAIT_SEC = 0.1

# 読んだ値の写し(`user_settings`)。**どの共有から読んだか**も持つ ──
# 置き場所を変えたあとに、前の共有の写しで刷らないため
CACHE_KEY = config.KEY_SHARED_CACHE

# 共有に持つ値の名前
KEYS = ("qa_mark", "admin_password")

# 右上の文字の出どころ(`Snapshot.values["qa_origin"]`)
ORIGIN_MASTER = "master"        # 梱包資材マスタの表
ORIGIN_JSON = "json"            # 控えのJSON(表か行が無い・マスタが読めない)
ORIGIN_DEFAULT = "default"      # どちらにも無い


class SharedError(RuntimeError):
    """共有に届かない・読めない・書けない。"""


@dataclass
class Snapshot:
    """読んだ結果。`source` は値の出どころ。

    shared … いま共有から読んだ
    cache  … 共有に届かないので、この端末が最後に読んだ写し
    none   … 共有に届かず写しも無い(既定で刷る)
    """

    values: dict[str, Any] = field(default_factory=dict)
    source: str = "shared"
    path: Path = Path()             # 共有フォルダ
    problem: str = ""               # 届かない理由(source が shared なら空)
    read_at: str = ""
    master: "Master" = field(default_factory=lambda: Master())
    json_problem: str = ""          # 控えのJSONが読めない理由

    def get(self, key: str) -> Any:
        return self.values.get(key)

    @property
    def reachable(self) -> bool:
        return self.source == "shared"

    @property
    def qa_origin(self) -> str:
        return str(self.values.get("qa_origin") or ORIGIN_DEFAULT)

    def to_dict(self) -> dict[str, Any]:
        """画面に出す分。**パスワードは出さない。**"""
        return {
            "share_path": str(self.path),
            "share_source": self.source,
            "share_problem": self.problem,
            "share_read_at": self.read_at,
            "share_updated_at": str(self.values.get("updated_at") or ""),
            "share_updated_by": str(self.values.get("updated_by") or ""),
            "share_json": str(self.path / FILE_NAME),
            "share_json_problem": self.json_problem,
            "share_master": str(self.master.path or ""),
            "share_master_state": self.master.state if self.reachable else "",
            "share_master_problem": self.master.problem,
            "share_master_note": self.master.note,
            "share_master_table": MASTER_TABLE,
            "qa_origin": self.qa_origin,
        }


@dataclass
class Master:
    """梱包資材マスタの表「梱包明細打ち出し」を読んだ結果。"""

    path: Optional[Path] = None     # マスタのファイル(見つからなければ None)
    table: bool = False             # 表があるか
    value: Optional[str] = None     # ID=1 の 設定文字列(行が無い・空なら None)
    rowids: tuple[int, ...] = ()    # ID=1 の行(書き換える先)
    problem: str = ""               # 読めない理由(掴まれている・壊れている)
    note: str = ""                  # 読めたが気をつけること(ID=1 が2行ある等)

    @property
    def state(self) -> str:
        if self.problem:
            return "error"
        if self.path is None:
            return "no_file"
        if not self.table:
            return "no_table"
        if self.value is None:
            return "no_row"
        return "ok"


# ==================================================================
# 置き場所
# ==================================================================
def shared_dir() -> Path:
    """共有フォルダ。設定画面の値(端末ごと)を優先する。"""
    configured = user_settings.get(config.KEY_SHARED_DIR)
    if isinstance(configured, str) and configured.strip():
        return config.resolve_dir(configured)
    return config.SHARED_DIR


def shared_path() -> Path:
    """控えのJSON。"""
    return shared_dir() / FILE_NAME


def master_path() -> Optional[Path]:
    """梱包資材マスタ(見つからなければ None)。"""
    return source_db.find(shared_dir(), config.MASTER_DB_NAME)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def pc_name() -> str:
    """このPCの名前(Windows の COMPUTERNAME)。"""
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


def login_id() -> str:
    """いまのログインID。取れなければ空。"""
    try:
        return getpass.getuser()
    except Exception:                                     # noqa: BLE001
        return ""


def _who() -> str:
    """誰が変えたか。**あとで「誰が変えたのか」を聞かれたときのため。**"""
    return " / ".join(part for part in (pc_name(), login_id()) if part)


# ==================================================================
# 読む
# ==================================================================
def _read_json(path: Path) -> Optional[dict[str, Any]]:
    """控えのJSONを読む。**無ければ None**(このアプリでまだ誰も変えていない)。"""
    last = ""
    for _ in range(3):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except PermissionError as exc:          # ほかの端末が置き換え中
            last = str(exc)
            time.sleep(0.05)
            continue
        except OSError as exc:
            raise SharedError(f"共有の設定(JSON)を読めません: {exc}") from exc
        try:
            data = json.loads(text)
        except ValueError as exc:
            last = f"中身が壊れています: {exc}"
            time.sleep(0.05)
            continue
        if not isinstance(data, dict):
            raise SharedError(f"共有の設定(JSON)の形が違います: {path}")
        return data
    raise SharedError(f"共有の設定(JSON)を読めません({last}): {path}")


def _is_qa_id(value: Any) -> bool:
    """ID=1 か。Access から来た値は数(1)のことも文字('1')のこともある。"""
    try:
        return float(str(value).strip()) == MASTER_QA_ID
    except ValueError:
        return False


def _read_master(folder: Path) -> Master:
    """梱包資材マスタの表を読む。**例外にしない**(読めなければ `problem`)。

    表は小さい(現場が足した設定の表)ので全部読んで、ID=1 の行を探す。
    `ID` の列に型が無い(Access から持ってきた表)ので、SQL で `= 1` と
    比べると '1' と 1 が別物になる。読んでから比べる。
    """
    path = source_db.find(folder, config.MASTER_DB_NAME)
    if path is None:
        return Master()
    q = source_db.quote_identifier
    try:
        rows = source_db.read_query(
            path, f"SELECT rowid AS __rowid, {q(MASTER_ID_COLUMN)} AS id,"
                  f" {q(MASTER_VALUE_COLUMN)} AS value FROM {q(MASTER_TABLE)}")
    except source_db.SourceError as exc:
        if "no such table" in str(exc).lower():
            return Master(path)
        if "no such column" in str(exc).lower():
            return Master(path, True, problem=(
                f"表「{MASTER_TABLE}」に {MASTER_ID_COLUMN} / {MASTER_VALUE_COLUMN}"
                f" の列がありません: {exc}"))
        return Master(path, problem=f"梱包資材マスタを読めません: {exc}")
    hits = [r for r in rows if _is_qa_id(r["id"])]
    if not hits:
        return Master(path, True)
    raw = hits[0]["value"]
    value = None if raw is None or str(raw).strip() == "" else str(raw)
    note = ""
    if len(hits) > 1:
        note = (f"表「{MASTER_TABLE}」に ID={MASTER_QA_ID} の行が{len(hits)}行あります"
                "(上の行を使います。変えるときは全部書き換えます)")
    return Master(path, True, value, tuple(int(r["__rowid"]) for r in hits), "", note)


def _read_all(folder: Path) -> dict[str, Any]:
    """共有の2つを読む(マスタと控えのJSON)。**届かなければ SharedError。**

    フォルダが無いのは2通りある。

        上のフォルダはある → まだ作っていない(JSONだけで使う置き場所)
        上のフォルダも無い → 共有に届いていない

    前者を「届かない」と言うと、入れたばかりの全ラインが警告を出す。
    JSONが壊れていてもマスタは読む(逆も同じ)── 片方の不具合で、正しい
    ほうまで捨てない。
    """
    if not folder.is_dir():
        if folder.parent.is_dir():
            return {"json": None, "json_problem": "", "master": Master()}
        raise SharedError(f"共有フォルダに届きません: {folder}")
    json_data, json_problem = None, ""
    try:
        json_data = _read_json(folder / FILE_NAME)
    except SharedError as exc:
        json_problem = str(exc)
    return {"json": json_data, "json_problem": json_problem,
            "master": _read_master(folder)}


@dataclass
class _Job:
    """いま共有を読んでいる1本。**並んで来た要求は、これを一緒に待つ。**"""

    path: Path
    thread: threading.Thread
    box: dict[str, Any]
    started: float
    # 誰かが待ちきれなかった。**止まっているとみなし、以後は待たない**
    stalled: bool = False


_inflight_lock = threading.Lock()
_inflight: Optional[_Job] = None


def _read_bounded(path: Path, timeout: float) -> dict[str, Any]:
    """`_read_all` を `timeout` 秒まで待つ。`path` は共有フォルダ。

    **並んで来た要求は、読みかけの1本を一緒に待つ。** 紙面を開く要求と
    設定の読み込みは同時に来る。あとから来たほうが「読みかけがある」
    だけで写しに逃げると、**元気な共有を「届かない」と言ってしまう**
    (直す前はそうなっていた。試験が見つけた)。

    **待ちきれなかった読みは裏で続けさせ、次は並べない。** 共有が
    落ちているあいだ紙面を開くたびに新しい読みを積むと、止まった読みが
    溜まっていく。誰かが待ちきれなかった読み・すでに `timeout` を過ぎた
    読みには、待たずに写しを返す。
    """
    global _inflight
    with _inflight_lock:
        job = _inflight
        if job is not None and job.thread.is_alive() and job.path == path:
            waited = time.monotonic() - job.started
            if job.stalled or waited >= timeout:
                raise SharedError(
                    f"共有フォルダの応答を{waited:.0f}秒待っています: {path}")
            remaining = timeout - waited
        else:
            box: dict[str, Any] = {}

            def run() -> None:
                try:
                    box["data"] = _read_all(path)
                except BaseException as exc:              # noqa: BLE001
                    box["error"] = exc

            job = _Job(path, threading.Thread(target=run, name="shared-read",
                                              daemon=True), box, time.monotonic())
            _inflight = job
            job.thread.start()
            remaining = timeout
    job.thread.join(remaining)
    if job.thread.is_alive():
        job.stalled = True
        raise SharedError(f"共有フォルダが{timeout:g}秒以内に応えません: {path}")
    if "error" in job.box:
        error = job.box["error"]
        if isinstance(error, SharedError):
            raise error
        raise SharedError(f"共有の設定を読めません: {error}")
    return job.box["data"]


# 「届かない」になったとき・戻ったときだけログに出す。紙面を開くたびに
# 同じ警告を積まない
_last_problem: Optional[str] = None
_legacy_warned = False


def _note_problem(problem: str) -> None:
    global _last_problem
    if problem == (_last_problem or ""):
        return
    if problem:
        log.warning("共有の設定に届かないので、この端末の写しで刷ります: %s", problem)
    elif _last_problem:
        log.info("共有の設定に届くようになりました")
    _last_problem = problem


def _warn_legacy() -> None:
    """VER 0.7.0 の端末ごとの値が残っていたら、1度だけ言う。"""
    global _legacy_warned
    if _legacy_warned:
        return
    _legacy_warned = True
    for key in (config.KEY_QA_MARK, config.KEY_ADMIN_PASSWORD):
        if user_settings.get(key):
            log.warning("VER 0.7.0 の端末ごとの設定(%s)は使いません。"
                        "全ラインで共有の値を使います", key)


def _pick(data: dict[str, Any]) -> dict[str, Any]:
    return {k: data[k] for k in (*KEYS, "updated_at", "updated_by") if k in data}


def _compose(found: dict[str, Any]) -> dict[str, Any]:
    """マスタとJSONから、使う値を決める。**右上の文字はマスタが正。**"""
    master: Master = found["master"]
    values = _pick(found["json"] or {})
    json_qa = values.get("qa_mark")
    if master.value is not None:
        values["qa_mark"] = master.value
        values["qa_origin"] = ORIGIN_MASTER
    elif json_qa is not None and json_qa != "":
        # 刷れる値かどうかは `qa_mark` が見る(刷れなければ警告して既定)。
        # ここで黙って捨てると、壊れていることに誰も気づかない
        values["qa_origin"] = ORIGIN_JSON
    else:
        values.pop("qa_mark", None)
        values["qa_origin"] = ORIGIN_DEFAULT
    return values


def _store_cache(path: Path, values: dict[str, Any], read_at: str) -> None:
    """写しを残す。**中身が変わったときだけ書く**(紙面を開くたびに書かない)。"""
    cached = user_settings.get(CACHE_KEY)
    if (isinstance(cached, dict) and cached.get("path") == str(path)
            and cached.get("values") == values):
        return
    user_settings.save(CACHE_KEY, {"path": str(path), "values": values,
                                   "read_at": read_at})


# マスタのほうが直されていたら、控えのJSONを合わせる。**紙面を待たせない**
# よう裏で行い、同じ値では2度行わない
_mirror_lock = threading.Lock()
_mirror_done: Optional[tuple[str, str]] = None
_mirror_thread: Optional[threading.Thread] = None


def _sync_mirror(folder: Path, value: str) -> None:
    try:
        with _locked(folder):
            path = folder / FILE_NAME
            current = _read_json(path) or {}
            if current.get("qa_mark") == value:
                return
            _write_json(folder, {**current, "qa_mark": value, "format": FORMAT,
                                 "mirrored_at": _now()})
        log.info("控えのJSONを梱包資材マスタに合わせました: %r", value)
    except (SharedError, OSError) as exc:
        log.warning("控えのJSONを梱包資材マスタに合わせられませんでした: %s", exc)


def _mirror_later(folder: Path, found: dict[str, Any]) -> None:
    global _mirror_done, _mirror_thread
    master: Master = found["master"]
    if master.value is None or found["json_problem"]:
        return
    if (found["json"] or {}).get("qa_mark") == master.value:
        return
    key = (str(folder), master.value)
    with _mirror_lock:
        if _mirror_done == key:
            return
        _mirror_done = key
        _mirror_thread = threading.Thread(target=_sync_mirror,
                                          args=(folder, master.value),
                                          name="shared-mirror", daemon=True)
        _mirror_thread.start()


def read(*, timeout: float = READ_TIMEOUT_SEC) -> Snapshot:
    """いまの共有の値。**届かなくても例外にしない**(写しか既定で返す)。"""
    _warn_legacy()
    folder = shared_dir()
    try:
        found = _read_bounded(folder, timeout)
        if found["master"].value is None and found["json_problem"]:
            # マスタに値が無く、頼みの控えも読めない。**既定で刷らず**、
            # 届かないときと同じく最後に読んだ値で刷る
            raise SharedError(found["json_problem"])
    except SharedError as exc:
        problem = str(exc)
        _note_problem(problem)
        cached = user_settings.get(CACHE_KEY)
        if (isinstance(cached, dict) and cached.get("path") == str(folder)
                and isinstance(cached.get("values"), dict)):
            return Snapshot(dict(cached["values"]), "cache", folder, problem,
                            str(cached.get("read_at") or ""))
        return Snapshot({}, "none", folder, problem, "")
    _note_problem("")
    _note_master(found["master"])
    now = _now()
    values = _compose(found)
    _store_cache(folder, values, now)
    _mirror_later(folder, found)
    return Snapshot(values, "shared", folder, "", now, found["master"],
                    found["json_problem"])


_last_master_state: Optional[str] = None


def _note_master(master: Master) -> None:
    """マスタの様子が変わったときだけログに出す(表が消えた・戻った等)。"""
    global _last_master_state
    state = f"{master.state}:{master.problem}:{master.note}"
    if state == _last_master_state:
        return
    _last_master_state = state
    if master.problem:
        log.warning("梱包資材マスタを読めないので、控えのJSONで刷ります: %s",
                    master.problem)
    elif master.state in ("no_file", "no_table", "no_row"):
        log.info("梱包資材マスタに右上の文字がありません(%s)。控えのJSONを見ます",
                 master.state)
    if master.note:
        log.warning("%s", master.note)


# ==================================================================
# 書く
# ==================================================================
@contextmanager
def _locked(folder: Path, name: str = LOCK_NAME) -> Iterator[None]:
    """共有に鍵を置いて、書くのを1台ずつにする。

    `name` は鍵のファイル名。**ファイルごとに別の鍵**(明細の履歴は
    `slip_history` が自分の鍵を使う ── 右上の文字を変えるのと、履歴を
    送るのとを互いに待たせない)。

    鍵は「無ければ作る(あれば失敗)」で取る。ネットワークの共有でも
    効く、いちばん古いやり方。書いた端末が途中で落ちて鍵が残ったときは、
    `LOCK_STALE_SEC` を過ぎたら外す。
    """
    lock = folder / name
    deadline = time.monotonic() + LOCK_WAIT_SEC
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise SharedError(f"共有の鍵を確かめられません: {exc}") from exc
            if age > LOCK_STALE_SEC:
                log.warning("共有の鍵が%.0f秒残っていたので外します: %s", age, lock)
                try:
                    lock.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() > deadline:
                raise SharedError("ほかの端末が共有の設定を書き換えています。"
                                  "少し待ってからもう一度押してください。")
            time.sleep(0.1)
            continue
        except OSError as exc:
            raise SharedError(f"共有フォルダに書けません: {exc}") from exc
        try:
            os.write(fd, f"{_who()} {_now()}".encode("utf-8"))
        finally:
            os.close(fd)
        break
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _replace(tmp: Path, path: Path) -> None:
    last: Optional[OSError] = None
    for _ in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:          # ほかの端末が読んでいる
            last = exc
            time.sleep(REPLACE_WAIT_SEC)
    raise SharedError(f"共有の設定を置き換えられません: {last}")


def _write_json(folder: Path, data: dict[str, Any]) -> None:
    """控えのJSONを書く。**別名で書いてから置き換える。** 鍵の中で呼ぶこと。"""
    path = folder / FILE_NAME
    tmp = folder / f".{FILE_NAME}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        _replace(tmp, path)
    except OSError as exc:
        raise SharedError(f"共有フォルダに書けません: {exc}") from exc
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _write_master(master: Master, value: str) -> None:
    """マスタの表の ID=1 の行を書き換える(無ければ1行足す)。

    **表は作らない・形は変えない。** 書くのは `設定文字列` の1列だけ。
    ID=1 が2行あるときは全部書き換える ── どちらを読まれても同じ値になる。
    """
    assert master.path is not None
    q = source_db.quote_identifier
    try:
        with source_db.connect(master.path) as conn:
            if master.rowids:
                marks = ", ".join("?" for _ in master.rowids)
                conn.execute(
                    f"UPDATE {q(MASTER_TABLE)} SET {q(MASTER_VALUE_COLUMN)} = ?"
                    f" WHERE rowid IN ({marks})", [value, *master.rowids])
            else:
                conn.insert(MASTER_TABLE, {MASTER_ID_COLUMN: MASTER_QA_ID,
                                           MASTER_VALUE_COLUMN: value})
    except source_db.SourceError as exc:
        raise SharedError(f"梱包資材マスタに書けません: {exc}") from exc


@dataclass
class Written:
    """書いた結果。**どこに書いたか**を画面に言うため。"""

    snapshot: Snapshot
    to_master: bool = False         # 梱包資材マスタの表に書いた
    note: str = ""                  # 気をつけること(控えに書けなかった等)


def _ensure_folder(folder: Path) -> None:
    if folder.is_dir():
        return
    if not folder.parent.is_dir():
        raise SharedError(f"共有フォルダに届きません: {folder}")
    try:
        folder.mkdir()                          # 最初に変えたとき、1段だけ作る
    except FileExistsError:
        pass
    except OSError as exc:
        raise SharedError(f"共有フォルダを作れません: {folder} ({exc})") from exc
    log.info("共有の設定フォルダを作りました: %s", folder)


def update(changes: dict[str, Any]) -> Written:
    """共有の値を変える。**書けなければ SharedError**(手元だけ変えない)。

    右上の文字は、**マスタに表があれば表へ**(正)、控えのJSONにも同じ値を
    書く。表が無ければJSONだけ。マスタが読めない(掴まれている・壊れている)
    ときは**断る** ── JSONにだけ書くと、次にマスタが読めたとき黙って古い
    値に戻る。

    管理者パスワードはJSONだけ。ほかの値は残す(右上の文字を変えても
    パスワードは消えない)。鍵の中で読み直してから書く。
    """
    unknown = set(changes) - set(KEYS)
    if unknown:
        raise ValueError(f"共有しない値です: {sorted(unknown)}")
    folder = shared_dir()
    _ensure_folder(folder)

    to_master, note = False, ""
    with _locked(folder):
        if "qa_mark" in changes:
            master = _read_master(folder)
            if master.problem:
                raise SharedError(f"梱包資材マスタを読めないので変えていません。"
                                  f"{master.problem}")
            if master.path is not None and master.table:
                _write_master(master, str(changes["qa_mark"]))
                to_master = True
        try:
            current = _read_json(folder / FILE_NAME) or {}
            data = {**current, **changes, "format": FORMAT,
                    "updated_at": _now(), "updated_by": _who()}
            _write_json(folder, data)
        except SharedError as exc:
            if not to_master:
                raise
            # 正(マスタ)には書けた。控えだけ書けなかったことは言う
            note = f"控えのJSONには書けませんでした({exc})。"
            log.warning("控えのJSONに書けませんでした(マスタには書いた): %s", exc)

    found = _read_all(folder)
    now = _now()
    values = _compose(found)
    _store_cache(folder, values, now)
    _note_problem("")
    log.info("共有の設定を変えました: %s (%s)", ", ".join(sorted(changes)),
             "梱包資材マスタ＋控えのJSON" if to_master else "JSON")
    return Written(Snapshot(values, "shared", folder, "", now, found["master"],
                            found["json_problem"]), to_master, note)


def reset_for_tests() -> None:
    """試験用。ログの状態を戻す。"""
    global _last_problem, _legacy_warned, _inflight, _mirror_done, _last_master_state
    if _mirror_thread is not None:
        _mirror_thread.join(5)
    _last_problem = None
    _legacy_warned = False
    _inflight = None
    _mirror_done = None
    _last_master_state = None


def wait_mirror(timeout: float = 5.0) -> None:
    """試験用。控えのJSONを合わせる裏の処理が終わるのを待つ。"""
    if _mirror_thread is not None:
        _mirror_thread.join(timeout)


# 共有フォルダに鍵を置いて1台ずつ書く(ほかのモジュールから使う口)
folder_lock = _locked
