"""
Format-agnostic entry point for extraction.

Callers do:
    content = extract("/path/to/anything")
…and never branch on file type themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .base import ExtractedContent
from .docx import extract_docx
from .excel import extract_excel
from .image import extract_image
from .pdf import extract_pdf
from .text import extract_text

# Map file suffix → extractor function. Lower-case keys, no leading dot.
_EXTRACTORS: dict[str, Callable[[Path], ExtractedContent]] = {
    "pdf":  extract_pdf,
    "docx": extract_docx,
    "xlsx": extract_excel,
    "xlsm": extract_excel,
    "txt":  extract_text,
    "md":   extract_text,
    "csv":  extract_text,
    "png":  extract_image,
    "jpg":  extract_image,
    "jpeg": extract_image,
    "webp": extract_image,
    "gif":  extract_image,
}

_UNSUPPORTED_HINT = {
    "doc":  "Legacy .doc not supported. Convert to .docx (LibreOffice: "
            "`soffice --headless --convert-to docx file.doc`).",
    "xls":  "Legacy .xls not supported. Convert to .xlsx (open & save-as).",
    "ppt":  "PowerPoint .ppt not supported (extract slides as images first).",
    "pptx": "PowerPoint .pptx not yet implemented; extract slides as images.",
}


def supported_extensions() -> list[str]:
    return sorted(_EXTRACTORS.keys())


def extract(path: str | Path) -> ExtractedContent:
    """Dispatch to the right extractor based on file suffix."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    suffix = p.suffix.lower().lstrip(".")
    if suffix in _EXTRACTORS:
        return _EXTRACTORS[suffix](p)
    if suffix in _UNSUPPORTED_HINT:
        raise ValueError(_UNSUPPORTED_HINT[suffix])
    raise ValueError(
        f"Unsupported file type: .{suffix} (supported: {', '.join(supported_extensions())})"
    )


def extract_bytes(data: bytes, suffix: str) -> ExtractedContent:
    """
    Extract from in-memory bytes (e.g. an uploaded file in a web handler).

    Writes to a temp file because most underlying parsers want a file path.
    """
    import tempfile
    suffix = suffix.lstrip(".").lower()
    with tempfile.NamedTemporaryFile(suffix=f".{suffix}", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        return extract(tmp_path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["extract", "extract_bytes", "supported_extensions"]
