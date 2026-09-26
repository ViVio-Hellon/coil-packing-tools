/*
  master.js — マスタ管理(見るだけ)

  梱包資材総合ツールのマスタ管理の「中身を見る」を移したもの
  (総合ツールの `views/master.js`)。**直すところは移していない。**
  梱包資材マスタを直すのは総合ツールの役目。

  【判断はサーバが済ませてある】
  どの表を出すか・何行出すか・どう並べるかは `meisai/master_browse.py` が
  決める。ここは受け取ったものを並べ、押されたら読み直すだけ。

  【共有に触るのは開いたときだけ】
  このダイアログを開いたときと、押したときだけ読む。

  **CSV はダウンロードしない。こちらから Excel なども開かない**(現場の指定)
  ── 書き出し先の場所を出して、「場所をコピー」で写せるようにするだけ。
*/
import { api } from "./api.js";
import { waiting } from "./ui.js";

const $ = (id) => document.getElementById(id);

let view = null;          // サーバが返した最後の状態。**画面の唯一の出どころ**
let seq = 0;              // 最後に頼んだ読み込みの番号。**古い応えで描き直さない**
let options = { openPlace: () => {} };

/** `openPlace` は「設定で置き場所を変える」を押したときに呼ぶ(設定のその欄を開く)。 */
export function wire(given = {}) {
  options = { ...options, ...given };
  $("mFix").addEventListener("click", () => {
    $("masterDialog").close();
    options.openPlace();
  });
  $("btnMaster").addEventListener("click", open);
  $("btnMasterClose").addEventListener("click", () => $("masterDialog").close());
  $("btnMRowClose").addEventListener("click", () => $("masterRowDialog").close());

  $("mSources").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-source]");
    if (!button) return;
    // ファイルを変えたら絞り込みを外す。前の表の言葉で絞ったままにすると
    // 「0件」だけが出て、なぜそうなっているのか分からない
    $("mQuery").value = "";
    $("mOut").hidden = true;
    showExportNote("");
    load(button.dataset.source, "");
  });
  $("mTables").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-table]");
    if (!button || !view) return;
    $("mQuery").value = "";
    load(view.source, button.dataset.table);
  });
  $("mFind").addEventListener("click", () => reload());
  $("mReload").addEventListener("click", () => reload());
  $("mQuery").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); reload(); }
  });
  // 見出しで並べ替え。**サーバが並べ替えた結果を描き直すだけ**
  $("mHead").addEventListener("click", (event) => {
    const button = event.target.closest("button.sortbtn");
    if (button) sortBy(button.dataset.column);
  });
  $("mRows").addEventListener("click", (event) => {
    const tr = event.target.closest("tr[data-key]");
    if (tr) openRow(tr.dataset.key);
  });
  $("mExport").addEventListener("click", exportTable);
  // 場所を写すだけ。**こちらからフォルダや Excel は開かない**
  $("btnMCopyFolder").addEventListener("click", () => {
    navigator.clipboard?.writeText($("mOutFolder").textContent)
      .then(() => showExportNote("場所をコピーしました。"))
      .catch(() => {});
  });
}

/** 開くたびに読み直す(総合ツールで直した・ほかのラインが送った分が入る)。 */
function open() {
  $("masterDialog").showModal();
  if (view) reload();
  else load("master", "");
}

// ------------------------------------------------------------------
// 読む
// ------------------------------------------------------------------
async function load(source, table, sort = "", sortDir = "asc") {
  const my = ++seq;
  const params = new URLSearchParams({
    source: source || "", table: table || "", q: $("mQuery").value.trim(),
    sort: sort || "", sort_dir: sortDir || "asc",
  });
  // **読んでいる間は経過秒を出す。** 共有の応えしだいで数秒かかり、
  // 何も変わらないと押せていないと思って押し直す(現場の指摘)
  const stop = waiting($("mBusy"), "共有から読んでいます…");
  $("masterDialog").classList.add("is-loading");
  for (const id of ["mFind", "mReload", "mExport"]) $(id).disabled = true;
  try {
    const body = await api.get(`/api/master/browse?${params}`);
    if (my !== seq) return;
    render(body);
  } catch (err) {
    if (my !== seq) return;
    $("mError").textContent = err.message;
    $("mError").hidden = false;
    $("mFixRow").hidden = true;
  } finally {
    if (my === seq) {
      stop();
      $("mBusy").hidden = false;
      $("masterDialog").classList.remove("is-loading");
      $("mFind").disabled = $("mReload").disabled = false;
      $("mExport").disabled = !(view && view.table && !view.error);
    }
  }
}

/** いま出している表を、並べ替えを保ったまま読み直す。 */
function reload() {
  if (!view) return void load("master", "");
  load(view.source, view.table, view.sort, view.sort_dir);
}

/** 見出しを押した。同じ列なら向きを反転、違う列なら小さい順から。 */
function sortBy(column) {
  if (!view) return;
  const dir = (view.sort === column && view.sort_dir === "asc") ? "desc" : "asc";
  load(view.source, view.table, column, dir);
}

function text(value) {
  if (value === null || value === undefined) return "";
  return String(value);
}

