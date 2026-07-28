"""コマンドライン。索引作成・検索・Webサーバー起動をここから叩く。"""

from __future__ import annotations

import argparse
import html
import re
import sqlite3
import sys
from pathlib import Path

from . import db, library, search as search_mod
from .analyze import analyze
from .assistant import Assistant, AssistantError
from .config import OCR_DPI, OCR_LANG, OCR_TEXT_THRESHOLD, Config
from .extract import extract_pdf, ocr_status
from .indexer import default_workers, index_directory, index_file, iter_pdfs

_MARK = re.compile(r"<mark>(.*?)</mark>", re.S)
_BOLD, _DIM, _RESET = "\033[1;33m", "\033[2m", "\033[0m"


def _plain(snippet_html: str, color: bool) -> str:
    """HTMLのスニペットを端末用に戻す。"""
    replacement = (_BOLD + r"\1" + _RESET) if color else r"\1"
    return html.unescape(_MARK.sub(replacement, snippet_html))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manualsearch",
        description="PDFマニュアルを全文検索する（SQLite FTS5 + 必要に応じてOCR）",
    )
    parser.add_argument("--db", default=None, help="索引DBのパス（既定: index.db / 環境変数 MANUAL_DB）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="PDFを索引に取り込む（差分更新）")
    p_index.add_argument("root", nargs="?", default=None, help="PDFの置き場（既定: manuals / 環境変数 MANUAL_ROOT）")
    p_index.add_argument(
        "--ocr",
        choices=["auto", "never", "force"],
        default="auto",
        help="auto: テキストが取れないページだけOCR（既定） / never: OCRしない / force: 全ページOCR",
    )
    p_index.add_argument("--lang", default=OCR_LANG, help=f"Tesseractの言語（既定: {OCR_LANG}）")
    p_index.add_argument("--dpi", type=int, default=OCR_DPI, help=f"OCR時の解像度（既定: {OCR_DPI}）")
    p_index.add_argument(
        "--threshold",
        type=int,
        default=OCR_TEXT_THRESHOLD,
        help=f"この文字数未満のページをOCR対象とみなす（既定: {OCR_TEXT_THRESHOLD}）",
    )
    p_index.add_argument("--force", action="store_true", help="変更がなくても全部作り直す")
    p_index.add_argument("--no-prune", action="store_true", help="消えたPDFを索引に残す")
    p_index.add_argument("--workers", type=int, default=None, help=f"並列数（既定: {default_workers()}）")
    p_index.add_argument("--quiet", action="store_true", help="1件ごとの進捗を出さない")

    p_add = sub.add_parser("add", help="PDFを1冊だけ取り込んで、中身を分析する")
    p_add.add_argument("pdf", help="取り込むPDF（PDF置き場の中にあるもの）")
    p_add.add_argument("--root", default=None, help="PDFの置き場")
    p_add.add_argument("--machine", default=None, help="機種名を確定する（library.csv に保存）")
    p_add.add_argument("--category", default=None, help="分類を付ける（例: 操作 / 保守 / 部品）")
    p_add.add_argument("--title", default=None, help="一覧に出す名前を上書きする")
    p_add.add_argument("--ocr", choices=["auto", "never", "force"], default="auto")
    p_add.add_argument("--lang", default=OCR_LANG)
    p_add.add_argument("--dpi", type=int, default=OCR_DPI)

    p_analyze = sub.add_parser("analyze", help="取り込まずに、PDFの分析結果だけ見る")
    p_analyze.add_argument("pdf", help="分析するPDF")
    p_analyze.add_argument("--ocr", choices=["auto", "never", "force"], default="auto")
    p_analyze.add_argument("--lang", default=OCR_LANG)
    p_analyze.add_argument("--dpi", type=int, default=OCR_DPI)

    p_set = sub.add_parser("set", help="取り込み済みマニュアルの機種などを後から設定する")
    p_set.add_argument("path", help="PDF置き場からの相対パス（library コマンドで確認できる）")
    p_set.add_argument("--root", default=None, help="PDFの置き場")
    p_set.add_argument("--machine", default=None)
    p_set.add_argument("--category", default=None)
    p_set.add_argument("--title", default=None)
    p_set.add_argument("--tags", default=None)
    p_set.add_argument("--note", default=None)

    p_library = sub.add_parser("library", help="マニュアル一覧（library.csv）を見る・作る")
    p_library.add_argument("root", nargs="?", default=None, help="PDFの置き場")
    p_library.add_argument(
        "--init",
        action="store_true",
        help="見つかったPDFから library.csv の下書きを作る（既存の行は残す）",
    )

    p_search = sub.add_parser("search", help="端末から検索する")
    p_search.add_argument(
        "query",
        nargs="+",
        help="検索語。除外語を書くときは `search ポンプ -- -旧型` のように -- で区切る",
    )
    p_search.add_argument("--limit", type=int, default=20, help="表示件数（既定: 20）")
    p_search.add_argument("--machine", default=None, help="この機種のマニュアルだけを対象にする")

    p_code = sub.add_parser("code", help="エラーコードから該当ページを引く")
    p_code.add_argument("code", help="例: E203")
    p_code.add_argument("--machine", default=None)

    p_serve = sub.add_parser("serve", help="ブラウザ用の検索画面を起動する")
    p_serve.add_argument("root", nargs="?", default=None, help="PDFの置き場（PDFを開くために必要）")
    p_serve.add_argument("--host", default="127.0.0.1", help="既定: 127.0.0.1（社内に公開するなら 0.0.0.0）")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="開発用の自動リロード")

    p_ask = sub.add_parser("ask", help="マニュアルの内容についてChatGPTに相談する")
    p_ask.add_argument("question", nargs="+", help="質問文（例: ロボドリルでSV0417が出た）")
    p_ask.add_argument("--machine", default=None, help="使っている機械")
    p_ask.add_argument("--sources", type=int, default=8, help="根拠として渡すページ数（既定: 8）")

    sub.add_parser("stats", help="索引の中身を表示する")
    sub.add_parser("optimize", help="索引を最適化して縮める")
    sub.add_parser("doctor", help="OCRなどの動作環境を確認する")

    return parser


