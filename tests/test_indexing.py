"""PDF取り込みから検索までを通しで確認する。"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_pdf
from manualsearch import db
from manualsearch.indexer import index_directory, iter_pdfs
from manualsearch.search import search


@pytest.fixture
def indexed(tmp_path: Path, manual_root: Path):
    conn = db.connect(tmp_path / "index.db")
    index_directory(conn, manual_root, ocr="never", workers=1)
    yield conn, manual_root
    conn.close()


def test_iter_pdfs_finds_files_in_subdirectories(manual_root: Path):
    found = {p.name for p in iter_pdfs(manual_root)}
    assert found == {"P-100_取扱説明書.pdf", "P-050_旧型マニュアル.pdf", "C-9_manual.pdf"}


def test_index_records_documents_and_pages(indexed):
    conn, _ = indexed
    info = db.stats(conn)
    assert info["documents"] == 3
    assert info["pages"] == 4


def test_title_comes_from_pdf_metadata(indexed):
    conn, _ = indexed
    row = conn.execute(
        "SELECT title FROM documents WHERE path = 'ポンプ/P-100_取扱説明書.pdf'"
    ).fetchone()
    assert row["title"] == "P-100 ポンプ取扱説明書"


def test_title_falls_back_to_filename(indexed):
    conn, _ = indexed
    row = conn.execute(
        "SELECT title FROM documents WHERE path = 'ポンプ/P-050_旧型マニュアル.pdf'"
    ).fetchone()
    assert row["title"] == "P-050_旧型マニュアル"


def test_japanese_search_finds_the_right_page(indexed):
    conn, _ = indexed
    result = search(conn, "エラーコード")
    pages = {(hit.path, hit.page_no) for hit in result.hits}
    assert ("ポンプ/P-100_取扱説明書.pdf", 2) in pages
    assert ("ポンプ/P-050_旧型マニュアル.pdf", 1) in pages


def test_multiple_terms_are_and_ed(indexed):
    conn, _ = indexed
    result = search(conn, "エラーコード 冷却")
    assert {hit.path for hit in result.hits} == {"ポンプ/P-100_取扱説明書.pdf"}


def test_fullwidth_query_matches_halfwidth_text(indexed):
    conn, _ = indexed
    assert search(conn, "Ｅ２０３").total == 2


def test_exclusion_removes_documents(indexed):
    conn, _ = indexed
    result = search(conn, "エラーコード -旧型")
    assert {hit.path for hit in result.hits} == {"ポンプ/P-100_取扱説明書.pdf"}


def test_phrase_query_requires_exact_sequence(indexed):
    conn, _ = indexed
    assert search(conn, '"冷却 ファン"').total == 1
    assert search(conn, '"ファン 冷却"').total == 0


def test_hyphenated_english_line_break_is_searchable(indexed):
    conn, _ = indexed
    assert search(conn, "international").total == 1


def test_short_query_uses_the_scan_fallback(indexed):
    conn, _ = indexed
    result = search(conn, "旧型"[:1])  # 1文字なのでtrigram索引が使えない
    assert result.slow_path is True
    assert result.total >= 1


def test_machine_filter_narrows_results(indexed):
    conn, _ = indexed
    # 台帳が無いときは第1階層のフォルダ名が機種になる
    assert search(conn, "エラーコード", machine="コンプレッサ").total == 0
    assert search(conn, "エラーコード", machine="ポンプ").total == 2


def test_snippet_highlights_the_term(indexed):
    conn, _ = indexed
    hit = search(conn, "エラーコード").hits[0]
    assert "<mark>エラーコード</mark>" in hit.snippet


def test_results_group_by_document(indexed):
    conn, _ = indexed
    groups = search(conn, "エラーコード").by_document()
    assert len(groups) == 2
    assert all(group.total_hits >= 1 for group in groups)


def test_empty_query_returns_nothing(indexed):
    conn, _ = indexed
    assert search(conn, "   ").total == 0


def test_reindex_skips_unchanged_files(indexed):
    conn, root = indexed
    report = index_directory(conn, root, ocr="never", workers=1)
    assert report.skipped == 3
    assert report.processed == 0


def test_edited_pdf_is_reindexed_and_old_text_disappears(indexed):
    conn, root = indexed
    target = root / "ポンプ" / "P-050_旧型マニュアル.pdf"
    make_pdf(target, [["新しい内容に差し替えました"]])

    report = index_directory(conn, root, ocr="never", workers=1)
    assert report.updated == 1
    assert search(conn, "新しい内容").total == 1
    # 差し替え前の本文がFTS索引に残っていないこと
    assert {hit.path for hit in search(conn, "エラーコード").hits} == {
        "ポンプ/P-100_取扱説明書.pdf"
    }


def test_deleted_pdf_is_pruned(indexed):
    conn, root = indexed
    (root / "コンプレッサ" / "C-9_manual.pdf").unlink()

    report = index_directory(conn, root, ocr="never", workers=1)
    assert report.removed == 1
    assert db.stats(conn)["documents"] == 2
    assert search(conn, "international").total == 0


def test_broken_pdf_is_reported_but_does_not_stop_indexing(tmp_path: Path, manual_root: Path):
    (manual_root / "壊れたファイル.pdf").write_bytes(b"not a pdf at all")
    conn = db.connect(tmp_path / "index.db")
    try:
        report = index_directory(conn, manual_root, ocr="never", workers=1)
        assert report.added == 3
        assert [path for path, _ in report.failures] == ["壊れたファイル.pdf"]
    finally:
        conn.close()


def test_parallel_indexing_matches_serial(tmp_path: Path, manual_root: Path):
    conn = db.connect(tmp_path / "parallel.db")
    try:
        report = index_directory(conn, manual_root, ocr="never", workers=2)
        assert report.added == 3
        assert search(conn, "エラーコード").total == 2
    finally:
        conn.close()
