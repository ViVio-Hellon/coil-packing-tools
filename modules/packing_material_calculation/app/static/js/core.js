/*
  画面から使う小道具。

  【業務判断は入れない】
  ここにあるのは「サーバへ投げる」「返ってきたものを DOM に写す」だけ。
  計算も判定も Python 側にある(docs/設計.md §1 の約束1)。
*/

export const token = window.APP_TOKEN || '';
// この機能の入口(統合版では `/material`)。**経路はここで1度だけ付ける**
export const BASE = window.APP_BASE || '';

/*
  このタブの符牒。**使ってよい画面は1つだけ**で、2枚目は断られる
  (いきさつは `app/screen.py`)。

  `sessionStorage` に置くのは、**タブごとに別で、読み直しでは
  変わらない**ため ── 「同じタブが開き直した」と「2枚目が開いた」を
  取り違えない。`localStorage` はタブ間で共有されるので使えない。

  出どころをここ1つにしてあるのは、`call` が毎回添えるから。
  画面ごとに作ると、添え忘れた画面だけが断られる。
*/
const SCREEN_KEY = 'coil.screen';

function makeScreenId() {
  const fresh = () => (crypto.randomUUID && crypto.randomUUID()) ||
    String(Date.now()) + '-' + Math.random().toString(16).slice(2);
  try {
    let id = sessionStorage.getItem(SCREEN_KEY);
    if (!id) { id = fresh(); sessionStorage.setItem(SCREEN_KEY, id); }
    return id;
  } catch (e) {
    // 記憶が使えないブラウザ設定。**読み直すたびに別の符牒**になるので
    // 毎回断られるが、黙って2枚動かすよりはよい
    return fresh();
  }
}

export const screenId = makeScreenId();

/** サーバへ投げる。断りは例外にせず、そのまま返す。 */
export async function call(path, body) {
  const opt = {
    method: body === undefined ? 'GET' : 'POST',
    // 符牒は**書き込みのたびに**添える。画面の覆いは次の心拍まで
    // 掛からないので、取り上げられた直後の隙をサーバ側で塞ぐ
    headers: { 'X-App-Token': token, 'X-App-Screen': screenId },
  };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(BASE + path, opt);
  } catch (e) {
    offline(true);
    return { ok: false, reason: 'offline', message: 'バックエンドに接続できません' };
  }
  offline(false);
  let data;
  try {
    data = await res.json();
  } catch (e) {
    return { ok: false, reason: 'bad_response', message: '応答を読めませんでした' };
  }
  data.status = res.status;
  return data;
}

/** 短い知らせ。消えてよいものだけをここに出す。 */
let toastTimer = null;
export function toast(message, kind = '') {
  const el = document.getElementById('toast');
  if (!el || !message) return;
  el.textContent = message;
  el.className = 'toast' + (kind ? ' toast--' + kind : '');
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4000);
}

export function offline(on) {
  const el = document.getElementById('offline');
  if (el) el.hidden = !on;
}

/** 要素を引く小道具。 */
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function text(el, value) {
  if (el) el.textContent = value == null || value === '' ? '' : String(value);
}

/** 印を付け外しする。**色だけに頼らない**ので文字も一緒に動かす。 */
export function mark(el, on, cls) {
  if (el) el.classList.toggle(cls, !!on);
}

export function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ------------------------------------------------------------------
// まだ保存していない入力(統合 1.2.5)
//
// 統合画面の「終了」・窓の × ・外からの停止(ランチャー・stop.bat)の前に、
// 統合画面が `window.cptUnsaved()` を呼んで訊く。返した名前があれば
// 「閉じると消えます。閉じますか?」を出す。各面が自分の確かめ方を登録する。
// 登録先は window に置く(同じファイルを別の URL で読んでも1つになる)。
// ------------------------------------------------------------------
const unsavedChecks = (window.__cptUnsavedChecks = window.__cptUnsavedChecks || []);

export function watchUnsaved(check) {
  unsavedChecks.push(check);
}

/**
 * 面をまたいで残る「止めると消えるもの」(計算・発注票)。計算の面・発注票の面は別のページなので、
 * ほかの面にいるあいだも訊けるよう、このタブの sessionStorage に名前を置く。
 * その面が出ているときは、面が自分で確かめる(`owner` が立つ)。
 */
const PENDING_KEYS = { calc: 'cpt.material.pending.calc', order: 'cpt.material.pending.order' };

export function markPending(kind, name) {
  try {
    if (name) sessionStorage.setItem(PENDING_KEYS[kind], name);
    else sessionStorage.removeItem(PENDING_KEYS[kind]);
  } catch (e) { /* 使えなくても動く(その面にいるあいだだけ訊く) */ }
}

export function readPending(kind) {
  try { return sessionStorage.getItem(PENDING_KEYS[kind]) || ''; } catch (e) { return ''; }
}

const pendingOwners = (window.__cptPendingOwners = window.__cptPendingOwners || {});
export function ownPending(kind) {
  pendingOwners[kind] = true;
}

watchUnsaved(() => Object.keys(PENDING_KEYS)
  .filter((kind) => !pendingOwners[kind] && readPending(kind))
  .map((kind) => readPending(kind)));

window.cptUnsaved = () => unsavedChecks
  .flatMap((check) => { try { return check() || []; } catch (e) { return []; } })
  .map((name) => `資材計算: ${name}`);
