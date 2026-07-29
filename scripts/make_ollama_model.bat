@echo off
rem Create an Ollama model with a fixed context length.
rem NOTE: keep this file UTF-8 + CRLF. cmd.exe misparses LF-only batch files.
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0.."

set "PYTHONUTF8=1"

rem Ollama の設定画面でコンテキスト長を変えられないとき用。
rem 元のモデルにコンテキスト長を焼き込んだ「別名のモデル」を作る。
rem こうして作ったモデルは、アプリ側の設定に左右されない。

echo ============================================
echo  Ollama モデル作成（コンテキスト長を固定）
echo ============================================
echo.
echo Ollama を探しています...

where ollama >nul 2>&1
if errorlevel 1 goto no_ollama

set "BASE=%~1"
if "%BASE%"=="" set /p "BASE=元にするモデル名 [qwen3:32b]: "
if "%BASE%"=="" set "BASE=qwen3:32b"

set "CTX=%~2"
if "%CTX%"=="" set /p "CTX=コンテキスト長 [16384]: "
if "%CTX%"=="" set "CTX=16384"

rem モデル名に使えない文字を置き換えて、新しい名前を作る
set "SUFFIX=%BASE::=-%"
set "SUFFIX=%SUFFIX:/=-%"
set "NEWNAME=%SUFFIX%-ctx%CTX%"

echo.
echo 元のモデル : %BASE%
echo 新しい名前 : %NEWNAME%
echo コンテキスト: %CTX%
echo.

rem 元のモデルが無ければ先に取得する
ollama show "%BASE%" >nul 2>&1
if errorlevel 1 (
  echo %BASE% がまだ無いので取得します。20GB前後あるので時間がかかります。
  ollama pull "%BASE%"
  if errorlevel 1 goto pull_failed
)

set "MODELFILE=%TEMP%\manualsearch_Modelfile"
> "%MODELFILE%" echo FROM %BASE%
>>"%MODELFILE%" echo PARAMETER num_ctx %CTX%

echo モデルを作成しています...
ollama create "%NEWNAME%" -f "%MODELFILE%"
if errorlevel 1 goto create_failed
del "%MODELFILE%" >nul 2>&1

echo.
echo ============================================
echo  作成しました
echo.
echo  マニュアル検索の「設定」タブを開き、
echo  モデル欄に次の名前を入れて保存してください:
echo.
echo      %NEWNAME%
echo.
echo  そのあと「接続テスト」を押して、
echo  「長い文章の読み取り」が OK になれば完了です。
echo ============================================
goto done

:no_ollama
echo [エラー] Ollama が入っていません。
echo.
echo   このスクリプトは Ollama の代わりではありません。
echo   Ollama を入れたうえで、そのモデルの設定を変えるための道具です。
echo.
echo   1. https://ollama.com/download から Ollama をインストール
echo   2. インストール後、これをもう一度実行
echo.
echo   なお、先に「マニュアル検索」の設定タブで接続テストを試してください。
echo   「長い文章の読み取り」が OK なら、このスクリプトは不要です。
goto done

:pull_failed
echo [エラー] モデルの取得に失敗しました。名前が正しいか確認してください。
echo   使える名前は https://ollama.com/library で探せます。
goto done

:create_failed
echo [エラー] モデルの作成に失敗しました。上のメッセージを確認してください。
goto done

:done
echo.
pause
exit /b
