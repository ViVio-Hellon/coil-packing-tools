/*
  log.js — ログとエラーの記録(/log)。統合画面の上の帯の「ログ」の中身。

  エラーの記録   一覧 → 押すと中身(なぜなぜ分析の記入欄つき)。番号・言葉で絞る
  今日のログ     終わりのほう(警告とエラーだけ / すべて)
  出力先の設定   出力先・残す日数。確かめる → 保存(すぐ切り替わる)
                 配布設定(共通): 書き出す・読み込み直す・消す(管理者パスワード。統合 1.0.14)
*/
(function () {
  "use strict";
  var TOKEN = (window.LOGPAGE || {}).token || "";
  function $(id) { return document.getElementById(id); }
  function api(path, body) {
    var opt = { headers: { "X-Tool-Token": TOKEN }, cache: "no-store" };
    if (body !== undefined) {
      opt.method = "POST";
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return { ok: false, message: "応答を読めません" }; });
    });
  }
  function td(text, cls) {
    var c = document.createElement("td");
    c.textContent = text == null ? "" : String(text);
    if (cls) c.className = cls;
    return c;
  }

  // ---------------------------------------------------------- 面の切り替え
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  function show(name) {
    tabs.forEach(function (t) {
      var on = t.dataset.tab === name;
      t.setAttribute("aria-selected", on ? "true" : "false");
      $("panel-" + t.dataset.tab).hidden = !on;
    });
    if (name === "today") loadToday();
    if (name === "settings") { loadStatus(); loadDist(); }
    if (name === "incidents") loadIncidents();
  }
  tabs.forEach(function (t) { t.addEventListener("click", function () { show(t.dataset.tab); }); });

  // ---------------------------------------------------------- エラーの記録
  var items = [];
  function renderIncidents() {
    var q = $("incQuery").value.trim().toLowerCase();
    var body = $("incRows");
    body.textContent = "";
    var shown = items.filter(function (it) {
      return !q || (it.id + " " + it.when + " " + it.module + " " + it.title + " " + it.kind)
        .toLowerCase().indexOf(q) >= 0;
    });
    if (!shown.length) {
      var tr = document.createElement("tr");
      tr.appendChild(td(items.length ? "当たる記録はありません" : "エラーの記録はまだありません",
                        "muted"));
      tr.firstChild.colSpan = 4;
      body.appendChild(tr);
      return;
    }
    shown.forEach(function (it) {
      var tr = document.createElement("tr");
      tr.tabIndex = 0;
      tr.dataset.id = it.id;
      tr.appendChild(td(it.id, "mono"));
      tr.appendChild(td(it.when, "mono"));
      tr.appendChild(td(it.module || "―"));
      tr.appendChild(td(it.title + (it.repeats ? "(ほか " + it.repeats + " 回)" : "")));
      tr.addEventListener("click", function () { openIncident(it.id); });
      tr.addEventListener("keydown", function (e) { if (e.key === "Enter") openIncident(it.id); });
      body.appendChild(tr);
    });
  }
  function loadIncidents() {
    return api("/api/log/incidents").then(function (j) {
      items = (j && j.items) || [];
      $("incFolder").textContent = j && j.folder ? "置き場所: " + j.folder : "";
      renderIncidents();
    });
  }
  function openIncident(id) {
    Array.prototype.forEach.call(document.querySelectorAll("#incRows tr"), function (tr) {
      tr.classList.toggle("on", tr.dataset.id === id);
    });
    return api("/api/log/incidents/" + encodeURIComponent(id)).then(function (j) {
      var box = $("incDetail");
      box.hidden = false;
      $("incTitle").textContent = j.ok ? id : "読めません";
      $("incPath").textContent = j.ok ? j.path : (j.message || "");
      $("incText").textContent = j.ok ? j.text : "";
      $("incCopy").dataset.path = j.ok ? j.path : "";
    });
  }
  $("incQuery").addEventListener("input", renderIncidents);
  $("incReload").addEventListener("click", loadIncidents);
  $("incCopy").addEventListener("click", function () {
    var p = this.dataset.path || "";
    if (p && navigator.clipboard) navigator.clipboard.writeText(p).catch(function () {});
  });

  // ---------------------------------------------------------- 今日のログ
  function loadToday() {
    var only = (document.querySelector("input[name=only]:checked") || {}).value || "";
    return api("/api/log/recent?limit=400&only=" + encodeURIComponent(only)).then(function (j) {
      $("todayPath").textContent = j.path || "";
      var lines = j.lines || [];
      $("todayText").textContent = lines.length ? lines.join("\n")
        : (only ? "今日は警告もエラーもありません" : "今日のログはまだありません");
      var pre = $("todayText");
      pre.scrollTop = pre.scrollHeight;
    });
  }
  $("todayReload").addEventListener("click", loadToday);
  Array.prototype.forEach.call(document.querySelectorAll("input[name=only]"), function (r) {
    r.addEventListener("change", loadToday);
  });

  // ---------------------------------------------------------- 出力先の設定
  function result(text, ok) {
    var el = $("result");
    el.hidden = !text;
    el.textContent = text || "";
    el.className = "result " + (ok ? "result--ok" : "result--ng");
  }
  function fill(s) {
    $("nowDir").textContent = s.dir || "";
    $("nowFile").textContent = s.today_file || "";
    $("defDir").textContent = s.default_dir || "";
    $("pcName").textContent = s.pc_name || "";
    $("dirInput").placeholder = "空なら このPCの既定: " + (s.default_dir || "");
    $("keepNote").textContent = "既定 " + s.keep_days_default + " 日(" + s.keep_days_min +
                                "〜" + s.keep_days_max + ")";
    $("problem").hidden = !s.problem;
    $("problem").textContent = s.problem ? "設定どおりに書けていません: " + s.problem : "";
    $("envNote").hidden = !s.env_override;
    $("envNote").textContent = s.env_override
      ? "環境変数 COIL_PACKING_TOOLS_LOG_DIR が入っているため、そちらに出しています(試験用)" : "";
  }
  // 保存してある値を出す。defaultValue にも置き、保存していない直しを見分ける(統合 1.2.5)
  function shown(id, value) {
    var el = $(id);
    el.value = String(value === undefined || value === null ? "" : value);
    el.defaultValue = el.value;
  }
  // 統合画面の「終了」・窓の × ・外からの停止の前に、統合画面が訊く
  window.cptUnsaved = function () {
    var dir = $("dirInput"), keep = $("keepInput");
    return (dir.value !== dir.defaultValue || keep.value !== keep.defaultValue)
      ? ["ログ: 出力先・残す日数(保存していない)"] : [];
  };
  function loadStatus() {
    return api("/api/log/status").then(function (s) {
      if (!s.ok) { result(s.message || "読めませんでした", false); return; }
      fill(s);
      shown("dirInput", s.configured || "");
      shown("keepInput", s.keep_days);
    });
  }
  function send(action) {
    var body = { action: action, log_dir: $("dirInput").value, keep_days: $("keepInput").value };
    result("確かめています…", true);
    return api("/api/log/settings", body).then(function (j) {
      result(j.message || (j.error && j.error.message) || "", !!j.ok);
      if (j.ok && action !== "check") {
        fill(j);
        shown("dirInput", j.configured || "");
        if (j.keep_days !== undefined) shown("keepInput", j.keep_days);
      }
    });
  }
  $("btnCheck").addEventListener("click", function () { send("check"); });
  $("btnSave").addEventListener("click", function () { send("save"); });
  $("btnReset").addEventListener("click", function () {
    $("dirInput").value = "";
    send("reset");
  });

  // ---------------------------------------------------------- 配布設定(共通)
  function distResult(text, ok) {
    var el = $("distResult");
    el.hidden = !text;
    el.textContent = text || "";
    el.className = "result " + (ok ? "result--ok" : "result--ng");
  }
  function list(rows, empty) {
    var dd = document.createElement("span");
    if (!rows.length) { dd.textContent = empty; return dd; }
    var ul = document.createElement("ul");
    rows.forEach(function (r) {
      var li = document.createElement("li");
      li.textContent = r;
      ul.appendChild(li);
    });
    return ul;
  }
  function fillDist(d) {
    $("distWhere").textContent = d.where || d.path || "";
    var now = $("distNow");
    now.textContent = "";
    if (d.exists) {
      now.appendChild(list(d.contents.map(function (c) { return c.label + ": " + c.value; }), ""));
      var made = document.createElement("span");
      made.className = "muted";
      made.textContent = "作成 " + (d.created_at || "?") + "(" + (d.created_on || "?") + ")";
      now.appendChild(made);
    } else {
      now.textContent = d.problem ? "読めません" : "まだありません";
    }
    $("distMine").textContent = "";
    $("distMine").appendChild(list(d.items.map(function (i) { return i.label + ": " + i.value; }), ""));
    $("distApplied").textContent = d.applied_at || "―";
    var bad = [d.problem].concat((d.skipped || []).map(function (s) {
      return "形が違うので読まないもの: " + s;
    })).filter(Boolean);
    $("distProblem").hidden = !bad.length;
    $("distProblem").textContent = bad.join(" / ");
    $("btnDistReapply").disabled = !d.exists;
    $("btnDistRemove").disabled = !d.exists && !d.problem;
  }
  function loadDist() {
    return api("/api/log/distribution").then(function (d) {
      if (d.ok) fillDist(d);
      else distResult(d.message || (d.error && d.error.message) || "読めませんでした", false);
    });
  }
  function distSend(action) {
    var pw = $("distPassword").value;
    if (!pw) {
      distResult("管理者パスワード(梱包明細と同じ)を入れてください", false);
      $("distPassword").focus();
      return Promise.resolve();
    }
    if (action === "remove" && !window.confirm("配布設定(共通)を消します。このPCの設定はそのままです。"))
      return Promise.resolve();
    if (action === "reapply" &&
        !window.confirm("配布設定の値で、このPCのログの出力先・残す日数を上書きします。"))
      return Promise.resolve();
    distResult("処理しています…", true);
    return api("/api/log/distribution", { action: action, password: pw }).then(function (j) {
      distResult(j.message || (j.error && j.error.message) || "", !!j.ok);
      if (j.field === "password") { $("distPassword").select(); return; }
      $("distPassword").value = "";
      if (j.distribution) fillDist(j.distribution);
      if (j.ok && action === "reapply" && j.log) {
        fill(j.log);
        $("dirInput").value = j.log.configured || "";
        $("keepInput").value = j.log.keep_days;
      }
    });
  }
  $("btnDistExport").addEventListener("click", function () { distSend("export"); });
  $("btnDistReapply").addEventListener("click", function () { distSend("reapply"); });
  $("btnDistRemove").addEventListener("click", function () { distSend("remove"); });

  // ---------------------------------------------------------- 最初
  var wanted = new URLSearchParams(location.search).get("id");
  loadIncidents().then(function () { if (wanted) openIncident(wanted); });
})();
