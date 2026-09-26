"""画面とAPI (VBA `frmCoilPacking` のイベントに対応する経路)

**本物と同じ経路を通す。** トークンを付け、Flask のテストクライアントで
実際に叩く ── サービス層を直接呼ぶ試験は別にあるので、ここは
「画面から操作したときに通るか」だけを見る。
"""
from __future__ import annotations

import time
import unittest

from modules.packing_details.meisai import (app_config, config, data_sync, history_repo, screen,
                    session)

from . import _db

TOKEN = "test-token-abc123"
# 画面の名前。**試験も1枚の画面として振る舞う** ── 持っていない画面の
# 操作はサーバが断るので、名乗らないと全部 409 になる
SCREEN = "test-screen-1"
HEADERS = {"X-Tool-Token": TOKEN, "X-Tool-Screen": SCREEN}


def _client(case: unittest.TestCase):
    """トークン付きのクライアントと、掴ませたDB接続。"""
    from modules.packing_details.app import routes                      # noqa: F401  (登録のため)
    from modules.packing_details import app as app_module

    conn = _db.case_db(case)
    original = app_module.get_db
    app_module.get_db = lambda: conn
    case.addCleanup(setattr, app_module, "get_db", original)

    # ルート側は `from .. import get_db` で名前を束縛済みなので、
    # そちらも差し替える。**持っているものだけ** ── `health` は
    # DBを触らないので `get_db` を import していない
    from modules.packing_details.app.routes import health as health_routes
    from modules.packing_details.app.routes import history as history_routes
    from modules.packing_details.app.routes import master as master_routes
    from modules.packing_details.app.routes import meisai as meisai_routes
    from modules.packing_details.app.routes import settings as settings_routes
    for module in (health_routes, history_routes, master_routes, meisai_routes,
                   settings_routes):
        if not hasattr(module, "get_db"):
            continue
        case.addCleanup(setattr, module, "get_db", module.get_db)
        module.get_db = lambda: conn

    flask_app = app_module.create_app(token=TOKEN)
    flask_app.config["READY"] = True
    client = flask_app.test_client()
    case.addCleanup(session.clear)
    session.clear()
    # 画面は**プロセスに1つ**。試験ごとに取り直す
    case.addCleanup(screen.reset)
    screen.reset()
    screen.claim(SCREEN)
    return client, conn


def _post(client, path, data=None):
    return client.post(path, json=data or {}, headers=HEADERS)


def _seed(conn, lot="L5160Z0", tate="2", yoko="12"):
    """1ロット分の取り込み済みデータを手で入れる。"""
    with conn:
        conn.execute(
            "INSERT INTO 仕掛ロット (ロット番号, 用途コード, 用途名, 製造材質,"
            " 製造調質, 製造板厚, 製造板幅, オーダー板厚, オーダー板幅,"
            " オーダー板丈, 設計_設備コース, 実績_設備コース)"
            " VALUES (?, 'R192', 'ｱﾝｾﾞﾝﾀｲｻﾝｺｰ', '52S', 'H34',"
            " 1.985, 104.0, 2.0, 104.0, 0, 'HOT LS4', 'HOT')", (lot,))
        conn.execute(
            "INSERT INTO 仕掛当工程 (ロット番号, 当工程設計_縦割数,"
            " 当工程設計_横割数) VALUES (?, ?, ?)", (lot, int(tate), int(yoko)))
        conn.execute(
            "INSERT INTO 仕掛引当 (ロット番号, 受注番号) VALUES (?, '62015946')",
            (lot,))
        conn.execute(
            "INSERT INTO 仕掛受注 (受注番号, 包装仕様NO) VALUES ('62015946', '1C0123')")


