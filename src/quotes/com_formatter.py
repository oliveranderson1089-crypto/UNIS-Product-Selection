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
            if "swap_oem_service_line" in enabled_rules:
                report.rule_results.append(_swap_oem_service_line(wb))
            if "fill_r3800ft20_template" in enabled_rules:
                report.rule_results.append(_fill_r3800ft20_template(wb))

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
    "swap_oem_service_line",
    "fill_r3800ft20_template",
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
# Per Q3 spec (confirmed): delete these 4 columns entirely from 价格汇总表.
# Note: 客户版产品描述 stays (even though it's usually empty), and 详细描述
# goes entirely — including its long-text content. Earlier this rule
# tried to "rename 详细描述 → 客户版产品描述" to preserve the rich text;
# the user explicitly rejected that — they want the column gone.
# Plus presentational fixes:
#   - Delete the project-title-with-date line above the 价格汇总表 heading
#   - Center-align the 价格汇总表 heading
_COLUMNS_TO_DROP = ("产品名称", "详细描述", "要求提前报备周期", "订单准备周期")


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
    # ---- originally curated from 动力院 R4930 G7 brick set ----------------
    "假内存模块", "硬盘背板模块", "PCIe5.0 FHHL", "Riser1/2模块",
    "PDU电源线", "墙插交流电源线", "iFIST模块", "滚珠短距滑轨", "滑轨",
    "导风罩模块", "OCP专用导风罩", "内部直流电源线", "AUX信号线",
    "PCIe电缆", "SAS电缆", "超级电容模块", "Flash掉电保护模块",
    # ---- added per R3935 G7 / R5330 G7 sightings -------------------------
    "AUX信号电缆",          # spelling variant of AUX信号线
    "风扇模块",             # e.g. "4U 8056风扇模块(CTO&BTO)" — standalone CTO row.
                            # Safe: main mainboard rows say "8056风扇*4" not
                            # "风扇模块".
    "散热器模块",           # e.g. "2U L型T型热管散热器模块(SL2)(CMCTO)"
                            # Safe: mainboard descriptions say "散热器*2" not
                            # "散热器模块".
    "BMC管理板",            # e.g. "HDM3 AST2600 BMC管理板模块"
                            # Safe: mainboard rows say "AST2600 BMC" without
                            # "管理板".
    "GPU Switch计算模块",   # e.g. "R5330 G7 8GPU Switch计算模块"
    "硬盘扩展模块",         # e.g. "R5300 G6 12LFF硬盘扩展模块"
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
    """
    Two-front cleanup of internal CTO components for server quotes:

      1. 价格明细清单: DELETE the row entirely (the line item is gone).
      2. 价格汇总表 描述 cell: STRIP matching lines from the multi-line
         summary blob. (Each row's 描述 cell is one big semicolon-
         separated string mirroring the line items; users see this in
         the customer-facing summary, so leaving 假内存/PDU电源线/etc.
         in there defeats the purpose of removing them from the detail.)

    Both fronts use the same keyword list so behavior is consistent.
    """
    res = ComRuleResult(name="drop_internal_server_components")

    if not _is_server_quote(wb):
        res.warnings.append("不适用于该文件(非服务器报价),跳过")
        return res

    # ---- Front 1: 价格明细清单 row deletion -------------------------------
    rows_deleted = _delete_internal_rows_in_detail(wb, res)

    # ---- Front 2: 价格汇总表 描述 cell line-filtering ---------------------
    lines_stripped = _strip_internal_lines_in_summary(wb, res)

    if rows_deleted == 0 and lines_stripped == 0:
        res.warnings.append("没识别到内部组件(可能已是干净的报价 / 或非 CTO 服务器)")
    else:
        res.applied = True
    return res


