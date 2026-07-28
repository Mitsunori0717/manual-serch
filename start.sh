#!/usr/bin/env bash
# ワンタッチ起動（macOS / Linux）
#
#   ./start.sh
#
# 初回は仮想環境の作成と依存関係のインストールまで自動でやる。2回目以降は
# 増えたPDFだけ索引に足してから、ブラウザで検索画面を開く。
set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
MANUAL_ROOT="${MANUAL_ROOT:-manuals}"
MANUAL_DB="${MANUAL_DB:-index.db}"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

say() { printf '\033[1;34m▶\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------- Python
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  warn "Python 3.10以上が見つかりません。https://www.python.org/downloads/ からインストールしてください。"
  exit 1
fi

# ---------------------------------------------------------------- 仮想環境
if [ ! -d "$VENV" ]; then
  say "初回セットアップ: 仮想環境を作ります（1〜2分かかります）"
  "$PYTHON" -m venv "$VENV"
fi

VPY="$VENV/bin/python"

# requirements.txt が更新されたときだけ入れ直す
STAMP="$VENV/.requirements-stamp"
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  say "依存パッケージをインストールします"
  "$VPY" -m pip install --quiet --upgrade pip
  "$VPY" -m pip install --quiet -r requirements.txt
  touch "$STAMP"
fi

# ---------------------------------------------------------------- OCR
if ! command -v tesseract >/dev/null 2>&1; then
  warn "OCR用の tesseract が見つかりません。スキャンしたPDFは検索できません。"
  case "$(uname -s)" in
    Darwin) warn "  インストール: brew install tesseract tesseract-lang" ;;
    *)      warn "  インストール: sudo apt install tesseract-ocr tesseract-ocr-jpn" ;;
  esac
fi

# ---------------------------------------------------------------- PDF置き場
if [ ! -d "$MANUAL_ROOT" ]; then
  mkdir -p "$MANUAL_ROOT"
  say "PDF置き場 $MANUAL_ROOT を作りました"
fi

if ! find "$MANUAL_ROOT" -iname '*.pdf' -print -quit | grep -q .; then
  warn "$MANUAL_ROOT にPDFがありません。"
  printf '  お試し用のサンプルを作りますか？ [y/N]: '
  read -r answer || answer=""
  case "$answer" in
    [yY]*) "$VPY" scripts/make_sample_manuals.py "$MANUAL_ROOT" ;;
    *) warn "機種ごとのフォルダを作ってPDFを入れてから、もう一度実行してください。"; exit 0 ;;
  esac
fi

# ---------------------------------------------------------------- 索引と起動
say "索引を更新します（増えたPDFだけ読みます）"
MANUAL_DB="$MANUAL_DB" "$VPY" -m manualsearch index "$MANUAL_ROOT"

URL="http://$HOST:$PORT/"
say "検索画面: $URL"
(
  sleep 2
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  fi
) >/dev/null 2>&1 &

MANUAL_DB="$MANUAL_DB" exec "$VPY" -m manualsearch serve "$MANUAL_ROOT" --host "$HOST" --port "$PORT"
