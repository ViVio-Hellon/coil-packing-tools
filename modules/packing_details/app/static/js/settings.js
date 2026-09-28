/*
  settings.js — 設定画面

  【欄ごとに保存する】(現場の指摘: ボタンが1つだと混乱のもと)
  仕掛台帳のフォルダ・梱包課共有の仕掛フォルダは、それぞれ「保存して取り込み」
  (その欄の取り込み元だけを取り込む)。梱包資材マスタのフォルダ(共有)と
  紙面の右上の文字は「保存」(管理者パスワード)。タブで分ける:

      置き場所・取り込み / 紙面の右上の文字 / 管理者パスワード / 配布設定

  【開いてすぐ出す】(現場の指摘: 押しても反応が鈍い)
  この端末の設定(`/api/settings`)は共有を見に行かないので、すぐ返る。
  共有の様子と取り込み元が届くか(`/api/settings/share`)は後から埋める。
  埋まるまでは、共有に関わるボタンを止めておく。

  **判断はサーバ。** ここは受け取ったものを並べ、押されたら送るだけ。
*/
import { api, ApiError } from "./api.js";
import { importWithProgress, showNote, waiting } from "./ui.js";

const $ = (id) => document.getElementById(id);

// 開いたままの紙面へ知らせる口。紙面のページ(`report._QA_SCRIPT`)と
// **同じ名前**で聞いている
const PRINT_CHANNEL = "meisai.print.v1";

let hooks = { showError: (err) => console.error(err), afterImport: () => {} };
let local = null;          // この端末の設定(最後に受け取ったもの)
let shareKnown = false;    // 共有の様子を受け取ったか
let qaMax = 20;
let openSeq = 0;           // 最後に開いた回。**古い応えで描き直さない**
let lastTab = "place";

const FOLDERS = {
  lot: { key: "lot_db_dir", input: "setLotDir", note: "setLotResult", button: "btnLotSave",
         label: "仕掛台帳" },
  konpo: { key: "konpo_db_dir", input: "setKonpoDir", note: "setKonpoResult",
           button: "btnKonpoSave", label: "梱包課共有の仕掛（LS4LOT）" },
};

// ------------------------------------------------------------------
// 組み立て
// ------------------------------------------------------------------
export function wire(given) {
  hooks = { ...hooks, ...given };
  $("btnSettings").addEventListener("click", () => open());
  $("btnSettingsClose").addEventListener("click", () => $("settingsDialog").close());

  const tabs = [...document.querySelectorAll("#settingsDialog [role=tab]")];
  for (const tab of tabs) {
    tab.addEventListener("click", () => selectTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const i = tabs.indexOf(tab) + (event.key === "ArrowRight" ? 1 : -1);
      const next = tabs[(i + tabs.length) % tabs.length];
      selectTab(next.dataset.tab);
      next.focus();
    });
  }

  $("btnLotSave").addEventListener("click", () => saveFolder("lot"));
  $("btnKonpoSave").addEventListener("click", () => saveFolder("konpo"));
  $("btnShareSave").addEventListener("click", saveShareDir);
  $("btnExportSave").addEventListener("click", saveExportDir);
  $("btnQaSave").addEventListener("click", () => saveQa(false));
  $("btnQaReset").addEventListener("click", () => saveQa(true));
  $("setQaValue").addEventListener("input", countQa);
  // パスワード欄で Enter なら「保存」。**ダイアログを閉じない**
  for (const [id, fn] of [["setQaPassword", () => saveQa(false)],
                          ["setSharePassword", saveShareDir],
                          ["setAdminConfirm", saveAdminPassword]]) {
    $(id).addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); fn(); }
    });
  }
  $("btnAdminSave").addEventListener("click", saveAdminPassword);
  $("btnDistExport").addEventListener("click", () => sendDist("export"));
  $("btnDistReapply").addEventListener("click", () => sendDist("reapply"));
  $("btnDistRemove").addEventListener("click", () => {
    if (window.confirm("配布設定（アプリのフォルダの「配布設定」）を消します。"
                       + "\nこの端末の設定はそのままです。よろしいですか?")) sendDist("remove");
  });
}

function selectTab(name) {
  lastTab = name || "place";
  for (const tab of document.querySelectorAll("#settingsDialog [role=tab]")) {
    const on = tab.dataset.tab === lastTab;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
    $(tab.getAttribute("aria-controls")).hidden = !on;
  }
}

/**
 * 設定を開く。`tab` で開く面、`focus` で入力欄を選べる
 * (マスタ管理の「置き場所を変える」から梱包資材マスタのフォルダへ飛ぶ)。
 */
