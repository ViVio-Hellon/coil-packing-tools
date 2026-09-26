# VBA 全体解析書（移植前 現行仕様の確定）

対象: テスラ向けコイル梱包 ラベル発行 / 風袋（梱包資材）重量計算システム（Excel VBA / .xlsm）

解析対象ファイル
- `標準モジュール群`（7,973行）… 本文中では「標準」と表記
- `UserForm モジュール群`（1,433行）… 本文中では「UF」と表記
- `型番.xlsx`（型番マスタのシート実体）
- `1.0mm×53.5mm 丈1.xlsx`（ラベル台紙シートの実体サンプル）

> 注: 標準モジュールの 1296〜3581行、4900〜5177行 は **全行コメントアウトされた旧版**
> （旧 `計算1` / `計算2` / `All計算` / `HB計算`）である。現行動作には一切関与しない。
> ただし「旧仕様がどうだったか」の根拠として本書では参照する。

---

## 1. このシステムは何をしているのか

コイル（帯鋼）を梱包・出荷する工程で、次の 2 つを行う **ライン端末用の Excel ツール**。

1. **出荷ラベルの発行**
   検査番号・型番・重量・コイル番号（連番）・バーコード文字列を、
   サイズごとに用意された専用シート（ラベル台紙）へ一括転記して印刷する。

2. **風袋（梱包資材）重量の計算**
   梱包資材マスタ（Access）から資材ごとの「単位質量 × 係数」を読み、
   コイル積み本数・コイル巾・チップボール径から
   ピン／チップボール／ストレッチフィルム／エサフォーム／ポリシート／
   ハードボード／樹脂パレット／PETバンド／EX-DRY の各重量を算出し、
   **NW（正味）/ 風袋 / GW（総重量）/ 梱包高さ** を求めてラベルへ反映・帳票印刷する。

「指定サイズ」＝定番 7 サイズ専用の高速入力画面、
「全サイズ」＝型番マスタから任意サイズを選び台紙シートを都度生成する汎用画面、
という **2 系統の入口** を持つ。

---

## 2. モジュール構成

### 2.1 標準モジュール（論理的な区分）

VBA 側は 1 ファイルに連結されているが、`Option Explicit` の出現位置と
先頭コメントから、元は以下のモジュールに分かれていたと判断できる。

| # | 論理モジュール名 | 行範囲 | 役割 |
|---|---|---|---|
| 1 | `DebugLog` | 1〜456 | ログ出力、ライン名判定、レジストリ読取、ファイル存在確認 |
| 2 | （日報連携） | 456〜486 | `IsBookOpened` / `ListOen`（日報ブックを開く） |
| 3 | `modTeslaConfig` / `modTeslaWrite` | 487〜1029 | ラベル転記列定数、`WriteCoilInfo`、`ラベル転記OP/CK`、`羅列計算`、`クリア` |
| 4 | （印刷） | 1030〜1295 | `印刷設定`（罫線＋印刷範囲＋ヘッダー/フッター） |
| 5 | **資材計算本体** | 3582〜5177 | `DB_Materials`、`CalculateMaterials`、各資材計算、`DB_HB`、`風袋罫線設定` |
| 6 | `ADO`（Access接続） | 5178〜5760 | ACE OLEDB 経由の Access 読み書き汎用ライブラリ |
| 7 | `ListView`（UI部品） | 5760〜6489 | ListView 描画汎用ライブラリ（業務ロジックなし） |
| 8 | `modTeslaConfig`（本体） | 6490〜7050 | **サイズ構成マスタ**、ラベル座標定数、`WriteAllToSheet`、`DetectSelectedOB` 等 |
| 9 | （全サイズ用） | 7050〜7620 | `シート追加1/2`、`コピー1/2`、`入力`、`型番入力`、`印刷範囲1/2`、`印刷実行` |
| 10 | （汎用ユーティリティ） | 7620〜7973 | パス確認、最終行取得、全半角変換、マージソート等 |

### 2.2 クラスモジュール

**クラスモジュールは存在しない。** 代わりにユーザー定義型（`Type`）を 3 つ使用。

| 型名 | 用途 | メンバ |
|---|---|---|
| `MaterialFieldIndices` | Access のフィールド位置 | 管理番号 / 梱包資材名 / 単位質量 / 係数 |
| `MaterialWeights` | 資材計算の結果一式 | col, TA(Single), KTA(Single), PIN, TIP, SUT, ESA, HB, POR, PET, PAR, NW, HU, GW, DRY, TempDiameter, HalfDiameter, GAI, 各`〜式` |
| `hbData` | ハードボード専用 | col, TA(**Double**), GAI, HBValue, HBFormula, HBFlag, HB固定値, HB530固定値, HB計算用, HB八角用 |

> **重要**: `MaterialWeights.TA` は `Single`（単精度）、`hbData.TA` は `Double`。
> エサフォームの `TA > 0.82` 判定が単精度で行われるため、境界値での挙動に影響する。

### 2.3 UserForm

| フォーム名 | 実装有無 | 役割 |
|---|---|---|
| `テスラ指定サイズ` | **あり**（UF 1〜1024行） | メイン画面。定番 7 サイズ × 丈1/丈2 |
| `テスラ全サイズ` | **あり**（UF 995〜1433行） | 型番マスタから任意サイズを選択。台紙シートを都度生成 |
| `表記用` | コード未提供 | 計算内訳（値＋計算式）の表示。参照フィールド名は判明（下記） |
| `UFProgress` | コード未提供 | プログレスバー（`ProgressBar1`, `Label1`, `LabelProgress`） |
| `msg` | コード未提供 | 旧プログレス（`ProgressLabel`, `LL`） |
| `UserForm4` | コード未提供 | 用途不明。`フォーム4起動` で表示、`QueryClose` で破棄 |
| `警告` | コード未提供 | 警告表示。`QueryClose` で破棄 |
| `UF_Material` | コード未提供 | `RowCount` プロパティのみ参照（`Sheet_Color`） |

`表記用` が持つコントロール（標準モジュールからの参照で確定）:
`PI, TIP, SUT, ESA, POR, PAR, PET, HB, NW, DRY, HU, GW`（値）
`PIsiki, TIPsiki, SUTsiki, ESAsiki, PORsiki, PARsiki, PETsiki, HBsiki, NWsiki, HUsiki, GWsiki, DRYsiki`（計算式）
`ListViewMaterials`（資材マスタ一覧）

### 2.4 シートモジュール

シートモジュールのコードは提供されていない。
`Auto_Open`（標準モジュール）が `起動` シートを選択し `テスラ指定サイズ` を表示する。

---

## 3. Public / Private 一覧（現行有効分のみ）

### 3.1 サイズ構成・座標（`modTeslaConfig`）

