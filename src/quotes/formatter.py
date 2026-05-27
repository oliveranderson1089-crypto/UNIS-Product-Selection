"""
Quote formatter — apply a chain of rules and save the result.

Usage:
    report = format_quote(Path("input.xlsx"), Path("output.xlsx"))
    for r in report.rule_results:
        print(r.name, r.applied, r.changes)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .reader import open_quote
from .rules import DEFAULT_RULES
from .rules.base import QuoteRule, RuleResult

logger = logging.getLogger(__name__)


@dataclass
class FormatReport:
    input_path: str
    output_path: str
    rule_results: list[RuleResult] = field(default_factory=list)

    @property
    def applied_count(self) -> int:
        return sum(1 for r in self.rule_results if r.applied)


def format_quote(
    input_path: Path,
    output_path: Path | None = None,
    *,
    rules: Iterable[QuoteRule] | None = None,
    context: dict | None = None,
) -> FormatReport:
    """
    Apply the rule chain to `input_path` and save to `output_path`.

    If `output_path` is None, writes alongside the input with a
    `.formatted.xlsx` suffix. Never overwrites the input in place — the
    user always has a clean fallback.
    """
    input_path = Path(input_path)
    if output_path is None:
        output_path = input_path.with_name(input_path.stem + ".formatted.xlsx")
    output_path = Path(output_path)

    rules = list(rules) if rules else list(DEFAULT_RULES)
    context = context or {}
    context.setdefault("input_path", str(input_path))
    context.setdefault("filename", input_path.name)

    logger.info("Formatting %s -> %s (%d rules)", input_path, output_path, len(rules))
    wb = open_quote(input_path)
    report = FormatReport(input_path=str(input_path), output_path=str(output_path))

    for rule in rules:
        try:
            applies = rule.applies_to(wb, context)
        except Exception as exc:                                  # noqa: BLE001
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
        except Exception as exc:                                  # noqa: BLE001
            logger.exception("rule.apply failed: %s", rule.name)
            res = RuleResult(name=rule.name, warnings=[f"规则异常: {exc}"])
        report.rule_results.append(res)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))
    logger.info("Saved formatted quote: %s", output_path)
    return report


__all__ = ["format_quote", "FormatReport"]
