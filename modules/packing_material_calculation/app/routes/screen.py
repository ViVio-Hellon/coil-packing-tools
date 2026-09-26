"""使ってよい画面の受付 (`/api/screen/*`)

画面が開くたびに、ここへ「この画面で使わせてほしい」と申し出る。
2枚目は断られ、**使える姿にならない**。

理由と、なぜ取り上げを用意したかは `app/screen.py` に書いてある。

【トークンを要求しない】
`/api/alive` と同じ扱いにする。関門そのものに関門を付けると、
断られた画面が「なぜ断られたか」も受け取れなくなる。
申し出で分かるのは「いま開いているか」だけで、業務データは出さない。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import screen

bp = Blueprint("screen", __name__)


@bp.post("/api/screen/claim")
def claim():
    """`{"screen": "…", "takeover": false}`

    断りは 409。**入力の形が違う**(符牒が無い)は 400 で分ける ──
    画面は文言ではなく `reason` を見る。
    """
    body = request.get_json(silent=True) or {}
    result = screen.claim(str(body.get("screen", "")),
                          takeover=bool(body.get("takeover")))
    if result.ok:
        return jsonify(ok=True)
    status = 400 if result.reason == screen.REFUSE_NO_SCREEN else 409
    return jsonify(ok=False, reason=result.reason, message=result.message,
                   other_age=round(result.other_age)), status


@bp.get("/api/screen")
def state():
    """いま誰か開いているか。**断られた画面が空きを待つために見る。**

    ここは見るだけで、符牒を取らない。自動で取りに行くと、画面を
    移っている一瞬の隙に横から入れ替わってしまう。
    """
    live = screen.holder()
    mine = str(request.args.get("screen", "")).strip()
    return jsonify(
        ok=True,
        busy=live is not None and live.screen != mine,
        mine=live is not None and live.screen == mine,
        other_age=round(live.age()) if live is not None else 0,
    )


@bp.post("/api/screen/release")
def release():
    """閉じた。`{"screen": "…"}`"""
    body = request.get_json(silent=True) or {}
    screen.release(str(body.get("screen", "")))
    return jsonify(ok=True)
