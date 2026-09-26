/*
  meisai.js — 梱包明細の画面

  VBA `frmCoilPacking` のイベントに対応する。

  【状態はサーバが持つ】
  操作のたびにサーバが「いま画面がどう見えるか」を丸ごと返し、ここは
  それを描くだけ。**画面側に状態を持たない** ── 2か所に持つと、
  どちらが正しいかを決める規則が要り、ずれたときに直す先が分からなくなる。

  ここが自前で持つのは、サーバに送るまでの**入力途中の値**だけ
  (重量の入力欄・廃棄モードの on/off)。
*/
import { api, ApiError, BASE } from "./api.js";
import * as health from "./health.js";
import * as tab from "./screen.js";
import * as master from "./master.js";
import * as settings from "./settings.js";
import { importWithProgress, showNote, waiting, wireBusy } from "./ui.js";

const $ = (id) => document.getElementById(id);

// 画面だけが持つもの
let haikiMode = false;         // 廃棄選択中か (VBA `mHaikiMode`)
let editingZen = false;        // 前工程実績数の修正中か (VBA ギミックA)
let selectedOutput = null;     // 一覧で選んでいる出力のNo
let selectedOrder = null;      // 選んでいる受注番号
// まとめ刷りで選んだ出力のNo。**1枚ずつ別の用紙**に出る(A4 1枚に
// 2枚並べる運用はしていない)。
const checkedOutputs = new Set();

// ------------------------------------------------------------------
// 通知
// ------------------------------------------------------------------
let noticeTimer = null;
function notify(message, kind = "") {
  const el = $("notice");
  el.textContent = message;
  el.className = "banner" + (kind ? ` ${kind}` : "");
  el.hidden = !message;
  clearTimeout(noticeTimer);
  if (message && kind === "ok") {
    noticeTimer = setTimeout(() => { el.hidden = true; }, 6000);
  }
}

/** 引き継がれた画面を畳む。**触らせない**のが要点。

    ここに出ている値(ロット番号・条番号・積み上げ)は、もう別の画面が
    開いているロットのもの。そのまま押せると、現物と紙が食い違う。 */
function lockScreen() {
  const box = document.getElementById("takenOver");
  if (box) box.hidden = false;
  // 畳まれた側は、以後あとから開くタブに「居る」と返さない ──
  // 返すと、次に開いたタブまで2枚目扱いになる
  tab.stopAnswering();
  // 下の画面を押せなくする。**隠すのではなく止める** ── 消してしまうと
  // 「何が起きたのか」が分からない
  document.querySelector(".wrap")?.setAttribute("inert", "");
}

/** 断りを画面に出す。**文言はサーバが持っている**ので、そのまま見せる。 */
function showError(err) {
  if (err instanceof ApiError) {
    // **心拍を待たずに畳む。** 20秒のあいだ押し続けられると、
    // 押すたびに赤帯が出るだけで理由が伝わらない
    if (err.code === "screen_taken") { health.screenLost(); return; }
    notify(err.message, "danger");
  } else {
    notify("処理できませんでした。もう一度お試しください。", "danger");
    console.error(err);
  }
}

// ------------------------------------------------------------------
// 確認ダイアログ (VBA `MsgBox ... vbYesNo`)
// ------------------------------------------------------------------
function confirmAsk(message, detail = []) {
  return new Promise((resolve) => {
    const dialog = $("confirmDialog");
    $("confirmBody").textContent = message;
    if (detail.length) {
      const list = document.createElement("ul");
      for (const line of detail) {
        const item = document.createElement("li");
        item.textContent = line;
        list.appendChild(item);
      }
      $("confirmBody").appendChild(list);
    }
    dialog.addEventListener("close", function once() {
      dialog.removeEventListener("close", once);
      resolve(dialog.returnValue === "yes");
    });
    dialog.showModal();
  });
}

// ------------------------------------------------------------------
// 描画
// ------------------------------------------------------------------
function render(state) {
  window.APP.state = state;

  $("lotNo").value = state.lot_no || "";
  $("zenKotei").value = state.zen_kotei || "";
  $("seqNote").textContent = `現在の連番 No${state.current_seq}`;

  renderLot(state);
  renderWeights(state);
  renderGrid(state);
  renderStack(state);
  renderOutputs(state);
}

