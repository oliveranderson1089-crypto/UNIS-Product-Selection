"""
Server-quote rules.

Applies only when the quote contains UNIS Server / R<digits> products.
The H3C 配置器 dumps a lot of internal-component rows (假内存模块, PDU
电源线, 滚珠短距滑轨, 通用导风罩, 内部直流电源线, AUX信号线, PCIe
电缆, SAS电缆, etc.) into 价格明细清单 — these are CTO bill-of-material
items the customer never asked for and that pollute the customer-facing
quote. We delete those rows.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..inspector import find_header, iter_data_rows
from ..reader import has_sheet, require_sheet
from .base import QuoteRule, RuleResult

if TYPE_CHECKING:
    from openpyxl.workbook import Workbook


# Keyword fragments that identify internal/BOM-only components.
# Any 描述 cell that CONTAINS one of these (case-insensitive) gets dropped.
# Curated against the live 动力院4所 + W1241 server quotes.
INTERNAL_COMPONENT_KEYWORDS = (
    "假内存模块",
    "硬盘背板模块",
    "PCIe5.0 FHHL",          # Riser
    "Riser1/2模块",
    "PDU电源线",
    "墙插交流电源线",
    "iFIST模块",
    "滚珠短距滑轨",
    "滑轨",
    "导风罩模块",
    "OCP专用导风罩",
    "内部直流电源线",
    "AUX信号线",
    "PCIe电缆",
    "SAS电缆",
    "超级电容模块",
    "Flash掉电保护模块",
)

# Models that indicate this quote needs server cleanup
SERVER_MODEL_RE = re.compile(
    r"UNIS\s*Server\s*R\d{3,4}|R\d{3,4}\s*G\d",
    re.IGNORECASE,
)


class DropInternalServerComponents(QuoteRule):
    name = "drop_internal_server_components"
    description = "删除服务器价格明细清单中的内部组件行(假内存/电缆/滑轨/导风罩等)"

    def applies_to(self, wb: "Workbook", context: dict) -> bool:
        """Only run if the workbook actually contains server models."""
        if not has_sheet(wb, "price_main"):
            return False
        sheet = require_sheet(wb, "price_main")
        for row in sheet.iter_rows(values_only=True):
            for v in row:
                if isinstance(v, str) and SERVER_MODEL_RE.search(v):
                    return True
        return False

    def apply(self, wb: "Workbook", context: dict) -> RuleResult:
        result = RuleResult(name=self.name)
        if not has_sheet(wb, "price_detail"):
            result.warnings.append("没有 '价格明细清单' sheet,跳过")
            return result

        sheet = require_sheet(wb, "price_detail")
        header = find_header(sheet)
        if header is None:
            result.warnings.append("没找到 '序号' 表头行,跳过")
            return result

        desc_col = header.headers.get("描述")
        if desc_col is None:
            result.warnings.append(f"没找到 '描述' 列(找到: {list(header.headers)})")
            return result

        # Collect rows to delete first (top-down), then delete bottom-up
        # so row numbers don't shift under us during deletion.
        to_delete: list[tuple[int, str, str]] = []
        for r in iter_data_rows(sheet, header):
            desc = sheet.cell(row=r, column=desc_col).value
            if not isinstance(desc, str):
                continue
            matched_kw = self._first_match(desc)
            if matched_kw:
                to_delete.append((r, matched_kw, desc.replace("\n", " ")[:50]))

        for r, _, _ in sorted(to_delete, key=lambda t: -t[0]):
            sheet.delete_rows(r, 1)

        if to_delete:
            result.applied = True
            for r, kw, snippet in to_delete:
                result.changes.append(f"R{r}: 删除 ({kw}) — {snippet}…")
        else:
            result.warnings.append("没有识别到任何内部组件行(可能已是干净的报价)")
        return result

    @staticmethod
    def _first_match(desc: str) -> str | None:
        for kw in INTERNAL_COMPONENT_KEYWORDS:
            if kw in desc:
                return kw
        return None


# ---------------------------------------------------------------------------
# COM-only placeholders
# ---------------------------------------------------------------------------
# These two rules require Excel COM to preserve formulas while inserting
# rows / rewriting service lines. They live in `com_formatter.py`. We
# register stubs here so:
#   1. They show up in DEFAULT_RULES (so the formatter actually enables
#      them on the COM path)
#   2. CLI --skip / UI checkboxes can target them by name
#   3. On non-Windows / no-Excel systems the openpyxl path can warn
#      clearly instead of silently doing nothing
class _ComOnlyStub(QuoteRule):
    """Base for rules implemented exclusively in `com_formatter.py`."""

    def applies_to(self, wb: "Workbook", context: dict) -> bool:
        # Only meaningful on the COM path. The formatter consults
        # `applies_to` only on the openpyxl fallback path — there we want
        # `apply` to run so it can emit a clear warning.
        return True

    def apply(self, wb: "Workbook", context: dict) -> RuleResult:
        return RuleResult(
            name=self.name,
            warnings=["此规则需要 Excel COM(Windows + Office/WPS),当前走纯 Python 路径,已跳过"],
        )


class SwapOemServiceLine(_ComOnlyStub):
    name = "swap_oem_service_line"
    description = "R4930/R3935 G7:用 IT产品BOM 中的 7×24 NBD 维保行替换默认 OEM 服务行"


class FillR3800FT20Template(_ComOnlyStub):
    name = "fill_r3800ft20_template"
    description = "R3800FT20 G3:按外部模板展开 BOM 行,保留公式与小计"


__all__ = [
    "DropInternalServerComponents",
    "SwapOemServiceLine",
    "FillR3800FT20Template",
    "INTERNAL_COMPONENT_KEYWORDS",
    "SERVER_MODEL_RE",
]
