/*
  tab_alive.js — 別のタブで開いた画面の心拍(統合アプリが差し込む)

  【なぜ要るのか】
  3機能とも**印刷は別のタブ**で開く。

    梱包明細  帳票(`/details/report/…`)。刷る前に紙面で書き足したサイズ・
              LOTNO を**サーバへ保存する**。右上の文字も開き直すたびに聞き直す
    ペナラベル 印刷ビュー(`/pena/…/print`)。バーコードのフォントもサーバから読む
    資材計算  チェックリスト・発注票(`/material/report/…`)。発注票は
              **サーバが覚えている中身**から組む(サーバが止まると出せない)

  統合アプリは、画面が全部居なくなると自分で終わる(common/idle_exit.py)。
  統合画面のタブを閉じても、開いたままの帳票・印刷ビューがあるあいだは
  終わってはいけない ── そこで**別のタブで開いた画面も自分で心拍を送る**。
  見張りは画面の名乗りごとに数えるので、どれか1枚でも開いていれば終わらない。

  【統合画面の中(iframe)では何もしない】
  外枠(shell.js)が心拍を送っているので、ここで送ると二重になる。

  【各機能のコードは触らない】
  このスクリプトは統合アプリが HTML の末尾に差し込む(app.py)。画面にも
  紙面にも何も描かないので、印刷の見た目は変わらない。
*/
(function () {
  "use strict";
  if (window.top !== window) { return; }          // 統合画面の中

  var me = document.currentScript;
  function attr(name, fallback) {
    var v = me && me.getAttribute(name);
    return v ? v : fallback;
  }
  var ALIVE_MS = parseInt(attr("data-alive-ms", ""), 10) || 20000;
  var URL_ALIVE = attr("data-url", "/api/alive");
  // 名乗りは**開くたびに新しい名前**(タブを複製しても別の画面として数える)
  var CLIENT = "tab-" + attr("data-key", "page") + "-" + Date.now().toString(36) + "-" +
               Math.random().toString(36).slice(2, 10);

  function state() { return document.visibilityState === "hidden" ? "hidden" : "visible"; }
  function body(extra) {
    var b = { client: CLIENT, state: state() };
    Object.keys(extra || {}).forEach(function (k) { b[k] = extra[k]; });
    return JSON.stringify(b);
  }

  // ふつうの心拍
  function alive(reason, extra) {
    var b = { reason: reason };
    Object.keys(extra || {}).forEach(function (k) { b[k] = extra[k]; });
    try {
      fetch(URL_ALIVE, {
        method: "POST", cache: "no-store", body: body(b),
        headers: { "Content-Type": "application/json" }
      }).catch(function () { /* 届かなければ次の心拍で */ });
    } catch (e) { /* 同上 */ }
  }

  // 閉じる・裏に回る瞬間でも届く送り方
  function signal(extra) {
    var text = body(extra);
    try {
      if (navigator.sendBeacon &&
          navigator.sendBeacon(URL_ALIVE, new Blob([text], { type: "application/json" }))) {
        return;
      }
    } catch (e) { /* 下の fetch へ */ }
    try {
      fetch(URL_ALIVE, { method: "POST", keepalive: true, body: text,
                         headers: { "Content-Type": "application/json" } });
    } catch (e) { /* 届かなくても次の心拍で直る */ }
  }

  // 同じタブの前の名乗り(ブラウザが裏のタブを捨てて読み直したとき。shell.js と同じ)
  var PREV_KEY = "cpt.tab." + attr("data-key", "page") + ".client", previous = "";
  try { previous = sessionStorage.getItem(PREV_KEY) || ""; sessionStorage.setItem(PREV_KEY, CLIENT); }
  catch (e) { previous = ""; }
  alive("open", { replaces: previous });
  setInterval(function () { alive("timer"); }, ALIVE_MS);
  document.addEventListener("visibilitychange", function () {
    if (state() === "visible") { alive("foreground"); } else { signal({ reason: "hidden" }); }
  });
  document.addEventListener("freeze", function () { signal({ reason: "freeze" }); });
  document.addEventListener("resume", function () { alive("resume"); });
  window.addEventListener("pagehide", function (e) {
    // 保存状態(bfcache)へ入るだけなら「裏に回った」、本当に閉じたら「閉じた」
    if (e.persisted) { signal({ reason: "hidden" }); }
    else { signal({ leaving: true, reason: "close" }); }
  });
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) { alive("restore"); }
  });
})();
