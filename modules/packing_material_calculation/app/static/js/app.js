/*
  どの画面にも共通の配線。

  ・帯のライン / 担当者 … 変えたらその場で保存する
  ・終了ボタン
  ・心拍(基盤仕様書 2.9)
*/
import { call, toast, $, screenId, BASE } from './core.js';

// ---- 帯 ----------------------------------------------------------
const line = $('#rb-line');
if (line) {
  line.addEventListener('change', async () => {
    const r = await call('/api/settings', { line: line.value });
    toast(r.ok ? `ラインを ${line.value} にしました` : (r.message || '保存できません'),
          r.ok ? '' : 'warn');
  });
}

const worker = $('#rb-worker');
if (worker) {
  worker.addEventListener('change', async () => {
    const r = await call('/api/settings', { worker: worker.value });
    toast(r.ok ? (worker.value ? `担当者を ${worker.value} にしました` : '担当者を未選択にしました')
               : (r.message || '保存できません'), r.ok ? '' : 'warn');
  });
}

// ---- 終了 --------------------------------------------------------
const quit = $('#quit');
if (quit) {
  // 統合画面の中(iframe)では、終了すると3機能とも終わる
  const inShell = window.top !== window;
  quit.addEventListener('click', async () => {
    if (!confirm(inShell ? 'このアプリを終了します。3つの機能とも終わります。よろしいですか？'
                         : 'このアプリを終了します。よろしいですか？')) return;
    const r = await call('/api/shutdown', {});
    // 【統合版】断られたら理由を出す(取り込みの途中など)。以前は結果を見ずに
    // 「終了しました」と出していた
    if (!r || r.ok === false) {
      toast((r && r.message) || '終了できませんでした', 'warn');
      return;
    }
    // 統合画面の外枠にも知らせる(外枠は「バックエンドと通信できません」ではなく
    // 「終了しました」を出す)
    if (inShell) {
      try { window.top.postMessage({ type: 'cpt:stopped' }, location.origin); } catch (e) { /* 無視 */ }
    }
    document.body.innerHTML =
      '<p style="padding:40px;font-size:16px">終了しました。このタブは閉じてください。</p>';
  });
}

// ---- 使ってよい画面は1つだけ ------------------------------------
//
// タブを2枚開くと、2枚とも入力できてしまい、**プロセスに1つしかない
// 作業状態を奪い合う**(`app/screen.py` にいきさつ)。2枚目は覆いを
// 掛けたまま断る。
//
// 符牒は `sessionStorage` に置く ── **タブごとに別で、読み直しでは
// 変わらない**ので、「同じタブが開き直した」と「2枚目が開いた」を
// 取り違えない。`localStorage` はタブ間で共有されるので使えない。
const gate = $('#gate');
// 通るまで出さないもの。**覆いは目隠しでしかない** ── これを
// 出さないことが、断られた画面で打てないことの本体
const hidden = ['.ribbon', '.shell'].map(sel => $(sel)).filter(Boolean);
let owned = false;

function gateShow(title, body, { refused = false, actions = false,
                                 note = '' } = {}) {
  if (!gate) return;
  gate.hidden = false;
  gate.classList.toggle('gate--ng', refused);
  $('#gateTitle').textContent = title;
  $('#gateBody').innerHTML = body;
  $('#gateActions').hidden = !actions;
  const noteEl = $('#gateNote');
  noteEl.textContent = note;
  noteEl.hidden = !note;
}

function gateOpen() {
  owned = true;
  if (gate) gate.hidden = true;
  hidden.forEach(el => { el.hidden = false; });
}

/** 使えなくする。**出していたものを引っ込める**(覆うだけにしない) */
function gateClosePanels() {
  owned = false;
  hidden.forEach(el => { el.hidden = true; });
}