def _delete_internal_rows_in_detail(wb, res: ComRuleResult) -> int:
    """Delete BOM rows in 价格明细清单 whose 描述 matches a keyword."""
    sheet = _try_get_sheet(wb, _SHEET_PRICE_DETAIL)
    if sheet is None:
        res.warnings.append(f"没有 '{_SHEET_PRICE_DETAIL}' sheet,跳过明细行删除")
        return 0
    header_row, headers = _find_header_row(sheet)
    if header_row is None or "描述" not in headers:
        res.warnings.append("明细清单没找到表头/描述列")
        return 0

    desc_col = headers["描述"]
    last_row = _used_rows(sheet)
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
    for r, kw, snippet in to_delete:
        res.changes.append(f"明细 R{r}: 删除 ({kw}) — {snippet}…")
    return len(to_delete)


def _strip_internal_lines_in_summary(wb, res: ComRuleResult) -> int:
    """
    For every row in 价格汇总表, split its 描述 cell on ';' and drop the
    lines containing internal-component keywords. Re-join the survivors
    so the cell stays well-formed.
    """
    sheet = _try_get_sheet(wb, _SHEET_PRICE_MAIN)
    if sheet is None:
        res.warnings.append(f"没有 '{_SHEET_PRICE_MAIN}' sheet,跳过汇总描述清洗")
        return 0
    header_row, headers = _find_header_row(sheet)
    if header_row is None or "描述" not in headers:
        res.warnings.append("汇总表没找到表头/描述列")
        return 0

    desc_col = headers["描述"]
    last_row = _used_rows(sheet)
    total_removed = 0
    for r in range(header_row + 1, last_row + 1):
        desc = sheet.Cells(r, desc_col).Value
        if not isinstance(desc, str) or not desc.strip():
            continue
        cleaned, removed = _filter_internal_lines(desc)
        if removed > 0:
            sheet.Cells(r, desc_col).Value = cleaned
            res.changes.append(f"汇总 R{r}: 描述里删 {removed} 行内部组件")
            total_removed += removed
    return total_removed


def _filter_internal_lines(text: str) -> tuple[str, int]:
    """
    Split on ';', filter out internal-component lines, rejoin.

    Returns (cleaned_text, removed_count). Preserves the original
    "<line>;<newline>" formatting style on output.
    """
    # H3C 配置器 uses ';' as line separator, often followed by a newline.
    parts = [p.strip() for p in re.split(r";\s*\n?", text)]
    kept: list[str] = []
    removed = 0
    for part in parts:
        if not part:
            continue
        if any(kw in part for kw in _INTERNAL_COMPONENT_KEYWORDS):
            removed += 1
            continue
        kept.append(part)
    if not kept:
        return text, 0     # don't blank the cell, leave as-is
    return ";\n".join(kept) + ";", removed


# ---------------------------------------------------------------------------
# Rule: swap_oem_service_line
# ---------------------------------------------------------------------------
# H3C 配置器 inserts a default "OEM 服务" placeholder line on server quotes.
# Per spec we replace that with the proper 3-year 7×24 NBD service (含硬盘
# 介质保留) sourced from the IT产品BOM workbook. Two surfaces to update:
#   - 价格汇总表  : substitute the OEM line in each row's 描述 cell
#   - 价格明细清单: rewrite 5 cells (产品编码 / 产品型号 / 产品代码 /
#                   描述 / 目录单价) on the OEM service line row
_OEM_SERVICE_LINE = "OEM服务器3年5×9下一工作日现场支持(含硬盘不返还) -附专属服务"


