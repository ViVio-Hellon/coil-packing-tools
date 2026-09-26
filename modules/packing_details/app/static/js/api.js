/*
  api.js — サーバとのやりとり

  JSが持つのは「呼ぶ」「受け取ったものを描く」だけ。
  **業務の判断はサーバが行う**(押せるか・何と出すか・断るか)。
*/

const TOKEN = window.APP.token;
// この機能の入口(統合版では `/details`)。**経路はここで1度だけ付ける**
export const BASE = window.APP.base || "";

/** サーバが返した断りを、そのまま画面に出せる形で持つ。 */
export class ApiError extends Error {
  constructor(status, body) {
    const info = (body && body.error) || {};
    super(info.message || (body && body.message)
          || `通信に失敗しました (HTTP ${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = info.code || "";
    this.field = info.field || "";
    this.body = body;
  }
}

async function call(path, options) {
  const res = await fetch(BASE + path, {
    cache: "no-store",
    headers: {
      "X-Tool-Token": TOKEN,
      // **この画面が何枚目か。** 起動のときに決まるので、その場で読む
      // (モジュールが読まれた時点ではまだ決まっていない)。
      // 持ち主でない画面の操作はサーバが 409 で断る(`screen_taken`)
      "X-Tool-Screen": window.APP.screen || "",
      "Content-Type": "application/json",
    },
    ...options,
  });
  // **本文を先に読む。** 断りのときも理由が本文に入っている。
  // 読まずに status だけ見ると「通信に失敗しました」としか出せない
  let body = null;
  try { body = await res.json(); } catch { /* 本文が無いこともある */ }
  if (!res.ok) throw new ApiError(res.status, body);
  return body;
}

export const api = {
  get: (path) => call(path, { method: "GET" }),
  post: (path, data) => call(path, {
    method: "POST",
    body: JSON.stringify(data || {}),
  }),
};
