"""
Image "extractor".

Images are not converted to text here — we keep the raw path so the LLM
router can hand them to a vision-capable provider (Claude) verbatim. The
returned `ExtractedContent` carries the path in `meta["image_path"]`.
"""

from __future__ import annotations

from pathlib import Path

from .base import ExtractedContent


def extract_image(path: Path) -> ExtractedContent:
    if not path.exists():
        raise FileNotFoundError(path)
    return ExtractedContent(
        source=path,
        kind="image",
        text=f"[Image input: {path.name}]",   # placeholder for text-only consumers
        meta={"image_path": str(path), "size_bytes": path.stat().st_size},
    )


__all__ = ["extract_image"]
