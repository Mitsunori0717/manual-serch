"""EXE版の入口（launcher）の動き。"""

from __future__ import annotations

from pathlib import Path

from manualsearch import launcher


def test_base_dir_is_the_repo_root_when_not_frozen():
    assert (launcher.base_dir() / "manualsearch").is_dir()


def test_has_pdf_finds_nested_pdfs(tmp_path: Path):
    assert not launcher.has_pdf(tmp_path)
    pdf = tmp_path / "ポンプ" / "取説.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4\n")
    assert launcher.has_pdf(tmp_path)


def test_indexed_documents_counts_the_index(tmp_path, manual_root):
    from manualsearch import db
    from manualsearch.indexer import index_directory

    assert launcher.indexed_documents(tmp_path / "missing.db") == 0

    db_path = tmp_path / "index.db"
    conn = db.connect(db_path)
    index_directory(conn, manual_root, ocr="never", workers=1)
    conn.close()
    assert launcher.indexed_documents(db_path) == 3


def test_main_explains_when_there_are_no_pdfs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MANUAL_ROOT", str(tmp_path / "manuals"))
    assert launcher.main() == 0
    out = capsys.readouterr().out
    assert "PDFがありません" in out
    # 置き場は作ってあげる（次に入れるだけでよいように）
    assert (tmp_path / "manuals").is_dir()
