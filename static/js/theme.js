/*
  theme.js — 画面の色(自動 / ライト / ダーク)を、開いている画面すべてでそろえる(統合 1.0.16)

  統合アプリが**どの画面にも**差し込む(app.py `_install_theme`)。各機能のコードは触らない。

  - 開いた瞬間の色は、サーバが `<html data-theme>` に付けてある(ちらつかない)
  - 上の帯の「画面の色」で選ぶと、統合画面が `BroadcastChannel("cpt-theme")` で知らせる。
    ここがそれを受けて、この画面の `data-theme` を付け替える(読み直さない。入力はそのまま)。
    統合画面の中の3機能の画面・ログの画面・別のタブで開いた帳票まで同じ
  - 戻る・進むで控えから出た画面は、いまの色を聞き直す

  CSS は `<html data-theme="dark|light">` をブラウザの外観より優先する作り。
  自動(付けない)ならブラウザの外観(prefers-color-scheme)に合わせる。
  紙(帳票・印刷用のタブ・ラベル)は配色を持たないので、どれを選んでも白地に黒のまま。
*/
(function () {
  "use strict";
  function apply(theme) {
    var root = document.documentElement;
    if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
  }
  if ("BroadcastChannel" in window) {
    try {
      var channel = new BroadcastChannel("cpt-theme");
      channel.addEventListener("message", function (e) {
        var d = e.data || {};
        if (d.type === "theme") apply(d.theme);
      });
    } catch (err) { /* 使えないブラウザでは、次に開いた画面から変わる */ }
  }
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    fetch("/api/theme", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j && j.ok) apply(j.theme); })
      .catch(function () {});
  });
})();
