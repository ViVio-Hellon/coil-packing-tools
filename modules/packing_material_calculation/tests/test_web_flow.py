"""画面ごしの一連 ── ロット検索から発注票まで"""
import pytest

from modules.packing_material_calculation.coil_tool import config
from modules.packing_material_calculation.tests._web import client, sandbox, seed   # noqa: F401


@pytest.fixture
def ready(client, sandbox):
    seed(sandbox)
    # 担当者を決めておく(チェックリストと発注票が要求する)
    client.post("/api/settings",
                json={"worker": config.WORKERS[0], "line": "LS4"})
    return client


# ==================================================================
def test_all_pages_open(ready):
    for path in ("/calc", "/checklist", "/order", "/settings"):
        r = ready.get(f"{path}?t=test-token")
        assert r.status_code == 200, path


def test_health_reports_identity(client):
    body = client.get("/api/health").get_json()
    assert body["ok"] is True
    assert body["app_id"] == "nlm.coil-material-tool"
    assert body["display_name"] == "コイル梱包資材計算ツール"


def test_lot_search_returns_candidates(ready):
    r = ready.post("/api/calc/lot", json={"LOT": "A123456"})
    assert r.status_code == 200
    assert r.get_json()["candidates"] == ["12345678"]


def test_lot_search_lowercase_is_upcased(ready):
    r = ready.post("/api/calc/lot", json={"LOT": "a123456"})
    assert r.get_json()["candidates"] == ["12345678"]
    assert r.get_json()["view"]["input"]["LOT"] == "A123456"


def test_unknown_lot_returns_empty(ready):
    r = ready.post("/api/calc/lot", json={"LOT": "Z999999"})
    body = r.get_json()
    assert body["candidates"] == []
    assert "ありません" in body["message"]


def test_order_expands_fields(ready):
    ready.post("/api/calc/lot", json={"LOT": "A123456"})
    r = ready.post("/api/calc/order", json={"受注番号": "12345678"})
    assert r.status_code == 200
    o = r.get_json()["view"]["order"]
    assert o["包装仕様NO"] == "1C0001"
    assert o["受注板厚"] == "1.000"
    assert o["受注板幅"] == "100.0"
    assert o["製品単重"] == "100.00"
    assert o["用途名"] == "ﾃｽﾄ用途"
    # コイル外径_MAX が外径欄に入る
    assert r.get_json()["view"]["input"]["外径"] == "900"


def test_unknown_order_is_refused(ready):
    r = ready.post("/api/calc/order", json={"受注番号": "99999999"})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "order_not_found"


def test_input_is_formatted_server_side(ready):
    r = ready.post("/api/calc/input", json={"検入数": "10.7", "外径": "900"})
    v = r.get_json()["view"]["input"]
    assert v["検入数"] == "10"      # 整数へ
    assert v["外径"] == "900.00"    # 小数2桁へ


def test_non_numeric_input_is_cleared(ready):
    r = ready.post("/api/calc/input", json={"検入数": "あ", "外径": "い"})
    v = r.get_json()["view"]["input"]
    assert v["検入数"] == "" and v["外径"] == ""


# ==================================================================
def _calc(client):
    client.post("/api/calc/lot", json={"LOT": "A123456"})
    client.post("/api/calc/order", json={"受注番号": "12345678"})
    client.post("/api/calc/input", json={"検入数": "10", "外径": "900"})
    return client.post("/api/calc/run", json={})


def test_full_calculation(ready):
    r = _calc(ready)
    assert r.status_code == 200
    v = r.get_json()["view"]["result"]
    assert v["パレット種類"] == "スカシ"
    assert v["パレットサイズ"] == "900"
    assert v["積数"] == "5"
    assert v["台数"] == "2"
    assert v["TotalC"]
    assert "count" in r.get_json()["view"]["flags"]


def test_calculation_without_count_is_refused(ready):
    ready.post("/api/calc/lot", json={"LOT": "A123456"})
    ready.post("/api/calc/order", json={"受注番号": "12345678"})
    ready.post("/api/calc/input", json={"検入数": ""})
    r = ready.post("/api/calc/run", json={})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "no_inspection_count"


