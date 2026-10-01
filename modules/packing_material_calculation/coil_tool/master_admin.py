"""梱包資材マスタの管理 ── 中身を見る / 直す

姉妹ツール(python-web-tools)の `master_admin` の移植です。思想は同じで、
扱う表だけがこのツールのもの(包装仕様 / パレット / リプラサイズ)に
なっています。

【なぜ要るのか】
梱包資材マスタは起動のたびに**黙って読まれるだけ**でした。中に何が
入っているかを見る手立ても、間違いを直す手立ても画面にありません。
値が違っていても、現場には「パレットが出ない」「リプラの長さがおかしい」
という形でしか現れず、原因に辿り着けません。
確かめる場所と、直す場所をここに作ります。

【どこを直すのか ── 取り込み元**だけ**】
取り込みは総入れ替えです(`data_sync`)。手元のDBを直しても、次の
取り込みで消えます。**同じ事実を2か所に持たない**ので、書き先は
取り込み元(共有フォルダの sqlite3)ただ1つにして、書いたあとに
その表だけ取り込み直します。

    画面 → 取り込み元へ書く → その表だけ取り込み直す → 手元が追いつく

【行をどう指すのか】
sqlite3 の暗黙の `rowid` を使います。`パレット` と `リプラサイズ` の
`id` は**取り込みのときに手元で振り直している**ので、手元の `id` は
取り込み元の行を指しません。取り込み元の行は取り込み元の言葉で
指す必要があります(`ROW_KEY`)。ここを踏むと「直したつもりが別の行
だった」になります。

【誰が直せるのか ── パスワードを通した人だけ】
移植元は「資材モードの端末 + 管理者パスワード」の2段で塞いでいました。
このツールは**アクセス権限マスタを持たない**ので1段目は落とし、
**パスワードだけ**を関門にしてあります。

    見る   … いつでも通る。中身を確かめられることと、書き換えられる
             ことは別の話
    直す   … パスワードを通してから(`admin_session`)

判断は `can_edit()` **1か所**だけです。画面も保存も取り込み直しも同じ
答えを使うので、画面は通したのに保存で断られる(あるいはその逆)が
起きません。

**これは誰かを見分けるものではありません。** 共有フォルダのマスタを
誤って書き換えないための関門で、端末そのものに鍵は掛かりません。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from common import sql_sort
from . import admin_session, config, data_sync, db, import_specs, source_db
from .logging_utils import get_logger

log = get_logger("master_admin")

# 行を指す隠しの列名。取り込み元の `rowid` をこの名前で持ち回る。
# **業務の列と衝突しない名前**にする(全角を混ぜてあるのはそのため)
ROW_KEY = "__行"

# 一度に出す行数。**全部は出さない。**
# 包装仕様は数百行あり、全部描いても読めないし、共有から引くだけで待つ。
# 出さなかった分は必ず数で言う(`Page.note`)── 黙って切ると
# 「これで全部だ」と読めてしまう
ROW_LIMIT = 200

# 空欄なら今の日時を入れる列。手で打たせる意味が無く、打ち間違いだけが増える。
# (いまのところ3表のどれにも無いが、上流が足したときに効く)
STAMP_COLUMNS = ("更新日時", "登録日時")


# ==================================================================
# どの表を扱うか
# ==================================================================
@dataclass(frozen=True)
class Managed:
    """直せる表1つ。"""

    table: str
    label: str
    mark: str
    note: str


# **直せるのは、このツールが計算に使う3表だけ。**
# 並びは触る頻度の順(ヒックの法則)。包装仕様がほとんどを占める
MANAGED: tuple[Managed, ...] = (
    Managed(config.TBL_SPEC, "包装仕様", "包",
            "パレット種類・リプラ・梱包単位・高さの指定。判定の入口"),
    Managed(config.TBL_PALLET, "パレット", "パ",
            "種類ごとの適合範囲(巾・丈の上下限)と記号・Ｗ"),
    Managed(config.TBL_RIPLA_SIZE, "リプラサイズ", "リ",
            "在庫にあるリプラの定尺"),
)
BY_TABLE: dict[str, Managed] = {m.table: m for m in MANAGED}

# 直せない表と、その理由。**出さないのではなく、理由を出す。**
# 「なぜこの表だけ直せないのか」が分からないと、画面が壊れて見える。
#
# 梱包資材マスタは姉妹ツール(板・ザラ板の梱包)と**同じファイル**なので、
# 向こうの表もここに並びます。中身は見られますが、直すのは向こうの画面。
VIEW_ONLY_WHY: dict[str, str] = {
    "PalletMaster": "板側の表です。梱包資材総合ツールのマスタ管理から直します。",
    "BoardMaster": "板側の表です。梱包資材総合ツールのマスタ管理から直します。",
    "CornerboardMaster": "板側の表です。梱包資材総合ツールのマスタ管理から直します。",
    "梱包保護材": "板側の表です。梱包資材総合ツールのマスタ管理から直します。",
    "松板角材": "板側の表です。梱包資材総合ツールのマスタ管理から直します。",
    "PalletPatterns": "実績から貯まる表です。梱包資材総合ツールが書きます。",
    "資材パレット注文管理": "梱包資材総合ツールが書き戻す表です。",
    "パレット入出庫履歴": "梱包資材総合ツールが書き戻す表です。",
    "Form状態管理": "上流の設備が書く表です。",
    "アクセス権限": "梱包資材総合ツールの権限表です。このツールは読みません。",
    "班員名簿": "日報ツールが持つ名簿です。人の出入りはそちらで直します"
                "(ここで直すと、日報と食い違います)。",
}
DEFAULT_VIEW_ONLY = "このツールが直す表ではありません。中身の確認だけできます。"


def view_only_why(table: str) -> str:
    """その表を直せない理由。直せる表なら空。"""
    if table in BY_TABLE:
        return ""
    return VIEW_ONLY_WHY.get(table, DEFAULT_VIEW_ONLY)


# ==================================================================
# 直せるかどうか
# ==================================================================
# 断りの種類。**文言から推し量らない**(docs/移植計画.md の約束3・4)
REFUSE_NOT_ALLOWED = "not_allowed"      # 許されていない
REFUSE_NOT_EDITABLE = "not_editable"    # この表は直す表ではない
REFUSE_BAD_VALUE = "bad_value"          # 入れた値の形が違う
REFUSE_NO_SOURCE = "no_source"          # 取り込み元に届かない
REFUSE_NO_ROW = "no_row"                # その行がもう無い
REFUSE_WRITE_FAILED = "write_failed"    # 書けなかった
REFUSE_NOT_CREATABLE = "not_creatable"  # 取り込み元にその表が無い


def can_edit(conn: Optional[sqlite3.Connection], table: str = "",
             *, touch: bool = True) -> tuple[bool, str]:
    """この端末はいまマスタを直せるか。**直せないなら理由も返す。**

    関門はパスワード1つです(`admin_session`)。通っていなければ、
    どの表も直せません ── 書き先は共有のマスタで、直せば全端末に
    効くためです。

    `touch` は時間切れの数え直しをするかどうか。**画面を出すだけの
    ときは `False`** にします ── 開いた画面を眺めているだけで
    時間切れが来なくなると、時間切れを置いた意味がなくなります。
    """
    if conn is None:
        return False, "手元のデータベースを開けませんでした。"
    if admin_session.is_open() if touch else admin_session.peek():
        return True, ""
    return False, (
        "マスタを直すにはパスワードが要ります。"
        "設定のいちばん上でパスワードを入れてください。"
        "(見るだけならそのままできます)")


# ==================================================================
# どのファイルにある表か
# ==================================================================
def source_for(table: str) -> Optional[Path]:
    """その表がどのファイルにあるか。

    このツールが直す3表はすべて梱包資材マスタの中にあります。
    移植元は閾値マスタが別ファイルで分岐していましたが、ここでは
    分岐の必要がありません。

    **関数として残してあるのは、判断を1か所に置くためです。** 画面も
    保存も取り込み直しも同じ答えを使います ── 分かれていると、
    読めた表に書けない(あるいはその逆)が起きます。
    """
    return data_sync.find_master_db()


def source_label(table: str) -> str:
    """その表の取り込み元の呼び名。見つからないときの案内に使う。"""
    return f"梱包資材マスタ({config.MATERIAL_DB_NAME})"


def source_dir(table: str) -> Path:
    return config.master_db_dir()


# ==================================================================
# 取り込み元に無い表
# ==================================================================
# **このツールは取り込み元に表を作りません。**
#
# 移植元は「自分が後から足した表」だけを作れるようにしていました
# (`import_specs.OPTIONAL_TABLES`)。このツールが直す3表は、どれも
# 上流(資材課)が持っている表です。
#
# 無いのは資材課側の事情(ファイルが違う・移された・まだ配られていない)
# で、空の表を作ってしまうと**その事情が「0件」という形に化けて**、
# 原因を探せなくなります。作らずに、無いことと置き場所を言います。
def can_create(table: str) -> bool:
    return False


def _missing_why(table: str) -> str:
    """その表が取り込み元に無いことの意味と、次にできること。"""
    return (f"{table} が取り込み元にありません。"
            f"{source_label(table)}の置き場所({source_dir(table)})と、"
            "ファイルの中身を確かめてください。"
            "このツールは取り込み元に表を作りません ── "
            "無いのは資材課側の事情なので、空の表を作るとその事情が"
            "「0件」に化けてしまいます。")


# ==================================================================
# 列
# ==================================================================
@dataclass(frozen=True)
class Column:
    """直せる列1つ。

    【`name` は取り込み元での呼び名】
    行を取り込み元の言葉(`rowid`)で指すのと同じ理由です。直すのは
    取り込み元のファイルなので、**列も取り込み元の言葉で指します**。

    このツールは取り込みのときに列名を寄せています(半角カナの
    `ｺｲﾙ間は間紙入` → 手元では `コイル間は間紙入`)。手元の名前で
    書きに行くと「そんな列は無い」と断られ、読むときは空に見えます。

    `local` は手元での呼び名。型と必須かどうかを手元のスキーマから
    引くためだけに使います。
    """

    name: str               # 取り込み元での列名。**画面と保存はこれ**
    local: str              # 手元での列名。型と必須の判断にだけ使う
    kind: str               # "int" | "real" | "text"
    required: bool = False
    stamp: bool = False     # 空欄なら今の日時が入る
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "local": self.local, "kind": self.kind,
                "required": self.required, "note": self.note}


# 画面に出す型の名前。**サーバが言葉を持つ**(画面側で書き分けない)
KIND_LABEL = {"int": "整数", "real": "小数", "text": "文字"}


def columns(conn: sqlite3.Connection, table: str,
            present: Optional[Iterable[str]] = None) -> list[Column]:
    """その表で直せる列。

    出どころは4つとも既にあるものを読むだけ ── **同じ事実を書き足さない**。

        どの列を扱うか   … `import_specs.MASTER_IMPORT_SPECS`(取り込みが読む列)
        どんな型か       … 手元のスキーマ(`schema.sql`)
        空にできないか   … `import_specs.REQUIRED_KEY_COLUMNS`
        本当にあるか     … 取り込み元の列(`present`)

    取り込み元にしか無い列(管理番号など)はここに出ません。触らずに
    そのまま残します。逆に**取り込み元に無い列も出しません** ── 出すと
    打ち込めてしまい、保存の瞬間に「そんな列は無い」と断られます。

    【必須の判断を取り込みから借りる理由】
    手元のスキーマは、空とNULLの2通りを持たないために全列へ
    `NOT NULL DEFAULT ''` を付けてあります。そのままだと「既定がある
    ので空欄で通してよい」となり、**包装仕様NO を空のまま足せて**
    しまいます。その行は次の取り込みで黙って捨てられる
    (`convert_row` が鍵の無い行を落とす)ので、入れた人には「足した
    はずの行が消えた」としか見えません。取り込みが鍵と見なす列は、
    画面でも空にできないことにします。
    """
    spec = import_specs.MASTER_IMPORT_SPECS.get(table, [])
    if not spec:
        return []
    keys = set(import_specs.REQUIRED_KEY_COLUMNS.get(table, ()))
    allowed = None if present is None else set(present)
    info: dict[str, sqlite3.Row] = {}
    try:
        for row in conn.execute(
                f"PRAGMA table_info({source_db.quote_identifier(table)})"):
            info[row["name"]] = row
    except sqlite3.Error as exc:                 # pragma: no cover - 通常は無い
        log.warning("%s の列を引けません: %s", table, exc)
        return []

    out: list[Column] = []
    for local, source, _conv in spec:
        row = info.get(local)
        if row is None:
            continue
        if allowed is not None and source not in allowed:
            continue
        kind = _kind_of(str(row["type"]))
        stamp = local in STAMP_COLUMNS
        # 鍵の列、または既定もNULLも無い列が必須
        required = not stamp and (
            local in keys
            or (bool(row["notnull"]) and row["dflt_value"] is None))
        note = "空欄なら今の日時が入ります" if stamp else (
            "この列が空の行は、取り込みのときに捨てられます" if local in keys else "")
        if source != local:
            # 取り込みで名前を寄せている列。**取り込み元の名前で出す**が、
            # 手元では別名であることも見えるようにしておく
            note = (note + " / " if note else "") + f"手元では「{local}」"
        out.append(Column(name=source, local=local, kind=kind,
                          required=required, stamp=stamp, note=note))
    return out


def expected_column_names(table: str) -> list[str]:
    """この表で取り込みが読もうとする、取り込み元の列名。

    `MASTER_IMPORT_SPECS` の `source` 側(取り込み元での呼び名)を並べた
    だけ。`columns()` が0件を返したとき、「取り込み元にこの表はあるのに、
    なぜ1つも打ち込めないのか」を具体的に言うために使う。
    """
    return [source for _local, source, _conv
            in import_specs.MASTER_IMPORT_SPECS.get(table, [])]


def column_mismatch_why(table: str, present: Iterable[str]) -> str:
    """**表はある。列名が期待と違うので、1つも打ち込めない。**

    取り込み元に表そのものは存在するのに `columns()` が0件を返すのは、
    たいていこれが原因。「まだ取り込み元にありません」とは別の話で、
    専用の理由を出さないと、編集の窓がただ空になって何も打てない画面に
    しか見えない。

    **このツールでは現実に起きます。** 包装仕様の `ｺｲﾙ間は間紙入` は
    半角カナで、上流が全角に直すとここに落ちます。
    """
    expected = expected_column_names(table)
    if not expected:
        return ""
    have = set(present)
    if have & set(expected):
        # 一部でも一致していれば、これは別の状況(型違い等)。ここでは
        # 「1つも無い」ときだけに絞る
        return ""
    return (f"{table} は取り込み元にありますが、列名が想定と違うため"
            f"1つも打ち込めません。このツールが読む列名は "
            + " / ".join(expected) +
            f" です。取り込み元の実際の列名({', '.join(present) or '(列が無い)'})"
            "と見比べて、列名を合わせてください。")


def _kind_of(declared: str) -> str:
    upper = declared.upper()
    if "INT" in upper:
        return "int"
    if "REAL" in upper or "FLOA" in upper or "DOUB" in upper:
        return "real"
    return "text"


# ==================================================================
# 見る
# ==================================================================
@dataclass
class TableInfo:
    """一覧に出す表1つ。"""

    table: str
    label: str
    mark: str
    note: str
    rows: int
    editable: bool
    why: str = ""
    # 取り込み元に無い表。**隠さずに出す**
    missing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "label": self.label, "mark": self.mark,
                "note": self.note, "rows": self.rows,
                "editable": self.editable, "why": self.why,
                "missing": self.missing}


def tables(path: Optional[Path]) -> list[TableInfo]:
    """取り込み元にある表ぜんぶ。**直せないものも出す。**

    直せる表だけを出すと「あるはずの表が無い」に見えます。中身を
    確かめるのは全部の表でできるので、並べたうえで直せるかどうかを
    札にします。並びは `MANAGED` が先(触る頻度の順)、残りは名前順。

    **このツールが使う3表は、取り込み元に無くても並びに出します。**
    出さないと「無い表は画面にも無い」になり、包装仕様が読めていない
    ことに気づく場所がどこにもなくなります。
    """
    if path is None:
        return []
    counts = source_db.table_counts(path)
    if not counts:
        return []
    out: list[TableInfo] = []
    for managed in MANAGED:
        if managed.table not in counts:
            # 無いことを言う。作りはしない(`can_create` を参照)
            out.append(TableInfo(
                table=managed.table, label=managed.table, mark=managed.mark,
                note=managed.note, rows=0, editable=False, missing=True,
                why=_missing_why(managed.table)))
            continue
        out.append(TableInfo(
            table=managed.table, label=managed.table, mark=managed.mark,
            note=managed.note, rows=counts[managed.table], editable=True))
    for name in sorted(n for n in counts if n not in BY_TABLE):
        out.append(TableInfo(
            table=name, label=name, mark="他", note="", rows=counts[name],
            editable=False, why=view_only_why(name)))
    return out


@dataclass
class Page:
    """1つの表の中身(の一部)。"""

    table: str
    label: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    editable: bool = False
    why: str = ""
    note: str = ""
    error: str = ""
    # 取り込み元に無い表
    missing: bool = False
    # いま並び替えている列。空なら既定(rowid、取り込み順)
    sort: str = ""
    sort_dir: str = "asc"

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "label": self.label,
                "columns": self.columns, "rows": self.rows,
                "total": self.total, "shown": len(self.rows),
                "editable": self.editable, "why": self.why,
                "note": self.note, "error": self.error,
                "missing": self.missing,
                "row_key": ROW_KEY,
                "sort": self.sort, "sort_dir": self.sort_dir}


def page(path: Optional[Path], table: str, *, query: str = "",
         sort: str = "", sort_dir: str = "asc",
         limit: int = ROW_LIMIT) -> Page:
    """表の中身を読む。**取り込み元から直に読む。**

    手元の写しではなく元を読むのは、直したあと「本当に入ったか」を
    ここで確かめられるようにするためです。写しを見せると、書き込みが
    失敗していても画面上は直ったように見えます。

    `sort` は列名(見出しクリック)。**取り込み元に実在する列だけ**を
    許す ── 列名をそのまま `ORDER BY` に組み込むので、絞り込み
    (`_filter`)と同じく許可リストで確かめてから使う。無効な指定は
    黙って既定(`rowid`)に戻す(拒否すると押しただけで断られる画面になる)。
    """
    managed = BY_TABLE.get(table)
    view = Page(table=table,
                label=table,
                editable=managed is not None,
                why=view_only_why(table))
    if path is None:
        view.error = (f"{source_label(table)}が見つかりません。"
                      f"{config.master_db_dir()} を確かめてください。")
        return view

    names = source_db.columns(path, table)
    if not names:
        if managed is not None:
            # **断りではなく、次にできること。** ここで「ありません」と
            # だけ言うと、直しようが無い故障に見える
            view.missing = True
            view.editable = False
            view.why = _missing_why(table)
            return view
        view.error = f"{table} は取り込み元にありません。"
        return view
    view.columns = names

    where, params = _filter(names, query)
    order, sort_col = _order(names, sort, sort_dir)
    view.sort = sort_col
    view.sort_dir = "desc" if sort_dir == "desc" else "asc"
    quoted = source_db.quote_identifier(table)
    try:
        count = source_db.read_query(
            path, f"SELECT COUNT(*) AS n FROM {quoted}{where}", params)
        view.total = int(count[0]["n"]) if count else 0
        view.rows = source_db.read_query(
            path,
            f'SELECT rowid AS "{ROW_KEY}", * FROM {quoted}{where}'
            f" {order} LIMIT ?", [*params, max(1, limit)])
    except source_db.SourceError as exc:
        view.rows = []
        view.error = str(exc)
        return view

    hidden = view.total - len(view.rows)
    if hidden > 0:
        # **黙って切らない。** 絞り込みの手があることまで言う
        view.note = (f"{view.total}件のうち {len(view.rows)}件を出しています"
                     f"(ほか {hidden}件)。絞り込むと目当ての行が出ます。")
    return view


def _order(names: list[str], sort: str, sort_dir: str) -> tuple[str, str]:
    """見出しクリックの並び替え。

    `sort` が実在の列でなければ、押していないのと同じ(`rowid` の
    既定順)へ静かに戻す ── マスタの列は取り込み元の都合で増減するので、
    もう無い列を指した並び替えを断ると「さっきまで押せたのに」が起きる。

    【統合版】**数字は数の大きさで並べる**(`common/sql_sort.py`)。取り込み元は数字も
    文字で持っているので、そのままだと 800 が 1350 の後ろへ来ていた(通し試験で見つけた)。
    """
    if sort and sort in names:
        direction = "DESC" if sort_dir == "desc" else "ASC"
        quoted = source_db.quote_identifier(sort)
        # 同値が並ぶと表示順がページごとに揺れるので、rowidで確定させる
        return f"ORDER BY {sql_sort.order_terms(quoted, direction)}, rowid ASC", sort
    return "ORDER BY rowid ASC", ""


def _filter(names: list[str], query: str) -> tuple[str, list[Any]]:
    """絞り込みの条件。**どの列でもいい**ので、全部の列を見る。

    どの列に何が入っているかを覚えていなくても引けるようにする。
    """
    text = (query or "").strip()
    if not text:
        return "", []
    conds = " OR ".join(
        f"CAST({source_db.quote_identifier(n)} AS TEXT) LIKE ?" for n in names)
    return f" WHERE ({conds})", [f"%{text}%"] * len(names)


# ==================================================================
# 直す
# ==================================================================
@dataclass
class Result:
    ok: bool = True
    message: str = ""
    reason: str = ""


def save_row(conn: sqlite3.Connection, table: str, row_key: Any,
             values: dict[str, Any], *,
             path: Optional[Path] = None) -> Result:
    """1行を書き換える。"""
    path, refused = _ready(conn, table, path)
    if refused:
        return refused

    clean, problem = _clean(conn, table, values, filling=False,
                            present=source_db.columns(path, table))
    if problem:
        return Result(False, problem, REFUSE_BAD_VALUE)
    if not clean:
        return Result(False, "変える値がありません。", REFUSE_BAD_VALUE)

    try:
        with source_db.connect(path) as src:
            changed = src.update(table, clean, {"rowid": row_key})
    except source_db.SourceError as exc:
        return _write_failed(table, exc)
    if not changed:
        # 一覧を出したあとに誰かが消した。押した人には見えていない事実
        return Result(False, "その行はもうありません。一覧を出し直してください。",
                      REFUSE_NO_ROW)

    log.info("マスタを直しました: %s rowid=%s %s", table, row_key, sorted(clean))
    return Result(True, f"{_label(table)}の1行を直しました{_follow(conn, path, table)}")


def add_row(conn: sqlite3.Connection, table: str, values: dict[str, Any], *,
            path: Optional[Path] = None) -> Result:
    """1行足す。"""
    path, refused = _ready(conn, table, path)
    if refused:
        return refused

    clean, problem = _clean(conn, table, values, filling=True,
                            present=source_db.columns(path, table))
    if problem:
        return Result(False, problem, REFUSE_BAD_VALUE)
    if not clean:
        return Result(False, "入れる値がありません。", REFUSE_BAD_VALUE)

    try:
        with source_db.connect(path) as src:
            src.insert(table, clean)
    except source_db.SourceError as exc:
        return _write_failed(table, exc)

    log.info("マスタに足しました: %s %s", table, sorted(clean))
    return Result(True, f"{_label(table)}に1行足しました{_follow(conn, path, table)}")


def delete_row(conn: sqlite3.Connection, table: str, row_key: Any, *,
               path: Optional[Path] = None) -> Result:
    """1行消す。"""
    path, refused = _ready(conn, table, path)
    if refused:
        return refused

    try:
        with source_db.connect(path) as src:
            changed = src.delete(table, {"rowid": row_key})
    except source_db.SourceError as exc:
        return _write_failed(table, exc)
    if not changed:
        return Result(False, "その行はもうありません。一覧を出し直してください。",
                      REFUSE_NO_ROW)

    log.info("マスタから消しました: %s rowid=%s", table, row_key)
    return Result(True, f"{_label(table)}の1行を消しました{_follow(conn, path, table)}")


def _ready(conn: sqlite3.Connection, table: str, path: Optional[Path],
           ) -> tuple[Optional[Path], Optional[Result]]:
    """書く前に通す関門。通れば `(ファイル, None)`、通らなければ理由。

    順番に意味がある ── **許されるか → 表 → 届くか → 表が本当にあるか**。
    届かないことを先に言うと、許されていない人に「共有が落ちている」と
    読ませてしまう。

    【表が本当にあるかを、ここでも確かめる理由】
    `MANAGED`(=`BY_TABLE`)に載っているだけでは、**取り込み元に実在する
    とは限りません**。ここを確かめずに書こうとすると、`source_db.columns`
    が空を返し、`_clean` がどの値も「取り込み元に無い列」として黙って
    弾きます。結果、押した人には「入れる値がありません」としか見えず、
    **表が無いこと**という本当の理由に辿り着けません。
    画面側にも同じ防御を置いていますが、直接APIを叩かれた場合や、
    二重にタブを開いていた場合のために、ここでも確かめます。
    """
    allowed, why = can_edit(conn, table)
    if not allowed:
        return None, Result(False, why, REFUSE_NOT_ALLOWED)
    if table not in BY_TABLE:
        return None, Result(False, view_only_why(table) or "直せない表です。",
                            REFUSE_NOT_EDITABLE)
    found = path or source_for(table)
    if found is None:
        return None, Result(False,
                            f"{source_label(table)}が見つかりません。"
                            f"{source_dir(table)} を確かめてください。",
                            REFUSE_NO_SOURCE)
    present = source_db.columns(found, table)
    if not present:
        return None, Result(False, _missing_why(table), REFUSE_NOT_CREATABLE)
    mismatch = column_mismatch_why(table, present)
    if mismatch:
        return None, Result(False, mismatch, REFUSE_NOT_CREATABLE)
    return found, None


def _write_failed(table: str, exc: Exception) -> Result:
    log.warning("%s へ書けませんでした: %s", table, exc)
    return Result(False, f"取り込み元へ書けませんでした: {exc}",
                  REFUSE_WRITE_FAILED)


def _label(table: str) -> str:
    return table


def _clean(conn: sqlite3.Connection, table: str, values: dict[str, Any],
           *, filling: bool,
           present: Optional[Iterable[str]] = None) -> tuple[dict[str, Any], str]:
    """画面から来た値を、取り込み元へ入れられる形にする。

    `filling` が真なら新しい行なので、送られてこなかった必須の列も
    見る。偽なら書き換えなので、**送られてきた列だけ**を触る
    (送っていない列を消さないため)。
    """
    out: dict[str, Any] = {}
    for column in columns(conn, table, present):
        if column.name not in values and not filling:
            continue
        raw = str(values.get(column.name, "")).strip()
        if raw == "":
            if column.stamp:
                out[column.name] = db.now_db_string()
                continue
            if column.required:
                return {}, f"「{column.name}」は空にできません。"
            # 空欄は「無し」。取り込みのときに手元の既定値で埋まる
            out[column.name] = None
            continue
        if column.kind == "int":
            try:
                out[column.name] = int(float(raw))
            except ValueError:
                return {}, f"「{column.name}」は{KIND_LABEL['int']}で入れてください。"
        elif column.kind == "real":
            try:
                out[column.name] = float(raw)
            except ValueError:
                return {}, f"「{column.name}」は{KIND_LABEL['real']}で入れてください。"
        else:
            out[column.name] = raw
    return out, ""


def _follow(conn: sqlite3.Connection, path: Path, table: str) -> str:
    """書いた表だけを取り込み直して、手元を追いつかせる。

    ここを飛ばすと、取り込み元は直っているのに画面の動きは変わらない
    ── **直したのに効かない**が一番たちが悪い。
    """
    try:
        result = data_sync.reimport_table(conn, table, path=path)
    except sqlite3.Error as exc:                 # pragma: no cover - 上流で拾う
        log.warning("%s を取り込み直せません: %s", table, exc)
        return "。ただし手元に取り込めなかったので「いま取り込む」を押してください"

    if not result.ok:
        return "。ただし手元に取り込めませんでした: " + result.error

    notes = [f"手元も{result.rows}件に更新しました"]
    if result.filled:
        # 取り込みが言いたいこと。**黙って捨てない**
        notes.append("なお取り込み元に無い列がありました("
                     + ", ".join(result.filled) + ")")
    if result.duplicates:
        notes.append("同じ番号の行が2つ以上あったので先の行を使いました("
                     + ", ".join(result.duplicates[:5]) + ")")
    # **開いたままの計算結果は、もう古い。** 計算は押した時点のマスタで
    # 出ているので、直したあとに画面へ残っている台数やリプラ本数は
    # 新しい値ではない。黙っていると、古い結果のままチェックリストへ
    # 積まれる
    notes.append("資材計算をやり直すと新しい値になります")
    return "。" + "、".join(notes) + "。"