def cmd_index(args: argparse.Namespace) -> int:
    config = Config.from_env(args.root, args.db)
    if args.ocr != "never":
        available, detail = ocr_status()
        if not available:
            print(f"警告: OCRが使えません（{detail}）。テキストPDFのみ索引します。", file=sys.stderr)
            print("  Ubuntu/Debian: sudo apt install tesseract-ocr tesseract-ocr-jpn", file=sys.stderr)
        else:
            print(f"OCR: {detail} / 言語 {args.lang}")

    print(f"PDF置き場: {config.root}")
    print(f"索引DB   : {config.db_path}")

    conn = db.connect(config.db_path)
    try:
        report = index_directory(
            conn,
            config.root,
            ocr=args.ocr,
            lang=args.lang,
            dpi=args.dpi,
            threshold=args.threshold,
            force=args.force,
            prune=not args.no_prune,
            workers=args.workers,
            on_progress=None if args.quiet else print,
        )
    finally:
        conn.close()

    print(
        f"\n完了: 追加 {report.added} / 更新 {report.updated} / 変更なし {report.skipped} "
        f"/ 台帳のみ更新 {report.relabeled} / 削除 {report.removed} "
        f"/ OCRしたページ {report.ocr_pages}"
    )
    if report.analyses and not args.quiet:
        sections = sum(len(a.sections) for _, a in report.analyses)
        codes = {code for _, a in report.analyses for code in a.error_codes}
        print(f"分析: 章立て {sections} 件 / エラーコード {len(codes)} 種を抽出しました")
    if report.scanned_docs:
        print(f"\nテキストを持たないPDFが {len(report.scanned_docs)} 件あります（--ocr auto で読めます）:")
        for path in report.scanned_docs[:10]:
            print(f"  - {path}")
    if report.failures:
        print(f"\n取り込めなかった/一部失敗したPDF {len(report.failures)} 件:", file=sys.stderr)
        for path, error in report.failures[:20]:
            print(f"  - {path}: {error}", file=sys.stderr)
    return 1 if report.failures and report.processed == 0 else 0


def _print_analysis(analysis, rel: str) -> None:
    """1冊分の分析結果を人が読める形で出す。"""
    print(f"\n■ {analysis.title}  ({rel})")
    print(f"  ページ数      : {analysis.page_count}（本文を取れた {analysis.text_pages}）")
    if analysis.ocr_pages:
        print(f"  OCRしたページ : {analysis.ocr_pages}")
    if analysis.coverage < 0.5:
        print("  ※ 本文を取れないページが半分以上あります。`--ocr force` を試してください。")

    source_label = {"pdf": "PDFのしおり", "heuristic": "本文の見出しから推定", "none": "見つからず"}
    print(f"  章立て        : {len(analysis.sections)} 件（{source_label[analysis.outline_source]}）")
    for section in analysis.sections[:8]:
        print(f"      P.{section.page_no:<4} {'  ' * (section.level - 1)}{section.title}")
    if len(analysis.sections) > 8:
        print(f"      … 他 {len(analysis.sections) - 8} 件")

    codes = analysis.error_codes
    print(f"  エラーコード  : {len(codes)} 種")
    if codes:
        print("      " + " ".join(codes[:20]) + (" …" if len(codes) > 20 else ""))
    if analysis.machine_candidates:
        print(f"  機種の候補    : {' / '.join(analysis.machine_candidates)}")


