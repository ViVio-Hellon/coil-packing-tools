/*
  設定画面。

  ここがするのは「打たれた値をサーバへ渡す」「返ってきた一式を写す」だけ。
  どこを実際に見に行くか、そこに届くかの判断はすべてサーバ側にある。
*/
import { call, toast, $, $$, text, esc, watchUnsaved } from '../core.js';
import { refresh as refreshMaster } from './master.js';

let view = null;

// ------------------------------------------------------------------
// 面の切り替え
//
// 設定は縦に長い。**分類ごとに分けて、探す距離を短くする。**
// 帯の版バッジが `/settings#about` で飛んでくるので、印を見て
// その面を開く ── 開かないと、飛んだ先が空に見える。
// ------------------------------------------------------------------
const PANELS = ['source', 'master', 'team', 'distribution', 'storage', 'about'];

export function openTab(name) {
  if (!PANELS.includes(name)) name = PANELS[0];
  PANELS.forEach(key => {
    const tab = $('#tab-' + key);
    const panel = $('#panel-' + key);
    if (!tab || !panel) return;
    const on = key === name;
    tab.setAttribute('aria-selected', String(on));
    panel.hidden = !on;
  });
  // 戻る/進むで面が戻るようにする。**押した面が URL に残る**ので、
  // 「さっき見ていたところ」を共有できる
  if (location.hash.slice(1) !== name) {
    history.replaceState(null, '', `${location.pathname}${location.search}#${name}`);
  }
}

PANELS.forEach(key => {
  const tab = $('#tab-' + key);
  if (tab) tab.addEventListener('click', () => openTab(key));
});
// `#about` は「このアプリ」の面そのものの名前でもある
openTab(location.hash.slice(1));
window.addEventListener('hashchange', () => openTab(location.hash.slice(1)));

// ------------------------------------------------------------------
function fieldHtml(f) {
  const optional = f.optional ? '<span class="muted">(空でよい)</span>' : '';
  const vba = f.vba ? `<span class="setrow__vba">VBA: ${esc(f.vba)}</span>` : '';
  const expect = f.expect
    ? `<div class="setrow__d">探すファイル: <code>${esc(f.expect)}</code></div>` : '';
  return `
  <div class="setrow" data-key="${esc(f.key)}">
    <div class="setrow__k">${esc(f.label)} ${optional} ${vba}</div>
    <div class="setrow__d">${esc(f.detail)}</div>
    ${expect}
    <input type="text" class="input" id="in-${esc(f.key)}"
           value="${esc(f.value)}" placeholder="${esc(f.default)}"
           spellcheck="false" autocomplete="off">
    <div class="resolved">
      いま見に行く場所: ${esc(f.resolved) || '(使いません)'}
      ${f.using_default ? ' <b>(既定)</b>' : ''}
      ${f.relative ? ' <b>(アプリのフォルダからの相対として読みました)</b>' : ''}
    </div>
    <div class="probe" id="pr-${esc(f.key)}"></div>
    <div class="btnrow">
      <button class="btn btn--find" type="button"
              data-probe="${esc(f.key)}">確かめる</button>
      <button class="btn btn--run" type="button"
              data-save="${esc(f.key)}">この欄を保存</button>
      <button class="btn" type="button" data-reset="${esc(f.key)}">既定に戻す</button>
    </div>
  </div>`;
}

// ------------------------------------------------------------------
// このアプリについて
//
// 版はサーバが持つ1つの値をそのまま写すだけ。画面側で組み立てない
// ── 帯のバッジと1文字でも違うと、どちらが本当か分からなくなる。
function renderAbout(a) {
  if (!a) return;
  text($('#a-version'), a.version_label);
  text($('#a-name'), a.display_name);
  text($('#a-appid'), a.app_id);

  // 実際に開いているポートを出す。設定と違うなら**繰り上がった**ので、
  // その事実まで出す(「8733 のはずが開かない」の答えになる)
  const port = a.actual_port || a.port;
  text($('#a-port'),
       !port ? 'なし（デスクトップ版）'
             : (a.port && port !== a.port) ? `${port} (設定は ${a.port})` : String(port));

  text($('#a-python'), a.python);
  text($('#a-pyexe'), a.python_exe);
  text($('#a-root'), a.app_root);
  text($('#a-local'), a.local_root);
  text($('#a-appconf'), a.app_config_path);

  // 版が読めない / 設定ファイルが読めない。どちらも起動は止めていないので、
  // ここで言わないと誰も気づかない
  const warn = [a.config_error, a.version_problem].filter(Boolean).join(' ');
  const box = $('#a-warn');
  text(box, warn);
  box.hidden = !warn;
}