| 種別 | 名前 | 概要 |
|---|---|---|
| Public Const | `COL_COILH=43` `COL_TA=48` `COL_NW=53` `COL_GW=59` | ラベル台紙の「本数/高さ/NW/GW」書込列 |
| Public Const | `OFS_BC_KATA=-1` `OFS_COIL=0` `OFS_KENSA=0` `OFS_KATABAN=1` `OFS_WEIGHT=2` `OFS_BC_KEN=3` | ラベル1枚内の行オフセット |
| Public Const | `COL_COIL_L=7 / _R=39`, `COL_KEN_L=4 / _R=36`, `COL_KATA_L=4 / _R=36`, `COL_WT_L=4 / _R=36`, `COL_BC_KATA_L=9 / _R=41`, `COL_BC_KEN_L=2 / _R=34` | 左面/右面の列 |
| Public Const | `HDR_KEN_ROW=3, HDR_KEN_COL=22`, `HDR_KEN2_ROW=92`, `HDR_WT1_ROW=3, HDR_WT1_COL=37`, `HDR_WT2_ROW=92` | ヘッダー部座標 |
| Public Function | `GetSizeConfig(obIdx)` | **サイズ構成マスタ**（後述） |
| Public Function | `GetSizeDisplayName(obIdx)` | 表示名 |
| Public Function | `GetTakeType(obIdx)` | 奇数=丈1 / 偶数=丈2 |
| Public Function | `BuildSheetName(obIdx)` | `cfg(0) & " 　丈" & takeType` |
| Public Function | `GetLabelBaseRows()` | ラベル16枚の基準行 |
| Public Sub | `GetCBtoPair(cbIdx, ob1, ob2)` | CheckBox → OBペア |
| Public Function | `DetectSelectedOB(frm)` | 選択中 OptionButton → 1〜14 / 0 |
| Public Function | `GetSelectedOBSheetName()` / `GetSelectedCBDisplayName()` | 印刷確認用 |
| Public Function | `GetSelectedCoilWidth()` | 選択サイズの巾（mm） |

### 3.2 ラベル転記

| 種別 | 名前 | 概要 |
|---|---|---|
| Public Sub | `WriteAllToSheet(ws, ken, wt, kataban, takeType)` | ラベル32枚＋ヘッダーを一括転記 |
| Public Sub | `WriteCoilInfo(ws, dataRow, extraRow, CoilH1,2, TA1,2, NW1,2, GW1,2)` | 本数/高さ/NW/GW を転記 |
| Sub | `ラベル転記OP()` | OB（単丈）モードの `WriteCoilInfo` 呼出 |
| Sub | `ラベル転記CK()` | CB（丈1,2同時）モードの `WriteCoilInfo` 呼出 |
| Public Sub | `チェック入力()` | CBモードの `WriteAllToSheet` 呼出（丈1/丈2 両方） |
| Public Sub | `チェック印刷()` | CBモードの 2 シート印刷 |
| Public Function | `InpCheck(a)` | 重量未入力チェック（1=丈1, 2=丈2, 3=丈1,2） |
| Sub | `クリア()` | フォームの NW/GW/TA と 全14シートの本数列をクリア |

### 3.3 資材計算

| 種別 | 名前 | 概要 |
|---|---|---|
| Public Function | `DB_Materials(CoilText, coilNo)` | **主関数**。マスタ取得→計算→フォーム反映→シート転記 |
| Private Function | `CalculateMaterials(...)` | 各資材の計算を束ねる |
| Public Sub | `SetCoilDimensions(mat, CoilText, coilNo)` | 巾 col・高さ TA・梱包高さ KTA |
| Public Sub | `SetDiameterInfo(mat)` | チップボール径→外径/半径/外周 GAI |
| Public Function | `CalculatePIN/TIP/SUT/ESA/POR/PET/PAR/DRY` | 各資材重量 |
| Public Function | `CalculateNW(CoilText, coilNo, T1, T2)` | 正味重量 |
| Public Function | `DB_HB(CoilText, coilNo)` | ハードボード（**独自のTA計算**） |
| Public Function | `CalculateHBData` / `GetHBCoilDimensions` / `GetHBMaterialData` / `JudgeHBFlag` / `CalculateHBValue` | HB 内訳 |
| Public Function | `GetFieldIndices(Hikifields)` | フィールド位置解決 |
| Function | `GetUnitMassAndCoefficient(TempHiki, 資材名, ...)` | 単位質量・係数の取得。**不一致時は `Array("","")`** |
| Public Sub | `SetCoilFormValues` / `SetDisplayFormValues` / `SetHBDisplayForm` | フォーム反映 |
| Public Sub | `WriteToSheet` / `WriteSheetValues` / `WriteSheetFormulas` / `WriteHBToSheet` | 風袋計算シート転記 |
| Sub | `羅列計算()` | 1〜50本の一覧表（`50まで` シート） |
| Public Function | `CalcHBForList(col, coilCount, TA, GAI, ...)` | 羅列計算専用 HB |

### 3.4 Access アクセス（ADO）

`GetRecordsArr(filePath, tableName, SQLfilter)` / `GetFieldsArr(...)` が業務側の入口。
接続文字列は `Provider=Microsoft.ACE.OLEDB.12.0;Data Source=` 固定。
`adoConnection` は拡張子で Provider を切替（`accdb/xlsx/xlsm/xlsb/csv`→ACE12、`mdb/xls`→Jet4）。

### 3.5 ログ

`DebugLog(message, useWorkbookPath)` / `InitializeDebugLog` / `CloseDebugLog` / `ResetDebugLog` / `WriteLog(msg, sep, path)` /
`UserLog` / `SetUserLogBox` / `ClearUserLog` / `GetMyLineName` / `ExtractCommonLineName` / `SanitizeRegistryValue` / `ReadCheck`

---

## 4. 外部依存

### 4.1 外部 DB（Access）

| 項目 | 値 |
|---|---|
| ファイル | `<PATH_AIM_参照>梱包資材マスタ.accdb` |
| テーブル | `資材重量` |
| 使用フィールド | `管理番号` / `梱包資材名` / `単位質量` / `係数` |
| 接続 | `Microsoft.ACE.OLEDB.12.0` |
| 読取専用 | `adOpenStatic` + `adLockReadOnly` |

`梱包資材名` で検索されるキー（**ハードコード**）:

```
テスラピン / チップボール1000ф / チップボール950ф / チップボール820ф /
ストレッチフィルム / エサフォーム / ポリシート / PETバンド / 樹脂パレット / EX-DRY /
ハードボード / 八角ハードボード / HB590*2000 / HB530*2000 /
HB580*2000 / HB500*2000 （※580/500 は 羅列計算 で取得のみ・未使用）
```

### 4.2 外部ブック

| ファイル | 用途 | 呼出元 |
|---|---|---|
| `<ThisWorkbook.path>\日報＆資材計算.xlsm` | 日報。`メイン` シートを表示 | `ListOen()`（日報ボタン） |

### 4.3 未定義（外部共通モジュール）の定数

本ファイル内に定義がなく、**別の共通モジュール／アドインに存在する**と判断されるもの:

- `PATH_AIM_参照` … 梱包資材マスタ.accdb の親フォルダ（UNC想定）
- `PATH_梱包_日報DebugPrint` … DebugLog の共有出力先

### 4.4 レジストリ

`GetSetting` でライン名を取得（上から順に試行、最初の非空を採用）:

| 順 | AppName | Section | Key |
|---|---|---|---|
| 1 | `MyApp` | `UFdaily` | `LineOption` |
| 2 | `梱包資材管理` | `Config` | `Position` |
| 3 | `コイルライン工程管理カレンダー` | `設定` | `PCライン` |

取得値は `SanitizeRegistryValue`（制御文字・全角空白除去）→ `ExtractCommonLineName`（`opt`接頭辞除去、表記ゆれ統一）で正規化。
`LS`→`機側`、`L1`→`L-1`、その他（`作業長` `コイル` `大板小板`）はそのまま。どれも取れなければ `Unknown`。

### 4.5 Windows API / COM

