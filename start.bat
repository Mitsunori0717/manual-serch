@echo off
rem One-touch launcher for Windows. Just double-click this file.
rem NOTE: keep this file UTF-8 + CRLF. cmd.exe misparses LF-only batch files.
rem Switch the console to UTF-8 before printing any Japanese.
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 初回は仮想環境の作成と依存関係のインストールまで自動でやる。2回目以降は
rem 増えたPDFだけ索引に足してから、ブラウザで検索画面を開く。

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if "%MANUAL_ROOT%"=="" set "MANUAL_ROOT=manuals"
if "%MANUAL_DB%"==""   set "MANUAL_DB=index.db"
if "%PORT%"==""        set "PORT=8000"
if "%HOST%"==""        set "HOST=127.0.0.1"

echo ============================================
echo  マニュアル検索
echo ============================================
echo.

rem ---------------------------------------------------------------- 書き込み権限
rem 書き込めないと仮想環境も索引も作れない。管理者として実行すれば通ってしまい
rem 原因に気づきにくいので、最初に確かめる。
type nul > "%~dp0.write-test" 2>nul
if not exist "%~dp0.write-test" goto no_write
del "%~dp0.write-test" >nul 2>&1

rem ---------------------------------------------------------------- Python
call :find_python
if not defined PYTHON goto no_python
echo 使用するPython: %PYTHON%

rem ---------------------------------------------------------------- 仮想環境
set "VPY=%~dp0.venv\Scripts\python.exe"
if exist "%VPY%" goto have_venv

echo [1/3] 初回セットアップ: 仮想環境を作ります（1〜2分かかります）
%PYTHON% -m venv .venv
if not exist "%VPY%" goto venv_failed

:have_venv
rem フォルダごと移動したりOneDriveで同期したりすると、.venv の中に焼き込まれた
rem 絶対パスがずれて動かなくなる。実際に起動できるか試し、駄目なら作り直す。
"%VPY%" -c "import sys" >nul 2>&1
if not errorlevel 1 goto venv_ok

echo 仮想環境が壊れているので作り直します（フォルダを移動しましたか？）
rmdir /s /q ".venv" >nul 2>&1
%PYTHON% -m venv .venv
if not exist "%VPY%" goto venv_failed

:venv_ok
if exist ".venv\.requirements-stamp" goto have_deps

echo [2/3] 依存パッケージをインストールします（数分かかることがあります）
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto pip_failed
type nul > ".venv\.requirements-stamp"

:have_deps
rem ---------------------------------------------------------------- OCR
where tesseract >nul 2>&1
if not errorlevel 1 goto have_ocr
echo.
echo [注意] OCR用の tesseract が見つかりません。スキャンしたPDFは検索できません。
echo        https://github.com/UB-Mannheim/tesseract/wiki からインストールし、
echo        言語データで Japanese を選んでください。
echo.

:have_ocr
rem ---------------------------------------------------------------- PDF置き場
if not exist "%MANUAL_ROOT%" mkdir "%MANUAL_ROOT%"

dir /s /b "%MANUAL_ROOT%\*.pdf" >nul 2>&1
if not errorlevel 1 goto do_index

echo [注意] %MANUAL_ROOT% フォルダにPDFがありません。
set "ANSWER=n"
set /p "ANSWER=お試し用のサンプルを作りますか？ [y/N]: "
if /i not "!ANSWER!"=="y" goto need_pdf
"%VPY%" scripts\make_sample_manuals.py "%MANUAL_ROOT%"

:do_index
rem ---------------------------------------------------------------- 索引と起動
echo.
echo [3/3] 索引を更新します（増えたPDFだけ読みます）
"%VPY%" -m manualsearch index "%MANUAL_ROOT%"
if errorlevel 1 goto index_failed

echo.
echo ============================================
echo  検索画面: http://%HOST%:%PORT%/
echo  終了するには、この画面で Ctrl+C を押すか
echo  ウィンドウを閉じてください。
echo ============================================
start "" "http://%HOST%:%PORT%/"
"%VPY%" -m manualsearch serve "%MANUAL_ROOT%" --host "%HOST%" --port "%PORT%"
goto done

rem ---------------------------------------------------------------- 異常終了
:no_python
echo [エラー] Python 3.10以上が見つかりません。
echo.
echo   1. https://www.python.org/downloads/ からインストールしてください。
echo   2. インストール画面の下にある
echo      「Add python.exe to PATH」に必ずチェックを入れてください。
echo   3. インストール後、このファイルをもう一度ダブルクリックしてください。
goto done

:no_write
echo [エラー] このフォルダに書き込めません。
echo.
echo   仮想環境も索引も作れないので、このままでは動きません。
echo   次のどちらかで直ります。
echo.
echo   A) フォルダの権限を直す（おすすめ・一度だけ）
echo      このフォルダを右クリック - プロパティ - セキュリティ - 編集 で、
echo      お使いのユーザーに「変更」を許可してください。
echo.
echo   B) 書き込める場所へ移す
echo      C:\manual-serch や、ドキュメントの下などへフォルダごと移動してください。
echo.
echo   「管理者として実行」でも動きますが、毎回必要になるうえ、
echo   作られたファイルが管理者のものになるため勧めません。
goto done

:venv_failed
echo [エラー] 仮想環境を作れませんでした。
echo   .venv フォルダを削除してから、もう一度実行してみてください。
goto done

:pip_failed
echo [エラー] 依存パッケージのインストールに失敗しました。
echo   社内プロキシがある場合は、管理者に pip の設定を確認してください。
goto done

:index_failed
echo [エラー] 索引の作成に失敗しました。上のメッセージを確認してください。
echo.
echo   原因が分からないときは、このフォルダにある 診断.bat を実行してください。
echo   診断結果.txt が作られるので、その中身を見せてもらえれば調べられます。
goto done

:need_pdf
echo.
echo %MANUAL_ROOT% フォルダの中に、機種ごとのフォルダを作ってPDFを入れてから、
echo もう一度このファイルをダブルクリックしてください。
echo.
echo   例）manuals\ロボドリル\操作説明書.pdf
goto done

:done
echo.
pause
exit /b

rem ---------------------------------------------------------------- 補助
rem py ランチャー、python、python3 の順に試し、実際に起動できて
rem 3.10以上のものだけを採用する（Microsoft Store のダミーを弾くため）。
:find_python
set "PYTHON="
call :try_python "py -3"
if defined PYTHON exit /b
call :try_python "python"
if defined PYTHON exit /b
call :try_python "python3"
exit /b

:try_python
%~1 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 exit /b
set "PYTHON=%~1"
exit /b
