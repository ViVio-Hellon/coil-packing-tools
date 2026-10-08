/*
  発注票。

  【重複は止めずに知らせる】
  同じロットが2週間以内に出ていたら確認を出す。続けるかどうかは人が決める。
*/
import { call, toast, token, $, esc, BASE, watchUnsaved, markPending, readPending, ownPending } from '../core.js';

function sheetHtml(s) {
  const rows = s.rows.map(r => `<tr>
      <td class="num">${esc(r['長さ'])}</td>
      <td>${esc(r['LotNo'])}</td>
      <td class="num">${esc(r['数量'])}</td>
      <td>${r['長さ'] || r['数量'] ? '本' : ''}</td>
    </tr>`).join('');
  return `<section class="sec">
    <h2>${esc(s['種類'])} ${esc(s['角サイズ'])}</h2>
    <p class="setrow__d">提出日付 ${esc(s['提出日付'])} ／ 依頼者 ${esc(s['依頼者'])}</p>
    <div class="wrap"><table class="grid-table">
      <thead><tr><th>長さ</th><th>LotNo</th><th>数量</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
  </section>`;
}

function render(sheets, extra) {
  $('#sheets').innerHTML = sheets.map(sheetHtml).join('');
  const on = sheets.length > 0;
  $('#print').hidden = !on;
  $('#commit').hidden = !on;
  $('#print').href = `${BASE}/report/order?t=${encodeURIComponent(token)}`;

  if (extra && extra.length) {
    $('#note').insertAdjacentHTML('beforeend',
      `<div class="note"><b>倉庫の定尺にない長さ</b>を書き足しました：`
      + `${esc(extra.join(', '))}</div>`);
  }
}

async function build(confirmed) {
  $('#note').innerHTML = '';
  const r = await call('/api/order/build', { 確認済み: !!confirmed });

  if (!r.ok) {
    $('#note').innerHTML =
      `<div class="note note--warn">${esc(r.message || '発注票を作れません')}</div>`;
    $('#sheets').innerHTML = '';
    $('#print').hidden = true;
    $('#commit').hidden = true;
    return;
  }

  if (r.duplicates && r.duplicates.length) {
    const list = r.duplicates.map(d => `<li>${esc(d.message)}</li>`).join('');
    $('#note').innerHTML =
      `<div class="note note--warn"><b>2週間以内に発注あり</b><ul>${list}</ul>
       <p>このまま重複して発注しますか？</p>
       <div class="btnrow">
         <button class="btn btn--run" type="button" id="go">このまま作る</button>
         <button class="btn" type="button" id="stop">やめる</button>
       </div></div>`;
    $('#go').addEventListener('click', () => build(true));
    $('#stop').addEventListener('click', () => {
      $('#note').innerHTML = '<div class="note">処理を中断しました。</div>';
      $('#sheets').innerHTML = '';
      $('#print').hidden = true;
      $('#commit').hidden = true;
    });
    // 中身は先に見せる。何を出そうとしているか分からないと判断できない
    render(r.sheets, r.extra_lengths);
    return;
  }

  render(r.sheets, r.extra_lengths);
  toast(`${r.sheets.length} 枚作りました`);
}

$('#build').addEventListener('click', () => build(false));

$('#commit').addEventListener('click', async () => {
  if (!confirm('この内容を発注済みとして記録します。よろしいですか？')) return;
  const r = await call('/api/order/commit', {});
  if (r.ok) { committed = true; markPending('order', ''); }
  toast(r.ok ? `${r.saved} 件を履歴に残しました`
             : (r.message || '記録できません'), r.ok ? '' : 'warn');
});

// 作った発注票はサーバのメモリにだけある。記録していなければ、閉じると消える(統合 1.2.5)。
// 面を移っても訊けるよう、このタブに覚えておく
const ORDER_PENDING = '発注票(「発注済みとして記録」をしていない)';
let committed = false;
ownPending('order');
$('#build').addEventListener('click', () => { committed = false; });
function orderPending() {
  return !committed && !$('#commit').hidden ? ORDER_PENDING : '';
}
// 作った・やめた・記録したのたびに覚え直す(ボタンの見え隠れを見る)
new MutationObserver(() => markPending('order', orderPending()))
  .observe($('#commit'), { attributes: true, attributeFilter: ['hidden'] });
watchUnsaved(() => (orderPending() ? [orderPending()] : []));

(async function start() {
  const r = await call('/api/order');
  if (r.ok && r.sheets.length) {
    // 開き直したとき: このタブで「記録していない」と覚えていれば訊く。覚えていなければ、
    // 前に記録したかは分からないので訊かない(読み直しのたびに訊かない)
    committed = !readPending('order');
    render(r.sheets, []);
  } else if (r.ok) {
    markPending('order', '');       // 発注票が無い(入口を起こし直した など)。覚えを消す
  }
}());
