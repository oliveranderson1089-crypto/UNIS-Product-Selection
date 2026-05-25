"""PDF extractor — text + tables via pdfplumber."""

from __future__ import annotations

import logging
from pathlib import Path

from .base import ExtractedContent, ExtractedTable

logger = logging.getLogger(__name__)


def extract_pdf(path: Path) -> ExtractedContent:
    """
    Extract plain text and tables from a PDF.

    pdfplumber handles vector-text PDFs well; scanned (image-only) PDFs will
    return empty text — those require an OCR pass which we intentionally do
    not bundle to keep the dependency surface small.
    """
    import pdfplumber

    text_chunks: list[str] = []
    tables: list[ExtractedTable] = []

    with pdfplumber.open(path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                text_chunks.append(page_text)

            for raw_table in page.extract_tables() or []:
                # pdfplumber returns list[list[str|None]] — coerce None to ""
                cleaned = [[(c or "").strip() for c in row] for row in raw_table]
                # Skip empty / single-cell garbage tables
                if len(cleaned) < 2 or all(not any(r) for r in cleaned):
                    continue
                tables.append(ExtractedTable(rows=cleaned, page=page_num))

    content = ExtractedContent(
        source=path,
        kind="pdf",
        text="\n\n".join(text_chunks),
        tables=tables,
        meta={"page_count": len(pdf.pages) if 'pdf' in dir() else None},
    )

    if not content:
        logger.warning(
            "PDF %s produced no text or tables — likely a scanned/image PDF. "
            "Consider OCR'ing it first.", path,
        )
    return content


__all__ = ["extract_pdf"]
