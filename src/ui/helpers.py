"""
Shared business logic for the Web UI.

The pages (select / catalog mgmt) call these helpers so the UI layer
stays cosmetic — all real logic lives in src.selector / src.catalog_lists.

Returns Markdown strings whenever possible, because gr.Markdown() is the
most compatible Gradio component for showing structured but compact text.
"""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path
from typing import Iterable

from ..requirement.schema import parse_requirement
from ..requirement.rule_parser import _iter_field_lines
from ..selector import AIMatcher, RuleMatcher
from ..selector.base import MatchResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def run_selection(
    *,
    text: str | None,
    document_path: str | None,
    image_path: str | None,
    use_ai: bool,
    section: str | None,
    catalog_name: str | None,
    top_k: int,
) -> tuple[str, str]:
    """
    Entry point used by every selection page.

    Returns a `(requirement_md, results_md)` pair for two side-by-side
    Markdown panels in the UI.
    """
    text = (text or "").strip() or None
    if not (text or document_path or image_path):
        return ("", "_请至少提供文本、文档或图片中的一种作为需求输入。_")

    if image_path and not use_ai:
        warning = (
            "> ⚠️ 图片输入需要 AI 模式才能解析(走 Claude vision)。"
            "当前忽略图片,仅处理文本/文档。\n\n"
        )
    else:
        warning = ""

    try:
        req = parse_requirement(
            text=text,
            document_path=document_path,
            image_path=image_path,
            use_ai=use_ai,
        )
    except Exception as exc:                                          # noqa: BLE001
        logger.exception("parse_requirement failed")
        return ("", f"❌ **需求解析失败**:`{exc}`")

    req_md = _render_requirement(req)

    if req.is_empty():
        return (req_md, "未能从输入中解析出任何可用约束。请尝试加上端口数、速率、层级等关键参数。")

    # Build matcher with scope applied.
    if use_ai:
        matcher = AIMatcher()
        matcher.rule.section = section
        matcher.rule.catalog_name = catalog_name
    else:
        matcher = RuleMatcher(section=section, catalog_name=catalog_name)

    try:
        results = matcher.match(req, top_k=top_k)
    except Exception as exc:                                          # noqa: BLE001
        logger.exception("matcher failed")
        return (req_md, f"❌ **匹配引擎报错**:`{exc}`")

    results_md = warning + _render_results(results, scope_label=_scope_label(section, catalog_name))
    return (req_md, results_md)


# ---------------------------------------------------------------------------
# Pretty rendering
# ---------------------------------------------------------------------------
def _render_requirement(req) -> str:
    lines = list(_iter_field_lines(req))
    body = "\n".join(f"- `{ln}`" for ln in lines) or "_(没有结构化字段)_"
    return f"#### 🎯 解析后的需求\n\n{body}"


