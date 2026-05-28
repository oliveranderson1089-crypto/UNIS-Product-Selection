"""
Quote formatter — orchestrator.

Two implementation paths, picked at runtime:

  1. **COM path** (preferred when on Windows with Excel/WPS installed):
     `com_formatter.format_via_com()` does the entire pipeline inside
     a single Excel session. Cross-sheet formulas, column widths, and
     number formats are preserved because Excel itself handles all the
     structural plumbing.

  2. **openpyxl fallback**: chain of rule classes from `rules/`. Used
     on non-Windows systems or when COM is unavailable. Has known
     limitations on cross-sheet formula updates after column deletion.

Output naming is always `<original_stem>.formatted.xlsx` next to the
original input, regardless of input format or path taken.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .com_formatter import (
    ComRuleResult,
    com_available,
    format_via_com,
)
from .reader import open_quote
from .rules import DEFAULT_RULES
from .rules.base import QuoteRule, RuleResult
from .xls_convert import cleanup as cleanup_conversion

logger = logging.getLogger(__name__)


@dataclass
class FormatReport:
    input_path: str
    output_path: str
    method: str = "openpyxl"            # "com" | "openpyxl"
    rule_results: list[RuleResult] = field(default_factory=list)
    # Only populated on the openpyxl fallback path for legacy .xls input.
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
    prefer_com: bool = True,
) -> FormatReport:
    """
    Apply formatting rules to `input_path` and save to `output_path`.

    Tries Excel COM first (preserves formulas + formatting). Falls back
    to openpyxl if COM is unavailable or fails. Output filename always
    lands next to the ORIGINAL input as `<stem>.formatted.xlsx`.
    """
    input_path = Path(input_path)
    if output_path is None:
        output_path = input_path.with_name(input_path.stem + ".formatted.xlsx")
    output_path = Path(output_path)

    # Map openpyxl rule names → COM rule names for filter compatibility.
    # If the caller supplied an explicit `rules` list (subset of
    # DEFAULT_RULES), translate that to the COM enabled_rules set.
    enabled_rule_names: set[str] | None = None
    if rules is not None:
        names = {r.name for r in rules}
        enabled_rule_names = {_OPENPYXL_TO_COM[n] for n in names if n in _OPENPYXL_TO_COM}

    if prefer_com and com_available():
        try:
            return _format_via_com_wrapped(
                input_path, output_path, enabled_rule_names,
            )
        except Exception as exc:                                  # noqa: BLE001
            logger.warning(
                "COM formatter failed (%s); falling back to openpyxl path", exc,
            )

    return _format_via_openpyxl(
        input_path, output_path,
        rules=rules, context=context,
        auto_convert_xls=auto_convert_xls,
    )


# ---------------------------------------------------------------------------
# COM path: thin wrapper that converts ComFormatReport into FormatReport
# ---------------------------------------------------------------------------
# The openpyxl rule chain uses one rule per concern; the COM formatter
# groups the universal column / heading work into one super-rule for
# atomicity (Excel needs the column work to happen in one session). This
# map lets a CLI --only / --skip filter still target old rule names.
_OPENPYXL_TO_COM = {
    "drop_fixed_columns":               "clean_price_main",
    "fill_empty_model":                 "fill_empty_model",
    "remove_h3c_logo":                  "remove_h3c_logo",
    "drop_internal_server_components":  "drop_internal_server_components",
    "swap_oem_service_line":            "swap_oem_service_line",
    "fill_r3800ft20_template":          "fill_r3800ft20_template",
}


def _format_via_com_wrapped(
    input_path: Path,
    output_path: Path,
    enabled_rule_names: set[str] | None,
) -> FormatReport:
    logger.info("Formatting via COM: %s -> %s", input_path, output_path)
    com_report = format_via_com(
        input_path, output_path, enabled_rules=enabled_rule_names,
    )
    report = FormatReport(
        input_path=str(input_path),
        output_path=str(output_path),
        method="com",
        rule_results=[_com_to_rule_result(r) for r in com_report.rule_results],
    )
    return report


def _com_to_rule_result(cr: ComRuleResult) -> RuleResult:
    # ComRuleResult and RuleResult are structurally identical, but live
    # in different modules so the openpyxl path's typing stays decoupled
    # from win32com. Translate field-by-field.
    return RuleResult(
        name=cr.name,
        applied=cr.applied,
        changes=list(cr.changes),
        warnings=list(cr.warnings),
    )


# ---------------------------------------------------------------------------
# openpyxl fallback (kept verbatim from previous implementation)
# ---------------------------------------------------------------------------
def _format_via_openpyxl(
    input_path: Path,
    output_path: Path,
    *,
    rules: Iterable[QuoteRule] | None,
    context: dict | None,
    auto_convert_xls: bool,
) -> FormatReport:
    rules = list(rules) if rules else list(DEFAULT_RULES)
    context = context or {}
    context.setdefault("input_path", str(input_path))
    context.setdefault("filename", input_path.name)

    logger.info("Formatting via openpyxl: %s -> %s (%d rules)",
                input_path, output_path, len(rules))

    loaded = open_quote(input_path, auto_convert_xls=auto_convert_xls)
    wb = loaded.workbook
    report = FormatReport(
        input_path=str(input_path),
        output_path=str(output_path),
        method="openpyxl",
    )
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
        if loaded.conversion is not None:
            cleanup_conversion(loaded.conversion)

    return report


__all__ = ["format_quote", "FormatReport"]
