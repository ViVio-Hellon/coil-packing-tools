# コイル梱包ツール (Python / Web版)

アルミコイルの梱包で使う3つの Python Web ツールを、**1つの Flask サーバ・
1つのブラウザ画面(タブ)** で使えるようにしたもの。

| タブ | 機能 | 移植元 | 版 |
|---|---|---|---|
| 梱包明細 | 梱包明細表の打ち出し(`frmCoilPacking`) | `ViVio-Hellon/packing-details-python-web` | VER 0.13.1 |
| ペナラベル | テスラ向け出荷ラベルの発行と風袋計算 | `ViVio-Hellon/packing-pena-label-python-web` | v1.5.0 |
| 資材計算 | パレット・リプラ・緩衝材の員数、チェックリスト、発注票 | `ViVio-Hellon/packing-material-calculation-python-web` | VER 0.2.0 |

**統合とは、機能を1つにまとめることではありません。** 3機能の計算・入力チェック・
DB処理・帳票は移植元のまま、それぞれ独立して動きます。変えたのは「入れ物」
(起動・停止・待ち受け・画面の外枠・ログ)だけです。何をどう決めたかは
[`docs/統合設計.md`](docs/統合設計.md) にあります。各機能の業務仕様は
`modules/<機能>/README.md`(移植元のまま)を見てください。

## 起動と停止

| ファイル | 用途 |
|---|---|
| `Start.vbs` | **ふだんはこれをダブルクリック。** コンソールを出さずに起動し、ブラウザに統合画面が開きます |
| `start.bat` | 起動しないときの診断用。コンソールに理由が出ます(`start.bat --check` は環境の確認だけ、`start.bat --diagnostic` は細かいログまで残す) |
| `stop.bat` | 明示的に止めるとき(3機能とも終わります)。資材計算の取り込み中は止めずに知らせます。中断してよければ `stop.bat --force` |
| `run.py` | `python run.py` で起動(`start_app.py` と同じ入口) |

- ブラウザの画面(統合画面)を閉じると、少し待ってからバックエンドも終わります
  (3機能の画面と外枠のどれか1つでも開いていれば終わりません)。
  **裏に回しても・スリープしても終わりません。** 作業の途中経過は各機能が
  操作のたびに保存しているので、開き直せば同じところから続けられます
- 画面右上の「終了」で止めることもできます(3機能とも終わります)
- 2回起動しても2つ目は上がらず、開いている画面へ合流します
- ポートは **8740**(使えなければ 8741〜8743)。`config/app.json` で変えられます

## 必要環境

- Python 3.9 以上、`pip install -r requirements.txt`(Flask と waitress の2つだけ)
- ラインPCには追加ライブラリを入れられない前提のまま。取り込み元の読み書きは標準ライブラリ
  (`sqlite3`)。ペナラベルの Access(.accdb)は `cscript.exe` + VBScript + ADODB + ACE 経由
  (`modules/packing_pena_label/app/repositories/vbs/`)
- フロントエンドにビルド工程(npm / webpack)は持ち込んでいません

## 画面の作り

```
上の帯   コイル梱包ツール VER1.0.9 [梱包明細] [ペナラベル] [資材計算]   接続OK [終了]
中身     見せているタブの機能の画面(iframe)。ほかのタブは隠しているだけで消えない
```

- タブを切り替えても、入力の途中の値・条番号の盤面・計算結果はそのまま残ります
- 各機能の画面は移植元と同じです(見た目・操作・設定画面・マスタ管理・印刷)。
  URL に機能の入口が付きます: `/details/…` `/pena/…` `/material/…`
- 帳票・印刷ビューは今までどおり別のタブで開きます
- 各機能の「画面は1枚だけ」の決まりは機能ごとです。統合画面を2枚開くと、3つの
  機能がそれぞれ「別の画面で開いています」になります。「この画面で使う」はタブごとに押します

## ディレクトリ構成

```
Start.vbs / start.bat / stop.bat   利用者の入口(CP932・CRLF)
start_app.py / run.py               Python側の起動開始点(どちらも同じ)
app.py                              統合 Flask アプリの組み立て(タブ・3機能の登録)
launch_guard.py                     多重起動の判定・起動の順番待ち
boot_server.py / server.py          起動待機画面 → 本体への引き継ぎ(waitress)
process_manager.py                  安全な停止
config/app.json                     統合アプリのID・表示名・版・ポート
common/                             3機能で共有する入れ物
  app_config.py  modes.py           統合アプリの固有値
  logging_utils.py                  ログ(3機能で1つのファイル、日付で切る)
  idle_exit.py                      自動終了の見張り(プロセスに1つ)
  security.py                       Host 検証・同一オリジン・トークン・応答ヘッダ
  sqlite_toolkit.py  boot_screen.py 梱包明細・資材計算で同一だったもの
templates/base.html  index.html     統合画面の外枠とタブ
static/css/shell.css  js/shell.js   外枠だけの CSS / JS(心拍・タブ・終了)
modules/
  packing_details/                  梱包明細   (meisai/ + app/ + config/ + docs/ + tests/)
  packing_pena_label/               ペナラベル (app/ + server.py〈Flask への取り次ぎ〉+ data/ + assets/ + tests/)
  packing_material_calculation/     資材計算   (coil_tool/ + app/ + config/ + docs/ + tests/)
tests/                              統合の試験(タブ・入口・トークン・心拍・停止・起動ファイル)
tools/smoke_shell.py                起動から停止までの通し(手動)
tools/e2e_scenarios.py              3機能を現場と同じように使う通し(一連の流れ・交互・放置。手動)
scripts/make_dist.py                配布用フォルダを作る
docs/統合設計.md                    調査・比較・採用した実装・影響・テスト
```