def test_restack_recomputes_units_only(ready):
    _calc(ready)
    ready.post("/api/calc/input", json={"積数": "3"})
    r = ready.post("/api/calc/restack", json={})
    assert r.status_code == 200
    v = r.get_json()["view"]["result"]
    assert v["積数"] == "3"          # 手で入れた値が残る
    assert v["台数"] == "4"          # 切り上げ(10/3)


def test_weight_recalculation(ready):
    _calc(ready)
    r = ready.post("/api/calc/weight", json={})
    assert r.status_code == 200
    # π/4 × (900² − 508²) × 2.71 × 100 / 10⁶
    assert r.get_json()["view"]["result"]["単重再計算"] == "117.47"


def test_weight_needs_all_values(client, sandbox):
    seed(sandbox)
    r = client.post("/api/calc/weight", json={})
    assert r.status_code == 422
    assert "入力がありません" in r.get_json()["message"]


def test_burr_split(ready):
    _calc(ready)
    r = ready.post("/api/calc/burr", json={"上本数": 6, "下本数": 4})
    assert r.status_code == 200
    b = r.get_json()["burr"]
    assert b["積数"] == 5
    assert b["上台数"] == 2 and b["下台数"] == 1
    assert b["台数"] == 3
    assert b["残り検入数"] == 0


def test_burr_transfer_updates_main(ready):
    _calc(ready)
    r = ready.post("/api/calc/burr/transfer", json={"上本数": 6, "下本数": 4})
    assert r.status_code == 200
    assert r.get_json()["view"]["result"]["台数"] == "3"


# ==================================================================
def test_checklist_add_and_print(ready):
    _calc(ready)
    r = ready.post("/api/checklist/add", json={})
    assert r.status_code == 200
    assert r.get_json()["行番号"] == 1

    rep = ready.get("/report/checklist?t=test-token")
    assert rep.status_code == 200
    assert "LS4資材発注管理チェックリスト".encode() in rep.data
    assert "A123456".encode() in rep.data


def test_checklist_needs_worker(client, sandbox):
    seed(sandbox)
    _calc(client)
    r = client.post("/api/checklist/add", json={})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "no_worker"


def test_checklist_needs_calculation(ready):
    r = ready.post("/api/checklist/add", json={})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "not_calculated"


def test_checklist_fills_up(ready):
    _calc(ready)
    for n in range(1, 16):
        assert ready.post("/api/checklist/add", json={}).status_code == 200
    r = ready.post("/api/checklist/add", json={})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "checklist_full"


def test_checklist_clear_row(ready):
    _calc(ready)
    ready.post("/api/checklist/add", json={})
    r = ready.post("/api/checklist/clear-row", json={"行番号": 1})
    assert r.get_json()["view"]["used"] == []


# ==================================================================
def test_order_sheet_round_trip(ready):
    _calc(ready)
    ready.post("/api/checklist/add", json={})

    r = ready.post("/api/order/build", json={})
    assert r.status_code == 200
    body = r.get_json()
    assert len(body["sheets"]) == 1
    sheet = body["sheets"][0]
    assert sheet["種類"] == "リプラ" and sheet["角サイズ"] == "30×40"
    assert body["duplicates"] == []

    rep = ready.get("/report/order?t=test-token")
    assert rep.status_code == 200
    assert "発注票".encode() in rep.data

    c = ready.post("/api/order/commit", json={})
    assert c.get_json()["saved"] > 0

    hist = ready.get("/api/order/history").get_json()
    assert len(hist["rows"]) > 0


def test_order_reports_duplicate_after_commit(ready):
    _calc(ready)
    ready.post("/api/checklist/add", json={})
    ready.post("/api/order/build", json={})
    ready.post("/api/order/commit", json={})

    r = ready.post("/api/order/build", json={})
    assert r.status_code == 200
    assert len(r.get_json()["duplicates"]) >= 1


def test_order_needs_worker(client, sandbox):
    seed(sandbox)
    r = client.post("/api/order/build", json={})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "no_worker"


def test_order_without_checklist_is_refused(ready):
    r = ready.post("/api/order/build", json={})
    assert r.status_code == 422
    assert r.get_json()["reason"] == "nothing_to_order"


