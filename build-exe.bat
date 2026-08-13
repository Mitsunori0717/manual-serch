@echo off
rem 配布用EXEのワンタッチビルド。ダブルクリックするだけでよい。
rem NOTE: keep this file UTF-8 + CRLF. cmd.exe misparses LF-only batch files.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

echo ============================================
echo  マニュアル検索 EXE作成
echo ============================================
echo.

rem ---------------------------------------------------------------- Python
call :find_python
if not defined PYTHON goto no_python
echo 使用するPython: %PYTHON%

rem ---------------------------------------------------------------- 仮想環境
set "VPY=%~dp0.venv\Scripts\python.exe"
if exist "%VPY%" goto have_venv

echo [1/3] 仮想環境を作ります（1〜2分かかります）
%PYTHON% -m venv .venv
if not exist "%VPY%" goto venv_failed

:have_venv
echo [2/3] 依存パッケージとPyInstallerを準備します
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 goto pip_failed
"%VPY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto pip_failed

echo [3/3] EXEを作ります（数分かかります）
"%VPY%" scripts\build_exe.py
if errorlevel 1 goto build_failed
goto done

rem ---------------------------------------------------------------- 異常終了
:no_python
echo [エラー] Python 3.10以上が見つかりません。
echo   https://www.python.org/downloads/ からインストールし、
echo   「Add python.exe to PATH」にチェックを入れてください。
goto done

:venv_failed
echo [エラー] 仮想環境を作れませんでした。
echo   .venv フォルダを削除してから、もう一度実行してみてください。
goto done

:pip_failed
echo [エラー] パッケージのインストールに失敗しました。
echo   社内プロキシがある場合は、管理者に pip の設定を確認してください。
goto done

:build_failed
echo [エラー] EXEの作成に失敗しました。上のメッセージを確認してください。
goto done

:done
echo.
pause
exit /b

rem ---------------------------------------------------------------- 補助
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
