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


def _to_speed_label(s: str) -> str:
    s = s.lower()
    if "100g" in s or "100 g" in s: return "100G"
    if "40g"  in s or "40 g"  in s: return "40G"
    if "25g"  in s or "25 g"  in s: return "25G"
    if "万兆" in s or "10g" in s:    return "10G"
    if "2.5g" in s:                  return "2.5G"
    if "千兆" in s or "1g" in s or "ge" in s: return "1G"
    return s.strip()


def _to_layer(s: str) -> str:
    s = s.lower()
    if "三层" in s or "l3" in s or "layer 3" in s: return "L3"
    if "二层" in s or "l2" in s or "layer 2" in s: return "L2"
    return s.strip().upper()


SPEC_RULES: list[SpecRule] = [
    SpecRule("port_count",
             re.compile(r"(?:端口数量|端口数|端口总数)[\s:\：]*([\d]{1,4})"),
             _to_int, "总端口数"),

    SpecRule("port_speed",
             re.compile(r"(?:端口速率|端口速度|主端口速率|access port)[\s:\：]*([^\n\r]{1,40})"),
             _to_speed_label, "端口速率"),

    SpecRule("uplink_speed",
             re.compile(r"(?:上行端口|uplink)[\s:\：]*([^\n\r]{1,40})"),
             _to_speed_label, "上行端口速率"),

    SpecRule("switching_capacity_gbps",
             re.compile(r"(?:交换容量)[\s:\：]*([\d\.]+)\s*([TGtg])"),
             # capture both groups in cast via factory:
             lambda raw: 0,  # overridden in match loop below
             "交换容量(Gbps)"),

    SpecRule("forwarding_rate_mpps",
             re.compile(r"(?:包转发率)[\s:\：]*([\d\.]+)\s*(M|Mpps|mpps)"),
             lambda raw: 0,
             "包转发率(Mpps)"),

    SpecRule("layer",
             re.compile(r"(?:层级|协议层级|工作模式)[\s:\：]*([^\n\r]{1,30})"),
             _to_layer, "层级"),

    SpecRule("poe",
             re.compile(r"(?:PoE|PoE\+|供电方式)[\s:\：]*([^\n\r]{1,30})"),
             lambda s: not bool(re.search(r"不支持|无|none", s, re.I)),
             "PoE 支持"),

    SpecRule("redundant_power",
             re.compile(r"(?:电源|供电)[\s:\：]*([^\n\r]{1,40})"),
             lambda s: bool(re.search(r"(冗余|双电源|1\+1|2\+1)", s)),
             "冗余电源"),

    SpecRule("rack_units",
             re.compile(r"(?:外形尺寸|机箱高度|高度)[^\n\r]*?(\d)\s*U", re.I),
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

        for rule in SPEC_RULES:
            m = rule.pattern.search(haystack)
            if not m:
                continue
            try:
                value = self._apply_rule(rule, m)
            except Exception as exc:                              # noqa: BLE001
                logger.debug("Rule %s failed on match %r: %s", rule.description, m.group(0), exc)
                continue
            if rule.column:
                if rule.column not in result:                     # first wins
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
