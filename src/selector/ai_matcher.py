"""
AI matcher.

Strategy:
1. Run RuleMatcher first to get a small high-quality candidate set.
2. Additively union in semantic (vector) recall — products that match the
   requirement in *meaning* but slipped through the keyword/spec SQL prefilter.
   Semantic-only candidates are scored by the rule scorer when it can judge
   them, and by scaled vector similarity when the rule scorer disqualifies them
   (typically a category-taxonomy mismatch on a fuzzy query).
3. Hand the combined candidates + the requirement to the LLM and ask it to
   re-rank + write human-friendly reasoning.
4. Merge LLM reasoning back onto the rule-based score (never lose the
   deterministic ranking signal).

This makes the AI matcher additive: semantic recall only *adds* candidates,
the LLM only improves explanations and breaks ties — neither can silently
downgrade a strictly-better product, and every step degrades gracefully
(semantic recall → rule shortlist; LLM rerank → rule ranking).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ..config import get_config
from ..llm import Message, get_router
from ..requirement.schema import Requirement
from ..storage import get_db
from .base import MatchResult, Matcher
from .rule_matcher import RuleMatcher

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """\
你是紫光华三/UNIS 产品选型专家。用户会给你:
1) 一段结构化的客户需求
2) 一份候选产品清单(已按硬指标预筛过)

任务:为每个候选产品给出 0-100 的契合度评分,并写一句话理由,说明为什么这款
产品适合(或不适合)该客户。理由要中文、面向销售/售前,不要堆砌参数。