function renderLot(state) {
  const found = state.found && state.lot;
  const hasStrands = Boolean(found && state.strands.length);
  $("lotInfo").hidden = !found;
  $("orderRow").hidden = !found;
  $("weightCard").hidden = !found;
  // 条番号と積み上げは**別々に隠す**。横に並んでいるので、
  // まとめて隠すと片方だけ出したいときに効かない
  $("strandCard").hidden = !hasStrands;
  $("stackCard").hidden = !hasStrands;
  $("outputCard").hidden = !(found && state.outputs.length);

  const note = $("lotNote");
  note.hidden = !(found && state.lot.jou_su_note);
  if (found && state.lot.jou_su_note) note.textContent = state.lot.jou_su_note;

  if (!found) {
    $("warisu").textContent = "";
    return;
  }

  const lot = state.lot;
  for (const key of ["yoto_code", "yoto_name", "zaishitsu", "choshitsu",
                     "odr_thickness", "odr_width", "seizou_thickness",
                     "seizou_width", "sekkei_course", "jisseki_course"]) {
    $(`f_${key}`).textContent = lot[key] || "";
  }
  $("warisu").textContent = `縦割${lot.tate_wari} × 横割${lot.yoko_wari}`;
  $("jouSu").value = state.jou_su || "";

  // 受注番号。押すと包装仕様NOが切り替わる (VBA `lstJuchuList_Click`)
  const orders = $("orders");
  orders.textContent = "";
  $("orderCount").textContent = `${state.orders.length}件`;
  if (selectedOrder === null && state.orders.length) {
    selectedOrder = state.orders[0].order_no;
  }
  for (const order of state.orders) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "order";
    chip.textContent = order.order_no;
    chip.setAttribute("aria-pressed", String(order.order_no === selectedOrder));
    chip.addEventListener("click", () => {
      selectedOrder = order.order_no;
      renderLot(window.APP.state);
    });
    orders.appendChild(chip);
  }
  renderSpec(state);
}

function renderSpec(state) {
  const spec = $("specNo");
  spec.textContent = "";
  const order = state.orders.find((o) => o.order_no === selectedOrder);
  if (!order || !order.spec_no) {
    // VBA: 包装仕様NOが空ならリンクを張らず、黒いまま
    spec.textContent = order ? "(未登録)" : "";
    return;
  }
  // VBA `lblHousouShiyou_Click`: クリップボードへ写して閲覧システムを開く
  const link = document.createElement("a");
  link.href = window.APP.hosoUrl;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = order.spec_no;
  link.title = "クリックでコピーし、包装仕様書の画面を開きます";
  link.addEventListener("click", () => {
    navigator.clipboard?.writeText(order.spec_no)
      .then(() => notify(`包装仕様NO ${order.spec_no} をコピーしました`, "ok"))
      .catch(() => { /* コピーできなくても画面は開く */ });
  });
  spec.appendChild(link);
}

function renderWeights(state) {
  const box = $("weights");
  box.textContent = "";
  for (let jou = 1; jou <= state.jou_su; jou += 1) {
    const cell = document.createElement("div");
    cell.className = "weight";

    const lab = document.createElement("div");
    lab.className = "lab";
    lab.textContent = `丈${jou}`;
    cell.appendChild(lab);

    const input = document.createElement("input");
    input.type = "text";
    input.inputMode = "numeric";
    input.id = `weight${jou}`;
    input.value = state.weights[jou - 1] || "";
    // VBA `clsWeightCtrl.Txt_KeyPress`: 数字以外を拒否(小数点も不可)
    input.addEventListener("beforeinput", (event) => {
      if (event.data && !/^\d+$/.test(event.data)) event.preventDefault();
    });
    input.addEventListener("change", sendWeights);
    cell.appendChild(input);

    // ±1 / ±10 (VBA の spn1 / spn10)
    const spin = document.createElement("div");
    spin.className = "spin";
    for (const step of [-10, -1, 1, 10]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = step > 0 ? `+${step}` : String(step);
      button.addEventListener("click", () => {
        const next = Math.max(0, (parseInt(input.value, 10) || 0) + step);
        input.value = String(next);
        sendWeights();
      });
      spin.appendChild(button);
    }
    cell.appendChild(spin);
    box.appendChild(cell);
  }
}

