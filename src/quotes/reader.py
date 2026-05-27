"""
Load a quote workbook in a way that preserves formulas.

openpyxl opens .xlsx with formulas intact when `data_only=False`. We
never read computed values — when we need to display a price we read
either the formula string OR the cached value (whichever exists).

Legacy .xls (the older H3C 配置器 default) is NOT supported for
editing — formulas would be lost on save. Users should "另存为 .xlsx"
in Excel/WPS first; we raise a clear error if they don't.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook
from openpyxl.workbook import Workbook

from .exceptions import SheetNotFoundError, UnsupportedFormatError

# H3C 配置器 export sheet name conventions — fixed across all quote files
# we've inspected. If H3C renames them in a future configurator release
# this is the single place to update.
SHEETS = {
    "cover":         "封面",
    "price_summary": "价格总表",
    "price_main":    "价格汇总表",      # main detail with 产品型号 / 描述 / 客户描述
    "price_detail":  "价格明细清单",    # line-item itemization, including BOM components
    "volume":        "体积重量功耗信息",
    "warranty":      "维保服务费明细清单",
    "notes":         "产品备注明细",
}


def open_quote(path: str | Path) -> Workbook:
    """
    Open a quote workbook for editing. Preserves formulas.

    Raises UnsupportedFormatError for non-.xlsx files with a hint on how
    to convert.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    suffix = p.suffix.lower()
    if suffix == ".xls":
        raise UnsupportedFormatError(
            f"{p.name}: 旧版 .xls 格式无法保留公式。请先用 Excel/WPS "
            f"另存为 .xlsx 再处理。"
        )
    if suffix not in (".xlsx", ".xlsm"):
        raise UnsupportedFormatError(
            f"{p.name}: 不支持的格式 {suffix!r}。期望 .xlsx 或 .xlsm。"
        )

    return load_workbook(
        filename=str(p),
        data_only=False,     # keep formulas as formulas
        keep_vba=(suffix == ".xlsm"),
    )


def require_sheet(wb: Workbook, sheet_key: str):
    """Fetch a sheet by its logical key (see `SHEETS` above)."""
    if sheet_key not in SHEETS:
        raise KeyError(f"Unknown sheet key: {sheet_key!r}")
    name = SHEETS[sheet_key]
    if name not in wb.sheetnames:
        raise SheetNotFoundError(
            f"Workbook is missing sheet {name!r} (expected for {sheet_key})."
        )
    return wb[name]


def has_sheet(wb: Workbook, sheet_key: str) -> bool:
    return SHEETS.get(sheet_key) in wb.sheetnames


__all__ = ["open_quote", "require_sheet", "has_sheet", "SHEETS"]
