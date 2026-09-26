/* チェックリスト ── 15行の表を出して、行を消せるようにするだけ */
import { call, toast, token, $, text, esc, BASE } from '../core.js';

function render(v) {
  text($('#sub'), `${v.used.length} / ${v.max_rows} 行`);
  document.querySelector('.head h1').textContent = v.title;
  $('#print').href = `${BASE}/report/checklist?t=${encodeURIComponent(token)}`;
  $('#to-order').href = `${BASE}/order?t=${encodeURIComponent(token)}`;

  const byRow = new Map();
  v.rows.forEach(r => {
    const n = r['行番号'];
    if (!byRow.has(n)) byRow.set(n, []);
    byRow.get(n).push(r);
  });

  const out = [];
  for (let n = 1; n <= v.max_rows; n++) {
    const rows = byRow.get(n);
    if (!rows) {
      out.push(`<tr><td class="num">${n}</td>`
        + '<td></td>'.repeat(14) + '<td></td></tr>');
      continue;
    }
    rows.forEach((r, i) => {
      out.push(`<tr class="on">
        <td class="num">${i === 0 ? n : ''}</td>
        <td>${esc(r['サイズ確定'])}</td>
        <td>${esc(r['依頼日'])}</td>
        <td>${esc(r['LotNo'])}</td>
        <td>${esc(r['用途名'])}</td>
        <td>${esc(r['パレット種類'])}</td>
        <td class="num">${esc(r['台数'])}</td>
        <td>${esc(r['営業納期'])}</td>
        <td>${esc(r['コイル間'])}</td>
        <td class="num">${esc(r['リプラ長さ'])}</td>
        <td class="num">${esc(r['リプラ本数'])}</td>
        <td>${esc(r['リプラサイズ'])}</td>
        <td>${esc(r['緩衝材'])}</td>
        <td>${esc(r['依頼者'])}</td>
        <td>${esc(r['入荷日'])}</td>
        <td>${i === 0
          ? `<button class="btn" type="button" data-clear="${n}">消す</button>`
          : ''}</td>
      </tr>`);
    });
  }
  $('#rows').innerHTML = out.join('');

  $('#rows').querySelectorAll('[data-clear]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const n = btn.dataset.clear;
      if (!confirm(`${n} 行目を消します。よろしいですか？`)) return;
      const r = await call('/api/checklist/clear-row', { 行番号: Number(n) });
      if (r.ok) { render(r.view); toast(`${n} 行目を消しました`); }
      else toast(r.message || '消せません', 'warn');
    });
  });

  $('#note').innerHTML = v.used.length ? '' :
    '<div class="note">まだ何も積まれていません。'
    + '「資材計算」で計算してから<b>チェックリストへ積む</b>を押してください。</div>';
}

$('#clear').addEventListener('click', async () => {
  if (!confirm('チェックリストを全部消します。よろしいですか？')) return;
  const r = await call('/api/checklist/clear', {});
  if (r.ok) { render(r.view); toast('全部消しました'); }
});

(async function start() {
  const r = await call('/api/checklist');
  if (r.ok) render(r.view);
}());