// 保存先(このPC／複数のPCで共有)。**このPCで引き継ぐもの**と**全ラインで共有するもの**を
// 分けて、ファイルの場所と中身を出す(現場の指摘: 2つは違う。設定部に明記してほしい)。
// 見出し・説明は `common/storage_places.py` が渡す(3機能で同じ言葉にする)
function renderStore(store) {
  if (!store) return;
  $('#storeGroups').innerHTML =
    `<p class="setrow__d store-intro">${esc(store.intro)}</p>` +
    store.groups.map(g => `
    <div class="store-box store-${esc(g.kind)}" data-store="${esc(g.kind)}">
      <h3 class="store-h">${esc(g.title)}
        <span class="where where-${esc(g.kind)}">${esc(store.badge[g.kind])}</span></h3>
      <p class="setrow__d">${esc(g.lead)}</p>
      <div class="wrap">
        <table class="grid-table store-table">
          <thead><tr><th>何</th><th>場所</th><th>入っているもの</th></tr></thead>
          <tbody>${g.rows.map(r => `<tr>
            <td class="store-name">${esc(r.name)}</td>
            <td class="store-path"><code>${esc(r.path)}</code></td>
            <td>${esc(r.holds)}${r.note ? `<span class="store-note">${esc(r.note)}</span>` : ''}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>
      ${g.note ? `<p class="setrow__d">${esc(g.note)}</p>` : ''}
    </div>`).join('');
}

function render(v) {
  view = v;
  $('#fields').innerHTML = v.fields.map(fieldHtml).join('');

  $('#line').value = v.line;
  renderWorkers(v.workers, v.worker);
  $('#auto-import').checked = !!v.auto_import;

  renderDistribution(v.distribution);
  renderPathLock(v.can_edit_paths);
  renderAbout(v.about);
  text($('#p-config'), v.config_path);
  text($('#p-db'), v.db_path);
  text($('#p-log'), v.log_dir);
  renderStore(v.storage);

  const order = ['包装仕様', 'パレット', 'リプラサイズ', '仕掛引当', '仕掛受注'];
  $('#imported').innerHTML = order.map(name => {
    const r = v.imported[name];
    return `<tr>
      <td>${esc(name)}</td>
      <td>${esc(r ? r['取り込み日時'] : '未取り込み')}</td>
      <td class="num">${esc(r ? r['件数'] : '')}</td>
      <td>${esc(r ? r['元ファイル'] : '')}</td>
    </tr>`;
  }).join('');

  // 欄ごとのボタンを配り直す(innerHTML で作り直しているので毎回)。
  // **1欄ぶんだけ送る。** まとめて送ると、直すつもりのない欄まで
  // いまの画面の値で上書きしてしまう
  $$('[data-save]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const key = btn.dataset.save;
      const r = await call('/api/settings', { [key]: $('#in-' + key).value });
      if (!r.ok) { refuse(r, '保存できません'); return; }
      render(r.view);
      toast(`${label(key)} を保存しました。`
            + '置き場所を変えたときは「いま取り込む」を押してください');
    });
  });

  $$('[data-probe]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const key = btn.dataset.probe;
      const r = await call('/api/settings/probe',
                           { [key]: $('#in-' + key).value });
      if (!r.ok) { toast(r.message || '確かめられません', 'warn'); return; }
      const res = r.results[0];
      showProbe(res);
      toast(res.ok ? '届きました' : '届きませんでした', res.ok ? '' : 'warn');
    });
  });

  $$('[data-reset]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const r = await call('/api/settings/reset', { key: btn.dataset.reset });
      if (!r.ok) { refuse(r, '戻せません'); return; }
      render(r.view);
      toast('既定に戻しました');
    });
  });
}

/** 担当者の一覧。**取り込み直したら、ここも入れ替わる。**

    画面を開いたときの分はサーバが描いているが、「いま取り込む」で
    名簿が入れ替わったときに古い一覧が残ると、居ない人を選べてしまう。 */
function renderWorkers(choices, current) {
  const sel = $('#worker');
  if (!sel || !choices) return;
  const opt = (w) =>
    `<option value="${esc(w)}"${w === current ? ' selected' : ''}>${esc(w)}</option>`;
  sel.innerHTML = '<option value="">(未選択)</option>'
    + choices.groups.map(g => g.team
        ? `<optgroup label="${esc(g.team)}">${g.names.map(opt).join('')}</optgroup>`
        : g.names.map(opt).join('')).join('');
  sel.value = current || '';

  // どこから来た一覧なのかを言う。**予備に落ちていることに気づけないと**、
  // 名簿を直したのに反映されない、と思われる
  const why = $('#workerWhy');
  if (!why) return;
  const 機側 = choices.line || '機側';
  if (!choices.from_master) {
    why.textContent = '梱包資材マスタの「班員名簿」を取り込めていないので、'
      + '内蔵の一覧を出しています。「いま取り込む」を押すと名簿から出ます。';
  } else if (choices.stale && choices.stale_why === 'not_machine_side') {
    // 名簿には居る。**消えたのではなく、担当ラインが違う**
    why.textContent = `いま選ばれている「${current}」は、担当ラインが`
      + `${機側}ではありません。選び直してください。`;
  } else if (choices.stale) {
    why.textContent = `いま選ばれている「${current}」は名簿にありません`
      + '（異動・退職のあとなど）。選び直してください。';
  } else if (!choices.narrowed) {
    // **絞れていないことに気づけないと**、機側以外が出ているのを
    // 不具合だと思われる。名簿の担当ラインが埋まっていない状態
    why.textContent = `名簿に担当ラインが${機側}の人がいないので、`
      + `${choices.count} 名すべてを出しています。`
      + '名簿の「担当ライン」を日報ツールで直してください。';
  } else {
    why.textContent = `梱包資材マスタの「班員名簿」から、担当ラインが`
      + `${機側}の ${choices.count} 名。`
      + (choices.hidden ? `（機側以外の ${choices.hidden} 名は出しません）` : '');
  }
}

/** 置き場所を直せるか。**押す前に言う** ── 押してから断られるのは手戻り */
function renderPathLock(canEdit) {
  const box = $('#pathLock');
  if (!box) return;
  box.hidden = !!canEdit;
  box.textContent = canEdit ? '' :
    '置き場所を変えるにはパスワードが要ります。'
    + 'この画面のいちばん上でパスワードを入れてください。'
    + '（見るだけならそのままできます）';
  $$('[data-save], [data-reset]').forEach(b => { b.disabled = !canEdit; });

  // 配布設定も同じ関門(置き場所とパスワードを配るので)
  const dist = $('#distLock');
  if (dist) {
    dist.hidden = !!canEdit;
    dist.textContent = canEdit ? '' :
      '配布設定を書き出す・読み込み直す・消すにはパスワードが要ります。'
      + 'この画面のいちばん上でパスワードを入れてください。';
  }
  ['#distExport', '#distReapply', '#distRemove'].forEach(sel => {
    const b = $(sel);
    if (b) b.disabled = !canEdit;
  });
}

// ------------------------------------------------------------------
// 配布設定(`coil_tool/distribution.py`)
//
// 選んだ項目を送り、返ってきた一式を写すだけ。**パスワードの値は
// 画面に出さない**(サーバも出さない)。
// ------------------------------------------------------------------
function renderDistribution(d) {
  if (!d || !$('#distRows')) return;
  const state = $('#distState');
  state.textContent = d.exists ? 'あり' : 'なし';
  state.className = `st st--${d.exists ? 'ok' : 'warn'}`;
  text($('#distMeta'), d.exists
    ? `${d.created_at} に ${d.created_on} で作成`
    : 'まだありません。下で書き出すと、ツールのフォルダの「配布設定\\packing_material_calculation」にできます。');
  $('#distRows').innerHTML = d.contents.length
    ? d.contents.map(c =>
        `<tr><td>${esc(c.label)}</td><td>${esc(c.value)}</td></tr>`).join('')
    : '<tr><td colspan="2" class="muted">（なし）</td></tr>';
  text($('#distPath'), d.path);
  text($('#distApplied'), d.applied_at
    ? `この端末が最後に配布設定を書き出した／読み込んだのは ${d.applied_at} です。` : '');

  // 選ぶ欄は**最初の1回だけ**作る。描き直すたびに作ると、選び直した
  // チェックが既定に戻ってしまう。いまの値の添え書きだけ入れ替える
  const box = $('#distItems');
  if (!box.querySelector('[data-dist-item]')) {
    box.insertAdjacentHTML('beforeend', d.items.map(it => `
      <label><input type="checkbox" data-dist-item="${esc(it.key)}"${it.default ? ' checked' : ''}>
        <span>${esc(it.label)}</span>
        <span class="muted" data-dist-now="${esc(it.key)}"></span></label>`).join(''));
  }
  d.items.forEach(it => {
    const now = box.querySelector(`[data-dist-now="${it.key}"]`);
    if (now) now.textContent = it.set ? `いま: ${it.value}` : 'この端末では既定のまま（入りません）';
  });
}

async function sendDistribution(path, body) {
  const r = await call(path, body);
  if (!r.ok) { refuse(r, 'できませんでした'); return; }
  render(r.view);
  toast(r.message || '済みました');
}

const distExport = $('#distExport');
if (distExport) {
  distExport.addEventListener('click', () => sendDistribution(
    '/api/settings/distribution/export',
    { items: $$('[data-dist-item]').filter(b => b.checked).map(b => b.dataset.distItem) }));
  $('#distReapply').addEventListener('click', () =>
    sendDistribution('/api/settings/distribution/reapply', {}));
  $('#distRemove').addEventListener('click', () =>
    sendDistribution('/api/settings/distribution/remove', {}));
}

function label(key) {
  const f = view.fields.find(x => x.key === key);
  return f ? f.label : key;
}

/** 断られたときの出し方。**パスワードが要るのは断りの種類が違う** */
function refuse(r, fallback) {
  if (r.reason === 'needs_password') {
    toast(r.message, 'warn');
    // 打つ場所は**いまの面の上**にある(面の外に出してあるので、
    // どの面からでもそのまま打てる)。面を移らずに合わせるだけ
    const pass = $('#authPass');
    if (pass) { pass.focus(); pass.scrollIntoView({ block: 'center' }); }
    return;
  }
  toast(r.message || fallback, 'warn');
}

function showProbe(res) {
  const el = $('#pr-' + res.key);
  if (!el) return;
  el.className = 'probe ' + (res.ok ? 'probe--ok' : 'probe--ng');
  const found = res.found && res.found.length
    ? '（' + res.found.slice(0, 6).join(', ') + '）' : '';
  el.textContent = (res.ok ? '✓ ' : '✕ ') + res.message + found
    + (res.resolved ? ` … ${res.resolved}` : '');
}

// ------------------------------------------------------------------
// ライン・担当者・自動取り込みは**選んだ時点で保存する**。
// 置き場所と違って間違えても害が小さく、押し忘れのほうが困る
// (「選んだのに票に出ない」になる)
['#line', '#worker', '#auto-import'].forEach(sel => {
  const el = $(sel);
  if (!el) return;
  el.addEventListener('change', async () => {
    const r = await call('/api/settings', {
      line: $('#line').value,
      worker: $('#worker').value,
      auto_import: $('#auto-import').checked,
    });
    if (!r.ok) { toast(r.message || '保存できません', 'warn'); return; }
    render(r.view);
    toast('保存しました');
  });
});

$('#import').addEventListener('click', async () => {
  const btn = $('#import');
  btn.disabled = true;
  btn.textContent = '取り込んでいます…';
  const r = await call('/api/settings/import', {});
  btn.disabled = false;
  btn.textContent = 'いま取り込む';

  const box = $('#import-result');
  const bad = (r.tables || []).filter(t => !t.ok);
  const filled = (r.tables || []).filter(t => t.ok && t.filled && t.filled.length);
  const dup = (r.tables || []).filter(t => t.duplicates && t.duplicates.length);

  let html = '';
  if (bad.length) {
    html += `<div class="note note--warn"><b>取り込めなかったもの</b><ul>`
      + bad.map(t => `<li>${esc(t.table)}: ${esc(t.error)}</li>`).join('')
      + `</ul></div>`;
  }
  if (filled.length) {
    html += `<div class="note"><b>取り込み元に無かった列</b>（既定値で埋めました）<ul>`
      + filled.map(t => `<li>${esc(t.table)}: ${esc(t.filled.join(', '))}</li>`).join('')
      + `</ul></div>`;
  }
  if (dup.length) {
    // **黙って捨てない。** 取り込み元に同じ番号の行が2つある、という
    // 知らせ。先に出てきた行を使っている（VBAも最初の1件しか見ない）
    html += `<div class="note note--warn"><b>取り込み元に同じ番号の行が2つ以上あります</b>`
      + `（先に出てきた行を使いました。元のマスタを直してください）<ul>`
      + dup.map(t => `<li>${esc(t.table)}: ${esc(t.duplicates.join(', '))}</li>`).join('')
      + `</ul></div>`;
  }
  box.innerHTML = html;

  if (r.view) render(r.view);
  toast(r.ok ? `取り込みました（${r.total} 件）`
             : `${bad.length} 件取り込めませんでした`, r.ok ? '' : 'warn');
});

// ------------------------------------------------------------------
(async function start() {
  const r = await call('/api/settings');
  if (r.ok) render(r.view);
  else toast(r.message || '設定を読めません', 'warn');
}());


// ------------------------------------------------------------------
// 直すための認証(面の外に1つ)
// ------------------------------------------------------------------
/*
  梱包資材マスタを**直す**ための関門。見るだけならそのまま通る。

  【判断はサーバが持つ】
  ここは打たれたものを投げて、返ってきた状態を写すだけ。
  合っているかどうかを画面で持たない。
*/
const authEls = {
  state: $('#authState'), pass: $('#authPass'),
  open: $('#authOpen'), close: $('#authClose'), note: $('#authNote'),
};

/** 残り時間を人の言葉にする。 */
function remainsText(seconds) {
  if (!seconds) return '';
  const min = Math.ceil(seconds / 60);
  return `あと約${min}分で自動的に閉じます`;
}

function renderAuth(state) {
  if (!authEls.state || !state) return;
  authEls.state.className = `st st--${state.open ? 'ok' : 'warn'}`;
  authEls.state.textContent = state.open ? '直せます' : '見るだけ';
  authEls.open.hidden = state.open;
  authEls.pass.hidden = state.open;
  authEls.close.hidden = !state.open;
  if (!state.open) authEls.pass.value = '';

  authEls.note.textContent = state.open
    ? remainsText(state.remains)
    : (state.custom
        ? 'この端末ではパスワードを変えてあります。'
        : '既定のパスワードのままです。');
  // 認証は面の外に1つ。**どの面も同じ状態を見る**ので、通ったら
  // 両方へ伝える(2か所に持つと片方だけ古くなる)
  refreshMaster();
  // 置き場所の関門も同じパスワードで開く。**ここで出し直さないと**、
  // 認証したのに「この欄を保存」が押せないままに見える
  renderPathLock(state.open);
}

async function sendAuth(body) {
  const r = await call('/api/master/auth', body);
  if (r.auth) renderAuth(r.auth);
  toast(r.message || (r.ok ? '' : '通りませんでした'), r.ok ? '' : 'warn');
  return r.ok;
}

if (authEls.open) {
  authEls.open.addEventListener('click', () =>
    sendAuth({ password: authEls.pass.value }));
  authEls.pass.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); authEls.open.click(); }
  });
  authEls.close.addEventListener('click', () => sendAuth({ close: true }));
}

const pwSave = $('#pwSave');
if (pwSave) {
  pwSave.addEventListener('click', async () => {
    const r = await call('/api/master/password', {
      current: $('#pwNow').value,
      new: $('#pwNew').value,
      confirm: $('#pwConfirm').value,
    });
    if (r.auth) renderAuth(r.auth);
    if (r.ok) {
      $('#pwNow').value = $('#pwNew').value = $('#pwConfirm').value = '';
    }
    toast(r.message || '変えられませんでした', r.ok ? '' : 'warn');
  });
}

// 最初の状態はテンプレートが埋め込んである(共有フォルダに触らずに分かる)
// 置き場所の欄で、まだ「この欄を保存」を押していないもの(統合 1.2.5)。
// 欄はサーバの値で作り直すので、作ったときの値(defaultValue)と比べる
watchUnsaved(() => $$('#fields .setrow').filter((row) => {
  const input = $('input.input', row);
  return input && input.value !== input.defaultValue;
}).map((row) => `設定: ${(($('.setrow__k', row) || {}).firstChild || {}).textContent || row.dataset.key}`.trim()));

if (window.MASTER_FRAME && window.MASTER_FRAME.auth) {
  renderAuth(window.MASTER_FRAME.auth);
}
