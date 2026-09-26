"""VBAが書き出した画面の値を、照合表へ取り込む

現行VBAの画面から `docs/検証/照合出力.bas` が書き出した CSV を読み、
`docs/検証/VBA実績.csv`(突き合わせの表)へ流し込みます。

    python tools/照合取り込み.py 照合_20260922.csv
    python tools/照合取り込み.py フォルダ          (中の CSV をぜんぶ)
    python tools/照合取り込み.py a.csv b.csv …     (いくつでも)

書き写す作業はありません。**画面のコントロール名がそのまま列名**に
なるように作ってあるので、ここは対応表を引くだけです。

色も一緒に取り込みます ── `F重量` `F枚数` `F高さ` `F包装仕様` の文字色が
赤なら「赤い欄」へ、`FM` の枠色が水色/赤なら「枠の色」へ。
目で見て書き写す必要はありません。

【表に無いロットも取り込める】
書き出しには受注の中身(板厚・板幅・製品単重・梱包単位…)も画面の
欄として入っている。VBA は**まさにその画面の値**で計算しているので、
そこから仕掛引当・仕掛受注の行を組み立てて
`tests/data/照合データ_書き出しから.sql` へ足す。**台帳の写しは要らない。**
珍しい仕様のロットが流れた日にマクロを押してもらえば、それで照合できる。

取り込んだら:

    python -m pytest tests/test_vba照合.py -q
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHEET = ROOT / "docs" / "検証" / "VBA実績.csv"
FROM_DUMPS = ROOT / "tests" / "data" / "照合データ_書き出しから.sql"

# 画面の欄 → 仕掛受注の列。VBA `フォーム展開` が埋める欄の逆をたどる
ORDER_FROM_FORM = {
    "受注番号": "受注番号", "受注材質": "受注材質", "受注調質": "受注調質",
    "受注板厚": "受注板厚", "受注板幅": "受注板幅",
    "用途コード": "用途コード", "用途名": "用途名", "包装仕様NO": "包装仕様NO",
    "納入先名称": "納入先名称", "取引先名称": "取引先名称",
    "製品単重": "製品単重",
    "梱包単位_重量": "梱包単位_重量", "梱包単位_枚数": "梱包単位_枚数",
    "コイル外径_MAX": "コイル外径_MAX", "コイル外径_目標": "コイル外径_目標",
    "コイル外径_MIN": "コイル外径_MIN", "コイル内径_目標": "コイル内径_目標",
    "工場用コメント": "工場用コメント", "出荷": "営業納期",
    "比重": "材質_比重", "梱包コード": "梱包コード",
}
# 数で持つ列。画面の「データなし」は 0 に戻す(`フォーム展開` の逆)
NUMERIC = {"受注板厚", "受注板幅", "製品単重", "梱包単位_重量", "梱包単位_枚数",
           "コイル外径_MAX", "コイル外径_目標", "コイル外径_MIN",
           "コイル内径_目標", "材質_比重"}

# 画面のコントロール名 → 表の列名。
# 名前が同じものは書かない(そのまま通す)
CONTROL_TO_COLUMN = {
    "HightCoil": "総高さ",
    "LOT": None,          # 行の鍵。値としては入れない
}
SAME_NAME = (
    "パレット種類", "パレット名称", "パレットサイズ",
    "積数", "台数", "Re_積数", "Re_台数",
    "コイル間", "リプラサイズ", "最下部本数", "最下部長さ", "最下部長さ_短",
    "長い本数", "短い本数", "リプラ長さ", "本数",
    "IMI中間長さ", "IMI中間本数", "緩衝材", "HB枚数", "TotalC",
    "受注番号", "外径", "検入数",
)

# VBA の色。`RGB(r,g,b) = r + g*256 + b*65536`
RED = 255                 # RGB(255, 0, 0)
CYAN = 16776960           # RGB(0, 255, 255)

# 赤くなる欄 → 表に書く言葉
RED_FIELDS = {"F重量": "重量", "F枚数": "枚数",
              "F高さ": "高さ", "F包装仕様": "包装仕様"}


def read_dump(path: Path) -> list[dict]:
    """書き出しを読む。VBA の `Print #` は Shift-JIS で書く。"""
    for encoding in ("utf-8-sig", "cp932"):
        try:
            with path.open(encoding=encoding, newline="") as f:
                rows = list(csv.DictReader(f))
            with path.open(encoding=encoding, newline="") as f:
                header = next(csv.reader(f), [])
            if "コントロール" in header:
                return rows
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"読めませんでした(形が違う?): {path}")


def latest_per_lot(rows: list[dict]) -> dict[str, list[dict]]:
    """同じロットを2度書き出していたら、**あとの回**を採る。"""
    by_lot: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_lot[row["LotNo"].strip()][row["書き出し時刻"]].append(row)
    out: dict[str, list[dict]] = {}
    for lot, runs in by_lot.items():
        newest = max(runs)
        out[lot] = runs[newest]
    return out


def to_sheet_row(controls: list[dict]) -> dict:
    """1回ぶんの書き出しを、表の1行にする。"""
    row: dict[str, str] = {}
    赤 : list[str] = []
    枠 = ""

    for c in controls:
        name = c["コントロール"].strip()
        value = c["値"].replace("\\n", "\n").strip()
        # マクロが読めなかった欄。値としては使わない(空として扱う)
        if value.startswith("#読めず:"):
            print(f"  ! {c['LotNo']} の {name} は VBA 側で読めませんでした: {value}")
            value = ""

        if name in RED_FIELDS:
            if (c.get("文字色") or "").strip() == str(RED):
                赤.append(RED_FIELDS[name])
            continue
        if name == "FM":
            color = (c.get("枠色") or "").strip()
            枠 = {str(CYAN): "水色", str(RED): "赤"}.get(color, "")
            continue

        column = CONTROL_TO_COLUMN.get(name, name if name in SAME_NAME else None)
        if column:
            row[column] = value

    # IMI中間の欄は VBA の フォームクリア/ソフトクリア が消さないので、
    # ｱｲｴﾑｱｲｶﾊﾞｰ(1C1282)を計算したあとの画面に**前の値が残る**。
    # 1C1282 以外では VBA の答えとして扱わない
    spec = next((c["値"].strip() for c in controls
                 if c["コントロール"].strip() == "包装仕様NO"), "")
    if spec != "1C1282":
        row["IMI中間長さ"] = ""
        row["IMI中間本数"] = ""
    # 疑似ロット(Q で始まる)は受注番号を持たない。組み立てた受注の番号に揃える
    lot = controls[0]["LotNo"].strip() if controls else ""
    if not row.get("受注番号", "").strip():
        row["受注番号"] = 疑似の受注番号(lot)

    row["赤い欄"] = ",".join(赤)
    row["枠の色"] = 枠
    # 台数が出ていなければ、VBA は断ったということ
    # (「包装仕様No未登録」「引当データなし」のとき)
    if not row.get("台数", "").strip() or row.get("台数", "").strip() == "0":
        row["断られた"] = "台数が出ませんでした"
    return row


def 疑似の受注番号(lot: str) -> str:
    """受注番号の無いロット(疑似ロット)に振る番号。実物の8桁と重ならない。"""
    return f"Q{lot}"


def _lit(value) -> str:
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def order_sql(lot: str, controls: list[dict]) -> str:
    """書き出しから、仕掛引当と仕掛受注の行を組み立てる。"""
    form = {c["コントロール"].strip(): c["値"].replace("\\n", "\n").strip()
            for c in controls}
    order: dict = {}
    for control, column in ORDER_FROM_FORM.items():
        value = form.get(control, "")
        if value.startswith("#読めず:"):
            value = ""
        if column == "受注番号" and not value:
            value = 疑似の受注番号(lot)
        if column in NUMERIC:
            try:
                order[column] = float(value.replace(",", ""))
            except ValueError:
                order[column] = 0.0          # 「データなし」・空
        else:
            order[column] = value
    when = controls[0]["書き出し時刻"]
    cols = ", ".join(f'"{c}"' for c in order)
    vals = ", ".join(_lit(v) for v in order.values())
    return (
        f"-- {lot}  ({when} の書き出しから)\n"
        f'INSERT OR IGNORE INTO "仕掛引当" ("ロット番号", "受注番号", "出荷日")'
        f" VALUES ({_lit(lot)}, {_lit(order['受注番号'])}, {_lit(order['営業納期'])});\n"
        f'INSERT OR IGNORE INTO "仕掛受注" ({cols}) VALUES ({vals});\n')


def add_from_dump(lot: str, controls: list[dict], columns: list[str]) -> dict:
    """表に無いロットを、書き出しだけで照合できるようにする。"""
    head = ("-- 照合用: 現場の書き出しから組み立てた受注\n"
            "--\n"
            "-- tools/照合取り込み.py が書く。手で直さない。\n"
            "-- VBA は画面に出ている受注の値で計算しているので、その値を\n"
            "-- 仕掛引当・仕掛受注の行に戻したもの(台帳の写しは要らない)。\n\n")
    text = FROM_DUMPS.read_text(encoding="utf-8") if FROM_DUMPS.exists() else head
    text += order_sql(lot, controls)
    FROM_DUMPS.write_text(text, encoding="utf-8")

    row = {c: "" for c in columns}
    row["LotNo"] = lot
    kind = "疑似ロット" if lot.startswith("Q") else "現場の書き出しから追加"
    row["ねらい"] = f"{kind}({controls[0]['書き出し時刻'][:10]})"
    form = {c["コントロール"].strip(): c["値"].strip() for c in controls}
    row["包装仕様NO"] = form.get("包装仕様NO", "")
    return row


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    # ロットごとにファイルを分けて渡されることがある。フォルダも受ける
    files: list[Path] = []
    for arg in argv[1:]:
        path = Path(arg)
        if path.is_dir():
            files.extend(sorted(path.glob("*.csv")))
        elif path.exists():
            files.append(path)
        else:
            print(f"ありません: {path}")
            return 2

    dumped: list[dict] = []
    for dump in files:
        rows = read_dump(dump)
        if not rows:
            # 見出しの行しか無い。マクロが1欄も書けずに止まっている
            print(f"{dump.name} には見出しの行しかありません(画面の値が1つも入っていない)。")
            print("  マクロの前の版の不具合です。docs/検証/照合出力.bas の新しい版に")
            print("  入れ替えて、もう一度書き出してください。")
            continue
        dumped.extend(rows)
    if not dumped:
        return 1
    runs = latest_per_lot(dumped)

    with SHEET.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        sheet = {r["LotNo"]: r for r in reader}

    埋めた, 足した = [], []
    for lot, controls in runs.items():
        if lot not in sheet:
            sheet[lot] = add_from_dump(lot, controls, columns)
            足した.append(lot)
        new = to_sheet_row(controls)
        for column, value in new.items():
            if column in columns:
                sheet[lot][column] = value
        埋めた.append(lot)

    with SHEET.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(sheet.values())

    print(f"取り込みました: {len(埋めた)} 件 ({', '.join(sorted(埋めた))})")
    if 足した:
        print(f"表に無かったので書き出しから足しました: {', '.join(sorted(足した))}")
        print(f"  受注の行は {FROM_DUMPS.relative_to(ROOT)} に書きました")
    print(f"\n{SHEET.relative_to(ROOT)} を更新しました。次:")
    print("  python -m pytest tests/test_vba照合.py -q")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
