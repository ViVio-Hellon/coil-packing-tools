# -*- coding: utf-8 -*-
"""Access(.accdb) 読み取りブリッジ。

ライン PC には ACE はあるが Python に追加ライブラリを入れられない、という制約のため
``cscript.exe`` + VBScript + ADODB(COM) + ACE OLEDB 経由で読む。

    Python -> subprocess -> cscript.exe -> VBScript -> ADODB -> ACE -> .accdb

VBA の ``GetFieldsArr`` / ``GetRecordsArr`` と同じ戻りを作ることを目的とし、
接続文字列・カーソル種別（adOpenStatic + adLockReadOnly）も VBA に合わせている。

``query_accdb.vbs`` は **ASCII のみ・CRLF** で保存すること
    ``cscript.exe`` は .vbs を UTF-8 ではなく **端末の ANSI コードページ**
    （日本語 Windows なら CP932）で読む。
    日本語コメントを UTF-8 で保存すると解釈に失敗してスクリプトが動かない。
    しかも ``//B``（バッチモード）はエラーを表に出さないため、

        Access 読取の応答が空です: rc=1 stderr=

    としか分からない。ASCII だけにしておけば、どの端末のコードページでも
    読み違えようがない。日本語の説明はこのファイル（呼び出し側）に置く。

失敗したときは、``//B`` を外して一度だけ実行し直し、本当の原因を拾う
（``//T`` で時間を区切るので、ダイアログが出ても止まらない）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

#: VBA AdoOpenCode() と同じ
ACE_PROVIDER = "Provider=Microsoft.ACE.OLEDB.12.0;Data Source="

_VBS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "vbs", "query_accdb.vbs")


class AccessError(RuntimeError):
    """Access 読み取り失敗。"""


class AccessBridge:
    """cscript 経由の Access リーダー。"""

    def __init__(self, timeout_sec: int = 60, cscript: str = "cscript.exe"):
        self.timeout_sec = timeout_sec
        self.cscript = cscript

    # ------------------------------------------------------------ 判定
    @staticmethod
    def available() -> bool:
        """この環境で cscript 経由の読み取りが使えるか。"""
        return os.name == "nt" and os.path.exists(_VBS_PATH)

    # ------------------------------------------------------------ 読取
    # ------------------------------------------------------------
    def _run(self, cmd, timeout=None):
        """cscript を実行する。

        stdout は VBScript 側で ASCII へ escape 済みなので何で読んでもよい。
        stderr は **cscript 自身のメッセージ**で、端末のコードページで来る。
        UTF-8 で読むと文字化けして原因が分からなくなる。
        """
        return subprocess.run(
            cmd, capture_output=True, timeout=timeout or self.timeout_sec,
            stdin=subprocess.DEVNULL,
            encoding=("cp932" if os.name == "nt" else "utf-8"),
            errors="replace",
        )

    def cscript_candidates(self) -> List[str]:
        """試す ``cscript.exe`` の一覧（既定 → もう一方のビット数）。

        **ACE は 32bit / 64bit が別物**で、``cscript.exe`` のビット数と
        一致していないと ``Provider ... が見つかりません`` になる。
        Office が 32bit の端末は非常に多く、その場合 64bit の cscript
        （既定）からは読めない。そこで駄目なら反対側を試す。

            64bit プロセスから 32bit  … %SystemRoot%\\SysWOW64\\cscript.exe
            32bit プロセスから 64bit  … %SystemRoot%\\Sysnative\\cscript.exe
                                        （WOW64 の仮想フォルダー）
        """
        out = [self.cscript]
        if os.name != "nt":
            return out
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        for sub in ("SysWOW64", "Sysnative", "System32"):
            path = os.path.join(root, sub, "cscript.exe")
            if path not in out and (sub == "Sysnative" or os.path.exists(path)):
                out.append(path)
        return out

    def _diagnose(self, args) -> str:
        """``//B`` が握り潰したエラーを、一度だけ拾い直す。

        ``//B``（バッチモード）はスクリプトのエラーもダイアログも出さないため、
        構文エラーでも rc しか分からない。原因を知るために ``//B`` を外して
        実行し直す。``//T`` で時間を区切るので、万一ダイアログが出ても
        待たされない。
        """
        try:
            proc = self._run([self.cscript, "//nologo", "//T:10",
                              _VBS_PATH] + list(args), timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            return "（詳細の取得に失敗: %s）" % exc
        detail = ((proc.stderr or "") + (proc.stdout or "")).strip()
        return ("\n詳細: %s" % detail[:500]) if detail else ""

    def fetch(self, db_path: str, table_name: str,
              sql_filter: str = "") -> Tuple[List[str], List[List[Optional[str]]]]:
        """テーブルを読み、``(フィールド名リスト, レコード2次元リスト)`` を返す。

        VBA の ``GetFieldsArr`` / ``GetRecordsArr`` に相当。
        """
        if not os.path.exists(_VBS_PATH):
            raise AccessError("VBScript が見つかりません: %s" % _VBS_PATH)
        if not os.path.exists(db_path):
            # VBA adoConnection は PathCheck で Nothing を返して無言終了するが
            # 移植では原因が分かるようにする
            raise AccessError("Access ファイルが見つかりません: %s" % db_path)

        args = [db_path, table_name, sql_filter or ""]
        log.info("Access 読取: table=%s filter=%r db=%s", table_name, sql_filter, db_path)

        # ACE のビット数が合わないと接続できないので、駄目なら別の
        # cscript.exe（32bit / 64bit）でもう一度試す。
        errors = []
        for i, exe in enumerate(self.cscript_candidates()):
            try:
                proc = self._run([exe, "//nologo", "//B", _VBS_PATH] + args)
            except FileNotFoundError:
                continue                      # その場所に cscript が無い
            except subprocess.TimeoutExpired as exc:
                raise AccessError("Access 読取がタイムアウトしました (%ds)"
                                  % self.timeout_sec) from exc

            out = (proc.stdout or "").strip()
            if not out:
                errors.append("%s: 応答が空 rc=%s stderr=%s"
                              % (exe, proc.returncode,
                                 (proc.stderr or "").strip()))
                continue

            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                errors.append("%s: 応答を解釈できません %s" % (exe, out[:200]))
                continue

            if data.get("ok"):
                if i:
                    log.info("Access 読取は %s で成功しました"
                             "（既定の cscript とは別のビット数）", exe)
                break
            errors.append("%s: %s" % (exe, data.get("error") or "読取に失敗"))
        else:
            raise AccessError("Access を読めません。\n" + "\n".join(errors)
                              + self._diagnose(args))

        fields = [str(f) for f in data.get("fields", [])]
        records = [list(r) for r in data.get("records", [])]
        log.info("Access 読取 完了: %d 列 / %d 行", len(fields), len(records))
        return fields, records
