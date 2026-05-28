"""Quote-editing rules. Each rule is independent and ordered by the formatter."""

from .base import QuoteRule, RuleResult
from .common import DropFixedColumns, FillEmptyModel, RemoveH3CLogo
from .server import (
    DropInternalServerComponents,
    FillR3800FT20Template,
    SwapOemServiceLine,
)

# Default rule chain — applied in this order. Rules whose `applies_to`
# returns False are silently skipped, so the same list works for any
# input file (switch quote, server quote, mixed).
DEFAULT_RULES: tuple[QuoteRule, ...] = (
    DropFixedColumns(),
    FillEmptyModel(),
    RemoveH3CLogo(),
    DropInternalServerComponents(),
    SwapOemServiceLine(),           # COM-only — replaces OEM service line
    FillR3800FT20Template(),        # COM-only — fills R3800FT20 G3 section
)

__all__ = [
    "QuoteRule", "RuleResult",
    "DropFixedColumns", "FillEmptyModel", "RemoveH3CLogo",
    "DropInternalServerComponents",
    "SwapOemServiceLine", "FillR3800FT20Template",
    "DEFAULT_RULES",
]
