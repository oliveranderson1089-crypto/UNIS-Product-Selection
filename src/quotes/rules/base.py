"""Base class for quote-editing rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openpyxl.workbook import Workbook


@dataclass
class RuleResult:
    """What a rule did. Used to render a friendly summary to the user."""

    name: str
    applied: bool = False               # rule was triggered (vs skipped)
    changes: list[str] = field(default_factory=list)   # human-readable bullet points
    warnings: list[str] = field(default_factory=list)


class QuoteRule(ABC):
    """A reusable transformation applied to a workbook."""

    name: str = "base"
    description: str = ""

    def applies_to(self, wb: "Workbook", context: dict) -> bool:
        """
        Whether the rule should run for this workbook.

        Default is True — common rules (delete columns, fill empty model)
        apply to ALL quotes. Specialized rules (server cleanup, R3800FT20
        template) override this to check sheet content or filename.
        """
        return True

    @abstractmethod
    def apply(self, wb: "Workbook", context: dict) -> RuleResult: ...


__all__ = ["QuoteRule", "RuleResult"]