function renderGrid(state) {
  const grid = $("grid");
  grid.textContent = "";
  state.rows.forEach((row, index) => {
    const jou = index + 1;
    const block = document.createElement("div");
    block.className = "jou";

    const lab = document.createElement("div");
    lab.className = "lab";
    lab.textContent = `丈${jou} ／ ${state.strands[index]}条`;
    block.appendChild(lab);

    const coils = document.createElement("div");
    coils.className = "coils";
    for (const cell of row) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "coil";
      button.dataset.state = cell.state;
      button.dataset.key = cell.key;
      button.textContent = cell.key;
      // 使用済みは押しても何も起きない(VBA も早期 Exit)。
      // **廃棄済みは押せる** ── 廃棄モードで解除するため
      button.disabled = cell.state === "used" && !haikiMode;
      button.addEventListener("click", () => clickCoil(cell.key));
      coils.appendChild(button);
    }
    block.appendChild(coils);
    grid.appendChild(block);
  });

  $("strandHint").textContent = haikiMode
    ? "廃棄選択中：クリックで廃棄の付け外し"
    : "クリックで積み上げへ";
}

function renderStack(state) {
  const box = $("stack");
  box.textContent = "";
  $("stackMax").value = state.stack_max || "";
  for (let i = 0; i < state.slots.length; i += 1) {
    const key = state.slots[i];
    const button = document.createElement("button");
    button.type = "button";
    button.className = key ? "slot" : "slot empty";
    button.textContent = key || `${i + 1}`;
    button.disabled = !key;
    button.addEventListener("click", () => unstack(i + 1));
    box.appendChild(button);
  }
  $("btnOutput").disabled = !state.complete;
}

function renderOutputs(state) {
  const box = $("outputs");
  box.textContent = "";
  if (!state.outputs.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "まだ出力していません。";
    box.appendChild(empty);
    checkedOutputs.clear();
    updatePrintLabel();
    return;
  }
  // 最新を選んでおく (VBA `RefreshSheetList` は末尾を自動選択する)
  if (selectedOutput === null
      || !state.outputs.some((o) => o.no === selectedOutput)) {
    selectedOutput = state.outputs[state.outputs.length - 1].no;
  }
  // 消えた出力のチェックは落とす
  const alive = new Set(state.outputs.map((o) => o.no));
  for (const no of [...checkedOutputs]) {
    if (!alive.has(no)) checkedOutputs.delete(no);
  }

  for (const output of state.outputs) {
    const row = document.createElement("div");
    row.className = "output";
    row.setAttribute("aria-pressed", String(output.no === selectedOutput));

    // まとめて刷れるように選べるようにする(1枚ずつ別の用紙に出る)
    const check = document.createElement("input");
    check.type = "checkbox";
    check.id = `pick${output.no}`;
    check.checked = checkedOutputs.has(output.no);
    check.addEventListener("change", () => {
      if (check.checked) checkedOutputs.add(output.no);
      else checkedOutputs.delete(output.no);
      updatePrintLabel();
    });

    const name = document.createElement("label");
    name.className = "n";
    name.htmlFor = check.id;
    name.textContent = output.name;

    const open = document.createElement("button");
    open.type = "button";
    open.className = "pick";
    open.textContent = `${output.count}本`;
    open.title = "この1枚を選ぶ";
    open.addEventListener("click", () => {
      selectedOutput = output.no;
      renderOutputs(window.APP.state);
      $("btnPrint").disabled = false;
    });

    row.append(check, name, open);
    box.appendChild(row);
  }
  updatePrintLabel();
}

/** 印刷ボタンに「いま何枚刷るか」を出す。**紙の枚数も添える。** */
function updatePrintLabel() {
  const nos = printTargets();
  const button = $("btnPrint");
  button.disabled = nos.length === 0;
  // **開いた紙面で書き足せる**ことをここで言う。押す前に見る場所なので、
  // 紙面を開いてから気づくより早い
  const hint = "開いた紙面でサイズ・LOTNOを書き足せます";
  // 用紙はA4横。**左半分に明細、右半分は空ける**(現場で切り取る)
  if (nos.length <= 1) {
    button.textContent = "印刷";
    $("printNote").textContent = nos.length
      ? `A4横の左半分に出ます（右半分は空白）。${hint}` : "";
    return;
  }
  button.textContent = `印刷 (${nos.length}枚)`;
  $("printNote").textContent =
    `${nos.length}枚 → 用紙${nos.length}枚（1枚ずつ左半分に）。${hint}`;
}