def _swap_oem_service_line(wb) -> ComRuleResult:
    res = ComRuleResult(name="swap_oem_service_line")

    if not _is_server_quote(wb):
        res.warnings.append("不适用(非服务器报价或为 R3800FT20 G3),跳过")
        return res

    from ..config import get_config
    from .bom_lookup import find_warranty, get_bom, resolve_bom_path

    bom_path = resolve_bom_path(get_config().quotes.bom_path)
    if bom_path is None:
        res.warnings.append(
            "BOM 文件没找到。请在 config.yaml 的 `quotes.bom_path` 设置 "
            "IT产品BOM编码*.xlsx 的路径(支持通配)。"
        )
        return res
    try:
        bom_entries = get_bom(bom_path)
    except Exception as exc:                                      # noqa: BLE001
        res.warnings.append(f"读 BOM 失败 ({bom_path.name}): {exc}")
        return res

    # ---- Phase 1: 价格汇总表 description lines ---------------------------
    # Also build a per-model BOM cache so Phase 2 can re-use the resolutions.
    model_to_bom: dict[str, object] = {}
    summary_count = _swap_in_price_main(
        wb, bom_entries, model_to_bom, res,
    )

    # ---- Phase 2: 价格明细清单 line-item replacement ---------------------
    detail_count = _swap_in_price_detail(
        wb, bom_entries, model_to_bom, res,
    )

    if summary_count == 0 and detail_count == 0:
        res.warnings.append("没找到任何 OEM 服务行(可能已被替换 / 或不是 CTO 服务器)")
    else:
        res.applied = True
    return res


def _swap_in_price_main(wb, bom_entries, model_to_bom, res) -> int:
    """Swap the OEM line in each 价格汇总表 row's 描述 cell."""
    from .bom_lookup import find_warranty

    sheet = _try_get_sheet(wb, _SHEET_PRICE_MAIN)
    if sheet is None:
        return 0
    header_row, headers = _find_header_row(sheet)
    if header_row is None:
        return 0
    desc_col = headers.get("描述")
    model_col = headers.get("产品型号")
    if desc_col is None or model_col is None:
        res.warnings.append("汇总表缺 描述/产品型号 列")
        return 0

    count = 0
    for r in range(header_row + 1, _used_rows(sheet) + 1):
        desc = sheet.Cells(r, desc_col).Value
        model = sheet.Cells(r, model_col).Value
        if not isinstance(desc, str) or not isinstance(model, str):
            continue
        if _OEM_SERVICE_LINE not in desc:
            continue
        model = model.strip()

        entry = find_warranty(bom_entries, model, hdd_retained=True)
        if entry is None:
            res.warnings.append(
                f"汇总 R{r}: BOM 里没找到 '{model} 3年7×24×NBD维保(含硬盘介质保留)'"
            )
            continue
        model_to_bom[model] = entry

        # Replace the OEM line with the BOM 对外中文描述 in-place.
        sheet.Cells(r, desc_col).Value = desc.replace(
            _OEM_SERVICE_LINE, entry.description,
        )
        res.changes.append(f"汇总 R{r}: {model} → '{entry.description[:50]}…'")
        count += 1
    return count


