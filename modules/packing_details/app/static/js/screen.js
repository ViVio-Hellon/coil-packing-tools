/*
  screen.js — この画面が「何枚目か」を決める

  作業の状態（開いているロット・条番号・積み上げ）は**アプリ全体で
  ひとつ**しかない。タブを2枚開くと、同じものを2つの画面が奪い合う
  ── 先に開いた画面のロットが、触った瞬間にもう1枚のロットへ化ける。
  だから **2枚目には業務の画面を出さない。**

  【サーバだけでは見分けられない】
  ブラウザは再読込（F5）のとき、**古いページを畳む前に新しい要求を
  送る。** サーバから見ると「もう1枚開いた」と区別がつかず、開くところで
  断ると F5 のたびに断ることになる。見分けられるのはブラウザ側だけ。

  【2つ使う】
    sessionStorage … タブごとに違い、**再読込では消えない**名前。
                     同じ名前で名乗り直せばサーバは通す
    BroadcastChannel … いま他のタブが開いていないかを直接きく。
                     タブの複製は sessionStorage ごと写されるので、
                     名前だけでは見分けられない。**返事があれば2枚目**

  再読込のときは古いページがもう居ないので、返事は来ない。
*/

const KEY = "meisai.screen";
const CHANNEL = "meisai.screen.v1";

// 他のタブの返事を待つ時間。同じPCの中の話なので、これで足りる。
// **長くすると、開くたびに画面が出るのが遅れる**
const PING_WAIT_MS = 180;

let channel = null;
let id = "";

/** このタブの名前。**再読込では変わらない**(sessionStorage)。 */
export function tabId() {
  if (id) return id;
  try {
    id = sessionStorage.getItem(KEY) || "";
  } catch { /* 私用ウィンドウ等で読めないことがある */ }
  if (!id) {
    id = (crypto.randomUUID && crypto.randomUUID())
      || `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    try { sessionStorage.setItem(KEY, id); } catch { /* 読めなくても動く */ }
  }
  return id;
}

function open_() {
  if (channel === null) {
    channel = ("BroadcastChannel" in window) ? new BroadcastChannel(CHANNEL) : false;
  }
  return channel || null;
}

/**
  他のタブが開いていれば true。

  **タブの複製もここで捕まえる。** 複製は sessionStorage ごと写されて
  同じ名前になるので、名前の比較では見分けられない。
*/
export function anotherTabIsOpen() {
  const ch = open_();
  if (!ch) return Promise.resolve(false);       // 使えない環境ではサーバに任せる
  return new Promise((resolve) => {
    let done = false;
    const finish = (found) => {
      if (done) return;
      done = true;
      ch.removeEventListener("message", onMessage);
      resolve(found);
    };
    const onMessage = (event) => {
      if (event.data && event.data.type === "pong") finish(true);
    };
    ch.addEventListener("message", onMessage);
    ch.postMessage({ type: "ping", from: tabId() });
    setTimeout(() => finish(false), PING_WAIT_MS);
  });
}

/**
  以後、あとから開いたタブの問い合わせに「居る」と返す。

  `onTaken` は**引き継がれたとき**に呼ぶ。引き継ぐ側が開き直すより先に
  こちらが手を引かないと、開き直した先で「まだ他のタブが居る」と
  見えてしまい、**いつまでも入れない。**
*/
export function answerPings(onTaken) {
  const ch = open_();
  if (!ch) return;
  ch.addEventListener("message", (event) => {
    const msg = event.data || {};
    if (msg.type === "ping") {
      ch.postMessage({ type: "pong", from: tabId() });
    } else if (msg.type === "taken" && msg.from !== tabId()) {
      if (onTaken) onTaken();
    }
  });
}

/** 引き継ぐ。**開き直す前に**、いま開いているタブへ知らせる。 */
export function announceTakeOver() {
  const ch = open_();
  if (ch) ch.postMessage({ type: "taken", from: tabId() });
}

/** 引き継いだので、以後は返事をしない ── 古いタブとして畳まれたとき。 */
export function stopAnswering() {
  const ch = open_();
  if (ch) { ch.close(); channel = false; }
}
