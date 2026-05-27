"""
Catalog extractor abstraction.

A `CatalogExtractor` reads a 名录 file (typically image-only PDF) and
returns plain text containing the product list. Callers DON'T parse the
text here — parsing model codes is a separate step (`model_codes.py`).

This split lets us swap extraction strategy without rewriting parsing:
    Claude vision  ──┐
                     ├── extracted text ──> model_codes.parse ──> [codes]
    Local OCR      ──┘
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar


class CatalogExtractor(ABC):
    """One method, but kept as an ABC so future extractors are obvious."""

    name: ClassVar[str] = "base"

    @abstractmethod
    def extract_text(self, pdf_path: Path) -> str:
        """Return the concatenated text content of the catalog PDF."""

    # ---- optional capability hint -------------------------------------------
    def available(self) -> bool:
        """
        Cheap pre-flight check. Default True — subclasses override if they
        need credentials / models that might not be present.
        """
        return True


__all__ = ["CatalogExtractor"]
