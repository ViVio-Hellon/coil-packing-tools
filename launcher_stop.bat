@echo off
rem  --- keep this file in CP932 (Shift-JIS) with CRLF line endings ---
rem  Keep everything above the chcp line ASCII; see start.bat for why.
chcp 932 >nul 2>&1
rem ===================================================================
rem  コイル梱包ツール ランチャーからの終了口(業務ツール統合ランチャー連携)
rem
rem  ブラウザ版・デスクトップ版の、動いているほうを止めます。
rem  このアプリだけを止め、ほかの Python や exe は触りません。
rem  取り込みなどの最中は止めずに 1 で終わります。中断してよければ --force。
rem
rem  戻り値   0 = 止めた(もともと動いていない)   1 = 止めなかった・止められなかった
rem  人が使う停止は stop.bat です(こちらは止まらずに一時停止しません)。
rem ===================================================================
setlocal
pushd "%~dp0" || exit /b 1
python process_manager.py --any %*
set RC=%ERRORLEVEL%
popd
endlocal & exit /b %RC%