`Sleep`(kernel32), `GetDC/CreateCompatibleDC/SelectObject/DeleteObject/ReleaseDC/CreateFont/DrawText`(user32,gdi32),
`GetWindowLong/SetWindowLong/GetActiveWindow/DrawMenuBar`(user32),
`Scripting.FileSystemObject`, `WScript.Network`, `WbemScripting.SWbemLocator`(WMI), `ADODB`, `ADOX.Catalog`

### 4.6 ワークシート依存

| シート名 | 用途 |
|---|---|
| `起動` | ホーム。各処理の最後に `Activate` |
| `型番` | 型番マスタ。A列=サイズ名, B列=記号, C列=型番 |
| `原本` | 全サイズモードの台紙テンプレート（`A1:BJ189`） |
| `<サイズ> 　丈1` / `丈2` × 7サイズ = 14枚 | 指定サイズのラベル台紙 |
| `風袋計算` | 資材計算の帳票（毎回削除→再作成） |
| `50まで` | 羅列計算の帳票（毎回削除→再作成） |
| `<ComboBox値>1` / `2` | 全サイズモードで都度生成 |
| `設定` | **旧版でのみ使用**（コメントアウト済み）。現行は Access から取得 |

---

## 5. 入力データ

### 5.1 テスラ指定サイズ（メイン画面）

| コントロール | 種別 | 内容 | 制約 |
|---|---|---|---|
| `OptionButton1〜12`, `lblSpec_08_53_1/2` | 選択 | サイズ×丈（単丈モード） | 排他 |
| `CheckBox1〜6`, `ck_08_53` | 選択 | サイズ（丈1,2同時モード） | 排他 |
| `TIP1000 / TIP950 / TIP820` | 選択 | チップボール径 | 必須 |
| `txtKensaNo` | 入力 | 検査番号 | 英数のみ。小文字→大文字強制 |
| `txtWeight1` / `txtWeight2` | 入力 | 1条重量（丈1 / 丈2） | 数値のみ（`-` は許容） |
| `CoilH1〜4` | 入力 | 積み本数（丈1:1梱包目/2梱包目、丈2:1梱包目/2梱包目） | 数値のみ。小文字→大文字強制 |

`CoilH1`=丈1の1梱包目、`CoilH2`=丈1の2梱包目、`CoilH3`=丈2の1梱包目、`CoilH4`=丈2の2梱包目。
内部の `coilNo` 1〜4 がこれに対応する。

### 5.2 テスラ全サイズ

| コントロール | 内容 |
|---|---|
| `ComboBox1` | 型番シートのA列（サイズ名）から選択 |
| `CheckBox1` / `CheckBox2` | コイル副番 2ケタ / 3ケタ（排他） |
| `TextBox1` | 検査番号 |
| `TextBox2 / 3` | 重量（丈1 / 丈2、2ケタ時） |
| `TextBox4 / 5` | 重量（1-2-? / 2-2-?、3ケタ時のみ表示） |

---

## 6. サイズ構成マスタ（ハードコード）

`GetSizeConfig(obIdx)` → `Array(シート名ベース, 型番, 記号, データ行, 重量追加行)`

| obIdx | シート名ベース | 型番 | 記号 | データ行 | 追加行 | 巾(mm) | CheckBox |
|---|---|---|---|---|---|---|---|
| 1, 2 | `1.0mm×73mm` | `BJB7604000QR` | c | 3 | 0 | 73 | CheckBox3 |
| 3, 4 | `0.6mm×82.5mm` | `BJB7604200QR` | e | 3 | 0 | 82.5 | CheckBox4 |
| 5, 6 | `1.0mm×53.5mm` | `BJB7606500QR` | d | 3 | 92 | 53.5 | CheckBox5 |
| 7, 8 | `1.0mm×40.0mm` | `BJB7604300QR` | b | 3 | 92 | 40 | CheckBox1 |
| 9, 10 | `1.0mm×33.0mm` | `BJB7603900QR` | a | 3 | 92 | 33 | CheckBox2 |
| 11, 12 | `1.0mm×63.0mm` | `BJB7605900QR` | f | **92** | 0 | 63 | CheckBox6 |
| 13, 14 | `0.8mm×53.5mm` | `BJB7610400QR` | g | 3 | 92 | 53.5 | ck_08_53 |

- 奇数 obIdx = 丈1、偶数 = 丈2。
- シート名 = `シート名ベース & " 　丈" & 丈番号`（全角スペース1つ + 半角スペース…実体は `" 　丈"`）。
- `1.0mm×63.0mm` のみ **データ行が 92**（画面表示の読取行が他と異なる）。
- **上表の文字列は VBA のまま**。`1.0mm×73mm` だけ小数点が無いのは当時の
  シート名がそのまま入っているため。移植側では表記を統一して出す（解析書 C-5）。

### 6.1 `GetSelectedCoilWidth` と `SetCoilDimensions` の巾決定の差異

- `GetSelectedCoilWidth`（羅列計算で使用）はシート名から巾を抽出するため
  **`1.0mm×53.5mm` は 53.5**、`ck_08_53`（名前付きCB）は **53.5 固定値**。
- `SetCoilDimensions` / `GetHBCoilDimensions` は **Select Case のハードコード**。
  `CheckBox5`→53.5、`OptionButton5 Or 6`→53.5。

> **注意**: `CommandButton13`（積み高さクイック計算）だけは
> `CheckBox5` → **43.5**、`OptionButton5/6` → **43.5** となっており、
> 他の全箇所（53.5）と食い違っている。2019.04.18 に 43.5→53.5 へ差し替えた際の
> **修正漏れと判断**（フォーム上の赤字注記「2019.04.25 43.5mm削除 53.5mm追加」と整合）。
> → 移植では **53.5 に統一**し、差異として本書に明記する（後述「不明点」参照）。

### 6.2 型番マスタ（`型番` シート / `型番.xlsx`）

| A（サイズ名） | B（記号） | C（型番） | 備考 |
|---|---|---|---|
| 1.0×33×Coil | a | BJB7603900QR | |
| 1.0×40×Coil | b | BJB7604300QR | |
| 1.0×73×Coil | c | BJB7604000QR | |
| 1.0×53.5×Coil | d | BJB7606500QR | 2019.04.18 追加 |
| 0.6×82.5×Coil | e | BJB7604200QR | |
| 1.0×63.0×Coil | f | BJB7605900QR | 2018.05.18 追加 |
| 0.8×53.5×Coil | g | BJB7610400QR | 2026.03.26 追加 |
| (0.6×43.5×Coil) | d | BJB7604100QR | **2019.04.18 削除**（F2:H2 に退避） |

---

## 7. 処理フロー

### 7.1 起動

```
Auto_Open → 起動シート選択 → テスラ指定サイズ.Show vbModeless
```

### 7.2 サイズ選択（OB＝単丈モード）

```
OptionButtonN_Click
  └ SelectSize(シート名, dataRow, takeType)
       ├ CheckBox1〜6 と ck_08_53 を全解除
       ├ takeType=1 → Take1表示/Take2非表示、txtWeight1表示、txtWeight2クリア
       │  takeType=2 → Take2表示/Take1非表示、txtWeight2表示、txtWeight1クリア
       ├ lblKensaNo / lblSize1 / lblWeight1 / lblSize2 / lblWeight2 を初期化
       └ 対象シートから読込
            lblKensaNo = Cells(dataRow, 22)
            takeType=1 → lblWeight1 = Cells(dataRow,37) & "kg" , lblSize1 = Cells(dataRow,2)
            takeType=2 → lblWeight2 = Cells(dataRow,37) & "kg" , lblSize2 = Cells(dataRow,2)
  └ （OB5/6 のみ）ClearCoilInfo "1","2" / "3","4"
     （lblSpec_08_53_1/2 も同様）
        → CoilH / NW / GW / TA を空にする
```

