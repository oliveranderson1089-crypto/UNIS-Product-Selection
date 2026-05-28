"""
Quote formatter — apply a chain of rules and save the result.

Usage:
    report = format_quote(Path("input.xlsx"), Path("output.xlsx"))
    for r in report.rule_results:
        print(r.name, r.applied, r.changes)

Accepts both .xls (auto-converted via Excel/WPS COM) and .xlsx input.
Output is always .xlsx, placed next to the ORIGINAL input regardless of
whether a temp conversion happened in between.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .reader import open_quote
from .rules import DEFAULT_RULES
from .rules.base import QuoteRule, RuleResult
from .xls_convert import cleanup as cleanup_conversion

logger = logging.getLogger(__name__)


@dataclass
class FormatReport:
    input_path: str
    output_path: str
    rule_results: list[RuleResult] = field(default_factory=list)
    # Set when the input was .xls and we auto-converted to .xlsx first.
    # Tells the user via the CLI/UI which method ran + any caveats.
    conversion_method: str | None = None
    conversion_warnings: list[str] = field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return sum(1 for r in self.rule_results if r.applied)


def format_quote(
    input_path: Path,
    output_path: Path | None = None,
    *,
    rules: Iterable[QuoteRule] | None = None,
    context: dict | None = None,
    auto_convert_xls: bool = True,
) -> FormatReport:
    """
    Apply the rule chain to `input_path` and save to `output_path`.

    Output naming defaults to `<original_stem>.formatted.xlsx` placed
    NEXT TO THE ORIGINAL input — even when input was .xls and got
    converted to a temp .xlsx in between, we always write back next to
    where the user expects to find it.

    Never overwrites the input in place — the user always has a clean
    fallback. Temp .xlsx files from .xls conversion are cleaned up
    after we save.
    """
    input_path = Path(input_path)
    if output_path is None:
        # Always .xlsx, regardless of input extension — output preserves
        # formulas only when written as .xlsx anyway.
        output_path = input_path.with_name(input_path.stem + ".formatted.xlsx")
    output_path = Path(output_path)

    rules = list(rules) if rules else list(DEFAULT_RULES)
    context = context or {}
    context.setdefault("input_path", str(input_path))
    context.setdefault("filename", input_path.name)

    logger.info("Formatting %s -> %s (%d rules)", input_path, output_path, len(rules))

    loaded = open_quote(input_path, auto_convert_xls=auto_convert_xls)
    wb = loaded.workbook

    report = FormatReport(input_path=str(input_path), output_path=str(output_path))
    if loaded.conversion is not None:
        report.conversion_method = loaded.conversion.method
        report.conversion_warnings = list(loaded.conversion.warnings)

    try:
        for rule in rules:
            try:
                applies = rule.applies_to(wb, context)
            except Exception as exc:                              # noqa: BLE001
                logger.exception("rule.applies_to failed: %s", rule.name)
                report.rule_results.append(RuleResult(
                    name=rule.name,
                    warnings=[f"applies_to 异常: {exc}"],
                ))
                continue
            if not applies:
                report.rule_results.append(RuleResult(
                    name=rule.name,
                    warnings=["不适用于该文件,跳过"],
                ))
                continue
            try:
                res = rule.apply(wb, context)
            except Exception as exc:                              # noqa: BLE001
                logger.exception("rule.apply failed: %s", rule.name)
                res = RuleResult(name=rule.name, warnings=[f"规则异常: {exc}"])
            report.rule_results.append(res)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(output_path))
        logger.info("Saved formatted quote: %s", output_path)
    finally:
        # Always clean up the temp .xlsx (if any), even on rule errors —
        # leaving them in tempdir is harmless but messy.
        if loaded.conversion is not None:
            cleanup_conversion(loaded.conversion)

    return report


__all__ = ["format_quote", "FormatReport"]
