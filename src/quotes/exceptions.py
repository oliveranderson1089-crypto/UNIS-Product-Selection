"""Custom exceptions for quote processing."""


class QuoteError(Exception):
    """Base for all quote-processing failures."""


class UnsupportedFormatError(QuoteError):
    """Raised for .xls / .doc / etc. inputs that need conversion first."""


class SheetNotFoundError(QuoteError):
    """A required sheet (价格汇总表 / 价格明细清单) is missing."""


class RuleError(QuoteError):
    """A specific rule failed; carries the rule name for context."""

    def __init__(self, rule: str, msg: str):
        super().__init__(f"{rule}: {msg}")
        self.rule = rule


__all__ = [
    "QuoteError",
    "UnsupportedFormatError",
    "SheetNotFoundError",
    "RuleError",
]
