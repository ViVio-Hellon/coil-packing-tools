"""取り込み定義 ── 取り込み元のどの列を、手元のどの列に入れるか

VBA は `GetFieldsArr` でフィールド名の並びを取り、`Select Case` で
「この名前ならこの添字」と対応づけていた。列の順番が変わっても壊れない
ための作りで、その考え方はここでも同じ ── **名前で対応づける**。

    (手元の列名, 取り込み元の列名, 変換関数)

取り込み元の列名に**半角カナが混ざる**のは元の Access がそうだったため
(`ﾛｯﾄ番号` `用途ｺｰﾄﾞ` `ｺｲﾙ外径_MAX` …)。手元側は読みやすい全角に寄せる。

【`schema.sql` と対で直す】
片方だけ直すと取り込みが落ちる。列を足すときは必ず両方。
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# ------------------------------------------------------------------
# 値の変換
# ------------------------------------------------------------------
# 取り込み元の型はあてにしない。Access 由来の sqlite3 では、数値のはずの
# 列に文字列が入っていることがある(Access の「数値型」がそのまま
# TEXT で落ちてくる)。ここで明示的に寄せる。


def to_text(value: Any) -> str:
    """文字列へ。None と空白は空文字。

    **前後の空白を落とす。** 包装仕様NO や ロット番号は突き合わせに
    使うので、見えない空白が付いていると一致しなくなる。
    """
    if value is None:
        return ""
    return str(value).strip()


def to_real(value: Any) -> float:
    """実数へ。数値として読めなければ 0.0。

    0 は「指定なし」を表す。VBA も `<> 0` で判定していた
    (`If TempHiki(1, C_梱包単位_重量) <> 0 Then … Else "データなし"`)。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def to_int(value: Any) -> int:
    """整数へ。読めなければ 0。"""
    return int(to_real(value))


# ------------------------------------------------------------------
# 梱包資材マスタ (VBA `PATH_AIM_参照` \ 梱包資材マスタ)
# ------------------------------------------------------------------
ColumnSpec = tuple[str, str, Callable[[Any], Any]]

# 【包装仕様】判定ロジックの入口。VBA が `Select Case CoilF(1, i)` で
# 拾っていた列をすべて持つ。
#
# **数値になりうる列も TEXT のまま入れる。** `梱包単位_重量` は
# 「値が入っている / 空 / 数値でない」の3通りを区別する必要があり
# (VBA は `<> "" And IsNumeric(...)` で見ていた)、数値へ寄せると
# 空と 0 の区別が消える。判定する側で読む。
SPEC_COLUMNS: list[ColumnSpec] = [
    ("包装仕様NO", "包装仕様NO", to_text),
    ("納入先名称", "納入先名称", to_text),
    ("用途名", "用途名", to_text),
    ("パレット", "パレット", to_text),
    ("サイズ", "サイズ", to_text),
    ("種類", "種類", to_text),
    ("リプラサイズ", "リプラサイズ", to_text),
    ("コイル間スペーサー", "コイル間スペーサー", to_text),
    ("最下部スペーサー", "最下部スペーサー", to_text),
    ("下本数", "下本数", to_text),
    ("緩衝材", "緩衝材", to_text),
    ("コイル間は間紙入", "ｺｲﾙ間は間紙入", to_text),
    ("梱包単位_重量", "梱包単位_重量", to_text),
    ("重量範囲", "重量範囲", to_text),
    ("梱包単位_枚数", "梱包単位_枚数", to_text),
    ("枚数範囲", "枚数範囲", to_text),
    ("梱包総高さ", "梱包総高さ", to_text),
    ("高さ範囲", "高さ範囲", to_text),
]

# 【パレット】外径が収まるパレットを選ぶための表。
# 上下限とＷは数値として比較するので寄せておく
PALLET_COLUMNS: list[ColumnSpec] = [
    ("種類", "種類", to_text),
    ("巾下限", "巾下限", to_real),
    ("巾上限", "巾上限", to_real),
    ("丈下限", "丈下限", to_real),
    ("丈上限", "丈上限", to_real),
    ("新記号", "新記号", to_text),
    ("W", "Ｗ", to_real),
]

