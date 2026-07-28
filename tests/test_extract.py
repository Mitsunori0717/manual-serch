"""テキストPDFとスキャンPDFの判別まわり。"""

from __future__ import annotations

from pathlib import Path

from conftest import make_image_only_pdf, make_pdf
from manualsearch.extract import extract_pdf, is_scanned


def test_text_pdf_is_extracted_per_page(tmp_path: Path):
    path = make_pdf(
        tmp_path / "a.pdf",
        [
            ["1ページ目の本文です。ここには十分な量の日本語が入っています。"],
            ["2ページ目の本文です。こちらにも同じくらいの文章があります。"],
        ],
    )
    result = extract_pdf(path, ocr="never")

    assert result.ok
    assert len(result.pages) == 2
    assert "1ページ目の本文" in result.pages[0]
    assert "2ページ目の本文" in result.pages[1]
    assert not is_scanned(result)


def test_scanned_pdf_has_no_text_layer(tmp_path: Path):
    path = make_image_only_pdf(tmp_path / "scan.pdf", ["これはスキャンしただけのページです"])
    result = extract_pdf(path, ocr="never")

    assert result.pages == [""]
    assert is_scanned(result) is True


def test_broken_file_reports_an_error_instead_of_raising(tmp_path: Path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"definitely not a pdf")

    result = extract_pdf(path, ocr="never")
    assert not result.ok
    assert result.pages == []


def test_missing_ocr_binary_is_reported_not_raised(tmp_path: Path, monkeypatch):
    """Tesseractが無い環境でも、テキストPDF部分は取り込めること。"""
    import manualsearch.extract as extract_module

    def boom(*args, **kwargs):
        raise extract_module.OcrUnavailable("tesseract is not installed")

    monkeypatch.setattr(extract_module, "_ocr_page", boom)

    path = make_image_only_pdf(tmp_path / "scan.pdf", ["スキャンページ"])
    result = extract_pdf(path, ocr="auto")

    assert result.ocr_pages == 0
    assert "OCRを実行できませんでした" in (result.error or "")


def test_ocr_result_is_used_when_it_beats_the_text_layer(tmp_path: Path, monkeypatch):
    import manualsearch.extract as extract_module

    monkeypatch.setattr(extract_module, "_ocr_page", lambda *a, **k: "OCRで読み取った本文")

    path = make_image_only_pdf(tmp_path / "scan.pdf", ["スキャンページ"])
    result = extract_pdf(path, ocr="auto")

    assert result.ocr_pages == 1
    assert result.pages == ["OCRで読み取った本文"]


def test_text_pages_are_not_sent_to_ocr(tmp_path: Path, monkeypatch):
    import manualsearch.extract as extract_module

    def fail(*args, **kwargs):
        raise AssertionError("テキストが取れているページをOCRに回してはいけない")

    monkeypatch.setattr(extract_module, "_ocr_page", fail)

    path = make_pdf(tmp_path / "a.pdf", [["十分な長さの日本語の本文がここに書かれています。"]])
    result = extract_pdf(path, ocr="auto")

    assert result.ocr_pages == 0
    assert result.ok