/** 刷る対象。チェックがあればそれ、無ければ選択中の1枚。 */
function printTargets() {
  if (checkedOutputs.size) return [...checkedOutputs].sort((a, b) => a - b);
  return selectedOutput === null ? [] : [selectedOutput];
}

// ------------------------------------------------------------------
// 操作
// ------------------------------------------------------------------
/** サーバを呼んで、返ってきた画面を描く。 */
async function send(path, data, onDone) {
  try {
    const body = await api.post(path, data);
    if (body.confirm) return body;          // 確認が要る。呼び元が扱う
    if (body.state) render(body.state);
    if (body.message) notify(body.message, "ok");
    if (onDone) onDone(body);
    return body;
  } catch (err) {
    showError(err);
    return null;
  }
}

async function openLot(lotNo, confirm = false) {
  const body = await send("/api/lot", { lot_no: lotNo, confirm });
  if (body && body.confirm === "unprinted") {
    if (await confirmAsk(body.message)) openLot(lotNo, true);
    else $("lotNo").value = window.APP.state.lot_no || "";
  }
}

function sendWeights() {
  const state = window.APP.state;
  const values = [];
  for (let jou = 1; jou <= state.jou_su; jou += 1) {
    values.push($(`weight${jou}`).value);
  }
  // 未入力があるうちは送らない(断られて赤帯が出続けるだけ)
  if (values.some((v) => String(v).trim() === "")) return;
  send("/api/weights", { weights: values });
}

async function buildStrands(confirm = false) {
  const body = await send("/api/strands", { confirm });
  if (body && body.confirm === "overwrite_restored") {
    if (await confirmAsk(body.message)) buildStrands(true);
  }
}

function clickCoil(key) {
  if (haikiMode) send("/api/discard", { key });
  else send("/api/stack", { key });
}

function unstack(slotNo) {
  send("/api/unstack", { slot_no: slotNo });
}

async function doOutput(confirm = false) {
  const specify = $("chkSpecify").checked ? $("specifyNo").value : null;
  const body = await send("/api/output", { confirm, specify_no: specify });
  if (body && body.confirm === "output") {
    const first = body.confirms[0];
    const detail = body.confirms.flatMap((c) => c.detail || []);
    const message = body.confirms.map((c) => c.message).join("\n");
    if (await confirmAsk(message, detail)) doOutput(true);
    else if (!first) notify("");
  } else if (body && body.ok) {
    // 出したら No指定は外す (VBA も chkSpecifyNo.Value = False にする)
    $("chkSpecify").checked = false;
    $("specifyNo").value = "";
    $("specifyNo").disabled = true;
    selectedOutput = body.seq_no;
    renderOutputs(window.APP.state);
  }
}

async function doPrint() {
  const nos = printTargets();
  if (!nos.length) return;
  const lotNo = window.APP.state.lot_no;
  // 先に印刷済みを記録してから帳票を開く。ブラウザの印刷は
  // 押されたかどうかを確実には拾えない
  await send("/api/printed", {});
  const token = encodeURIComponent(window.APP.token);
  const url = nos.length === 1
    ? `${BASE}/report/${encodeURIComponent(lotNo)}/${nos[0]}?t=${token}`
    : `${BASE}/report/${encodeURIComponent(lotNo)}?nos=${nos.join(",")}&t=${token}`;
  window.open(url, "_blank", "noopener");
}

async function clearHistory() {
  if (!await confirmAsk("副番履歴を全件クリアしますか？")) return;
  send("/api/clear-history", {});
}

// ------------------------------------------------------------------
// 取り込み元の鮮度(バージョン情報に出す。設定画面は `settings.js`)
// ------------------------------------------------------------------
function renderStamps(stamps, boxId) {
  const box = $(boxId);
  box.textContent = "";
  for (const s of stamps) {
    const line = document.createElement("div");
    // **届くかどうかと、取り込んであるかどうかは別。** ここは届くかを
    // 見に行かない(`found` が null)ので、いつ時点のものかだけを出す
    const reach = s.found === null || s.found === undefined ? ""
      : (s.found ? "届く  " : "届かない  ");
    line.className = s.found === false ? "miss" : "ok";
    line.textContent =
      `${s.table}  ${reach}${s.as_of || "まだ取り込んでいません"}  ${s.count.toLocaleString()}件`;
    box.appendChild(line);
  }
}