# 【リプラサイズ】在庫にあるリプラの定尺
RIPLA_SIZE_COLUMNS: list[ColumnSpec] = [
    ("リプラ長さ", "リプラ長さ", to_real),
    ("長さ", "長さ", to_text),
]

# 【班員名簿】依頼者(担当者)の名簿。**読むだけ**の表。
# 姉妹ツール(日報)の `班員名簿` と同じ表・同じ列を読む ── 名簿を
# 二重に持つと、異動があったときに片方だけ古くなる。
STAFF_COLUMNS: list[ColumnSpec] = [
    ("管理番号", "管理番号", to_text),
    ("苗字", "苗字", to_text),
    ("班", "班", to_text),
    ("名前", "名前", to_text),
    ("読み", "読み", to_text),
    ("担当ライン", "担当ライン", to_text),
]

MASTER_IMPORT_SPECS: dict[str, list[ColumnSpec]] = {
    "包装仕様": SPEC_COLUMNS,
    "パレット": PALLET_COLUMNS,
    "リプラサイズ": RIPLA_SIZE_COLUMNS,
    "班員名簿": STAFF_COLUMNS,
}

# ------------------------------------------------------------------
# 仕掛台帳 (VBA `PATH_仕掛_参照` \ SIKAHIKI / SIKAODR)
# ------------------------------------------------------------------
# 仕掛台帳側のテーブル名は、どのファイルでも「仕掛」
LOT_SOURCE_TABLE = "仕掛"

LOT_IMPORT_SPECS: dict[str, list[ColumnSpec]] = {
    # VBA `LOT検索`: ロット番号 → 受注番号の候補、と出荷日
    "仕掛引当": [
        ("ロット番号", "ﾛｯﾄ番号", to_text),
        ("受注番号", "受注番号", to_text),
        ("出荷日", "出荷日", to_text),
    ],
    # VBA `フォーム展開`: 受注番号から計算に要る値をすべて
    "仕掛受注": [
        ("受注番号", "受注番号", to_text),
        ("受注材質", "受注材質", to_text),
        ("受注調質", "受注調質", to_text),
        ("受注板厚", "受注板厚", to_real),
        ("受注板幅", "受注板幅", to_real),
        ("用途コード", "用途ｺｰﾄﾞ", to_text),
        ("用途名", "用途名", to_text),
        ("包装仕様NO", "包装仕様NO", to_text),
        ("納入先名称", "納入先名称", to_text),
        ("取引先名称", "取引先名称", to_text),
        ("製品単重", "製品単重", to_real),
        ("梱包単位_重量", "梱包単位_重量", to_real),
        ("梱包単位_枚数", "梱包単位_枚数", to_real),
        ("コイル外径_MAX", "ｺｲﾙ外径_MAX", to_real),
        ("コイル外径_目標", "ｺｲﾙ外径_目標", to_real),
        ("コイル外径_MIN", "ｺｲﾙ外径_MIN", to_real),
        ("コイル内径_目標", "コイル内径_目標", to_real),
        ("工場用コメント", "工場用ｺﾒﾝﾄ", to_text),
        ("営業納期", "営業納期", to_text),
        ("材質_比重", "材質_比重", to_real),
        ("梱包コード", "梱包ｺｰﾄﾞ", to_text),
    ],
}

# 取り込み元のファイル名(手元のテーブル名 → 探すファイル名)は
# `config.LOT_DB_FILES` が持つ。ここは列の対応だけ

