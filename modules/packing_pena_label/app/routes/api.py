# -*- coding: utf-8 -*-
"""JSON API。VBA のボタン処理に 1 対 1 で対応させる。"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from ..config import code_stamp as _code_stamp
from ..models import FormState
from ..services import validation as V
from ..services.vba_compat import val

log = logging.getLogger(__name__)

#: 接続テストで存在を確かめる資材名（VBA がハードコードしていたキー）
_REQUIRED_MATERIALS = [
    "テスラピン", "チップボール1000ф", "ストレッチフィルム", "エサフォーム",
    "ポリシート", "PETバンド", "樹脂パレット", "EX-DRY",
    "ハードボード", "八角ハードボード", "HB590*2000", "HB530*2000",
]


#: 取得元の表示名
_SOURCE_LABEL = {"sqlite": "SQLite", "access": "Access(ACE)",
                 "csv": "CSV（検証用）"}


def _esc_lines(lines):
    """接続テストの表示行。<b> だけは残したいので最小限のエスケープに留める。"""
    import html as _html
    out = []
    for ln in lines:
        if "<b>" in ln:
            out.append(ln)
        else:
            out.append(_html.escape(ln))
    return out


def _visible_of(body) -> "Optional[bool]":
    """画面から来た ``visible``。無い・解釈できないなら None（変えない）。"""
    v = (body or {}).get("visible")
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("true", "false"):
        return v.lower() == "true"
    return None


class ApiRoutes:
    def __init__(self, wf, cfg, started_at: float, ctx=None):
        self.wf = wf
        self.cfg = cfg
        self.started_at = started_at
        #: 設定の即時反映で触る実体（server.AppContext）
        self.ctx = ctx

    # ------------------------------------------------------------
    def _state_from(self, body: Dict[str, Any]) -> FormState:
        """画面から来た値で状態を作る。保存済み状態へ上書きマージする。"""
        state = self.wf.load_state()
        cur = body.get("current") or {}
        if not cur:
            return state

        state.selected_ob = int(cur.get("selectedOb") or 0)
        state.selected_cb = int(cur.get("selectedCb") or 0)
        state.named_cb = bool(cur.get("namedCb"))
        tip = cur.get("tip") or state.tip
        state.tip = tip

        # 入力値は VBA の入力制限と同じ正規化をサーバー側でも行う
        state.kensa_no = V.filter_alnum_upper(cur.get("kensaNo") or "")
        state.weight1 = V.filter_numeric_text(cur.get("weight1") or "")
        state.weight2 = V.filter_numeric_text(cur.get("weight2") or "")
        coil = cur.get("coilH") or {}
        for i in (1, 2, 3, 4):
            state.coil_h[i] = V.filter_coil_text(
                str(coil.get(str(i), coil.get(i, "")) or ""))
        return state

    # ============================================================ ルート
    def routes(self) -> Dict[str, Any]:
        """公開するAPIの一覧。反射で呼ばず明示表で引く。"""
        return {
            "/api/health": self._health,
            "/api/state": self._state,
            "/api/select-size": self._select_size,
            "/api/select-checkbox": self._select_checkbox,
            "/api/apply-weight": self._apply_weight,
            "/api/calc-tare": self._calc_tare,
            "/api/progress": self._progress,
            "/api/calc-list": self._calc_list,
            "/api/clear": self._clear,
            "/api/print-targets": self._print_targets,
            "/api/quick-height": self._quick_height,
            "/api/materials": self._materials,
            "/api/reload-materials": self._reload_materials,
            "/api/shutdown": self._shutdown,
            "/api/all-size/prepare": self._all_size_prepare,
            "/api/all-size/submit": self._all_size_submit,
            "/api/all-size/print-targets": self._all_size_print_targets,
            "/api/settings/save": self._settings_save,
            "/api/settings/test-master": self._settings_test_master,
            "/api/settings/reset": self._settings_reset,
            "/api/settings/check-password": self._settings_check_password,
            "/api/label/align": self._label_align,
            "/api/settings/distribution/state": self._dist_state,
            "/api/settings/distribution/export": self._dist_export,
            "/api/settings/distribution/reapply": self._dist_reapply,
            "/api/settings/distribution/remove": self._dist_remove,
            "/api/settings/distribution/build": self._dist_build,
            "/api/master/tables": self._master_tables,
            "/api/master/rows": self._master_rows,
            "/api/master/save": self._master_save,
            "/api/master/delete": self._master_delete,
            "/api/screen/claim": self._screen_claim,
            "/api/screen/ping": self._screen_ping,
            "/api/screen/release": self._screen_release,
            "/api/screen/state": self._screen_state,
        }

    #: 作業状態を書き換えるAPI。入力画面を持っていない画面からは受け付けない。
    _STATE_WRITING = frozenset({
        "/api/select-size", "/api/select-checkbox", "/api/apply-weight",
        "/api/calc-tare", "/api/calc-list", "/api/clear",
        "/api/all-size/prepare", "/api/all-size/submit",
        "/api/settings/save", "/api/settings/reset", "/api/label/align",
        "/api/settings/distribution/export", "/api/settings/distribution/reapply",
        "/api/settings/distribution/remove", "/api/settings/distribution/build",
        "/api/reload-materials",
        "/api/master/save", "/api/master/delete",
    })

    def _progress(self, body):
        """時間のかかる処理(重量計算_DB)の進み具合。読むだけ。"""
        return {"ok": True, **self.wf.progress.snapshot()}

    def handle(self, path: str, method: str, body: Dict[str, Any]):
        """(status, payload) を返す。未知のパスは 404。"""
        fn = self.routes().get(path)
        if fn is None:
            return 404, {"ok": False, "message": "不明なAPIです: %s" % path}
        if method not in ("POST", "GET"):
            return 405, {"ok": False, "message": "メソッドが不正です"}

        # 使用権を持っていない画面からの書き換えは受け付けない。
        # 画面側でも塞いでいるが、こちらが本体（画面の作りに依存しない）。
        deny = self._deny_if_not_active(path, body)
        if deny is not None:
            return deny
        try:
            out = fn(body)
            # 断りを HTTP の番号で返したい処理は (番号, 中身) を返す
            if isinstance(out, tuple) and len(out) == 2 \
                    and isinstance(out[0], int):
                return out
            return 200, out
        except Exception as exc:                       # 予期しない失敗も画面へ返す
            log.exception("API 失敗: %s", path)
            return 500, {"ok": False, "level": "error",
                         "message": "処理に失敗しました: %s" % exc}

    # ------------------------------------------------------------ マスタ管理
    def _master_tables(self, body):
        from ..services import master_admin as MA
        repo = self.wf.materials
        return {"ok": True, "tables": MA.tables(repo),
                "editable": MA.can_edit(repo),
                "whyNot": MA.editable_why_not(repo),
                "sourcePath": MA.source_path(repo)}

    def _master_rows(self, body):
        from ..services import master_admin as MA
        try:
            return {"ok": True, **MA.page(self.wf.materials,
                                          str(body.get("table") or ""),
                                          str(body.get("keyword") or ""),
                                          sort=str(body.get("sort") or ""),
                                          sort_dir=str(body.get("sortDir") or "asc"))}
        except MA.Refused as exc:
            return exc.status, {"ok": False, "level": "warn",
                                "refuse": exc.kind, "message": exc.message}

    def _master_password_gate(self, body):
        """マスタを書き換えるには合言葉が要る。

        マスタを直すと**全員の計算・印刷に影響する**ので、
        参照先の変更と同じ扱いにする。
        """
        from ..services import master_admin as MA
        from ..services import settings as S
        if S.password_ok(body.get("password")):
            return None
        return MA.STATUS[MA.REFUSE_NOT_ALLOWED], {
            "ok": False, "level": "warn", "needPassword": True,
            "refuse": MA.REFUSE_NOT_ALLOWED,
            "message": "マスタの書き換えには合言葉が要ります。",
        }

    def _master_save(self, body):
        from ..services import master_admin as MA
        deny = self._master_password_gate(body)
        if deny is not None:
            return deny
        row_id = body.get("rowId")
        try:
            row_id = None if row_id in (None, "", "new") else int(row_id)
        except (TypeError, ValueError):
            return 400, {"ok": False, "level": "warn",
                         "refuse": MA.REFUSE_BAD_VALUE,
                         "message": "行の指定が不正です"}
        try:
            r = MA.save_row(self.wf.materials, str(body.get("table") or ""),
                            row_id, body.get("values") or {})
        except MA.Refused as exc:
            return exc.status, {"ok": False, "level": "warn",
                                "refuse": exc.kind, "message": exc.message}
        self.wf.refresh_label_sheets(force=True)
        return {"level": "info", **r}

    def _master_delete(self, body):
        from ..services import master_admin as MA
        deny = self._master_password_gate(body)
        if deny is not None:
            return deny
        try:
            row_id = int(body.get("rowId"))
        except (TypeError, ValueError):
            return 400, {"ok": False, "level": "warn",
                         "refuse": MA.REFUSE_BAD_VALUE,
                         "message": "行の指定が不正です"}
        try:
            r = MA.delete_row(self.wf.materials,
                              str(body.get("table") or ""), row_id)
        except MA.Refused as exc:
            return exc.status, {"ok": False, "level": "warn",
                                "refuse": exc.kind, "message": exc.message}
        self.wf.refresh_label_sheets(force=True)
        return {"level": "info", **r}

    # ------------------------------------------------------------ 画面の使用権
    @property
    def _screens(self):
        return getattr(self.ctx, "screens", None)

    def _deny_if_not_active(self, path: str, body: Dict[str, Any]):
        """使用権を持たない画面からの書き換えを断る。

        ``screenId`` を名乗らない相手（自動テスト・停止スクリプトなど）は
        画面ではないので素通しする。ブラウザーは必ず名乗る。
        """
        if path not in self._STATE_WRITING:
            return None
        guard = self._screens
        if guard is None:
            return None
        screen_id = str((body or {}).get("screenId") or "").strip()
        if not screen_id:
            return None
        if guard.is_active(screen_id):
            return None
        return 409, {
            "ok": False, "level": "error", "screenTaken": True,
            "message": "この画面は使用中ではありません。\n"
                       "別の画面で操作されています。"
                       "この画面を使うには読み込み直してください。",
        }

    def _screen_claim(self, body):
        """入力画面の使用権を求める。"""
        guard = self._screens
        if guard is None:
            return {"ok": True, "granted": True}
        r = guard.claim(str(body.get("screenId") or ""),
                        force=bool(body.get("force")),
                        visible=_visible_of(body))
        return {"ok": True, **r}

    def _screen_ping(self, body):
        """生存通知。使用権を失っていれば granted=False。

        失った理由も返す。「別の画面に取られた」のか
        「誰も使っていない（放置で期限切れ）」のかで
        画面側の出し方が変わる。
        """
        guard = self._screens
        if guard is None:
            return {"ok": True, "granted": True, "heldByOther": False}
        r = guard.heartbeat_info(str(body.get("screenId") or ""),
                                 visible=_visible_of(body))
        return {"ok": True, **r}

    def _screen_state(self, body):
        """画面が裏に回る・表に戻る知らせ（ブラウザーの sendBeacon から）。

        裏に回った画面は、タイマーが間引かれて生存通知が途切れても
        空きとみなさない（services/screen_guard.py）。
        """
        guard = self._screens
        if guard is None:
            return {"ok": True, "granted": True}
        visible = _visible_of(body)
        if visible is None:
            return 400, {"ok": False, "message": "visible がありません"}
        reason = str(body.get("reason") or "")[:40]
        r = guard.set_visibility(str(body.get("screenId") or ""), visible,
                                 reason)
        if reason in ("sleep", "resume") and r.get("granted"):
            log.info("画面がスリープ・凍結から戻りました（%s）", reason)
        return {"ok": True, **r}

    def _screen_release(self, body):
        """画面を閉じるときに手放す。"""
        guard = self._screens
        if guard is not None:
            guard.release(str(body.get("screenId") or ""))
        return {"ok": True}

    # ------------------------------------------------------------ 起動確認
    def _health(self, body):
        """基盤仕様書 2.3: 起動完了の確認用。アプリ識別情報も返す。"""
        return {
            "ok": True,
            "appId": self.cfg.app_id,
            "appName": self.cfg.app_name,
            "version": self.cfg.version,
            "build": getattr(self.cfg, "build", ""),
            "codeStamp": _code_stamp(),
            "ready": True,
            "pid": os.getpid(),
            "port": self.cfg.port,
            "uptimeSec": round(time.time() - self.started_at, 1),
            "materialSource": self.wf.materials.last_source,
            "monitorLevel": self.cfg.monitor_level,
        }

    # ------------------------------------------------------------ 選択
    def _select_size(self, body):
        state = self._state_from(body)
        r = self.wf.select_size(state, int(body.get("obIdx") or 0))
        return r.to_dict()

    def _select_checkbox(self, body):
        state = self._state_from(body)
        if body.get("named"):
            r = self.wf.select_checkbox(state, 0, named=True)
        else:
            r = self.wf.select_checkbox(state, int(body.get("cbIdx") or 0))
        return r.to_dict()

    # ------------------------------------------------------------ 操作
    def _apply_weight(self, body):
        return self.wf.apply_weight(self._state_from(body)).to_dict()

    def _calc_tare(self, body):
        return self.wf.calc_tare(self._state_from(body)).to_dict()

    def _calc_list(self, body):
        state = self._state_from(body)
        self.wf.save_state(state)
        return self.wf.calc_list(state).to_dict()

    def _clear(self, body):
        return self.wf.clear_all(self._state_from(body)).to_dict()

    def _print_targets(self, body):
        return self.wf.print_targets(self._state_from(body)).to_dict()

    def _quick_height(self, body):
        state = self._state_from(body)
        count = val(body.get("coilCount"))
        if count <= 0:
            return {"ok": False, "level": "warn",
                    "message": "コイル積み数を入力してください"}
        return self.wf.quick_height(state, count).to_dict()

    def _state(self, body):
        return {"ok": True, "state": self.wf.load_state().to_dict()}

    def _materials(self, body):
        """資材マスタの内容（表記用フォームの ListView 相当）。"""
        try:
            table = self.wf.materials.load()
        except Exception as exc:
            return {"ok": False, "level": "error", "message": str(exc), "rows": []}
        return {"ok": True, "rows": table.as_rows(),
                "source": self.wf.materials.last_source,
                "path": table.source,
                "fields": table.fields}

    def _reload_materials(self, body):
        """マスタのキャッシュを捨てて読み直す。"""
        self.wf.materials.clear_cache()
        return self._materials(body)

    # ------------------------------------------------------------ 全サイズ
    def _all_size_prepare(self, body):
        """VBA ``ComboBox1_Change``（シート追加1/2 + コピー1/2）。"""
        combo = (body.get("comboText") or "").strip()
        if not combo:
            return {"ok": False, "level": "warn", "message": "サイズが選択されていません"}
        n1, n2 = self.wf.all_size.prepare_sheets(combo)
        st = self.wf.all_size.load_state()
        st["comboText"] = combo
        self.wf.all_size.save_state(st)
        return {"ok": True, "message": "台紙を用意しました（%s / %s）" % (n1, n2),
                "sheets": [n1, n2]}

    def _all_size_submit(self, body):
        """VBA ``CommandButton1_Click``（決定）。"""
        three = body.get("threeDigit")
        if three is not None:
            three = bool(three)
        return self.wf.all_size.submit(
            combo_text=(body.get("comboText") or "").strip(),
            kensa_no=V.filter_alnum_upper(body.get("kensaNo") or ""),
            three_digit=three,
            w1=V.filter_numeric_text(body.get("w1") or ""),
            w2=V.filter_numeric_text(body.get("w2") or ""),
            w4=V.filter_numeric_text(body.get("w4") or ""),
            w5=V.filter_numeric_text(body.get("w5") or ""))

    def _all_size_print_targets(self, body):
        combo = (body.get("comboText") or "").strip()
        if not combo:
            return {"ok": False, "level": "warn", "message": "サイズが選択されていません"}
        return self.wf.all_size.print_targets(combo)

    # ------------------------------------------------------------ 設定
    def _settings_save(self, body):
        """設定を保存し、再起動せずに反映できるものは即時反映する。"""
        from ..services import settings as S
        from ..config import load_config

        values = body.get("values") or {}
        if not isinstance(values, dict):
            return {"ok": False, "level": "warn", "message": "入力を解釈できません"}

        # パス（参照先）の変更は合言葉が要る。
        # 参照先を変えると全員の計算に影響するため、うっかり防止。
        changed = S.changed_path_keys(values, self.cfg)
        if changed and not S.password_ok(body.get("password")):
            labels = [S.SETTINGS_BY_KEY[k].label for k in changed]
            return {
                "ok": False, "level": "warn", "needPassword": True,
                "pathKeys": changed,
                "message": ("パスの変更には合言葉が要ります。\n変更しようとした項目: "
                            + "、".join(labels)),
            }

        result = S.save(values, self.cfg)
        if not result.get("ok"):
            return result

        # 保存した内容で設定を読み直し、動いているオブジェクトへ反映する
        new_cfg = load_config()
        self._adopt(new_cfg)
        try:
            result["applied"] = S.apply_live(new_cfg, self.ctx)
        except Exception as exc:
            log.exception("設定の反映に失敗しました")
            result["applied"] = []
            result["message"] += "（反映でエラー: %s。再起動してください）" % exc
            result["level"] = "warn"
        return result

    def _label_align(self, body):
        """試し刷りの実測から、印刷の補正値を決めて保存する。

        現場に割り算をさせないための窓口。
        ``mode`` は ``measure``（実測から出す）か ``nudge``（今の値から動かす）。
        補正値は **端末ごとの設定** なので合言葉は要らない
        （参照先を変えるわけではなく、そのプリンターの癖を吸収するだけ）。
        """
        from ..services import label_align as LA
        from ..services import settings as S
        from ..config import load_config

        cur_x = float(getattr(self.cfg, "label_offset_x_mm", 0) or 0)
        cur_y = float(getattr(self.cfg, "label_offset_y_mm", 0) or 0)
        cur_s = float(getattr(self.cfg, "label_scale_pct", 100) or 100)

        mode = str(body.get("mode") or "measure")
        try:
            if mode == "nudge":
                res = LA.nudge(cur_x, cur_y, cur_s,
                               dx=body.get("dx"), dy=body.get("dy"),
                               dscale=body.get("dscale"))
            elif mode == "reset":
                res = LA.AlignResult(0.0, 0.0, 100.0, ["補正を戻しました。"])
            else:
                res = LA.plan(cur_x, cur_y, cur_s,
                              span_x=body.get("spanX"), span_y=body.get("spanY"),
                              cross_x=body.get("crossX"),
                              cross_y=body.get("crossY"))
        except LA.AlignError as exc:
            return {"ok": False, "level": "warn", "message": str(exc)}

        saved = S.save({"label_offset_x_mm": res.offset_x_mm,
                        "label_offset_y_mm": res.offset_y_mm,
                        "label_scale_pct": res.scale_pct}, self.cfg)
        if not saved.get("ok"):
            return saved

        new_cfg = load_config()
        self._adopt(new_cfg)
        try:
            saved["applied"] = S.apply_live(new_cfg, self.ctx)
        except Exception as exc:                       # 反映だけ失敗しても値は残す
            log.exception("補正の反映に失敗しました")
            saved["applied"] = []
            saved["level"] = "warn"
            saved["message"] = "保存しましたが反映に失敗しました（%s）" % exc

        before = "X %+.2f / Y %+.2f / 倍率 %.2f%%" % (cur_x, cur_y, cur_s)
        after = "X %+.2f / Y %+.2f / 倍率 %.2f%%" % (
            res.offset_x_mm, res.offset_y_mm, res.scale_pct)
        saved.update({
            "ok": True,
            "stateText": LA.describe_html(res.offset_x_mm, res.offset_y_mm,
                                          res.scale_pct),
            "plainMessage": "直しました。" + LA.describe(
                res.offset_x_mm, res.offset_y_mm, res.scale_pct),
            "offsetXMm": res.offset_x_mm,
            "offsetYMm": res.offset_y_mm,
            "scalePct": res.scale_pct,
            "notes": res.messages,
            "before": before, "after": after,
            "message": "補正を決めました: %s → %s" % (before, after),
        })
        return saved

    # ------------------------------------------------------------ 配布設定
    # python-web-tools の配布設定と同じ流れ（services/distribution.py）。
    # 書き出す・読み込み直す・消すは合言葉が要る。
    def _dist_reply(self, result):
        from ..services import distribution as D
        out = result.to_dict()
        out["level"] = "info" if result.ok else "warn"
        if result.reason == D.REFUSE_NEED_PASSWORD:
            out["needPassword"] = True
        out["distribution"] = D.summary(self.cfg)
        return out

    def _dist_state(self, body):
        from ..services import distribution as D
        return {"ok": True, "distribution": D.summary(self.cfg)}

    def _dist_export(self, body):
        from ..services import distribution as D
        keys = body.get("items") or []
        if not isinstance(keys, list):
            keys = []
        return self._dist_reply(
            D.export(str(body.get("password") or ""),
                     [str(k) for k in keys], self.cfg))

    def _dist_remove(self, body):
        from ..services import distribution as D
        return self._dist_reply(D.remove(str(body.get("password") or "")))

    def _dist_reapply(self, body):
        """置いてある配布設定を読み込み直す（この端末にある値も上書き）。"""
        from ..services import distribution as D
        from ..services import settings as S
        from ..config import load_config

        result = D.reapply(str(body.get("password") or ""), self.cfg)
        if result.ok and result.applied:
            # 保存と同じく、読み直した設定を動いているオブジェクトへ反映する
            new_cfg = load_config()
            self._adopt(new_cfg)
            try:
                S.apply_live(new_cfg, self.ctx)
            except Exception as exc:
                log.exception("配布設定の反映に失敗しました")
                result.message += "（反映でエラー: %s。再起動してください）" % exc
        return self._dist_reply(result)

    def _dist_build(self, body):
        """配布用フォルダを作る（python-web-tools の scripts/make_dist.py と同じ中身）。"""
        from pathlib import Path
        from ..services import dist_folder as DF
        from ..services import distribution as D
        from ..services import settings as S

        if not S.password_ok(str(body.get("password") or "")):
            return {"ok": False, "level": "warn", "needPassword": True,
                    "message": "配布用フォルダを作るには合言葉が要ります。",
                    "distribution": D.summary(self.cfg)}
        raw = str(body.get("out") or "").strip()
        try:
            r = DF.build(Path(raw) if raw else None,
                         with_settings=bool(body.get("withSettings", True)),
                         force=bool(body.get("force")),
                         make_zip=bool(body.get("zip")))
        except DF.BuildRefused as exc:
            return {"ok": False, "level": "warn", "message": str(exc),
                    "distribution": D.summary(self.cfg)}
        out = r.to_dict()
        out.update(ok=True, level="info",
                   message="配布用フォルダを作りました（%d ファイル）: %s"
                           % (r.files, r.out),
                   distribution=D.summary(self.cfg))
        return out

    def _settings_check_password(self, body):
        """合言葉が合っているかだけを返す（何も書き換えない）。

        入力欄に打ち込んだだけでは合っているか分からず、
        現場が「入れたのに何も起きない」と迷う。
        その場で ○×を出すためだけの窓口。
        """
        from ..services import settings as S

        given = str(body.get("password") or "")
        if not given:
            return {"ok": False, "valid": False, "level": "warn",
                    "message": "合言葉が空です"}
        if S.password_ok(given):
            return {"ok": True, "valid": True,
                    "message": "合言葉は合っています。"
                               "このままパスの変更やマスタの書き換えができます。"}
        return {"ok": False, "valid": False, "level": "warn",
                "message": "合言葉が違います"}

    def _settings_test_master(self, body):
        """入力中の参照先で資材マスタを実際に読んでみる。

        保存せずに試せるようにして、パスの打ち間違いをその場で気付けるようにする。
        """
        from ..services import settings as S
        from ..repositories.material_repo import MaterialRepository
        from ..repositories.access_bridge import AccessBridge, AccessError

        values = body.get("values") or {}
        errors = S.validate_all(values)
        if errors:
            return {"ok": False, "level": "warn", "errors": errors,
                    "message": "入力を確認してください"}

        def pick(key):
            if key in values:
                return S.coerce(S.SETTINGS_BY_KEY[key], values[key])
            return getattr(self.cfg, key)

        aim = pick("aim_ref_path")
        csv_override = pick("material_csv_file")
        csv_path = csv_override or self.cfg.default_material_csv_path
        prefer = pick("prefer_access")
        db_file = pick("material_db_file")

        probe = MaterialRepository(aim, csv_path, prefer_access=prefer,
                                   timeout_sec=pick("access_timeout_sec"),
                                   db_path=db_file)
        lines = []
        lines.append("SQLite 参照先: %s" % (db_file or "(未設定)"))
        lines.append("Access 参照先: %s" % (probe.accdb_path or "(未設定)"))
        lines.append("cscript 経由の読取: %s"
                     % ("使える" if AccessBridge.available() else
                        "この環境では使えません（Windows 以外 / VBScript 無し）"))
        lines.append("CSV 代替: %s" % csv_path)
        try:
            table = probe.load(use_cache=False)
        except AccessError as exc:
            lines.append("結果: 読めませんでした")
            lines.append(str(exc))
            return {"ok": False, "level": "error",
                    "message": "資材マスタを読めませんでした",
                    "detail": "<br>".join(_esc_lines(lines))}

        src = _SOURCE_LABEL.get(probe.last_source, probe.last_source)
        lines.append("結果: <b>%s</b> から %d 件を読みました" % (src, len(table.records)))
        missing = [n for n in _REQUIRED_MATERIALS if not table.has(n)]
        if missing:
            lines.append("<b>不足している資材名: %s</b>（0kg として計算されます）"
                         % "、".join(missing))
        if probe.last_error:
            lines.append("補足: %s" % probe.last_error)
        return {"ok": True, "level": "warn" if missing else "info",
                "message": "%s から読めました（%d 件）" % (src, len(table.records)),
                "detail": "<br>".join(_esc_lines(lines))}

    def _settings_reset(self, body):
        from ..services import settings as S
        from ..config import load_config
        result = S.reset(self.cfg)
        new_cfg = load_config()
        self._adopt(new_cfg)
        try:
            S.apply_live(new_cfg, self.ctx)
        except Exception:
            log.exception("設定リセット後の反映に失敗しました")
        return result

    def _adopt(self, new_cfg) -> None:
        """読み直した設定を、画面・API が見ている cfg へ反映する。"""
        for f in vars(new_cfg):
            setattr(self.cfg, f, getattr(new_cfg, f))

    def _shutdown(self, body):
        """基盤仕様書 2.8: まずアプリ自身へ正常終了を要求する経路。"""
        if self.wf.shutdown_hook is None:
            return {"ok": False, "message": "停止要求を受け付けられません"}
        self.wf.shutdown_hook()
        return {"ok": True, "message": "停止要求を受け付けました"}
