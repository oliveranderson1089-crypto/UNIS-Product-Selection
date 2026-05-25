"""
AI matcher.

Strategy:
1. Run RuleMatcher first to get a small high-quality candidate set.
2. Hand the top-N candidates + the requirement to the LLM and ask it to
   re-rank + write human-friendly reasoning.
3. Merge LLM reasoning back onto the rule-based score (never lose the
   deterministic ranking signal).

This makes the AI matcher additive: it improves explanations and breaks ties
intelligently, but never silently downgrades a strictly-better product.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ..config import get_config
from ..llm import Message, get_router
from ..requirement.schema import Requirement
from .base import MatchResult, Matcher
from .rule_matcher import RuleMatcher

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
你是紫光华三/UNIS 产品选型专家。用户会给你:
1) 一段结构化的客户需求
2) 一份候选产品清单(已按硬指标预筛过)

任务:为每个候选产品给出 0-100 的契合度评分,并写一句话理由,说明为什么这款
产品适合(或不适合)该客户。理由要中文、面向销售/售前,不要堆砌参数。

严格按下面 JSON 数组格式返回,顺序与输入候选清单一致,不要额外解释:
[
  {"model": "...", "score": 0-100, "reason": "..."},
  ...
]
"""


@dataclass
class AIMatcher(Matcher):
    """Rule-first, LLM-augmented matcher."""

    rule: RuleMatcher = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.rule is None:
            self.rule = RuleMatcher()

    def match(self, requirement: Requirement, *, top_k: int = 5) -> list[MatchResult]:
        cfg = get_config()
        context_n = cfg.selector.ai_context_top_n

        # 1) deterministic shortlist
        shortlist = self.rule.match(requirement, top_k=context_n)
        if not shortlist:
            return []

        # 2) ask the LLM to re-rank + explain
        try:
            ranked = self._llm_rerank(requirement, shortlist)
        except Exception as exc:                          # noqa: BLE001
            logger.warning("AI matcher failed (%s) — returning rule ranking unchanged.", exc)
            return shortlist[:top_k]

        # 3) merge: combine LLM score with rule score (60/40 weight in favor
        #    of the LLM — but bounded so a rule-killed product can never
        #    re-enter the list).
        by_model = {r.model: r for r in shortlist}
        merged: list[MatchResult] = []
        for entry in ranked:
            base = by_model.get(entry["model"])
            if base is None:
                continue
            llm_score = max(0.0, min(1.0, float(entry["score"]) / 100.0))
            combined = 0.6 * llm_score + 0.4 * base.score
            new = MatchResult(
                product=base.product,
                score=combined,
                reasons=base.reasons + [f"AI: {entry['reason']}"],
                warnings=base.warnings,
                meta={"llm_score": llm_score, "rule_score": base.score},
            )
            merged.append(new)

        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[:top_k]

    # ---- LLM call ----------------------------------------------------------
    def _llm_rerank(self, req: Requirement, shortlist: list[MatchResult]) -> list[dict]:
        router = get_router()
        prompt_payload = {
            "requirement": req.to_dict(),
            "candidates": [
                {
                    "model": r.model,
                    "category": r.product.category,
                    "port_count": r.product.port_count,
                    "port_speed": r.product.port_speed,
                    "layer": r.product.layer,
                    "switching_capacity_gbps": r.product.switching_capacity_gbps,
                    "poe": r.product.poe,
                    "redundant_power": r.product.redundant_power,
                    "rack_units": r.product.rack_units,
                    "is_domestic": r.product.is_domestic,
                    "description": (r.product.description or "")[:300],
                }
                for r in shortlist
            ],
        }

        resp = router.call(
            "chat",
            messages=[
                Message(role="system", content=SYSTEM_PROMPT),
                Message(role="user", content=json.dumps(prompt_payload, ensure_ascii=False)),
            ],
        )

        # Models occasionally wrap JSON in fences.
        raw = resp.text.strip()
        m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL)
        if m:
            raw = m.group(1)
        return json.loads(raw)


__all__ = ["AIMatcher"]
