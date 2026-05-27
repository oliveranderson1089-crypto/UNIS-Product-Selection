"""
Classify files inside a project folder by intent.

Categories (`ProjectFile.kind`):
  - "quote"        — H3C 配置器导出的报价单 (.xls / .xlsx with project-id prefix)
  - "requirement"  — 客户需求 / 标书 (含 需求/标书/投标/要求/参数)
  - "config"       — 配置模板 (含 配置/config/模板/template)
  - "image"        — 截图等图片
  - "other"        — 兜底

Plus `is_final` flag detected from 终版 / 最终 / final / 已选型 markers.

Purely filename-based — no file IO. The scanner uses this to tag files
as it discovers them so the UI can show "this project has 3 quotes
including a final version" at a glance.
"""

from __future__ import annotations

import re
from pathlib import Path


_KIND_RULES: list[tuple[str, list[str]]] = [
    # Order matters — first match wins. Put more specific rules first.
    ("quote",       ["报价", "quote", "_报价单_", "价格汇总"]),
    ("requirement", ["需求", "标书", "投标", "要求", "参数", "tender", "rfq", "rfp"]),
    ("config",      ["配置", "config", "模板", "template", "bom"]),
]

# H3C 配置器 exports use the convention `<id>-<desc>_<YYYYMMDD>.xls(x)`
# without the word "报价" anywhere. We treat any Excel with this date stamp
# as a quote — false positives are rare since users don't manually rename
# random spreadsheets with 8-digit date suffixes.
_QUOTE_DATESTAMPED = re.compile(r"[_\-]\d{8}\.xlsx?$", re.IGNORECASE)
_QUOTE_EXTS = {".xls", ".xlsx", ".xlsm"}

_FINAL_PATTERNS = re.compile(r"终版|最终版?|final|已选型|定稿|确认版", re.IGNORECASE)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


def classify_file(path: Path) -> tuple[str, bool]:
    """
    Return `(kind, is_final)` for one file.

    Decisions are based on filename alone — no parsing, no IO.
    """
    name_lower = path.name.lower()

    suffix = path.suffix.lower()

    # Image takes precedence — a "需求.png" is still an image (to be shown
    # as a thumbnail), not unpacked as a doc.
    if suffix in _IMAGE_EXTS:
        kind = "image"
    else:
        kind = "other"
        for k, kws in _KIND_RULES:
            if any(kw.lower() in name_lower for kw in kws):
                kind = k
                break
        # Fallback for unannotated quotes: H3C 配置器 file convention
        if kind == "other" and suffix in _QUOTE_EXTS and _QUOTE_DATESTAMPED.search(path.name):
            kind = "quote"

    is_final = bool(_FINAL_PATTERNS.search(path.name))
    return kind, is_final


__all__ = ["classify_file"]
