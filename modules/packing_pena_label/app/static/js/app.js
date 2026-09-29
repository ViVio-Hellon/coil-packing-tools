/* 梱包ペナ ラベル・風袋計算 — 画面スクリプト
   追加ライブラリなし（素の DOM API のみ）。 */
(function () {
  "use strict";

  // ---------------------------------------------------------- 入口とトークン(統合版)
  // この機能の入口(統合版では `/pena`)と起動トークンは、外枠(_layout.html)の
  // body に載っている。**経路はここで1度だけ付け、トークンはヘッダに載せる**
  var BASE = (document.body && document.body.getAttribute("data-base")) || "";
  var TOKEN = (document.body && document.body.getAttribute("data-token")) || "";
  function url(path) { return BASE + path; }
  function headers(extra) {
    var h = { "X-Tool-Token": TOKEN };
    Object.keys(extra || {}).forEach(function (k) { h[k] = extra[k]; });
    return h;
  }
  window.pplUrl = url;
  window.pplHeaders = headers;

  // ---------------------------------------------------------- 共通
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var toastTimer = null;
  function hideToast() {
    var el = $("#toast");
    if (el) { el.hidden = true; }
    if (toastTimer) { clearTimeout(toastTimer); toastTimer = null; }
  }
  function toast(msg, level) {
    var el = $("#toast");
    if (!el) { return; }
    if (!msg) { hideToast(); return; }
    // 本文と閉じるボタンを分けて入れる（本文は textContent で安全に）
    el.textContent = "";
    var body = document.createElement("span");
    body.className = "toast-body";
    body.textContent = msg;
    var x = document.createElement("button");
    x.type = "button";
    x.className = "toast-x";
    x.title = "消す";
    x.textContent = "×";
    el.appendChild(body);
    el.appendChild(x);
    el.className = "toast no-print " + (level || "info");
    el.hidden = false;
    if (toastTimer) { clearTimeout(toastTimer); }
    toastTimer = setTimeout(hideToast, 7000);
  }
  window.pplToast = toast;

  // どこを押しても消せる（待たずに片付けたいときのため）
  (function () {
    var el = $("#toast");
    if (el) { el.addEventListener("click", hideToast); }
  })();

  function post(path, body) {
    var payload = body || {};
    // どの画面からの操作かを必ず名乗る（入力画面は同時に1つだけ）
    if (screenId && !payload.screenId) { payload.screenId = screenId; }
    return fetch(url(path), {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().catch(function () {
        throw new Error("サーバー応答を解釈できません (HTTP " + r.status + ")");
      });
    }).then(function (j) {
      // 使用権を失っていた（別の画面で「この画面で使う」が押された）
      if (j && j.screenTaken) { showLost(); }
      return j;
    });
  }
  window.pplPost = post;

  // ---------------------------------------------------------- 合言葉を聞く
  // **打った文字を見せない**(●で出す)。以前はブラウザの `prompt()` で聞いていて、
  // 合言葉がそのまま画面に見えていた(現場の指摘)。`prompt()` は伏せ字にできない。
  // 戻り値は Promise: 入れた合言葉、やめたら null。
  function askPassword(message) {
    return new Promise(function (resolve) {
      var dlg = document.createElement("dialog");
      dlg.className = "pwdialog";
      var p = document.createElement("p");
      p.className = "pwdialog-msg";
      p.textContent = message || "合言葉を入力してください";
      var box = document.createElement("input");
      box.type = "password";
      box.autocomplete = "off";
      box.className = "pwdialog-box";
      box.setAttribute("aria-label", "合言葉");
      var row = document.createElement("div");
      row.className = "btn-row";
      var ok = document.createElement("button");
      ok.type = "button"; ok.className = "btn btn-primary"; ok.textContent = "OK";
      var cancel = document.createElement("button");
      cancel.type = "button"; cancel.className = "btn"; cancel.textContent = "やめる";
      row.appendChild(ok); row.appendChild(cancel);
      dlg.appendChild(p); dlg.appendChild(box); dlg.appendChild(row);
      document.body.appendChild(dlg);
      var done = false;
      function finish(value) {
        if (done) { return; }
        done = true;
        try { dlg.close(); } catch (e) { /* 閉じていても構わない */ }
        dlg.remove();
        resolve(value);
      }
      ok.addEventListener("click", function () { finish(box.value); });
      cancel.addEventListener("click", function () { finish(null); });
      dlg.addEventListener("cancel", function (ev) { ev.preventDefault(); finish(null); });
      box.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { ev.preventDefault(); finish(box.value); }
      });
      dlg.showModal();
      box.focus();
    });
  }
  window.pplAskPassword = askPassword;

  // ---------------------------------------------------------- 印刷用のタブ
  // 帯の「印刷」「このタブを閉じる」(`pages._printbar`)。閉じられないブラウザ・
  // 開き方のときは、× で閉じるよう案内する(本ツールのタブはそのまま残っている)
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) { return; }
    if (t.closest("[data-print-now]")) { window.print(); return; }
    if (t.closest("[data-close-tab]")) {
      window.close();
      setTimeout(function () {
        if (!window.closed) {
          toast("このタブはブラウザの × で閉じてください。本ツールのタブはそのまま開いています。", "info");
        }
      }, 300);
    }
  });

  // ---------------------------------------------------------- 画面の多重起動
  // 作業状態（検査番号・重量・本数・計算結果）はサーバー側に1組しか無い。
  // 入力できる画面を2枚開くと両方が同じ1組を書き換え、
  // 画面に出ている検査番号と刷られる検査番号が食い違う。
  // そのため入力画面は同時に1つだけにし、2枚目は開くこと自体を断る。
  var guardEl = $("#screenguard");
  var guarded = !!(guardEl && guardEl.getAttribute("data-guard") === "1");
  var screenId = "";
  var beatTimer = null;

  function newScreenId() {
    // タブ単位で一意ならよい。暗号用途ではない。
    var rnd = "";
    try {
      var a = new Uint8Array(8);
      (window.crypto || window.msCrypto).getRandomValues(a);
      for (var i = 0; i < a.length; i++) {
        rnd += ("0" + a[i].toString(16)).slice(-2);
      }
    } catch (e) {
      rnd = String(Math.random()).slice(2);
    }
    return "s" + Date.now().toString(36) + rnd;
  }

  // sessionStorage はタブごとに分かれる（再読込では同じ画面のまま）。
  // localStorage だとタブ間で共有されてしまうので使わない。
  try {
    screenId = sessionStorage.getItem("pplScreenId") || "";
    if (!screenId) {
      screenId = newScreenId();
      sessionStorage.setItem("pplScreenId", screenId);
    }
  } catch (e) {
    screenId = newScreenId();       // プライベートウィンドウなど
  }

  function sgShow(which) {
    if (!guardEl) { return; }
    ["sgWait", "sgDeny", "sgLost"].forEach(function (id) {
      var el = $("#" + id);
      if (el) { el.hidden = (id !== which); }
    });
    guardEl.hidden = false;
  }

  function stopBeat() {
    if (beatTimer) { clearInterval(beatTimer); beatTimer = null; }
  }

  function showLost() {
    if (!guarded) { return; }
    lost = true;
    holding = false;
    stopBeat();
    sgShow("sgLost");
  }

  // 表に出ているか。裏のタブ・最小化したウィンドウでは false。
  function isVisible() {
    return !(document.hidden || document.visibilityState === "hidden");
  }

  var lost = false;                   // 別の画面に取られた（戻さない）
  var holding = false;                // この画面が使用権を持っている

  // 生存通知・表裏の知らせの返事を見て、画面を合わせる
  function onBeatReply(j) {
    if (!j || j.granted !== false) { return; }
    if (j.heldByOther) {
      showLost();                     // 別の画面に取られた
    } else {
      // 誰も使っていない（期限切れ・サーバーの再起動）。
      // 警告を出さずに黙って取り直す。
      claim(false);
    }
  }

  function beat() {
    post("/api/screen/ping", { visible: isVisible() })
      .then(onBeatReply)
      .catch(function () { /* 接続監視の担当 */ });
  }

  function startBeat() {
    stopBeat();
    beatTimer = setInterval(beat, 5000);
  }

  function claim(force) {
    sgShow("sgWait");
    return post("/api/screen/claim", { force: !!force, visible: isVisible() }).then(function (j) {
      if (j && j.granted) {
        lost = false;
        holding = true;
        guardEl.hidden = true;
        startBeat();
        return true;
      }
      var since = $("#sgSince");
      if (since) { since.textContent = (j && j.heldSince) || "-"; }
      // 相手の画面が裏に回っているなら、そう伝える（タブを探してもらう）
      var bg = $("#sgBg"), bgSince = $("#sgBgSince");
      if (bg) { bg.hidden = !(j && j.holderHidden); }
      if (bgSince) { bgSince.textContent = (j && j.hiddenSince) || "-"; }
      holding = false;
      sgShow("sgDeny");
      return false;
    }).catch(function () {
      // サーバーへ届かないなら接続監視に任せる（画面は塞いだまま）
      sgShow("sgWait");
      return false;
    });
  }

  if (guarded) {
    ["sgTake", "sgBack"].forEach(function (id) {
      var b = $("#" + id);
      if (b) { b.addEventListener("click", function () { claim(true); }); }
    });
    var retry = $("#sgRetry");
    if (retry) { retry.addEventListener("click", function () { claim(false); }); }

    claim(false);

    // ページが止められる直前でも届く送り方（sendBeacon → keepalive fetch）
    function beacon(path, obj) {
      obj.screenId = screenId;
      var body = JSON.stringify(obj);
      try {
        if (navigator.sendBeacon &&
            navigator.sendBeacon(url(path), new Blob([body], { type: "application/json" }))) {
          return;
        }
      } catch (e) { /* 下の fetch へ */ }
      try {
        fetch(url(path), {
          method: "POST", body: body, keepalive: true,
          headers: { "Content-Type": "application/json" }
        });
      } catch (e) { /* 届かなくても生存通知に表裏を載せている */ }
    }

    // ---- 裏に回る ----
    // ブラウザーは裏のタブのタイマーを間引く（Chrome / Edge は 5 分ほどで
    // 1 分に 1 回まで。眠ったタブ・PC のスリープでは止まる）。生存通知が
    // 途切れても空きとみなさないよう、裏に回る瞬間にサーバーへ知らせる。
    // 断られている画面・取られた画面は知らせない（使用中の画面だけ）
    function goingAway(reason) {
      if (!holding) { return; }
      beacon("/api/screen/state", { visible: false, reason: reason });
    }

    // ---- 表に戻る・凍結やスリープから戻る ----
    // タイマーを待たずに、すぐ生存を知らせて使用権と接続を確かめる。
    var lastWake = 0;
    function wake(reason) {
      if (!holding || !isVisible()) { return; }
      var now = Date.now();
      if (now - lastWake < 1000) { return; }     // 同時に来るイベントをまとめる
      lastWake = now;
      post("/api/screen/state", { visible: true, reason: reason })
        .then(onBeatReply)
        .catch(function () { /* 接続監視の担当 */ });
      startBeat();                                // 次の通知を今から数え直す
      if (typeof ping === "function") { ping(); } // 接続表示も今すぐ更新
    }

    document.addEventListener("visibilitychange", function () {
      if (isVisible()) { wake("visible"); } else { goingAway("hidden"); }
    });
    // Page Lifecycle（Chrome / Edge）: 眠ったタブの凍結と復帰
    document.addEventListener("freeze", function () { goingAway("freeze"); });
    document.addEventListener("resume", function () { wake("resume"); });
    window.addEventListener("focus", function () { wake("focus"); });
    window.addEventListener("online", function () { wake("online"); });

    // 戻る／進むで保存状態（bfcache）から戻った: 閉じるときに手放しているので取り直す
    window.addEventListener("pageshow", function (e) {
      if (e.persisted && !lost) { claim(false); }
    });

    // ---- スリープ復帰の検知 ----
    // PC がスリープするとタイマーごと止まる。表に出ているのに時計が
    // 大きく飛んでいたら、スリープ（または凍結）から戻ったとみなす。
    var lastTick = Date.now();
    setInterval(function () {
      var now = Date.now();
      var gap = now - lastTick;
      lastTick = now;
      if (gap > 30000 && isVisible()) { wake("sleep"); }
    }, 5000);

    // 閉じるときは手放す。fetch は間に合わないので sendBeacon を使う。
    window.addEventListener("pagehide", function () {
      if (!holding) { return; }
      beacon("/api/screen/release", {});
    });
  }

  // ---------------------------------------------------------- 画面の切替
  // 表示だけの面（風袋計算・羅列・計算内容・ラベル台紙）は、
  // 別タブを開かずに起動ページの中で差し替える。
  // 印刷ビューだけは今までどおり別タブ（用紙を替える作業があるため）。
  (function () {
    var home = $("#paneHome");
    var view = $("#paneView");
    if (!home || !view) { return; }            // 起動ページ以外では何もしない

    var links = $$(".appbar-nav a[data-pane]");

    function mark(name) {
      links.forEach(function (a) {
        a.classList.toggle("on", a.getAttribute("data-pane") === name);
      });
    }

    function runScripts(root) {
      // innerHTML で入れた <script> は動かないので入れ直す
      $$("script", root).forEach(function (old) {
        var s = document.createElement("script");
        if (old.src) { s.src = old.src; } else { s.textContent = old.textContent; }
        old.parentNode.replaceChild(s, old);
      });
    }

    function showHome() {
      view.hidden = true;
      view.innerHTML = "";
      home.hidden = false;
      mark("home");
      history.replaceState(null, "", BASE + "/");
    }

    function showPane(name, href) {
      home.hidden = true;
      view.hidden = false;
      view.innerHTML = '<div class="card"><div class="empty">読み込んでいます…</div></div>';
      mark(name);
      fetch(href + (href.indexOf("?") < 0 ? "?" : "&") + "pane=1",
            { cache: "no-store" })
        .then(function (r) { return r.text(); })
        .then(function (html) {
          view.innerHTML = html;
          runScripts(view);
          history.replaceState(null, "", href);
          window.scrollTo(0, 0);
        })
        .catch(function (err) {
          view.innerHTML = '<div class="card"><div class="empty">'
            + "読み込めませんでした: " + String(err) + "</div></div>";
        });
    }

    links.forEach(function (a) {
      a.addEventListener("click", function (e) {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) { return; }
        e.preventDefault();
        var name = a.getAttribute("data-pane");
        if (name === "home") { showHome(); } else { showPane(name, a.getAttribute("href")); }
      });
    });
  })();

  // ---------------------------------------------------------- 接続監視
  // 基盤仕様書 2.9: ブラウザーとバックエンドの状態を分けて扱う（監視レベル1）
  var connEl = $("#conn");
  function ping() {
    if (!connEl) { return; }
    fetch(url("/api/health"), { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.appId) {
          connEl.textContent = "接続OK";
          connEl.className = "conn ok";
        } else {
          throw new Error("別アプリ応答");
        }
      })
      .catch(function () {
        connEl.textContent = "接続なし — 再読込してください";
        connEl.className = "conn ng";
      });
  }
  ping();
  setInterval(ping, 15000);

  // ---------------------------------------------------------- 入力制限
  // VBA の KeyPress / Change 制限を再現
  function applyFilter(input) {
    var kind = input.getAttribute("data-filter");
    var v = input.value;
    var out = v;
    if (kind === "alnum") {
      // 0-9 A-Z a-z のみ。小文字は大文字へ（VBA KeyAscii - 32）
      out = v.replace(/[^0-9A-Za-z]/g, "").toUpperCase();
    } else if (kind === "num") {
      // VBA: If Not IsNumeric(text) Then text = ""（"-" 単独は許容）
      if (v !== "-" && v !== "" && !/^[+-]?(\d+\.?\d*|\.\d+)$/.test(v)) {
        out = v.slice(0, -1);
        if (out !== "-" && out !== "" && !/^[+-]?(\d+\.?\d*|\.\d+)$/.test(out)) { out = ""; }
      }
    } else if (kind === "coil") {
      // CoilH1..4: 数値でなければ空（"-" の例外なし）
      if (v !== "" && !/^\d+\.?\d*$/.test(v)) {
        out = v.slice(0, -1);
        if (out !== "" && !/^\d+\.?\d*$/.test(out)) { out = ""; }
      }
    }
    if (out !== v) { input.value = out; }
  }
  document.addEventListener("input", function (e) {
    if (e.target && e.target.hasAttribute && e.target.hasAttribute("data-filter")) {
      applyFilter(e.target);
    }
  });

  // ---------------------------------------------------------- メイン画面
  var form = $("#mainForm");
  if (!form) { return; }

  function collect() {
    var sel = $$("input[name=size]:checked")[0];
    var st = {
      selectedOb: 0, selectedCb: 0, namedCb: false,
      tip: ($$("input[name=tip]:checked")[0] || {}).value || "TIP1000",
      kensaNo: $("#kensaNo").value,
      weight1: $("#weight1").value,
      weight2: $("#weight2").value,
      coilH: {}
    };
    if (sel) {
      if (sel.dataset.kind === "ob") {
        st.selectedOb = parseInt(sel.dataset.ob, 10);
      } else if (sel.dataset.kind === "cbNamed") {
        st.namedCb = true;
      } else {
        st.selectedCb = parseInt(sel.dataset.cb, 10);
      }
    }
    [1, 2, 3, 4].forEach(function (i) {
      var el = $("#coilH" + i);
      st.coilH[String(i)] = el ? el.value : "";
    });
    return st;
  }

  function paint(state) {
    if (!state) { return; }
    $("#lblKensaNo").textContent = state.lblKensaNo || "";
    $("#lblSize1").textContent = state.lblSize1 || "";
    $("#lblWeight1").textContent = state.lblWeight1 || "";
    $("#lblSize2").textContent = state.lblSize2 || "";
    $("#lblWeight2").textContent = state.lblWeight2 || "";
    [1, 2, 3, 4].forEach(function (i) {
      var nw = $("#nw" + i), gw = $("#gw" + i), ta = $("#ta" + i), ch = $("#coilH" + i);
      if (nw) { nw.textContent = (state.nw && state.nw[i]) || ""; }
      if (gw) { gw.textContent = (state.gw && state.gw[i]) || ""; }
      if (ta) { ta.textContent = (state.ta && state.ta[i]) || ""; }
      if (ch && state.coilH && typeof state.coilH[i] === "string") { ch.value = state.coilH[i]; }
    });
    if (typeof state.kensaNo === "string") { $("#kensaNo").value = state.kensaNo; }
    applyTakeVisibility(state);
  }

  // VBA: Take1/Take2 の Visible と txtWeight1/2 の表示切替
  function applyTakeVisibility(state) {
    var t1 = state ? state.take1Visible : false;
    var t2 = state ? state.take2Visible : false;
    $("#take1").classList.toggle("off", !t1);
    $("#take2").classList.toggle("off", !t2);
    $("#w1wrap").style.opacity = t1 ? "" : ".4";
    $("#w2wrap").style.opacity = t2 ? "" : ".4";
    $("#weight1").disabled = !t1;
    $("#weight2").disabled = !t2;
    [1, 2].forEach(function (i) { var e = $("#coilH" + i); if (e) { e.disabled = !t1; } });
    [3, 4].forEach(function (i) { var e = $("#coilH" + i); if (e) { e.disabled = !t2; } });
  }

  function markSelectedRow() {
    $$(".size-row").forEach(function (row) {
      row.classList.toggle("on", !!$("input[name=size]:checked", row));
    });
  }

  // --- サイズ選択（VBA OptionButtonN_Click / CheckBoxN_Click）---
  form.addEventListener("change", function (e) {
    var t = e.target;
    if (t.name === "size") {
      markSelectedRow();
      var body;
      if (t.dataset.kind === "ob") {
        body = { obIdx: parseInt(t.dataset.ob, 10) };
      } else if (t.dataset.kind === "cbNamed") {
        body = { named: true };
      } else {
        body = { cbIdx: parseInt(t.dataset.cb, 10) };
      }
      body.current = collect();
      var url = (t.dataset.kind === "ob") ? "/api/select-size" : "/api/select-checkbox";
      post(url, body).then(function (j) {
        if (!j.ok) { toast(j.message, j.level || "error"); }
        paint(j.state);
      }).catch(function (err) { toast(String(err), "error"); });
    }
    if (t.id === "cbMode") {
      // 表示モードを切り替え、選択をいったん外す（VBA の排他と同じ考え方）
      document.body.classList.toggle("mode-cb", t.checked);
      $$("input[name=size]").forEach(function (r) {
        var isCb = (r.dataset.kind !== "ob");
        r.closest("label").hidden = (t.checked !== isCb);
        if (r.checked && (t.checked !== isCb)) { r.checked = false; }
      });
      $("#modeHint").textContent = t.checked
        ? "丈1・丈2 を同時に処理します（VBA の CheckBox モード）"
        : "丈1 または 丈2 を個別に処理します（VBA の OptionButton モード）";
      markSelectedRow();
    }
  });

  // --- 進み具合の棒(重量計算_DB) ---
  // 押してから答えが返るまで、サーバの進み具合(`/api/progress`)を 0.25 秒ごとに聞いて
  // 「何段目まで済んだか」を棒と文で出す(現場の指摘: 重いのはいいが進み具合を出すこと)。
  // すぐ終わるときに一瞬だけ出てちらつかないよう、0.3 秒たってから出す。
  function progressBar(title) {
    var box = null, timer = null, shown = null, stopped = false;
    function ensure() {
      if (box) { return box; }
      box = document.createElement("div");
      box.className = "pbar no-print";
      box.setAttribute("role", "progressbar");
      box.innerHTML = '<div class="pbar-card"><b class="pbar-title"></b>' +
        '<div class="pbar-track"><div class="pbar-fill"></div></div>' +
        '<div class="pbar-text"></div></div>';
      box.querySelector(".pbar-title").textContent = title + "中…";
      document.body.appendChild(box);
      return box;
    }
    function draw(p) {
      var el = ensure();
      var total = Math.max(1, p.total || 1), step = Math.min(p.step || 0, total);
      var pct = Math.round(step * 100 / total);
      el.querySelector(".pbar-fill").style.width = pct + "%";
      el.setAttribute("aria-valuenow", String(pct));
      el.querySelector(".pbar-text").textContent =
        (p.text || "始めています") + "(" + step + " / " + total + ")" +
        (p.elapsed ? "  " + p.elapsed + "秒" : "");
    }
    function poll() {
      if (stopped) { return; }
      fetch(url("/api/progress"), { cache: "no-store", headers: headers() })
        .then(function (r) { return r.json(); })
        .then(function (p) { if (!stopped && p && p.active) { draw(p); } })
        .catch(function () { /* 聞けなくても計算は続く */ })
        .then(function () { if (!stopped) { timer = setTimeout(poll, 250); } });
    }
    shown = setTimeout(function () { if (!stopped) { draw({ step: 0, total: 1 }); poll(); } }, 300);
    return {
      stop: function () {
        stopped = true;
        clearTimeout(shown); clearTimeout(timer);
        if (box) { box.remove(); box = null; }
      }
    };
  }

  // --- ボタン ---
  var ACTIONS = {
    applyWeight: function () {
      return post("/api/apply-weight", { current: collect() }).then(function (j) {
        toast(j.message, j.ok ? (j.level || "info") : (j.level || "warn"));
        paint(j.state);
      });
    },
    calcTare: function () {
      var bar = progressBar("重量計算_DB");
      return post("/api/calc-tare", { current: collect() }).then(function (j) {
        bar.stop();
        toast(j.message, j.ok ? (j.level || "info") : (j.level || "warn"));
        paint(j.state);
      }, function (e) { bar.stop(); throw e; });
    },
    clearAll: function () {
      if (!window.confirm("入力内容（NW・GW・高さ）と台紙の本数欄をクリアします。よろしいですか？")) {
        return Promise.resolve();
      }
      return post("/api/clear", { current: collect() }).then(function (j) {
        toast(j.message, "info");
        paint(j.state);
      });
    },
    printLabels: function () {
      return post("/api/print-targets", { current: collect() }).then(function (j) {
        if (!j.ok) { toast(j.message, "warn"); return; }
        if (!window.confirm(j.message)) { return; }
        window.open(url("/labels/print?ob=" + j.targets.join(",")), "_blank", "noopener");
      });
    },
    quickHeight: function () {
      var v = window.prompt("コイル積み数", "10");
      if (v === null || v === "") { return Promise.resolve(); }
      return post("/api/quick-height", { current: collect(), coilCount: v })
        .then(function (j) {
          if (!j.ok || !j.quick) { toast(j.message, "warn"); return; }
          showQuick(j.quick, v);
        });
    }
  };

  // 積み高さの結果は、消すまで出したままにする（作業中に見ていたい参考値）
  function showQuick(q, count) {
    var card = $("#quickCard");
    if (!card) { toast(q.message || "", "info"); return; }
    var set = function (id, text) {
      var el = $(id);
      if (el) { el.textContent = text; }
    };
    set("#qWidth", (q.width || "") + "mm × " + count + "本");
    set("#qTip", q.tip);
    set("#qOne", q.one);
    set("#qPallet", q.palletRoundUp + "（そのまま " + q.palletRaw + "）");
    card.hidden = false;
  }
  (function () {
    var x = $("#quickClose");
    if (x) {
      x.addEventListener("click", function () { $("#quickCard").hidden = true; });
    }
  })();

  form.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-act]");
    if (!btn) { return; }
    e.preventDefault();
    var fn = ACTIONS[btn.getAttribute("data-act")];
    if (!fn) { return; }
    btn.disabled = true;
    Promise.resolve(fn())
      .catch(function (err) { toast(String(err), "error"); })
      .then(function () { btn.disabled = false; });
  });

  // --- 初期表示 ---
  var boot = window.__PPL_STATE__ || null;
  if (boot) {
    document.body.classList.toggle("mode-cb", !!(boot.selectedCb || boot.namedCb));
    var cb = $("#cbMode");
    if (cb) {
      cb.checked = !!(boot.selectedCb || boot.namedCb);
      $$("input[name=size]").forEach(function (r) {
        var isCb = (r.dataset.kind !== "ob");
        r.closest("label").hidden = (cb.checked !== isCb);
      });
    }
    paint(boot);
  }
  markSelectedRow();
})();