`mSuppressEvent` により再入を抑止。

### 7.3 サイズ選択（CB＝丈1,2同時モード）

```
CheckBoxN_Click（Value=True のときのみ）
  └ SelectCheckBox(cbIdx, isNamed)
       ├ 他 CheckBox / ck_08_53 を全解除し、自分だけ ON
       ├ Take1・Take2 とも表示、txtWeight1・2 とも表示
       ├ OptionButton1〜12 と lblSpec_08_53_1/2 を全解除
       ├ ラベル5種を初期化
       ├ GetCBtoPair(cbIdx) → ob1, ob2 （isNamed なら 13,14）
       └ ob1 のシートから 検番/重量1/サイズ1、ob2 のシートから 重量2/サイズ2 を読込
```

### 7.4 重量反映（`CommandButton1`）

```
ken = StrConv(UCase(txtKensaNo), vbNarrow)
ValidateKensaNo(ken)                      ── NG なら中止
InpCheck(1) → "重量入力がありません　丈1"  ── 中止
InpCheck(2) → "重量入力がありません　丈2"  ── 中止
InpCheck(3) → "重量入力がありません　丈1　or　丈2" ── 中止
チェック入力()                             ── CBモード時のみ実処理
obIdx = DetectSelectedOB()
If obIdx > 0 Then ExecuteWrite(ken, obIdx) ── OBモード時のみ実処理
```

- `チェック入力` は CB 未選択なら即 `Exit Sub`、`ExecuteWrite` は OB 未選択なら呼ばれない。
  → **OB と CB は排他** なので、どちらか一方だけが動く。
- `ExecuteWrite`: `takeType=1` なら `txtWeight1`、`2` なら `txtWeight2` を重量として使用し
  `WriteAllToSheet(ws, ken, wt, kataban, takeType)` → 画面ラベルを対象シートから読み直す。
- `チェック入力`: 丈1 を `WriteAllToSheet(ws1, ken, txtWeight1, kataban1, 1)`、
  丈2 を `WriteAllToSheet(ws2, ken, txtWeight2, kataban2, 2)`。

### 7.5 重量計算_DB（`CommandButton16`）

```
IsAllCoilEmpty() が True なら何もしない
クリア()                     ── フォームの NW/GW/TA と 全14シートの本数列をクリア
RecreateSheet("風袋計算")     ── 既存削除 → 末尾に追加 → 非表示
IsValidSettings()
   ├ チップボール未選択 → "チップボールチェックがありません" / Kei=False / 中止
   ├ Take1・Take2 とも非表示 → 中止（無言）
   └ サイズ未選択 → "コイルサイズが選択されていません" / Kei=False / 中止
ShowProgressForm()
CoilH4 ≠ "" → DB_Materials(CoilH4, 4)   ← 4 から 1 の順（逆順）
CoilH3 ≠ "" → DB_Materials(CoilH3, 3)
CoilH2 ≠ "" → DB_Materials(CoilH2, 2)
CoilH1 ≠ "" → DB_Materials(CoilH1, 1)
ラベル転記CK()
ラベル転記OP()
HideProgressForm()
起動シートへ戻る
```

### 7.6 DB_Materials（1梱包分の計算）

```
GetRecordsArr(梱包資材マスタ.accdb, 資材重量) ── 空なら "資材重量なし" で中止
GetFieldsArr(...)                            ── 空なら "資材重量なし" で中止
GetFieldIndices()
CalculateMaterials()
SetCoilFormValues(coilNo, mat)   ── NW{n}, GW{n}
SetDisplayFormValues(...)        ── 表記用フォーム
PopulateListViewMaterials(...)   ── 表記用の資材一覧
WriteToSheet(coilNo, CoilText, mat) ── 風袋計算シート
FormatSheet()                    ── フォント/列幅/罫線/印刷設定
```

---

## 8. 判定ロジック・計算式（**移植の最重要部**）

### 8.1 検査番号バリデーション `ValidateKensaNo(ken)`

`ken = StrConv(UCase(txtKensaNo), vbNarrow)` 後に、**左から** 判定:

| 判定順 | 条件 | NG時メッセージ（`msg` = `"検査NOが7桁入力されていない" & vbLf & "もしくは"`） |
|---|---|---|
| 1 | `Not IsNumeric(Mid(ken,2,1))` | `msg & "6桁目にアルファベットが入力されています"` |
| 2 | `Not IsNumeric(Mid(ken,7,1))` | `msg & "1桁目にアルファベットが入力されています"` |
| 3 | `Not IsNumeric(Mid(ken,4,4))` | `msg & "1～4桁目にアルファベットが入力されています"` |
| 4 | `IsNumeric(Mid(ken,1,1))` | `msg & "7桁目に数字が入力されています"` |
| 5 | 上記すべて回避 | OK |

実質ルール: **1文字目=非数字（英字）、2文字目=数字、4〜7文字目=数字**。
**3文字目は検査されない。** 7文字未満は `Mid` が空文字を返し `IsNumeric("")=False` のため NG。
メッセージ文言は「右から数えた桁」を指しており、判定位置（左からの Mid）とは対応していないが、
**現行の文言・判定順をそのまま維持する。**

### 8.2 重量未入力チェック `InpCheck(a)`

| a | 対象 | 条件 |
|---|---|---|
| 1 | 丈1 | `OptionButton1,3,5,7,9,11` のいずれか ON かつ `txtWeight1 = ""` → True。<br>加えて `lblSpec_08_53_1` ON かつ `txtWeight1=""` → True |
| 2 | 丈2 | `OptionButton2,4,6,8,10,12` のいずれか ON かつ `txtWeight2 = ""` → True。<br>加えて `lblSpec_08_53_2` ON かつ `txtWeight2=""` → True |
| 3 | 丈1,2 | `CheckBox1〜6` のいずれか ON かつ (`txtWeight1=""` **または** `txtWeight2=""`) → True。<br>加えて `ck_08_53` ON かつ同条件 → True |

`For` ループは最初に ON を見つけた時点で `Exit For`。

### 8.3 コイル巾 `col` の決定

| 選択 | col |
|---|---|
| CheckBox1 / OptionButton7,8 | 40 |
| CheckBox2 / OptionButton9,10 | 33 |
| CheckBox3 / OptionButton1,2 | 73 |
| CheckBox4 / OptionButton3,4 | 82.5 |
| CheckBox5 / OptionButton5,6 | 53.5 |
| CheckBox6 / OptionButton11,12 | 63 |
| ck_08_53 / lblSpec_08_53_1,2 | 53.5 |

CB 側の `Select Case` を評価した後に OB 側の `Select Case` を評価するため、
**OB が選択されていれば OB の値で上書きされる**（排他運用なので実害なし）。

### 8.4 チップボール径 `SetDiameterInfo`

| 選択 | 外径 TempDiameter | 半径 HalfDiameter | 外周 GAI (m) |
|---|---|---|---|
| TIP1000 | 1100 | 550 | `1100 × π ÷ 1000` |
| TIP950 | 1000 | 500 | `1000 × π ÷ 1000` |
| TIP820 | 900 | 450 | `900 × π ÷ 1000` |

