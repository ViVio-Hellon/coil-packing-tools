-- ------------------------------------------------------------------
-- コイル梱包明細打ち出しシステム — 手元DBのスキーマ
--
-- 大きく2種類ある。
--
--   取り込み表  … 共有フォルダの sqlite3 から**総入れ替え**で写したもの。
--                 名前は `仕掛*`。ここへ書いても上流へは戻らない
--   作業表      … このアプリが自分で書くもの。VBA版が隠しシート
--                 「副番履歴」に持っていた内容がここへ来る
--
-- 取り込み表には代理キー `管理番号` を立てる。取り込み元はどれも
-- キーを持たないスナップショットで、VBA は「WHERE で絞った先頭行」を
-- 採るだけだった。取り込み順(=元の物理順)の先頭を引けるようにする。
-- ------------------------------------------------------------------

PRAGMA foreign_keys = ON;

-- ==================================================================
-- 取り込み表
-- ==================================================================

-- ------------------------------------------------------------------
-- 仕掛ロット (SIKALOT.sqlite3 の「仕掛」)
--
-- VBA `mDB.GetLotInfo` が引く表。ロット番号は**一意ではない**
-- (実データ13,624行に対し6,677ロット)。VBA は
--     WHERE ﾛｯﾄ番号=? AND ｵｰﾀﾞｰ板丈='0'
-- の先頭1行を採る。オーダー板丈0のロット3,113件のうち2,987件は
-- 複数行あるが、**下に並ぶ10列の内容はどの行でも同一**だった
-- (実データで全件確認)。どの行を採っても結果は変わらない。
--
-- 取り込み元の `ｵｰﾀﾞｰ板丈` は TEXT で、実値は '0' ではなく '0.0'。
-- `to_real` で REAL に正規化して入れるので、ここでは素直に
--     WHERE オーダー板丈 = 0
-- と数値で書ける(元を直接引くと '0' は1件も一致しない)。
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS 仕掛ロット (
    管理番号          INTEGER PRIMARY KEY AUTOINCREMENT,
    ロット番号        TEXT    NOT NULL,
    用途コード        TEXT    NOT NULL DEFAULT '',
    用途名            TEXT    NOT NULL DEFAULT '',
    製造材質          TEXT    NOT NULL DEFAULT '',
    製造調質          TEXT    NOT NULL DEFAULT '',
    製造板厚          REAL    NOT NULL DEFAULT 0,
    製造板幅          REAL    NOT NULL DEFAULT 0,
    オーダー板厚      REAL    NOT NULL DEFAULT 0,
    オーダー板幅      REAL    NOT NULL DEFAULT 0,
    -- 抽出条件そのもの。VBA の `ｵｰﾀﾞｰ板丈='0'`
    オーダー板丈      REAL    NOT NULL DEFAULT 0,
    設計_設備コース    TEXT    NOT NULL DEFAULT '',
    実績_設備コース    TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_lot_no ON 仕掛ロット(ロット番号);

-- ------------------------------------------------------------------
-- 仕掛引当 (SIKAHIKI.sqlite3 の「仕掛」)
--
-- VBA `mDB.GetJuchuBangouList` が引く表。ロット番号で複数行あり、
-- **初出順**に重複を除いた受注番号の並びを返す。
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS 仕掛引当 (
    管理番号     INTEGER PRIMARY KEY AUTOINCREMENT,
    ロット番号   TEXT NOT NULL,
    受注番号     TEXT NOT NULL DEFAULT '',
    -- 番号であって数値ではない。数値にすると指数表記になり読めなくなる
    引当番号     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_hiki_lot ON 仕掛引当(ロット番号);

-- ------------------------------------------------------------------
-- 仕掛受注 (SIKAODR.sqlite3 の「仕掛」)
--
-- VBA `mDB.GetOdrInfo` が引く表。受注番号 → 包装仕様NO。
-- 実データでは引当側の受注番号のうち81%しかここに無く、残りは
-- 見つからない。VBA は見つからなければ包装仕様NOを空のまま返し、
-- 画面も空欄にしてリンクを張らない。同じ扱いにする。
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS 仕掛受注 (
    管理番号     INTEGER PRIMARY KEY AUTOINCREMENT,
    受注番号     TEXT NOT NULL,
    包装仕様NO   TEXT NOT NULL DEFAULT '',
    取引先名称   TEXT NOT NULL DEFAULT '',
    納入先名称   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_odr_no ON 仕掛受注(受注番号);

-- ------------------------------------------------------------------
-- 仕掛当工程 (LS4LOT.sqlite3 の「仕掛」)
--
-- VBA `mDB.GetWariSuFromShikakari` が引く表。**前工程実績数と丈数は
-- ここにしか無い。**
--
--     前工程実績数 = 当工程設計_縦割数 × 当工程設計_横割数
--     丈数(初期値) = 当工程設計_縦割数
--
-- 仕掛ロット側にも `BOX設計_縦割数/横割数` があるが、実データで
-- 突き合わせると532件中340件(64%)で食い違う。**代用してはいけない。**
--
-- この表はLS4ラインの現在仕掛だけで589ロット。載っていないロットは
-- 縦割・横割とも0になり、前工程実績数も0になる。VBA はその場合
-- 「前工程実績数が0以下です」で止め、利用者が手で入れ直す
-- (ギミックA = 修正する/確定する)。
--
-- 取り込み元は**型宣言が無く**、値は text の "1".."6"。`to_int` を
-- 通して INTEGER に入れる(元を直接引くと `= 2` が1件も一致しない)。
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS 仕掛当工程 (
    管理番号           INTEGER PRIMARY KEY AUTOINCREMENT,
    ロット番号         TEXT    NOT NULL,
    当工程設計_設備名   TEXT    NOT NULL DEFAULT '',
    当工程設計_縦割数   INTEGER NOT NULL DEFAULT 0,
    当工程設計_横割数   INTEGER NOT NULL DEFAULT 0,
    当工程設計_枚本数   INTEGER NOT NULL DEFAULT 0,
    製造板厚           REAL    NOT NULL DEFAULT 0,
    製造板幅           REAL    NOT NULL DEFAULT 0,
    製造板丈           REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tokotei_lot ON 仕掛当工程(ロット番号);

-- ------------------------------------------------------------------
-- 取り込み記録
--
-- 元ファイルが前回の取り込み以降に更新されているかを見る。
-- 仕掛台帳は日々更新されるので結果的にほぼ毎回読む。
-- ------------------------------------------------------------------
-- 鍵は**手元のテーブル名**。置き場所(パス)ではない。
--
-- パスを鍵にすると、共有に届かない端末で「取り込んであるのに未取込と
-- 出る」。取り込んだ実績と、いま元が届くかどうかは別の話なので、
-- 実績はテーブル名で引き、到達性は `find_sources()` が別に見る。
CREATE TABLE IF NOT EXISTS 取り込み記録 (
    テーブル     TEXT PRIMARY KEY,
    ファイル     TEXT NOT NULL DEFAULT '',
    更新時刻     REAL NOT NULL DEFAULT 0,
    取り込み日時  TEXT NOT NULL DEFAULT '',
    -- 取り込み元の `_更新情報` にある作成日時。画面に
    -- 「いつ時点の台帳か」を出すために持つ(LS4LOT には無いので空)
    元作成日時   TEXT NOT NULL DEFAULT '',
    件数        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_torikomi_file ON 取り込み記録(ファイル);

-- ==================================================================
-- 作業表 (VBA の隠しシート「副番履歴」に相当)
-- ==================================================================

-- ------------------------------------------------------------------
-- 副番履歴 (VBA `mSheet.RegisterFuban` / `IsDuplicate` / `ClearFubanList`)
--
-- 出力した副番を1行ずつ積む。次の出力で同じ副番が出てきたら警告する
-- ための表。副番キーは `LotNo-丈-条`(例 `N3250Q0-2-15`)。
--
-- **一意制約は付けない。** VBA は重複を見つけても「このまま出力
-- しますか？」と尋ねて続行でき、続行した場合は登録をスキップする
-- (`If Not IsDuplicate Then RegisterFuban`)。制約で弾く形にすると、
-- 続行を選んだときの挙動が変わる。
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS 副番履歴 (
    管理番号   INTEGER PRIMARY KEY AUTOINCREMENT,
    副番キー   TEXT NOT NULL,
    登録日時   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_fuban_key ON 副番履歴(副番キー);

-- ------------------------------------------------------------------
-- 明細スナップショット (VBA `mSheet.SaveSnapshot` / `LoadSnapshot`)
--
-- 作業の途中経過。ロット番号を打ち直したときに前回の続きから
-- 再開できるようにする。VBA はシートのG〜K列に**直近5ロット**だけ
-- 持っていた(列数という物理的な制約)。同じ5件に揃える。
--
-- 重量・条数・使用済・廃棄はVBAと同じく**カンマ区切りの文字列**で
-- 持つ。表を分ければ正規化できるが、VBA版と1対1で見比べられる形を
-- 優先した(移植の正しさを確かめるのがいまの主目的)。
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS 明細スナップショット (
    ロット番号      TEXT PRIMARY KEY,
    前工程実績数    INTEGER NOT NULL DEFAULT 0,
    丈数           INTEGER NOT NULL DEFAULT 0,
    重量           TEXT    NOT NULL DEFAULT '',
    条数           TEXT    NOT NULL DEFAULT '',
    使用済         TEXT    NOT NULL DEFAULT '',
    廃棄           TEXT    NOT NULL DEFAULT '',
    更新日時       TEXT    NOT NULL DEFAULT ''
);

-- ------------------------------------------------------------------
-- 明細出力状態 (VBA 副番履歴シートの D1/E1/F1)
--
-- プロセスに1つしかない状態。**1行だけ**持つ(`ID` は常に1)。
--   現在ロット番号 … D1。ロット切替の判定に使う
--   現在連番       … E1。出力のたびに +1
--   印刷済         … F1。出力で0、印刷で1
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS 明細出力状態 (
    ID             INTEGER PRIMARY KEY CHECK (ID = 1),
    現在ロット番号  TEXT    NOT NULL DEFAULT '',
    現在連番        INTEGER NOT NULL DEFAULT 0,
    印刷済          INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO 明細出力状態 (ID, 現在ロット番号, 現在連番, 印刷済)
VALUES (1, '', 0, 1);

-- ------------------------------------------------------------------
-- 明細出力 (VBA の `Lot-NoN` シート1枚に相当)
--
-- 出力1件＝1行。VBA はシートを生成して現物を残していたが、
-- Web版は行として残し、帳票はそこから組み立てる。
--
-- `副番の並び` は積み上げ順そのもの(カンマ区切り)。スロット1が先頭で、
-- 紙の上から順に並ぶ。`重量` は丈ごとの値(カンマ区切り)で、行ごとの
-- 重量は副番キーの丈番号で引き当てる ── VBA `WriteData` と同じ。
--
-- `手入力サイズ` / `手入力ロット番号` は**印刷の前に紙面で書き足したもの**。
-- VBA は紙がExcelシートだったので、刷る前にセルへ打ち込めた。その代わり。
-- 空なら何も印字しない(VBA の既定の紙面と同じ)。この行と一緒に消える
-- ので、ロットを切り替えれば消える。
--
-- **列を足したら `db._ADDED_COLUMNS` にも書く。** `CREATE TABLE IF NOT
-- EXISTS` は既にある表に何もしないので、現場の古いDBには列が増えない。
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS 明細出力 (
    管理番号        INTEGER PRIMARY KEY AUTOINCREMENT,
    ロット番号      TEXT    NOT NULL,
    No             INTEGER NOT NULL,
    副番の並び      TEXT    NOT NULL DEFAULT '',
    重量           TEXT    NOT NULL DEFAULT '',
    出力日時        TEXT    NOT NULL DEFAULT '',
    手入力サイズ     TEXT    NOT NULL DEFAULT '',
    手入力ロット番号  TEXT    NOT NULL DEFAULT '',
    -- 明細の履歴(`明細履歴.送信ID`)。書き足し・紙面を開いた・作り直しを
    -- 履歴へ映すための目印。この表はロットを切り替えると消えるが、
    -- 履歴は消えない
    履歴ID          TEXT    NOT NULL DEFAULT ''
);
-- 同じロットの同じNoは1枚だけ。VBA はシート名が重複しないことで
-- これを守っていた(同名なら上書き確認になる)
CREATE UNIQUE INDEX IF NOT EXISTS ux_shutsuryoku ON 明細出力(ロット番号, No);

-- ------------------------------------------------------------------
-- 明細の履歴 (`slip_history`)
--
-- **この表は消さない。** `明細出力` はロットを切り替えると消える(VBA の
-- DeleteLotSheets と同じ)が、あとから「このコイルはどの紙に載ったか」
-- 「何枚出したか」を追えるよう、出力と同じまとまりでここにも書く。
-- 書いたら共有の `梱包明細履歴.sqlite3` へ送る(全ライン・3年)。
-- 送れるまでここに残る(ネットワークが切れても失わない)。
--
-- 列は共有の表と同じ。最後の3列(要送信・変更回数・送信日時)だけ手元用
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS 明細履歴 (
    送信ID            TEXT    PRIMARY KEY,   -- 1枚ごとの一意の番号(送り直しても二重にならない)
    ロット番号         TEXT    NOT NULL,
    No                INTEGER NOT NULL,
    出力日時           TEXT    NOT NULL,
    本数              INTEGER NOT NULL DEFAULT 0,
    重量合計           REAL    NOT NULL DEFAULT 0,
    用途名            TEXT    NOT NULL DEFAULT '',
    製造材質           TEXT    NOT NULL DEFAULT '',
    製造調質           TEXT    NOT NULL DEFAULT '',
    製造板厚           REAL    NOT NULL DEFAULT 0,
    製造板幅           REAL    NOT NULL DEFAULT 0,
    設計_設備コース     TEXT    NOT NULL DEFAULT '',
    実績_設備コース     TEXT    NOT NULL DEFAULT '',
    受注番号           TEXT    NOT NULL DEFAULT '',   -- 複数あればカンマ区切り
    包装仕様NO         TEXT    NOT NULL DEFAULT '',
    手入力サイズ        TEXT    NOT NULL DEFAULT '',
    手入力ロット番号     TEXT    NOT NULL DEFAULT '',
    右上の文字          TEXT    NOT NULL DEFAULT '',
    状態              TEXT    NOT NULL DEFAULT '有効',  -- 有効 / 作り直し(No指定で上書きされた)
    作り直し日時        TEXT    NOT NULL DEFAULT '',
    紙面を開いた回数     INTEGER NOT NULL DEFAULT 0,
    最後に紙面を開いた日時 TEXT   NOT NULL DEFAULT '',
    出力PC            TEXT    NOT NULL DEFAULT '',
    ログインID         TEXT    NOT NULL DEFAULT '',
    版                TEXT    NOT NULL DEFAULT '',
    更新日時           TEXT    NOT NULL DEFAULT '',
    要送信            INTEGER NOT NULL DEFAULT 1,
    変更回数           INTEGER NOT NULL DEFAULT 0,
    送信日時           TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rireki_pending ON 明細履歴(要送信);
CREATE INDEX IF NOT EXISTS idx_rireki_date ON 明細履歴(出力日時);

-- 1行 = コイル1本。**「このコイルはどの紙か」はここから引く**
CREATE TABLE IF NOT EXISTS 明細履歴_副番 (
    送信ID     TEXT    NOT NULL,
    順         INTEGER NOT NULL,     -- 積んだ順(紙の上から1, 2, …)
    ロット番号  TEXT    NOT NULL,
    副番       TEXT    NOT NULL,     -- 1-10 の形
    丈         INTEGER NOT NULL DEFAULT 0,
    条         INTEGER NOT NULL DEFAULT 0,
    重量       REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (送信ID, 順)
);
CREATE INDEX IF NOT EXISTS idx_rireki_fuban ON 明細履歴_副番(ロット番号, 副番);
