@echo off
rem  --- keep this file in CP932 (Shift-JIS) with CRLF line endings ---
rem  Keep everything above the chcp line ASCII; see start.bat for why.
chcp 932 >nul 2>&1
rem ===================================================================
rem  コイル梱包ツール (Web版) 停止
rem
rem  このアプリだけを止めます(3つの機能とも終わります)。同じPCで動く
rem  他の Python アプリは影響を受けません(基盤仕様書 2.8)。
rem  資材計算の取り込みが走っているときは止めずに知らせます。
rem  中断してよければ  stop.bat --force
rem ===================================================================
setlocal

rem  共有フォルダに置かれていても動くよう pushd を使う(start.bat と同じ)
pushd "%~dp0" || (
    echo [エラー] このフォルダに移動できませんでした: %~dp0
    pause
    exit /b 1
)
title コイル梱包ツール - 停止

python --version >nul 2>&1
if errorlevel 1 (
    echo [エラー] Python が見つかりません。
    goto :failed
)

python process_manager.py %*
if errorlevel 1 (
    echo.
    echo 止められませんでした。上のメッセージを確認してください。
    echo 実行中の処理があるときは、中断してよければ次を実行してください:
    echo     stop.bat --force
    echo.
    goto :failed
)
popd
endlocal
exit /b 0

:failed
pause
popd
endlocal
exit /b 1
