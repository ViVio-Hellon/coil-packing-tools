/*
  ui.js — 画面のあちこちで使う小物

  【黙って待たせない】(現場の指摘: 押しても反応が鈍い・やや重い)
  共有フォルダに問い合わせるものは、共有の応えしだいで数秒かかる。
  その間なにも変わらないと「押せていない」と思って押し直す。

    - ダイアログは**先に開いて**、中身は後から埋める
    - 読んでいる間は「読んでいます…（3秒）」と**経過秒**を出す(`waiting`)
    - 取り込みは進み具合の窓を出す(`importWithProgress`)。いま何を読んで
      いるか(n/m)・棒・経過秒
*/
import { api } from "./api.js";

const $ = (id) => document.getElementById(id);

/** ダイアログの中に結果を出す。**下の帯はダイアログの裏に隠れる**ので使わない。 */
export function showNote(id, message, bad = false) {
  const el = $(id);
  el.textContent = message || "";
  el.className = bad ? "set-note bad" : "set-note";
  el.hidden = !message;
}

/**
 * `el` に「text（N秒）」を出し続ける。返り値を呼ぶと止めて消す。
 * 1秒たつまでは秒を出さない(すぐ終わるものに数字をちらつかせない)。
 */
export function waiting(el, text) {
  const started = Date.now();
  const draw = () => {
    const sec = Math.floor((Date.now() - started) / 1000);
    el.textContent = sec >= 1 ? `${text}（${sec}秒）` : text;
  };
  el.hidden = false;
  el.classList.add("is-waiting");
  draw();
  const timer = setInterval(draw, 500);
  return () => {
    clearInterval(timer);
    el.classList.remove("is-waiting");
    el.textContent = "";
    el.hidden = true;
  };
}

// ------------------------------------------------------------------
// 取り込みの進み具合
// ------------------------------------------------------------------
const POLL_MS = 400;

/**
 * 取り込みを頼んで、終わるまで進み具合の窓を出す。応答の本文を返す。
 * `body` は `/api/import` に送るもの(`{force, only}`)。
 */
export async function importWithProgress(body, title = "取り込み中") {
  const dialog = $("busyDialog");
  $("busyTitle").textContent = title;
  $("busyMsg").textContent = "取り込み元を探しています…";
  $("busyBar").style.width = "0%";
  const started = Date.now();
  const tick = () => {
    $("busyTime").textContent = `${Math.floor((Date.now() - started) / 1000)}秒たちました`;
  };
  tick();
  const clock = setInterval(tick, 500);
  if (!dialog.open) dialog.showModal();
  let polling = true;
  const poll = async () => {
    while (polling) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      if (!polling) break;
      try {
        const p = await api.get("/api/import/progress");
        if (!polling || !p.running) continue;
        $("busyMsg").textContent = p.message || "読み込み中…";
        $("busyBar").style.width = `${Math.max(3, Math.min(100, p.pct || 0))}%`;
      } catch { /* 進み具合が取れなくても、取り込みそのものは続く */ }
    }
  };
  poll();
  try {
    return await api.post("/api/import", body);
  } finally {
    polling = false;
    clearInterval(clock);
    $("busyBar").style.width = "100%";
    dialog.close();
  }
}

/** 取り込み中の窓は Esc で閉じさせない(閉じても取り込みは止まらず、終わりが見えなくなる)。 */
export function wireBusy() {
  $("busyDialog").addEventListener("cancel", (event) => event.preventDefault());
}
