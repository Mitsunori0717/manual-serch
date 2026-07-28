"""ディレクトリを走査してPDFを索引に取り込む。

同じディレクトリを何度実行しても、サイズと更新時刻が変わっていないPDFは読み飛ばす。
マニュアルが増えたときは同じコマンドをもう一度叩けばよい。
"""

from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Callable, Iterator

from . import db, library
from .analyze import Analysis, analyze
from .config import LIBRARY_FILENAME, OCR_DPI, OCR_LANG, OCR_TEXT_THRESHOLD
from .extract import ExtractResult, OcrMode, extract_pdf, is_scanned
from .library import LibraryEntry


@dataclass
class IndexReport:
    """インデックス作成の結果。"""

    added: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0
    relabeled: int = 0
    ocr_pages: int = 0
    scanned_docs: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    analyses: list[tuple[str, Analysis]] = field(default_factory=list)

    @property
    def processed(self) -> int:
        return self.added + self.updated


def iter_pdfs(root: Path) -> Iterator[Path]:
    """ルート配下のPDFを名前順に列挙する。"""
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and path.suffix.lower() == ".pdf" and not path.name.startswith("."):
            yield path


def entry_for(entries: dict[str, LibraryEntry], rel: str) -> LibraryEntry:
    """台帳の行を取り出す。無ければフォルダ名から機種を埋めた既定値を返す。"""
    entry = entries.get(rel)
    if entry is None:
        return LibraryEntry(path=rel, machine=library.machine_from_path(rel))
    if not entry.machine:
        entry.machine = library.machine_from_path(rel)
    return entry


def default_workers() -> int:
    return max(1, min(os.cpu_count() or 1, 8))


def _needs_reindex(row: sqlite3.Row | None, stat: os.stat_result) -> bool:
    if row is None:
        return True
    # 更新時刻は環境によって微妙にずれるので、1秒の誤差は同一とみなす
    return row["size"] != stat.st_size or abs(row["mtime"] - stat.st_mtime) > 1.0


def index_directory(
    conn: sqlite3.Connection,
    root: Path,
    *,
    ocr: OcrMode = "auto",
    lang: str = OCR_LANG,
    dpi: int = OCR_DPI,
    threshold: int = OCR_TEXT_THRESHOLD,
    force: bool = False,
    prune: bool = True,
    workers: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> IndexReport:
    """``root`` 配下のPDFを索引に取り込む。"""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"PDFの置き場が見つかりません: {root}")

    report = IndexReport()
    existing = db.known_documents(conn)
    entries = library.load(root / LIBRARY_FILENAME)
    seen: set[str] = set()
    targets: list[tuple[Path, str, os.stat_result, bool]] = []

    for path in iter_pdfs(root):
        rel = path.relative_to(root).as_posix()
        seen.add(rel)
        row = existing.get(rel)
        stat = path.stat()
        if not force and not _needs_reindex(row, stat):
            report.skipped += 1
            # 本文は読み直さないが、台帳を書き換えただけの変更はここで拾う。
            # 台帳に書いていない項目で既存の値を消さないこと。機種は取り込み時に
            # 本文から推定していることがあり、ここで空にすると2回目の実行で消える。
            entry = entry_for(entries, rel)
            if db.update_metadata(
                conn,
                int(row["id"]),
                title=entry.title or row["title"],
                machine=entry.machine or row["machine"],
                category=entry.category,
                tags=entry.tags,
                note=entry.note,
            ):
                report.relabeled += 1
            continue
        targets.append((path, rel, stat, row is None))

    if prune:
        for rel, row in existing.items():
            if rel not in seen:
                db.delete_document(conn, int(row["id"]))
                report.removed += 1
                _emit(on_progress, f"削除  {rel}")

    if not targets:
        return report

    extract = partial(extract_pdf, ocr=ocr, lang=lang, dpi=dpi, threshold=threshold)
    worker_count = workers if workers is not None else default_workers()
    total = len(targets)

    def store(index: int, rel: str, stat: os.stat_result, is_new: bool, result: ExtractResult) -> None:
        if not result.ok and not any(result.pages):
            report.failures.append((rel, result.error or "不明なエラー"))
            _emit(on_progress, f"[{index}/{total}] 失敗  {rel}: {result.error}")
            return
        if not result.ok:
            # 一部のページだけ失敗した場合は、取れたぶんを索引に入れて警告だけ残す
            report.failures.append((rel, result.error or "不明なエラー"))

        entry = entry_for(entries, rel)
        # 1冊ずつ中身を分析して、章立て・コード・機種候補まで出しておく
        analysis = analyze(result, rel)
        machine = entry.machine or (analysis.machine_candidates[0] if analysis.machine_candidates else "")

        db.upsert_document(
            conn,
            path=rel,
            # 台帳のタイトルを最優先。無ければPDFのメタデータ、それも無ければファイル名。
            title=entry.title or result.title,
            machine=machine,
            category=entry.category,
            tags=entry.tags,
            note=entry.note,
            size=stat.st_size,
            mtime=stat.st_mtime,
            pages=result.pages,
            ocr_pages=result.ocr_pages,
            analysis=analysis,
        )
        report.ocr_pages += result.ocr_pages
        report.analyses.append((rel, analysis))
        if is_new:
            report.added += 1
        else:
            report.updated += 1
        if ocr == "never" and is_scanned(result, threshold):
            report.scanned_docs.append(rel)

        mark = "OCR" if result.ocr_pages else "   "
        detail = f"{len(result.pages)}ページ"
        if analysis.sections:
            detail += f" / 章 {len(analysis.sections)}"
        if analysis.error_codes:
            detail += f" / コード {len(analysis.error_codes)}"
        if machine:
            detail += f" / 機種 {machine}"
        _emit(on_progress, f"[{index}/{total}] {mark} {rel} ({detail})")

    if worker_count == 1:
        for i, (path, rel, stat, is_new) in enumerate(targets, start=1):
            store(i, rel, stat, is_new, extract(path))
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(extract, path): (i, rel, stat, is_new)
                for i, (path, rel, stat, is_new) in enumerate(targets, start=1)
            }
            for future in as_completed(futures):
                index, rel, stat, is_new = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - 子プロセスの異常終了
                    report.failures.append((rel, f"抽出プロセスが失敗: {exc}"))
                    _emit(on_progress, f"[{index}/{total}] 失敗  {rel}: {exc}")
                    continue
                store(index, rel, stat, is_new, result)

    return report


