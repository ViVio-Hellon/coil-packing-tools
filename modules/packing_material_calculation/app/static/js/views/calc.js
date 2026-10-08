/*
  資材計算の画面。

  サーバが返す**ビューモデル一式**をそのまま写す。差分は扱わない
  (どこかが古いまま残る、が起きない)。

  「どの規則で決まったか」は `flags` で来るので、対応する欄に印を付ける。
  色だけに頼らないよう、見出しにも印が付く(coil.css `.f--why`)。
*/
import { call, toast, $, $$, text, esc, watchUnsaved, markPending, readPending, ownPending } from '../core.js';

const FLAG_TO_FIELD = {
  weight: '#w-梱包重量',
  count: '#w-梱包枚数',
  height: '#w-高さ',
};

let lastView = null;
// チェックリストへ積んだときの入力と結果(積んだあと変えていなければ、閉じても困らない)。
// 面を移っても覚えておく(このタブの sessionStorage)
const ADDED_KEY = 'cpt.material.calc.added';
let addedKey = '';
ownPending('calc');
// バリ揃えの上下の本数を最後に計算・表示した値
let burrShown = null;

function viewKey(v) {
  return v ? JSON.stringify([v.input, v.result]) : '';
}

// ------------------------------------------------------------------
/** 積んでいない計算の名前(無ければ空)。ほかの面にいるあいだも訊けるよう覚えておく */
function calcPending() {
  const shown = (lastView && lastView.input && lastView.input.LOT) || '';
  return shown && lastView.result && lastView.result.台数 && viewKey(lastView) !== addedKey
    ? `計算した LOT ${shown}(チェックリストへ積んでいない)` : '';
}

function rememberAdded(key) {
  addedKey = key;
  try { sessionStorage.setItem(ADDED_KEY, key); } catch (e) { /* 使えなくても動く */ }
}

function render(v) {
  lastView = v;
  // 描き終えてから(描くたびに)、積んでいない計算を覚え直す
  setTimeout(() => markPending('calc', calcPending()), 0);
  const o = v.order, r = v.result;

  // ---- 入力(サーバが整形した値で上書きする) ----
  if (document.activeElement !== $('#lot')) $('#lot').value = v.input.LOT;
  if (document.activeElement !== $('#ins')) $('#ins').value = v.input.検入数;
  if (document.activeElement !== $('#outer')) $('#outer').value = v.input.外径;
  if (document.activeElement !== $('#stack')) $('#stack').value = r.積数;

  // ---- 受注情報 ----
  text($('#o-厚'), o.受注板厚);        text($('#o-幅'), o.受注板幅);
  text($('#o-材質'), o.受注材質);      text($('#o-調質'), o.受注調質);
  text($('#o-用途c'), o.用途コード);   text($('#o-用途名'), o.用途名);
  text($('#o-包装'), o.包装仕様NO);    text($('#o-単重'), o.製品単重);
  text($('#o-取引先'), o.取引先名称);  text($('#o-納入先'), o.納入先名称);
  text($('#o-梱包重量'), o.梱包単位_重量);
  text($('#o-梱包枚数'), o.梱包単位_枚数);
  text($('#o-外径min'), o.コイル外径_MIN);
  text($('#o-外径目標'), o.コイル外径_目標);
  text($('#o-外径max'), o.コイル外径_MAX);
  text($('#o-内径'), o.コイル内径_目標);
  text($('#o-納期'), o.営業納期);      text($('#o-比重'), o.比重);
  text($('#o-コメント'), o.工場用コメント);
  text($('#o-単重再計算'), r.単重再計算);

  // ---- 結果 ----
  text($('#r-種類'), r.パレット種類);  text($('#r-サイズ'), r.パレットサイズ);
  text($('#r-名称'), r.パレット名称);  text($('#r-台数'), r.台数);
  text($('#r-高さ'), r.総高さ);
  text($('#r-re'), r.Re_積数 ? `${r.Re_積数} / ${r.Re_台数}` : '');
  $('#w-re').hidden = !r.Re_積数;

  text($('#r-コイル間'), r.コイル間);
  text($('#r-リサイズ'), r.リプラサイズ表示 || r.リプラサイズ);
  text($('#r-緩衝材'), r.緩衝材);      text($('#r-HB'), r.HB枚数);
  text($('#r-下長'), r.最下部長さ);    text($('#r-長本'), r.長い本数);
  text($('#r-下短'), r.最下部長さ_短); text($('#r-短本'), r.短い本数);
  text($('#r-間長'), r.リプラ長さ);    text($('#r-間本'), r.本数);
  text($('#r-下本数'), r.最下部本数);  text($('#r-total'), r.TotalC);

  const imi = !!(r.IMI中間長さ || r.IMI中間本数);
  $('#w-imi').hidden = !imi;
  text($('#r-imi長'), r.IMI中間長さ);  text($('#r-imi本'), r.IMI中間本数);

  text($('#r-間種'), r.間リプラ種類);  text($('#r-下種'), r.最下部リプラ種類);

  // ---- 印 ----
  $$('.f--why').forEach(el => el.classList.remove('f--why'));
  $$('.f--nosingle').forEach(el => el.classList.remove('f--nosingle'));

  const flags = new Set(v.flags || []);
  Object.entries(FLAG_TO_FIELD).forEach(([flag, sel]) => {
    if (flags.has(flag)) $(sel)?.classList.add('f--why');
  });
  if (flags.has('spec')) {
    $('#w-梱包重量')?.classList.add('f--why');
    $('#w-梱包枚数')?.classList.add('f--why');
  }
  if (flags.has('no_single') || flags.has('single_split')) {
    $('#w-re')?.classList.add('f--nosingle');
  }
  const differs = flags.has('spacer_differs');
  $('#w-種類差')?.classList.toggle('f--why', differs);
  $('#w-種類差2')?.classList.toggle('f--why', differs);

  // ---- 図形(最下部リプラの並び) ----
  // **画面には出さない。** 本数は表に出ているので、並びの絵からは
  // 新しく分かることが無かった(現場の判断)。
  // 計算そのもの(`v.shape`)は VBA どおり残してあるので、要るように
  // なったらここで出すだけでよい。

  // ---- 断りと注意 ----
  const box = $('#messages');
  const msgs = v.messages || [];
  box.innerHTML = msgs.length
    ? `<div class="note note--warn"><ul>${
        msgs.map(m => `<li>${esc(m)}</li>`).join('')}</ul></div>`
    : '';
}

