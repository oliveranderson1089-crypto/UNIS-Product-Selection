"""
Default selection engine: SQL pre-filter + per-field scoring.

Designed to be:
- **deterministic** — same input always returns the same ranking
- **explainable** — each candidate carries a list of reasons
- **forgiving** — if a product has NULL for a spec, it's not penalized
  (we don't punish products for incomplete data)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..requirement.schema import Requirement, RequirementField
from ..storage import get_db
from ..storage.models import Product
from .base import MatchResult, Matcher


# Per-field weights. The total possible score gets normalized to [0, 1]
# at the end so different requirements remain comparable.
FIELD_WEIGHTS: dict[str, float] = {
    "category":                10.0,    # category mismatch is almost always disqualifying
    "must_be_domestic":         6.0,
    "port_count":               4.0,
    "port_speed":               4.0,
    "layer":                    3.0,
    "switching_capacity_gbps":  2.0,
    "forwarding_rate_mpps":     2.0,
    "rack_units":               1.0,
    "poe":                      2.0,
    "redundant_power":          1.5,
    "budget_cny":               3.0,
}

# Speed string ordering — used to allow "≥" semantics on `port_speed`.
SPEED_RANK = {
    "100M": 0, "1G": 1, "2.5G": 2, "10G": 3, "25G": 4, "40G": 5, "100G": 6,
}


@dataclass
class RuleMatcher(Matcher):
    """No LLM, no network. Pure SQL + Python scoring.

    Optional scoping (set on the instance before calling .match):
      section       — "innovation" | "general" | None
      catalog_name  — restrict to products in a named CatalogList (政府名录…)
    """

    section: str | None = None
    catalog_name: str | None = None

    def match(self, requirement: Requirement, *, top_k: int = 5) -> list[MatchResult]:
        candidates = self._prefilter(requirement)
        results = [self._score(p, requirement) for p in candidates]
        # Drop candidates with score 0 (i.e. they failed a hard constraint)
        results = [r for r in results if r.score > 0]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    # ---- candidate retrieval -----------------------------------------------
    def _prefilter(self, req: Requirement) -> list[Product]:
        db = get_db()
        return db.find_products(
            section=self.section,
            catalog_name=self.catalog_name,
            category=req.category,
            min_port_count=req.port_count.min if req.port_count.is_set() else None,
            port_speed=req.port_speed.exact if req.port_speed.is_set() else None,
            layer=req.layer.exact if req.layer.is_set() else None,
            poe=req.poe.exact if req.poe.is_set() else None,
            is_domestic=True if req.must_be_domestic else None,
            max_price=req.budget_cny.max if req.budget_cny.is_set() else None,
            limit=500,
        )

    # ---- scoring -----------------------------------------------------------
    def _score(self, p: Product, req: Requirement) -> MatchResult:
        score = 0.0
        max_possible = 0.0
        reasons: list[str] = []
        warnings: list[str] = []

        # Category — hard requirement when user specified one.
        if req.category:
            max_possible += FIELD_WEIGHTS["category"]
            if p.category == req.category:
                score += FIELD_WEIGHTS["category"]
                reasons.append(f"类别匹配:{p.category}")
            else:
                # Disqualify entirely: don't let a router score against a
                # request for a switch.
                return MatchResult(product=p, score=0.0,
                                   reasons=[f"类别不匹配({p.category} vs {req.category})"])

        # Domestic constraint
        if req.must_be_domestic:
            max_possible += FIELD_WEIGHTS["must_be_domestic"]
            if p.is_domestic:
                score += FIELD_WEIGHTS["must_be_domestic"]
                reasons.append("满足国产化/自主可控要求")
            else:
                warnings.append("非国产化产品")

        # Numeric / enum fields.
        # `ok_msg` / `fail_msg` are CALLABLES so the f-strings inside them
        # are only evaluated when the field is actually set — this avoids
        # NoneType formatting errors when the user didn't specify a constraint.
        def consider(
            field_name: str,
            rf: RequirementField,
            product_value,
            ok_msg,
            fail_msg,
        ) -> None:
            nonlocal score, max_possible
            if not rf.is_set():
                return
            w = FIELD_WEIGHTS.get(field_name, 1.0)
            max_possible += w
            if product_value is None:
                warnings.append(f"产品未给出{field_name}")
                return
            if rf.matches(product_value):
                score += w
                reasons.append(ok_msg() if callable(ok_msg) else ok_msg)
            else:
                warnings.append(fail_msg() if callable(fail_msg) else fail_msg)

        consider("port_count", req.port_count, p.port_count,
                 ok_msg=lambda: f"端口数符合({p.port_count} ≥ {req.port_count.min})",
                 fail_msg=lambda: f"端口数不足({p.port_count} < {req.port_count.min})")

        # port_speed: use rank-based "≥" comparison so a 25G switch can
        # match a "10G or above" requirement.
        if req.port_speed.is_set() and req.port_speed.exact:
            w = FIELD_WEIGHTS["port_speed"]
            max_possible += w
            need_rank = SPEED_RANK.get(req.port_speed.exact, -1)
            have_rank = SPEED_RANK.get(p.port_speed or "", -1)
            if p.port_speed is None:
                warnings.append("产品未给出端口速率")
            elif have_rank >= need_rank >= 0:
                score += w
                msg = f"端口速率匹配({p.port_speed})"
                if have_rank > need_rank:
                    msg = f"端口速率超过需求({p.port_speed} ≥ {req.port_speed.exact})"
                reasons.append(msg)
            else:
                warnings.append(f"端口速率不足({p.port_speed} < {req.port_speed.exact})")

        consider("layer", req.layer, p.layer,
                 ok_msg=lambda: f"层级匹配({p.layer})",
                 fail_msg=lambda: f"层级不匹配({p.layer} vs {req.layer.exact})")

        consider("switching_capacity_gbps", req.switching_capacity_gbps, p.switching_capacity_gbps,
                 ok_msg=lambda: f"交换容量满足({p.switching_capacity_gbps} Gbps)",
                 fail_msg=lambda: f"交换容量不足({p.switching_capacity_gbps} Gbps)")

        consider("forwarding_rate_mpps", req.forwarding_rate_mpps, p.forwarding_rate_mpps,
                 ok_msg=lambda: f"包转发率满足({p.forwarding_rate_mpps} Mpps)",
                 fail_msg=lambda: f"包转发率不足({p.forwarding_rate_mpps} Mpps)")

        consider("rack_units", req.rack_units, p.rack_units,
                 ok_msg=lambda: f"机架规格匹配({p.rack_units}U)",
                 fail_msg=lambda: f"机架规格不匹配({p.rack_units}U vs {req.rack_units.exact}U)")

        consider("poe", req.poe, p.poe,
                 ok_msg=("支持 PoE 供电" if req.poe.exact else "无需 PoE,产品也未配 PoE"),
                 fail_msg=("不支持 PoE" if req.poe.exact else "产品带 PoE,但用户不需要"))

        consider("redundant_power", req.redundant_power, p.redundant_power,
                 ok_msg="支持冗余电源",
                 fail_msg="不支持冗余电源")

        consider("budget_cny", req.budget_cny, p.list_price_cny,
                 ok_msg=lambda: f"预算内(报价 ≤ ¥{req.budget_cny.max:.0f})",
                 fail_msg=lambda: f"超出预算(报价 > ¥{req.budget_cny.max:.0f})")

        # Normalize to 0..1; if no constraints were set, fall back to 0.5
        # (we have nothing to score on — treat as neutral candidate).
        normalized = (score / max_possible) if max_possible > 0 else 0.5

        return MatchResult(
            product=p,
            score=normalized,
            reasons=reasons or ["未指定明确约束,作为候选返回"],
            warnings=warnings,
        )


__all__ = ["RuleMatcher"]