> ボタン名（1000/950/820）と実際に使う径（1100/1000/900）は **一致しない**。
> 「パレットサイズと同等とする」ため径を一段大きく取る、という旧版コメントがある。現行仕様として維持。

### 8.5 高さ計算（2系統あり — **重要**）

#### (a) `SetCoilDimensions`（資材計算本体。`mat.TA` は **Single**）

```
TIPT = Val((CoilText + 1) * 0.7 / 10^3)          ' Single
mat.TA = Val(CoilText * col / 10^3) + TIPT        ' Single   [m]
KTA    = Val(mat.TA * 1000 + 150 + 150) + (2.35 * 2)   ' Single  [mm]
TA{coilNo} = Format(WorksheetFunction.RoundUp(KTA, -1), "0.0")
```

- `+150+150` = 樹脂パレット高さ（上下）
- `+2.35*2` = ハードボード上下 2 枚（1枚 2.35mm）
- `RoundUp(x, -1)` = 10mm 単位で切り上げ

#### (b) `GetHBCoilDimensions`（ハードボード専用。`HB.TA` は **Double**）

```
TIPT  = Val(((CoilText + 1) * 0.7) / 10^3)
HB.TA = Val((CoilText * HB.col) + TIPT) / 10^3
```

> **これは (a) と数値が一致しなかった。― 2026.09 に VBA 側で統一された。**
> (b) は `/10^3` の位置が誤っていて、括弧の中で mm と m を足していた。
> チップボール分（11本で 8.4mm）が実質消えていた。
>
> 現在は共通関数 `GetCoilTA()` に集約され、**(a) と同一の値**になる。
> 移植側も `tare_calc.get_coil_ta()` に集約した。
> 詳細と影響範囲は `docs/03_不明点と要確認事項.md` A-1。

#### (b') 共通関数（2026.09〜）

```
GetCoilWidth()  … 巾（14 分岐を 1 箇所に）
GetCoilTA()     … 高さ    TA = 本数×巾/1000 + (本数+CHIP_EXTRA_SHEETS)×0.7/1000
GetCoilGAI()    … 外周
GetHBBandHeight(TA) … 胴巻き HB の高さ。TA - HB_BAND_CLEARANCE_M（現状 0）
```

#### (c) `羅列計算`（一覧表。`TA` は **Single**）

```
TIPT = Val((Q + 1) * 0.7 / 10^3)      ' Double
TA   = Val(Q * col / 10^3) + TIPT     ' Single
```
→ (a) と同じ式。

### 8.5b 計算式の欄の文字列化（``CStr``）

風袋計算シートの「計算式」列（C 列）と、積み高さクイック計算の MsgBox は
VBA の ``&`` 連結で作られる。``&`` は数値を ``CStr`` で文字列化する。

| 値 | VBA の出方 | 理由 |
|---|---|---|
| マスタの単重・係数 | `0.35` / `1` | Access から来た **Variant 文字列のまま** |
| `mat.TA` | `0.5969` | **Single** → 有効数字 7 桁 |
| `mat.GAI` | `3.45575191894877` | **Double** → 有効数字 15 桁 |
| `12 * 0.7` | `8.4` | 15 桁に丸めて末尾の 0 を落とす |

移植側で素の float を埋めると、同じ欄が

```
4*0.35*1.0
[((0.5968999862670898+0.5968999862670898/ 2)*1100*3.4557519189487724/ ...
ﾁｯﾌﾟ:8.399999999999999
```

のようになってしまう。`vba_compat.cstr()` / `cstr_single()` で再現し、
マスタ値は `mat.units_raw`（文字列のまま）を使う。

### 8.6 各資材の重量式

`単重(0)` = `単位質量`、`単重(1)` = `係数`（Access `資材重量` テーブル）。

| 資材 | 検索キー | 式 |
|---|---|---|
| ピン | `テスラピン` | `4 × 単位質量 × 係数` |
| チップボール | `チップボール1000ф`/`950ф`/`820ф` | `(積み本数 + 1) × 単位質量 × 係数` |
| ストレッチフィルム | `ストレッチフィルム` | `GAI × 5 × 単位質量 × 係数` |
| エサフォーム | `エサフォーム` | `TA > 0.82` → `GAI × 2 × 単重 × 係数`<br>それ以外 → `GAI × 単重 × 係数` |
| ポリシート | `ポリシート` | `AREA = 半径² × π`<br>`AREA2 = (TA + TA/2) × GAI`<br>`(AREA × 2 + AREA2) ÷ 10⁶ × 単重 × 係数` |
| PETバンド | `PETバンド` | `DOU = (GAI + 0.2) × 4`<br>`TATE = ((TA + 0.2) × 4) × 2 + ((外径 × 4) × 2) ÷ 10³`<br>`(DOU + TATE) × 単重 × 係数` |
| 樹脂パレット | `樹脂パレット` | `単位質量 × 係数 × 2` |
| EX-DRY | `EX-DRY` | `単位質量 × 係数` |
| ハードボード | 下記 8.7 | 下記 8.7 |

- エサフォーム閾値 0.82 m のコメント: 「エサフォームの高さが550、1.5倍ほどの高さになった時2周とする」
- PETバンド: 「締める時の余分を 200mm（=0.2m）とした」
- ポリシート: 「包み分は高さの半分とする」

### 8.7 ハードボード `DB_HB`

#### フラグ判定 `JudgeHBFlag(CoilText)`（**上から評価、最初に一致したもの**）

| 条件 | Flag |
|---|---|
| CheckBox1（40mm） かつ 14本 | True |
| CheckBox2（33mm） かつ 15本 | True |
| CheckBox3（73mm） かつ 8本 | True |
| CheckBox3（73mm） かつ 7本 | True |
| CheckBox4（82.5mm） かつ 7本 | True |
| CheckBox4（82.5mm） かつ 6本 | True |
| CheckBox5（53.5mm） かつ 11本 | True |
| CheckBox5（53.5mm） かつ 10本 | True |
| CheckBox6（63mm） かつ 9本 | True |
| CheckBox6（63mm） かつ 8本 | True |
| ck_08_53（53.5mm） かつ 11本 / 10本 | True |
| OptionButton5 または 6 かつ 11本 / 10本 | True |
| lblSpec_08_53_1 または 2 かつ 11本 / 10本 | True |
| 上記以外 | False |

#### 重量算出 `CalculateHBValue`

```
If Flag And col = 53.5 And 本数 = 11 →  2 × HB590*2000単重 × 係数  +  2 × 八角単重 × 係数
If Flag And col = 53.5 And 本数 = 10 →  2 × HB530*2000単重 × 係数  +  2 × 八角単重 × 係数
それ以外（Flag=True/False いずれも同じ）
                                     →  (GAI + 0.7) × HB.TA × ハードボード単重 × 係数
                                        + 2 × 八角単重 × 係数
```

> **実効的に Flag が結果を変えるのは `col = 53.5` かつ 10/11本 のときだけ。**
> 40mm×14本 などで Flag=True になっても、分岐先は Flag=False と同一式。
> これは「将来サイズ別固定HBを追加するための枠」と判断できるが、
> **現行の分岐構造をそのまま移植する。**

#### 羅列計算専用 `CalcHBForList`（Flag を使わない）

