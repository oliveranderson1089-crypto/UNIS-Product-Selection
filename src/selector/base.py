"""Matcher interface + result container."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..requirement.schema import Requirement
from ..storage.models import Product


@dataclass
class MatchResult:
    """One ranked candidate with traceable scoring breakdown."""

    product: Product
    score: float                                 # 0.0–1.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def model(self) -> str:
        return self.product.model

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.product.model,
            "series": self.product.series,
            "category": self.product.category,
            "score": round(self.score, 4),
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "page_url": self.product.page_url,
        }


class Matcher(ABC):
    @abstractmethod
    def match(self, requirement: Requirement, *, top_k: int = 5) -> list[MatchResult]: ...


__all__ = ["MatchResult", "Matcher"]