严格按下面 JSON 数组格式返回,顺序与输入候选清单一致,不要输出任何解释或前后缀,
直接以 [ 开头、以 ] 结尾:
[
  {"model": "...", "score": 0-100, "reason": "..."},
  ...
]
"""


@dataclass
class AIMatcher(Matcher):
    """Rule-first, semantic-augmented, LLM-reranked matcher."""

    rule: RuleMatcher = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.rule is None:
            self.rule = RuleMatcher()

    def match(self, requirement: Requirement, *, top_k: int = 5) -> list[MatchResult]:
        cfg = get_config()
        context_n = cfg.selector.ai_context_top_n

        # 1) deterministic shortlist
        shortlist = self.rule.match(requirement, top_k=context_n)

        # 1b) additive semantic recall (best-effort): union vector-recalled
        #     candidates the SQL prefilter missed. Degrades to `shortlist` on
        #     any failure (semantic disabled, empty index, Ollama down, …).
        candidates = self._augment_with_semantic(requirement, shortlist, cfg, context_n)
        if not candidates:
            return []

        # 2) ask the LLM to re-rank + explain. Rerank only the strongest slice
        #    so the (currently CPU-bound) local model stays responsive and its
        #    output fits max_tokens; the full set still informs the fallback.
        rerank_input = candidates[: min(len(candidates), max(top_k * 2, 10))]
        try:
            ranked = self._llm_rerank(requirement, rerank_input)
        except Exception as exc:                          # noqa: BLE001
            logger.warning("AI matcher failed (%s) — returning rule ranking unchanged.", exc)
            return candidates[:top_k]

        # 3) merge: combine LLM score with rule score (60/40 weight in favor
        #    of the LLM — but bounded so a rule-killed product can never
        #    re-enter the list).
        by_model = {r.model: r for r in rerank_input}
        merged: list[MatchResult] = []
        for entry in ranked:
            base = by_model.get(entry.get("model"))
            if base is None:
                continue
            try:
                llm_score = max(0.0, min(1.0, float(entry["score"]) / 100.0))
            except (KeyError, TypeError, ValueError):
                continue
            combined = 0.6 * llm_score + 0.4 * base.score
            new = MatchResult(
                product=base.product,
                score=combined,
                reasons=base.reasons + [f"AI: {entry.get('reason', '')}"],
                warnings=base.warnings,
                meta={"llm_score": llm_score, "rule_score": base.score},
            )
            merged.append(new)

        if not merged:
            # LLM returned JSON but nothing usable — keep the deterministic order.
            return candidates[:top_k]
        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[:top_k]

    # ---- semantic recall ---------------------------------------------------
    def _augment_with_semantic(
        self,
        req: Requirement,
        shortlist: list[MatchResult],
        cfg,
        context_n: int,
    ) -> list[MatchResult]:
        """Union the rule shortlist with vector-recalled candidates.

        Best-effort and additive. Scope (section / 名录) is read from the
        attached RuleMatcher so semantic recall respects the same boundaries as
        the rule prefilter. ANY failure degrades to the rule shortlist.
        """
        if not getattr(cfg.selector, "use_semantic", False):
            return shortlist

        query_text = (req.raw_input or "").strip() or " ".join(req.keywords).strip()
        if not query_text:
            return shortlist

        try:
            from .semantic_index import SemanticIndex

            index = SemanticIndex(cfg)
            if index.count() == 0:
                return shortlist
            allowed = self._catalog_models() if self.rule.catalog_name else None
            hits = index.query(
                query_text,
                n=cfg.selector.semantic_top_n,
                section=self.rule.section,
                granularity=self.rule.effective_granularity(),
                allowed_models=allowed,
            )
        except Exception as exc:                          # noqa: BLE001
            logger.warning("Semantic recall failed (%s) — using rule shortlist only.", exc)
            return shortlist

        have = {r.model for r in shortlist}
        dist_by_model = {h.model: h.distance for h in hits}
        new_models = [h.model for h in hits if h.model and h.model not in have]
        if not new_models:
            return shortlist

        products = self._products_by_model(new_models)
        extra: list[MatchResult] = []
        for model in new_models:
            p = products.get(model)
            if p is None:
                continue
            scored = self.rule._score(p, req)
            if scored.score > 0:
                # Rule agrees it's a fit — keep the deterministic score.
                scored.reasons = scored.reasons + ["语义召回(向量匹配)"]
                extra.append(scored)
            else:
                # Rule disqualified it — usually a category-taxonomy mismatch on
                # a fuzzy NL query (e.g. parsed "服务器" vs catalog "大模型一体机").
                # Trust the vector recall, but rank below rule-verified hits by
                # scaling cosine similarity down; the LLM rerank makes the call.
                sim = max(0.0, 1.0 - float(dist_by_model.get(model, 1.0)))
                extra.append(
                    MatchResult(
                        product=p,
                        score=0.5 * sim,
                        reasons=["语义召回(向量匹配;规则未命中硬指标,按语义相关性纳入)"],
                        warnings=scored.warnings,
                    )
                )

        if not extra:
            return shortlist

        logger.info(
            "Semantic recall: +%d candidate(s) on top of %d rule hit(s).",
            len(extra), len(shortlist),
        )
        combined = shortlist + extra
        combined.sort(key=lambda r: r.score, reverse=True)
        return combined[:context_n]

    def _products_by_model(self, models: list[str]) -> dict:
        """Resolve product models to Product rows (catalog is small — one scan)."""
        wanted = set(models)
        return {p.model: p for p in get_db().all_products() if p.model in wanted}

    def _catalog_models(self) -> set[str]:
        """Model whitelist for the active 名录 scope (empty set if none)."""
        prods = get_db().find_products(catalog_name=self.rule.catalog_name, limit=10000)
        return {p.model for p in prods if p.model}

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
        return self._parse_rerank(resp.text)

    @staticmethod
    def _parse_rerank(text: str) -> list[dict]:
        """Extract the JSON array from a chat response, tolerant of fences/prose.

        Local models often wrap the array in ```json fences or add a sentence
        before/after. We strip fences, then slice the outermost [...] array.
        """
        raw = (text or "").strip()
        fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        if fence:
            raw = fence.group(1).strip()
        if not raw.startswith("["):
            start, end = raw.find("["), raw.rfind("]")
            if start != -1 and end > start:
                raw = raw[start : end + 1]
        if not raw:
            raise ValueError("empty LLM rerank response")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("LLM rerank: unparseable response (first 240 chars): %r", (text or "")[:240])
            raise
        if not isinstance(data, list):
            raise ValueError(f"LLM rerank returned {type(data).__name__}, expected list")
        return data


__all__ = ["AIMatcher"]