def _swap_in_price_detail(wb, bom_entries, model_to_bom, res) -> int:
    """
    Find each OEM service row in 价格明细清单 and overwrite its 5 fields
    from the BOM entry corresponding to the row's parent UNIS Server.
    """
    from .bom_lookup import find_warranty

    sheet = _try_get_sheet(wb, _SHEET_PRICE_DETAIL)
    if sheet is None:
        return 0
    header_row, headers = _find_header_row(sheet)
    if header_row is None or "描述" not in headers:
        return 0

    desc_col = headers["描述"]
    code_col = headers.get("产品编码")
    pmodel_col = headers.get("产品型号")
    pcode_col = headers.get("产品代码")
    price_col = headers.get("目录单价(RMB)") or headers.get("目录单价")

    # Walk top-down, tracking the most recent UNIS-Server section marker.
    # H3C 配置器 puts these in column 2 (产品编码) in several formats:
    #   "UNIS Server R4930 G7 #1"  (canonical, with spaces)
    #   "UNISR4930G7 #1"           (compact, no spaces)
    #   "R3935G7 #1"               (very compact)
    # We extract the model-number digits and generation digit, then build
    # the CANONICAL "UNIS Server R<NNNN> G<N>" string for BOM lookup.
    current_server: str | None = None
    server_marker_re = re.compile(
        r"(?:UNIS[\s_]?(?:Server[\s_]?)?)?R(\d{3,4})[\s_-]?G(\d)",
        re.IGNORECASE,
    )
    count = 0

    for r in range(header_row + 1, _used_rows(sheet) + 1):
        marker = sheet.Cells(r, 2).Value
        if isinstance(marker, str):
            m = server_marker_re.search(marker)
            if m:
                current_server = f"UNIS Server R{m.group(1)} G{m.group(2)}"

        desc = sheet.Cells(r, desc_col).Value
        if not isinstance(desc, str) or desc.strip() != _OEM_SERVICE_LINE:
            # Note: we require EXACT match here. The cell in 价格明细清单
            # contains just this line on its own, not as part of a bundle.
            continue

        if current_server is None:
            res.warnings.append(f"明细 R{r}: OEM 行,但上方找不到 UNIS Server 标记")
            continue

        entry = model_to_bom.get(current_server) or find_warranty(
            bom_entries, current_server, hdd_retained=True,
        )
        if entry is None:
            res.warnings.append(
                f"明细 R{r}: BOM 里没找到 '{current_server}' 的 7×24 NBD 维保"
            )
            continue
        model_to_bom.setdefault(current_server, entry)

        # Write the 5 fields. Each write is silent if the column doesn't
        # exist on this particular configurator template.
        if code_col:
            sheet.Cells(r, code_col).Value = entry.code
        if pmodel_col:
            sheet.Cells(r, pmodel_col).Value = entry.model
        if pcode_col:
            sheet.Cells(r, pcode_col).Value = entry.ext_code
        sheet.Cells(r, desc_col).Value = entry.description
        if price_col:
            sheet.Cells(r, price_col).Value = entry.list_price

        res.changes.append(
            f"明细 R{r}: {current_server} → {entry.code} (¥{entry.list_price:.0f})"
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Rule: fill_r3800ft20_template
# ---------------------------------------------------------------------------
# R3800FT20 G3 is a fault-tolerant server H3C 配置器 exports as a single
# bundled CTO row. Customers want to see a per-component breakdown of
# what's inside the bundle, so the user maintains an external template
# Excel that lists all CTO components, then marks 数量 for the ones they
# selected. This rule reads that template and replaces the bundle row
# with the breakdown.
#
# Two surfaces (same as swap_oem_service_line):
#   - 价格汇总表 描述 cell: replace with template descriptions joined by ;
#   - 价格明细清单 R3800FT20G3 section: resize to N template rows, copy
#     data cells from template, preserve per-row formulas via FillDown
_R3800FT20_MODEL = "UNIS Server R3800FT20 G3"
_R3800FT20_SECTION_RE = re.compile(r"R3800FT20\s*G3\s*#\d+", re.IGNORECASE)
# Footer markers — first cell whose 描述 or 产品编码 matches signals the
# bottom of the data section in 价格明细清单.
_FT20_FOOTER_DESCS = frozenset({"单台", "数量"})
_FT20_FOOTER_CODES = frozenset({"小计", "配置组小计", "总计"})


def _fill_r3800ft20_template(wb) -> ComRuleResult:
    res = ComRuleResult(name="fill_r3800ft20_template")

    if not _contains_r3800ft20(wb):
        res.warnings.append("不适用(报价不含 R3800FT20 G3),跳过")
        return res

    from ..config import get_config
    from .r3800ft20_template import load_template, resolve_template_path

    template_path = resolve_template_path(get_config().quotes.r3800ft20_template_path)
    if template_path is None:
        res.warnings.append(
            "找不到 R3800FT20 G3 配置模板。在 config.yaml 里设 "
            "`quotes.r3800ft20_template_path` 指向 'R3800FT20 G3配置模板*.xlsx'。"
        )
        return res

    try:
        items = load_template(template_path)
    except Exception as exc:                                      # noqa: BLE001
        res.warnings.append(f"读模板失败 ({template_path.name}): {exc}")
        return res
    if not items:
        res.warnings.append(
            f"模板 {template_path.name} 里没有任何 数量>0 的行 — 没什么可填的"
        )
        return res

    res.changes.append(f"模板: {template_path.name}  ({len(items)} 项)")

    # Phase 1 + Phase 2 are independent — neither aborts the other.
    summary_updated = _ft20_update_summary_desc(wb, items, res)
    detail_updated = _ft20_replace_detail_section(wb, items, res)

    res.applied = summary_updated or detail_updated
    return res


def _contains_r3800ft20(wb) -> bool:
    """True if 价格汇总表 has a row with 产品型号 mentioning R3800FT20."""
    sheet = _try_get_sheet(wb, _SHEET_PRICE_MAIN)
    if sheet is None:
        return False
    header_row, headers = _find_header_row(sheet)
    if header_row is None or "产品型号" not in headers:
        return False
    model_col = headers["产品型号"]
    for r in range(header_row + 1, _used_rows(sheet) + 1):
        v = sheet.Cells(r, model_col).Value
        if isinstance(v, str) and "R3800FT20" in v:
            return True
    return False


def _ft20_update_summary_desc(wb, items, res) -> bool:
    """Replace the 描述 cell of the FT20 row in 价格汇总表 with template desc."""
    sheet = _try_get_sheet(wb, _SHEET_PRICE_MAIN)
    if sheet is None:
        return False
    header_row, headers = _find_header_row(sheet)
    if header_row is None:
        return False
    model_col = headers.get("产品型号")
    desc_col = headers.get("描述")
    if model_col is None or desc_col is None:
        res.warnings.append("汇总表缺 产品型号 / 描述 列")
        return False

    new_desc = ";\n".join(item.description for item in items) + ";"
    updated = False
    for r in range(header_row + 1, _used_rows(sheet) + 1):
        model = sheet.Cells(r, model_col).Value
        if not isinstance(model, str) or "R3800FT20" not in model:
            continue
        sheet.Cells(r, desc_col).Value = new_desc
        res.changes.append(f"汇总 R{r}: 描述写入 {len(items)} 项 (来自模板)")
        updated = True
    if not updated:
        res.warnings.append("汇总表没找到 R3800FT20 行")
    return updated


def _ft20_replace_detail_section(wb, items, res) -> bool:
    """
    Resize the R3800FT20G3 BOM section in 价格明细清单 to N template
    rows. Per-row formulas (单价 / 折扣 / 总价 / 目录总价) survive via
    Range.FillDown(), so the existing row's formula scaffolding is
    propagated to new rows automatically.
    """
    sheet = _try_get_sheet(wb, _SHEET_PRICE_DETAIL)
    if sheet is None:
        res.warnings.append("没有 价格明细清单 sheet,跳过明细填充")
        return False

    header_row, headers = _find_header_row(sheet)
    if header_row is None:
        return False
    desc_col = headers.get("描述")
    if desc_col is None:
        res.warnings.append("明细清单缺 描述 列")
        return False

    code_col = headers.get("产品编码")
    model_col = headers.get("产品型号")
    pcode_col = headers.get("产品代码")
    qty_col = headers.get("数量")
    price_col = headers.get("目录单价(RMB)") or headers.get("目录单价")
    last_col = max(headers.values())

    # ---- Locate the FT20 section -----------------------------------------
    # The H3C 配置器 puts a subheader row with "<group> #N" in col-2 just
    # above the data rows. There may be TWO matching rows (the parent
    # group "1 | 服务器" line above, and the sub-group "1_1 | R3800FT20G3
    # #1"). We want the SUB-group line — it's the one immediately above
    # the actual BOM data.
    subheader_r = None
    for r in range(header_row + 1, _used_rows(sheet) + 1):
        v = sheet.Cells(r, 2).Value
        if isinstance(v, str) and _R3800FT20_SECTION_RE.search(v):
            # Match the subheader specifically: col-1 has a "1_1"-style ID.
            col1 = sheet.Cells(r, 1).Value
            if col1 is not None and "_" in str(col1):
                subheader_r = r
                break
    if subheader_r is None:
        res.warnings.append("明细清单没找到 R3800FT20G3 #N 子段头")
        return False

    first_data_r = subheader_r + 1

    # Footer: first row at-or-below first_data_r whose 描述/产品编码 hits
    # one of the section-end markers.
    footer_r = None
    for r in range(first_data_r, _used_rows(sheet) + 1):
        v_desc = sheet.Cells(r, desc_col).Value
        v_code = sheet.Cells(r, 2).Value
        if isinstance(v_desc, str) and v_desc.strip() in _FT20_FOOTER_DESCS:
            footer_r = r; break
        if isinstance(v_code, str) and v_code.strip() in _FT20_FOOTER_CODES:
            footer_r = r; break
    if footer_r is None:
        res.warnings.append("明细清单没找到 R3800FT20G3 段结尾")
        return False

    existing_count = footer_r - first_data_r
    if existing_count <= 0:
        res.warnings.append(f"R3800FT20G3 段没有数据行 (subheader R{subheader_r}, footer R{footer_r})")
        return False

    target_count = len(items)
    diff = target_count - existing_count

    # ---- Resize the section ----------------------------------------------
    # COM constants we use here:
    _xl_shift_down = -4121
    if diff > 0:
        # Insert `diff` rows BEFORE the footer so footer SUM formulas
        # auto-extend their ranges to cover the new rows.
        for _ in range(diff):
            sheet.Rows(footer_r).Insert(Shift=_xl_shift_down)
        footer_r += diff
        # Newly inserted rows are blank; copy first_data_r's formulas/
        # formats down into them so single-row formulas like
        # `=H8*I8` become `=H9*I9`, `=H10*I10`, …
        target_block = sheet.Range(
            sheet.Cells(first_data_r, 1),
            sheet.Cells(first_data_r + target_count - 1, last_col),
        )
        target_block.FillDown()
    elif diff < 0:
        # Delete (existing - target) rows from the END of the data range
        # so we don't disturb the first row's formula template.
        sheet.Range(
            sheet.Cells(first_data_r + target_count, 1),
            sheet.Cells(first_data_r + existing_count - 1, last_col),
        ).EntireRow.Delete()
        footer_r -= -diff

    # ---- Write template data into each row -------------------------------
    # We write VALUES into the data cells; FORMULA cells (单价 / 折扣 /
    # 总价 / 目录总价) we leave alone so the FillDown'd formulas keep
    # referencing this-row's data.
    for i, item in enumerate(items):
        r = first_data_r + i
        if code_col:
            sheet.Cells(r, code_col).Value = item.code
        if model_col:
            sheet.Cells(r, model_col).Value = item.model
        if pcode_col:
            sheet.Cells(r, pcode_col).Value = item.product_code
        sheet.Cells(r, desc_col).Value = item.description
        if qty_col:
            sheet.Cells(r, qty_col).Value = item.qty

        # 目录单价: prefer template's price; otherwise keep what FillDown
        # propagated from the existing first row, BUT only for the first
        # row (which is the bundle SKU 9801A27M and legitimately holds the
        # bundle's total). Clear it on subsequent rows so we don't over-
        # multiply the bundle price by N quantities.
        if price_col:
            if item.list_price is not None:
                sheet.Cells(r, price_col).Value = item.list_price
            elif i > 0:
                sheet.Cells(r, price_col).Value = None

    res.changes.append(
        f"明细 R3800FT20G3: {existing_count} → {target_count} 行 "
        f"(R{first_data_r}-R{first_data_r + target_count - 1})"
    )
    return True


# ---------------------------------------------------------------------------
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
