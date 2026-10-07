//! 外枠が自分で出す画面。**Python がまだ居ない / 居なくなった**ときだけ使う。
//! ふだんの画面(待機画面も含む)は Python が返す。

use crate::bridge::Failure;

fn escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

const STYLE: &str = r#"
 body{margin:0;min-height:100vh;display:grid;place-items:center;background:#eef1f5;color:#101720;
      font-family:system-ui,"Yu Gothic UI","Meiryo UI",sans-serif;line-height:1.7}
 .box{width:min(640px,calc(100vw - 48px));background:#fff;border:1px solid #c9d2dc;border-radius:4px;
      padding:28px 32px;box-shadow:0 6px 20px rgba(16,23,32,.08)}
 h1{margin:0 0 12px;font-size:19px}
 h1.ng{color:#b4232a}
 .hint{margin-top:16px;padding:14px;background:#fdeaea;border-left:4px solid #b4232a;white-space:pre-wrap}
 dt{color:#556171;font-size:13px;margin-top:14px}
 code,pre{font-family:ui-monospace,Consolas,monospace;font-size:12px;background:rgba(0,0,0,.05);
          padding:2px 5px;border-radius:2px;word-break:break-all}
 pre{padding:10px;max-height:16em;overflow:auto;white-space:pre-wrap}
 button{font:inherit;padding:8px 18px;margin-top:16px;cursor:pointer}
 .note{color:#556171;font-size:13px;margin-top:4px}
"#;

/// Python が「受け付け始めた」と言う前の、ほんの一瞬に出す画面。
/// 1秒ごとに読み直し、Python の待機画面(段と進み具合が出る)へ移る。
pub fn starting() -> String {
    format!(
        r#"<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="1"><title>コイル梱包ツール</title><style>{STYLE}</style></head>
<body><main class="box"><h1>起動しています…</h1><p>しばらくお待ちください。</p></main></body></html>"#
    )
}

/// 移り先へ移るだけのページ(画面の 302 の代わり)。
/// 移り先はアプリの中の経路だけにする(外へは移らない)。
pub fn moving_to(target: &str) -> String {
    let inside = target.starts_with('/') && !target.starts_with("//");
    let target = if inside { target } else { "/" };
    // `</script>` で抜け出させない(JSON は `<` をそのまま書くので、自分で崩す)
    let quoted = serde_json::to_string(target)
        .unwrap_or_else(|_| "\"/\"".into())
        .replace('<', "\\u003c");
    format!(
        r#"<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="0;url={url}"><title>コイル梱包ツール</title></head>
<body><script>location.replace({quoted});</script></body></html>"#,
        url = escape(target)
    )
}

/// 起動できなかった / 途中で止まった。**次に何をすればよいか**まで出す。
pub fn failure(failure: Option<&Failure>, python: &str, stderr: &[String], log_hint: &str) -> String {
    let (message, hint, log_dir) = match failure {
        Some(f) => (f.message.clone(), f.hint.clone(), f.log_dir.clone()),
        None => (
            "Python の処理が止まりました".to_string(),
            "もう一度起動してください。続けて起きるときは、下の「最後の出力」と\
             ログを担当に送ってください。"
                .to_string(),
            String::new(),
        ),
    };
    // Python が知らせた場所は、実際に置かれている場所(Microsoft Store の Python は
    // %LOCALAPPDATA% の下を別の場所に置く。Python の `app_config.real_location`)。
    // 知らせが無いときは外枠の見立てで、Store の Python なら別の場所にあると添える
    let guessed = log_dir.is_empty();
    let log_dir = if guessed { log_hint.to_string() } else { log_dir };
    let tail = stderr.iter().rev().take(25).rev().cloned().collect::<Vec<_>>().join("\n");
    format!(
        r#"<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>起動できませんでした</title>
<style>{STYLE}</style></head><body><main class="box">
<h1 class="ng">起動できませんでした</h1>
<p>{message}</p>
{hint}
<dt>ログの場所</dt><p><code>{log_dir}</code></p>{store}
<dt>使った Python</dt><p><code>{python}</code></p>
{tail}
<button type="button" onclick="location.reload()">もう一度試す</button>
</main></body></html>"#,
        message = escape(&message),
        hint = if hint.is_empty() { String::new() } else { format!(r#"<div class="hint">{}</div>"#, escape(&hint)) },
        log_dir = escape(if log_dir.is_empty() { "(分かりません)" } else { &log_dir }),
        store = if guessed { STORE_NOTE } else { "" },
        python = escape(if python.is_empty() { "(見つかっていません)" } else { python }),
        tail = if tail.is_empty() {
            String::new()
        } else {
            format!("<dt>最後の出力</dt><pre>{}</pre>", escape(&tail))
        },
    )
}

/// 外枠の見立てのログの場所に添える(Microsoft Store の Python はファイルを別の場所に置く)
const STORE_NOTE: &str = r#"<p class="note">Microsoft Store から入れた Python のときは、
<code>%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.…\LocalCache\Local\CoilPackingTools\logs</code>
にあります。</p>"#;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 理由と次の一手と出力が出て_タグは無害化される() {
        let f = Failure { message: "<b>だめ</b>".into(), hint: "pip で入れる".into(), log_dir: "C:\\logs".into() };
        let html = failure(Some(&f), "python.exe", &["Traceback <x>".into()], "");
        assert!(html.contains("&lt;b&gt;だめ&lt;/b&gt;"));
        assert!(html.contains("pip で入れる"));
        assert!(html.contains("C:\\logs"));
        assert!(html.contains("Traceback &lt;x&gt;"));
        assert!(html.contains("python.exe"));
        assert!(!html.contains("LocalCache"), "Python が知らせた場所だけを出す");
    }

    #[test]
    fn 場所の知らせが無ければ見立てとstoreのpythonの場所を出す() {
        let html = failure(None, "python.exe", &[], "C:\\Users\\u\\AppData\\Local\\CoilPackingTools\\logs");
        assert!(html.contains("AppData\\Local\\CoilPackingTools\\logs"));
        assert!(html.contains("LocalCache\\Local\\CoilPackingTools\\logs"));
    }

    #[test]
    fn 移り先はアプリの中だけ() {
        assert!(moving_to("/lot?x=1").contains(r#"location.replace("/lot?x=1")"#));
        assert!(moving_to("https://evil.example/").contains(r#"location.replace("/")"#));
        assert!(moving_to("//evil.example/").contains(r#"location.replace("/")"#));
        let tricky = moving_to("/a\"</script><script>alert(1)</script>");
        assert_eq!(tricky.matches("</script>").count(), 1, "{tricky}");
    }

    #[test]
    fn 理由が無ければ止まったと言う() {
        let html = failure(None, "", &[], "D:\\ログ");
        assert!(html.contains("止まりました"));
        assert!(html.contains("D:\\ログ"));
        assert!(html.contains("見つかっていません"));
    }
}
