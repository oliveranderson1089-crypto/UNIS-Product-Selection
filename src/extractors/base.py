"""Shared types for document/image extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExtractedTable:
    """A 2D table pulled from a document. First row is treated as header."""

    rows: list[list[str]]                # including header row at index 0
    page: int | None = None              # source page (PDF/Word)
    sheet: str | None = None             # source sheet (Excel)

    @property
    def header(self) -> list[str]:
        return self.rows[0] if self.rows else []

    @property
    def body(self) -> list[list[str]]:
        return self.rows[1:] if len(self.rows) > 1 else []

    def to_markdown(self) -> str:
        if not self.rows:
            return ""
        header = "| " + " | ".join(self.header) + " |"
        sep = "| " + " | ".join("---" for _ in self.header) + " |"
        body = "\n".join("| " + " | ".join(r) + " |" for r in self.body)
        return f"{header}\n{sep}\n{body}"


@dataclass
class ExtractedContent:
    """
    Normalized output of any extractor.

    `text` is plain-text concatenation; `tables` keeps structure so downstream
    parsers can look for spec keys ("端口数 | 48") with high precision.
    """

    source: Path | str
    kind: str                            # "pdf" | "docx" | "xlsx" | "txt" | "image"
    text: str = ""
    tables: list[ExtractedTable] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.text) or bool(self.tables)

    def to_prompt(self, max_chars: int = 8000) -> str:
        """
        Render a compact representation suitable for feeding to an LLM.

        Tables are rendered as markdown so the model can read them as data;
        free text is appended after.
        """
        parts: list[str] = []
        for i, t in enumerate(self.tables):
            label = f"[Table {i+1}"
            if t.page is not None:
                label += f", page {t.page}"
            if t.sheet:
                label += f", sheet {t.sheet}"
            label += "]"
            parts.append(f"{label}\n{t.to_markdown()}")
        if self.text:
            parts.append(self.text)
        joined = "\n\n".join(parts)
        return joined if len(joined) <= max_chars else joined[:max_chars] + "\n...[truncated]"


__all__ = ["ExtractedContent", "ExtractedTable"]