## 手元のファイルの置き場所

3機能の手元DB・設定は**移植元の領域のまま**です(統合前に配った端末のデータをそのまま使う)。

| | 置き場所 |
|---|---|
| 梱包明細の手元DB・設定 | `%LOCALAPPDATA%\PackingDetails\data\` |
| ペナラベルの状態DB・設定 | `%LOCALAPPDATA%\PackingPenaLabel\runtime\` `…\config\` |
| 資材計算の作業用DB・設定 | `%LOCALAPPDATA%\CoilMaterialTool\data\` |
| 統合アプリのロック・**ログ(3機能ぶん1つ)** | `%LOCALAPPDATA%\CoilPackingTools\runtime\` `…\logs\coil_packing_tools_YYYYMMDD.log` |
| Python のキャッシュ(`__pycache__`。アプリのフォルダには作らない) | `%LOCALAPPDATA%\CoilPackingTools\pycache\` |
| 配布設定(各機能の設定画面で書き出す) | アプリ直下 `配布設定\<機能>\` |
| CSV などの書き出し先 | アプリ直下 `export\<機能>\` |

共有フォルダの設定(梱包明細の右上の文字・管理者パスワード、梱包資材マスタ、仕掛台帳)は
各機能の移植元のとおりです。

## 設定

「設定画面」も「設定値」も**機能ごと**のままです(1つにしていません)。各タブの中の
設定画面で、その機能の置き場所・パスワード・配布設定を扱います。統合アプリ自身の
設定は `config/app.json`(ポート・表示名・版)だけです。

## 版

版は**統合ツールの版と3機能の版を分けて**持ちます。画面の帯の版を押すと一覧が出ます。

| | 版 | 出どころ |
|---|---|---|
| コイル梱包ツール(統合ツール) | 1.0.9 | `config/app.json` |
| 梱包明細 | 0.13.6 | `modules/packing_details/config/app.json` |
| ペナラベル | 1.5.5 | `modules/packing_pena_label/app/config.py` の `APP_VERSION` |
| 資材計算 | 0.2.5 | `modules/packing_material_calculation/config/app.json` |

機能の中身を変えたら、その機能の版と統合ツールの版の両方を上げます。統合画面や共通部分
だけを変えたら、統合ツールの版だけを上げます。決まりの全体は `docs/変更履歴.md`。

## 印刷

3機能とも、これまでどおり各画面の印刷ボタンで刷ります。帳票・印刷ビューは別のタブで開き、
**開いているあいだはサーバが止まりません**(統合画面のタブを閉じても、帳票で書き足した
値の保存や発注票の印刷は続けられます)。統合画面で **Ctrl+P** を押すと、いま見せている
機能の画面が刷られます。ブラウザのメニューから印刷すると、見せている画面の1枚目の分しか
出ないので、各画面の印刷ボタンか Ctrl+P を使ってください。

**文字・罫線・バー・目盛りは、すべて紙の端から 5mm 以上内側に置きます**(プリンターは紙の縁
約 4mm に刷れないため)。距離は印刷ダイアログの余白ではなく紙の中で取っているので、ダイアログが
「デフォルト」でも「余白なし」でも同じ位置に刷れます。紙面を直したときは、開発用の PC で
次を流して確かめてください(PyMuPDF と Playwright が要ります。現場の PC には要りません)。

```
python tools/print_edge_check.py
```

## テスト

```
python -m unittest discover -s modules/packing_details/tests -t .        # 梱包明細   463
python -m unittest discover -s modules/packing_pena_label/tests -t .     # ペナラベル 586
python -m pytest modules/packing_material_calculation/tests              # 資材計算   432
python -m pytest tests                                                   # 統合       174
python tools/smoke_shell.py                                              # 通し(配布前に一度)
python tools/e2e_scenarios.py                                           # 3機能の一連の流れ・交互・放置(約20分。--quick で約9分)
python tools/print_edge_check.py                                         # 印刷の端 5mm(紙面を直したら)
```

3機能の試験は移植元のものを(import パスだけ直して)そのまま流しています。
そのうち画面・API を叩く試験は、統合の試験(`tests/test_modules_mounted.py`)が
**統合アプリに入口とトークン付きで載せた形**でもう一度流します(`tools/pytest_mounted.py`)。
統合前後の件数と結果は `docs/統合設計.md` §6。

## 配布

`python scripts\make_dist.py`(またはペナラベルの設定画面の「配布用フォルダを作る」)で、
配るものだけを新しいフォルダへ写します(tests・tools・手元DBの写し・ログは入りません)。
配った先では `Start.vbs` を押すだけです。各機能の配布設定(`配布設定\<機能>\`)は
そのフォルダごと配れば、配った先が起動時に読み込みます。配布用フォルダを作るときは、
書き出してある3機能ぶんの配布設定をまとめて入れます。`--no-settings`(画面では
「配布設定を入れない」)を選ぶと、どの機能の配布設定も入れません。
