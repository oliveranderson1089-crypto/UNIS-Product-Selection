"""
Excel-COM-based quote formatter.

Why COM instead of openpyxl for these operations?

  - `openpyxl.Worksheet.delete_cols` does NOT update formulas in OTHER
    sheets, leading to broken cross-sheet refs (e.g. 价格总表 →
    价格汇总表 references break after column shifts).
  - Multiple successive `delete_cols` calls on the same sheet can
    miscompute same-sheet formula shifts too, producing circular refs
    like `H13 = =I13*H13`.
  - openpyxl re-save sometimes loses Excel-specific number formats and
    column widths, making the output visually different from the input.

Excel itself handles ALL of these correctly: formula references update
across every sheet, column widths and styles are preserved, and shared
formulas survive structural edits.

Requires:
  - Microsoft Excel or WPS Office installed (most H3C 配置器 users have
    one — the configurator exports require it)
  - `pywin32` to expose the COM bridge

When COM is unavailable we fall back to the openpyxl rule path in
`formatter.py`.
"""

from __future__ import annotations

import logging
import platform
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# Excel constants — values from the VBA reference. Defining them here so
# we don't have to import the Excel constants module (which gencache-
# generates dynamically and would slow startup).
_XL_OPEN_XML_WORKBOOK = 51       # FileFormat for .xlsx
_XL_CENTER = -4108               # HorizontalAlignment xlCenter
_XL_SHIFT_UP = -4162             # Delete shift direction


# Sheet names — same canonical set as reader.py
_SHEET_PRICE_MAIN = "价格汇总表"
_SHEET_PRICE_DETAIL = "价格明细清单"