# ==================================================================
def test_master_lives_in_settings(ready):
    """マスタ管理は設定の中。独立した画面は持たない

    設定から見られるものを2か所に出さない(`tests/test_web_master.py`
    が中身を確かめる)。
    """
    assert "マスタ管理".encode() in ready.get("/settings?t=test-token").data
    assert ready.get("/master?t=test-token").status_code == 404


def test_api_refused_while_starting(sandbox):
    from modules.packing_material_calculation.app import create_app
    from modules.packing_material_calculation.coil_tool import db
    with db.connect() as conn:
        db.apply_schema(conn)
    app = create_app(token="t")          # READY のまま立てない
    with app.test_client() as c:
        c.environ_base["HTTP_X_APP_TOKEN"] = "t"
        r = c.get("/api/calc")
        assert r.status_code == 503
        assert r.get_json()["reason"] == "starting"
        # 起動確認と心拍は通る
        assert c.get("/api/health").status_code == 200
        assert c.post("/api/alive", json={}).status_code == 200


def test_バリ入力はスピンボタンでも変えられる(client):
    """VBA の UFalmo はスピンで増減できた。手袋のままでも押せるように。

    打ち込みも残す ── 10本を打つのに10回押させない。
    """
    page = client.get("/calc?t=test-token").data.decode()
    for name in ("upper", "lower"):
        assert f'data-step="1" data-for="{name}"' in page, name
        assert f'data-step="-1" data-for="{name}"' in page, name
    # 本数なので0より下へ行かせない
    assert 'id="upper" type="number"' in page
    assert 'min="0"' in page


def test_最下部は長と短を列にして組で出す(client):
    """「長さ / 本数（長） / 長さ（短） / 本数（短）」と横に4つ並べると、
    どの長さがどの本数のものか目で線を引くことになり、取り違える。

    列を「長 / 短」にすれば **1列を上から下に読めば1組**。
    """
    import re
    page = client.get("/calc?t=test-token").data.decode()
    table = re.search(r'<table class="pair">.*?</table>', page, re.S)
    assert table, "組で読ませる表がありません"
    body = table.group(0)

    # 見出しは 長 / 短
    assert "<th>長</th>" in body and "<th>短</th>" in body
    # 長の列 = 長さ(r-下長) と 本数(r-長本)、短の列 = r-下短 と r-短本
    for 長, 短 in (("r-下長", "r-下短"), ("r-長本", "r-短本")):
        assert body.index(長) < body.index(短), f"{長} は {短} より左"
    # 長さの行が本数の行より上
    assert body.index("r-下長") < body.index("r-長本")


def test_最下部リプラの並びは画面に出さない(client):
    """本数は表に出ているので、並びの絵からは新しく分かることが無い。

    計算そのものは VBA どおり残してある(要るようになったら出すだけ)。
    """
    page = client.get("/calc?t=test-token").data.decode()
    assert 'id="shape"' not in page
    assert 'id="bar0"' not in page


def test_並びの計算そのものは残っている(ready):
    """画面から外しただけ。VBA `図形表示` の判定は消していないので、
    要るようになったら出すだけでよい。
    """
    r = _calc(ready)
    assert r.status_code == 200
    shape = r.get_json()["view"]["shape"]
    assert set(shape) == {"kind", "bars"}


# ==================================================================
# 包装仕様No を押すと、コピーして閲覧システムを開く
# ==================================================================
def test_包装仕様Noは閲覧システムへのリンク(ready):
    """姉妹ツール python-web-tools と同じ。閲覧システムは固定URLで開く
    だけで番号が渡らないので、画面が押したときに番号をコピーする。"""
    from modules.packing_material_calculation.coil_tool import config
    page = ready.get("/calc?t=test-token").data.decode()
    assert 'id="o-包装"' in page
    assert f'href="{config.URL_HOSO_SHIYOSHO}"' in page
    assert 'target="_blank"' in page and 'rel="noopener"' in page


def test_包装仕様Noを押すとコピーする作り():
    """押したときのコピーは画面(JS)の仕事。消されていないかだけ見る。"""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent
          / "app/static/js/views/calc.js").read_text(encoding="utf-8")
    handler = js.split("$('#o-包装').addEventListener('click'", 1)[1][:900]
    assert "navigator.clipboard.writeText(no)" in handler
    assert "をコピーしました" in handler
    assert "e.preventDefault()" in handler        # 番号が無いときは開かない