export async function open({ tab = "", focus = "" } = {}) {
  const seq = ++openSeq;
  const dialog = $("settingsDialog");
  for (const id of ["setQaValue", "setQaPassword", "setAdminCurrent", "setAdminNew",
                    "setAdminConfirm", "setSharePassword", "distPassword"]) {
    $(id).value = "";
  }
  for (const id of ["setLotResult", "setKonpoResult", "setExportResult", "setShareNote", "setQaNote",
                    "setAdminNote", "distNote"]) {
    showNote(id, "");
  }
  selectTab(tab || lastTab);
  shareKnown = false;
  setShareButtons();
  // **先に開く。** 中身は後から埋める
  if (!dialog.open) dialog.showModal();

  let stop = waiting($("setLoading"), "設定を読んでいます…");
  try {
    const body = await api.get("/api/settings");
    if (seq !== openSeq) return;
    renderLocal(body, { fill: true });
  } catch (err) {
    stop();
    if (seq === openSeq) failed(err);
    return;
  }
  stop();
  if (focus) { $(focus).focus(); $(focus).select?.(); }

  // 共有の様子・取り込み元が届くか。**待つのはサーバが区切る**(数秒まで)
  stop = waiting($("setLoading"), "共有フォルダと取り込み元を確かめています…");
  try {
    const share = await api.get("/api/settings/share");
    if (seq !== openSeq) return;
    renderShare(share);
  } catch (err) {
    if (seq === openSeq) failed(err);
  } finally {
    stop();
  }
}

function failed(err) {
  if (err instanceof ApiError && err.code === "screen_taken") {
    $("settingsDialog").close();
    hooks.showError(err);
    return;
  }
  const el = $("setLoading");
  el.hidden = false;
  el.textContent = `読めませんでした: ${err.message}`;
}

// ------------------------------------------------------------------
// 描く
// ------------------------------------------------------------------
function renderStamps(stamps, tables, boxId, problem = "") {
  const box = $(boxId);
  box.textContent = "";
  for (const s of stamps.filter((x) => tables.includes(x.table))) {
    const line = document.createElement("div");
    // **届くかどうかと、取り込んであるかどうかは別。** 共有が切れて
    // いても手元のデータは使えるので、両方を出す
    const reach = s.found === null || s.found === undefined ? "確かめています"
      : (s.found ? "届く" : "届かない");
    line.className = s.found === false ? "miss" : (s.found ? "ok" : "");
    line.textContent = `${s.table}  ${reach}  ${s.as_of || "まだ取り込んでいません"}`
      + (s.imported ? `  ${s.count.toLocaleString()}件` : "");
    box.appendChild(line);
  }
  if (problem) {
    const line = document.createElement("div");
    line.className = "miss";
    line.textContent = `※ ${problem}`;
    box.appendChild(line);
  }
}

/** この端末の設定。`fill` なら入力欄にも入れる(保存した直後は打った値を残す)。 */
function renderLocal(body, { fill = false } = {}) {
  local = body;
  if (fill) {
    $("setLotDir").value = body.lot_db_dir_setting || "";
    $("setKonpoDir").value = body.konpo_db_dir_setting || "";
    $("setShareDir").value = body.share_setting || "";
    $("setExportDir").value = body.export_dir_setting || "";
  }
  // **このフォルダに何を探しているか。** 道だけ出しても、そこへ
  // 何を置けばよいかが分からない
  $("setLotFiles").textContent = `探すファイル: ${body.lot_files.join(" / ")}`;
  $("setKonpoFiles").textContent = `探すファイル: ${body.konpo_files.join(" / ")}`;
  $("setShareFiles").textContent =
    `ここにあるもの: ${body.master_db_name} / ${body.history_db_name} / ${body.share_file}`;
  $("setLotNote").textContent = `いま見ている場所: ${body.lot_db_dir}`;
  $("setKonpoNote").textContent = `いま見ている場所: ${body.konpo_db_dir}`;
  $("setLotDir").placeholder = `空なら既定: ${body.lot_db_dir_default}`;
  $("setKonpoDir").placeholder = `空なら既定: ${body.konpo_db_dir_default}`;
  $("setShareDir").placeholder = `空なら既定: ${body.share_default}`;
  $("setShareNow").textContent = `いま見ている場所: ${body.share_dir}`;
  $("setExportNow").textContent = `いまの出力先: ${body.export_dir}`;
  $("setExportDir").placeholder = `空なら既定: ${body.export_dir_default}`;
  $("setPaths").textContent = `手元のDB: ${body.db_path}\nログ: ${body.log_dir}`;
  $("setAdminMin").textContent = `${body.admin_min_length}文字以上`;
  qaMax = body.qa_mark_max || qaMax;
  countQa();
  // 届くかどうかを確かめたあとは、ここ(見に行かない鮮度)で描き直さない
  if (!shareKnown) {
    renderStamps(body.stamps, body.lot_tables, "setLotStamps");
    renderStamps(body.stamps, body.konpo_tables, "setKonpoStamps");
  }
  renderDist(body.distribution);
  if (!shareKnown) {
    $("setQaNow").textContent = "…";
    $("setQaKind").textContent = "（共有を確かめています）";
    $("setShare").className = "set-share";
    $("setShare").textContent = "共有フォルダを確かめています…";
  }
}

