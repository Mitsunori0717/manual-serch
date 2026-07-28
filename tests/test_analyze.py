"""1冊のマニュアルを分解する解析まわり。"""

from __future__ import annotations

from pathlib import Path

from conftest import make_pdf
from manualsearch.analyze import (
    analyze,
    detect_codes,
    detect_headings,
    map_sections_to_pages,
    suggest_machines,
)
from manualsearch.extract import ExtractResult, extract_pdf


def _result(pages: list[str], **kwargs) -> ExtractResult:
    return ExtractResult(path=Path("dummy.pdf"), title="ダミー", pages=pages, **kwargs)


def test_chapter_headings_are_detected():
    sections = detect_headings(["第1章 安全上のご注意\n本文", "第2章 保守\n本文"])
    # 正規化で日本語間の空白は落ちる（索引と同じ形）
    assert [s.title for s in sections] == ["第1章安全上のご注意", "第2章保守"]


def test_numbered_headings_are_detected():
    sections = detect_headings(["3.2 潤滑油の交換\n手順は次のとおり"])
    assert sections[0].title == "3.2 潤滑油の交換"


def test_ordinary_sentences_are_not_headings():
    assert detect_headings(["1. これは普通の文章として終わります。"]) == []


def test_long_lines_are_not_headings():
    assert detect_headings(["第1章 " + "あ" * 60]) == []


def test_pages_inherit_the_section_that_started_before_them():
    sections = detect_headings(["第1章 概要\n本文", "続きの本文", "第3章 保守\n本文"])
    mapping = map_sections_to_pages(sections, 3)
    assert mapping == {1: "第1章概要", 2: "第1章概要", 3: "第3章保守"}


def test_pdf_outline_wins_over_heuristics(tmp_path: Path):
    path = make_pdf(
        tmp_path / "a.pdf",
        [["第1章 概要", "本文がここに入ります。"], ["第2章 保守", "本文がここに入ります。"]],
        toc=[(1, "しおりの見出し", 1)],
    )
    analysis = analyze(extract_pdf(path, ocr="never"), "a.pdf")

    assert analysis.outline_source == "pdf"
    assert [s.title for s in analysis.sections] == ["しおりの見出し"]


def test_labelled_error_codes_are_collected():
    codes = detect_codes(["エラーコード E203 が表示されます"])
    assert ("E203", "error") in {(c.code, c.kind) for c in codes}


def test_alarm_number_is_collected():
    codes = detect_codes(["アラーム 1234 が発生した場合"])
    assert "1234" in {c.code for c in codes if c.kind == "error"}


def test_bare_codes_are_only_picked_up_next_to_a_label():
    # 前置きの無いページの英数字は拾わない（誤検出を避けるため）
    assert not [c for c in detect_codes(["部品番号 AB123 を使用"]) if c.kind == "error"]
    # 同じページに「エラーコード」があれば一覧表とみなして拾う
    codes = detect_codes(["エラーコード一覧 エラーコード E203 E204 E205"])
    assert {"E203", "E204", "E205"} <= {c.code for c in codes if c.kind == "error"}


def test_page_numbers_are_recorded_for_codes():
    codes = detect_codes(["前書き", "エラーコード E410 の対処"])
    assert [c.page_no for c in codes if c.code == "E410"] == [2]


def test_machine_is_suggested_from_the_folder_first():
    result = _result(["本文"])
    assert suggest_machines(result, "ロボドリル/α-D21_操作.pdf", [])[0] == "ロボドリル"


def test_machine_falls_back_to_a_model_code_in_the_filename():
    result = _result(["本文"])
    assert "P-100" in suggest_machines(result, "P-100_取扱説明書.pdf", [])


def test_coverage_reports_pages_without_text():
    analysis = analyze(_result(["本文がある", "", "本文がある"]), "a.pdf")
    assert analysis.text_pages == 2
    assert analysis.empty_pages == [2]
    assert analysis.coverage == 2 / 3


def test_analysis_of_a_real_pdf(tmp_path: Path):
    path = make_pdf(
        tmp_path / "manual.pdf",
        [
            ["第1章 安全上のご注意", "設置前に必ずお読みください。"],
            ["第2章 異常時の処置", "エラーコード E203 が出たら電源を切ります。"],
        ],
    )
    analysis = analyze(extract_pdf(path, ocr="never"), "ロボドリル/manual.pdf")

    assert analysis.page_count == 2
    assert analysis.outline_source == "heuristic"
    assert analysis.section_of_page[2] == "第2章異常時の処置"
    assert "E203" in analysis.error_codes
    assert analysis.machine_candidates[0] == "ロボドリル"
