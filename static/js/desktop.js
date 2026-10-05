/*
  desktop.js — デスクトップ版(Tauri)のときだけ、窓まわりを外枠(Rust)に頼む

  統合アプリが**デスクトップ版のときだけ**どの画面にも差し込む(app.py `_install_desktop`)。
  3機能のコードは触らない(ブラウザ版では今までどおり `window.open` などがそのまま動く)。

  外枠の窓は「タブ」を持たないので、そのままでは次のものが何も起きない:
    - `window.open(url, "_blank")`        … 帳票・印刷ビュー(梱包明細・ペナラベル)
    - `<a target="_blank">`               … 資材計算の印刷・説明書・印刷ビューを開く
    - `window.close()`                    … 印刷用のタブの「このタブを閉じる」
  アプリの中のページは外枠に**別の窓**で開いてもらい、アプリの外(包装仕様書の閲覧システム)は
  **既定のブラウザ**で開いてもらう(`src-tauri/src/main.rs` の `open_window` ほか)。

  3機能の画面は統合画面の中(iframe)にある。外枠への頼みごとは、いちばん外の窓
  (`window.top`。同じ宛先なので触れる)の `__TAURI__` から出す。
*/
(function () {
  "use strict";
  var top = window;
  try { if (window.top && window.top.document) top = window.top; } catch (e) { top = window; }
  var tauri = window.__TAURI__ || top.__TAURI__;
  if (!tauri || !tauri.core || !tauri.core.invoke) return;      // ブラウザ版: 何もしない

  function invoke(cmd, args) {
    return tauri.core.invoke(cmd, args || {}).catch(function (err) {
      if (window.console) console.error("外枠に頼めませんでした: " + cmd, err);
    });
  }

  /** アプリの中なら別の窓、外なら既定のブラウザ。 */
  function open(href, title) {
    var url;
    try { url = new URL(href, location.href); } catch (e) { return; }
    if (url.origin === location.origin) {
      invoke("open_window", { url: url.pathname + url.search + url.hash, title: title || null });
    } else if (url.protocol === "http:" || url.protocol === "https:") {
      invoke("open_external", { url: url.href });
    }
  }

  // window.open: 3機能とも "noopener" で呼び、戻り値は使っていない(null と同じに返す)
  var nativeOpen = window.open;
  window.open = function (url, target) {
    if (!url) return nativeOpen.apply(window, arguments);
    open(String(url), "");
    return null;
  };

  // target="_blank" のリンク(外枠の窓は新しいタブを持たない)
  document.addEventListener("click", function (e) {
    var link = e.target && e.target.closest && e.target.closest('a[target="_blank"]');
    if (!link || !link.href || e.defaultPrevented) return;
    e.preventDefault();
    open(link.href, (link.textContent || "").trim());
  }, false);

  // 閉じる・裏に回る瞬間の合図(sendBeacon)。3機能とも画面の持ち主・心拍に使う。
  // 外枠の宛先(独自の宛先)では、そのままだと次のことが起きた(Linux の試しの起動で確かめた):
  //   - sendBeacon が「HTTP(S) でしか送れない」と例外を投げる(`app://`)
  //   - 本文が Blob の POST を外枠へ送ると、WebKit の窓ごと落ちる(文字・ArrayBuffer は届く)
  // そこで**どの OS でも fetch で送り、Blob は文字にしてから送る**。keepalive(閉じる途中でも
  // 送りきる)は http(s) の宛先(Windows の `http://app.localhost`)だけに付ける
  if (navigator.sendBeacon) {
    var keep = location.protocol === "http:" || location.protocol === "https:";
    var post = function (url, body, type) {
      var init = { method: "POST", credentials: "same-origin", keepalive: keep };
      if (body !== undefined && body !== null) init.body = body;
      if (type) init.headers = { "Content-Type": type };
      try { fetch(url, init).catch(function () {}); } catch (e) { /* 送れなくても止めない */ }
    };
    navigator.sendBeacon = function (url, data) {
      if (typeof Blob !== "undefined" && data instanceof Blob) {
        var type = data.type;
        data.text().then(function (text) { post(url, text, type); });
      } else {
        post(url, data);
      }
      return true;
    };
  }

  // いちばん外の窓(帳票・印刷ビューの窓)の「閉じる」は外枠に閉じてもらう。
  // 統合画面の窓(main)を閉じると、外枠が「終了しますか」の確認を通す
  if (window === top) {
    window.close = function () { invoke("close_window"); };
  }
  document.documentElement.setAttribute("data-desktop", "1");
})();
