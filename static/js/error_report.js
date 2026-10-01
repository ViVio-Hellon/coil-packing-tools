/*
  error_report.js — 画面(ブラウザ)で起きたエラーを記録する(統合アプリが差し込む)

  【なぜ要るのか】(統合 1.0.12。現場の指摘: エラー等の後追いが現状できない)
  画面の JavaScript が止まると、**押しても何も起きない**だけで、どこにも残らなかった。
  サーバのログには「要求が来なかった」ことしか残らないので、後から追えない。

  このスクリプトは次を `/api/client-log` へ送る:

    例外(window の error)・取りこぼした Promise の失敗 → エラーの記録(番号が返る)
    読み込めないファイル(script・css・画像)       → エラーの記録
    サーバに届かなかった通信(止まっていた・切れた) → つながったあとでまとめて1行

  エラーの記録ができたら、画面の隅に**エラー番号**を出す。現場の人がその番号を
  伝えれば、上の帯の「ログ」ですぐ中身を見られる。

  【各機能のコードは触らない】統合アプリが HTML の末尾に差し込む(app.py)。
  統合画面の外枠(base.html)は自分で読む。印刷には何も出さない。
*/
(function () {
  "use strict";
  if (window.__cptErrorReport) { return; }
  window.__cptErrorReport = true;

  var me = document.currentScript;
  function attr(name, fallback) {
    var v = me && me.getAttribute(name);
    return v ? v : fallback;
  }
  var TOKEN = attr("data-token", "");
  var MODULE = attr("data-key", "");
  var URL_LOG = "/api/client-log";
  var QUEUE_KEY = "cpt.offline.queue";
  var MAX_REPORTS = 10;                 // 1つの画面から送る上限(壊れた画面が送り続けない)
  var sent = 0, seen = {};
  var realFetch = window.fetch ? window.fetch.bind(window) : null;

  // 意味の無いもの(ブラウザの都合・中身の分からないもの)は送らない
  var IGNORE = [/ResizeObserver loop/i, /^Script error\.?$/i];

  function page() { return location.pathname + location.search; }

  function post(body) {
    if (!realFetch || !TOKEN) { return Promise.resolve(null); }
    body.module = MODULE;
    body.page = page();
    return realFetch(URL_LOG, {
      method: "POST", cache: "no-store", keepalive: true,
      headers: { "Content-Type": "application/json", "X-Tool-Token": TOKEN },
      body: JSON.stringify(body)
    }).then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  function report(body) {
    var msg = String(body.message || "");
    for (var i = 0; i < IGNORE.length; i++) { if (IGNORE[i].test(msg)) { return; } }
    var key = body.kind + "|" + msg + "|" + (body.source || "");
    if (seen[key] || sent >= MAX_REPORTS) { return; }
    seen[key] = true;
    sent += 1;
    post(body).then(function (j) {
      if (j && j.error_id) { notice(j.error_id); }
    });
  }

  // ---------------------------------------------------------- 画面の隅の知らせ
  function notice(id) {
    try {
      var box = document.getElementById("cpt-error-notice");
      if (!box) {
        box = document.createElement("div");
        box.id = "cpt-error-notice";
        box.setAttribute("role", "status");
        box.style.cssText = "position:fixed;left:12px;bottom:12px;z-index:2147483646;" +
          "max-width:min(520px,calc(100vw - 24px));padding:10px 36px 10px 12px;" +
          "background:#fff4f3;color:#5a1512;border:1px solid #e3a19c;border-left:4px solid #c3352f;" +
          "border-radius:6px;font:13px/1.6 'Meiryo UI',Meiryo,sans-serif;" +
          "box-shadow:0 2px 10px rgba(0,0,0,.18)";
        var style = document.createElement("style");
        style.textContent = "@media print{#cpt-error-notice{display:none!important}}";
        document.head.appendChild(style);
        document.body.appendChild(box);
      }
      box.textContent = "";
      var text = document.createElement("div");
      text.textContent = "画面でエラーが起きました。記録しました(エラー番号 " + id + ")。" +
        "動きがおかしいときは、読み込み直してください。続くときはこの番号を伝えてください。";
      var x = document.createElement("button");
      x.type = "button";
      x.textContent = "×";
      x.title = "閉じる";
      x.style.cssText = "position:absolute;right:6px;top:6px;border:0;background:none;" +
        "font-size:16px;cursor:pointer;color:inherit";
      x.addEventListener("click", function () { box.remove(); });
      box.appendChild(text);
      box.appendChild(x);
      if (window.top !== window) {
        var link = document.createElement("button");
        link.type = "button";
        link.textContent = "中身を見る";
        link.style.cssText = "margin-top:4px;padding:2px 10px;border:1px solid #c3352f;" +
          "background:#fff;color:#c3352f;border-radius:4px;cursor:pointer;font:inherit";
        link.addEventListener("click", function () {
          try { window.top.postMessage({ type: "cpt-open-log", id: id }, location.origin); }
          catch (e) { /* 開けなくても番号は出ている */ }
        });
        box.appendChild(link);
      }
      clearTimeout(box._timer);
      box._timer = setTimeout(function () { if (box.parentNode) { box.remove(); } }, 20000);
    } catch (e) { /* 知らせを出せなくても記録はできている */ }
  }

  // ---------------------------------------------------------- 例外・読み込めないファイル
  window.addEventListener("error", function (ev) {
    var t = ev.target;
    if (t && t !== window && (t.src || t.href)) {
      report({ kind: "resource", message: "読み込めませんでした: " + (t.tagName || "") +
               " " + (t.src || t.href), source: t.src || t.href });
      return;
    }
    var err = ev.error;
    report({ kind: "error", message: ev.message || (err && String(err)) || "不明なエラー",
             source: ev.filename || "", line: ev.lineno || "", col: ev.colno || "",
             stack: (err && err.stack) || "" });
  }, true);

  window.addEventListener("unhandledrejection", function (ev) {
    var r = ev.reason;
    var msg = (r && (r.message || String(r))) || "Promise の失敗";
    // 届かなかった通信(サーバが止まっていた)は下の「届かなかった通信」で数える
    if (r && r.name === "TypeError" && /fetch|network|Failed to fetch|NetworkError/i.test(msg)) {
      return;
    }
    report({ kind: "rejection", message: msg, stack: (r && r.stack) || "" });
  });

  // ---------------------------------------------------------- 届かなかった通信
  // サーバが止まっている間は送れないので、このタブに貯めて、つながったら送る
  function remember(url) {
    try {
      var q = JSON.parse(sessionStorage.getItem(QUEUE_KEY) || "{}");
      q[url] = q[url] || { count: 0, first: new Date().toLocaleString() };
      q[url].count += 1;
      q[url].last = new Date().toLocaleString();
      sessionStorage.setItem(QUEUE_KEY, JSON.stringify(q));
    } catch (e) { /* 貯められなくても画面は動く */ }
  }
  function flush() {
    var q;
    try { q = JSON.parse(sessionStorage.getItem(QUEUE_KEY) || "{}"); }
    catch (e) { return; }
    var keys = Object.keys(q);
    if (!keys.length) { return; }
    try { sessionStorage.removeItem(QUEUE_KEY); } catch (e) { /* 次に送る */ }
    keys.forEach(function (url) {
      post({ kind: "offline", source: url, count: q[url].count,
             message: q[url].first + " 〜 " + q[url].last });
    });
  }
  function sameOrigin(input) {
    try {
      var u = new URL(typeof input === "string" ? input : input.url, location.href);
      return u.origin === location.origin ? u.pathname : "";
    } catch (e) { return ""; }
  }
  if (realFetch) {
    window.fetch = function (input, init) {
      var path = sameOrigin(input);
      return realFetch(input, init).then(function (res) {
        if (path && path !== URL_LOG) { flush(); }
        return res;
      }, function (err) {
        if (path && path !== URL_LOG && !(err && err.name === "AbortError")) { remember(path); }
        throw err;
      });
    };
  }
})();