class SecurityTest(unittest.TestCase):
    """トークンと同一オリジンの守り。"""

    def setUp(self):
        self.client, self.conn = _client(self)

    def test_トークンが無ければ断る(self):
        res = self.client.post("/api/lot", json={"lot_no": "L5160Z0"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json()["error"]["code"], "bad_token")

    def test_健康確認はトークン不要(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["app_id"], "nlm.packing-details")

    def test_健康確認が版の出どころを名乗る(self):
        """**「入れ替えたのに古いまま」を調べるのに要る。**

        現場は端末へフォルダごとコピーして配るので、版だけ見ても
        「どの写しが動いているか」が言えない。実行フォルダとPythonを
        一緒に返す。版の書き方が壊れていれば理由もここに出る。
        """
        body = self.client.get("/api/health").get_json()
        self.assertEqual(body["version"], app_config.version())
        self.assertTrue(body["app_root"])
        self.assertRegex(body["python"], r"^\d+\.\d+\.\d+")
        self.assertTrue(body["python_exe"])
        self.assertEqual(body["version_problem"], "")   # 版の形は正しい
        self.assertIn("db_path_problem", body)          # 共有に置かれていないか

    def test_心拍はトークン不要(self):
        self.assertEqual(self.client.post("/api/alive", json={}).status_code, 200)

    def test_別オリジンからは断る(self):
        res = self.client.post("/api/lot", json={"lot_no": "L5160Z0"},
                               headers={**HEADERS,
                                        "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(res.status_code, 403)

    def test_帳票もトークンが要る(self):
        """業務データを返す経路。`/api/` だけを守ると素通しになる。"""
        res = self.client.get("/report/L5160Z0/1")
        self.assertEqual(res.status_code, 401)

    def test_別のホスト名では断る(self):
        res = self.client.get("/api/health", headers={"Host": "example.com"})
        self.assertEqual(res.status_code, 400)

    def test_画面のHTMLは控えさせない(self):
        screen.reset()                     # 1枚目として開く
        res = self.client.get("/meisai")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["Cache-Control"], "no-store")


class ScreenTest(unittest.TestCase):
    """画面は1枚だけ (タブを2枚開かせない)。

    **プロセスの多重起動とは別の話。** `launch_guard` は Python が
    2つ走っていないかを見るが、タブを2枚開くのは1プロセスのままなので
    止められない。ところが作業状態(`session.py`)はプロセスに1つしか
    無いので、2枚開くと同じ盤面を奪い合う ── 自分が開いたはずのロットが
    触った瞬間に別のロットへ化ける。
    """

    def setUp(self):
        self.client, self.conn = _client(self)
        _seed(self.conn)

    def _claim(self, screen_id):
        return self.client.post("/api/screen/claim", json={"screen": screen_id},
                                headers={"X-Tool-Token": TOKEN})

    def test_2枚目の名乗りは断る(self):
        """`_client` が SCREEN を持っている＝1枚目が開いている状態。

        **器(HTML)は誰にでも返す。** ブラウザは再読込のとき古いページを
        畳む前に次の要求を送るので、開くところで断ると F5 のたびに
        断ることになる。見分けるのはブラウザ側(`screen.js`)の仕事で、
        サーバは名乗りを断る。
        """
        res = self._claim("2枚目")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["error"]["code"], "screen_busy")
        self.assertEqual(screen.holder().screen_id, SCREEN)

    def test_同じ名前の名乗り直しは通る(self):
        """**再読込(F5)がこれ。** タブの名前は sessionStorage に残る。"""
        self.assertEqual(self._claim(SCREEN).status_code, 200)
        self.assertEqual(screen.holder().screen_id, SCREEN)

    def test_器は誰にでも返す(self):
        """業務データは名乗ってからでないと触れないので、器は返してよい。"""
        res = self.client.get("/meisai")
        self.assertEqual(res.status_code, 200)

    def test_持っていない画面の操作は断る(self):
        """ここが本当の歯止め。開くところの断りはすり抜けうる。"""
        res = self.client.post("/api/lot", json={"lot_no": "L5160Z0"},
                               headers={"X-Tool-Token": TOKEN,
                                        "X-Tool-Screen": "べつの画面"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["error"]["code"], "screen_taken")

    def test_画面を名乗らない操作も断る(self):
        res = self.client.post("/api/lot", json={"lot_no": "L5160Z0"},
                               headers={"X-Tool-Token": TOKEN})
        self.assertEqual(res.status_code, 409)

    def test_引き継ぐと前の画面が使えなくなる(self):
        """**2枚にするのではなく、移す。**"""
        self.assertEqual(_post(self.client, "/api/lot",
                               {"lot_no": "L5160Z0"}).status_code, 200)

        # 2枚目が引き継ぐ → 誰も持っていない状態になり、開けば取れる
        take = self.client.post("/api/screen/take-over", json={},
                                headers={"X-Tool-Token": TOKEN})
        self.assertEqual(take.status_code, 200)
        self.assertIsNone(screen.holder())
        self.assertEqual(self._claim("2枚目").status_code, 200)

        # 前の画面は断られる
        res = _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        self.assertEqual(res.status_code, 409)

    def test_閉じたらすぐ空く(self):
        """猶予を待たせない。**開き直しはよくある操作。**"""
        self.client.post("/api/alive", json={"screen": SCREEN, "leaving": True})
        self.assertIsNone(screen.holder())
        self.assertEqual(self._claim("べつの画面").status_code, 200)

    def test_心拍が途切れれば空く(self):
        """タブが落ちても、誰も入れないままにはしない。"""
        self.assertIsNotNone(screen.holder())
        screen._last_seen = time.time() - screen.GRACE_SEC - 1
        self.assertIsNone(screen.holder())
        self.assertEqual(self._claim("べつの画面").status_code, 200)

    def test_持ち主でない心拍で延命しない(self):
        """延命すると、引き継がれた古いタブが裏で生き続ける。"""
        body = self.client.post(
            "/api/alive", json={"screen": "べつの画面"}).get_json()
        self.assertFalse(body["holds"])
        self.assertEqual(screen.holder().screen_id, SCREEN)

    def test_帳票は画面を持たなくても出せる(self):
        """**印刷は別のタブで開く**(`window.open`)。

        そのタブは画面を持っていない。ここで断ると印刷できなくなる。
        盤面は触らず、DBから引いて組むだけ(トークンは要る)。
        """
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        _post(self.client, "/api/weights", {"weights": ["250", "248"]})
        _post(self.client, "/api/strands", {})
        _post(self.client, "/api/stack-max", {"value": "1"})
        _post(self.client, "/api/stack", {"key": "1-12"})
        _post(self.client, "/api/output", {"confirm": True})

        res = self.client.get(f"/report/L5160Z0/1?t={TOKEN}")
        self.assertEqual(res.status_code, 200)
        self.assertIn("梱包明細表", res.get_data(as_text=True))


class LotTest(unittest.TestCase):
    """ロットを開く (VBA `txtLotNo_Change`)。"""

    def setUp(self):
        self.client, self.conn = _client(self)
        _seed(self.conn)

    def test_開くとロット情報と割数が返る(self):
        res = _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        self.assertEqual(res.status_code, 200)
        state = res.get_json()["state"]
        self.assertTrue(state["found"])
        self.assertEqual(state["lot"]["yoto_code"], "R192")
        self.assertEqual(state["lot"]["tate_wari"], 2)
        self.assertEqual(state["zen_kotei"], 24)          # 2 × 12
        self.assertEqual(state["jou_su"], 2)              # 縦割数が入る

    def test_表示書式がVBAと同じ(self):
        state = _post(self.client, "/api/lot",
                      {"lot_no": "L5160Z0"}).get_json()["state"]
        self.assertEqual(state["lot"]["seizou_thickness"], "1.985")
        self.assertEqual(state["lot"]["seizou_width"], "104.0")
        self.assertEqual(state["lot"]["odr_thickness"], "2.000")

    def test_受注と包装仕様NOが返る(self):
        state = _post(self.client, "/api/lot",
                      {"lot_no": "L5160Z0"}).get_json()["state"]
        self.assertEqual(state["orders"],
                         [{"order_no": "62015946", "spec_no": "1C0123",
                           "customer": "", "deliver_to": ""}])

    def test_桁数が違えば断る(self):
        res = _post(self.client, "/api/lot", {"lot_no": "L516"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"]["code"], "bad_lot_no")

    def test_見つからなければ404(self):
        res = _post(self.client, "/api/lot", {"lot_no": "ZZZZZZZ"})
        self.assertEqual(res.status_code, 404)

    def test_小文字は大文字にそろえる(self):
        res = _post(self.client, "/api/lot", {"lot_no": "l5160z0"})
        self.assertEqual(res.status_code, 200)

    def test_未印刷のまま切り替えると確認する(self):
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        history_repo.set_printed(self.conn, False)
        _seed(self.conn, lot="AAA0001")
        body = _post(self.client, "/api/lot", {"lot_no": "AAA0001"}).get_json()
        self.assertEqual(body["confirm"], "unprinted")

    def test_了解すれば切り替わり前ロットが片付く(self):
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        history_repo.set_printed(self.conn, False)
        _seed(self.conn, lot="AAA0001")
        body = _post(self.client, "/api/lot",
                     {"lot_no": "AAA0001", "confirm": True}).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"]["lot_no"], "AAA0001")
        self.assertEqual(history_repo.current_seq(self.conn), 0)   # ResetSeq

    def test_LS4LOTに無ければ理由を出す(self):
        with self.conn:
            self.conn.execute("DELETE FROM 仕掛当工程")
        state = _post(self.client, "/api/lot",
                      {"lot_no": "L5160Z0"}).get_json()["state"]
        self.assertEqual(state["zen_kotei"], 0)
        self.assertIn("LS4LOT", state["lot"]["jou_su_note"])


class FlowTest(unittest.TestCase):
    """条番号 → 積み上げ → 出力 → 印刷 の一周。"""

    def setUp(self):
        self.client, self.conn = _client(self)
        _seed(self.conn)
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})

    def _to_strands(self):
        _post(self.client, "/api/weights", {"weights": ["250", "248"]})
        return _post(self.client, "/api/strands", {}).get_json()

    def test_重量が無いと条番号を作れない(self):
        res = _post(self.client, "/api/strands", {})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"]["code"], "no_weight")

    def test_条番号が配分どおりに出る(self):
        state = self._to_strands()["state"]
        self.assertEqual(state["strands"], [12, 12])
        self.assertEqual(state["rows"][0][0]["key"], "1-12")   # 左端が最大
        self.assertEqual(state["rows"][0][-1]["key"], "1-1")

    def test_条番号を作ると途中経過が保存される(self):
        self._to_strands()
        snap = history_repo.load_snapshot(self.conn, "L5160Z0")
        self.assertIsNotNone(snap)
        self.assertEqual(snap.strands, [12, 12])
        self.assertEqual(snap.weights, [250, 248])

    def test_積み上げて出力できる(self):
        self._to_strands()
        _post(self.client, "/api/stack-max", {"value": "3"})
        for key in ("1-10", "1-5"):
            _post(self.client, "/api/stack", {"key": key})
        state = _post(self.client, "/api/stack", {"key": "2-8"}).get_json()["state"]
        self.assertTrue(state["complete"])
        self.assertEqual(state["slots"], ["1-10", "1-5", "2-8"])

        body = _post(self.client, "/api/output", {}).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["seq_no"], 1)
        # 出力後は積み上げだけ空。条番号は残る
        self.assertEqual(body["state"]["slots"], [None, None, None])
        self.assertEqual(body["state"]["strands"], [12, 12])
        self.assertEqual(body["state"]["outputs"][0]["name"], "L5160Z0-No1")

    def test_埋まるまで出力できない(self):
        self._to_strands()
        _post(self.client, "/api/stack-max", {"value": "2"})
        _post(self.client, "/api/stack", {"key": "1-1"})
        res = _post(self.client, "/api/output", {})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"]["code"], "incomplete")

    def test_使用済みのクリックは黙って無視する(self):
        self._to_strands()
        _post(self.client, "/api/stack-max", {"value": "2"})
        _post(self.client, "/api/stack", {"key": "1-1"})
        body = _post(self.client, "/api/stack", {"key": "1-1"}).get_json()
        self.assertTrue(body["ok"])            # 断りではなく無視
        self.assertEqual(body["ignored"], "used")

    def test_解除できる(self):
        self._to_strands()
        _post(self.client, "/api/stack-max", {"value": "2"})
        _post(self.client, "/api/stack", {"key": "1-1"})
        body = _post(self.client, "/api/unstack", {"slot_no": 1}).get_json()
        self.assertEqual(body["released"], "1-1")
        self.assertEqual(body["state"]["slots"], [None, None])

    def test_廃棄を付けると保存される(self):
        self._to_strands()
        body = _post(self.client, "/api/discard", {"key": "1-3"}).get_json()
        self.assertTrue(body["discarded"])
        snap = history_repo.load_snapshot(self.conn, "L5160Z0")
        self.assertEqual(snap.discarded, ["1-3"])

    def test_廃棄済みは積めない(self):
        self._to_strands()
        _post(self.client, "/api/stack-max", {"value": "2"})
        _post(self.client, "/api/discard", {"key": "1-3"})
        body = _post(self.client, "/api/stack", {"key": "1-3"}).get_json()
        self.assertEqual(body["ignored"], "discarded")

    def test_反転しても条番号は変わらない(self):
        before = self._to_strands()["state"]["rows"][0]
        after = _post(self.client, "/api/reverse", {}).get_json()["state"]["rows"][0]
        self.assertEqual([c["key"] for c in after],
                         list(reversed([c["key"] for c in before])))

    def test_帳票が出せる(self):
        self._to_strands()
        _post(self.client, "/api/stack-max", {"value": "2"})
        _post(self.client, "/api/stack", {"key": "1-10"})
        _post(self.client, "/api/stack", {"key": "2-8"})
        _post(self.client, "/api/output", {})

        res = self.client.get(f"/report/L5160Z0/1?t={TOKEN}")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("L5160Z0-No1  梱包明細表", html)
        self.assertIn("250.0Kg", html)
        self.assertIn("248.0Kg", html)
        self.assertEqual(html.count('class="r-data"'), config.SHEET_ROWS)

    def test_印刷すると印刷済みになる(self):
        history_repo.set_printed(self.conn, False)
        _post(self.client, "/api/printed", {})
        self.assertTrue(history_repo.is_printed(self.conn))

    def test_丈数を変えると条番号も重量もやり直し(self):
        self._to_strands()
        state = _post(self.client, "/api/jou-su", {"value": "3"}).get_json()["state"]
        self.assertEqual(state["jou_su"], 3)
        self.assertEqual(state["strands"], [])
        self.assertEqual(state["weights"], [0, 0, 0])

    def test_丈数の上限(self):
        res = _post(self.client, "/api/jou-su",
                    {"value": str(config.JOUSU_MAX + 1)})
        self.assertEqual(res.status_code, 400)

    def test_前工程実績数を直せる(self):
        body = _post(self.client, "/api/zen-kotei", {"value": "30"}).get_json()
        self.assertEqual(body["state"]["zen_kotei"], 30)

    def test_前工程実績数は数値だけ(self):
        res = _post(self.client, "/api/zen-kotei", {"value": "あ"})
        self.assertEqual(res.status_code, 400)


class PrintTest(unittest.TestCase):
    """帳票の経路。**明細1枚につき用紙1枚**(A4横の左半分、右は空ける)。"""

    def setUp(self):
        self.client, self.conn = _client(self)
        _seed(self.conn)
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        _post(self.client, "/api/weights", {"weights": ["250", "248"]})
        _post(self.client, "/api/strands", {})
        _post(self.client, "/api/stack-max", {"value": "2"})
        for keys in (("1-10", "2-8"), ("1-9", "2-7"), ("1-8", "2-6")):
            for key in keys:
                _post(self.client, "/api/stack", {"key": key})
            _post(self.client, "/api/output", {"confirm": True})

    def _get(self, path):
        return self.client.get(path, headers=HEADERS)

    def test_紙面に書き足しの欄と送り先が付く(self):
        """**印刷の前に手直しできる。** 送り先には開いた窓のトークンが付く。"""
        html = self.client.get(f"/report/L5160Z0/1?t={TOKEN}").get_data(as_text=True)
        self.assertIn('data-placeholder="サイズ"', html)
        self.assertIn('data-placeholder="LOTNO"', html)
        self.assertIn(f'"/report/L5160Z0/edits?t={TOKEN}"', html)

    def _row_id(self, no=1):
        from modules.packing_details.meisai import meisai_service
        return meisai_service.find_output(self.conn, "L5160Z0", no).row_id

    def test_書き足しを送ると刷り直しても出る(self):
        rid = self._row_id()
        res = self.client.post(f"/report/L5160Z0/edits?t={TOKEN}",
                               json={"edits": {f"{rid}.size": "1.985×104.0",
                                               f"{rid}.lotno": "L5160Z0"}})
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        html = self._get("/report/L5160Z0/1").get_data(as_text=True)
        self.assertIn("1.985×104.0", html)

    def test_書き足しは画面を持っていなくても送れる(self):
        """**紙面は別のタブ**で、画面を持っていない。断ると直せない。"""
        rid = self._row_id()
        screen.reset()                     # 誰も画面を持っていない
        res = self.client.post(f"/report/L5160Z0/edits?t={TOKEN}",
                               json={"edits": {f"{rid}.size": "x"}})
        self.assertEqual(res.status_code, 200)

    def test_書き足しにもトークンが要る(self):
        rid = self._row_id()
        res = self.client.post("/report/L5160Z0/edits",
                               json={"edits": {f"{rid}.size": "x"}})
        self.assertEqual(res.status_code, 401)

    def test_長すぎる書き足しは理由を返す(self):
        """紙面のスクリプトはこの文言をそのまま出す。"""
        rid = self._row_id()
        res = self.client.post(f"/report/L5160Z0/edits?t={TOKEN}",
                               json={"edits": {f"{rid}.lotno": "X" * 50}})
        self.assertEqual(res.status_code, 400)
        self.assertIn("LOTNO", res.get_json()["error"]["message"])

    def test_作り直された明細への書き足しは409(self):
        rid = self._row_id()
        _post(self.client, "/api/stack", {"key": "1-7"})
        _post(self.client, "/api/stack", {"key": "2-5"})
        _post(self.client, "/api/output", {"confirm": True, "specify_no": 1})
        res = self.client.post(f"/report/L5160Z0/edits?t={TOKEN}",
                               json={"edits": {f"{rid}.size": "x"}})
        self.assertEqual(res.status_code, 409)

    def test_1枚は左半分で右半分は空ける(self):
        html = self._get("/report/L5160Z0/1").get_data(as_text=True)
        self.assertEqual(html.count('class="page"'), 1)
        self.assertEqual(html.count('class="half blank"'), 1)

    def test_まとめて刷ると1枚ずつ別の用紙(self):
        """**A4 1枚に2枚並べる運用はしていない**(現場に確認済み)。"""
        html = self._get("/report/L5160Z0?nos=1,2").get_data(as_text=True)
        self.assertEqual(html.count('class="page"'), 2)
        self.assertEqual(html.count('class="half blank"'), 2)
        self.assertIn("L5160Z0-No1", html)
        self.assertIn("L5160Z0-No2", html)

    def test_3枚なら用紙3枚(self):
        html = self._get("/report/L5160Z0?nos=1,2,3").get_data(as_text=True)
        self.assertEqual(html.count('class="page"'), 3)
        self.assertEqual(html.count('class="half blank"'), 3)

    def test_並びはNo順に固定(self):
        """押した順で刷ると、出てくる紙の順が押し方で変わってしまう。"""
        html = self._get("/report/L5160Z0?nos=3,1").get_data(as_text=True)
        self.assertLess(html.index("L5160Z0-No1"), html.index("L5160Z0-No3"))

    def test_重複した指定はまとめる(self):
        html = self._get("/report/L5160Z0?nos=1,1,2").get_data(as_text=True)
        self.assertEqual(html.count('class="meisai"'), 2)

    def test_無いNoを混ぜたら断る(self):
        res = self._get("/report/L5160Z0?nos=1,99")
        self.assertEqual(res.status_code, 404)

    def test_数値でないNoは断る(self):
        res = self._get("/report/L5160Z0?nos=1,あ")
        self.assertEqual(res.status_code, 400)

    def test_Noを指定しなければ断る(self):
        self.assertEqual(self._get("/report/L5160Z0?nos=").status_code, 400)

    def test_まとめ刷りもトークンが要る(self):
        self.assertEqual(self.client.get("/report/L5160Z0?nos=1").status_code,
                         401)


class RestoreTest(unittest.TestCase):
    """復元 (VBA `RestoreSnapshot`)。"""

    def setUp(self):
        self.client, self.conn = _client(self)
        _seed(self.conn)

    def test_開き直すと途中経過が戻る(self):
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        _post(self.client, "/api/weights", {"weights": ["250", "248"]})
        _post(self.client, "/api/strands", {})
        _post(self.client, "/api/stack-max", {"value": "2"})
        _post(self.client, "/api/stack", {"key": "1-10"})
        _post(self.client, "/api/discard", {"key": "1-3"})

        # プロセスが落ちた想定。セッションだけ捨てて開き直す
        session.clear()
        body = _post(self.client, "/api/lot", {"lot_no": "L5160Z0"}).get_json()

        state = body["state"]
        self.assertTrue(state["restored"])
        self.assertIn("復元しました", body["message"])
        self.assertEqual(state["strands"], [12, 12])
        self.assertEqual(state["weights"], [250, 248])
        states = {c["key"]: c["state"] for row in state["rows"] for c in row}
        self.assertEqual(states["1-10"], "used")
        self.assertEqual(states["1-3"], "discarded")

    def test_復元後の再表示は確認を挟む(self):
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        _post(self.client, "/api/weights", {"weights": ["250", "248"]})
        _post(self.client, "/api/strands", {})
        session.clear()
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})

        body = _post(self.client, "/api/strands", {}).get_json()
        self.assertEqual(body["confirm"], "overwrite_restored")

        body = _post(self.client, "/api/strands", {"confirm": True}).get_json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["state"]["restored"])


