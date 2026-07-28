"""PDFからページ単位のテキストを取り出す。必要ならOCRにかける。

スキャンしただけのPDFはテキストを持っていないので、抽出結果が空に近いページだけ
画像に起こしてTesseractに渡す。ページ単位で判定するので、テキストPDFとスキャンPDFが
混ざったファイルでも無駄なOCRを走らせない。
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF

from .config import OCR_DPI, OCR_LANG, OCR_TEXT_THRESHOLD
from .textnorm import normalize_blocks, normalize_line, normalize_text

OcrMode = Literal["auto", "never", "force"]


class OcrUnavailable(RuntimeError):
    """Tesseract本体かPythonバインディングが入っていない。"""


@dataclass
class ExtractResult:
    """1つのPDFの抽出結果。"""

    path: Path
    title: str
    pages: list[str] = field(default_factory=list)
    # PDFのしおり（目次）。(階層, 見出し, ページ番号) の並び。
    outline: list[tuple[int, str, int]] = field(default_factory=list)
    ocr_pages: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def ocr_status() -> tuple[bool, str]:
    """OCRが使えるかと、その理由を返す。起動時の警告に使う。"""
    try:
        import pytesseract
    except ImportError:
        return False, "pytesseract が未インストールです（pip install pytesseract）"
    try:
        version = pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - 環境依存
        return False, f"tesseract 本体を実行できません（{exc}）"
    return True, f"tesseract {version}"


def _ocr_page(page: "fitz.Page", lang: str, dpi: int) -> str:
    """1ページを画像に起こしてOCRする。"""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - 環境依存
        raise OcrUnavailable(str(exc)) from exc

    pixmap = page.get_pixmap(dpi=dpi)
    image = Image.open(io.BytesIO(pixmap.tobytes("png")))
    try:
        raw = pytesseract.image_to_string(image, lang=lang)
    except Exception as exc:  # pragma: no cover - 環境依存
        raise OcrUnavailable(str(exc)) from exc
    finally:
        image.close()
    return normalize_text(raw)


def _outline(doc: "fitz.Document") -> list[tuple[int, str, int]]:
    """PDFのしおりを読む。無いPDFも多いので、その場合は空。"""
    try:
        toc = doc.get_toc(simple=True)
    except Exception:  # pragma: no cover - 壊れたしおり
        return []

    entries: list[tuple[int, str, int]] = []
    for item in toc:
        if len(item) < 3:
            continue
        level, title, page_no = int(item[0]), normalize_line(str(item[1])), int(item[2])
        if title and page_no > 0:
            entries.append((max(1, level), title, page_no))
    return entries


def _page_text(page: "fitz.Page") -> str:
    """埋め込みテキストを段落ごとに読み出す。"""
    blocks = page.get_text("blocks", sort=True)
    # blocks の要素は (x0, y0, x1, y1, text, block_no, block_type)。type 0 がテキスト。
    return normalize_blocks([b[4] for b in blocks if len(b) > 6 and b[6] == 0])


def extract_pdf(
    path: Path,
    *,
    ocr: OcrMode = "auto",
    lang: str = OCR_LANG,
    dpi: int = OCR_DPI,
    threshold: int = OCR_TEXT_THRESHOLD,
) -> ExtractResult:
    """PDFを開き、ページごとの正規化済みテキストを返す。

    例外は投げず :attr:`ExtractResult.error` に入れる。1冊壊れていても
    インデックス作成全体を止めないため。
    """
    path = Path(path)
    result = ExtractResult(path=path, title=path.stem)

    try:
        doc = fitz.open(path)
    except Exception as exc:
        result.error = f"PDFを開けません: {exc}"
        return result

    with doc:
        if doc.needs_pass:
            result.error = "パスワード保護されています"
            return result

        meta_title = (doc.metadata or {}).get("title") or ""
        meta_title = normalize_text(meta_title).strip()
        if meta_title:
            result.title = meta_title

        result.outline = _outline(doc)

        ocr_failed: str | None = None
        for page in doc:
            try:
                text = "" if ocr == "force" else _page_text(page)
            except Exception as exc:
                result.pages.append("")
                result.error = f"ページ {page.number + 1} の抽出に失敗: {exc}"
                continue

            needs_ocr = ocr == "force" or (ocr == "auto" and len(text) < threshold)
            if needs_ocr and ocr != "never" and ocr_failed is None:
                try:
                    ocr_text = _ocr_page(page, lang, dpi)
                except OcrUnavailable as exc:
                    # 一度失敗したら以降のページで再試行しない（同じ理由で必ず失敗するため）
                    ocr_failed = str(exc)
                else:
                    if len(ocr_text) > len(text):
                        text = ocr_text
                        result.ocr_pages += 1

            result.pages.append(text)

        if ocr_failed and result.error is None:
            result.error = f"OCRを実行できませんでした: {ocr_failed}"

    return result


def is_scanned(result: ExtractResult, threshold: int = OCR_TEXT_THRESHOLD) -> bool:
    """テキストが取れなかったページが過半数か（OCRの要否を人に見せるための判定）。"""
    if not result.pages:
        return False
    empty = sum(1 for text in result.pages if len(text) < threshold)
    return empty * 2 > len(result.pages)
