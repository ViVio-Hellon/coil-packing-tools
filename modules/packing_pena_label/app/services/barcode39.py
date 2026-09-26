# -*- coding: utf-8 -*-
"""Code 39 バーコードを SVG で描く。

Excel の台紙は **バーコードフォント**（`*` で囲んだ文字列を字形で表す）に
依存していた。ブラウザーでは同じフォントが入っている保証がないため、
移植版は **バーを自分で描く**。これでフォントの導入・字形差に左右されなくなる。

VBA が作っていた文字列
    型番   : ``*BJB7606500QR*``
    検査番号: ``*W111111-0101 10*``
``*`` は Code 39 のスタート/ストップ記号なので、
描画時は前後の ``*`` を取り除いて中身だけを符号化し、
**バーの先頭と末尾に ``*`` を必ず付ける**（``_elements``）。

バーの下に添える文字も ``*BJB7606500QR*`` のように ``*`` で挟んで出す
（VBA が作っていた文字列そのまま。出荷先の求めとして現場から指定があった）。
読取機が返す値は ``*`` を含まない中身だけで、Excel のバーコードフォントと同じ。
"""

from __future__ import annotations

from typing import List, Optional

#: Code 39 の符号表。b=バー, s=スペース、n=細, w=太
#: 各文字は 5 バー + 4 スペース = 9 エレメント（うち太が 3 つ）
_PATTERNS = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn",
    "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw",
    "8": "wnnwnnwnn", "9": "nnwwnnwnn",
    "A": "wnnnnwnnw", "B": "nnwnnwnnw", "C": "wnwnnwnnn", "D": "nnnnwwnnw",
    "E": "wnnnwwnnn", "F": "nnwnwwnnn", "G": "nnnnnwwnw", "H": "wnnnnwwnn",
    "I": "nnwnnwwnn", "J": "nnnnwwwnn", "K": "wnnnnnnww", "L": "nnwnnnnww",
    "M": "wnwnnnnwn", "N": "nnnnwnnww", "O": "wnnnwnnwn", "P": "nnwnwnnwn",
    "Q": "nnnnnnwww", "R": "wnnnnnwwn", "S": "nnwnnnwwn", "T": "nnnnwnwwn",
    "U": "wwnnnnnnw", "V": "nwwnnnnnw", "W": "wwwnnnnnn", "X": "nwnnwnnnw",
    "Y": "wwnnwnnnn", "Z": "nwwnwnnnn",
    "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn",
    "$": "nwnwnwnnn", "/": "nwnwnnnwn", "+": "nwnnnwnwn", "%": "nnnwnwnwn",
    "*": "nwnnwnwnn",                      # スタート / ストップ
}

#: 太バーと細バーの比（Code 39 の標準は 2.0〜3.0）
DEFAULT_WIDE_RATIO = 2.5
#: 文字間ギャップ（細バー何本ぶんか）
INTER_CHAR_GAP = 1.0

START_STOP = "*"


class Code39Error(ValueError):
    """Code 39 で表せない文字が含まれている。"""


def normalize(text: str) -> str:
    """VBA が作った ``*...*`` から中身を取り出し、大文字へ。"""
    s = (text or "").strip()
    if len(s) >= 2 and s.startswith(START_STOP) and s.endswith(START_STOP):
        s = s[1:-1]
    return s.upper()


def caption(text: str) -> str:
    """バーの下に添える文字（``*`` で挟む）。"""
    data = normalize(text)
    return (START_STOP + data + START_STOP) if data else ""


def encodable(text: str) -> bool:
    return all(ch in _PATTERNS for ch in normalize(text))


def unsupported_chars(text: str) -> List[str]:
    return sorted({ch for ch in normalize(text) if ch not in _PATTERNS})


def _elements(data: str, wide_ratio: float) -> List[tuple]:
    """``(是バーか, 幅(細バー単位))`` の並びを返す。"""
    out: List[tuple] = []
    chars = START_STOP + data + START_STOP
    for i, ch in enumerate(chars):
        pat = _PATTERNS[ch]
        for j, kind in enumerate(pat):
            is_bar = (j % 2 == 0)
            out.append((is_bar, wide_ratio if kind == "w" else 1.0))
        if i != len(chars) - 1:
            out.append((False, INTER_CHAR_GAP))     # 文字間ギャップ
    return out


