-- コイル梱包資材計算ツール — 手元の作業用DB
--
-- 2種類のテーブルが入る。
--
--   取り込んだ写し … 梱包資材マスタ・仕掛台帳から読んだもの。
--                    取り込みのたびに全行入れ替わる(ここでは編集しない)
--   このツールが書くもの … チェックリストと発注履歴
--
-- 【Access → SQLite の型】
--   Yes/No        -> INTEGER (0/1)
--   オートナンバー -> INTEGER PRIMARY KEY
--   日付/時刻      -> TEXT ("YYYY-MM-DD HH:MM:SS"。文字列比較で並べられる)
--   通貨/倍精度    -> REAL
--   テキスト       -> TEXT
--
-- 取り込んだ写しの列は**VBA が読んでいた列だけ**を持つ。増やすときは
-- `import_specs.py` と対にして直す(片方だけ直すと取り込みが落ちる)。

-- ==================================================================
-- 梱包資材マスタの写し
-- ==================================================================

-- VBA: 梱包資材マスタ.accdb / テーブル「包装仕様」
-- `PalletType` `台数計算` `リプラ計算` がすべてここを引く
CREATE TABLE IF NOT EXISTS 包装仕様 (
    包装仕様NO          TEXT PRIMARY KEY,
    納入先名称          TEXT NOT NULL DEFAULT '',
    用途名              TEXT NOT NULL DEFAULT '',
    パレット            TEXT NOT NULL DEFAULT '',
    -- 指定パレットフラグ。数値ならそのＷへ強制、"外径指定" なら段階表
    サイズ              TEXT NOT NULL DEFAULT '',
    -- 特殊フラグ("ｱｲｴﾑｱｲｶﾊﾞｰ" 等)
    種類                TEXT NOT NULL DEFAULT '',
    リプラサイズ        TEXT NOT NULL DEFAULT '',
    コイル間スペーサー  TEXT NOT NULL DEFAULT '',
    最下部スペーサー    TEXT NOT NULL DEFAULT '',
    下本数              TEXT NOT NULL DEFAULT '',
    緩衝材              TEXT NOT NULL DEFAULT '',
    -- "〇" で協豊製作所向け(コイル間に間紙を入れる)
    コイル間は間紙入       TEXT NOT NULL DEFAULT '',
    梱包単位_重量       TEXT NOT NULL DEFAULT '',
    重量範囲            TEXT NOT NULL DEFAULT '',
    梱包単位_枚数       TEXT NOT NULL DEFAULT '',
    枚数範囲            TEXT NOT NULL DEFAULT '',
    梱包総高さ          TEXT NOT NULL DEFAULT '',
    高さ範囲            TEXT NOT NULL DEFAULT ''
);

