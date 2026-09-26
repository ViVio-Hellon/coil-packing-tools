/*
  shell.js — 統合画面の外枠のふるまい

  【ここがすること】
    ・タブの切り替え(iframe を見せる/隠す。**外さない**)
    ・アプリ全体の心拍(`/api/alive`)と生存確認(`/api/health`)
    ・終了ボタン

  【ここがしないこと】
    3機能の画面の中のこと。中の画面は自分の心拍・画面の見張り・
    入力チェックをそれぞれ持っていて、iframe の中でそのまま動く。

  【裏に回ったタブ】
  ブラウザは裏に回ったタブのタイマーを間引く(Chrome/Edge は5分後から
  1分に1回まで、Edge のスリープタブは止める)。裏に回るときに合図
  (`state: hidden`)を送っておけば、サーバは心拍の途切れで終わらない。
  中の画面も同じことをそれぞれ言うので、どちらか片方が届けば足りる。
*/
(function () {
  "use strict";

  var S = window.SHELL || {};
  var TOKEN = S.token || "";
  var TAB_KEY = "cpt.tab";
  var MISSES_BEFORE_OFFLINE = 2;
  var CLOCK_TICK_MS = 5000;
  var CLOCK_JUMP_MS = 30000;

  function $(id) { return document.getElementById(id); }

  // ---------------------------------------------------------- タブ
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  var panes = Array.prototype.slice.call(document.querySelectorAll(".pane"));

  function show(key) {
    var found = false;
    panes.forEach(function (p) {
      var on = (p.id === "pane-" + key);
      p.hidden = !on;
      found = found || on;
    });
    if (!found) return false;
    tabs.forEach(function (t) {
      t.setAttribute("aria-selected", t.dataset.key === key ? "true" : "false");
    });
    try { sessionStorage.setItem(TAB_KEY, key); } catch (e) { /* 私用ウィンドウ等 */ }
    if (location.hash !== "#" + key) history.replaceState(null, "", "#" + key);
    // 見せた画面へ焦点を移す(キーボードで操作している人のため)
    var frame = document.querySelector("#pane-" + key + " iframe");
    if (frame) { try { frame.focus(); } catch (e) { /* 無視 */ } }
    return true;
  }

  tabs.forEach(function (t) {
    t.addEventListener("click", function () { show(t.dataset.key); });
  });
  // ← → でタブを移る
  document.querySelector(".tabs").addEventListener("keydown", function (e) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    var i = tabs.findIndex(function (t) { return t.getAttribute("aria-selected") === "true"; });
    var next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    next.focus();
    show(next.dataset.key);
  });

  // 最初に見せるタブ: URL の #key → 前回のタブ → 先頭
  (function () {
    var want = (location.hash || "").slice(1);
    if (!want) { try { want = sessionStorage.getItem(TAB_KEY) || ""; } catch (e) { want = ""; } }
    if (!want || !show(want)) show(tabs.length ? tabs[0].dataset.key : "");
  })();
  window.addEventListener("hashchange", function () {
    var key = (location.hash || "").slice(1);
    if (key) show(key);
  });

  // ---------------------------------------------------------- 生存確認
  var misses = 0;
  var restarted = false;
  var stopped = false;

  function setOffline(on) { var el = $("offline"); if (el) el.hidden = !on || stopped; }
  function setConn(state, text) {
    var el = $("conn");
    if (!el) return;
    el.textContent = text;
    el.className = "conn " + state;
  }

  function checkRestart(pid) {
    if (!pid || !S.serverPid || pid === S.serverPid || restarted) return restarted;
    restarted = true;
    setOffline(false);
    var box = $("restarted");
    if (box) box.hidden = false;
    var reload = $("btnReload");
    if (reload) reload.addEventListener("click", function () { location.reload(); }, { once: true });
    return true;
  }

  function health() {
    return fetch("/api/health", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(function (j) {
        if (checkRestart(j.pid)) return true;
        misses = 0;
        setOffline(false);
        setConn("ok", "接続OK");
        return true;
      })
      .catch(function () {
        if (stopped) return false;
        if (++misses >= MISSES_BEFORE_OFFLINE) { setOffline(true); setConn("ng", "接続なし"); }
        return false;
      });
  }

  // ---------------------------------------------------------- 心拍
  function visibility() { return document.visibilityState === "hidden" ? "hidden" : "visible"; }

  function payload(extra) {
    var body = { state: visibility() };
    Object.keys(extra || {}).forEach(function (k) { body[k] = extra[k]; });
    return JSON.stringify(body);
  }

  function beacon(extra) {
    var body = new Blob([payload(extra)], { type: "application/json" });
    if (navigator.sendBeacon && navigator.sendBeacon("/api/alive", body)) return;
    fetch("/api/alive", { method: "POST", keepalive: true, cache: "no-store",
                          headers: { "Content-Type": "application/json" },
                          body: payload(extra) }).catch(function () {});
  }

  function alive(extra) {
    return fetch("/api/alive", { method: "POST", cache: "no-store",
                                 headers: { "Content-Type": "application/json" },
                                 body: payload(extra) })
      .then(function (r) { return r.json(); })
      .then(function (j) { checkRestart(j && j.pid); return j; })
      .catch(function () { return null; });
  }

  var resuming = 0;
  function resume(reason) {
    var now = Date.now();
    if (now - resuming < 1000) return;
    resuming = now;
    alive({ reason: reason }).then(function () { health(); });
  }

  document.addEventListener("visibilitychange", function () {
    if (visibility() === "hidden") beacon({ reason: "hidden" });
    else resume("foreground");
  });
  document.addEventListener("freeze", function () { beacon({ reason: "freeze" }); });
  document.addEventListener("resume", function () { resume("resume"); });
  window.addEventListener("focus", function () { resume("focus"); });
  window.addEventListener("online", function () { resume("online"); });
  window.addEventListener("pagehide", function (event) {
    if (stopped) return;
    if (event.persisted) beacon({ reason: "hidden" });
    else beacon({ leaving: true, reason: "close" });
  });
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) resume("restore");
  });

  // スリープからの戻りは時計の飛びで気づく
  var lastTick = Date.now();
  setInterval(function () {
    var now = Date.now();
    var jumped = now - lastTick > CLOCK_TICK_MS + CLOCK_JUMP_MS;
    lastTick = now;
    if (jumped) resume("sleep");
  }, CLOCK_TICK_MS);

  // ---------------------------------------------------------- 終了
  var quit = $("quit");
  if (quit) {
    quit.addEventListener("click", function () {
      if (!window.confirm("このアプリを終了します。3つの機能とも終わります。よろしいですか？")) return;
      quit.disabled = true;
      fetch("/api/shutdown", { method: "POST", cache: "no-store",
                               headers: { "Content-Type": "application/json", "X-Tool-Token": TOKEN },
                               body: "{}" })
        .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
        .then(function (res) {
          if (res.body && res.body.stopped) {
            stopped = true;
            setConn("", "終了しました");
            var box = $("stopped"); if (box) box.hidden = false;
            panes.forEach(function (p) { p.hidden = true; });
            return;
          }
          quit.disabled = false;
          window.alert((res.body && res.body.message) || ("終了できませんでした (HTTP " + res.status + ")"));
        })
        .catch(function () { quit.disabled = false; window.alert("終了の要求を送れませんでした。"); });
    });
  }

  // ---------------------------------------------------------- 開始
  health();
  setInterval(health, S.healthPollMs || 15000);
  alive({ reason: "open" });
  setInterval(function () { alive({ reason: "timer" }); }, S.alivePollMs || 20000);
})();