def modules(text: str, wide_ratio: float = DEFAULT_WIDE_RATIO) -> float:
    """細バー何本ぶんの幅になるか（描画前に幅を見積もるため）。"""
    data = normalize(text)
    if not encodable(data):
        raise Code39Error("Code 39 で表せない文字: %s"
                          % "".join(unsupported_chars(text)))
    return sum(w for _, w in _elements(data, wide_ratio))


#: 静止帯の下限（0.1 インチ）。Code 39 は
#: 「細バーの 10 倍 以上、かつ 2.54mm 以上」を要求する
MIN_QUIET_MM = 2.54
#: 静止帯の倍率（細バーの何本ぶんか）
QUIET_RATIO = 10.0


def quiet_zone_mm(width_mm: float, total_modules: float) -> float:
    """枠幅に対して規格を満たす静止帯を求める。

    静止帯を広げると細バーが細くなり、細バーが細くなると必要な静止帯も
    狭くなる ―― 循環するので解いておく::

        quiet = 10 * (W - 2*quiet) / modules
        quiet * (modules + 20) = 10 * W
        quiet = 10*W / (modules + 20)

    これが 2.54mm に満たない場合は 2.54mm を使う。
    """
    if total_modules <= 0 or width_mm <= 0:
        return MIN_QUIET_MM
    q = QUIET_RATIO * width_mm / (total_modules + QUIET_RATIO * 2)
    q = max(q, MIN_QUIET_MM)
    # 枠が極端に狭いときに静止帯だけで埋め尽くさない
    return min(q, width_mm * 0.4)


def svg(text: str, width_mm: float, height_mm: float,
        wide_ratio: float = DEFAULT_WIDE_RATIO,
        quiet_mm: Optional[float] = None,
        show_text: bool = True,
        text_mm: float = 2.2) -> str:
    """Code 39 を SVG で返す。

    ``width_mm`` / ``height_mm`` は **ラベル上の実寸**。
    左右に静止帯（クワイエットゾーン）を取り、残りへバーを収める。
    ``show_text`` で下に人が読める文字列を添える（VBA の台紙と同じ見え方）。

    ``quiet_mm`` を省くと規格から計算する（``quiet_zone_mm``）。
    静止帯が足りないのは読み取れない原因として最も多いものの一つ。
    """
    data = normalize(text)
    if not data:
        return ""
    if not encodable(data):
        raise Code39Error("Code 39 で表せない文字: %s"
                          % "".join(unsupported_chars(text)))

    elems = _elements(data, wide_ratio)
    total = sum(w for _, w in elems)

    if quiet_mm is None:
        quiet_mm = quiet_zone_mm(width_mm, total)
    usable = max(width_mm - quiet_mm * 2, 0.1)
    unit = usable / total                       # 細バー 1 本の mm

    bar_h = height_mm - (text_mm + 0.6 if show_text else 0)
    bar_h = max(bar_h, 1.0)

    parts = [
        '<svg class="bc39" viewBox="0 0 %.4f %.4f" width="%.4fmm" height="%.4fmm" '
        'preserveAspectRatio="none" shape-rendering="crispEdges" '
        'role="img" aria-label="%s">' % (width_mm, height_mm, width_mm, height_mm,
                                         _esc(data)),
        '<rect x="0" y="0" width="%.4f" height="%.4f" fill="#fff"/>'
        % (width_mm, height_mm),
    ]
    x = quiet_mm
    for is_bar, w in elems:
        bw = w * unit
        if is_bar:
            parts.append('<rect x="%.4f" y="0" width="%.4f" height="%.4f" '
                         'fill="#000"/>' % (x, bw, bar_h))
        x += bw

    if show_text:
        parts.append(
            '<text x="%.4f" y="%.4f" font-size="%.3f" text-anchor="middle" '
            'font-family="Consolas, monospace" fill="#000">%s</text>'
            % (width_mm / 2, height_mm - 0.4, text_mm, _esc(caption(data))))
    parts.append("</svg>")
    return "".join(parts)


