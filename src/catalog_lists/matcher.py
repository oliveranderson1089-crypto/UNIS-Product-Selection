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
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage.models import Product
from .model_codes import normalize

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    raw_code: str
    product_id: int | None
    product_model: str | None
    method: str   # "exact" | "normalized" | "fuzzy" | "none"


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

        # Fuzzy: substring on normalized. The 名录 code might be a series
        # name (no suffix) and we want it to map to the canonical product;
        # OR vice versa. Try both directions.
        fuzzy_hits = sorted(
            (p for p in products
             if norm and (norm in normalize(p.model) or normalize(p.model) in norm)),
            key=lambda p: p.id,
        )
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