def _render_results(results: list[MatchResult], *, scope_label: str = "") -> str:
    header = "#### 📋 推荐产品"
    if scope_label:
        header += f"  <small>{scope_label}</small>"

    if not results:
        return (
            f"{header}\n\n"
            "**没有产品匹配该需求。** 可能原因:\n"
            "- 当前作用域内没有可用产品(检查 section / catalog 过滤)\n"
            "- 需求约束太严格,试着去掉一两个再选\n"
        )

    table_rows = ["| # | 型号 | 评分 | 类别 | 端口 | 层级 | 国产 |",
                  "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        p = r.product
        ports = "-"
        if p.port_count and p.port_speed:
            ports = f"{p.port_count}×{p.port_speed}"
        elif p.port_count:
            ports = f"{p.port_count}口"
        table_rows.append(
            f"| {i} | **{p.model}** | {r.score:.0%} | "
            f"{p.category or '-'} | {ports} | {p.layer or '-'} | "
            f"{'✓' if p.is_domestic else ''} |"
        )

    details = []
    for i, r in enumerate(results, 1):
        bullets = "\n".join(f"  - ✓ {x}" for x in r.reasons)
        warns = "\n".join(f"  - ⚠ {x}" for x in r.warnings)
        url_line = f"  - [产品页]({r.product.page_url})" if r.product.page_url else ""
        details.append(
            f"<details><summary>#{i}  <code>{r.product.model}</code>  &nbsp;评分 {r.score:.0%}</summary>\n\n"
            + (bullets or "_(无理由)_")
            + (("\n" + warns) if warns else "")
            + (("\n" + url_line) if url_line else "")
            + "\n</details>"
        )

    return f"{header}\n\n" + "\n".join(table_rows) + "\n\n" + "\n".join(details)


def _scope_label(section: str | None, catalog_name: str | None) -> str:
    bits = []
    if section: bits.append(f"section=`{section}`")
    if catalog_name: bits.append(f"catalog=`{catalog_name}`")
    return f"({', '.join(bits)})" if bits else ""


# ---------------------------------------------------------------------------
# Catalog management
# ---------------------------------------------------------------------------
def list_catalog_names() -> list[str]:
    """For populating the catalog-dropdown on the 名录型 page."""
    from sqlalchemy import select
    from ..storage import get_db
    from ..storage.models import CatalogList

    db = get_db()
    with db.session() as s:
        return [c.name for c in s.scalars(select(CatalogList).order_by(CatalogList.name))]


def list_catalogs_summary() -> str:
    """Markdown-rendered summary of all catalogs (count, match rate)."""
    from sqlalchemy import select
    from ..storage import get_db
    from ..storage.models import CatalogEntry, CatalogList

    db = get_db()
    with db.session() as s:
        cats = list(s.scalars(select(CatalogList).order_by(CatalogList.imported_at.desc())))
        if not cats:
            return "_还没有任何名录。在下方面板上传 PDF 导入。_"
        lines = ["| 名录 | 总型号 | 匹配率 | 抽取器 | 导入时间 |",
                 "|---|---|---|---|---|"]
        for c in cats:
            entries = list(s.scalars(
                select(CatalogEntry).where(CatalogEntry.catalog_id == c.id)
            ))
            total = len(entries)
            matched = sum(1 for e in entries if e.product_id is not None)
            pct = f"{matched}/{total}" + (f" ({matched/total:.0%})" if total else "")
            stamp = c.imported_at.strftime("%Y-%m-%d %H:%M") if c.imported_at else "-"
            lines.append(f"| **{c.name}** | {total} | {pct} | {c.extractor or '?'} | {stamp} |")
        return "\n".join(lines)


def import_catalog_via_ui(
    pdf_path: str | None,
    name: str,
    extractor: str | None,
    notes: str | None,
) -> str:
    """
    Gradio callback for catalog import.

    `pdf_path` is the temp file Gradio places on disk for uploaded files.
    Returns a Markdown-formatted import report.
    """
    if not pdf_path:
        return "❌ 请先选择一个 PDF 文件。"
    name = (name or "").strip()
    if not name:
        return "❌ 请填写名录名称(以后用 `--catalog NAME` 引用它)。"

    from ..catalog_lists import import_catalog
    try:
        report = import_catalog(
            Path(pdf_path), name=name,
            extractor=(extractor or None) if extractor and extractor.lower() != "auto" else None,
            notes=(notes or None),
            replace=True,
        )
    except Exception as exc:                                          # noqa: BLE001
        logger.exception("import_catalog failed")
        return f"❌ **导入失败:** `{exc}`"

    method_str = ", ".join(f"{k}={v}" for k, v in sorted(report.by_method.items())) or "-"
    md = [
        f"✅ **导入完成** — `{report.catalog_name}`",
        "",
        f"- 抽取器: `{report.extractor_used}`",
        f"- 总型号数: {report.total_codes}",
        f"- 已匹配产品: **{report.matched}** / 未匹配: {report.unmatched}",
        f"- 匹配方法: {method_str}",
    ]
    if report.unmatched_codes:
        md.append("")
        md.append("**未匹配型号** (产品库可能未爬到,或型号源不一致):")
        for c in report.unmatched_codes[:20]:
            md.append(f"  - `{c}`")
        if len(report.unmatched_codes) > 20:
            md.append(f"  - ... 还有 {len(report.unmatched_codes) - 20} 条")
        md.append("")
        md.append("> 抓取产品后,点击下面的 **重新匹配** 按钮自动重连。")
    return "\n".join(md)


def show_catalog_md(name: str) -> str:
    """Markdown table of one catalog's entries."""
    if not name:
        return "_选择一份名录查看详情。_"
    from sqlalchemy import select
    from ..storage import get_db
    from ..storage.models import CatalogEntry, CatalogList, Product

    db = get_db()
    with db.session() as s:
        cat = s.scalar(select(CatalogList).where(CatalogList.name == name))
        if cat is None:
            return f"❌ 找不到名录 `{name}`。"
        entries = list(s.scalars(
            select(CatalogEntry).where(CatalogEntry.catalog_id == cat.id)
        ))
        products = {p.id: p for p in s.scalars(select(Product))}

        lines = [f"### 📦 {cat.name}  <small>(共 {len(entries)} 条 · 抽取器 `{cat.extractor}`)</small>",
                 "",
                 "| # | 原始型号 | 匹配到产品 | 方法 | 类别 |",
                 "|---|---|---|---|---|"]
        for i, e in enumerate(entries, 1):
            p = products.get(e.product_id) if e.product_id else None
            matched = f"`{p.model}`" if p else "❌ 未匹配"
            lines.append(
                f"| {i} | `{e.raw_model_code}` | {matched} | "
                f"{e.match_method or '-'} | {p.category if p else '-'} |"
            )
        return "\n".join(lines)


def rematch_all_ui() -> str:
    """Trigger rematch_all and report."""
    from ..catalog_lists import rematch_all
    reports = rematch_all()
    if not reports:
        return "_还没有任何名录。_"
    lines = ["✅ **重新匹配完成**", ""]
    for name, r in reports.items():
        method_str = ", ".join(f"{k}={v}" for k, v in sorted(r.by_method.items()))
        lines.append(f"- **{name}**: {r.matched}/{r.total_codes} 匹配 ({method_str})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quote formatting
# ---------------------------------------------------------------------------
def format_quote_ui(
    input_path: str | None,
    skip_logo: bool,
    skip_columns: bool,
    skip_model_fill: bool,
    skip_server_cleanup: bool,
    skip_oem_swap: bool = False,
    skip_ft20_template: bool = False,
) -> tuple[str, str | None]:
    """
    Gradio callback for quote format.

    Returns (markdown_report, downloadable_file_path).
    """
    if not input_path:
        return ("❌ 请选择一个 .xlsx 报价单。", None)

    from pathlib import Path
    from ..quotes import DEFAULT_RULES, format_quote
    from ..quotes.exceptions import QuoteError

    skip_names = set()
    if skip_logo: skip_names.add("remove_h3c_logo")
    if skip_columns: skip_names.add("drop_fixed_columns")
    if skip_model_fill: skip_names.add("fill_empty_model")
    if skip_server_cleanup: skip_names.add("drop_internal_server_components")
    if skip_oem_swap: skip_names.add("swap_oem_service_line")
    if skip_ft20_template: skip_names.add("fill_r3800ft20_template")

    rules = [r for r in DEFAULT_RULES if r.name not in skip_names]
    src = Path(input_path)
    out = src.with_name(src.stem + ".formatted.xlsx")

    try:
        report = format_quote(src, out, rules=rules)
    except QuoteError as exc:
        return (f"❌ **格式化失败:** `{exc}`", None)
    except Exception as exc:                                      # noqa: BLE001
        logger.exception("format_quote crashed")
        return (f"❌ **意外错误:** `{exc}`", None)

    lines = [
        f"✅ **格式化完成** — 应用 **{report.applied_count}** / {len(report.rule_results)} 条规则",
        "",
        f"- 输入: `{src.name}`",
        f"- 输出: `{out.name}` ({out.stat().st_size // 1024} KB)",
    ]
    if report.conversion_method:
        emoji = "✨" if report.conversion_method == "com" else "⚠️"
        lines.append(f"- {emoji} 转换: `.xls` → `.xlsx` (方式: `{report.conversion_method}`)")
        for w in report.conversion_warnings:
            lines.append(f"  - ⚠️ {w}")
    lines.append("")
    for r in report.rule_results:
        emoji = "✅" if r.applied else ("➖" if not r.changes else "ℹ️")
        lines.append(f"### {emoji} `{r.name}`")
        if r.changes:
            for c in r.changes[:12]:
                lines.append(f"  - {c}")
            if len(r.changes) > 12:
                lines.append(f"  - _... 还有 {len(r.changes) - 12} 条改动_")
        for w in r.warnings:
            lines.append(f"  - ⚠️ {w}")
        if not r.changes and not r.warnings:
            lines.append("  - _(无变化)_")
        lines.append("")

    return ("\n".join(lines), str(out))


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
def scan_projects_ui() -> str:
    """Walk work_dir and upsert projects + files."""
    from ..projects import scan_projects

    r = scan_projects()
    return (
        f"✅ **扫描完成** — `{r.work_dir}`\n\n"
        f"- 人员目录: {r.assigners_seen}\n"
        f"- 项目数: {r.projects_seen}  (新增 **{r.projects_new}**)\n"
        f"- 文件数: {r.files_total}  (新增 **{r.files_new}**,已删 {r.files_removed})\n"
    )


def list_projects_md(assigner: str | None, status: str | None, customer: str | None) -> str:
    """Render projects table as Markdown."""
    from ..projects import list_projects

    items = list_projects(
        assigner=(assigner or None),
        status=(status or None),
        customer_like=(customer or None),
    )
    if not items:
        return "_没有匹配的项目。先点上面的 `🔄 扫描` 按钮?_"

    lines = [f"**共 {len(items)} 个项目**",
             "",
             "| ID | 状态 | 下发人 | 代码 | 全称 | 客户 | 文件数 | 终版 |",
             "|---|---|---|---|---|---|---|---|"]
    status_emoji = {
        "进行中": "🟡 进行中", "中标": "🟢 中标",
        "未中标": "🔴 未中标", "结案": "⚪ 结案",
    }
    for p in items:
        lines.append(
            f"| {p.id} | {status_emoji.get(p.status, p.status)} | "
            f"{p.assigner} | `{p.name}` | "
            f"{(p.display_name or '')[:40]} | "
            f"{p.customer or '-'} | {p.file_count} | "
            f"{'✅' if p.has_final_quote else ''} |"
        )
    return "\n".join(lines)


def list_project_choices() -> list[tuple[str, str]]:
    """For populating the project-picker dropdown.

    Returns a list of (label, value) tuples. Value is the project id.
    """
    from ..projects import list_projects

    items = list_projects()
    return [
        (f"[{p.id}] {p.assigner} / {p.name}"
         + (f"  ({p.customer})" if p.customer else ""),
         str(p.id))
        for p in items
    ]


def show_project_md(project_id: str | None) -> str:
    """Render one project's full details."""
    if not project_id:
        return "_左侧选一个项目查看详情。_"

    from ..projects import get_project

    found = get_project(project_id)
    if not found:
        return f"❌ 找不到项目 `{project_id}`。"
    proj, files = found

    status_emoji = {"进行中": "🟡", "中标": "🟢", "未中标": "🔴", "结案": "⚪"}
    kind_label = {
        "quote": "📊 报价单", "requirement": "📋 需求",
        "config": "⚙️ 配置", "image": "🖼️ 图片", "other": "📄 其他",
    }

    lines = [
        f"## {status_emoji.get(proj.status, '')} `{proj.name}` <small>(id={proj.id})</small>",
        f"**{proj.display_name or '(无全称)'}**",
        "",
        f"- 状态: **{proj.status}**",
        f"- 下发人: `{proj.assigner}`",
        f"- 客户: {proj.customer or '_(未填)_'}",
        f"- 文件夹: `{proj.folder_path}`",
        f"- 更新时间: {proj.updated_at.strftime('%Y-%m-%d %H:%M')}",
    ]
    if proj.notes:
        lines += ["", f"> {proj.notes}"]

    if files:
        lines += ["", f"### 📂 文件 ({len(files)})", "",
                  "| 类型 | 终版 | 文件名 | 大小 | 修改 |",
                  "|---|---|---|---|---|"]
        for f in files:
            size_kb = f"{f.size_bytes/1024:.1f} KB" if f.size_bytes else "-"
            mod = f.modified_at.strftime("%Y-%m-%d %H:%M") if f.modified_at else "-"
            lines.append(
                f"| {kind_label.get(f.kind, f.kind)} | "
                f"{'✅' if f.is_final else ''} | "
                f"`{f.name}` | {size_kb} | {mod} |"
            )
    else:
        lines += ["", "_(项目文件夹空)_"]

    return "\n".join(lines)


def update_project_status_ui(project_id: str | None, new_status: str) -> str:
    if not project_id:
        return "_先选一个项目。_"
    from ..projects import set_status
    try:
        proj = set_status(int(project_id), new_status)
    except ValueError as exc:
        return f"❌ {exc}"
    if proj is None:
        return f"❌ 找不到项目 id={project_id}"
    return f"✅ 已将 `{proj.name}` 状态更新为 **{new_status}**"


def update_project_customer_ui(project_id: str | None, customer: str) -> str:
    if not project_id:
        return "_先选一个项目。_"
    from ..projects import set_customer
    proj = set_customer(int(project_id), customer)
    if proj is None:
        return f"❌ 找不到项目 id={project_id}"
    return f"✅ 客户已更新为 `{proj.customer or '(已清空)'}`"


def update_project_notes_ui(project_id: str | None, notes: str) -> str:
    if not project_id:
        return "_先选一个项目。_"
    from ..projects import set_notes
    proj = set_notes(int(project_id), notes)
    if proj is None:
        return f"❌ 找不到项目 id={project_id}"
    return "✅ 备注已保存"


def open_project_ui(project_id: str | None) -> str:
    if not project_id:
        return "_先选一个项目。_"
    from ..projects import get_project, open_in_explorer
    found = get_project(int(project_id))
    if not found:
        return f"❌ 找不到项目 id={project_id}"
    proj, _ = found
    if open_in_explorer(proj.folder_path):
        return f"✅ 已唤起文件管理器: `{proj.folder_path}`"
    return f"❌ 打不开: `{proj.folder_path}`"


__all__ = [
    "run_selection",
    "list_catalog_names",
    "list_catalogs_summary",
    "import_catalog_via_ui",
    "show_catalog_md",
    "rematch_all_ui",
    # quotes
    "format_quote_ui",
    # projects
    "scan_projects_ui",
    "list_projects_md",
    "list_project_choices",
    "show_project_md",
    "update_project_status_ui",
    "update_project_customer_ui",
    "update_project_notes_ui",
    "open_project_ui",
]
