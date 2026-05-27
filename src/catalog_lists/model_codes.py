"""
Extract UNIS product model codes from arbitrary text.

The extractor stage (Claude or OCR) gives us either:
  - A JSON array `[{"model": "UNIS S5800-X-EI-G", ...}, ...]`
  - Or raw text dumped from OCR

This module handles both:
  1. If JSON parses, take the "model" fields.
  2. Otherwise (or as a supplement), regex-scan the text for UNIS codes.

We also `normalize()` codes so "UNIS-S5800" and "UNIS S5800" collapse to
the same canonical form for matching against the product catalog.
"""

from __future__ import annotations

import json
import re

# UNIS model codes follow patterns like:
#   UNIS S12600-CR-G         (switches: S-prefix + digits + suffix)
#   UNIS-S5800X-EI-G         (with hyphen, suffix codes)
#   UNIS Server R3810 G5     (servers: "Server" + R-prefix)
#   UNIS R7900-08-M          (routers)
#   UNIS F5000-M             (firewalls: F-prefix)
#   UNIS IE4500-G2           (industrial ethernet)
#   UNIS UIS                 (cloud platform)
#
# We accept any token starting with UNIS (optionally with space/hyphen and
# "Server"/"Storage") followed by an alphanumeric model designation.
MODEL_PATTERNS = [
    # UNIS [Server|Storage] <prefix><digits>[<suffix>] [<gen>]
    re.compile(
        r"\bUNIS[\s\-]*(?:Server|Storage)?[\s\-]*"
        r"[A-Z]{1,3}\d{2,5}[A-Z0-9\-]*"
        r"(?:\s+G\d)?\b",
        re.IGNORECASE,
    ),
]


def normalize(code: str) -> str:
    """
    Canonicalize a model code for comparison.

    Lowercase, strip whitespace + hyphens between tokens, so
    "UNIS S5800X-EI-G", "UNIS-S5800X-EI-G", "unis s5800xeig" all
    collapse to "uniss5800xeig".
    """
    return re.sub(r"[\s\-_/]+", "", code.lower())


def extract_from_text(text: str) -> list[str]:
    """
    Regex-scan free text for UNIS model codes.

    Returns DEDUPLICATED, preserving first-occurrence order. Whitespace
    inside a match is collapsed to a single space ("UNIS  S5800" → "UNIS S5800").
    """
    seen: set[str] = set()
    out: list[str] = []
    for pat in MODEL_PATTERNS:
        for m in pat.finditer(text):
            raw = re.sub(r"\s+", " ", m.group(0).strip())
            key = normalize(raw)
            if key in seen:
                continue
            seen.add(key)
            out.append(raw)
    return out


def extract_from_extractor_output(payload: str) -> list[str]:
    """
    Combined extractor — tries JSON first (Claude output), falls back to
    free-text regex (OCR output, or Claude that didn't comply).

    The JSON path produces cleaner results because Claude already
    distinguishes product rows from page furniture (titles, footers,
    stamps). The regex path is a safety net.
    """
    codes: list[str] = []
    seen: set[str] = set()

    # ---- 1) JSON path ----
    payload_strip = payload.strip()
    if payload_strip.startswith("[") and payload_strip.endswith("]"):
        try:
            data = json.loads(payload_strip)
        except Exception:                                             # noqa: BLE001
            data = None
        if isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                code = entry.get("model")
                if not code or not isinstance(code, str):
                    # Some entries are {"_raw": "..."} fallbacks — scan them.
                    raw_text = entry.get("_raw")
                    if raw_text:
                        for c in extract_from_text(raw_text):
                            key = normalize(c)
                            if key not in seen:
                                seen.add(key); codes.append(c)
                    continue
                key = normalize(code)
                if key not in seen:
                    seen.add(key); codes.append(code.strip())

    # ---- 2) Regex path: also scan the raw payload to catch anything the
    # JSON missed (Claude occasionally drops trailing rows).
    for c in extract_from_text(payload):
        key = normalize(c)
        if key not in seen:
            seen.add(key); codes.append(c)

    return codes


__all__ = ["normalize", "extract_from_text", "extract_from_extractor_output"]
