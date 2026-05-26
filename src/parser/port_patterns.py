"""
Port count + port speed extraction from brochure body text.

Why not table extraction?  pdfplumber tables for UNIS 彩页 are heavily
fragmented (cells split across multiple rows, headers misaligned). The
SAME data is stated cleanly in the body text in patterns like:

    "S5800X-54C-EI-G:48 个 10/100/1000BASE-T 端口,4 个 Combo 端口,
     8 个 10G/1G BASE-X SFP+端口"

    "单机最大可以提供 3072 个线速 10G/25G 端口或者 768 个线速
     40G/100G/400G端口"

This module recognizes "<digits> 个 <port-type-keywords>" patterns and
aggregates across all SKUs mentioned in a single brochure into:

    port_count   = max access port count seen
    port_speed   = highest non-uplink speed seen
    uplink_speed = highest uplink/extension speed seen
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# Order matters: higher-speed patterns first so 400G beats a stray "40G" match.
# Each pattern captures the port count; the speed label and a "is_uplink" flag
# are carried alongside.
@dataclass(frozen=True)
class PortPattern:
    pattern: re.Pattern[str]
    speed: str          # canonical label, must match selector.SPEED_RANK
    is_uplink: bool     # True if this port type is typically uplink/extension
                        # (so we don't conflate it with main access ports)


# Use Unicode hex escapes so this file stays safe to grep/edit in any editor.
# `\s*` after `\d+` swallows both regular spaces and PDF line-break artifacts.
_RE_FLAGS = re.IGNORECASE
_NEAR = r"[^\n]{0,30}?"   # short look-ahead window between count and unit

PORT_PATTERNS: list[PortPattern] = [
    # ---- 400G / 200G (uplink-like; only on top-of-rack/chassis) ----
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}400G", _RE_FLAGS), "400G", True),
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}200G", _RE_FLAGS), "200G", True),

    # ---- 100G (uplink for ToR, access for DC core) ----
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}(?:QSFP28|100G(?:E|\b))", _RE_FLAGS),
                "100G", True),

    # ---- 40G ----
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}(?:QSFP\+|40G(?:E|\b))", _RE_FLAGS),
                "40G", True),

    # ---- 25G ----
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}25G(?:E|\b)", _RE_FLAGS), "25G", True),

    # ---- 10G (SFP+ is usually uplink on access switches; 10GBASE-T is access on others) ----
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}(?:SFP\+|10GBASE-T|10G(?:E|\b)|万兆)",
                           _RE_FLAGS), "10G", True),
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}10G/1G\s*BASE", _RE_FLAGS), "10G", True),

    # ---- 2.5G ----
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}2\.5G(?:E|\b)", _RE_FLAGS), "2.5G", False),

    # ---- 1G — the COMMON access type on enterprise switches ----
    # "10/100/1000BASE-T" is the canonical phrase, "千兆" is the marketing term.
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}(?:10/100/1000\s*BASE|千兆|GE\b|1000BASE)",
                           _RE_FLAGS), "1G", False),

    # ---- 100M ----
    PortPattern(re.compile(rf"(\d+)\s*个{_NEAR}(?:10/100\s*BASE|百兆|100BASE-T)",
                           _RE_FLAGS), "100M", False),
]


# Speed ordering for "highest" reductions.
_SPEED_RANK = {
    "100M": 0, "1G": 1, "2.5G": 2, "10G": 3, "25G": 4,
    "40G": 5, "100G": 6, "200G": 7, "400G": 8,
}


@dataclass
class PortFindings:
    port_count: int | None = None       # max access port count
    port_speed: str | None = None       # highest access speed
    uplink_speed: str | None = None     # highest uplink speed (40G/100G/400G…)


def extract_port_specs(text: str) -> PortFindings:
    """
    Return aggregated port specs across all mentions in `text`.

    Strategy:
      - Normalize whitespace so PDF line breaks don't split patterns.
      - For each PortPattern, find every count.
      - For each (count, speed, is_uplink) tuple, group:
          * access_speeds: not uplink
          * uplink_speeds: uplink
      - port_count = max count seen on access ports (fallback: max overall).
      - port_speed = highest access speed.
      - uplink_speed = highest uplink speed.

    No matches → all None (caller decides whether to fall back).
    """
    # Collapse all whitespace runs to a single space so cross-line patterns work.
    normalized = re.sub(r"\s+", " ", text)

    access_counts: list[int] = []
    access_speeds: list[str] = []
    uplink_counts: list[int] = []
    uplink_speeds: list[str] = []

    for spec in PORT_PATTERNS:
        for m in spec.pattern.finditer(normalized):
            try:
                count = int(m.group(1))
            except (TypeError, ValueError):
                continue
            # Sanity bound — anything > 8192 is almost certainly a number
            # from a different context (year, frequency, etc.).
            if count <= 0 or count > 8192:
                continue
            if spec.is_uplink:
                uplink_counts.append(count)
                uplink_speeds.append(spec.speed)
            else:
                access_counts.append(count)
                access_speeds.append(spec.speed)

    def _highest(speeds: list[str]) -> str | None:
        if not speeds:
            return None
        return max(speeds, key=lambda s: _SPEED_RANK.get(s, -1))

    findings = PortFindings()

    # Prefer access ports for "primary" port_count + port_speed because that's
    # what buyers typically care about ("48 口千兆 access switch"). Fall back to
    # uplink figures if no access ports were found (some pure-uplink ToR boxes).
    if access_counts:
        findings.port_count = max(access_counts)
        findings.port_speed = _highest(access_speeds)
    elif uplink_counts:
        findings.port_count = max(uplink_counts)
        findings.port_speed = _highest(uplink_speeds)

    findings.uplink_speed = _highest(uplink_speeds)
    return findings


__all__ = ["PortFindings", "PortPattern", "PORT_PATTERNS", "extract_port_specs"]
