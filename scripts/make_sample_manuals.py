#!/usr/bin/env python3
"""動作確認用のサンプルPDFを作る。

    python scripts/make_sample_manuals.py manuals
    python -m manualsearch index manuals
    python -m manualsearch serve manuals

テキストPDF2冊と、テキスト層を持たない「スキャンしただけ」のPDF1冊を作るので、
OCRの要否判定もそのまま試せる。
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz

PAGE_SETS: dict[str, tuple[str, list[list[str]]]] = {
    "ポンプ/P-100_取扱説明書.pdf": (
        "P-100 循環ポンプ 取扱説明書",
        [
            [
                "第1章 安全上のご注意",
                "本機を設置する前に必ずお読みください。",
                "資格を持つ作業者以外は分解しないでください。",
                "電源を切ってから10分間は内部が高温です。",
            ],
            [
                "第2章 据付けと配線",
                "据付面は水平で、振動の少ない場所を選んでください。",
                "電源は AC200V 三相、専用ブレーカから配線します。",
                "アース線は必ず接続してください。",
            ],
            [
                "第3章 トラブルシューティング",
                "エラーコード E203 が表示された場合は装置を停止し、",
                "冷却ファンの回転と吸気口の目詰まりを確認してください。",
                "エラーコード E410 は吐出圧の異常です。バルブの開度を確認します。",
                "異音がするときは軸受けの摩耗が考えられます。",
            ],
        ],
    ),
    "ポンプ/P-050_旧型マニュアル.pdf": (
        "P-050 循環ポンプ（旧型）保守マニュアル",
        [
            [
                "P-050 の保守手順",
                "本機は P-100 の旧型です。部品の一部は生産を終了しています。",
                "エラーコード E203 は旧型では発生しません。",
                "定期点検は6か月ごとに実施してください。",
            ],
        ],
    ),
    "コンプレッサ/C-9_service_manual.pdf": (
        "C-9 Compressor Service Manual",
        [
            [
                "Maintenance schedule",
                "Replace the inter-",
                "national filter cartridge every 500 operating hours.",
                "Check the oil level before every shift.",
            ],
        ],
    ),
}

SCANNED = (
    "コンプレッサ/C-9_点検記録シート.pdf",
    [
        "点検記録シート（スキャン版）",
        "この紙はスキャンしただけなのでテキストが入っていません。",
        "OCRを有効にすると検索できるようになります。",
        "エラーコード E777 発生時は代理店に連絡してください。",
    ],
)


def _write_page(page: "fitz.Page", lines: list[str]) -> None:
    y = 90.0
    for i, line in enumerate(lines):
        page.insert_text((64, y), line, fontname="japan", fontsize=14 if i == 0 else 11)
        y += 26 if i == 0 else 20


def make_text_pdf(path: Path, title: str, pages: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for lines in pages:
        _write_page(doc.new_page(), lines)
    doc.set_metadata({"title": title})
    doc.save(path)
    doc.close()


def make_scanned_pdf(path: Path, lines: list[str]) -> None:
    """一度描画してから画像に焼き直し、テキスト層のないPDFにする。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    source = fitz.open()
    page = source.new_page()
    _write_page(page, lines)
    pixmap = page.get_pixmap(dpi=200)

    scanned = fitz.open()
    out = scanned.new_page(width=page.rect.width, height=page.rect.height)
    out.insert_image(out.rect, pixmap=pixmap)
    scanned.save(path)
    scanned.close()
    source.close()


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "manuals")
    for rel, (title, pages) in PAGE_SETS.items():
        make_text_pdf(root / rel, title, pages)
        print(f"作成: {root / rel}")

    rel, lines = SCANNED
    make_scanned_pdf(root / rel, lines)
    print(f"作成: {root / rel}（スキャン想定・テキスト層なし）")

    print(f"\n次はこれを実行してください:\n  python -m manualsearch index {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