def cmd_add(args: argparse.Namespace) -> int:
    config = Config.from_env(args.root, args.db)
    pdf = Path(args.pdf).expanduser().resolve()
    conn = db.connect(config.db_path)
    try:
        analysis, error = index_file(
            conn,
            config.root,
            pdf,
            ocr=args.ocr,
            lang=args.lang,
            dpi=args.dpi,
            machine=args.machine,
            category=args.category,
            title=args.title,
        )
    finally:
        conn.close()

    if analysis is None:
        print(f"取り込めませんでした: {error}", file=sys.stderr)
        return 1

    rel = pdf.relative_to(config.root).as_posix()
    _print_analysis(analysis, rel)
    if error:
        print(f"  警告          : {error}", file=sys.stderr)
    print(f"\n索引に登録しました。機種などは `manualsearch set {rel} --machine ...` で直せます。")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf).expanduser().resolve()
    result = extract_pdf(pdf, ocr=args.ocr, lang=args.lang, dpi=args.dpi)
    if not result.ok and not any(result.pages):
        print(f"読み取れませんでした: {result.error}", file=sys.stderr)
        return 1

    _print_analysis(analyze(result, pdf.name), pdf.name)
    print("\n（索引には登録していません。登録するには add を使ってください）")
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    config = Config.from_env(args.root, args.db)
    rel = args.path.replace("\\", "/").strip()

    conn = db.connect(config.db_path)
    try:
        row = conn.execute(
            "SELECT id, title, machine, category, tags, note FROM documents WHERE path = ?", (rel,)
        ).fetchone()
        if row is None:
            print(f"索引にありません: {rel}", file=sys.stderr)
            print("`manualsearch library` で登録済みのパスを確認してください。", file=sys.stderr)
            return 1

        entries = library.load(config.library_path)
        entry = entries.get(rel) or library.LibraryEntry(path=rel)
        for name in ("machine", "category", "title", "tags", "note"):
            value = getattr(args, name, None)
            if value is not None:
                setattr(entry, name, value)
        if not entry.machine:
            entry.machine = library.machine_from_path(rel)

        db.update_metadata(
            conn,
            int(row["id"]),
            title=entry.title or row["title"],
            machine=entry.machine,
            category=entry.category,
            tags=entry.tags,
            note=entry.note,
        )
    finally:
        conn.close()

    entries[rel] = entry
    library.save(config.library_path, sorted(entries.values(), key=lambda e: e.path))
    print(f"更新しました: {rel}")
    print(f"  機種: {entry.machine or '(なし)'} / 分類: {entry.category or '(なし)'}")
    print(f"  台帳: {config.library_path}")
    return 0


def cmd_library(args: argparse.Namespace) -> int:
    config = Config.from_env(args.root, args.db)

    if args.init:
        pdfs = list(iter_pdfs(config.root))
        entries = library.scaffold(config.root, pdfs, library.load(config.library_path))
        library.save(config.library_path, entries)
        print(f"{config.library_path} に {len(entries)} 行を書き出しました。")
        print("machine / title / category 列を埋めてから `index` を実行すると反映されます。")
        return 0

    entries = library.load(config.library_path)
    if not entries:
        print(f"{config.library_path} がありません。`library --init` で下書きを作れます。")
        return 0

    print(f"{config.library_path}（{len(entries)} 件）\n")
    print(f"{'機種':<16} {'分類':<10} パス")
    for entry in sorted(entries.values(), key=lambda e: (e.machine, e.path)):
        print(f"{entry.machine or '-':<16} {entry.category or '-':<10} {entry.path}")
    return 0