function renderShare(body) {
  shareKnown = true;
  if (local) {
    renderStamps(body.stamps, local.lot_tables, "setLotStamps", body.reach_problem);
    renderStamps(body.stamps, local.konpo_tables, "setKonpoStamps", body.reach_problem);
  }
  renderQa(body);
}

function setShareButtons(reach = false, stuck = false) {
  for (const id of ["btnQaSave", "btnQaReset"]) $(id).disabled = !reach || stuck;
  $("btnAdminSave").disabled = !reach;
}

function renderQa(body) {
  qaMax = body.qa_mark_max || qaMax;
  $("setQaNow").textContent = body.qa_mark;
  const from = { master: "梱包資材マスタから", json: "控えのJSONから",
                 default: "既定" }[body.qa_origin] || "";
  $("setQaKind").textContent = body.qa_mark_custom
    ? `（${from}。既定は ${body.qa_mark_default}）`
    : `（${from === "既定" ? "既定" : `${from}・既定と同じ`}）`;
  $("setQaValue").placeholder = body.qa_mark;
  $("setShareNow").textContent = `いま見ている場所: ${body.share_dir || body.share_path}`
    + (body.share_source === "shared" ? "（届く）" : "（届かない）");

  // **どこから読んだ値か。** 届かなければ変えさせない(手元だけ変えると
  // ラインごとに違う紙が出る)。ボタンを止めて、理由を出す
  const box = $("setShare");
  const reach = body.share_source === "shared";
  box.className = reach ? "set-share" : "set-share bad";
  box.textContent = "";
  const line = (text) => {
    const div = document.createElement("div");
    div.textContent = text;
    box.appendChild(div);
  };
  line(`共有: ${body.share_path}`);
  if (reach) {
    // **正は梱包資材マスタの表。** 無ければ控えのJSON。どちらで刷っているかを出す
    const table = `表「${body.share_master_table}」`;
    const master = {
      ok: `正: 梱包資材マスタの${table}（ここから刷っています）`,
      no_table: `正: 梱包資材マスタに${table}がありません → 控えのJSONを見ています`,
      no_row: `正: ${table}に ID=1 の行がありません → 控えのJSONを見ています`,
      no_file: "正: 梱包資材マスタが見つかりません → 控えのJSONを見ています",
      error: `正: 梱包資材マスタを読めません → 控えのJSONで刷っています（${body.share_master_problem}）`,
    }[body.share_master_state] || "";
    if (master) line(master);
    if (body.share_master_note) line(`※ ${body.share_master_note}`);
    line(body.share_updated_at
      ? `控え: ${body.share_json.split(/[\\/]/).pop()}（このアプリで最後に変えた: ${body.share_updated_at.slice(0, 16)}　${body.share_updated_by}）`
      : "控え: まだありません（最初に変えたとき作ります）");
    if (body.share_json_problem) line(`※ 控えのJSONを読めません: ${body.share_json_problem}`);
  } else {
    line(`共有に届きません: ${body.share_problem}`);
    line(body.share_source === "cache"
      ? `この端末が ${body.share_read_at.slice(0, 16)} に読んだ値で刷っています。変えられません。`
      : "既定の文字で刷っています。変えられません。");
    line("置き場所は「置き場所・取り込み」の「梱包資材マスタのフォルダ（共有）」で変えられます。");
  }
  // マスタが読めないときも変えさせない(JSONにだけ書くと、あとで黙って戻る)
  const stuck = reach && body.share_master_state === "error";
  if (stuck) box.className = "set-share bad";
  if (body.share_setting) line("※ この端末は梱包資材マスタのフォルダを差し替えています");
  setShareButtons(reach, stuck);
}

