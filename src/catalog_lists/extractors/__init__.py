"""Catalog extractors — pluggable strategies for reading 名录 PDFs."""

from __future__ import annotations

from .base import CatalogExtractor
from .claude_vision import ClaudeVisionExtractor
from .ocr import OCRExtractor


def get_extractor(name: str) -> CatalogExtractor:
    """Return a fresh extractor by name. Raises if unknown."""
    if name == "claude":
        return ClaudeVisionExtractor()
    if name == "ocr":
        return OCRExtractor()
    raise ValueError(
        f"Unknown extractor {name!r}. Available: 'claude', 'ocr'."
    )


def get_best_available_extractor(preferred: str | None = None) -> CatalogExtractor:
    """
    Pick the best extractor that's actually usable.

    Resolution order:
      1) `preferred` if provided and available
      2) Claude (better accuracy, requires API key)
      3) OCR (offline fallback)
    """
    if preferred:
        ext = get_extractor(preferred)
        if not ext.available():
            raise RuntimeError(f"Requested extractor {preferred!r} is not available.")
        return ext
    claude = ClaudeVisionExtractor()
    if claude.available():
        return claude
    return OCRExtractor()


__all__ = [
    "CatalogExtractor",
    "ClaudeVisionExtractor",
    "OCRExtractor",
    "get_extractor",
    "get_best_available_extractor",
]