// ------------------------------------------------------------------
// 明細の履歴 (全ライン・3年)
// ------------------------------------------------------------------
const pad2 = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

function renderHistory(body) {
  const box = $("hisState");
  box.textContent = "";
  const line = (text) => {
    const div = document.createElement("div");
    div.textContent = text;
    box.appendChild(div);
  };
  line(`共有: ${body.shared_path}`);
  if (body.shared_count !== null && body.shared_count !== undefined && body.shared_count > 0) {
    line(`共有の履歴: ${body.shared_count.toLocaleString()}枚・${body.shared_coils.toLocaleString()}本`
      + `（${body.shared_oldest.slice(0, 10)} 〜 ${body.shared_newest.slice(0, 10)}）`);
  } else if (body.shared_count === 0) {
    line("共有の履歴: まだ0枚です（明細を出力すると送られます）");
  }
  if (body.shared_problem) line(`※ ${body.shared_problem}`);
  line(body.pending
    ? `この PC の送り残し: ${body.pending}枚（届けば自動で送ります）`
    : "この PC の送り残し: なし");
  if (body.last_error) line(`送れなかった理由: ${body.last_error}`);
  else if (body.last_sent_at) line(`最後に送った: ${body.last_sent_at.slice(0, 16)}`);
  line(`${body.keep_years}年より前（${body.cutoff} より前）は自動で整理します`);
  const bad = Boolean(body.last_error) || (body.shared_problem && body.shared_count === null);
  box.className = bad ? "set-share bad" : "set-share";
}

async function openHistory() {
  const today = new Date();
  if (!$("hisFrom").value) $("hisFrom").value = ymd(new Date(today.getFullYear(), today.getMonth(), 1));
  if (!$("hisTo").value) $("hisTo").value = ymd(today);
  showNote("hisSendNote", "");
  showNote("hisFindNote", "");
  // **先に開く。** 共有の様子は後から埋める(共有の応えしだいで数秒かかる)
  const box = $("hisState");
  box.className = "set-share";
  const stop = waiting(box, "共有の履歴を確かめています…");
  $("historyDialog").showModal();
  try {
    const body = await api.get("/api/history/status");
    stop();
    box.hidden = false;
    renderHistory(body);
  } catch (err) {
    stop();
    box.hidden = false;
    if (err instanceof ApiError && err.code === "screen_taken") {
      $("historyDialog").close();
      showError(err);
      return;
    }
    box.className = "set-share bad";
    box.textContent = `共有の履歴の様子を読めませんでした: ${err.message}`;
  }
}

async function sendHistory() {
  $("btnHisSend").disabled = true;
  try {
    const body = await api.post("/api/history/send", {});
    renderHistory(body);
    showNote("hisSendNote", body.message, Boolean(body.last_error));
  } catch (err) {
    showNote("hisSendNote", err.message, true);
  } finally {
    $("btnHisSend").disabled = false;
  }
}

function historyFilter() {
  return { from: $("hisFrom").value, to: $("hisTo").value,
           lot: $("hisLot").value, fuban: $("hisFuban").value };
}

let hisLoadLimit = 20;

function updatePicked() {
  const picked = [...document.querySelectorAll("#hisRows input:checked")];
  $("btnHisReprint").disabled = !picked.length || picked.length > hisLoadLimit;
  $("hisPicked").textContent = picked.length > hisLoadLimit
    ? `一度に開けるのは${hisLoadLimit}枚までです（いま${picked.length}枚）`
    : (picked.length ? `${picked.length}枚選んでいます` : "紙を選んでください（行を押すと選べます）");
}