function render(next) {
  view = next;

  // --- 見るファイル ---
  $("mSources").replaceChildren(...view.sources.map((s) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.source = s.key;
    button.textContent = s.label;
    button.setAttribute("aria-pressed", String(s.key === view.source));
    return button;
  }));
  const info = $("mSource");
  info.textContent = "";
  const line = (label, value) => {
    const div = document.createElement("div");
    div.append(label);
    if (value) {
      const code = document.createElement("code");
      code.textContent = value;
      div.append(code);
    }
    info.appendChild(div);
  };
  line(view.source_note);
  line("場所: ", view.path || view.folder);
  if (view.pending) {
    line(`この PC にまだ送っていない紙が${view.pending}枚あります`
         + "（送ると、ここに出ます。届けば自動で送ります）");
  }
  $("mError").textContent = view.error || "";
  $("mError").hidden = !view.error;
  // 置き場所の話なら、直す所へ飛べるボタン
  $("mFixRow").hidden = view.fix !== "share";

  // --- 左: 表の一覧 ---
  const items = view.tables.map((t) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.table = t.table;
    if (t.table === view.table) button.setAttribute("aria-current", "true");
    // 押す前に、その先に何があるかを示す
    if (t.note) button.title = t.note;
    const name = document.createElement("span");
    name.className = "nm";
    name.textContent = t.table;
    const count = document.createElement("span");
    count.className = "n";
    count.textContent = t.rows < 0 ? "?" : t.rows.toLocaleString();
    button.append(name, count);
    return button;
  });
  if (!items.length && !view.error) {
    const empty = document.createElement("p");
    empty.className = "stamp";
    empty.textContent = "表がありません。";
    items.push(empty);
  }
  $("mTables").replaceChildren(...items);

  // --- 右: 見出し ---
  $("mTitle").textContent = view.table || view.label;
  $("mCan").hidden = !view.table;
  $("mCount").textContent = view.table
    ? (view.query ? `絞り込み ${view.total.toLocaleString()}行` : `${view.total.toLocaleString()}行`)
    : "";
  $("mSub").textContent = view.table
    ? [view.table_note, `並び: ${view.order_label}（見出しを押すと並べ替え）`,
       "行を押すと1行の中身"].filter(Boolean).join("　")
    : "";
  $("mQuery").value = view.query || "";

  // --- 右: 表 ---
  const head = document.createElement("tr");
  for (const name of view.columns) {
    const th = document.createElement("th");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sortbtn";
    button.dataset.column = name;
    button.append(name);
    if (view.sort === name) {
      const mark = document.createElement("span");
      mark.className = "sortmark";
      // ▲▼は文字なので、色を拾えない環境でも並び順が読める
      mark.textContent = view.sort_dir === "asc" ? "▲" : "▼";
      button.append(mark);
      th.setAttribute("aria-sort", view.sort_dir === "asc" ? "ascending" : "descending");
    }
    th.append(button);
    head.appendChild(th);
  }
  $("mHead").replaceChildren(head);
  $("mRows").replaceChildren(...view.rows.map((row) => {
    const tr = document.createElement("tr");
    tr.dataset.key = String(row[view.row_key]);
    for (const name of view.columns) {
      const td = document.createElement("td");
      const value = text(row[name]);
      td.textContent = value;
      if (value.length > 24) td.title = value;
      tr.appendChild(td);
    }
    return tr;
  }));

  // 出しきれなかった分は**黙って落とさない**(文言はサーバが持つ)
  $("mNote").textContent = view.note || "";
  $("mNote").hidden = !view.note;
}

// ------------------------------------------------------------------
// 1行を開く(見るだけ)
// ------------------------------------------------------------------
function openRow(key) {
  if (!view) return;
  const row = view.rows.find((r) => String(r[view.row_key]) === String(key));
  if (!row) return;
  $("mRowTitle").textContent = `${view.table} の1行（見るだけ）`;
  const out = [];
  for (const name of view.columns) {
    const dt = document.createElement("dt");
    dt.textContent = name;
    const dd = document.createElement("dd");
    const value = row[name];
    if (value === null || value === undefined) {
      dd.className = "null";
      dd.textContent = "（空）";
    } else {
      dd.textContent = String(value);
    }
    out.push(dt, dd);
  }
  $("mRowFields").replaceChildren(...out);
  $("masterRowDialog").showModal();
}

// ------------------------------------------------------------------
// CSV に書き出す(書くだけ)
// ------------------------------------------------------------------
function showExportNote(message, bad = false) {
  const note = $("mExportNote");
  note.textContent = message || "";
  note.hidden = !message;
  note.className = bad ? "set-note bad" : "set-note";
}

async function exportTable() {
  if (!view || !view.table) return;
  $("mExport").disabled = true;
  $("mOut").hidden = true;
  showExportNote("書き出しています…");
  try {
    const body = await api.post("/api/master/export", {
      source: view.source, table: view.table, q: view.query || "",
      sort: view.sort || "", sort_dir: view.sort_dir || "asc",
    });
    $("mOutName").textContent = body.file;
    $("mOutFolder").textContent = body.folder;
    $("mOut").hidden = false;
    showExportNote(`${body.rows.toLocaleString()}行を書き出しました。${body.note || ""}`);
  } catch (err) {
    showExportNote(err.message, true);
  } finally {
    $("mExport").disabled = false;
  }
}