#: 横だけ引き伸ばす倍率の上限。太細の比は変わらないので読取には影響しないが、
#: 極端に伸ばすと見た目が崩れるため頭打ちにする
MAX_FONT_STRETCH = 4.0


def font_html(text: str, width_mm: float, height_mm: float,
              family: str, show_text: bool = True,
              text_mm: float = 2.2, font_path: str = "") -> str:
    """バーコードフォントで出す（現行 Excel と同じ字形）。

    フォントが入っていないと **ただの文字列** が印刷されてしまうため、
    既定は SVG 描画。実績を優先したい場合だけこちらを使う。

    ``font_path`` を渡すと、そのフォントの送り幅を実際に読んで
    **枠幅いっぱいに横へ引き伸ばす**。バーコードフォントは字形の縦横比が
    固定なので、文字サイズだけで合わせると枠の半分ほどしか使えず、
    細バーが 0.2mm を切って読み取りにくくなる。
    横方向だけの伸縮は**太いバーと細いバーの比を変えない**ので、
    読取機から見た符号は同じまま、バーを太くできる。
    """
    data = normalize(text)
    if not data:
        return ""
    bar_h = max(height_mm - (text_mm + 0.6 if show_text else 0), 1.0)
    size = bar_h * 0.92

    style = ('height:%.3fmm;font-family:%s;font-size:%.3fmm;line-height:%.3fmm;'
             % (bar_h, _css_family(family), size, bar_h))

    # 枠幅いっぱいへ伸ばす（静止帯は残す）
    stretch = 0.0
    if font_path:
        em = text_width_em(font_path, START_STOP + data + START_STOP)
        if em and em > 0:
            natural = em * size
            try:
                quiet = quiet_zone_mm(width_mm, modules(data))
            except Code39Error:
                quiet = MIN_QUIET_MM
            # 送り幅の見積もりは実際の描画と 1% ほどずれる（ヒンティング等）。
            # 静止帯を削る側へずれないよう、少し内側を狙う。
            target = max((width_mm - quiet * 2) * 0.97, 0.1)
            if natural > 0:
                stretch = min(target / natural, MAX_FONT_STRETCH)
    if stretch and abs(stretch - 1.0) > 0.01:
        style += ('display:inline-block;transform:scaleX(%.4f);'
                  'transform-origin:center center;' % stretch)

    out = [
        '<div class="bcwrap" style="width:100%%;text-align:center;">'
        '<div class="bcfont" style="%s">%s</div></div>'
        % (_esc(style), _esc(START_STOP + data + START_STOP)),
    ]
    if show_text:
        out.append('<div class="bctext" style="font-size:%.3fmm;">%s</div>'
                   % (text_mm, _esc(caption(data))))
    return "".join(out)


def css_family_name(family: str) -> str:
    """フォント名を CSS の二重引用符の中へ入れられる形にする。

    ``"`` と ``\\`` は文字列を壊す。``<`` ``>`` は ``</style>`` で
    style 要素そのものを終わらせてしまう。改行も CSS 文字列に置けない。
    **``'``（``K's BarCodeFont Code39``）は落とさない** ― 落とすと
    実フォントの名前と一致しなくなる。

    @font-face 側と利用側の**両方でこれを通す**こと。
    片側だけ HTML エスケープすると（``K&#x27;s``）名前が食い違い、
    style 要素の中では実体参照が戻らないためフォントが当たらない。
    """
    f = family or ""
    for bad in ('"', "\\", "<", ">", "\r", "\n", "\t"):
        f = f.replace(bad, "")
    return f.strip()


def css_url_path(name: str) -> str:
    """url("...") の中へ入れられる形にする（ファイル名用）。"""
    f = name or ""
    for bad in ('"', "\\", "<", ">", "(", ")", "\r", "\n", "\t", " "):
        f = f.replace(bad, "")
    return f.strip()


