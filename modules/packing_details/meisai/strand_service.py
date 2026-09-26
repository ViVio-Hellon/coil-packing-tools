"""条番号と積み上げの業務ロジック (VBA `frmCoilPacking` のギミックB/C)

画面の都合(ラベルの色・位置・イベント)を落として、**判定と計算だけ**を
ここに置く。VBA では UserForm のコントロールが状態を持っていたが、
Web版はブラウザを閉じてもプロセスが終わるので、状態はDBに書いて
そこから組み立て直す(`history_repo`)。

    VBA                              -> Python
    ---------------------------------------------------------------
    cmdShowJouTai_Click の配分計算     -> distribute()
    BuildGimmickB                      -> Strand.keys_for_jou() / Board
    GimmickB_Click(通常)                -> Board.stack()
    GimmickC_Click                      -> Board.unstack()
    GimmickB_Click(廃棄モード)           -> Board.toggle_discard()
    CheckAllFilled                      -> Board.is_complete
    cmdReverse_Click                    -> Board.reverse()
    ReapplyHaiki                         -> Board.reapply_discards()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from . import config
from .logging_utils import get_logger

log = get_logger("strand_service")

# 断りの種類。**文言から推し量らない**
REFUSE_NO_SLOTS = "no_slots"        # 積み条数が未入力
REFUSE_FULL = "full"                # 積み上げの上限に達した
REFUSE_USED = "used"                # すでに積み上げ済み
REFUSE_DISCARDED = "discarded"      # 廃棄済みなので積めない
REFUSE_UNKNOWN_KEY = "unknown_key"  # そんな条番号は無い
REFUSE_EMPTY_SLOT = "empty_slot"    # 空のスロットを解除しようとした


class RefusedError(ValueError):
    """操作を断った。`reason` で理由を区別する。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# ==================================================================
# RULE-03: 条数配分
# ==================================================================
def distribute(total: int, jou_su: int) -> list[int]:
    """前工程実績数を丈へ配る (VBA `cmdShowJouTai_Click`)。

        base = total \\ jou_su        ' 整数除算(切り捨て)
        rem  = total Mod jou_su
        For i = jou_su To 1 Step -1
            条数(i) = base
            If rem > 0 Then 条数(i) = 条数(i) + 1 : rem = rem - 1
        Next

    **余りは丈番号の大きいほうから配る。** 昇順で配ると結果が変わる
    ので、向きを変えてはいけない。

        30本 / 2丈 -> [15, 15]
        31本 / 2丈 -> [15, 16]   ← 丈2に余りが付く
        5本 / 3丈  -> [1, 2, 2]

    整数除算に `//` を使うのも要点。`/` だと float になり、条数が
    `7.0` のような値で出てきて条番号キーが `1-7.0` になる。
    """
    if jou_su < 1:
        raise ValueError(f"丈数は1以上です: {jou_su}")
    if total < 0:
        raise ValueError(f"前工程実績数が負です: {total}")

    base, rem = divmod(total, jou_su)
    out = [base] * jou_su
    for i in range(jou_su, 0, -1):
        if rem <= 0:
            break
        out[i - 1] += 1
        rem -= 1
    return out


# ==================================================================
# RULE-04: 条番号
# ==================================================================
def key_of(jou: int, suji: int) -> str:
    """条番号キー。VBA は `i & "-" & k` で作っている。"""
    return f"{jou}-{suji}"


def parse_key(key: str) -> tuple[int, int]:
    """条番号キーを `(丈, 条)` に戻す。読めなければ ValueError。"""
    jou_text, _, suji_text = (key or "").partition("-")
    if not jou_text or not suji_text:
        raise ValueError(f"条番号キーとして読めません: {key!r}")
    return int(jou_text), int(suji_text)


def keys_for_jou(jou: int, count: int) -> list[str]:
    """丈1本ぶんの条番号を**描画順(左から右)**で返す。

    VBA `BuildGimmickB` は `For k = mStrandsPerJou(i) To 1 Step -1` で
    左から置いていくので、**左端が最大の条番号**になる。

        丈2・15条 -> ["2-15", "2-14", ..., "2-1"]

    0条の丈は空を返す。VBA も `If jouCount < 1 Then GoTo ContinueJou`
    で飛ばしている(`ReDim positions(1 To 0)` のエラーを避けるため)。
    """
    if count < 1:
        return []
    return [key_of(jou, k) for k in range(count, 0, -1)]


