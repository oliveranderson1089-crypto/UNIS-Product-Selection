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


class ComFormatError(QuoteError):
    """Excel-COM formatting was expected to work (Windows + pywin32) but
    failed, and we deliberately refused to silently fall back to the lossy
    openpyxl/xlrd path.

    This is the hard guard against the "跑久了就丢格式" symptom: after a
    long-running session, orphaned ``excel.exe`` processes pile up until
    ``DispatchEx`` can no longer start a new instance; the old code then
    quietly degraded to a values-only conversion that drops images, merged
    cells and column widths (a ~18KB formatless table). Rather than hand the
    user a broken quote, we raise this so the UI/CLI can tell them the fix:
    restart the program or refresh the page (which resets the resource
    pressure), and make sure the source file isn't open in Excel/WPS.

    Carries the original exception via ``__cause__`` (``raise ... from exc``)
    for logs and debugging.
    """


__all__ = [
    "QuoteError",
    "UnsupportedFormatError",
    "SheetNotFoundError",
    "RuleError",
    "ComFormatError",
]
