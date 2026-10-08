@echo off
rem  --- keep this file in CP932 (Shift-JIS) with CRLF line endings ---
rem  Keep everything above the chcp line ASCII; see start.bat for why.
chcp 932 >nul 2>&1
rem ===================================================================
rem  コイル梱包ツール 起動完了の確認口(業務ツール統合ランチャー連携)
rem
rem  ブラウザ版・デスクトップ版のどちらで動いていても答えます。
rem  戻り値   0 = 使える(起動完了)   2 = 起動中   1 = 動いていない
rem  ブラウザ版は http://127.0.0.1:ポート/api/health でも確かめられます
rem  (app_id と ready=true)。デスクトップ版はポートを持たないので、こちらを使います。
rem ===================================================================
setlocal
pushd "%~dp0" || exit /b 1
python process_manager.py --any --status
set RC=%ERRORLEVEL%
popd
endlocal & exit /b %RC%
