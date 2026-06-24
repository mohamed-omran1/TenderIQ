"""PDF text extraction via PyMuPDF (imported as `fitz`).

Two failure modes that REQ-001 calls out explicitly:
  - Corrupt / password-protected PDF → extraction raises → caller fails the run
    with a "could not extract" reason (Alt Flow 4).
  - Scanned PDF (image-only, near-zero extractable text) → caller fails with a
    "scanned" reason; OCR is explicitly out of MVP scope (Alt Flow 5).

`extract_pages` returns one dict per page: {page_number, text}. The caller
chunks and embeds from there.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Below this many characters of extractable text across the WHOLE PDF we treat
# it as scanned (image-only). A real scanned page extracts exactly 0 chars; a
# text-born tender page extracts dozens minimum. Keep this small so a short
# but legitimate text page isn't misclassified as scanned.
SCANNED_TEXT_THRESHOLD_CHARS = 10


class PdfExtractionError(Exception):
    """Raised when a PDF can't be parsed (corrupt, encrypted, wrong format)."""


class ScannedPdfError(Exception):
    """Raised when the PDF extracts near-zero text across all pages (image-only).

    OCR fallback is explicitly out of MVP scope (PRD §4.2, REQ-001 Alt Flow 5).
    """


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int  # 1-indexed for human-facing citations
    text: str


def extract_pages(pdf_bytes: bytes) -> list[ExtractedPage]:
    """Extract text page-by-page. Raises on corrupt/encrypted or scanned PDFs.

    Page numbers are 1-indexed so they map directly to what an analyst sees in
    a PDF viewer — provenance citations must be human-meaningful (rag-architect
    skill: "a finding without a page reference is an assertion").
    """
    pages: list[ExtractedPage] = []
    total_text_chars = 0

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 — PyMuPDF raises various error types
        logger.info("pdf_open_failed error=%s", type(exc).__name__)
        raise PdfExtractionError(f"Could not open PDF: {type(exc).__name__}") from exc

    if doc.is_encrypted:
        # try empty password (common for "owner-locked" but user-open docs)
        if not doc.authenticate(""):
            doc.close()
            raise PdfExtractionError("PDF is password-protected.")

    try:
        for i in range(doc.page_count):
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            total_text_chars += len(text.strip())
            pages.append(ExtractedPage(page_number=i + 1, text=text))
    finally:
        doc.close()

    # Scanned-PDF detection: if EVERY page is near-empty, it's image-only.
    if total_text_chars < SCANNED_TEXT_THRESHOLD_CHARS:
        raise ScannedPdfError(
            "PDF appears to be scanned (no extractable text). OCR is not supported in MVP."
        )

    return pages