function renderHistoryRows(body) {
  hisLoadLimit = body.load_limit || hisLoadLimit;
  const tbody = $("hisRows");
  tbody.textContent = "";
  for (const row of body.rows) {
    const tr = document.createElement("tr");
    if (row["状態"] === "作り直し") tr.className = "replaced";
    const cell = (text, cls = "") => {
      const td = document.createElement("td");
      if (cls) td.className = cls;
      td.textContent = text;
      tr.appendChild(td);
      return td;
    };
    const pick = document.createElement("input");
    pick.type = "checkbox";
    pick.value = row["送信ID"];
    pick.setAttribute("aria-label", `${row["ロット番号"]}-No${row["No"]} を選ぶ`);
    pick.addEventListener("change", updatePicked);
    const first = document.createElement("td");
    first.appendChild(pick);
    tr.appendChild(first);
    cell(String(row["出力日時"]).slice(0, 16));
    cell(`${row["ロット番号"]}-No${row["No"]}`);
    cell(String(row["本数"]), "num");
    cell(row["副番の並び"] || "", "fuban").title = row["副番の並び"] || "";
    cell(row["状態"], "state");
    cell(row["出力PC"]);
    // 行のどこを押しても選べる(小さな四角だけを狙わせない)
    tr.addEventListener("click", (e) => {
      if (e.target !== pick) { pick.checked = !pick.checked; updatePicked(); }
    });
    tbody.appendChild(tr);
  }
  $("hisResult").hidden = false;
  updatePicked();
}

async function searchHistory() {
  $("btnHisSearch").disabled = true;
  $("hisFiles").hidden = true;
  try {
    const body = await api.post("/api/history/search", historyFilter());
    renderHistoryRows(body);
    const where = body.source === "local" ? "この PC の分から" : "全ラインから";
    showNote("hisFindNote", body.total
      ? `${where}${body.total.toLocaleString()}枚見つかりました。${body.note || ""}`
      : `見つかりませんでした。${body.note || ""}`, body.source === "local");
  } catch (err) {
    $("hisResult").hidden = true;
    showNote("hisFindNote", err.message, true);
  } finally {
    $("btnHisSearch").disabled = false;
  }
}

/** 選んだ紙を、履歴の中身のとおりに作り直して別のタブで開く(読むだけ)。 */
function reprintHistory() {
  const ids = [...document.querySelectorAll("#hisRows input:checked")].map((i) => i.value);
  if (!ids.length) return;
  const token = encodeURIComponent(window.APP.token);
  window.open(`${BASE}/report/history?ids=${ids.map(encodeURIComponent).join(",")}&t=${token}`,
              "_blank", "noopener");
}

async function exportHistory() {
  $("btnHisExport").disabled = true;
  $("hisFiles").hidden = true;
  showNote("hisFindNote", "書き出しています…");
  try {
    const body = await api.post("/api/history/export", historyFilter());
    $("hisDetailName").textContent = body.detail_file;
    $("hisSummaryName").textContent = body.summary_file;
    $("hisFolder").textContent = body.folder;
    $("hisFiles").hidden = false;
    showNote("hisFindNote", body.note
      || `${body.slips.toLocaleString()}枚・${body.detail_rows.toLocaleString()}本を書き出しました。`);
    renderHistory(await api.get("/api/history/status"));
  } catch (err) {
    showNote("hisFindNote", err.message, true);
  } finally {
    $("btnHisExport").disabled = false;
  }
}

