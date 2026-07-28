@echo off
rem このファイルはUTF-8で保存されている。日本語Windowsの既定の文字コード(932)のままだと
rem メッセージが文字化けするので、コンソールをUTF-8に切り替えてから先に進む。
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem ワンタッチ起動（Windows）
rem
rem   このファイルをダブルクリックするだけ。
rem
rem 初回は仮想環境の作成と依存関係のインストールまで自動でやる。2回目以降は
rem 増えたPDFだけ索引に足してから、ブラウザで検索画面を開く。
setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%MANUAL_ROOT%"=="" set "MANUAL_ROOT=manuals"
if "%MANUAL_DB%"==""   set "MANUAL_DB=index.db"
if "%PORT%"==""        set "PORT=8000"
if "%HOST%"==""        set "HOST=127.0.0.1"

rem ---------------------------------------------------------------- Python
where py >nul 2>&1
if %errorlevel%==0 (
  set "PYTHON=py -3"
) else (
  where python >nul 2>&1
  if !errorlevel!==0 (
    set "PYTHON=python"
  ) else (
    echo [エラー] Python が見つかりません。
    echo   https://www.python.org/downloads/ からインストールしてください。
    echo   インストール時に "Add python.exe to PATH" にチェックを入れてください。
    pause
    exit /b 1
  )
)

rem ---------------------------------------------------------------- 仮想環境
if not exist ".venv" (
  echo [1/3] 初回セットアップ: 仮想環境を作ります（1〜2分かかります）
  %PYTHON% -m venv .venv
  if errorlevel 1 goto :failed
)

set "VPY=.venv\Scripts\python.exe"

if not exist ".venv\.requirements-stamp" (
  echo [2/3] 依存パッケージをインストールします
  "%VPY%" -m pip install --quiet --upgrade pip
  "%VPY%" -m pip install --quiet -r requirements.txt
  if errorlevel 1 goto :failed
  echo ok> ".venv\.requirements-stamp"
)

rem ---------------------------------------------------------------- OCR
where tesseract >nul 2>&1
if not %errorlevel%==0 (
  echo [注意] OCR用の tesseract が見つかりません。スキャンしたPDFは検索できません。
  echo        https://github.com/UB-Mannheim/tesseract/wiki からインストールし、
  echo        言語データで Japanese を選んでください。
)

rem ---------------------------------------------------------------- PDF置き場
if not exist "%MANUAL_ROOT%" mkdir "%MANUAL_ROOT%"

dir /s /b "%MANUAL_ROOT%\*.pdf" >nul 2>&1
if errorlevel 1 (
  echo [注意] %MANUAL_ROOT% にPDFがありません。
  set /p "ANSWER=お試し用のサンプルを作りますか？ [y/N]: "
  if /i "!ANSWER!"=="y" (
    "%VPY%" scripts\make_sample_manuals.py "%MANUAL_ROOT%"
  ) else (
    echo 機種ごとのフォルダを作ってPDFを入れてから、もう一度実行してください。
    pause
    exit /b 0
  )
)

rem ---------------------------------------------------------------- 索引と起動
echo [3/3] 索引を更新します（増えたPDFだけ読みます）
"%VPY%" -m manualsearch index "%MANUAL_ROOT%"
if errorlevel 1 goto :failed

echo.
echo 検索画面: http://%HOST%:%PORT%/
start "" "http://%HOST%:%PORT%/"
"%VPY%" -m manualsearch serve "%MANUAL_ROOT%" --host "%HOST%" --port "%PORT%"
goto :eof

:failed
echo.
echo セットアップに失敗しました。上のメッセージを確認してください。
pause
exit /b 1
