"""
Catalog extractor using local OCR.

Fallback for when Claude API is unavailable or you want a zero-cost option.
Uses rapidocr-onnxruntime — a lightweight ONNX port of PaddleOCR with
excellent Chinese support and ~150MB footprint.

First run will lazy-download the bundled ONNX models (~25MB).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image
import io

from ..rasterize import pdf_to_png_bytes
from .base import CatalogExtractor

logger = logging.getLogger(__name__)


class OCRExtractor(CatalogExtractor):
    name = "ocr"

    def __init__(self, dpi: int = 200):
        self.dpi = dpi
        self._engine = None     # lazy

    def available(self) -> bool:
        """
        True only if rapidocr AND its onnxruntime backend both import.

        We swallow EVERY exception (not just ImportError) because
        onnxruntime on Windows commonly fails with a DLL initialization
        error when the Microsoft VC++ Redistributable is missing — that
        comes through as OSError/ImportError, depending on the version.
        """
        try:
            import rapidocr_onnxruntime   # noqa: F401
            return True
        except Exception:                                              # noqa: BLE001
            return False

    def extract_text(self, pdf_path: Path) -> str:
        if not self.available():
            raise RuntimeError(
                "OCR extractor unavailable. Likely causes:\n"
                "  1) Package not installed: pip install rapidocr-onnxruntime\n"
                "  2) onnxruntime DLL fails to load on Windows. Install "
                "Microsoft VC++ Redistributable (x64): "
                "https://aka.ms/vs/17/release/vc_redist.x64.exe"
            )

        pages = pdf_to_png_bytes(pdf_path, dpi=self.dpi)
        if not pages:
            logger.warning("No pages rasterized from %s", pdf_path)
            return ""

        engine = self._lazy_engine()
        out_lines: list[str] = []
        for i, png in enumerate(pages, 1):
            logger.info("OCR: page %d/%d", i, len(pages))
            img_array = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
            result, _elapse = engine(img_array)
            if not result:
                continue
            # rapidocr returns list of [bbox, text, confidence]
            for item in result:
                # robust against shape variations across rapidocr versions
                text = item[1] if len(item) >= 2 else ""
                if text and isinstance(text, str):
                    out_lines.append(text.strip())
            out_lines.append("")    # blank line between pages

        return "\n".join(out_lines)

    def _lazy_engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            # Defaults are tuned for Chinese; force CPU to avoid GPU surprises.
            self._engine = RapidOCR()
        return self._engine


__all__ = ["OCRExtractor"]
