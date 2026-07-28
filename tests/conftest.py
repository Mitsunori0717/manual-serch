from __future__ import annotations

from pathlib import Path

import fitz
import pytest


def make_pdf(
    path: Path,
    pages: list[list[str]],
    *,
    title: str | None = None,
    toc: list[tuple[int, str, int]] | None = None,
) -> Path:
    """行のリストからテスト用の日本語PDFを作る。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        y = 80.0
        for line in lines:
            page.insert_text((60, y), line, fontname="japan", fontsize=11)
            y += 18
    if title:
        doc.set_metadata({"title": title})
    if toc:
        doc.set_toc([list(item) for item in toc])
    doc.save(path)
    doc.close()
    return path


def make_image_only_pdf(path: Path, lines: list[str]) -> Path:
    """テキスト層を持たない「スキャンしただけ」のPDFを作る。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    source = fitz.open()
    page = source.new_page()
    y = 80.0
    for line in lines:
        page.insert_text((60, y), line, fontname="japan", fontsize=11)
        y += 18
    pixmap = page.get_pixmap(dpi=150)

    scanned = fitz.open()
    out_page = scanned.new_page(width=page.rect.width, height=page.rect.height)
    out_page.insert_image(out_page.rect, pixmap=pixmap)
    scanned.save(path)
    scanned.close()
    source.close()
    return path


@pytest.fixture
def manual_root(tmp_path: Path) -> Path:
    """テスト用のマニュアル置き場。"""
    root = tmp_path / "manuals"
    make_pdf(
        root / "ポンプ" / "P-100_取扱説明書.pdf",
        [
            [
                "第1章 安全上のご注意",
                "本機を設置する前に必ずお読みください。",
                "資格を持つ作業者以外は分解しないでください。",
            ],
            [
                "第2章 トラブルシューティング",
                "エラーコード E203 が表示された場合は、装置を停止し",
                "冷却 ファン の回転を確認してください。異音がする",
                "ときは軸受けの摩耗が考えられます。",
            ],
        ],
        title="P-100 ポンプ取扱説明書",
    )
    make_pdf(
        root / "ポンプ" / "P-050_旧型マニュアル.pdf",
        [
            [
                "旧型 P-050 の保守手順について説明します。",
                "エラーコード E203 は旧型では発生しません。",
                "定期点検は6か月ごとに実施してください。",
            ]
        ],
    )
    make_pdf(
        root / "コンプレッサ" / "C-9_manual.pdf",
        [
            [
                "Maintenance schedule",
                "Replace the inter-",
                "national filter every 500 hours.",
            ]
        ],
    )
    return root
