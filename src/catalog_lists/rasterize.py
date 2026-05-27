"""
PDF → page images.

Both extractors (Claude vision + OCR) need rasterized page images, so we
centralize the conversion here. Caller controls DPI to trade speed vs.
text crispness — 200 dpi works well for OCR, 150 is enough for Claude
because the model upscales internally.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def pdf_to_png_bytes(
    pdf_path: Path,
    *,
    dpi: int = 200,
    max_pages: int | None = None,
) -> list[bytes]:
    """
    Rasterize each PDF page to PNG bytes.

    Returns a list `[page1_bytes, page2_bytes, ...]`. Empty list if PDF
    cannot be opened.

    pypdfium2 is preferred over pdf2image because it doesn't require a
    system-installed Poppler binary.
    """
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                                          # noqa: BLE001
        logger.warning("Cannot open PDF %s: %s", pdf_path, exc)
        return []

    out: list[bytes] = []
    n_pages = len(pdf) if max_pages is None else min(len(pdf), max_pages)
    scale = dpi / 72.0   # PDF default user units = 72 dpi
    for i in range(n_pages):
        page = pdf[i]
        try:
            pil_image = page.render(scale=scale).to_pil()
        finally:
            page.close()
        buf = io.BytesIO()
        # PNG is lossless; Claude's tokenizer is happier with PNG than JPEG.
        pil_image.save(buf, format="PNG", optimize=True)
        out.append(buf.getvalue())
    pdf.close()
    logger.info("Rasterized %d page(s) from %s @ %d dpi", len(out), pdf_path.name, dpi)
    return out


__all__ = ["pdf_to_png_bytes"]