function apply(res, okMessage) {
  if (res.view) render(res.view);
  if (res.ok) { if (okMessage) toast(okMessage); }
  else if (res.message) toast(res.message, 'warn');
  return res.ok;
}

// ------------------------------------------------------------------
// ロット
// ------------------------------------------------------------------
$('#lot').addEventListener('input', async (e) => {
  // VBA は7桁打ち終わった時点で自動で走らせていた。同じにする
  const value = e.target.value.toUpperCase().replace(/[^0-9A-Z]/g, '');
  e.target.value = value;
  if (value.length !== 7) return;

  const r = await call('/api/calc/lot', { LOT: value });
  const sel = $('#order-no');
  sel.innerHTML = '<option value="">—</option>'
    + (r.candidates || []).map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join('');
  if (r.view) render(r.view);
  if (r.message) toast(r.message, 'warn');
  else if ((r.candidates || []).length === 1) {
    // 候補が1つなら選んでおく。選ばせる意味がない
    sel.value = r.candidates[0];
    sel.dispatchEvent(new Event('change'));
  }
});

// ------------------------------------------------------------------
// 包装仕様No ── 押すとコピーして閲覧システムを開く
// ------------------------------------------------------------------
// 閲覧システムは固定URLで開くだけで番号は渡らない(先方で検索し直す)。
// **開く前にコピーしておく**ので、開いた先にそのまま貼れる。
// コピーできたかどうかは必ず言う ── 黙って失敗すると、貼り付け先で
// 「何も入らない」としか分からない(python-web-tools と同じ)
$('#o-包装').addEventListener('click', async (e) => {
  const no = e.currentTarget.textContent.trim();
  if (!no) {
    // まだロットを引いていない。番号の無いまま開いても仕方がない
    e.preventDefault();
    toast('先にロットを引いてください', 'warn');
    return;
  }
  try {
    await navigator.clipboard.writeText(no);
    toast(`包装仕様書NO「${no}」をコピーしました`);
  } catch {
    toast('コピーできませんでした。文字を選択してコピーしてください', 'warn');
  }
});

$('#order-no').addEventListener('change', async (e) => {
  if (!e.target.value) return;
  apply(await call('/api/calc/order', { 受注番号: e.target.value }));
});

// ------------------------------------------------------------------
// 入力
// ------------------------------------------------------------------
['ins', 'outer'].forEach(id => {
  $('#' + id).addEventListener('change', async () => {
    apply(await call('/api/calc/input', {
      検入数: $('#ins').value, 外径: $('#outer').value,
    }));
  });
});

