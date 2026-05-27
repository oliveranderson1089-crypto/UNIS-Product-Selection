"""
Match a raw model code from a 名录 file to a Product row in our catalog.

The product catalog uses canonical UNIS spellings (from URL slugs), but
名录 sources frequently differ in spacing/punctuation:

    名录 says:    "UNIS S5800-X-EI-G"
    我们的库里:    "UNIS-S5800X-EI-G"   ← same product, different spelling

This module canonicalizes both sides for matching and reports which
strategy succeeded (exact / normalized / fuzzy / none).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage.models import Product
from .model_codes import normalize

logger = logging.getLogger(__name__)

# Fuzzy matching only fires when BOTH normalized strings have at least
# this many characters. Without a floor, short product slugs like "UNIS"
# (4 chars) become wildcards that match every other UNIS product as a
# substring. 8 chars is long enough to include the model number digits
# that actually identify a product family.
MIN_FUZZY_LEN = 8

# Port-count tokens in 名录 SKU codes that the series-level URL slug omits.
# e.g.  "UNIS S5800-56T-EI-G"   →   strip "56T"  →  "UNIS S5800-EI-G"
#       "UNIS S6600XP-54XG-EI-G" → strip "54XG" → "UNIS S6600XP-EI-G"
#       "UNIS S12600-08-G"      →   strip "08"  → "UNIS S12600-G"
# Pattern: a hyphen-bounded token that's <digits><optional letters> AND
# is followed by another hyphen (so we only strip MIDDLE tokens, never
# the trailing "-G" / "-EI-G" suffix).
_PORT_COUNT_TOKEN = re.compile(r"-(\d+[A-Z]*)(?=-)")


def strip_port_count(code: str) -> str:
    """Drop the inline port-count token so a SKU collapses to its series."""
    return _PORT_COUNT_TOKEN.sub("", code)


@dataclass
class MatchResult:
    raw_code: str
    product_id: int | None
    product_model: str | None
    method: str   # "exact" | "normalized" | "series" | "fuzzy" | "none"


def match_codes_to_products(
    session: Session,
    raw_codes: list[str],
) -> list[MatchResult]:
    """
    Match a batch of 名录 model codes against the product table.

    Strategy (per code):
      1. EXACT match on Product.model
      2. NORMALIZED match (lowercase + strip separators)
      3. FUZZY: normalized PREFIX match — code starts-with or contained-in
         the product's normalized model. Catches "UNIS S5800" → all S5800
         family products.

    A code matching multiple products via fuzzy is recorded as the FIRST
    hit (deterministic — sorted by id) and we log a warning so users can
    decide whether to clean up the source code.
    """
    products = list(session.scalars(select(Product)))
    # Build index for fast lookup
    by_exact = {p.model: p for p in products}
    by_norm = {normalize(p.model): p for p in products}

    out: list[MatchResult] = []
    for raw in raw_codes:
        if raw in by_exact:
            p = by_exact[raw]
            out.append(MatchResult(raw, p.id, p.model, "exact"))
            continue

        norm = normalize(raw)
        if norm in by_norm:
            p = by_norm[norm]
            out.append(MatchResult(raw, p.id, p.model, "normalized"))
            continue

        # SERIES match: 名录 codes often spell out the port-count variant
        # (UNIS S5800-56T-EI-G) while our catalog only carries the SERIES
        # page (UNIS-S5800-EI-G). Strip the inline port-count token and
        # re-try the normalized lookup before falling through to fuzzy.
        stripped = strip_port_count(raw)
        if stripped != raw:
            stripped_norm = normalize(stripped)
            if stripped_norm in by_norm:
                p = by_norm[stripped_norm]
                out.append(MatchResult(raw, p.id, p.model, "series"))
                continue

        # Fuzzy: substring on normalized. The 名录 code might be a series
        # name (no suffix) and we want it to map to the canonical product;
        # OR vice versa. Try both directions.
        #
        # IMPORTANT: skip products whose normalized model is shorter than
        # MIN_FUZZY_LEN — they're too generic (e.g. "UNIS" = 4 chars) and
        # would otherwise wildcard-match every other UNIS code in the
        # catalog as a substring.
        fuzzy_hits: list[Product] = []
        if norm and len(norm) >= MIN_FUZZY_LEN:
            for p in products:
                p_norm = normalize(p.model)
                if len(p_norm) < MIN_FUZZY_LEN:
                    continue
                if norm in p_norm or p_norm in norm:
                    fuzzy_hits.append(p)
            fuzzy_hits.sort(key=lambda p: p.id)
        if fuzzy_hits:
            if len(fuzzy_hits) > 1:
                logger.info(
                    "Fuzzy match for %r resolved to %d candidates; using %s",
                    raw, len(fuzzy_hits), fuzzy_hits[0].model,
                )
            p = fuzzy_hits[0]
            out.append(MatchResult(raw, p.id, p.model, "fuzzy"))
            continue

        out.append(MatchResult(raw, None, None, "none"))

    return out


__all__ = ["MatchResult", "match_codes_to_products"]
