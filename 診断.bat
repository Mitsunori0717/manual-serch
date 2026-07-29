@echo off
rem Collect diagnostics into a text file so the user can just send it.
rem NOTE: keep this file UTF-8 + CRLF. cmd.exe misparses LF-only batch files.
rem Keep everything above chcp pure ASCII; switching codepage mid-file with
rem multibyte text above it can confuse the parser.
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "OUT=%~dp0診断結果.txt"
set "VPY=%~dp0.venv\Scripts\python.exe"

echo ============================================
echo  マニュアル検索 の診断
echo ============================================
echo.
echo 調べています。少し待ってください...
echo.

> "%OUT%" echo ===== マニュアル検索 診断結果 =====
>>"%OUT%" echo 日時: %DATE% %TIME%
>>"%OUT%" echo 場所: %~dp0
>>"%OUT%" echo.

rem ---------------------------------------------------------------- OneDrive
>>"%OUT%" echo ----- 置き場所 -----
echo %~dp0 | find /i "OneDrive" >nul
if errorlevel 1 (
  >>"%OUT%" echo OneDrive の外: OK
) else (
  >>"%OUT%" echo [注意] OneDrive の中にあります。同期の競合や、ファイルが
  >>"%OUT%" echo        クラウドのみ状態になって読めないことがあります。
  >>"%OUT%" echo        C:\manual-serch などへ移すことを勧めます。
)
>>"%OUT%" echo.

rem ---------------------------------------------------------------- ファイル
>>"%OUT%" echo ----- 必要なファイル -----
if exist "%~dp0manualsearch\__init__.py" (
  >>"%OUT%" echo manualsearch フォルダ: OK
) else (
  >>"%OUT%" echo [エラー] manualsearch フォルダがありません。
  >>"%OUT%" echo          ZIPを展開し直してください。
)
if exist "%~dp0requirements.txt" (>>"%OUT%" echo requirements.txt: OK) else (>>"%OUT%" echo [エラー] requirements.txt がありません)
if exist "%~dp0manuals" (>>"%OUT%" echo manuals フォルダ: OK) else (>>"%OUT%" echo manuals フォルダ: まだありません)
>>"%OUT%" echo.
>>"%OUT%" echo ----- 直下の中身 -----
>>"%OUT%" dir /b "%~dp0"
>>"%OUT%" echo.

rem ---------------------------------------------------------------- PDF
>>"%OUT%" echo ----- PDFの数 -----
if exist "%~dp0manuals" (
  dir /s /b "%~dp0manuals\*.pdf" 2>nul | find /c /v "" > "%TEMP%\ms_pdfcount.txt"
  set /p PDFCOUNT=<"%TEMP%\ms_pdfcount.txt"
  >>"%OUT%" echo PDF: !PDFCOUNT! 件
  del "%TEMP%\ms_pdfcount.txt" >nul 2>&1
) else (
  >>"%OUT%" echo manuals フォルダがないので数えられません
)
>>"%OUT%" echo.

rem ---------------------------------------------------------------- Python
>>"%OUT%" echo ----- Python -----
where py >nul 2>&1
if errorlevel 1 (>>"%OUT%" echo py ランチャー: 見つかりません) else (
  >>"%OUT%" echo py ランチャー: あり
  >>"%OUT%" py -3 -V 2>&1
)
where python >nul 2>&1
if errorlevel 1 (>>"%OUT%" echo python: 見つかりません) else (
  >>"%OUT%" echo python: あり
  >>"%OUT%" python -V 2>&1
  >>"%OUT%" where python
)
>>"%OUT%" echo.

rem ---------------------------------------------------------------- 仮想環境
>>"%OUT%" echo ----- 仮想環境 -----
if not exist "%VPY%" goto no_venv
>>"%OUT%" echo .venv: あり
"%VPY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
  >>"%OUT%" echo [エラー] .venv の中のPythonが動きません。
  >>"%OUT%" echo          フォルダを移動した場合に起きます。
  >>"%OUT%" echo          .venv フォルダを削除して start.bat を実行し直してください。
  goto after_venv
)
>>"%OUT%" echo .venv のPython: 動作OK
>>"%OUT%" "%VPY%" -V 2>&1
>>"%OUT%" echo.
>>"%OUT%" echo ----- 必要なパッケージ -----
>>"%OUT%" "%VPY%" -c "import fitz, fastapi, uvicorn, jinja2; print('主要パッケージ: OK')" 2>&1
>>"%OUT%" echo.
>>"%OUT%" echo ----- 動作環境の確認 -----
>>"%OUT%" "%VPY%" -m manualsearch doctor 2>&1
>>"%OUT%" echo.
>>"%OUT%" echo ----- 索引の状況 -----
>>"%OUT%" "%VPY%" -m manualsearch stats 2>&1
>>"%OUT%" echo.
>>"%OUT%" echo ----- 索引作成を試す -----
>>"%OUT%" "%VPY%" -m manualsearch index manuals 2>&1
goto after_venv

:no_venv
>>"%OUT%" echo .venv: ありません（まだ start.bat が最後まで通っていません）

:after_venv
>>"%OUT%" echo.
>>"%OUT%" echo ===== ここまで =====

echo ============================================
echo  診断結果.txt を作りました
echo.
echo  この画面のあと、診断結果.txt が開きます。
echo  その中身をそのまま見せてもらえれば、原因を調べられます。
echo ============================================
echo.
start "" notepad "%OUT%"
pause
exit /b