// 積数を手で直したとき。**台数計算は呼ばない**(規則で上書きしない)
$('#stack').addEventListener('change', async () => {
  const r = await call('/api/calc/input', { 積数: $('#stack').value });
  if (!r.ok) { apply(r); return; }
  apply(await call('/api/calc/restack', {}), '積数から台数を出し直しました');
});

// ------------------------------------------------------------------
// 計算
// ------------------------------------------------------------------
$('#run').addEventListener('click', async () => {
  const r = await call('/api/calc/run', {});
  apply(r, r.ok ? `台数 ${r.view.result.台数} 台 / ﾘﾌﾟﾗ ${r.view.result.TotalC} 本` : '');
});

$('#weight').addEventListener('click', async () => {
  if (!confirm('単重を再計算します（外径・内径・幅・比重で計算します）')) return;
  apply(await call('/api/calc/weight', {}));
});

$('#clear').addEventListener('click', async () => {
  $('#order-no').innerHTML = '<option value="">—</option>';
  apply(await call('/api/calc/clear', {}), 'はじめからにしました');
  $('#lot').focus();
});

$('#add').addEventListener('click', async () => {
  const r = await call('/api/checklist/add',
                       { サイズ確定: $('#size-fixed').checked });
  if (r.ok) {
    rememberAdded(viewKey(lastView));
    markPending('calc', '');
    toast(`チェックリストの ${r.行番号} 行目に積みました`);
  }
  else toast(r.message || 'チェックリストへ積めません', 'warn');
});

// ------------------------------------------------------------------
// バリ揃え梱包
// ------------------------------------------------------------------
function showBurr(b) {
  if (!b) return;
  $('#upper').value = b.上本数;
  $('#lower').value = b.下本数;
  burrShown = [$('#upper').value, $('#lower').value];
  text($('#b-残'), b.残り検入数); text($('#b-積数'), b.積数);
  text($('#b-上'), b.上台数);     text($('#b-下'), b.下台数);
  text($('#b-台数'), b.台数);
}

// スピンボタン。**増減するだけで、計算はしない** ── VBA も
// スピンで数を変えてから「計算Start」を押す作りだった。
// 押すたびに計算しに行くと、10本ぶん押すあいだに10回走る。
$$('.spin__b').forEach(btn => {
  btn.addEventListener('click', () => {
    const el = $('#' + btn.dataset.for);
    if (!el) return;
    const now = parseInt(el.value, 10);
    const next = (Number.isFinite(now) ? now : 0) + Number(btn.dataset.step);
    // 本数なので**0より下へは行かない**
    el.value = String(Math.max(0, next));
    // 打ち込みと同じ扱いにする(見ている側があれば気づけるように)
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
});

$('#burr').addEventListener('click', async () => {
  const r = await call('/api/calc/burr', {
    上本数: $('#upper').value, 下本数: $('#lower').value,
  });
  if (r.ok) showBurr(r.burr);
  else toast(r.message || '上下の台数を出せません', 'warn');
});

$('#burr-send').addEventListener('click', async () => {
  if (!confirm('台数を送りますか？')) return;
  const r = await call('/api/calc/burr/transfer', {
    上本数: $('#upper').value, 下本数: $('#lower').value,
  });
  apply(r, r.ok ? '結果を送りました' : '');
});

// ------------------------------------------------------------------
/**
 * 閉じると消えるもの(統合 1.2.5)。計算の中身はサーバのメモリにだけあり、
 * 止めると消える(チェックリストへ積んだものは残る)。
 */
watchUnsaved(() => {
  const out = [];
  const lot = $('#lot').value || '';
  const shown = (lastView && lastView.input && lastView.input.LOT) || '';
  if (lot && lot !== shown) out.push('LOT(7桁になっていない入力)');
  else if (calcPending()) out.push(calcPending());
  const burr = [$('#upper').value, $('#lower').value];
  if (burr.some((v) => String(v).trim() !== '')
      && (!burrShown || burr[0] !== burrShown[0] || burr[1] !== burrShown[1])) {
    out.push('バリ揃えの上下の本数(「計算」を押していない)');
  }
  return out;
});

(async function start() {
  const r = await call('/api/calc');
  if (r.ok) {
    // 面を移って戻ってきた: 覚えている「積んだときの中身」と比べる。覚えていなければ
    // (このタブで初めて・覚えられない)前に積んだかは分からないので、積んだものとみなす
    // (読み直しのたびに「積んでいない」と訊かない)
    let known = null;
    try { known = sessionStorage.getItem(ADDED_KEY); } catch (e) { known = null; }
    addedKey = known !== null ? known
      : (readPending('calc') ? '' : viewKey(r.view));
    render(r.view);
  }
  burrShown = [$('#upper').value, $('#lower').value];
}());
