"""
Brochure → product specs.

Pipeline:
1. Use the generic PDF extractor (`src.extractors.pdf`) to get text + tables.
2. Apply Chinese-language spec heuristics keyed off common 彩页 vocabulary
   ("端口数量", "交换容量", "包转发率", "层级", "外形尺寸", "电源"…).
3. Write back to the Product row in SQLite.
4. Anything we couldn't categorize lands in `extra_specs` (JSON).

This file is intentionally heuristic — UNIS brochures are not standardized.
When you find a recurring extraction miss, add a pattern in `SPEC_RULES`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select

from ..extractors.base import ExtractedContent
from ..extractors.pdf import extract_pdf
from ..storage import get_db
from ..storage.models import Product, ProductPDF
from .port_patterns import extract_port_specs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Spec regex rules. (Each rule maps to one column on Product OR to a key in
# extra_specs.) The first rule that fires wins.
# ---------------------------------------------------------------------------

@dataclass
class SpecRule:
    column: str | None           # None → land in extra_specs
    pattern: re.Pattern[str]
    cast: callable                # str -> typed value
    description: str = ""


def _to_int(s: str) -> int:        return int(re.sub(r"[^\d-]", "", s) or "0")
def _to_float(s: str) -> float:    return float(re.sub(r"[^\d\.\-]", "", s) or "0")
def _to_bool(s: str) -> bool:      return s.strip().lower() in ("yes", "true", "支持", "有", "1")
def _identity(s: str) -> str:      return s.strip()


# Casters MAY return None to signal "I refuse this match — discard it".
# This is the key sanity check: if the matched text doesn't normalize to a
# value in a known set, we don't store garbage. Without this, a too-loose
# regex captures arbitrary prose and pollutes the catalog.

def _to_int(s: str) -> int | None:
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def _to_float(s: str) -> float | None:
    cleaned = re.sub(r"[^\d\.]", "", s)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


_VALID_SPEEDS = {"100M", "1G", "2.5G", "10G", "25G", "40G", "100G", "200G", "400G"}
_VALID_LAYERS = {"L2", "L3"}


def _to_speed_label(s: str) -> str | None:
    """
    Normalize a free-form speed string to one of the values in _VALID_SPEEDS.
    Returns None if no recognized token is present — the matcher then drops
    the field instead of storing garbage like "百分比的风扇调速".
    """
    s = s.lower()
    if "400g" in s or "400 g" in s:           return "400G"
    if "200g" in s or "200 g" in s:           return "200G"
    if "100g" in s or "100 g" in s:           return "100G"
    if "40g"  in s or "40 g"  in s:           return "40G"
    if "25g"  in s or "25 g"  in s:           return "25G"
    if "10g"  in s or "万兆" in s:             return "10G"
    if "2.5g" in s:                            return "2.5G"
    if re.search(r"\b1g\b|千兆|gigabit|\bge\b", s): return "1G"
    if "100m" in s or "百兆" in s:             return "100M"
    return None


def _to_layer(s: str) -> str | None:
    s = s.lower()
    if "三层" in s or re.search(r"\bl3\b|layer\s*3", s):  return "L3"
    if "二层" in s or re.search(r"\bl2\b|layer\s*2", s):  return "L2"
    return None


def _to_poe(s: str) -> bool | None:
    if re.search(r"不支持|无 ?poe|none|无供电", s, re.I): return False
    if re.search(r"poe\+?|支持poe|802\.3a[tf]", s, re.I): return True
    return None


def _to_redundant(s: str) -> bool | None:
    if re.search(r"(冗余|双电源|1\+1|2\+1|N\+1)", s): return True
    if re.search(r"单电源|无冗余", s):                 return False
    return None


SPEC_RULES: list[SpecRule] = [
    # ---- port count: must be a 1-4 digit number IMMEDIATELY after the label,
    # tolerating only whitespace/colons between. Prevents matches across
    # arbitrary text.
    SpecRule("port_count",
             re.compile(r"(?:端口数量|端口数|端口总数|总端口数)\s*[:：]?\s*(\d{1,4})\b"),
             _to_int, "总端口数"),

    # ---- port_speed: cap window to 20 chars, value must contain a unit token
    # (G/M/兆) AND normalize to a known label.
    SpecRule("port_speed",
             re.compile(r"(?:端口速率|端口速度|主端口速率|access port|端口类型)"
                        r"\s*[:：]?\s*([^\n\r]{0,30}?(?:G|M|兆)[^\n\r]{0,10})", re.I),
             _to_speed_label, "端口速率"),

    SpecRule("uplink_speed",
             re.compile(r"(?:上行端口|uplink)"
                        r"\s*[:：]?\s*([^\n\r]{0,30}?(?:G|M|兆)[^\n\r]{0,10})", re.I),
             _to_speed_label, "上行端口速率"),

    # Switching capacity / forwarding rate use a 2-group capture; the cast
    # for these is handled specially in _apply_rule (kept as 0 placeholder).
    SpecRule("switching_capacity_gbps",
             re.compile(r"(?:交换容量|switching\s*capacity)"
                        r"\s*[:：]?\s*([\d\.]+)\s*([TGtg])bps?", re.I),
             lambda raw: 0, "交换容量(Gbps)"),

    SpecRule("forwarding_rate_mpps",
             re.compile(r"(?:包转发率|forwarding\s*rate)"
                        r"\s*[:：]?\s*([\d\.]+)\s*(M|Mpps|mpps)", re.I),
             lambda raw: 0, "包转发率(Mpps)"),

    # ---- layer: value must contain a layer keyword right after the label.
    SpecRule("layer",
             re.compile(r"(?:层级|协议层级|工作模式|交换层级)"
                        r"\s*[:：]?\s*([^\n\r]{0,15}?(?:层|L\d|layer))", re.I),
             _to_layer, "层级"),

    SpecRule("poe",
             re.compile(r"(?:PoE|PoE\+|供电方式)\s*[:：]?\s*([^\n\r]{0,30})", re.I),
             _to_poe, "PoE 支持"),

    SpecRule("redundant_power",
             re.compile(r"(?:电源|供电)\s*[:：]?\s*([^\n\r]{0,40})", re.I),
             _to_redundant, "冗余电源"),

    SpecRule("rack_units",
             re.compile(r"(?:外形尺寸|机箱高度|高度)[^\n\r]{0,20}?(\d)\s*U", re.I),
             _to_int, "U 数"),
]


@dataclass
class ParseStats:
    parsed: int = 0
    failed: int = 0
    skipped: int = 0
    fields_set: int = 0


@dataclass
class BrochureParser:
    """One brochure (PDF) → spec dict ready for `Database.upsert_product`."""

    def parse_file(self, pdf_path: Path) -> dict[str, Any]:
        content = extract_pdf(pdf_path)
        return self.parse_content(content)

    def parse_content(self, content: ExtractedContent) -> dict[str, Any]:
        # Flatten tables into the same text body so regex can hit either source.
        haystack = self._build_haystack(content)
        result: dict[str, Any] = {}
        extras: dict[str, Any] = {}

        # ---- 1) port specs via the aggregating PortPattern extractor --------
        # UNIS brochures describe ports in prose ("48 个 10/100/1000BASE-T 端口")
        # rather than in clean key:value tables, so a multi-pattern aggregator
        # works far better than a single regex on "端口数:".
        ports = extract_port_specs(haystack)
        if ports.port_count is not None:
            result["port_count"] = ports.port_count
        if ports.port_speed is not None:
            result["port_speed"] = ports.port_speed
        if ports.uplink_speed is not None:
            result["uplink_speed"] = ports.uplink_speed

        # ---- 2) every other spec via the rule set ---------------------------
        for rule in SPEC_RULES:
            if rule.column in result:                              # already filled by step 1
                continue
            # Try every occurrence, not just the first. PDF brochures often
            # mention a keyword once in marketing prose (no usable value) and
            # again in a spec table (clean value). `finditer` keeps trying
            # until a match yields a value the caster accepts.
            value = None
            for m in rule.pattern.finditer(haystack):
                try:
                    candidate = self._apply_rule(rule, m)
                except Exception as exc:                          # noqa: BLE001
                    logger.debug("Rule %s failed on match %r: %s",
                                 rule.description, m.group(0), exc)
                    continue
                if candidate is None:                              # caster rejected
                    continue
                value = candidate
                break

            if value is None:
                continue
            if rule.column:
                if rule.column not in result:
                    result[rule.column] = value
            else:
                extras[rule.description or m.group(0)[:32]] = value

        if extras:
            result["extra_specs"] = extras
        return result

    # ---- internals ----------------------------------------------------------
    @staticmethod
    def _build_haystack(content: ExtractedContent) -> str:
        parts: list[str] = [content.text]
        for t in content.tables:
            for row in t.rows:
                parts.append(" | ".join(row))
        return "\n".join(parts)

    @staticmethod
    def _apply_rule(rule: SpecRule, match: re.Match[str]) -> Any:
        if rule.column == "switching_capacity_gbps":
            val, unit = float(match.group(1)), match.group(2).lower()
            return val * 1000.0 if unit == "t" else val
        if rule.column == "forwarding_rate_mpps":
            return float(match.group(1))
        return rule.cast(match.group(1))


# ---------------------------------------------------------------------------
def parse_all_pending() -> ParseStats:
    """
    Walk every PDF the downloader has stored and (re)parse it into specs.
    Idempotent — running again just overwrites with the latest extraction.
    """
    db = get_db()
    stats = ParseStats()
    parser = BrochureParser()

    with db.session() as s:
        # Join Product + ProductPDF so we know which product owns each PDF.
        pdfs = list(s.scalars(select(ProductPDF)))
        product_by_id = {
            p.id: p for p in s.scalars(select(Product).where(Product.id.in_([x.product_id for x in pdfs])))
        }

    for pdf in pdfs:
        if not pdf.local_path or not Path(pdf.local_path).exists():
            stats.skipped += 1
            continue
        try:
            specs = parser.parse_file(Path(pdf.local_path))
        except Exception as exc:                                  # noqa: BLE001
            logger.warning("Parse failed for %s: %s", pdf.local_path, exc)
            stats.failed += 1
            continue
        if not specs:
            stats.skipped += 1
            continue

        product = product_by_id.get(pdf.product_id)
        if product is None:
            continue
        payload = {"model": product.model, **specs}
        db.upsert_product(payload)
        stats.parsed += 1
        stats.fields_set += len(specs)

    return stats


__all__ = ["BrochureParser", "SpecRule", "parse_all_pending", "ParseStats"]
