/*
  health.js — 生存監視と心拍 (基盤仕様書 2.8 / 2.9)

  ブラウザ画面が開いていることと、バックエンドが動いていることは別。
  切れたら**画面に明示して**、黙って古い表示を出し続けない。

  【裏に回ったタブの心拍は止まる】
  ブラウザは裏に回ったタブのタイマーを間引く。Chrome は5分を過ぎると
  **1分に1回**まで、Edge のスリープタブは**完全に止める**。PCのスリープ
  ではサーバごと止まる。そのままだと、

    ・心拍が途切れて、裏にいるあいだにアプリが終わる
    ・前に戻った瞬間、自分の画面から締め出される(他のタブは無いのに)

  が起きていた(実測)。そこで、

    裏に回るとき   … その場で「裏に回った」と伝える(sendBeacon)。
                     サーバはその間、終了も空き判定もしない
    前に戻ったとき … すぐ心拍を送り、アプリが生きているか・開き直され
                     ていないかを確かめる
    スリープから   … 時計の飛びで気づいて、同じことをする
*/

// 何回続けて失敗したら「切れた」と見なすか。
// 1回の取りこぼしで赤帯を出すと、かえって信用されなくなる
const MISSES_BEFORE_OFFLINE = 2;

// 時計の見張り。この間隔で刻み、**刻みが大きく飛んだら止まっていた**
// (スリープ・凍結)とみなす。裏では間引かれて1分に1回になるが、
// そのときも心拍を1回送るだけなので害は無い
const CLOCK_TICK_MS = 5000;
const CLOCK_JUMP_MS = 30000;

// 前に戻った直後はネットワークがまだ戻っていないことがある。
// **すぐ赤帯を出さず**、間を空けて送り直す(合わせて約10秒)
const RESUME_RETRY_MS = [1000, 3000, 6000];

// この機能の入口(統合版では `/details`)
const BASE = window.APP.base || "";

let misses = 0;
let onLost = null;        // 画面を引き継がれたときに呼ぶ
let lost = false;
let restarted = false;

function setOffline(offline) {
  const banner = document.getElementById("offline");
  if (banner) banner.hidden = !offline;
}

function visibility() {
  return document.visibilityState === "hidden" ? "hidden" : "visible";
}

/*
  アプリが開き直されていた。**この画面のトークンはもう通らない**ので、
  読み込み直してもらう。

  引き継がれた(`holds:false`)より**先に**見る ── 開き直されたプロセスには
  持ち主が居ないので、心拍には `holds:false` が返る。そちらで判断すると
  「別のタブに引き継がれました」と、事実と違う理由を出してしまう。
*/
function checkRestart(pid) {
  const mine = window.APP.serverPid;
  if (!pid || !mine || pid === mine || restarted) return restarted;
  restarted = true;
  setOffline(false);
  const box = document.getElementById("restarted");
  if (box) box.hidden = false;
  // 下の画面は**消さずに止める**。消すと「何が起きたのか」が分からない
  document.querySelector(".wrap")?.setAttribute("inert", "");
  document.getElementById("btnReload")
    ?.addEventListener("click", () => location.reload(), { once: true });
  return true;
}