/** 「◯分前から」。秒のままだと、さっき自分で開いたのか判断できない */
function ago(sec) {
  if (!sec || sec < 60) return 'たったいま';
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}分前`;
  return `${Math.floor(m / 60)}時間${m % 60}分前`;
}

function refused(other) {
  gateShow('すでに別の画面で開いています', `
    このアプリは<b>1つの画面でしか使えません</b>。
    2枚開くと、どちらで打った値が使われるのか分からなくなります
    （打った本人にも見えません）。<br><br>
    別の画面が <b>${ago(other)}</b> から開いています。`, {
    refused: true, actions: true,
    note: '「この画面で使う」を押すと、もう一方の画面は使えなくなります。' +
          'もう一方で入力の途中だった場合、その入力は失われます。',
  });
}

async function claim(takeover) {
  const r = await call('/api/screen/claim', { screen: screenId, takeover });
  if (r.ok) { gateOpen(); return true; }
  if (r.reason === 'offline') {
    gateShow('バックエンドに接続できません',
             'アプリが終了しているかもしれません。<br>' +
             '<code>Start.vbs</code> から開き直してください。',
             { refused: true });
    return false;
  }
  refused(r.other_age || 0);
  return false;
}

// **押されたときだけ取り上げる。** 自動で奪い返すと、断られた画面と
// 取り合いになって、どちらも安定しない
const take = $('#gateTake');
if (take) {
  take.addEventListener('click', async () => {
    take.disabled = true;
    if (!await claim(true)) take.disabled = false;
  });
}
const gateClose = $('#gateClose');
if (gateClose && window.top !== window) {
  // 【統合版】統合画面の中(iframe)では出さない。iframe からは `window.close()` が
  // 効かず、押しても「ブラウザの × で閉じて」と出るだけで、そのタブを閉じると
  // 梱包明細・ペナラベルまで閉じる(移植漏れの点検で見つかった)。
  // 梱包明細・ペナラベルの「別の画面で開いています」にも閉じるボタンは無い
  gateClose.hidden = true;
} else if (gateClose) {
  gateClose.addEventListener('click', () => {
    window.close();
    // `window.close()` は自分で開いたタブしか閉じられない。
    // 閉じられなかったときのために、やり方を出しておく
    gateShow('このタブを閉じてください',
             'ブラウザの × でこのタブを閉じてください。', { refused: true });
  });
}

// 断られている間も、空いたかどうかは見に行く(取りには行かない)。
// もう一方を閉じたとき、**押せば使える**ところまでは案内したい
setInterval(async () => {
  if (owned) return;
  const r = await call(`/api/screen?screen=${encodeURIComponent(screenId)}`);
  if (r && r.ok && !r.busy) {
    gateShow('もう一方の画面は閉じられました', `
      この画面で続けられます。`, { actions: true });
  }
}, 5000);

// ---- 心拍 --------------------------------------------------------
// 画面が生きていることを伝える。途切れるとサーバが自分で終わる。
// **符牒も一緒に送る** ── 取り上げられたことは、この返事で分かる。
const BEAT_MS = 20000;
async function beat() {
  // **符牒を名乗るのは、自分のものだと通ったときだけ。**
  // 断られている画面が名乗ると、相手の心拍が途切れた隙にサーバが
  // 「戻ってきた画面」とみなして通してしまい、押していないのに
  // 取り上げたことになる(取り上げは人が押したときだけ)。
  // 裏に回っているかも一緒に言う。**裏の間はサーバが心拍の途切れで
  // 終わらない** ── ブラウザが裏のタブのタイマーを間引くため
  const body = { hidden: document.hidden };
  if (owned) body.screen = screenId;
  const r = await call('/api/alive', body);
  // `own=false` は取り上げられた合図。**そこで使えなくする** ──
  // 2枚とも動くのを防ぐ最後の砦(サーバ側も同じ判断で断っている)
  if (owned && r && r.ok && r.own === false) {
    gateClosePanels();
    gateShow('この画面は使われなくなりました', `
      別の画面で「この画面で使う」が押されました。<br>
      続けるには、そちらの画面を使ってください。`, {
      refused: true, actions: true,
      note: 'この画面で続けたい場合は「この画面で使う」を押してください。' +
            '今度はもう一方が使えなくなります。',
    });
  }
}
beat();
setInterval(beat, BEAT_MS);

// 裏に回ったとき・戻ったときは**すぐに**伝える。
// 裏に回ったあとはタイマーが間引かれて次の心拍がいつ届くか分からない
// (Edge の「スリープ中のタブ」では届かない)ので、合図は sendBeacon で
// 確実に送る。戻ったときは返事(取り上げられていないか)を見たいので
// ふつうの心拍で送る
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    const payload = { hidden: true };
    if (owned) payload.screen = screenId;
    navigator.sendBeacon(BASE + '/api/alive', new Blob([JSON.stringify(payload)],
                                               { type: 'application/json' }));
  } else {
    beat();
  }
});

// 閉じたことは即座に伝える。**再読込でも飛ぶ**ので、サーバ側は
// 猶予を置いて待つ(戻ってくれば次の心拍で取り消される)。
// 符牒も返すので、閉じたあとに開き直した画面は待たずに通る。
window.addEventListener('pagehide', () => {
  const blob = new Blob([JSON.stringify({ closing: true, screen: screenId })],
                        { type: 'application/json' });
  navigator.sendBeacon(BASE + '/api/alive', blob);
});

// 最初の申し出。**ここを通るまで覆いは外れない**
claim(false);