def cmd_code(args: argparse.Namespace) -> int:
    config = Config.from_env(None, args.db)
    conn = db.connect(config.db_path, read_only=True)
    try:
        rows = search_mod.find_code(conn, args.code, machine=args.machine)
    finally:
        conn.close()

    if not rows:
        print(f"{args.code} は見つかりませんでした。`search {args.code}` も試してみてください。")
        return 1

    print(f"{args.code} の記載箇所:\n")
    for row in rows:
        machine = f"[{row['machine']}] " if row["machine"] else ""
        section = f" / {row['section']}" if row["section"] else ""
        print(f"  {machine}{row['title']}  P.{row['page_no']}{section}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    config = Config.from_env(None, args.db)
    if not config.ai.enabled:
        print("OPENAI_API_KEY が設定されていません。", file=sys.stderr)
        print("  export OPENAI_API_KEY=sk-...  を設定してから実行してください。", file=sys.stderr)
        return 1

    conn = db.connect(config.db_path, read_only=True)
    try:
        assistant = Assistant(config.ai)
        answer = assistant.ask(
            conn, " ".join(args.question), machine=args.machine, max_sources=args.sources
        )
    except AssistantError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    color = sys.stdout.isatty()
    print(f"\n{answer.answer}\n")
    if answer.sources:
        print(f"{_DIM if color else ''}根拠:{_RESET if color else ''}")
        for source in answer.sources:
            print(f"  [{source.number}] {source.title}  P.{source.page_no}  ({source.path})")
    if answer.keywords:
        note = f"検索した語: {' / '.join(answer.keywords)}"
        print(f"\n{_DIM}{note}{_RESET}" if color else f"\n{note}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    config = Config.from_env(None, args.db)
    conn = db.connect(config.db_path, read_only=True)
    color = sys.stdout.isatty()
    try:
        result = search_mod.search(
            conn, " ".join(args.query), limit=args.limit, machine=args.machine
        )
    finally:
        conn.close()

    if not result.hits:
        print("該当なし")
        return 1

    for group in result.by_document():
        machine = f"[{group.machine}] " if group.machine else ""
        print(f"\n{machine}{group.title}  ({group.path})")
        for hit in group.pages:
            if hit.section:
                print(f"  P.{hit.page_no:<5}{_DIM if color else ''}{hit.section}{_RESET if color else ''}")
                print(f"  {'':<7}{_plain(hit.snippet, color)}".replace("\n", " "))
            else:
                print(f"  P.{hit.page_no:<5}{_plain(hit.snippet, color)}".replace("\n", " "))

    shown = len(result.hits)
    tail = f"\n{result.total} ページ中 {shown} 件を表示 ({result.took_ms:.0f} ms)"
    print(f"{_DIM}{tail}{_RESET}" if color else tail)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    config = Config.from_env(args.root, args.db)
    if not config.db_path.exists():
        print(f"索引がありません: {config.db_path}", file=sys.stderr)
        print("先に `python -m manualsearch index <PDFの置き場>` を実行してください。", file=sys.stderr)
        return 1

    from .web import create_app

    print(f"http://{args.host}:{args.port}/ を開いてください（Ctrl+C で終了）")
    uvicorn.run(create_app(config), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    config = Config.from_env(None, args.db)
    conn = db.connect(config.db_path, read_only=True)
    try:
        info = db.stats(conn)
        size_mb = config.db_path.stat().st_size / 1024 / 1024
        print(f"索引DB          : {config.db_path} ({size_mb:.1f} MB)")
        print(f"マニュアル数    : {info['documents']:,}")
        print(f"総ページ数      : {info['pages']:,}")
        print(f"検索対象ページ  : {info['indexed_pages']:,}")
        print(f"OCRしたページ   : {info['ocr_pages']:,}")
        print(f"索引済み文字数  : {info['characters']:,}")
    finally:
        conn.close()
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    config = Config.from_env(None, args.db)
    conn = db.connect(config.db_path)
    try:
        before = config.db_path.stat().st_size
        db.optimize(conn)
        after = config.db_path.stat().st_size
        print(f"最適化しました: {before / 1024 / 1024:.1f} MB → {after / 1024 / 1024:.1f} MB")
    finally:
        conn.close()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    print(f"Python        : {sys.version.split()[0]}")
    print(f"SQLite        : {sqlite3.sqlite_version}")

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        print("FTS5 trigram  : 利用可")
    except sqlite3.Error as exc:
        ok = False
        print(f"FTS5 trigram  : 使えません（{exc}）。SQLite 3.34以降が必要です。")
    finally:
        conn.close()

    available, detail = ocr_status()
    print(f"OCR           : {'利用可' if available else '利用不可'}（{detail}）")
    if not available:
        print("                Ubuntu/Debian: sudo apt install tesseract-ocr tesseract-ocr-jpn")
        print("                macOS        : brew install tesseract tesseract-lang")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "index": cmd_index,
        "add": cmd_add,
        "analyze": cmd_analyze,
        "set": cmd_set,
        "library": cmd_library,
        "search": cmd_search,
        "code": cmd_code,
        "ask": cmd_ask,
        "serve": cmd_serve,
        "stats": cmd_stats,
        "optimize": cmd_optimize,
        "doctor": cmd_doctor,
    }
    try:
        return handlers[args.command](args)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n中断しました", file=sys.stderr)
        return 130