```
col = 53.5 And 本数 = 11 →  2 × HB590*2000 + 2 × 八角
col = 53.5 And 本数 = 10 →  2 × HB530*2000 + 2 × 八角
それ以外                 →  (GAI + 0.7) × TA × ハードボード単重 × 係数 + 2 × 八角
```
※ ここで使う `TA` は **8.5(c) の TA**（= 8.5(a) と同じ式）。`DB_HB` の TA とは異なる。

### 8.8 NW / 風袋 / GW

```
T1 = CDbl(Replace(lblWeight1, "kg", ""))     ' 丈1 の1条重量
T2 = CDbl(Replace(lblWeight2, "kg", ""))     ' 丈2 の1条重量

CalculateNW:
  coilNo 1,2 → Take1 表示中なら  Format(Val(本数 × T1), "0.0")
  coilNo 3,4 → Take2 表示中なら  Format(Val(本数 × T2), "0.0")
  （非表示なら 0）

HU（風袋） = PIN + TIP + SUT + ESA + POR + HB + PET + PAR + DRY
GW         = HU + NW
```

> `CalculateNW` は `Format(...)` の**文字列**を `Double` の戻り値へ代入している。
> そのため **NW は小数第1位に丸められてから** HU/GW に加算される。
> 表示は `NW{n} = Format(NW, "0.0")`、`GW{n} = Format(Round(GW, 0), "0.0")`
> （GW は整数へ四捨五入してから「0.0」書式）。

> **羅列計算の風袋は DRY（EX-DRY）を含まない。**
> `Cells(r,11) = Round(B+C+D+E+F+G+H+I, 0)`（ピン〜ペットバンドの8項目）であり、
> `DB_Materials` の HU（9項目）と定義が異なる。
>
> **2026.09 方針変更**: 一覧表は参考値であり `CalculateMaterials` を正とする、
> という判断により、移植版の一覧表は 9 項目（EX-DRY 込み）に統一した。
> 梱包高さ・HB も同様に `CalculateMaterials` と一致する。
> 詳細は `docs/03_不明点と要確認事項.md` の A-2 / A-3 を参照。

### 8.9 コイル番号の自動採番（`WriteAllToSheet`）

```
baseRows = 8,18,28,38,48,57,66,75, 97,106,115,124,133,142,151,160   (16枚)

For i = 0 To 15
  If i < 8 Then seqL = i + 1  : seqR = i + 9
  Else            seqL = i + 9  : seqR = i + 17

  coilL = "-" & Format(takeType,"00") & Format(seqL,"00")
  coilR = "-" & Format(takeType,"00") & Format(seqR,"00")
```

→ 左面 = 01〜08 / 17〜24、右面 = 09〜16 / 25〜32。丈1 なら `-01xx`、丈2 なら `-02xx`。
実シート（`1.0mm×53.5mm 丈1.xlsx`）で `G8=-0101`, `AM8=-0109`, `G97=-0117`, `AM160=-0132` を確認済み。**一致。**

### 8.10 ラベル1枚の書込座標

基準行 `r` に対して:

| 内容 | 行 | 左面列 | 右面列 | 値 |
|---|---|---|---|---|
| バーコード(型番) | `r-1` | 9 | 41 | `"*" & 型番 & "*"` |
| コイル番号 | `r` | 7 | 39 | `coilL` / `coilR`（表示形式 `@`） |
| 型番 | `r+1` | 4 | 36 | 型番 |
| 重量 | `r+2` | 4 | 36 | 重量（書式 `0.0_ `） |
| 単位 | `r+2` | 7 | 39 | `"kg"` |
| バーコード(検番) | `r+3` | 2 | 34 | `"*" & 検番 & コイル番号 & " " & 重量 & "*"` |

ヘッダー: `(3,22)=検番`, `(92,22)=検番`, `(3,37)=重量`, `(92,37)=重量`

### 8.11 本数/高さ/NW/GW の転記（`WriteCoilInfo`）

```
dataRow      : 1段目  → COL_COILH(43)=本数1, COL_TA(48)=高さ1, COL_NW(53)=NW1, COL_GW(59)=GW1
dataRow + 1  : 2段目  → 同上（本数2, 高さ2, NW2, GW2）
extraRow > 0 のとき extraRow / extraRow+1 へ同じ値を複写
書式: 本数 "0_ " / 高さ・NW・GW "0.0_ " / 重量列(37) "0.0_ "
```

- `ラベル転記OP`（単丈）: `takeType=1` → `CoilH1/CoilH2`・`TA1/TA2`・`NW1/NW2`・`GW1/GW2`
  `takeType=2` → `CoilH3/CoilH4`・`TA3/TA4`・`NW3/NW4`・`GW3/GW4`
- `ラベル転記CK`（同時）: `Take1` 表示中なら ob1 のシートへ `CoilH1/2` 系、
  `Take2` 表示中なら ob2 のシートへ `CoilH3/4` 系

---

## 9. 出力内容

### 9.1 風袋計算シート（`風袋計算`）

`coilNo` ごとの開始行: 1 / 15 / 29 / 43

| 行 | A列（項目） | B列（値・書式） | C列（計算式） |
|---|---|---|---|
| i | `丈1_1梱包目:<本数>本` 等 | | |
| i+1 | ﾋﾟﾝ | `0.000` | `4*単重*係数` |
| i+2 | ﾁｯﾌﾟﾎﾞｰﾙ | `0.00` | `(本数+1)*単重*係数` |
| i+3 | ｽﾄﾚｯﾁﾌｨﾙﾑ | `0.000` | `外径*GAI/10^3)*5*単重*係数[約5周計算]` |
| i+4 | ｴｻﾌｫｰﾑ | `0.000` | `…[2周計算]` / `…[1周計算]` |
| i+5 | ﾎﾟﾘｼｰﾄ | `0.000` | 円柱表面積式 |
| i+6 | ﾊｰﾄﾞﾎﾞｰﾄﾞ | `0.000`（表示形式は `0.00_ `） | `HB.HBFormula` |
| i+7 | 樹脂ﾊﾟﾚｯﾄ | `0.0` | `単重*2` |
| i+8 | ﾍﾟｯﾄﾊﾞﾝﾄﾞ | `0.000` | 胴巻き＋縦バンド式 |
| i+9 | EX-DRY | `0.000` | `単重*1` |
| i+10 | NW | `0.0` | `T*本数` |
| i+11 | 風袋 | `Round(HU,0)` → `0.0` | 9項目の連結 |
| i+12 | GW | `Round(GW,0)` → `0.0` | `HU+NW` |

> `WriteHBToSheet` は `If CoilH1 <> ""` の場合のみ書き込む（`coilNo` に関係なく `CoilH1` を見る）。
> `CoilH1` が空で `CoilH2` のみ入力されたケースでは **HB 行（i+6）の計算式がシートに出ない**
> （値は `WriteSheetValues` が無条件に書くので入る）。
>
> これは **Excel のセルへ書く処理**の話で、表示用の `SetHBDisplayForm` とは別物。
> 移植版はセルへ書く処理そのものが無いため、この条件は持たない（A-5）。
> `mat.HB` 自体は計算されて HU/GW には入るため、**帳票だけ HB 欄が空になる**。現行挙動として維持。

印刷設定（`風袋罫線設定`）:

