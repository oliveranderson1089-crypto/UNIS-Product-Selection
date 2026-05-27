"""Quote-editing rules. Each rule is independent and ordered by the formatter."""

from .base import QuoteRule, RuleResult
from .common import DropFixedColumns, FillEmptyModel, RemoveH3CLogo
from .server import DropInternalServerComponents

# Default rule chain — applied in this order. Rules whose `applies_to`
# returns False are silently skipped, so the same list works for any
# input file (switch quote, server quote, mixed).
DEFAULT_RULES: tuple[QuoteRule, ...] = (
    DropFixedColumns(),
    FillEmptyModel(),
    RemoveH3CLogo(),
    DropInternalServerComponents(),
)

__all__ = [
    "QuoteRule", "RuleResult",
    "DropFixedColumns", "FillEmptyModel", "RemoveH3CLogo",
    "DropInternalServerComponents",
    "DEFAULT_RULES",
]
