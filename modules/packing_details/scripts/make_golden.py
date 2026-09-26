"""実データの仕掛台帳から、回帰試験の期待値(golden)を作る

**VBAの出力紙が無くても、台帳側の事実はここで固定できる。**
ロット番号を入れると何が引けて、前工程実績数がいくつになり、
条数がどう配られるか ── ここまでは取り込み元だけで決まる。

    python scripts/make_golden.py <取り込み元のフォルダ> [出力先]

作られた JSON は `tests/test_golden.py` がそのまま読む。上流の
写しが新しくなったら作り直して、差分を見て「変わったのはデータか、
こちらの計算か」を切り分ける。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.packing_details.meisai import config, data_sync, db, lot_repo, strand_service  # noqa: E402

# 期待値に採るロット数。**多すぎても読めない。**
# 3ファイルすべてが揃うものから、条件のばらける順に採る
SAMPLE_LIMIT = 40


def build(source_dir: Path) -> dict:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.apply_schema(conn)

    found = data_sync.find_sources(source_dir, source_dir)
    missing = data_sync.missing_sources(found)
    if missing:
        raise SystemExit(
            f"取り込み元が足りません: {', '.join(missing.values())}\n"
            f"探した場所: {source_dir}")
    for table, path in found.items():
        data_sync.import_table(conn, table, path)

    # 3ファイルすべてで引けるロットを、条件がばらけるように選ぶ。
    # **縦割数ごとに拾う** ── 全部が縦割1だと配分の余りを踏めない
    rows = conn.execute(
        "SELECT t.ロット番号 AS lot, t.当工程設計_縦割数 AS tate,"
        "       t.当工程設計_横割数 AS yoko"
        "  FROM 仕掛当工程 t"
        "  JOIN 仕掛ロット l ON l.ロット番号 = t.ロット番号 AND l.オーダー板丈 = 0"
        " GROUP BY t.ロット番号"
        " ORDER BY t.当工程設計_縦割数 DESC, t.当工程設計_横割数 DESC,"
        "          t.ロット番号").fetchall()

    picked: list[sqlite3.Row] = []
    seen: dict[int, int] = {}
    for row in rows:
        tate = int(row["tate"] or 0)
        if seen.get(tate, 0) >= max(2, SAMPLE_LIMIT // 6):
            continue
        seen[tate] = seen.get(tate, 0) + 1
        picked.append(row)
        if len(picked) >= SAMPLE_LIMIT:
            break

    cases = []
    for row in picked:
        lot_no = row["lot"]
        result = lot_repo.search(conn, lot_no)
        lot = result.lot
        zen = lot.zen_kotei_jisseki_su
        jou = lot.tate_wari if 1 <= lot.tate_wari <= config.JOUSU_MAX else 0
        strands = strand_service.distribute(zen, jou) if jou else []
        cases.append({
            "lot_no": lot_no,
            "yoto_code": lot.yoto_code,
            "yoto_name": lot.yoto_name,
            "zaishitsu": lot.zaishitsu,
            "choshitsu": lot.choshitsu,
            # 表示書式まで固定する。VBA の Format と同じ文字列か
            "seizou_thickness": lot.seizou_thickness_text,
            "seizou_width": lot.seizou_width_text,
            "odr_thickness": lot.odr_thickness_text,
            "odr_width": lot.odr_width_text,
            "sekkei_course": lot.sekkei_course,
            "jisseki_course": lot.jisseki_course,
            "tate_wari": lot.tate_wari,
            "yoko_wari": lot.yoko_wari,
            "zen_kotei": zen,
            "jou_su": jou,
            "strands": strands,
            # 条番号の並び(丈1のぶんだけ。全部入れると読めない)
            "keys_jou1": strand_service.keys_for_jou(1, strands[0]) if strands else [],
            "orders": [{"order_no": o.order_no, "spec_no": o.spec_no}
                       for o in result.orders],
        })

    # **自動セットのままだと配分の余りが一度も出ない。**
    #   前工程実績数 = 縦割数 × 横割数、丈数 = 縦割数
    # なので必ず割り切れる。余りが出るのは、作業者が丈数か
    # 前工程実績数を手で直したとき(ギミックA)だけ。
    # RULE-03 の「余りは丈番号の大きいほうから」が効く場面なので、
    # その形も期待値に残す
    overrides = []
    for case in cases[:6]:
        zen = case["zen_kotei"]
        for jou in (case["jou_su"] + 1, case["jou_su"] + 2):
            if not 1 <= jou <= config.JOUSU_MAX or zen < 1:
                continue
            strands = strand_service.distribute(zen, jou)
            if len(set(strands)) == 1:
                continue                      # 割り切れた。余りの例にならない
            overrides.append({
                "lot_no": case["lot_no"],
                "why": "丈数を手で変えた(ギミックA相当)",
                "zen_kotei": zen,
                "jou_su": jou,
                "strands": strands,
            })

    counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
              for t in ("仕掛ロット", "仕掛引当", "仕掛受注", "仕掛当工程")}
    stamps = {s.table: s.created_at for s in data_sync.stamps(conn)}
    conn.close()
    return {"source_counts": counts, "source_created_at": stamps,
            "cases": cases, "manual_overrides": overrides}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    source = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else (
        Path(__file__).resolve().parent.parent / "tests" / "golden" / "lot_cases.json")
    data = build(source)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"{out} に {len(data['cases'])}件 書きました")
    print("取り込み元:", data["source_counts"])
    print(f"うち手修正の例: {len(data['manual_overrides'])}件"
          "(自動セットのままでは配分の余りが出ないため)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