# 取り込み元に無くても止めない列。
#
# 上流(RNE)の更新で列名が変わることがあり、VBA も
# 「ﾌｨｰﾙﾄﾞ名ﾁｪｯｸ *RNE更新に対応」と書いて名前で引き直していた。
# **無い列は既定値で埋めて、残りは取り込む。** 1列消えただけで
# ロット検索そのものが使えなくなるほうが困る
OPTIONAL_SOURCE_COLUMNS: dict[str, frozenset[str]] = {
    "仕掛引当": frozenset({"出荷日"}),
    "仕掛受注": frozenset({
        "コイル内径_目標", "梱包ｺｰﾄﾞ", "工場用ｺﾒﾝﾄ", "営業納期",
        "ｺｲﾙ外径_目標", "ｺｲﾙ外径_MIN",
    }),
    "包装仕様": frozenset({"最下部スペーサー", "ｺｲﾙ間は間紙入", "種類", "サイズ"}),
    "パレット": frozenset(),
    "リプラサイズ": frozenset({"長さ"}),
    # 並べ替えと区切りに使うだけの列。無くても名前は出せる
    "班員名簿": frozenset({"管理番号", "苗字", "読み", "担当ライン"}),
}

# この列が空の行は取り込まない(鍵が無い行は引けないので写しても意味が無い)
REQUIRED_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "包装仕様": ("包装仕様NO",),
    "パレット": ("種類",),
    "リプラサイズ": (),
    # 名前の無い行は依頼者欄に出せない(日報ツールも同じ扱い)
    "班員名簿": ("名前",),
    "仕掛引当": ("ロット番号",),
    "仕掛受注": ("受注番号",),
}


# ------------------------------------------------------------------
# 鍵が重なる行
# ------------------------------------------------------------------
# 手元の表が「鍵ごとに1行」で持っている表。**取り込み元に同じ鍵の行が
# 2つあったら、先に出てきたほうを採る。**
#
# 【なぜ先か】VBA は鍵で引いて `TempHiki(1, ...)` ── **最初の1件**しか
# 見ない(`包装仕様` を引く `PalletType` / `台数計算` / `リプラ計算` は
# どれもこの形)。あとの行で上書きすると、VBA と違う行で計算することに
# なる。実際、提出された梱包資材マスタには `1C0118` が2行あり、
# 片方は重量指定、もう片方は枚数指定で**計算が変わる**。
#
# 飛ばした鍵は呼ぶ側へ返す ── **黙って捨てない**。元のマスタが
# 直せるように画面に出す。
UNIQUE_KEY_COLUMN: dict[str, str] = {
    "包装仕様": "包装仕様NO",
    "仕掛受注": "受注番号",
}


def drop_duplicates(
    table: str, rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """鍵が重なる行を落とす。**先に出てきた行を残す。**

    戻り値は `(残した行, 落とした鍵)`。鍵を持たない表はそのまま返す。
    """
    key = UNIQUE_KEY_COLUMN.get(table)
    if not key:
        return rows, []
    kept: list[dict[str, Any]] = []
    seen: set[Any] = set()
    dropped: list[str] = []
    for row in rows:
        value = row.get(key, "")
        if value in seen:
            dropped.append(str(value))
            continue
        seen.add(value)
        kept.append(row)
    return kept, dropped


def default_for(convert: Callable[[Any], Any]) -> Any:
    """取り込み元にその列が無かったときに入れる値。"""
    if convert is to_text:
        return ""
    if convert is to_int:
        return 0
    return 0.0


def missing_columns(table: str, available: set[str]) -> list[str]:
    """取り込み元に無く、**無いと困る**列を返す。空なら取り込める。"""
    specs = MASTER_IMPORT_SPECS.get(table) or LOT_IMPORT_SPECS.get(table)
    if specs is None:
        raise ValueError(f"未知のテーブル: {table!r}")
    optional = OPTIONAL_SOURCE_COLUMNS.get(table, frozenset())
    return [src for _, src, _ in specs
            if src not in available and src not in optional]


def convert_row(table: str, row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """取り込み元の1行を、手元の列名の辞書にする。

    鍵の列が空なら `None` を返す(その行は写さない)。
    """
    specs = MASTER_IMPORT_SPECS.get(table) or LOT_IMPORT_SPECS.get(table)
    if specs is None:
        raise ValueError(f"未知のテーブル: {table!r}")

    out: dict[str, Any] = {}
    for dest, src, convert in specs:
        if src in row:
            out[dest] = convert(row[src])
        else:
            out[dest] = default_for(convert)

    for key in REQUIRED_KEY_COLUMNS.get(table, ()):
        if not str(out.get(key, "")).strip():
            return None
    return out
