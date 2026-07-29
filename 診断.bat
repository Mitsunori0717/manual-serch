@echo off
rem Diagnose the installation and write the result to a text file.
rem Almost nothing happens here: cmd is bad at redirection + non-ASCII text,
rem so the real work lives in scripts\diagnose.py.
rem NOTE: keep this file UTF-8 + CRLF. cmd.exe misparses LF-only batch files.
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  マニュアル検索 の診断
echo ============================================
echo.
echo 調べています。索引の作成も試すので数分かかることがあります...
echo.

set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto run

rem .venv がまだ無ければシステムのPythonで動かす
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%~dp0scripts\diagnose.py"
  goto finished
)
where python >nul 2>&1
if not errorlevel 1 (
  python "%~dp0scripts\diagnose.py"
  goto finished
)
echo [エラー] Python が見つかりません。
echo.
echo   https://www.python.org/downloads/ からインストールしてください。
echo   インストール画面の「Add python.exe to PATH」に必ずチェックを入れてください。
goto done

:run
"%PY%" "%~dp0scripts\diagnose.py"

:finished
if errorlevel 1 (
  echo.
  echo [エラー] 診断を実行できませんでした。
  echo   scripts\diagnose.py が無い場合は、ZIPを展開し直してください。
)
echo.
echo 診断結果.txt を、このフォルダに書き出しました。
echo 中身をそのまま見せてもらえれば、原因を調べられます。

:done
echo.
pause
exit /b