-- VBA: 梱包資材マスタ.accdb / テーブル「パレット」
-- `PalletSize` が種類で絞って、外径が収まる最小のＷを選ぶ
--
-- 姉妹ツール(python-web-tools)の `PalletMaster`(幅/丈/巾適合min…)とは
-- **別のテーブル**。名前が似ているので取り違えないこと
CREATE TABLE IF NOT EXISTS パレット (
    id      INTEGER PRIMARY KEY,
    種類    TEXT NOT NULL DEFAULT '',
    巾下限  REAL NOT NULL DEFAULT 0,
    巾上限  REAL NOT NULL DEFAULT 0,
    丈下限  REAL NOT NULL DEFAULT 0,
    丈上限  REAL NOT NULL DEFAULT 0,
    新記号  TEXT NOT NULL DEFAULT '',
    W       REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_パレット_種類 ON パレット(種類);

-- VBA: 梱包資材マスタ.accdb / テーブル「リプラサイズ」
-- 在庫にあるリプラの定尺。ここに無い長さは倉庫でのカットが要る
CREATE TABLE IF NOT EXISTS リプラサイズ (
    id          INTEGER PRIMARY KEY,
    リプラ長さ  REAL NOT NULL DEFAULT 0,
    長さ        TEXT NOT NULL DEFAULT ''
);

-- VBA: 梱包資材マスタ.accdb / テーブル「班員名簿」
-- 依頼者(担当者)の名簿。**この表は読むだけ。** 書くのは日報ツール側で、
-- 同じ表を姉妹ツール(vba-daily-report-python-migration)も読んでいる。
--
-- 列は提出データで確認済みの 管理番号/苗字/班/名前/読み/担当ライン。
-- 「読み」は五十音の並べ替えに使い、「班」は画面の区切りに使う。
CREATE TABLE IF NOT EXISTS 班員名簿 (
    id          INTEGER PRIMARY KEY,
    管理番号    TEXT NOT NULL DEFAULT '',
    苗字        TEXT NOT NULL DEFAULT '',
    班          TEXT NOT NULL DEFAULT '',
    名前        TEXT NOT NULL DEFAULT '',
    読み        TEXT NOT NULL DEFAULT '',
    担当ライン  TEXT NOT NULL DEFAULT ''
);

-- ==================================================================
-- 仕掛台帳の写し
-- ==================================================================

-- VBA: SIKAHIKINOW.accdb / テーブル「仕掛」
-- ロット番号から受注番号の候補を引く
CREATE TABLE IF NOT EXISTS 仕掛引当 (
    id          INTEGER PRIMARY KEY,
    ロット番号  TEXT NOT NULL DEFAULT '',
    受注番号    TEXT NOT NULL DEFAULT '',
    出荷日      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_仕掛引当_ロット ON 仕掛引当(ロット番号);

-- VBA: SIKAODRNOW.accdb / テーブル「仕掛」
-- 受注番号から計算に要る値をすべて展開する
CREATE TABLE IF NOT EXISTS 仕掛受注 (
    受注番号        TEXT PRIMARY KEY,
    受注材質        TEXT NOT NULL DEFAULT '',
    受注調質        TEXT NOT NULL DEFAULT '',
    受注板厚        REAL NOT NULL DEFAULT 0,
    受注板幅        REAL NOT NULL DEFAULT 0,
    用途コード      TEXT NOT NULL DEFAULT '',
    用途名          TEXT NOT NULL DEFAULT '',
    包装仕様NO      TEXT NOT NULL DEFAULT '',
    納入先名称      TEXT NOT NULL DEFAULT '',
    取引先名称      TEXT NOT NULL DEFAULT '',
    製品単重        REAL NOT NULL DEFAULT 0,
    梱包単位_重量   REAL NOT NULL DEFAULT 0,
    梱包単位_枚数   REAL NOT NULL DEFAULT 0,
    コイル外径_MAX  REAL NOT NULL DEFAULT 0,
    コイル外径_目標 REAL NOT NULL DEFAULT 0,
    コイル外径_MIN  REAL NOT NULL DEFAULT 0,
    コイル内径_目標 REAL NOT NULL DEFAULT 0,
    工場用コメント  TEXT NOT NULL DEFAULT '',
    営業納期        TEXT NOT NULL DEFAULT '',
    材質_比重       REAL NOT NULL DEFAULT 0,
    梱包コード      TEXT NOT NULL DEFAULT ''
);

-- ==================================================================
-- このツールが書くもの
-- ==================================================================

-- VBA: ブックの「資材発注管理」シート(5行目から15行)
--
-- シートは端末ごとの写しに残っていた。ここでは行番号を持つ表にして、
-- 画面を閉じても消えないようにする(シートに残っていたのと同じ感覚)。
--
-- 列は帳票の列そのまま。**計算結果を出力用の文字列にしてから入れる**
-- (改行込み)。VBA が配列を Range へ貼っていたのと同じ形で、
-- あとから帳票を組み直すときに解釈が割れない
CREATE TABLE IF NOT EXISTS 発注チェックリスト (
    行番号        INTEGER NOT NULL,
    -- 同じ行に2行書く特殊品(1C1282 / 1C1297)のための枝番。0 が主
    枝番          INTEGER NOT NULL DEFAULT 0,
    サイズ確定    TEXT NOT NULL DEFAULT '',
    依頼日        TEXT NOT NULL DEFAULT '',
    LotNo         TEXT NOT NULL DEFAULT '',
    用途名        TEXT NOT NULL DEFAULT '',
    パレット種類  TEXT NOT NULL DEFAULT '',
    台数          TEXT NOT NULL DEFAULT '',
    営業納期      TEXT NOT NULL DEFAULT '',
    コイル間      TEXT NOT NULL DEFAULT '',
    リプラ長さ    TEXT NOT NULL DEFAULT '',
    リプラ本数    TEXT NOT NULL DEFAULT '',
    リプラサイズ  TEXT NOT NULL DEFAULT '',
    緩衝材        TEXT NOT NULL DEFAULT '',
    依頼者        TEXT NOT NULL DEFAULT '',
    入荷日        TEXT NOT NULL DEFAULT '',
    更新日時      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (行番号, 枝番)
);

-- VBA: ブックの隠しシート「発注履歴」
--
-- 発注票を起こすときに、同じ LotNo が2週間以内に出ていないか調べる。
-- **追記しかしない**ので、複数端末が同じファイルへ書いても行の更新が
-- ぶつからない(共有に置く場合の前提)
CREATE TABLE IF NOT EXISTS 発注履歴 (
    id        INTEGER PRIMARY KEY,
    処理日    TEXT NOT NULL DEFAULT '',
    担当者    TEXT NOT NULL DEFAULT '',
    品名      TEXT NOT NULL DEFAULT '',
    サイズ    TEXT NOT NULL DEFAULT '',
    長さ      TEXT NOT NULL DEFAULT '',
    LotNo     TEXT NOT NULL DEFAULT '',
    数量      TEXT NOT NULL DEFAULT '',
    単位      TEXT NOT NULL DEFAULT '本',
    登録日時  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_発注履歴_lot ON 発注履歴(LotNo);

-- 取り込みの記録。画面に「いつの写しか」を出すために使う。
-- マスタが変わっても自動では取り込み直さないので、古ければ促せるようにする
CREATE TABLE IF NOT EXISTS 取り込み履歴 (
    テーブル名  TEXT PRIMARY KEY,
    取り込み日時 TEXT NOT NULL DEFAULT '',
    元ファイル  TEXT NOT NULL DEFAULT '',
    件数        INTEGER NOT NULL DEFAULT 0
);
