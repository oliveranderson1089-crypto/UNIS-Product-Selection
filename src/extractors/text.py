"""Plain-text / Markdown / CSV extractor with encoding sniffing."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import chardet

from .base import ExtractedContent, ExtractedTable


def _detect_encoding(path: Path) -> str:
    raw = path.read_bytes()[:65536]
    guess = chardet.detect(raw)
    enc = guess.get("encoding") or "utf-8"
    # chardet sometimes returns ascii confidently for UTF-8 with no non-ASCII
    # in its sample window — UTF-8 is a strict superset of ASCII so this is safe.
    return enc if enc.lower() != "ascii" else "utf-8"


def extract_text(path: Path) -> ExtractedContent:
    """Read .txt / .md / .csv and normalize."""
    enc = _detect_encoding(path)
    raw = path.read_text(encoding=enc, errors="replace")

    if path.suffix.lower() == ".csv":
        reader = csv.reader(io.StringIO(raw))
        rows = [[c.strip() for c in row] for row in reader if any(c.strip() for c in row)]
        tables = [ExtractedTable(rows=rows)] if len(rows) >= 2 else []
        return ExtractedContent(
            source=path, kind="csv", text=raw, tables=tables, meta={"encoding": enc},
        )

    return ExtractedContent(
        source=path,
        kind="txt" if path.suffix.lower() != ".md" else "md",
        text=raw,
        meta={"encoding": enc},
    )


__all__ = ["extract_text"]