def _css_family(family: str) -> str:
    """font-family をCSSへ安全に埋める（引用符で囲み直す）。"""
    f = css_family_name(family)
    return '"%s", monospace' % f if f else "monospace"


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# ------------------------------------------------------- フォントの見分け
#: TrueType の name テーブル nameID 1 = フォントファミリー名
_NAME_ID_FAMILY = 1


def font_family_of(path: str) -> str:
    """TTF/OTF から**ファミリー名**を読む。読めなければ空文字。

    ファイル名とフォント名は一致しない（``KsBarCodeCode39.ttf`` の中身は
    ``K's BarCodeFont Code39``）。設定のフォント名と実ファイルを
    突き合わせるために、**中身の名前**で判断する。
    """
    import struct

    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 12:
            return ""
        count = struct.unpack(">H", data[4:6])[0]
        off = length = 0
        for i in range(count):
            p = 12 + 16 * i
            if p + 16 > len(data):
                return ""
            if data[p:p + 4] == b"name":
                off, length = struct.unpack(">II", data[p + 8:p + 16])
                break
        if not length or off + 6 > len(data):
            return ""
        n_rec, str_off = struct.unpack(">HH", data[off + 2:off + 6])
        best = ""
        for i in range(n_rec):
            p = off + 6 + 12 * i
            if p + 12 > len(data):
                break
            pid, _eid, _lid, nid, ln, o2 = struct.unpack(">HHHHHH",
                                                         data[p:p + 12])
            if nid != _NAME_ID_FAMILY:
                continue
            s0 = off + str_off + o2
            raw = data[s0:s0 + ln]
            try:
                name = (raw.decode("utf-16-be") if pid == 3
                        else raw.decode("latin-1"))
            except (UnicodeDecodeError, ValueError):
                continue
            name = name.strip()
            # Windows(pid=3) の名前を優先する
            if name and (pid == 3 or not best):
                best = name
                if pid == 3:
                    break
        return best
    except (OSError, struct.error, ValueError):
        return ""


def list_fonts(dir_path: str) -> List[tuple]:
    """フォント置き場を調べ ``[(ファイル名, ファミリー名), ...]`` を返す。"""
    import os

    out: List[tuple] = []
    try:
        names = sorted(os.listdir(dir_path))
    except OSError:
        return out
    for fn in names:
        if not fn.lower().endswith((".ttf", ".otf")):
            continue
        full = os.path.join(dir_path, fn)
        if os.path.isfile(full):
            out.append((fn, font_family_of(full)))
    return out


def pick_font_file(fonts: List[tuple], family: str) -> Optional[str]:
    """設定のフォント名に**中身が一致する**ファイルを選ぶ。

    一致しなければ None（呼び出し側で「未配置」として扱う）。
    ファイル名順の先頭を黙って使うと、名前と中身が食い違ったまま
    別のフォントで印刷してしまうため、それはしない。
    """
    want = _norm_family(family)
    if not want:
        return None
    for fn, fam in fonts:
        if _norm_family(fam) == want:
            return fn
    return None


def _norm_family(s: str) -> str:
    """フォント名の比較用。大小・空白・記号の揺れを吸収する。"""
    t = (s or "").lower()
    return "".join(ch for ch in t if ch.isalnum())


# ------------------------------------------------- フォントの送り幅
#: TTF を解析した結果の覚え書き（パス, 更新時刻, サイズ）で無効化する
_METRICS_CACHE: dict = {}


def _font_tables(data: bytes) -> dict:
    import struct

    n = struct.unpack(">H", data[4:6])[0]
    out = {}
    for i in range(n):
        p = 12 + 16 * i
        if p + 16 > len(data):
            break
        tag = data[p:p + 4].decode("latin-1")
        off, ln = struct.unpack(">II", data[p + 8:p + 16])
        out[tag] = (off, ln)
    return out