# ==================================================================
# 盤面
# ==================================================================
@dataclass
class Board:
    """条番号グリッドと積み上げスロットの状態。

    VBA では `mHandlersB`(条番号ラベル)と `mHandlersC`(スロット)の
    2つの Collection が持っていた状態を1つにまとめたもの。
    """

    lot_no: str = ""
    zen_kotei: int = 0                          # 前工程実績数
    jou_su: int = 0                             # 丈数
    strands: list[int] = field(default_factory=list)      # 丈ごとの条数
    weights: list[int] = field(default_factory=list)      # 丈ごとの重量(kg)
    stack_max: int = 0                          # 積み条数
    # スロット。長さは `stack_max`。空きは None。**添字0がスロット1**
    slots: list[Optional[str]] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)    # 廃棄した条番号
    # 使用済みだが、どのスロットにも入っていない条番号。
    #
    # **復元したときだけ現れる。** VBA `RestoreSnapshot` はギミックBの
    # `IsUsed` を立て直すが、積み上げスロット(ギミックC)は作り直さない
    # (積み条数は入力し直す)。そのため「灰色だがスロットには無い」
    # コイルが残り、押しても反応しない。次の `txtTsumiJouSu_Change` で
    # `ResetGimmickBUsed` が回っても、スロットが空なので**戻らない**。
    # 解除されるのは条番号の再表示か副番履歴クリアのときだけ。
    restored_used: list[str] = field(default_factory=list)
    # 丈ごとの表示の向き。`cmdReverse_Click` は**表示位置だけ**を
    # 入れ替えてキーは変えないので、盤面としては向きだけを覚える
    reversed_view: bool = False

    # --- 見え方 ---------------------------------------------------
    @property
    def used(self) -> set[str]:
        """使用済みの条番号 (VBA `IsUsed = True`)。

        スロットに入っているものと、復元で立て直されたものの両方。
        VBA も `IsUsed` という1つのフラグで両方を表していて、
        どちらも**押しても反応しない**。
        """
        return {k for k in self.slots if k} | set(self.restored_used)

    @property
    def is_complete(self) -> bool:
        """全スロットが埋まったか (VBA `CheckAllFilled`)。

        **埋まるまで出力させない。** VBA も `cmdKettei.Enabled` を
        ここで切り替えている。
        """
        return bool(self.slots) and all(self.slots)

    @property
    def all_keys(self) -> list[str]:
        """全条番号を丈順・描画順に並べたもの。"""
        out: list[str] = []
        for index, count in enumerate(self.strands, start=1):
            out.extend(keys_for_jou(index, count))
        return out

    def rows(self) -> list[list[str]]:
        """画面に出す行(丈ごと)。`reversed_view` なら左右を入れ替える。"""
        out = []
        for index, count in enumerate(self.strands, start=1):
            keys = keys_for_jou(index, count)
            out.append(list(reversed(keys)) if self.reversed_view else keys)
        return out

    def state_of(self, key: str) -> str:
        """その条番号がいまどう見えるか。画面の色分けに使う。"""
        if key in self.discarded:
            return "discarded"
        if key in self.used:
            return "used"
        return "available"

    def weight_of(self, key: str) -> int:
        """その条番号の重量 (VBA `WriteData` の `weights(jouPart)`)。

        重量は**丈単位**。同じ丈のコイルはすべて同じ値になる。
        """
        jou, _ = parse_key(key)
        if 1 <= jou <= len(self.weights):
            return self.weights[jou - 1]
        return 0

    def knows(self, key: str) -> bool:
        """そんな条番号があるか。"""
        try:
            jou, suji = parse_key(key)
        except ValueError:
            return False
        return 1 <= jou <= len(self.strands) and 1 <= suji <= self.strands[jou - 1]

    # --- 組み立て -------------------------------------------------
    def build_strands(self) -> None:
        """条数を配り直す (VBA `cmdShowJouTai_Click` の後半)。

        **使用済みは全部リセットされる。** VBA も `BuildGimmickB` が
        ラベルを作り直すので `IsUsed` は消える。廃棄だけは
        `ReapplyHaiki` で塗り直す。
        """
        self.strands = distribute(self.zen_kotei, self.jou_su)
        self.restored_used = []          # ラベルを作り直す＝IsUsed も消える
        self.clear_slots()
        self.reapply_discards()
        log.debug("build_strands: 合計=%s 配分=%s", self.zen_kotei, self.strands)

    def set_stack_max(self, value: int) -> None:
        """積み条数を決めてスロットを作り直す (VBA `txtTsumiJouSu_Change`)。

        VBA は `ResetGimmickBUsed` で**いま割り当たっているものだけ**を
        戻してから作り直す。スロットを空にすれば同じことになる。
        """
        if not 1 <= value <= config.STACK_LIMIT:
            raise ValueError(
                f"積み条数は1〜{config.STACK_LIMIT}です: {value}")
        self.stack_max = value
        self.clear_slots()

    def clear_slots(self) -> None:
        """スロットを空にする (VBA `ClearGimmickC` 相当)。"""
        self.slots = [None] * self.stack_max

    def reapply_discards(self) -> None:
        """廃棄を塗り直す (VBA `ReapplyHaiki`)。

        条数が変わって**存在しなくなった条番号は落とす。** VBA も
        ハンドラが取れないものを新しい Collection に入れ直さない。
        """
        self.discarded = [k for k in self.discarded if self.knows(k)]

    # --- 操作 -----------------------------------------------------
    def stack(self, key: str) -> int:
        """条番号を積む (VBA `GimmickB_Click` の通常モード)。

        **最も若い空きスロットに入る。** 戻り値はそのスロット番号
        (1始まり)。スロット1が最下段。
        """
        if not self.knows(key):
            raise RefusedError(REFUSE_UNKNOWN_KEY, f"条番号 {key} がありません。")
        if not self.slots:
            raise RefusedError(REFUSE_NO_SLOTS, "積み条数が入力されていません。")
        if key in self.discarded:
            raise RefusedError(REFUSE_DISCARDED, f"{key} は廃棄済みです。")
        if key in self.used:
            raise RefusedError(REFUSE_USED, f"{key} はすでに積み上げ済みです。")

        for index, slot in enumerate(self.slots):
            if slot is None:
                self.slots[index] = key
                log.debug("stack: %s -> スロット%s", key, index + 1)
                return index + 1
        raise RefusedError(REFUSE_FULL, "積み条数の上限に達しています。")

    def unstack(self, slot_no: int) -> str:
        """スロットを解除する (VBA `GimmickC_Click`)。戻り値は外した条番号。

        **詰め直さない。** VBA もそのスロットだけを空にするので、
        間が空いたまま残る(次に積むとそこへ入る)。
        """
        if not 1 <= slot_no <= len(self.slots):
            raise RefusedError(REFUSE_EMPTY_SLOT,
                               f"スロット{slot_no}がありません。")
        key = self.slots[slot_no - 1]
        if key is None:
            raise RefusedError(REFUSE_EMPTY_SLOT,
                               f"スロット{slot_no}は空です。")
        self.slots[slot_no - 1] = None
        log.debug("unstack: スロット%s の %s を解除", slot_no, key)
        return key

    def toggle_discard(self, key: str) -> bool:
        """廃棄を付け外しする (VBA `GimmickB_Click` の廃棄モード)。

        戻り値は**この操作のあと廃棄になっているか**。

        VBA の分岐をそのまま移す。
          - すでに廃棄 → 解除する
          - 未使用      → 廃棄にする
          - 使用済み    → **何もしない**(使用済みは廃棄不可)
        """
        if not self.knows(key):
            raise RefusedError(REFUSE_UNKNOWN_KEY, f"条番号 {key} がありません。")
        if key in self.discarded:
            self.discarded.remove(key)
            log.debug("toggle_discard: 廃棄解除 %s", key)
            return False
        if key in self.used:
            raise RefusedError(REFUSE_USED,
                               f"{key} は積み上げ済みなので廃棄にできません。")
        self.discarded.append(key)
        log.debug("toggle_discard: 廃棄登録 %s", key)
        return True

    def reverse(self) -> None:
        """表示の向きを入れ替える (VBA `cmdReverse_Click`)。

        **キーは変わらない。** VBA はラベルの `Left` だけを入れ替えて
        いて、条番号も積み上げ済みの状態もそのまま。現物の並び向きと
        画面の向きを合わせるための表示補助。
        """
        self.reversed_view = not self.reversed_view

    def stacked_keys(self) -> list[str]:
        """積み上げ順の条番号 (VBA `slotKeys`)。空きは含めない。

        出力の主役。この並びがそのまま紙の行順になる。
        """
        return [k for k in self.slots if k]

    def release_all_used(self) -> None:
        """使用済みを全部ほどく (VBA `ResetAllGimmickBUsed`)。

        副番履歴クリアのときだけ呼ぶ。**条番号のキャンバスは残す** ──
        VBA も `cmdClearDup_Click` で「条番号・積み上げをリセット
        (キャンバスは残す)」とコメントしている。廃棄も残る。
        """
        self.restored_used = []
        self.clear_slots()
