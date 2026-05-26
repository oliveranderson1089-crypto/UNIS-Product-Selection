"""
Feature extraction for layer / PoE / rack units from brochure body text.

Same philosophy as `port_patterns.py`: pdfplumber tables are unreliable on
these PDFs, but the body text is. For each field we accept multiple
synonymous phrasings, normalize, and refuse to commit a value when the
signal is too weak (return None instead of guessing).

Detection strategies per field:

    layer        — Any L3 routing protocol mention (OSPF/BGP/IS-IS/RIP/VRRP)
                   OR explicit "三层"/"L3"/"Layer 3"  →  L3.
                   Otherwise "二层"/"L2"/"Layer 2"     →  L2.
                   Else None.

    poe          — Explicit negative ("不支持POE","无POE")               →  False.
                   "POE+", "支持POE", "POE/POE+", "POE供电/端口",
                   IEEE 802.3af/at                                       →  True.
                   Else None (don't infer absence — many DC switches
                   simply don't mention PoE at all because it's irrelevant).

    rack_units   — Direct match on "<digit>U" near 外形尺寸/高度/机箱     → N.
                   Otherwise read the third dimension out of "WxDxH" mm
                   and bucket-convert (44mm→1U, 87mm→2U, …).
                   Else None.
"""

from __future__ import annotations

import re


_NORMALIZE_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Collapse all whitespace so PDF line breaks don't split a regex match."""
    return _NORMALIZE_WS.sub(" ", text)


# ---------------------------------------------------------------------------
# Layer
# ---------------------------------------------------------------------------
# Order matters within each layer's pattern list — first hit wins, and we
# check L3 indicators before L2 because L3 switches CAN do L2 (the inverse
# isn't true, so an "L3 only" marker is more informative than an "L2" marker).
_L3_PATTERNS = [
    re.compile(r"三层(?:交换|路由|转发)"),
    re.compile(r"\bL3\b|Layer\s*3", re.IGNORECASE),
    # Routing protocols → product offers L3 routing
    re.compile(r"\b(?:OSPF|BGP|IS-IS|ISIS|RIPv?2?|VRRP|EIGRP|MPLS|PIM)\b",
               re.IGNORECASE),
    re.compile(r"路由协议|动态路由|静态路由"),
]
_L2_PATTERNS = [
    re.compile(r"二层(?:交换|转发)?"),
    re.compile(r"\bL2\b|Layer\s*2", re.IGNORECASE),
]


def extract_layer(text: str) -> str | None:
    normalized = _normalize(text)
    if any(p.search(normalized) for p in _L3_PATTERNS):
        return "L3"
    if any(p.search(normalized) for p in _L2_PATTERNS):
        return "L2"
    return None


# ---------------------------------------------------------------------------
# PoE
# ---------------------------------------------------------------------------
_POE_NEG = re.compile(r"不支持\s*P[Oo][Ee]|无\s*P[Oo][Ee]|无\s*供电")
_POE_POS = re.compile(
    r"P[Oo][Ee]\+"                       # PoE+ — strongest signal
    r"|支持\s*P[Oo][Ee]"                  # 支持POE
    r"|P[Oo][Ee]\s*[/／]\s*P[Oo][Ee]"     # POE/POE+
    r"|P[Oo][Ee]\s*(?:供电|端口|接口|供\s*电)"  # POE 供电 / POE 端口
    r"|802\.3a[ft]\b",                   # 802.3af / 802.3at
    re.IGNORECASE,
)


def extract_poe(text: str) -> bool | None:
    normalized = _normalize(text)
    if _POE_NEG.search(normalized):
        return False
    if _POE_POS.search(normalized):
        return True
    return None


# ---------------------------------------------------------------------------
# Rack units
# ---------------------------------------------------------------------------
# Direct: "高度 1U", "外形尺寸 ... 1U", "1U 机箱".
_RU_DIRECT = [
    re.compile(r"(?:外形尺寸|机箱高度|高度|尺寸)[^,。;:\n]{0,40}?(\d{1,2})\s*U\b",
               re.IGNORECASE),
    re.compile(r"(\d{1,2})\s*U\s*(?:高度|机箱)", re.IGNORECASE),
]

# Dimensions WxDxH (mm) → bucket by 44mm steps.
_RU_DIMENSIONS = re.compile(
    r"\d{3,4}\s*[×xX*]\s*\d{3,4}\s*[×xX*]\s*(\d{2,3})\b"
)


def _height_mm_to_units(mm: int) -> int | None:
    """Bucket a height in mm to standard rack-unit count."""
    if mm <= 0:
        return None
    # Standard rack unit ≈ 44.45mm. Allow ±5mm tolerance per unit.
    for u in range(1, 17):
        center = round(u * 44.45)
        if abs(mm - center) <= 5:
            return u
    return None


def extract_rack_units(text: str) -> int | None:
    normalized = _normalize(text)

    for pat in _RU_DIRECT:
        for m in pat.finditer(normalized):
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            if 1 <= n <= 16:
                return n

    for m in _RU_DIMENSIONS.finditer(normalized):
        try:
            mm = int(m.group(1))
        except ValueError:
            continue
        units = _height_mm_to_units(mm)
        if units is not None:
            return units

    return None


__all__ = ["extract_layer", "extract_poe", "extract_rack_units"]