def _font_cmap(data: bytes, off: int) -> dict:
    """unicode -> グリフ番号。format 4 / 12 / 6 / 0 に対応。"""
    import struct

    n = struct.unpack(">H", data[off + 2:off + 4])[0]
    best = None
    for i in range(n):
        p = off + 4 + 8 * i
        if p + 8 > len(data):
            break
        pid, eid, sub = struct.unpack(">HHI", data[p:p + 8])
        score = {(3, 10): 5, (3, 1): 4, (0, 4): 3, (0, 3): 3,
                 (3, 0): 2, (1, 0): 1}.get((pid, eid), 0)
        if best is None or score > best[0]:
            best = (score, off + sub)
    if best is None:
        return {}

    so = best[1]
    if so + 2 > len(data):
        return {}
    fmt = struct.unpack(">H", data[so:so + 2])[0]
    m: dict = {}
    if fmt == 4:
        segx2 = struct.unpack(">H", data[so + 6:so + 8])[0]
        seg = segx2 // 2
        ends = struct.unpack(">%dH" % seg, data[so + 14:so + 14 + segx2])
        sp = so + 16 + segx2
        starts = struct.unpack(">%dH" % seg, data[sp:sp + segx2])
        dp = sp + segx2
        deltas = struct.unpack(">%dh" % seg, data[dp:dp + segx2])
        rp = dp + segx2
        ranges = struct.unpack(">%dH" % seg, data[rp:rp + segx2])
        for i in range(seg):
            for c in range(starts[i], min(ends[i], 0xFFFF) + 1):
                if ranges[i] == 0:
                    g = (c + deltas[i]) & 0xFFFF
                else:
                    gp = rp + i * 2 + ranges[i] + (c - starts[i]) * 2
                    if gp + 2 > len(data):
                        continue
                    g = struct.unpack(">H", data[gp:gp + 2])[0]
                    if g:
                        g = (g + deltas[i]) & 0xFFFF
                if g:
                    m[c] = g
    elif fmt == 12:
        ng = struct.unpack(">I", data[so + 12:so + 16])[0]
        for i in range(min(ng, 10000)):
            p = so + 16 + 12 * i
            if p + 12 > len(data):
                break
            st, en, gi = struct.unpack(">III", data[p:p + 12])
            for c in range(st, min(en, st + 0xFFFF) + 1):
                m[c] = gi + (c - st)
    elif fmt == 6:
        first, cnt = struct.unpack(">HH", data[so + 6:so + 10])
        gs = struct.unpack(">%dH" % cnt, data[so + 10:so + 10 + cnt * 2])
        for i, g in enumerate(gs):
            m[first + i] = g
    elif fmt == 0:
        for c in range(256):
            if so + 6 + c < len(data):
                m[c] = data[so + 6 + c]
    return m


def font_metrics(path: str) -> Optional[tuple]:
    """``(1em の単位数, cmap, 送り幅, 既定送り幅)``。読めなければ None。"""
    import os
    import struct

    try:
        st = os.stat(path)
        key = (path, st.st_mtime, st.st_size)
    except OSError:
        return None
    if key in _METRICS_CACHE:
        return _METRICS_CACHE[key]

    try:
        with open(path, "rb") as f:
            data = f.read()
        t = _font_tables(data)
        head, hhea, hmtx = t["head"][0], t["hhea"][0], t["hmtx"][0]
        upem = struct.unpack(">H", data[head + 18:head + 20])[0]
        nhm = struct.unpack(">H", data[hhea + 34:hhea + 36])[0]
        cm = _font_cmap(data, t["cmap"][0])
        adv = {}
        last = 0
        for gid in range(nhm):
            p = hmtx + 4 * gid
            if p + 2 > len(data):
                break
            last = struct.unpack(">H", data[p:p + 2])[0]
            adv[gid] = last
        if not upem or not cm:
            raise ValueError("metrics が読めない")
        got = (upem, cm, adv, last)
    except (OSError, KeyError, struct.error, ValueError, IndexError):
        got = None

    _METRICS_CACHE[key] = got
    return got


def text_width_em(path: str, text: str) -> Optional[float]:
    """フォントで ``text`` を出したときの幅（em）。読めなければ None。"""
    got = font_metrics(path)
    if not got:
        return None
    upem, cm, adv, last = got
    total = 0
    for ch in text:
        gid = cm.get(ord(ch))
        if gid is None:
            return None                 # 1 文字でも無いなら見積もらない
        total += adv.get(gid, last)
    return total / float(upem)