/** 文字数を出す。**打てる数を切らない**(`maxlength` は貼り付けを黙って削る)。 */
function countQa() {
  const n = [...$("setQaValue").value.trim()].length;
  const el = $("setQaCount");
  el.textContent = n ? `${n} / ${qaMax}文字` : `${qaMax}文字まで`;
  el.style.color = n > qaMax ? "var(--danger)" : "";
}

/** 開いたままの紙面に知らせる。刷る前に差し替わる。 */
function announceQa(value) {
  if (!("BroadcastChannel" in window)) return;
  const ch = new BroadcastChannel(PRINT_CHANNEL);
  ch.postMessage({ type: "qa_mark", value });
  ch.close();
}

function refused(err, noteId) {
  if (!(err instanceof ApiError) || err.code === "screen_taken") {
    if (err.code === "screen_taken") $("settingsDialog").close();
    hooks.showError(err);
    return false;
  }
  showNote(noteId, err.message, true);
  return true;
}

async function refreshShare() {
  try { renderShare(await api.get("/api/settings/share")); } catch { /* 出せるところまで */ }
}

// ------------------------------------------------------------------
// 置き場所(欄ごとに保存して取り込み)
// ------------------------------------------------------------------
async function saveFolder(kind) {
  const f = FOLDERS[kind];
  const button = $(f.button);
  button.disabled = true;
  showNote(f.note, "");
  try {
    const saved = await api.post("/api/settings", { key: f.key, value: $(f.input).value });
    // **保存したら、その欄の分だけ取り込む。** 置き場所を変えただけでは何も起きない
    const body = await importWithProgress({ force: true, only: kind },
                                          `${f.label} を取り込み中`);
    showNote(f.note, [saved.note, body.summary, body.note, ...body.errors]
      .filter(Boolean).join("\n"), !body.ok);
    hooks.afterImport(body);
    const next = await api.get("/api/settings");
    renderLocal(next);
    // 取り込みの応答は、届くかどうかを確かめた鮮度を持っている
    renderStamps(body.stamps, next.lot_tables, "setLotStamps");
    renderStamps(body.stamps, next.konpo_tables, "setKonpoStamps");
  } catch (err) {
    if (refused(err, f.note) && err.field === "value") $(f.input).focus();
  } finally {
    button.disabled = false;
  }
}

/** CSV の出力先。**書けるかをサーバが確かめてから**保存する。取り込みはしない */
async function saveExportDir() {
  const button = $("btnExportSave");
  button.disabled = true;
  showNote("setExportResult", "");
  try {
    const saved = await api.post("/api/settings", { key: "export_dir", value: $("setExportDir").value });
    showNote("setExportResult", saved.note || "保存しました。");
    renderLocal(await api.get("/api/settings"));
  } catch (err) {
    if (refused(err, "setExportResult") && err.field === "value") $("setExportDir").focus();
  } finally {
    button.disabled = false;
  }
}

async function saveShareDir() {
  const button = $("btnShareSave");
  button.disabled = true;
  showNote("setShareNote", "");
  const stop = waiting($("setLoading"), "新しい置き場所を確かめています…");
  try {
    const body = await api.post("/api/settings/shared-dir", {
      value: $("setShareDir").value,
      password: $("setSharePassword").value,
    });
    $("setSharePassword").value = "";
    renderQa(body);
    showNote("setShareNote", body.message, body.share_source !== "shared");
    announceQa(body.qa_mark);
    renderLocal(await api.get("/api/settings"));
    renderQa(body);
  } catch (err) {
    if (!refused(err, "setShareNote")) return;
    if (err.field === "password") {
      $("setSharePassword").value = "";
      $("setSharePassword").focus();
    } else {
      $("setShareDir").focus();
    }
  } finally {
    stop();
    button.disabled = false;
  }
}

// ------------------------------------------------------------------
// 紙面の右上の文字 (既定 NLM.NAGOYA.QA。管理者パスワードが要る)
// ------------------------------------------------------------------
async function saveQa(reset) {
  const password = $("setQaPassword").value;
  const data = reset ? { reset: true, password }
                     : { value: $("setQaValue").value, password };
  try {
    const body = await api.post("/api/settings/qa-mark", data);
    renderQa(body);
    $("setQaValue").value = "";
    $("setQaPassword").value = "";
    countQa();
    showNote("setQaNote", body.message);
    announceQa(body.qa_mark);
  } catch (err) {
    if (!refused(err, "setQaNote")) return;
    // 共有に届かなくなっていた。**様子を出し直す**(ボタンも止まる)
    if (err.code === "shared_unreachable") { refreshShare(); return; }
    // 直す欄へ。パスワードが違えば**打ち直させる**(残すと同じ誤りを繰り返す)
    if (err.field === "password") {
      $("setQaPassword").value = "";
      $("setQaPassword").focus();
    } else {
      $("setQaValue").focus();
    }
  }
}

