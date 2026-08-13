#!/usr/bin/env python3
"""配布用のEXE（実行ファイル）を作る。

    python scripts/build_exe.py

Windowsでは build-exe.bat をダブルクリックすれば、依存の準備からここまで
自動で走る。出来上がりは dist/マニュアル検索/ にまとまり、そのフォルダごと
コピーすればPythonが入っていないPCでも動く。

PyInstallerはクロスビルドできないので、Windows用のEXEはWindows上で作ること
（他のOSで実行すると、そのOS用の実行ファイルができる）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_NAME = "マニュアル検索"

# dist/マニュアル検索/ に同梱する説明書き。現場のPCに配る人が読む。
DIST_README = """\
==============================
 マニュアル検索（EXE版）
==============================

【使いかた】
1. このフォルダごと、好きな場所にコピーしてください。
   （Pythonのインストールは不要です）
2. 「マニュアル検索.exe」をダブルクリックしてください。
3. はじめて起動すると「manuals」フォルダができます。
   その中に機種ごとのフォルダを作ってPDFを入れ、もう一度起動してください。
     例） manuals\\ロボドリル\\操作説明書.pdf
4. 索引の更新が終わると、ブラウザで検索画面が開きます。

【やめるとき】
黒い画面で Ctrl+C を押すか、黒い画面のウィンドウを閉じてください。

【注意】
- スキャンしたPDF（画像だけのPDF）を検索するには、別途 Tesseract OCR の
  インストールが必要です。
  https://github.com/UB-Mannheim/tesseract/wiki （言語で Japanese を選ぶ）
- _internal フォルダは動作に必要です。消したり動かしたりしないでください。
- 検索の索引は index.db に、設定は .env に保存されます（どちらもEXEの隣に
  自動で作られます）。
"""


def main() -> int:
    os.chdir(REPO)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller がありません。先にインストールしてください:", file=sys.stderr)
        print(f"  {sys.executable} -m pip install pyinstaller", file=sys.stderr)
        return 1

    sep = ";" if os.name == "nt" else ":"
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        APP_NAME,
        # 検索画面のHTMLテンプレート。コードと同じ相対位置に入れる
        # （specは build/ に置かれ相対パスの基準がずれるので、絶対パスで渡す）
        "--add-data",
        f"{REPO / 'manualsearch' / 'templates'}{sep}manualsearch/templates",
        # uvicornは待ち受け方式を文字列で選んで動的にimportするので、
        # PyInstallerの静的解析から漏れることがある。明示しておく
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        # 作業ファイルは build/ に隔離する
        "--workpath", "build",
        "--specpath", "build",
        "--distpath", "dist",
        "scripts/exe_entry.py",
    ]

    print("PyInstallerでビルドします（数分かかります）...")
    result = subprocess.run(args)
    if result.returncode != 0:
        print("\n[エラー] ビルドに失敗しました。上のメッセージを確認してください。", file=sys.stderr)
        return result.returncode

    out_dir = REPO / "dist" / APP_NAME
    exe_name = f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME
    exe_path = out_dir / exe_name
    if not exe_path.exists():
        print(f"\n[エラー] 出来上がりが見つかりません: {exe_path}", file=sys.stderr)
        return 1

    (out_dir / "はじめにお読みください.txt").write_text(DIST_README, encoding="utf-8")

    size_mb = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1024 / 1024
    print()
    print("============================================")
    print(" ビルド完了")
    print("============================================")
    print(f"  出来上がり : {out_dir}")
    print(f"  実行ファイル: {exe_path.name}")
    print(f"  サイズ     : {size_mb:.0f} MB")
    print()
    print("このフォルダごとコピーして配ってください。")
    print("（Pythonが入っていないPCでも動きます）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