# ---------------------------------------------------------------------------
# Result types — mirror RuleResult so formatter.py can render either path
# uniformly.
# ---------------------------------------------------------------------------
@dataclass
class ComRuleResult:
    name: str
    applied: bool = False
    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ComFormatReport:
    method: str = "com"
    rule_results: list[ComRuleResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def com_available() -> bool:
    """True if we're on Windows AND pywin32 imports cleanly."""
    if platform.system() != "Windows":
        return False
    try:
        import win32com.client  # noqa: F401
        return True
    except ImportError:
        return False


def format_via_com(
    input_path: Path,
    output_path: Path,
    *,
    enabled_rules: set[str] | None = None,
) -> ComFormatReport:
    """
    Open `input_path` (.xls / .xlsx) in Excel, apply rules in-process,
    save as `output_path` (.xlsx). Cross-sheet formulas survive intact.

    `enabled_rules` filters which rules run. None means all 4 default
    rules; pass a set of names from `KNOWN_RULES` to limit.
    """
    if enabled_rules is None:
        enabled_rules = set(KNOWN_RULES)

    report = ComFormatReport(method="com")
    with _excel_session() as excel:
        wb = excel.Workbooks.Open(
            str(input_path.resolve()),
            UpdateLinks=0,
            IgnoreReadOnlyRecommended=True,
        )
        try:
            if "clean_price_main" in enabled_rules:
                report.rule_results.append(_clean_price_main(wb))
            if "fill_empty_model" in enabled_rules:
                report.rule_results.append(_fill_empty_model(wb))
            if "remove_h3c_logo" in enabled_rules:
                report.rule_results.append(_remove_h3c_logo(wb))
            if "drop_internal_server_components" in enabled_rules:
                report.rule_results.append(_drop_internal_server_components(wb))

            output_path.parent.mkdir(parents=True, exist_ok=True)
            wb.SaveAs(str(output_path.resolve()), FileFormat=_XL_OPEN_XML_WORKBOOK)
        finally:
            try:
                wb.Close(SaveChanges=False)
            except Exception:                                      # noqa: BLE001
                pass

    return report


KNOWN_RULES = (
    "clean_price_main",
    "fill_empty_model",
    "remove_h3c_logo",
    "drop_internal_server_components",
)


# ---------------------------------------------------------------------------
# Excel session lifecycle
# ---------------------------------------------------------------------------
@contextmanager
def _excel_session() -> Iterator:
    """Spin up an isolated Excel COM instance; always Quit() on exit."""
    import win32com.client as win32

    excel = win32.DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.ScreenUpdating = False
    try:
        yield excel
    finally:
        try:
            excel.Quit()
        except Exception:                                          # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Rule: clean_price_main (the big one)
# ---------------------------------------------------------------------------
# Per user's refined Q3 spec:
#   1. Delete 产品名称
#   2. Delete (the originally-empty) 客户版产品描述
#   3. Rename 详细描述 → 客户版产品描述  (the rich text becomes customer-facing)
#   4. Delete 要求提前报备周期
#   5. Delete 订单准备周期
# Plus presentational fixes that were also requested:
#   6. Delete the project-title-with-date line above the 价格汇总表 heading
#   7. Center-align the 价格汇总表 heading
_COLUMNS_TO_DROP = ("产品名称", "客户版产品描述", "要求提前报备周期", "订单准备周期")
_RENAME_COLUMN = ("详细描述", "客户版产品描述")


def _clean_price_main(wb) -> ComRuleResult:
    res = ComRuleResult(name="clean_price_main")
    sheet = _try_get_sheet(wb, _SHEET_PRICE_MAIN)
    if sheet is None:
        res.warnings.append(f"没有 '{_SHEET_PRICE_MAIN}' sheet,跳过")
        return res

    # --- 1) drop the date-line title row sitting above the heading ----------
    heading_row = _find_row_with_value(sheet, _SHEET_PRICE_MAIN, scan_rows=30)
    if heading_row is not None:
        title_row = _find_date_title_row(sheet, above_row=heading_row)
        if title_row is not None:
            sheet.Rows(title_row).Delete()
            res.changes.append(f"删除标题行 R{title_row}(日期+项目名)")
            heading_row -= 1   # everything shifted up by one
    else:
        res.warnings.append("没找到 '价格汇总表' 标题单元格")

    # --- 2) center-align the 价格汇总表 heading -----------------------------
    if heading_row is not None:
        cell = sheet.Cells(heading_row, 1)
        try:
            cell.HorizontalAlignment = _XL_CENTER
            res.changes.append(f"标题居中(R{heading_row})")
        except Exception as exc:                                  # noqa: BLE001
            res.warnings.append(f"标题居中失败: {exc}")

    # --- 3+4) column delete + rename ---------------------------------------
    header_row, headers = _find_header_row(sheet)
    if header_row is None:
        res.warnings.append("没找到 '序号' 表头行,跳过列操作")
        return res

    # Drop columns in DESCENDING order so earlier indices stay valid.
    drop_targets = [(name, headers[name]) for name in _COLUMNS_TO_DROP
                    if name in headers]
    drop_targets.sort(key=lambda t: -t[1])
    for name, col_idx in drop_targets:
        sheet.Columns(col_idx).Delete()
        res.changes.append(f"删除列 '{name}' (原列 {col_idx})")

    if not drop_targets:
        res.warnings.append(f"4 个目标列一个都没找到 — 表头可能是其他模板")

    # Re-discover header positions after deletion, then rename.
    _, headers = _find_header_row(sheet)
    src_name, dst_name = _RENAME_COLUMN
    if src_name in headers:
        col_idx = headers[src_name]
        sheet.Cells(header_row, col_idx).Value = dst_name
        res.changes.append(f"重命名列 '{src_name}' → '{dst_name}' (列 {col_idx})")

    res.applied = bool(res.changes)
    return res


def _find_date_title_row(sheet, *, above_row: int) -> int | None:
    """
    Locate the row containing the project name + "截止日期" between
    the top of the sheet and `above_row`. H3C 配置器 inserts this
    annotation above the heading; users want it gone.
    """
    for r in range(1, above_row):
        for c in range(1, _used_cols(sheet) + 1):
            v = sheet.Cells(r, c).Value
            if v and "截止日期" in str(v):
                return r
    return None


# ---------------------------------------------------------------------------
# Rule: fill_empty_model
# ---------------------------------------------------------------------------
# Match the first whitespace-separated UNIS code in a description.
_MODEL_RE = re.compile(
    r"\b(UNIS(?:\s+(?:Server|Storage))?[\s\-]+[A-Z][A-Z0-9\-]*(?:\s+G\d)?)",
)


def _fill_empty_model(wb) -> ComRuleResult:
    res = ComRuleResult(name="fill_empty_model")
    sheet = _try_get_sheet(wb, _SHEET_PRICE_MAIN)
    if sheet is None:
        res.warnings.append(f"没有 '{_SHEET_PRICE_MAIN}' sheet,跳过")
        return res

    header_row, headers = _find_header_row(sheet)
    if header_row is None:
        res.warnings.append("没找到表头,跳过")
        return res
    if "产品型号" not in headers or "描述" not in headers:
        res.warnings.append(f"缺少 '产品型号' 或 '描述' 列(现有:{list(headers)})")
        return res

    model_col = headers["产品型号"]
    desc_col = headers["描述"]
    last_row = _used_rows(sheet)

    for r in range(header_row + 1, last_row + 1):
        if sheet.Cells(r, model_col).Value not in (None, ""):
            continue
        desc = sheet.Cells(r, desc_col).Value
        if not desc:
            continue
        m = _MODEL_RE.search(str(desc))
        if m:
            extracted = re.sub(r"\s+", " ", m.group(1).strip())
            sheet.Cells(r, model_col).Value = extracted
            res.changes.append(f"R{r}: 填入 '{extracted}'")

    res.applied = bool(res.changes)
    return res


# ---------------------------------------------------------------------------
# Rule: remove_h3c_logo
# ---------------------------------------------------------------------------
def _remove_h3c_logo(wb) -> ComRuleResult:
    res = ComRuleResult(name="remove_h3c_logo")
    sheet = _try_get_sheet(wb, _SHEET_PRICE_MAIN)
    if sheet is None:
        res.warnings.append(f"没有 '{_SHEET_PRICE_MAIN}' sheet,跳过")
        return res

    # COM Shapes collection is 1-indexed; walk in reverse so deletion
    # doesn't shift the iterator out from under us.
    n_shapes = sheet.Shapes.Count
    if n_shapes == 0:
        res.warnings.append("没找到图片(可能已被手动删除)")
        return res
    for i in range(n_shapes, 0, -1):
        try:
            sheet.Shapes.Item(i).Delete()
        except Exception:                                          # noqa: BLE001
            # Some shape types refuse deletion; carry on
            pass

    res.applied = True
    res.changes.append(f"删除了 {n_shapes} 张图片")
    return res


# ---------------------------------------------------------------------------
# Rule: drop_internal_server_components
# ---------------------------------------------------------------------------
_INTERNAL_COMPONENT_KEYWORDS = (
    "假内存模块", "硬盘背板模块", "PCIe5.0 FHHL", "Riser1/2模块",
    "PDU电源线", "墙插交流电源线", "iFIST模块", "滚珠短距滑轨", "滑轨",
    "导风罩模块", "OCP专用导风罩", "内部直流电源线", "AUX信号线",
    "PCIe电缆", "SAS电缆", "超级电容模块", "Flash掉电保护模块",
)

# Detects rows where 价格汇总表 references a server (R4930, R3935, etc.)
# but NOT R3800FT20 G3 — that one has a different cleanup spec (Wave 3
# template fill) and minimal CTO breakdown, so this rule is a no-op for it.
_SERVER_MODEL_RE = re.compile(
    r"UNIS\s*Server\s*R\d{3,4}|R\d{3,4}\s*G\d",
    re.IGNORECASE,
)
_R3800FT20_RE = re.compile(r"R3800FT20", re.IGNORECASE)


def _drop_internal_server_components(wb) -> ComRuleResult:
    res = ComRuleResult(name="drop_internal_server_components")

    if not _is_server_quote(wb):
        res.warnings.append("不适用于该文件(非服务器报价),跳过")
        return res

    sheet = _try_get_sheet(wb, _SHEET_PRICE_DETAIL)
    if sheet is None:
        res.warnings.append(f"没有 '{_SHEET_PRICE_DETAIL}' sheet,跳过")
        return res

    header_row, headers = _find_header_row(sheet)
    if header_row is None or "描述" not in headers:
        res.warnings.append("没找到表头或 '描述' 列,跳过")
        return res

    desc_col = headers["描述"]
    last_row = _used_rows(sheet)

    # Collect first, delete bottom-up so row numbers stay valid.
    to_delete: list[tuple[int, str, str]] = []
    for r in range(header_row + 1, last_row + 1):
        desc = sheet.Cells(r, desc_col).Value
        if not isinstance(desc, str):
            continue
        for kw in _INTERNAL_COMPONENT_KEYWORDS:
            if kw in desc:
                to_delete.append((r, kw, desc.replace("\n", " ")[:50]))
                break

    for r, _, _ in sorted(to_delete, key=lambda t: -t[0]):
        sheet.Rows(r).Delete()

    if to_delete:
        res.applied = True
        for r, kw, snippet in to_delete:
            res.changes.append(f"R{r}: 删除 ({kw}) — {snippet}…")
    else:
        res.warnings.append("没识别到内部组件行(可能已是干净的报价 / 或非 CTO 服务器)")
    return res


def _is_server_quote(wb) -> bool:
    """Scan 价格汇总表 for any cell mentioning a server model code."""
    sheet = _try_get_sheet(wb, _SHEET_PRICE_MAIN)
    if sheet is None:
        return False
    for r in range(1, _used_rows(sheet) + 1):
        for c in range(1, _used_cols(sheet) + 1):
            v = sheet.Cells(r, c).Value
            if not isinstance(v, str):
                continue
            if _SERVER_MODEL_RE.search(v) and not _R3800FT20_RE.search(v):
                return True
    return False


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def _try_get_sheet(wb, name: str):
    for s in wb.Sheets:
        if s.Name == name:
            return s
    return None


def _used_rows(sheet) -> int:
    """Number of rows in UsedRange, clamped to a reasonable max."""
    try:
        return min(sheet.UsedRange.Rows.Count, 500)
    except Exception:                                              # noqa: BLE001
        return 0


def _used_cols(sheet) -> int:
    try:
        return min(sheet.UsedRange.Columns.Count, 50)
    except Exception:                                              # noqa: BLE001
        return 0


def _find_header_row(sheet, scan_rows: int = 60) -> tuple[int | None, dict[str, int]]:
    """
    Walk the first `scan_rows` rows looking for col-1 == '序号'.
    Returns (row, {header_text: col_index}) or (None, {}) if not found.
    """
    for r in range(1, scan_rows + 1):
        v = sheet.Cells(r, 1).Value
        if v is None or str(v).strip() != "序号":
            continue
        headers: dict[str, int] = {}
        for c in range(1, _used_cols(sheet) + 1):
            cv = sheet.Cells(r, c).Value
            if cv is not None:
                headers[str(cv).strip()] = c
        return r, headers
    return None, {}


def _find_row_with_value(sheet, value: str, *, col: int = 1, scan_rows: int = 60) -> int | None:
    for r in range(1, scan_rows + 1):
        v = sheet.Cells(r, col).Value
        if v and str(v).strip() == value:
            return r
    return None


__all__ = [
    "com_available",
    "format_via_com",
    "ComFormatReport",
    "ComRuleResult",
    "KNOWN_RULES",
]