async function beat() {
  try {
    // **トークンは付けない。** `/api/health` は素通しの経路で、
    // まだトークンを知らない相手(起動待機画面・多重起動の判定)も叩く
    const res = await fetch(BASE + "/api/health", { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    const body = await res.json().catch(() => ({}));
    if (checkRestart(body.pid)) return true;
    if (misses >= MISSES_BEFORE_OFFLINE) setOffline(false);
    misses = 0;
    return true;
  } catch {
    if (++misses === MISSES_BEFORE_OFFLINE) setOffline(true);
    return false;
  }
}

function payload(extra) {
  // **画面の名前と、いま裏か前かを必ず乗せる。** 持ち主かどうかの確認、
  // 閉じたときに空けること、裏での終了の抑え ── を、この1本に相乗り
  // させている
  return JSON.stringify({ screen: window.APP.screen || "",
                          state: visibility(), ...(extra || {}) });
}

function handleAliveResponse(out) {
  if (!out) return;
  if (checkRestart(out.pid)) return;
  // **持ち主でなくなった。** 別のタブが引き継いでいるので、
  // この画面に出ている値はもう別のロットのもの
  if (out.holds === false && !lost) { lost = true; if (onLost) onLost(); }
}

/* 閉じる・裏に回る瞬間の合図。**ブラウザが送りきってくれる**(sendBeacon)。
   そのあと凍結されても、合図だけは届く */
function beacon(extra) {
  const body = new Blob([payload(extra)], { type: "application/json" });
  if (navigator.sendBeacon && navigator.sendBeacon(BASE + "/api/alive", body)) return;
  fetch(BASE + "/api/alive", { method: "POST", keepalive: true, cache: "no-store",
                        headers: { "Content-Type": "application/json" },
                        body: payload(extra) }).catch(() => {});
}

/*
  こちらが生きていることを伝える。

  **窓が無いアプリなので、タブを閉じたら終わったつもりになる。**
  ところが Python は動いたままで、次の起動が「すでに起動しています」と
  判定し、入れ替えた新しい版がいつまでも動かない。
*/
function alive(extra) {
  return fetch(BASE + "/api/alive", {
    method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json" }, body: payload(extra),
  }).then((res) => res.json())      // 読まないと接続を抱え続ける
    .then((out) => { handleAliveResponse(out); return out; })
    .catch(() => null);             // 届かなくても画面は続く。次の心拍で取り戻す
}

/** 操作が 409 で断られたときにも、心拍を待たずに閉じられるように。 */
export function screenLost() {
  if (lost) return;
  lost = true;
  if (onLost) onLost();
}

/*
  前に戻った・スリープから戻った。

  **すぐ心拍を送る。** タイマーに任せると、間引かれた分だけ遅れる。
  続けてアプリが生きているか・開き直されていないかを確かめる。
  ネットワークが戻りきっていないことがあるので、失敗しても少し待って
  確かめ直してから赤帯を出す。

  前に戻ると `visibilitychange`・`focus`・`resume` がまとめて来るので、
  1秒以内の重なりは1回にまとめる。
*/
let resuming = 0;
async function resume(reason) {
  const now = Date.now();
  if (now - resuming < 1000) return;
  resuming = now;
  let out = await alive({ reason });
  // 裏のまま時計が飛んだだけなら、心拍1回で足りる
  if (visibility() === "hidden" || restarted) return;
  for (const wait of RESUME_RETRY_MS) {
    if (out) { await beat(); return; }       // 届いた。赤帯を消す(開き直しも見る)
    await new Promise((r) => setTimeout(r, wait));
    if (restarted) return;
    out = await alive({ reason });
  }
  if (!out) setOffline(true);                // 10秒送り直しても届かない
}

export function start(opts) {
  onLost = (opts && opts.onLost) || null;
  beat();
  setInterval(beat, window.APP.healthPollMs || 15000);

  alive({ reason: "open" });
  setInterval(() => alive({ reason: "timer" }), window.APP.alivePollMs || 20000);

  // --- 裏に回る・前に戻る -------------------------------------------
  document.addEventListener("visibilitychange", () => {
    if (visibility() === "hidden") beacon({ reason: "hidden" });
    else resume("foreground");
  });
  // 凍結の直前(Chrome のページライフサイクル)。裏に回ったあとに来るが、
  // 念のためもう一度言っておく。凍結が解けたら `resume`
  document.addEventListener("freeze", () => beacon({ reason: "freeze" }));
  document.addEventListener("resume", () => resume("resume"));
  window.addEventListener("focus", () => resume("focus"));
  window.addEventListener("online", () => resume("online"));

  // --- 閉じた / 戻る見込みのある離脱 ---------------------------------
  // 閉じた・再読込したときは「閉じた」。再読込でも飛ぶが、**戻ってくれば
  // 次の心拍で取り消される**(サーバが猶予を持っている)。
  // `persisted` は「閉じずにしまっておく」(戻る・進むの控え)。戻ってくる
  // 見込みがあるので、閉じたことにせず「裏に回った」と言う
  window.addEventListener("pagehide", (event) => {
    if (event.persisted) beacon({ reason: "hidden" });
    else beacon({ leaving: true, reason: "close" });
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) resume("restore");
  });

  // --- スリープからの戻り --------------------------------------------
  // スリープ中は何のイベントも来ない。**時計の飛び**で気づく
  let lastTick = Date.now();
  setInterval(() => {
    const now = Date.now();
    const jumped = now - lastTick > CLOCK_TICK_MS + CLOCK_JUMP_MS;
    lastTick = now;
    if (jumped) resume("sleep");
  }, CLOCK_TICK_MS);
}