| 表示状態 | 印刷範囲 | ヘッダー右 |
|---|---|---|
| Take1・Take2 表示 かつ CoilH1〜4 すべて入力 | `$A$1:$C$55` | `＊検査番号：<検番>＊ｺｲﾙｻｲｽﾞ：<サイズ1> <サイズ2>` |
| Take1 のみ表示 かつ CoilH1 または CoilH2 | `$A$1:$C$27` | `…ｺｲﾙｻｲｽﾞ：<サイズ1>` |
| Take2 のみ表示 かつ CoilH3 または CoilH4 | `$A$29:$C$55` | `…ｺｲﾙｻｲｽﾞ：<サイズ2>` |

共通: A4 / 横 / 余白0 / 1ページに収める / 右フッター `yyyy.mm.dd印刷`
罫線: CoilH1→`A2:C13`, CoilH2→`A16:C27`, CoilH3→`A30:C41`, CoilH4→`A44:C55`

### 9.2 羅列計算シート（`50まで`）

1〜50 本の一覧。列構成:

| 列 | 見出し |
|---|---|
| 1 | 本数 |
| 2 | ﾋﾟﾝ(kg) |
| 3 | ﾁｯﾌﾟﾎﾞｰﾙ(kg) |
| 4 | ｽﾄﾚｯﾁﾌｨﾙﾑ(kg) |
| 5 | ｴｻﾌｫｰﾑ(kg) |
| 6 | ﾎﾟﾘｼｰﾄ(kg) |
| 7 | ﾊｰﾄﾞﾎﾞｰﾄﾞ(kg) |
| 8 | 樹脂ﾊﾟﾚｯﾄ(kg) |
| 9 | ﾍﾟｯﾄﾊﾞﾝﾄﾞ(kg) |
| 10 | NW(kg) |
| 11 | 風袋(kg) |
| 12 | GW(kg) |
| 13 | ｺｲﾙ高さ(mm) |
| 14 | 梱包高さ(mm) |
| 15 | ﾁｯﾌﾟﾎﾞｰﾙ高さ(mm) |

- NW: Take1・Take2 とも表示 → `Q × T1`／Take1 のみ → `Q × T1`／Take2 のみ → `Q × T2`
  （**両方表示時も T1 を使う**）
- 風袋 = `Round(列2..列9 の合計, 0)`（**EX-DRY 非計上**）
- GW = `Round(列2..列10 の合計, 0)`
- コイル高さ = `TA × 1000`
- 梱包高さ = `RoundUp(TA × 1000 + 150 + 150, -1)`（**2.35×2 を加算しない**。`SetCoilDimensions` の KTA とは異なる）
- チップボール高さ = `TIPT × 1000`
- 書式: `D2:G53,I2:I53` → `#,##0.000_ ` / `H2:H53,J2:O53` → `0.0_ `
- 印刷: `A1:O51` / A4 / 縦 / 余白0 / 1ページ / 左フッターに検番・サイズ / 右フッター `yyyy.mm.dd印刷`

### 9.3 ラベル台紙シート

「8.9 / 8.10 / 8.11」のとおり。1シートあたり 32 枚（16行 × 左右2面）。

### 9.4 積み高さクイック計算（`CommandButton13`）

```
x    = InputBox("コイル積み数")
TIPT = Val((x + 1) * 0.7 / 10^3)
TA   = Val(1 * col / 10^3) + TIPT      ← 本数を掛けず 1 固定
KTA  = Val(TA * 1000 + 150 + 150)      ← 2.35*2 を加算しない
MsgBox "ﾁｯﾌﾟ:<TIPT*1000> / 1条分:<TA*1000> / ﾊﾟﾚｯﾄ+分(繰上値):<RoundUp(KTA,-1)> : そのまま<KTA>"
```

> `TA` に `1 × col` を使うため「1条分の高さ」を表示する仕様。
> ここだけ `CheckBox5`/`OptionButton5,6` の巾が **43.5**（他は 53.5）。

---

## 10. 印刷・ラベル・帳票

| ボタン | 処理 | 対象 |
|---|---|---|
| `CommandButton2` | OB選択時は当該1シート、CB選択時は `チェック印刷`（2シート）。確認ダイアログ後に印刷 → `クリア()` | ラベル台紙 |
| `CommandButton9` | `羅列計算` 実行 → `50まで` を表示 | 一覧 |
| `CommandButton11` | `50まで` を表示して印刷 | 一覧 |
| `CommandButton10` | 確認2回（印刷する？／ﾗﾍﾞﾙ用紙から普通紙に変えてください）→ `風袋罫線設定` → `風袋計算` 印刷 | 風袋帳票 |
| `CommandButton12` | `風袋計算` を表示して印刷 | 風袋帳票 |
| `CommandButton6` | `DB_Materials` を 4,3,2,1 の順で実行 → ラベル転記 → `風袋計算` を表示（ズーム70%） | — |
| `CommandButton3 / 4` | 14 シートの表示 / 非表示切替 | — |
| `CommandButton5` | `テスラ全サイズ` を開き、指定サイズ・UserForm4 を閉じる | — |
| `CommandButton7` | `表記用` を表示 | — |
| `CommandButton8` | アクティブシートの B〜M 列を全角→半角変換（`Ｘ x × → Ｘ`、全角数字・括弧→半角） | — |
| `CommandButton14` | `ListOen`（日報ブックを開く） | — |
| 全サイズ `CommandButton2` | `92,37` と `185,37` の重量有無で印刷範囲を切替えて 2 シート印刷 | ラベル台紙 |

> `CommandButton6` には既知の不具合がある: `DB_Materials(.CoilH4, 4)` の後に
> `DB_Materials(.CoilH3, 3)`, `(.CoilH2, 2)` と続き、最後が **`DB_Materials(.CoilH4, 1)`**
> （`CoilH1` ではなく `CoilH4`）。`CommandButton16` 側は正しく `CoilH1` を使う。
> `CommandButton16` が現行の主経路であり、`CommandButton6` は旧経路。

---

## 11. エラー処理

| 箇所 | 方式 | 内容 |
|---|---|---|
| `GetMyLineName` | `On Error GoTo ErrHandler` | 失敗時 `Unknown` を返し `Debug.Print` |
| `DebugLog` | `On Error Resume Next` | 失敗を握りつぶす |
| `InitializeDebugLog` | `On Error GoTo ErrorHandler` | 共有パス失敗時 `ThisWorkbook.path\DebugPrint` へフォールバック |
| `WriteLog` | `On Error GoTo WriteLogError` | 失敗時 `False` を返し `DebugLog` に記録 |
| `ReadCheck` | `On Error Resume Next` | 存在しなければ `False` |
| `WriteAllToSheet` | `On Error GoTo Cleanup` | `Application` 設定を必ず復帰。エラーは `DebugLog` のみ（**ユーザーへは無通知**） |
| `PrintSheet`(UF) | `On Error Resume Next` + `Is Nothing` | `"指定されたシート「…」は存在しません。"` |
| `DB_Materials` / `DB_HB` / `羅列計算` | 戻り値チェック | `"資材重量なし"`（vbCritical）で中止 |
| `GetUnitMassAndCoefficient` | 戻り値 | 不一致時 `Array("", "")` → **計算結果は 0**（エラーにならない） |
| `adoConnection` | `PathCheck` | ファイル無しなら `Nothing`（メッセージはコメントアウト） |
| `ListOen` | `ReadCheck` | `"日報ファイルがありません"`（vbCritical） |
| `IsBookOpened` | `On Error Resume Next` | 開けなければ「開いている」と判定 |
| `fldAccdb` ほか ADO | `On Error GoTo Catch` + `Resume finally` | |

