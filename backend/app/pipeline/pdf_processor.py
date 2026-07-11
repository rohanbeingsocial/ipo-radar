"""Stage 1: raw PDF -> per-page text + table-of-contents bookmarks.

PyMuPDF gives page-level text (needed for citations) and embedded bookmarks.
Pages with almost no text are image pages; OCR is opt-in via ENABLE_OCR=1.
"""
from __future__ import annotations

import fitz  # PyMuPDF

from ..config import ENABLE_OCR

LOW_TEXT_THRESHOLD = 40  # chars; below this a page is treated as image-only


def _ocr_page(page: fitz.Page) -> str:
    try:
        import pytesseract
        from PIL import Image
        import io

        pix = page.get_pixmap(dpi=300)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        return pytesseract.image_to_string(img)
    except Exception:
        return ""


def process_pdf(path: str) -> dict:
    doc = fitz.open(path)
    pages: list[dict] = []
    low_text_pages: list[int] = []

    for i, page in enumerate(doc):
        n = i + 1  # 1-indexed physical page, used everywhere for citations
        text = page.get_text("text") or ""
        if len(text.strip()) < LOW_TEXT_THRESHOLD:
            if ENABLE_OCR:
                text = _ocr_page(page) or text
            if len(text.strip()) < LOW_TEXT_THRESHOLD:
                low_text_pages.append(n)
        pages.append({"n": n, "text": text})

    toc = [{"level": lvl, "title": title, "page": pno} for lvl, title, pno in doc.get_toc(simple=True)]
    result = {
        "page_count": len(pages),
        "pages": pages,
        "toc": toc,
        "low_text_pages": low_text_pages,
        "readable_ratio": 1 - (len(low_text_pages) / max(1, len(pages))),
    }
    doc.close()
    return result