// ------------------------------------------------------------------
// 管理者パスワード
// ------------------------------------------------------------------
async function saveAdminPassword() {
  try {
    const body = await api.post("/api/settings/admin-password", {
      current: $("setAdminCurrent").value,
      new: $("setAdminNew").value,
      confirm: $("setAdminConfirm").value,
    });
    for (const id of ["setAdminCurrent", "setAdminNew", "setAdminConfirm"]) {
      $(id).value = "";
    }
    renderQa(body);
    showNote("setAdminNote", body.message);
  } catch (err) {
    if (!refused(err, "setAdminNote")) return;
    if (err.code === "shared_unreachable") { refreshShare(); return; }
    $(err.field === "current" ? "setAdminCurrent" : "setAdminNew").focus();
  }
}

// ------------------------------------------------------------------
// 配布設定(`meisai/distribution.py`)
// ------------------------------------------------------------------
function renderDist(d) {
  if (!d) return;
  const skipped = d.skipped || [];
  // 置いてあるのに読めない(壊れた・形が違う)ときは**「なし」と言わない**
  $("distState").textContent = d.exists ? "あり" : (d.problem ? "読めません" : "なし");
  $("distState").className = d.exists ? "m-badge ok" : "m-badge";
  $("distMeta").textContent = d.exists
    ? `${d.created_at} に ${d.created_on} で作成`
      + (d.applied_at ? `（この端末で最後に書き出した・読み込んだ: ${d.applied_at}）` : "")
      + (skipped.length ? `\n※ 形が違うので読まない項目: ${skipped.join(" / ")}` : "")
    : (d.problem || "まだありません。下で書き出すと、アプリのフォルダの「配布設定\\packing_details」にできます。");
  $("distMeta").classList.toggle("bad", Boolean(d.problem || skipped.length));
  const rows = d.contents.length ? d.contents : [{ label: "（なし）", value: "" }];
  $("distRows").replaceChildren(...rows.map((c) => {
    const tr = document.createElement("tr");
    for (const text of [c.label, c.value]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    return tr;
  }));
  $("distPath").textContent = d.path;

  // 入れる項目。**この端末で変えていない項目は入れられない**(既定のまま配っても同じ)
  const legend = $("distItems").querySelector("legend");
  // 選び直した分は描き直しても残す。**選べなかった箱は覚えない**(既定のままだった
  // 項目をあとで変えたら、既定どおり選ばれた形で出す)
  const was = new Map([...$("distItems").querySelectorAll("input[data-dist-item]")]
    .filter((box) => !box.disabled)
    .map((box) => [box.dataset.distItem, box.checked]));
  $("distItems").replaceChildren(legend, ...d.items.map((it) => {
    const label = document.createElement("label");
    const box = document.createElement("input");
    box.type = "checkbox";
    box.dataset.distItem = it.key;
    box.checked = it.set && (was.has(it.key) ? was.get(it.key) : it.default);
    box.disabled = !it.set;
    const value = document.createElement("span");
    value.className = "stamp";
    value.textContent = it.value;
    label.append(box, ` ${it.label}　`, value);
    return label;
  }));
  $("distNot").replaceChildren(...d.not_included.map((n) => {
    const li = document.createElement("li");
    li.textContent = `入れないもの: ${n.label} ── ${n.why}`;
    return li;
  }));
}

async function sendDist(action) {
  const data = { password: $("distPassword").value };
  if (action === "export") {
    data.items = [...document.querySelectorAll("#distItems input[data-dist-item]:checked")]
      .map((box) => box.dataset.distItem);
  }
  for (const id of ["btnDistExport", "btnDistReapply", "btnDistRemove"]) $(id).disabled = true;
  showNote("distNote", "");
  try {
    const body = await api.post(`/api/settings/distribution/${action}`, data);
    $("distPassword").value = "";
    // 読み込み直したら置き場所の欄も変わる
    renderLocal(body, { fill: action === "reapply" });
    showNote("distNote", body.message);
  } catch (err) {
    if (!refused(err, "distNote")) return;
    if (err.field === "password") {
      $("distPassword").value = "";
      $("distPassword").focus();
    }
  } finally {
    for (const id of ["btnDistExport", "btnDistReapply", "btnDistRemove"]) $(id).disabled = false;
  }
}