class HistoryTest(unittest.TestCase):
    """副番履歴クリア (VBA `cmdClearDup_Click`)。"""

    def setUp(self):
        self.client, self.conn = _client(self)
        _seed(self.conn)
        _post(self.client, "/api/lot", {"lot_no": "L5160Z0"})
        _post(self.client, "/api/weights", {"weights": ["250", "248"]})
        _post(self.client, "/api/strands", {})
        _post(self.client, "/api/stack-max", {"value": "2"})
        _post(self.client, "/api/stack", {"key": "1-10"})
        _post(self.client, "/api/stack", {"key": "2-8"})
        _post(self.client, "/api/output", {})

    def test_履歴とスナップショットが消えて画面がほどける(self):
        body = _post(self.client, "/api/clear-history", {}).get_json()
        self.assertEqual(history_repo.fuban_count(self.conn), 0)
        self.assertIsNone(history_repo.load_snapshot(self.conn, "L5160Z0"))
        # キャンバスは残る
        self.assertEqual(body["state"]["strands"], [12, 12])
        self.assertEqual(body["state"]["slots"], [None, None])

    def test_連番は戻らない(self):
        _post(self.client, "/api/clear-history", {})
        self.assertEqual(history_repo.current_seq(self.conn), 1)


class SettingsTest(unittest.TestCase):
    """設定。"""

    def setUp(self):
        self.client, self.conn = _client(self)

    def test_設定が読める(self):
        body = self.client.get("/api/settings", headers=HEADERS).get_json()
        self.assertIn("lot_db_dir", body)
        self.assertEqual(len(body["stamps"]), len(config.ALL_SOURCE_FILES))

    def test_一覧に無い設定は変えられない(self):
        res = _post(self.client, "/api/settings",
                    {"key": "admin_password", "value": "x"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"]["code"], "not_listed")


class ManualTest(unittest.TestCase):
    """説明書 (VBA `cmdManual_Click`)。"""

    def setUp(self):
        self.client, self.conn = _client(self)

    def test_トークン無しで開ける(self):
        """**業務データを含まない。**

        トークンを要求すると、切れた画面から説明書に辿り着けなくなる
        ── 困っている人ほど読めない、という形になる。
        """
        res = self.client.get("/docs")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("コイル梱包明細打ち出しシステム", html)

    def test_見出しと表が組まれる(self):
        html = self.client.get("/docs").get_data(as_text=True)
        self.assertIn("<h2>", html)
        self.assertIn("<table>", html)
        self.assertIn("<blockquote>", html)

    def test_中身がそのまま出ない(self):
        """Markdown の記号が生で出ていないか。"""
        html = self.client.get("/docs").get_data(as_text=True)
        body = html.split("<body>", 1)[1]
        self.assertNotIn("## ", body)
        self.assertNotIn("**", body)

    def test_変更履歴も同じ経路で読める(self):
        """**版を見せるなら「何が変わったか」まで辿れないと半分。**"""
        res = self.client.get("/docs/変更履歴")
        self.assertEqual(res.status_code, 200)
        self.assertIn("変更履歴", res.get_data(as_text=True))

    def test_変更履歴も記号が生で出ない(self):
        """版のバッジから開く。説明書だけ見ていて、こちらが崩れていた。"""
        import re
        html = self.client.get("/docs/変更履歴").get_data(as_text=True)
        body = html.split("<body>", 1)[1]
        # コードの中の `**` は記号そのものを見せている(記法ではない)
        body = re.sub(r"<pre>.*?</pre>|<code>.*?</code>", "", body, flags=re.S)
        self.assertNotIn("**", body)

    def test_コードの中の記号は強調にしない(self):
        """`` `**` `` を離れた `**` と組にすると、間が丸ごと太字になる。"""
        from modules.packing_details.app.routes.settings import _inline
        self.assertEqual(_inline("記号 `**` のまま。**強調**"),
                         "記号 <code>**</code> のまま。<strong>強調</strong>")
        self.assertEqual(_inline("**強調の中の `コード`**"),
                         "<strong>強調の中の <code>コード</code></strong>")

    def test_箇条書きの続きの行は同じ項目(self):
        """字下げした続きの行を段落にしない。以前は

            - 前の画面は、次に操作した時点で「…」と出て
              止まります

        の2行目が箇条書きの外へ出て、別の段落になっていた。
        """
        from modules.packing_details.app.routes.settings import _render_markdown
        html = _render_markdown("- 前の画面は、**次に操作した\n  時点で**止まります\n- 次")
        self.assertEqual(
            html, "<ul><li>前の画面は、<strong>次に操作した 時点で</strong>止まります</li>"
                  "<li>次</li></ul>")

    def test_字下げしていない行で箇条書きは終わる(self):
        from modules.packing_details.app.routes.settings import _render_markdown
        self.assertEqual(_render_markdown("- 項目\n段落"),
                         "<ul><li>項目</li></ul><p>段落</p>")

    def test_引用の続きの行は同じ段落(self):
        """`>` だけの行で段落を分ける。以前は1行ごとに段落になり、
        行をまたいだ強調が `**` のまま出ていた。
        """
        from modules.packing_details.app.routes.settings import _render_markdown
        html = _render_markdown("> **見出し**\n>\n> 本文の**1行目\n> 2行目**です")
        self.assertEqual(
            html, "<blockquote><p><strong>見出し</strong></p>"
                  "<p>本文の<strong>1行目 2行目</strong>です</p></blockquote>")

    def test_一覧に無い名前は断る(self):
        """受け取った文字列でパスを組むと、アプリの外まで読める。"""
        for name in ("秘密", "../config/app.json", "..%2fapp.json"):
            with self.subTest(name=name):
                res = self.client.get(f"/docs/{name}")
                self.assertEqual(res.status_code, 404)
                self.assertNotIn("app_id", res.get_data(as_text=True))


class StackSpinTest(unittest.TestCase):
    """積み条数にも −/＋ を置く(現場の指摘。梱包明細 0.13.5)。"""

    def test_積み条数の横にスピンボタン(self):
        client, _ = _client(self)
        html = client.get(f"/meisai?t={TOKEN}").data.decode("utf-8")
        at = html.index('id="stackMax"')
        near = html[at:at + 600]
        self.assertIn('data-stack="-1"', near)
        self.assertIn('data-stack="1"', near)
        self.assertIn(f'data-max="{config.STACK_LIMIT}"', near)


if __name__ == "__main__":
    unittest.main()