def index_file(
    conn: sqlite3.Connection,
    root: Path,
    pdf: Path,
    *,
    ocr: OcrMode = "auto",
    lang: str = OCR_LANG,
    dpi: int = OCR_DPI,
    threshold: int = OCR_TEXT_THRESHOLD,
    machine: str | None = None,
    category: str | None = None,
    title: str | None = None,
) -> tuple[Analysis | None, str | None]:
    """PDFを1冊だけ取り込んで分析する。``(分析結果, エラー)`` を返す。

    ``machine`` などを渡すとその場で確定し、library.csv にも書き戻すので、
    次に索引を作り直しても設定が残る。
    """
    root = Path(root).resolve()
    pdf = Path(pdf).resolve()
    if not pdf.is_file():
        return None, f"PDFが見つかりません: {pdf}"
    if not pdf.is_relative_to(root):
        return None, f"PDFはPDF置き場({root})の中に置いてください: {pdf}"

    rel = pdf.relative_to(root).as_posix()
    result = extract_pdf(pdf, ocr=ocr, lang=lang, dpi=dpi, threshold=threshold)
    if not result.ok and not any(result.pages):
        return None, result.error

    analysis = analyze(result, rel)
    entries = library.load(root / LIBRARY_FILENAME)
    entry = entry_for(entries, rel)

    if machine is not None:
        entry.machine = machine
    elif not entry.machine and analysis.machine_candidates:
        entry.machine = analysis.machine_candidates[0]
    if category is not None:
        entry.category = category
    if title is not None:
        entry.title = title

    stat = pdf.stat()
    db.upsert_document(
        conn,
        path=rel,
        title=entry.title or result.title,
        machine=entry.machine,
        category=entry.category,
        tags=entry.tags,
        note=entry.note,
        size=stat.st_size,
        mtime=stat.st_mtime,
        pages=result.pages,
        ocr_pages=result.ocr_pages,
        analysis=analysis,
    )

    entries[rel] = entry
    library.save(root / LIBRARY_FILENAME, sorted(entries.values(), key=lambda e: e.path))
    return analysis, result.error


def _emit(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)