> **重要な穴**: `GetUnitMassAndCoefficient` がマスタに無い資材名を引くと `Array("","")` を返し、
> `Val("") * Val("")` → `0` となって **静かに 0kg** で計算される。
> 移植先では同じ結果（0）を維持しつつ、**警告をログ／画面に残す** ことを推奨（第13章）。

---

## 12. 設定値・マスタ値・ハードコード業務ルール

### 12.1 定数（ハードコード）

| 値 | 意味 | 出現箇所 |
|---|---|---|
| `0.7` | チップボール1枚の厚み(mm) | `SetCoilDimensions`, `GetHBCoilDimensions`, `羅列計算`, `CommandButton13` |
| `+1` | チップボールは「積み本数 + 1 枚」 | 同上 |
| `150 + 150` | 樹脂パレット高さ（上下, mm） | `SetCoilDimensions`, `羅列計算`, `CommandButton13` |
| `2.35 * 2` | ハードボード上下2枚の厚み(mm) | `SetCoilDimensions` のみ |
| `4` | ピン本数（固定） | `CalculatePIN` |
| `5` | ストレッチフィルム巻数 | `CalculateSUT` |
| `0.82` | エサフォーム 1周/2周 の閾値(m) | `CalculateESA`, `羅列計算` |
| `2` | エサフォーム 2周時の係数 | 同上 |
| `TA + TA/2` | ポリシート「包み分は高さの半分」 | `CalculatePOR`, `羅列計算` |
| `0.2` | PETバンド 締め代(m) | `CalculatePET`, `羅列計算` |
| `4` / `×2` | PETバンド 胴巻き4本・縦バンド4本×2 | 同上 |
| `2` | 樹脂パレット 台数 | `CalculatePAR` |
| `1` | EX-DRY 個数 | `CalculateDRY` |
| `0.7` (m) | HB「外周 + 700mm」 | `CalculateHBValue`, `CalcHBForList` |
| `2` | 八角ハードボード 上下2枚 | 同上 |
| `1100 / 1000 / 900` | チップボール外径(mm) | `SetDiameterInfo` ほか |
| `550 / 500 / 450` | 同 半径(mm) | `CalculatePOR` ほか |
| `-1`（RoundUp桁） | 梱包高さは 10mm 単位切上 | `SetCoilDimensions`, `羅列計算`, `CommandButton13` |
| `50` | 羅列計算の上限本数 | `羅列計算` |
| `16` / `32` | ラベル枚数（16行 × 左右2面） | `GetLabelBaseRows`, `WriteAllToSheet` |
| `"メイリオ"` / `10.5` | 帳票フォント | `FormatListSheet`, `FormatSheet` |

### 12.2 マスタ値

- **サイズ構成マスタ**: 第6章の表（VBA 内ハードコード）
- **型番マスタ**: `型番` シート（A/B/C 列）
- **資材マスタ**: Access `梱包資材マスタ.accdb` / `資材重量` テーブル

---

## 13. 不明点・要確認事項（**移植では現行動作を維持し、ここに列挙**）

| # | 内容 | 現行の挙動 | 移植での扱い |
|---|---|---|---|
| 1 | `DB_HB` の `TA` が `SetCoilDimensions` の `TA` と異なる（チップボール分が 1/1000） | HB がわずかに小さく出る | **現行どおり**。設定 `hb_ta_legacy_divide` で切替可能にした（既定=現行） |
| 2 | `CommandButton13` の巾が `CheckBox5 / OB5,6` で **43.5**（他は 53.5） | クイック計算のみ 43.5 | **53.5 に統一**（2019.04.18 の差替漏れと判断）。設定で 43.5 に戻せる |
| 3 | `羅列計算` の風袋に EX-DRY が入っていない | 8項目合計 | **現行どおり**（`DB_Materials` は 9項目） |
| 4 | `羅列計算` の梱包高さに `2.35*2` が入っていない | `RoundUp(TA*1000+300, -1)` | **現行どおり** |
| 5 | `WriteHBToSheet` が `CoilH1` の有無だけを見る | `CoilH1` 空なら HB 行の**計算式**が出ない（値は出る） | **条件ごと削除（2026.09）**。`WriteHBToSheet` は Excel のセルへ書く処理で、移植版には該当が無い。計算式は他の資材と同じく常に出る。数値への影響なし（A-5） |
| 6 | `JudgeHBFlag` の 40/33/73/82.5/63mm の条件が結果に影響しない | Flag=True でも Flag=False と同式 | **現行どおり**（分岐構造を保持） |
| 7 | `CommandButton6` の最後が `DB_Materials(.CoilH4, 1)`（`CoilH1` ではない） | 旧経路のバグ | 旧経路は移植対象外。主経路 `CommandButton16` のみ実装 |
| 8 | `ValidateKensaNo` のメッセージ文言が判定位置と対応しない／3文字目が未検査 | そのまま | **文言・判定順ともそのまま維持** |
| 9 | `PATH_AIM_参照` / `PATH_梱包_日報DebugPrint` の実値 | 本ファイルに定義なし | `config/app.json` に外出し。**要提供** |
| 10 | `表記用` `UserForm4` `警告` `msg` `UF_Material` `UFProgress` のフォーム定義 | コード未提供 | 参照フィールド名から再構成。`UserForm4`・`警告` は**用途不明のため未実装** |
| 11 | ラベル台紙の `Cells(dataRow, 2)`（サイズ表示） | シート上の固定文字。実体は `1.0mm×53.5mm 　 丈1`（スペースがシート名と微妙に異なる） | `data/size_master.json` の `size_cell_label` で個別設定可 |
| 12 | 画面のボタンキャプションと `CommandButtonN` の対応 | `.frx` 未提供のため確証なし | スクリーンショットから推定してマッピング（`docs/03_画面対応表.md`） |
| 13 | `資材重量` テーブルの `係数` の意味（歩留り？単位換算？） | 単純に乗算 | そのまま乗算 |
| 14 | `TIP950` / `TIP820` 選択時に `羅列計算` の `TempDiam` が 1000/900 になるが `CalculatePOR` の半径は 500/450 | 整合している | 問題なし |
| 15 | `Format(x,"0.0")` の丸め方向 | VBA 実装依存（概ね四捨五入） | **half-up（0から遠い方）** で実装。`WorksheetFunction.Round` も half-up |

---

## 14. 移植方針（要約）

| VBA | Python Web |
|---|---|
| `テスラ指定サイズ` UserForm | `/` 画面（HTML） |
| `テスラ全サイズ` UserForm | `/all-size` 画面 |
| `表記用` UserForm | `/breakdown` 画面（計算内訳） |
| OptionButton / CheckBox | ラジオ / チェックの排他 UI（同じ排他ロジック） |
| `CommandButtonN` | POST API + ボタン |
| セル操作（14シート） | SQLite `sheet_state` / `label_cell` |
| `風袋計算` シート | SQLite `tare_result` → HTML 帳票 + 印刷ビュー |
| `50まで` シート | SQLite `list_result` → HTML 帳票 + 印刷ビュー |
| Access 参照 | `cscript.exe` + VBScript + ADODB（Windows）／CSV・SQLite（開発時） |
| 資材マスタ | `repositories/material_repo.py` |
| `DebugLog` | `logging` → `%LOCALAPPDATA%\<AppId>\logs` |
| `MsgBox` | 画面のメッセージ領域（同じ文言） |
| `PrintOut` | ブラウザ印刷用ビュー（`@media print`） |
