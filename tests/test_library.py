"""マニュアル一覧（library.csv）と、後からの手動設定。"""

from __future__ import annotations

from pathlib import Path

import pytest

from manualsearch import db, library
from manualsearch.indexer import index_directory, index_file
from manualsearch.search import list_machines, search


def write_csv(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_missing_library_is_not_an_error(tmp_path: Path):
    assert library.load(tmp_path / "library.csv") == {}


def test_entries_are_keyed_by_path(tmp_path: Path):
    write_csv(
        tmp_path / "library.csv",
        "path,machine,title,category,tags,note\n"
        "ロボドリル/a.pdf,ロボドリル,操作説明書,操作,段取り,2024年版\n",
    )
    entries = library.load(tmp_path / "library.csv")

    entry = entries["ロボドリル/a.pdf"]
    assert entry.machine == "ロボドリル"
    assert entry.title == "操作説明書"
    assert entry.category == "操作"
    assert entry.note == "2024年版"


def test_model_column_is_read_as_machine(tmp_path: Path):
    write_csv(tmp_path / "library.csv", "path,model\na.pdf,ロボドリル\n")
    assert library.load(tmp_path / "library.csv")["a.pdf"].machine == "ロボドリル"


def test_excel_bom_is_handled(tmp_path: Path):
    (tmp_path / "library.csv").write_text("path,machine\na.pdf,ロボドリル\n", encoding="utf-8-sig")
    assert library.load(tmp_path / "library.csv")["a.pdf"].machine == "ロボドリル"


def test_backslash_paths_are_normalised(tmp_path: Path):
    write_csv(tmp_path / "library.csv", "path,machine\nロボドリル\\a.pdf,ロボドリル\n")
    assert "ロボドリル/a.pdf" in library.load(tmp_path / "library.csv")


def test_missing_path_column_is_rejected(tmp_path: Path):
    write_csv(tmp_path / "library.csv", "machine,title\nロボドリル,操作\n")
    with pytest.raises(library.LibraryError):
        library.load(tmp_path / "library.csv")


def test_roundtrip_through_save(tmp_path: Path):
    entries = [library.LibraryEntry(path="a.pdf", machine="ロボドリル", note="メモ")]
    library.save(tmp_path / "library.csv", entries)
    assert library.load(tmp_path / "library.csv")["a.pdf"].note == "メモ"


def test_scaffold_fills_machine_from_the_folder(tmp_path: Path, manual_root: Path):
    from manualsearch.indexer import iter_pdfs

    entries = library.scaffold(manual_root, list(iter_pdfs(manual_root)))
    machines = {entry.path: entry.machine for entry in entries}
    assert machines["ポンプ/P-100_取扱説明書.pdf"] == "ポンプ"


def test_scaffold_keeps_rows_that_are_already_filled_in(tmp_path: Path, manual_root: Path):
    from manualsearch.indexer import iter_pdfs

    existing = {"ポンプ/P-100_取扱説明書.pdf": library.LibraryEntry(
        path="ポンプ/P-100_取扱説明書.pdf", machine="P-100 循環ポンプ", title="手で付けた名前"
    )}
    entries = {e.path: e for e in library.scaffold(manual_root, list(iter_pdfs(manual_root)), existing)}

    assert entries["ポンプ/P-100_取扱説明書.pdf"].title == "手で付けた名前"
    assert entries["ポンプ/P-100_取扱説明書.pdf"].machine == "P-100 循環ポンプ"


def test_library_overrides_machine_and_title(tmp_path: Path, manual_root: Path):
    write_csv(
        manual_root / "library.csv",
        "path,machine,title,category\n"
        "ポンプ/P-100_取扱説明書.pdf,循環ポンプP-100,現場用マニュアル,保守\n",
    )
    conn = db.connect(tmp_path / "index.db")
    try:
        index_directory(conn, manual_root, ocr="never", workers=1)
        row = conn.execute(
            "SELECT machine, title, category FROM documents WHERE path = ?",
            ("ポンプ/P-100_取扱説明書.pdf",),
        ).fetchone()

        assert row["machine"] == "循環ポンプP-100"
        assert row["title"] == "現場用マニュアル"
        assert row["category"] == "保守"
        assert search(conn, "エラーコード", machine="循環ポンプP-100").total == 1
    finally:
        conn.close()


def test_editing_the_library_updates_skipped_documents(tmp_path: Path, manual_root: Path):
    """PDFを触っていなくても、台帳を書き換えたら一覧に反映されること。"""
    conn = db.connect(tmp_path / "index.db")
    try:
        index_directory(conn, manual_root, ocr="never", workers=1)
        write_csv(
            manual_root / "library.csv",
            "path,machine\nポンプ/P-100_取扱説明書.pdf,あとから決めた機種\n",
        )
        report = index_directory(conn, manual_root, ocr="never", workers=1)

        assert report.processed == 0  # 本文は読み直していない
        assert report.relabeled == 1
        assert {m.name for m in list_machines(conn)} >= {"あとから決めた機種"}
    finally:
        conn.close()


def test_index_file_adds_one_manual_and_records_it_in_the_library(tmp_path: Path, manual_root: Path):
    conn = db.connect(tmp_path / "index.db")
    try:
        analysis, error = index_file(
            conn,
            manual_root,
            manual_root / "ポンプ" / "P-100_取扱説明書.pdf",
            ocr="never",
            machine="ロボドリル",
            category="操作",
        )
        assert error is None
        assert analysis is not None
        assert db.stats(conn)["documents"] == 1
        assert search(conn, "エラーコード", machine="ロボドリル").total == 1
    finally:
        conn.close()

    entry = library.load(manual_root / "library.csv")["ポンプ/P-100_取扱説明書.pdf"]
    assert entry.machine == "ロボドリル"
    assert entry.category == "操作"


def test_index_file_refuses_a_pdf_outside_the_root(tmp_path: Path, manual_root: Path):
    outside = tmp_path / "よそのPDF.pdf"
    outside.write_bytes(b"%PDF-1.4")

    conn = db.connect(tmp_path / "index.db")
    try:
        analysis, error = index_file(conn, manual_root, outside, ocr="never")
    finally:
        conn.close()

    assert analysis is None
    assert "PDF置き場" in (error or "")


def test_reindexing_keeps_a_machine_that_was_detected_from_the_pdf(tmp_path: Path, manual_root: Path):
    """台帳に機種が書かれていなくても、2回目の index で機種を消さないこと。

    start.bat / start.sh は起動のたびに index を通るため、ここで消えると
    2回目の起動で機種の選択肢が全部なくなってしまう。
    """
    from conftest import make_pdf

    # 機種フォルダを作らず、ルート直下に置く（フォルダ名から機種を取れない状況）
    make_pdf(
        manual_root / "FANUC ROBODORILL 取扱説明書.pdf",
        [["第1章 概要", "アラーム SV0417 が発生した場合の処置を説明します。"]],
    )

    conn = db.connect(tmp_path / "index.db")
    try:
        index_directory(conn, manual_root, ocr="never", workers=1)
        first = conn.execute(
            "SELECT machine FROM documents WHERE path = ?", ("FANUC ROBODORILL 取扱説明書.pdf",)
        ).fetchone()["machine"]
        assert first == "FANUC ROBODORILL"  # タイトル無しPDFなのでファイル名から

        # PDFを触っていないので読み飛ばされる経路を通る
        report = index_directory(conn, manual_root, ocr="never", workers=1)
        assert report.processed == 0

        second = conn.execute(
            "SELECT machine FROM documents WHERE path = ?", ("FANUC ROBODORILL 取扱説明書.pdf",)
        ).fetchone()["machine"]
        assert second == first
    finally:
        conn.close()


def test_the_library_still_wins_over_the_detected_machine(tmp_path: Path, manual_root: Path):
    from conftest import make_pdf

    make_pdf(manual_root / "FANUC ROBODORILL 取扱説明書.pdf", [["第1章 概要", "本文がここに入ります。"]])
    conn = db.connect(tmp_path / "index.db")
    try:
        index_directory(conn, manual_root, ocr="never", workers=1)
        write_csv(
            manual_root / "library.csv",
            "path,machine\nFANUC ROBODORILL 取扱説明書.pdf,ROBODORILL\n",
        )
        index_directory(conn, manual_root, ocr="never", workers=1)

        row = conn.execute(
            "SELECT machine FROM documents WHERE path = ?", ("FANUC ROBODORILL 取扱説明書.pdf",)
        ).fetchone()
        assert row["machine"] == "ROBODORILL"
    finally:
        conn.close()
