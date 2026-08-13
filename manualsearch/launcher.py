"""EXE版の入口。ダブルクリック一発で「索引の更新 → 検索画面の起動」まで行う。

PyInstallerで固めたEXE（build-exe.bat で作る）はここから始まる。start.bat と
同じ流れだが、Pythonのインストールも仮想環境も要らない。

EXEの隣に manuals フォルダ・index.db・.env を置く。フォルダごとコピーすれば
別のPCでもそのまま動く。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def base_dir() -> Path:
    """データ（manuals / index.db / .env）を置く場所。

    EXEのときはEXEの隣。開発中に ``python -m manualsearch.launcher`` で
    動かしたときはリポジトリ直下。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def has_pdf(root: Path) -> bool:
    return any(root.rglob("*.pdf"))


def indexed_documents(db_path: Path) -> int:
    """索引に入っている冊数。索引がまだ無い・読めないときは0。"""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def _pause() -> None:
    """ダブルクリック起動だとエラーの瞬間に窓が消えるので、読む時間を作る。"""
    try:
        input("\nEnterキーを押すと閉じます...")
    except (EOFError, OSError):
        pass


def main() -> int:
    base = base_dir()
    # .env・index.db・manuals が全部EXEの隣で解決されるように、ここを基準にする
    os.chdir(base)

    from .cli import main as cli_main

    manuals = base / os.environ.get("MANUAL_ROOT", "manuals")

    print("============================================")
    print(" マニュアル検索")
    print("============================================")
    print()

    manuals.mkdir(parents=True, exist_ok=True)
    if not has_pdf(manuals):
        print(f"[注意] {manuals} にPDFがありません。")
        print()
        print("機種ごとのフォルダを作ってPDFを入れてから、もう一度実行してください。")
        print(r"  例）manuals\ロボドリル\操作説明書.pdf")
        _pause()
        return 0

    print("[1/2] 索引を更新します（増えたPDFだけ読みます）")
    rc = cli_main(["index", str(manuals)])
    if rc != 0:
        # OCRが無くてスキャンPDFだけ毎回失敗する、といった場合でも、
        # 取り込めた分があるなら検索画面は開いたほうが役に立つ。
        db_path = Path(os.environ.get("MANUAL_DB", "index.db"))
        if indexed_documents(db_path) > 0:
            print()
            print("[注意] 一部のPDFを取り込めませんでした（上のメッセージを参照）。")
            print("       取り込めたPDFだけで検索画面を起動します。")
        else:
            print()
            print("[エラー] 索引を作れませんでした。上のメッセージを確認してください。")
            _pause()
            return rc

    print()
    print("[2/2] 検索画面を起動します。ブラウザが自動で開きます。")
    print("      終了するには、この画面で Ctrl+C を押すか、ウィンドウを閉じてください。")
    print()
    rc = cli_main(["serve", str(manuals), "--open"])
    if rc != 0:
        _pause()
    return rc


def entry() -> None:
    """EXEの本当の入口。例外で窓が一瞬で消えるのを防ぐ。"""
    # 索引作成はプロセスを並べて走る。freeze_support() を最初に呼ばないと、
    # EXEでは子プロセスがもう一度 main() を実行して無限に増殖する。
    import multiprocessing

    multiprocessing.freeze_support()

    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        import traceback

        traceback.print_exc()
        print()
        print("[エラー] 予期しない問題が起きました。上のメッセージを控えてください。")
        _pause()
        sys.exit(1)


if __name__ == "__main__":
    entry()
