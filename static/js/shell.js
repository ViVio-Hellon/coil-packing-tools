/*
  shell.js — 統合画面の外枠のふるまい

  【ここがすること】
    ・タブの切り替え(iframe を見せる/隠す。**外さない**)
    ・アプリ全体の心拍(`/api/alive`)と生存確認(`/api/health`)
    ・終了ボタン
    ・Ctrl+P で、いま見せている機能の画面を刷る(外枠を刷らない)

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

  // ---------------------------------------------------------- 版の一覧
  // 統合ツールの版と3機能の版は分けて持つ。帯の版を押すと一覧が出る
  (function () {
    var btn = $("verBtn"), panel = $("verPanel");
    if (!btn || !panel) return;
    function set(open) {
      panel.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
    btn.addEventListener("click", function (e) { e.stopPropagation(); set(panel.hidden); });
    document.addEventListener("click", function (e) {
      if (!panel.hidden && !panel.contains(e.target)) set(false);
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") set(false); });
    // 機能の画面(iframe)を押すと外枠の文書には click が来ないので、窓から焦点が外れたら閉じる
    window.addEventListener("blur", function () { set(false); });
  })();

  // ---------------------------------------------------------- 画面の色(統合 1.0.16)
  // 上の帯の「自動 / ライト / ダーク」。サーバに保存(このPC)し、この画面と、開いている
  // 画面すべて(3機能の iframe・ログ・別のタブの帳票)をその場で切り替える。
  // 各画面は差し込まれた theme.js が BroadcastChannel を受けて data-theme を付け替える。
  // 使えないブラウザに備えて、統合画面の中の iframe は直接も付け替える(同じ origin)。
  (function () {
    var box = $("themeSwitch");
    if (!box) return;
    var buttons = Array.prototype.slice.call(box.querySelectorAll("[data-theme-set]"));
    var channel = null;
    try { channel = new BroadcastChannel("cpt-theme"); } catch (e) { channel = null; }
    function applyTo(doc, theme) {
      try {
        var root = doc.documentElement;
        if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
        else root.removeAttribute("data-theme");
      } catch (e) { /* 読み込み中の枠は飛ばす(開いたときにサーバが付ける) */ }
    }
    function show(theme) {
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed", b.dataset.themeSet === theme ? "true" : "false");
      });
    }
    function apply(theme) {
      applyTo(document, theme);
      Array.prototype.forEach.call(document.querySelectorAll("iframe"), function (f) {
        try { applyTo(f.contentDocument, theme); } catch (e) { /* 別の origin は触らない */ }
      });
      if (channel) channel.postMessage({ type: "theme", theme: theme });
      show(theme);
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        var theme = b.dataset.themeSet;
        var before = (buttons.filter(function (x) {
          return x.getAttribute("aria-pressed") === "true"; })[0] || {}).dataset;
        apply(theme);                                   // 押したらすぐ変える
        fetch("/api/theme", {
          method: "POST", cache: "no-store",
          headers: { "Content-Type": "application/json", "X-Tool-Token": TOKEN },
          body: JSON.stringify({ theme: theme })
        }).then(function (r) {
          if (!r.ok) throw new Error(String(r.status));
        }).catch(function () {
          // 保存できなければ元へ戻す(次に開いた画面と食い違わせない)
          if (before && before.themeSet) apply(before.themeSet);
          window.alert("画面の色を保存できませんでした。もう一度押してください。");
        });
      });
    });
  })();

  // ---------------------------------------------------------- ログとエラーの記録
  // 上の帯の「ログ」。中身は /log(出力先の設定・エラーの一覧)。開くたびに読み直す。
  // 機能の画面の隅に出たエラー番号の「中身を見る」からも開く(postMessage。同じ origin だけ)
  (function () {
    var btn = $("logBtn"), dlg = $("logDialog"), body = $("logBody"), close = $("logClose");
    if (!btn || !dlg || !body) return;
    var frame = null;
    function open(id) {
      if (!frame) {
        frame = document.createElement("iframe");
        frame.className = "logdlg__frame";
        frame.title = "ログとエラーの記録";
        body.appendChild(frame);
      }
      frame.src = "/log?embed=1&t=" + encodeURIComponent(window.SHELL.token) +
                  (id ? "&id=" + encodeURIComponent(id) : "");
      if (!dlg.open) { try { dlg.showModal(); } catch (e) { dlg.setAttribute("open", ""); } }
    }
    btn.addEventListener("click", function () { open(""); });
    if (close) close.addEventListener("click", function () { dlg.close(); });
    window.addEventListener("message", function (e) {
      if (e.origin !== location.origin) return;
      var d = e.data || {};
      if (d.type === "cpt-open-log") open(String(d.id || ""));
    });
  })();

  // ---------------------------------------------------------- 操作説明書(統合 1.2.0)
  // 上の帯の「説明書」。**いま見ているタブのツールの説明書**(写真入り)を開く。
  // 中身は /manual/<機能>(ダウンロードにしない。このまま読む)。説明書どうしは中で行き来できる。
  // 「別の窓で開く」は、いまダイアログに出ている説明書を別の窓(デスクトップ版)・別のタブ
  // (ブラウザ版)で開き直す。画面と並べて読むため。
  (function () {
    var btn = $("manualBtn"), dlg = $("manualDialog"), body = $("manualBody");
    var pop = $("manualPop"), close = $("manualClose");
    if (!btn || !dlg || !body) return;
    var frame = null;
    function currentKey() {
      var pane = panes.filter(function (p) { return !p.hidden; })[0];
      return pane ? pane.id.replace(/^pane-/, "") : "";
    }
    function open() {
      if (!frame) {
        frame = document.createElement("iframe");
        frame.className = "logdlg__frame";
        frame.title = "操作説明書";
        body.appendChild(frame);
      }
      frame.src = "/manual/" + encodeURIComponent(currentKey()) + "?embed=1";
      if (!dlg.open) { try { dlg.showModal(); } catch (e) { dlg.setAttribute("open", ""); } }
    }
    function shownPath() {
      // ダイアログの中で別の説明書へ移っていれば、そちらを開く(同じ origin なので読める)
      try {
        var loc = frame.contentWindow.location;
        if (loc.pathname.indexOf("/manual/") === 0) return loc.pathname + loc.hash;
      } catch (e) { /* 読めなければ、いまのタブの説明書 */ }
      return "/manual/" + encodeURIComponent(currentKey());
    }
    btn.addEventListener("click", open);
    if (close) close.addEventListener("click", function () { dlg.close(); });
    if (pop) pop.addEventListener("click", function () {
      var path = frame ? shownPath() : "/manual/" + encodeURIComponent(currentKey());
      window.open(path, "_blank");
      dlg.close();
    });
  })();

  // ---------------------------------------------------------- 印刷(Ctrl+P)
  // 3機能の画面は iframe の中にある。ブラウザの Ctrl+P は**一番外の文書**
  // (この外枠)を刷るので、そのままでは見出しとタブと、iframe の見えている
  // 部分だけが紙に出る。**Ctrl+P は、いま見せている機能の画面を刷る**
  // (移植元で Ctrl+P を押したときと同じ紙面)。機能の画面の中で押しても同じ。
  // 各機能の「印刷」ボタン(帳票を別のタブに出す・その画面で window.print)は
  // これまでどおりで、ここは関わらない。
  function currentFrame() {
    var pane = panes.filter(function (p) { return !p.hidden; })[0];
    return pane ? pane.querySelector("iframe") : null;
  }
  function isPrintKey(e) {
    return (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey &&
           (e.key === "p" || e.key === "P");
  }
  function printCurrent(e) {
    if (!isPrintKey(e)) return;
    var frame = currentFrame();
    var win = frame && frame.contentWindow;
    if (!win) return;                          // 画面が無ければブラウザに任せる
    e.preventDefault();
    e.stopPropagation();
    try { win.focus(); win.print(); } catch (err) { window.print(); }
  }
  document.addEventListener("keydown", printCurrent, true);
  // 機能の画面の中で押されたときも拾う(同じアプリなので中の文書に触れる)。
  // 画面を移る(iframe の中で別のページを開く)たびに文書が替わるので、読み込むたびに付け直す
  panes.forEach(function (p) {
    var frame = p.querySelector("iframe");
    if (!frame) return;
    function attach() {
      try { frame.contentWindow.document.addEventListener("keydown", printCurrent, true); }
      catch (err) { /* 触れない文書(起きないはず)はブラウザに任せる */ }
    }
    frame.addEventListener("load", attach);
    attach();
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
        // 止めた直後は、まだ答えが返ることがある(止めるのは少し遅れる)。
        // 「終了しました」を「接続OK」で上書きしない
        if (stopped) return true;
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

  // この画面の名乗り。**開くたびに新しい名前**にする(タブを複製すると
  // sessionStorage も写るので、そこには置かない)。見張りは名乗りごとに
  // 生き死にを持つので、別のタブ(ペナラベルの印刷ビュー)が開いていれば
  // この画面を閉じてもサーバは終わらない
  var CLIENT = "shell-" + Date.now().toString(36) + "-" +
               Math.random().toString(36).slice(2, 10);

  function payload(extra) {
    var body = { state: visibility(), client: CLIENT };
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
  function markStopped() {
    stopped = true;
    setConn("", "終了しました");
    var box = $("stopped"); if (box) box.hidden = false;
    panes.forEach(function (p) { p.hidden = true; });
    if (quit) quit.disabled = true;
  }
  // 機能の画面の「終了」(資材計算の帯)で止まったときも、同じ表示にする。
  // 知らせが無いと、外枠は「バックエンドと通信できません」を出していた
  window.addEventListener("message", function (e) {
    if (e.origin !== location.origin || !e.data || e.data.type !== "cpt:stopped") return;
    markStopped();
  });

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
            markStopped();
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
  // 同じタブの前の名乗り。ブラウザが裏のタブを捨てて読み直したとき、前の名乗りは
  // 閉じた合図を出せないまま「裏に回ったまま」で残る(全部閉じても終わらなくなる)。
  // 開いたときに言っておけば、サーバが忘れる(`IdleWatch.forget`)
  var PREV_KEY = "cpt.shell.client", previous = "";
  try { previous = sessionStorage.getItem(PREV_KEY) || ""; sessionStorage.setItem(PREV_KEY, CLIENT); }
  catch (e) { previous = ""; }
  alive({ reason: "open", replaces: previous });
  setInterval(function () { alive({ reason: "timer" }); }, S.alivePollMs || 20000);
})();