// ------------------------------------------------------------------
// バージョン情報
// ------------------------------------------------------------------
/** 秒を「2時間14分」の形に。**秒まで出しても読む人が困るだけ。** */
function since(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}日${h}時間`;
  if (h) return `${h}時間${m}分`;
  if (m) return `${m}分`;
  return `${s}秒`;
}

async function openAbout() {
  // **健康確認と設定の両方から集める。** どちらか片方が落ちても
  // 出せるところまでは出す ── 版を知りたいときはたいてい何かが
  // おかしいときで、そこで画面ごと出ないのがいちばん困る
  const [health, settings] = await Promise.all([
    fetch(BASE + "/api/health", { cache: "no-store" })
      .then((r) => r.json()).catch(() => null),
    api.get("/api/settings").catch(() => null),
  ]);

  if (health) {
    $("abVer").textContent = `VER ${health.version}`;
    $("abRoot").textContent = health.app_root || "—";
    $("abAppId").textContent = health.app_id || "—";
    $("abPort").textContent = `${health.port} / ${health.pid}`;
    $("abUptime").textContent = since(health.uptime_sec);
    $("abPython").textContent = health.python
      ? `${health.python}　${health.python_exe || ""}`.trim() : "—";
    // 版の書き方が壊れていても、手元DBが共有に置かれていても、
    // 起動は止めない。**気づける場所に出す**
    const bad = $("abProblem");
    const problems = [health.version_problem, health.db_path_problem]
      .filter(Boolean);
    bad.textContent = problems.join("\n");
    bad.hidden = !problems.length;
  }
  if (settings) {
    $("abDb").textContent = settings.db_path;
    $("abLog").textContent = settings.log_dir;
    renderStamps(settings.stamps, "abStamps");
  }
  $("aboutDialog").showModal();
}

/** 上の帯の「いつ時点の台帳か」を描き直す。**いつ時点かはサーバが決める**(`as_of`)。 */
function renderTopStamps(stamps) {
  $("stamp").textContent = stamps.map((s) => `${s.table} ${s.as_of_short}`).join("　");
}

async function runImport() {
  $("btnImport").disabled = true;
  try {
    // **進み具合の窓を出す。** 何を読んでいるか・何秒たったかが見えないと、
    // 押せていないと思って押し直す(現場の指摘: 反応が鈍い)
    const body = await importWithProgress({ force: true }, "取り込み中");
    // **開いているロットが古いままなら、そう言う。** 黙って
    // 読み直すと、手で直した前工程実績数が消える
    notify([body.summary, body.note, ...body.errors].filter(Boolean).join("\n"),
           body.ok ? "ok" : "danger");
    renderTopStamps(body.stamps);
  } catch (err) {
    showError(err);
  } finally {
    $("btnImport").disabled = false;
  }
}

// ------------------------------------------------------------------
// 組み立て
// ------------------------------------------------------------------
function wire() {
  // --- ロット番号。英数字だけ・大文字・7桁で自動検索 ---
  const lotNo = $("lotNo");
  lotNo.addEventListener("input", () => {
    const cleaned = lotNo.value.replace(/[^A-Za-z0-9]/g, "").toUpperCase();
    if (cleaned !== lotNo.value) lotNo.value = cleaned;
    if (cleaned.length === window.APP.lotNoLength) openLot(cleaned);
  });

  // --- 前工程実績数の修正 (VBA ギミックA) ---
  $("btnEditZen").addEventListener("click", () => {
    const input = $("zenKotei");
    if (!editingZen) {
      editingZen = true;
      input.readOnly = false;
      input.focus();
      input.select();
      $("btnEditZen").textContent = "確定する";
      return;
    }
    send("/api/zen-kotei", { value: input.value }, () => {
      editingZen = false;
      input.readOnly = true;
      $("btnEditZen").textContent = "修正する";
    });
  });

  // --- 丈数 ---
  $("jouSu").addEventListener("change", () => {
    send("/api/jou-su", { value: $("jouSu").value });
  });
  for (const button of document.querySelectorAll("[data-jou]")) {
    button.addEventListener("click", () => {
      const step = Number(button.dataset.jou);
      const next = Math.min(window.APP.jousuMax,
                            Math.max(1, (parseInt($("jouSu").value, 10) || 0) + step));
      $("jouSu").value = String(next);
      send("/api/jou-su", { value: next });
    });
  }

  $("btnStrands").addEventListener("click", () => buildStrands());
  $("btnReverse").addEventListener("click", () => send("/api/reverse", {}));

  // --- 廃棄モード (VBA `cmdHaiki_Click`) ---
  $("btnHaiki").addEventListener("click", () => {
    haikiMode = !haikiMode;
    $("btnHaiki").textContent = haikiMode ? "廃棄選択中" : "廃棄選択";
    $("btnHaiki").classList.toggle("on", haikiMode);
    renderGrid(window.APP.state);
  });

  // --- 積み条数 ---
  $("stackMax").addEventListener("change", () => {
    send("/api/stack-max", { value: $("stackMax").value });
  });
  // −/＋ でも入れられる(丈数と同じ形)。1〜最大の範囲に収める
  document.querySelectorAll("[data-stack]").forEach((button) => {
    button.addEventListener("click", () => {
      const box = $("stackMax");
      const max = parseInt(box.dataset.max, 10) || 15;
      const now = parseInt(box.value, 10) || 0;
      const next = Math.min(max, Math.max(1, now + parseInt(button.dataset.stack, 10)));
      if (String(next) === box.value) return;
      box.value = String(next);
      send("/api/stack-max", { value: box.value });
    });
  });

  // --- 出力・印刷 ---
  $("btnOutput").addEventListener("click", () => doOutput());
  $("btnPrint").addEventListener("click", doPrint);
  $("chkSpecify").addEventListener("change", () => {
    $("specifyNo").disabled = !$("chkSpecify").checked;
    if ($("chkSpecify").checked) $("specifyNo").focus();
    else $("specifyNo").value = "";
  });

  $("btnClearHistory").addEventListener("click", clearHistory);
  $("btnImport").addEventListener("click", runImport);
  wireBusy();
  settings.wire({ showError, afterImport: (body) => renderTopStamps(body.stamps) });
  $("btnHistory").addEventListener("click", openHistory);
  $("btnHisClose").addEventListener("click", () => $("historyDialog").close());
  $("btnHisSend").addEventListener("click", sendHistory);
  $("btnHisExport").addEventListener("click", exportHistory);
  $("btnHisSearch").addEventListener("click", searchHistory);
  $("btnHisReprint").addEventListener("click", reprintHistory);
  // 場所を写すだけ。**こちらからフォルダや Excel は開かない**
  $("btnHisCopyFolder").addEventListener("click", () => {
    navigator.clipboard?.writeText($("hisFolder").textContent)
      .then(() => showNote("hisFindNote", "場所をコピーしました。"))
      .catch(() => {});
  });
  // マスタ管理の「置き場所を変える」。マスタ管理を閉じて、設定の
  // 梱包資材マスタのフォルダの欄を開く(**変えたらすぐ、マスタ管理もその場所を見る**)
  master.wire({ openPlace: () => settings.open({ tab: "place", focus: "setShareDir" }) });

  // 引き継ぐ。**手放させてから開き直す** ── 開き直すだけだと、
  // いま持っている画面に断られて2度手間になる
  for (const bid of ["btnRetake", "btnSecondTake"]) {
    $(bid).addEventListener("click", () => takeOverScreen($(bid)));
  }
  $("btnSecondRetry").addEventListener("click", () => location.reload());

  $("btnAbout").addEventListener("click", () => openAbout().catch(showError));
  $("btnAboutClose").addEventListener("click",
    () => $("aboutDialog").close());
}

// ------------------------------------------------------------------
// 起動
// ------------------------------------------------------------------
/** 2枚目として開いたことを告げる。**業務の画面は出さない。** */
function showSecondScreen(info) {
  const box = $("secondScreen");
  const note = $("secondNote");
  if (info && info.idle_sec !== undefined) {
    note.textContent = `前の画面からの応答: ${info.idle_sec}秒前`
      + `　（${info.grace_sec}秒とだえると、自動で空きます）`;
  } else {
    note.textContent = "別のタブが開いています。";
  }
  box.hidden = false;
  // 下の画面は**消さずに止める**。消すと「何が起きたのか」が分からない
  document.querySelector(".wrap")?.setAttribute("inert", "");
}

/** 前の画面に手放させて、開き直す。**2枚にするのではなく、移す。** */
async function takeOverScreen(button) {
  button.disabled = true;
  // **先に知らせる。** 開き直してから知らせると、開き直した先では
  // まだ前のタブが「居る」と返してくるので、いつまでも入れない
  tab.announceTakeOver();
  try { await api.post("/api/screen/take-over", {}); } catch { /* 開き直せば分かる */ }
  location.href = BASE + "/meisai";
}

async function boot() {
  window.APP.screen = tab.tabId();
  wire();

  // **まず他のタブに直接きく。** タブの複製は名前ごと写されるので、
  // 名前の比較だけでは見分けられない
  if (await tab.anotherTabIsOpen()) { showSecondScreen(null); return; }

  try {
    await api.post("/api/screen/claim", { screen: window.APP.screen });
  } catch (err) {
    if (err instanceof ApiError && err.code === "screen_busy") {
      showSecondScreen(err.body || null);
      return;
    }
    showError(err);
    return;
  }

  tab.answerPings(lockScreen);
  render(window.APP.state);
  health.start({ onLost: lockScreen });
}

boot();
