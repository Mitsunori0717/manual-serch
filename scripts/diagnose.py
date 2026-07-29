#!/usr/bin/env python3
"""環境を調べて 診断結果.txt を書き出す。

cmd のバッチでこれをやろうとすると、リダイレクトと日本語と括弧ブロックの
組み合わせで壊れやすい。判定はすべてここで行い、バッチ側は
「Pythonを見つけてこれを呼ぶ」だけにしてある。
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "診断結果.txt"

# 索引作成は時間がかかるので、確認用は短く打ち切る
INDEX_TIMEOUT = 300


class Report:
    """診断結果を組み立てる。"""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.problems: list[str] = []

    def head(self, title: str) -> None:
        self.lines.append("")
        self.lines.append(f"----- {title} -----")

    def say(self, text: str = "") -> None:
        self.lines.append(text)

    def ok(self, text: str) -> None:
        self.lines.append(f"  OK   {text}")

    def warn(self, text: str) -> None:
        self.lines.append(f"  注意 {text}")

    def bad(self, text: str) -> None:
        self.lines.append(f"  NG   {text}")
        self.problems.append(text)

    def block(self, text: str) -> None:
        for line in (text or "(出力なし)").rstrip().splitlines():
            self.lines.append(f"    | {line}")

    def render(self) -> str:
        header = [
            "===== マニュアル検索 診断結果 =====",
            f"日時    : {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"場所    : {ROOT}",
            f"OS      : {platform.platform()}",
        ]
        if self.problems:
            header.append("")
            header.append(f"■ 問題が {len(self.problems)} 件見つかりました:")
            header.extend(f"   - {problem}" for problem in self.problems)
        else:
            header.append("")
            header.append("■ 目立った問題は見つかりませんでした。")
        return "\n".join(header + self.lines) + "\n"


def run(command: list[str], timeout: int = 60) -> tuple[int, str]:
    """コマンドを実行して (終了コード, 出力) を返す。例外は文字列にして返す。"""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=ROOT,
        )
    except FileNotFoundError:
        return 127, "コマンドが見つかりません"
    except subprocess.TimeoutExpired:
        return 124, f"{timeout}秒を過ぎても終わりませんでした"
    except Exception as exc:  # pragma: no cover - 環境依存
        return 1, f"実行できませんでした: {exc}"
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def venv_python() -> Path | None:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        candidate = ROOT / rel
        if candidate.is_file():
            return candidate
    return None


def check_location(report: Report) -> None:
    report.head("置き場所")
    text = str(ROOT)
    if "onedrive" in text.lower():
        report.warn(
            "OneDrive の中にあります。同期の競合や、ファイルがクラウドのみの状態に"
            "なって読めないことがあります。C:\\manual-serch などへ移すことを勧めます。"
        )
    else:
        report.ok("OneDrive の外にあります")

    if " " in ROOT.name:
        report.warn(f"フォルダ名に空白があります: {ROOT.name}")

    check_writable(report)


def check_writable(report: Report) -> None:
    """このフォルダに書き込めるか。

    書き込めないと、仮想環境も索引もPDFの台帳も作れない。管理者として実行すれば
    通ってしまうため原因に気づきにくく、次に普通に起動したときまた失敗する。
    """
    probe = ROOT / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except PermissionError:
        report.bad(
            "このフォルダに書き込めません。仮想環境も索引も作れないので動きません。"
            "フォルダを右クリック → プロパティ → セキュリティ で、お使いのユーザーに"
            "「変更」を許可してください（管理者として実行するのは応急処置です）。"
        )
        return
    except OSError as exc:
        report.bad(f"このフォルダに書き込めません: {exc}")
        return
    report.ok("このフォルダに書き込めます")


def check_files(report: Report) -> None:
    report.head("必要なファイル")
    required = {
        "manualsearch/__init__.py": "本体のプログラム",
        "requirements.txt": "必要なパッケージの一覧",
        "scripts/make_sample_manuals.py": "サンプル作成スクリプト",
    }
    for rel, label in required.items():
        if (ROOT / rel).is_file():
            report.ok(f"{rel}（{label}）")
        else:
            report.bad(f"{rel} がありません。ZIPを展開し直してください。")

    report.say()
    report.say("  このフォルダの中身:")
    for entry in sorted(ROOT.iterdir()):
        suffix = "\\" if entry.is_dir() else ""
        report.say(f"    {entry.name}{suffix}")


def check_pdfs(report: Report) -> None:
    report.head("PDFの置き場")
    manuals = ROOT / "manuals"
    if not manuals.is_dir():
        report.bad("manuals フォルダがありません")
        return

    pdfs = [p for p in manuals.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf"]
    if not pdfs:
        report.bad("manuals の中にPDFが1冊もありません")
    else:
        report.ok(f"PDF {len(pdfs)} 冊")

    machines: dict[str, int] = {}
    for pdf in pdfs:
        rel = pdf.relative_to(manuals)
        top = rel.parts[0] if len(rel.parts) > 1 else "(直下)"
        machines[top] = machines.get(top, 0) + 1
    for name, count in sorted(machines.items(), key=lambda kv: -kv[1]):
        report.say(f"    {name}: {count} 冊")

    # 置き場の外にPDFを置いてしまっていないか
    outside = [
        p
        for p in ROOT.rglob("*.pdf")
        if p.is_file() and manuals not in p.parents and ".venv" not in p.parts
    ]
    if outside:
        report.warn(
            f"manuals の外にPDFが {len(outside)} 冊あります。"
            "manuals フォルダの中に移動しないと検索できません。"
        )
        for path in outside[:10]:
            report.say(f"    {path.relative_to(ROOT)}")


def check_python(report: Report) -> None:
    report.head("Python")
    report.ok(f"この診断を動かしているPython: {sys.version.split()[0]} ({sys.executable})")

    if sys.version_info < (3, 10):
        report.bad(f"Python 3.10以上が必要です（今は {sys.version.split()[0]}）")

    report.say(f"    SQLite: {sqlite3.sqlite_version}")
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        conn.close()
        report.ok("全文検索(FTS5 trigram)が使えます")
    except sqlite3.Error as exc:
        report.bad(f"全文検索が使えません: {exc}")


def check_venv(report: Report) -> Path | None:
    report.head("仮想環境")
    python = venv_python()
    if python is None:
        report.warn(".venv がありません（start.bat がまだ最後まで通っていません）")
        return None

    code, out = run([str(python), "-c", "import sys; print(sys.version)"])
    if code != 0:
        report.bad(
            ".venv の中のPythonが動きません。フォルダを移動すると起こります。"
            ".venv フォルダを削除して start.bat を実行し直してください。"
        )
        report.block(out)
        return None

    report.ok(f".venv のPython: {out.strip().splitlines()[0]}")
    return python


def check_packages(report: Report, python: Path) -> None:
    report.head("必要なパッケージ")
    for module, label in [
        ("fitz", "PyMuPDF（PDFの読み取り）"),
        ("fastapi", "FastAPI（検索画面）"),
        ("uvicorn", "Uvicorn（サーバー）"),
        ("jinja2", "Jinja2（画面の組み立て）"),
        ("multipart", "python-multipart（設定画面の保存）"),
        ("openai", "OpenAI（AI相談。無くても検索は動く）"),
    ]:
        code, out = run([str(python), "-c", f"import {module}"])
        if code == 0:
            report.ok(label)
        elif module == "openai":
            report.warn(f"{label} が入っていません")
        else:
            report.bad(f"{label} が入っていません")
            report.block(out)


def check_ocr(report: Report) -> None:
    report.head("OCR（スキャンしたPDFの読み取り）")
    if shutil.which("tesseract") is None:
        report.warn(
            "tesseract が見つかりません。テキストを持つPDFは検索できますが、"
            "スキャンしただけのPDFは読み取れません。"
        )
        return
    code, out = run(["tesseract", "--list-langs"])
    langs = [line.strip() for line in out.splitlines()[1:] if line.strip()]
    report.ok(f"tesseract あり（言語: {', '.join(langs) if langs else '不明'}）")
    if "jpn" not in langs:
        report.warn("日本語データ(jpn)が入っていません。日本語のスキャンPDFは読めません。")


def check_app(report: Report, python: Path) -> None:
    report.head("アプリの動作確認")
    # index を先に走らせる。索引がまだ無い状態で stats を呼ぶと
    # 「索引が見つかりません」となり、問題ではないのに問題として数えてしまう。
    for label, args, timeout in [
        ("doctor（動作環境）", ["-m", "manualsearch", "doctor"], 60),
        ("index（索引の作成）", ["-m", "manualsearch", "index", "manuals"], INDEX_TIMEOUT),
        ("stats（索引の状況）", ["-m", "manualsearch", "stats"], 60),
    ]:
        report.say()
        report.say(f"  ▼ {label}")
        code, out = run([str(python), *args], timeout=timeout)
        report.block(out)
        if code != 0:
            report.bad(f"{label} が失敗しました（終了コード {code}）")


def write_report(text: str) -> Path | None:
    """診断結果を書き出す。書けない場所なら別の場所に逃がす。

    「フォルダに書き込めない」こと自体がよくある原因なので、
    その場合でもレポートだけは残せるようにしておく。
    """
    candidates = [OUTPUT]
    for folder in (Path(os.environ.get("TEMP", "")), Path.home() / "Desktop", Path.home()):
        if str(folder) and folder.is_dir():
            candidates.append(folder / OUTPUT.name)

    for candidate in candidates:
        try:
            candidate.write_text(text, encoding="utf-8-sig")
        except OSError:
            continue
        return candidate
    return None


def main() -> int:
    report = Report()
    check_location(report)
    check_files(report)
    check_pdfs(report)
    check_python(report)
    check_ocr(report)

    python = check_venv(report)
    if python is not None:
        check_packages(report, python)
        check_app(report, python)

    written = write_report(report.render())
    if written is None:
        print("診断結果をファイルに書き出せませんでした。以下をそのまま見せてください。")
        print()
        print(report.render())
        return 1

    print(f"診断結果を書き出しました: {written}")
    print()
    if report.problems:
        print(f"問題が {len(report.problems)} 件見つかりました:")
        for problem in report.problems:
            print(f"  - {problem}")
    else:
        print("目立った問題は見つかりませんでした。")
    print()

    # Windowsならメモ帳で開く
    try:
        os.startfile(written)  # type: ignore[attr-defined]
    except AttributeError:
        pass
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
