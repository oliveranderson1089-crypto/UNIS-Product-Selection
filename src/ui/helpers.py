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
import shutil
import tempfile
from datetime import datetime
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
    project_ref: str | None = None,
    track_version: bool = True,
    notes: str | None = None,
    archive_to_project: bool = False,
) -> tuple[str, str | None]:
    """
    Gradio callback for quote format.

    `project_ref`: optional project id ("12") or name to link the resulting
        QuoteVersion to. Empty string / None → fall back to path-based
        auto-inference, then filename-based inference.
    `track_version`: if False, skip writing the QuoteVersion row entirely.
    `archive_to_project`: if True AND a project gets linked, copy the
        .formatted.xlsx into that project's folder.

    Returns (markdown_report, downloadable_file_path).
    """
    if not input_path:
        return ("❌ 请选择一个 .xlsx 报价单。", None)

    from pathlib import Path
    from ..quotes import DEFAULT_RULES, format_quote
    from ..quotes.exceptions import QuoteError
    from ..quotes.workspace import stage_input

    skip_names = set()
    if skip_logo: skip_names.add("remove_h3c_logo")
    if skip_columns: skip_names.add("drop_fixed_columns")
    if skip_model_fill: skip_names.add("fill_empty_model")
    if skip_server_cleanup: skip_names.add("drop_internal_server_components")
    if skip_oem_swap: skip_names.add("swap_oem_service_line")
    if skip_ft20_template: skip_names.add("fill_r3800ft20_template")

    rules = [r for r in DEFAULT_RULES if r.name not in skip_names]

    # Stage the upload into data/quote_workspace/<ts>__<stem>/ so Excel
    # COM doesn't choke on Gradio's AppData/Local/Temp paths. The output
    # ends up in the same workspace subdir alongside the source.
    user_upload = Path(input_path)
    try:
        src = stage_input(user_upload)
    except Exception as exc:                                          # noqa: BLE001
        logger.exception("stage_input failed")
        return (f"❌ **暂存输入文件失败:** `{exc}`", None)
    out = src.with_name(src.stem + ".formatted.xlsx")

    try:
        report = format_quote(src, out, rules=rules)
    except QuoteError as exc:
        return (f"❌ **格式化失败:** `{exc}`", None)
    except Exception as exc:                                      # noqa: BLE001
        logger.exception("format_quote crashed")
        return (f"❌ **意外错误:** `{exc}`", None)

    # ---- Record QuoteVersion (best-effort, never breaks the format) ----
    version_md = ""
    archive_md = ""
    summary = None
    if track_version:
        from ..projects import record_quote_version
        ref = (project_ref or "").strip() or None
        summary = record_quote_version(
            report,
            project_ref=ref,
            auto_infer=(ref is None),
            notes=(notes or "").strip() or None,
        )
        if summary is None:
            version_md = "\n> ⚠️ 版本记录写入失败(已忽略,见日志)"
        elif summary.project_id is not None:
            version_md = (
                f"\n> 📌 已记录为版本 **#{summary.id}**,关联项目 "
                f"`[{summary.project_id}] {summary.project_name}`"
            )
        else:
            version_md = (
                f"\n> 📌 已记录为版本 **#{summary.id}**(未关联项目)。"
                f"可在「项目管理」标签里手动关联,或用 "
                f"`quote versions link {summary.id} <project>` 命令。"
            )
    else:
        version_md = "\n> ⏭️ 已跳过版本记录(取消勾选「记录此次版本」)"

    # ---- Archive to project folder (only if version + project both OK)
    if (
        track_version and archive_to_project and summary is not None
        and summary.project_id is not None
    ):
        from ..projects import archive_quote_to_project
        dest = archive_quote_to_project(summary.id)
        if dest is not None:
            archive_md = (
                f"\n> 📂 已归档到项目文件夹:`{dest}`"
            )
        else:
            archive_md = (
                "\n> ⚠️ 归档失败 — 项目文件夹可能已挪走或输出文件丢失"
                "(详见日志)"
            )
    elif archive_to_project and track_version and (
        summary is None or summary.project_id is None
    ):
        archive_md = (
            "\n> ⏭️ 跳过归档:没有关联到任何项目"
            "(打开自动关联或手动选项目即可)"
        )

    lines = [
        f"✅ **格式化完成** — 应用 **{report.applied_count}** / {len(report.rule_results)} 条规则"
        + version_md + archive_md,
        "",
        f"- 输入(原始): `{user_upload.name}`",
        f"- 输入(工作区): `{src}`",
        f"- 输出: `{out.name}` ({out.stat().st_size // 1024} KB)",
        f"- 工作区目录: `{src.parent}`",
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


def list_project_picker_choices(include_none: bool = True) -> list[tuple[str, str]]:
    """
    (label, value) pairs for picking a project in dropdowns.

    Value is the project id as a string; label is "[id] assigner/name (customer)".
    First option (when include_none=True) is the "auto-infer / none" sentinel.
    """
    from ..projects import list_projects
    out: list[tuple[str, str]] = []
    if include_none:
        out.append(("(自动按文件路径推断)", ""))
    for p in list_projects():
        label = f"[{p.id}] {p.assigner}/{p.name}"
        if p.customer:
            label += f" — {p.customer}"
        out.append((label, str(p.id)))
    return out


def scan_and_refresh_project_choices() -> tuple[object, str]:
    """
    Walk the work_dir, upsert any new project folders found on disk, then
    return a fresh dropdown choices update + a one-line status markdown.

    Kept for backward compat / single-tab callers. The new cross-tab path
    is `global_scan_and_refresh` below.
    """
    try:
        from ..projects import scan_projects
        report = scan_projects()
    except Exception as exc:                                          # noqa: BLE001
        logger.exception("scan_projects failed")
        import gradio as gr
        return (
            gr.update(choices=list_project_picker_choices()),
            f"❌ 扫描失败: `{exc}`",
        )

    import gradio as gr
    status_bits = [
        f"✅ 已扫描 `{report.work_dir}`",
        f"项目: {report.projects_seen}(新增 **{report.projects_new}**)",
        f"文件: {report.files_total}(新增 {report.files_new},删除 {report.files_removed})",
    ]
    status = " · ".join(status_bits)
    return (
        gr.update(choices=list_project_picker_choices()),
        status,
    )


# ---------------------------------------------------------------------------
# Cross-tab unified scan + refresh
# ---------------------------------------------------------------------------
# These power the "click refresh anywhere → both tabs reflect the new
# state of disk + DB" UX. Same backend (scan_projects), but the return
# tuples are sized to match the wiring in app.py.

def _build_scan_status_md(report) -> str:
    """Pretty markdown panel for the projects-tab scan status area."""
    return (
        f"✅ **扫描完成** — `{report.work_dir}`\n\n"
        f"- 人员目录: {report.assigners_seen}\n"
        f"- 项目数: {report.projects_seen}  (新增 **{report.projects_new}**)\n"
        f"- 文件数: {report.files_total}  (新增 **{report.files_new}**,"
        f"已删 {report.files_removed})"
    )


def _build_scan_status_inline(report) -> str:
    """One-line status for the quote-tab inline status spot."""
    return (
        f"✅ 已扫描 `{report.work_dir}` · "
        f"项目: {report.projects_seen}(新增 **{report.projects_new}**) · "
        f"文件: {report.files_total}(新增 {report.files_new},"
        f"已删 {report.files_removed})"
    )


def global_scan_and_refresh(
    assigner: str | None,
    status: str | None,
    search: str | None,
    current_picker_value: str | None,
    current_page: int,
):
    """
    Manual-refresh handler shared by both tabs.

    Runs one disk scan, then returns updates for EVERY refreshable
    component across both tabs. Page state is preserved (clamped if
    the total page count shrank).

    Returns (must match the outputs= list in app.py):
      0. quote tab: project_pick choices update
      1. quote tab: scan_status_md text
      2. projects tab: scan_md (large panel above filter row)
      3. projects tab: list_md (paginated)
      4. projects tab: project_picker choices update
      5. projects tab: detail_md (re-rendered if a project is picked)
      6. projects tab: page_info_md
      7. projects tab: page_state (clamped current page)
    """
    import gradio as gr
    try:
        from ..projects import scan_projects
        report = scan_projects()
        inline_status = _build_scan_status_inline(report)
        panel_status = _build_scan_status_md(report)
    except Exception as exc:                                          # noqa: BLE001
        logger.exception("global_scan_and_refresh failed")
        inline_status = f"❌ 扫描失败: `{exc}`"
        panel_status = inline_status

    detail_update: object
    if current_picker_value:
        detail_update = show_project_md(current_picker_value)
    else:
        detail_update = gr.update()  # leave as-is

    list_md, page_info, total_pages = list_projects_md(
        assigner, status, search, page=current_page,
    )
    # Clamp page state so the indicator stays consistent if scan shrank
    # the result set.
    clamped_page = max(1, min(int(current_page or 1), total_pages))

    return (
        gr.update(choices=list_project_picker_choices()),       # 0
        inline_status,                                          # 1
        panel_status,                                           # 2
        list_md,                                                # 3
        gr.update(choices=list_project_choices()),              # 4
        detail_update,                                          # 5
        page_info,                                              # 6
        clamped_page,                                           # 7
    )


def silent_scan_for_timer(
    assigner: str | None,
    status: str | None,
    search: str | None,
    current_page: int,
):
    """
    Background timer handler — scans silently, returns only the
    dropdown-list updates. Skips status banners and the detail panel
    so the UI doesn't flicker while users are reading.

    Returns:
      0. quote tab: project_pick choices update
      1. projects tab: list_md (paginated)
      2. projects tab: project_picker choices update
      3. projects tab: page_info_md
      4. projects tab: page_state (clamped)
    """
    import gradio as gr
    try:
        from ..projects import scan_projects
        scan_projects()
    except Exception:                                                 # noqa: BLE001
        logger.exception("silent scan failed")

    list_md, page_info, total_pages = list_projects_md(
        assigner, status, search, page=current_page,
    )
    clamped_page = max(1, min(int(current_page or 1), total_pages))

    return (
        gr.update(choices=list_project_picker_choices()),
        list_md,
        gr.update(choices=list_project_choices()),
        page_info,
        clamped_page,
    )


def render_quote_versions_md(project_id: int) -> str:
    """Markdown table of recent QuoteVersion rows for one project."""
    from pathlib import Path
    from ..projects import list_quote_versions
    rows = list_quote_versions(project_id=project_id, limit=20)
    if not rows:
        return "_(本项目还没有任何报价版本记录。下次在「报价单编辑」标签格式化时会自动记录。)_"

    lines = [
        f"### 📋 报价版本历史 (最近 {len(rows)} 条)",
        "",
        "| #ID | 生成时间 | 源文件 | 规则 | 方式 | 归档文件 | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    for v in rows:
        notes = (v.notes or "").replace("|", "/")[:30]
        if v.archived_path:
            arch_path = Path(v.archived_path)
            archived = f"✅ `{arch_path.name}`"
        else:
            archived = "⬜ _未归档_"
        lines.append(
            f"| **{v.id}** | {v.generated_at:%Y-%m-%d %H:%M} | "
            f"`{v.source_filename}` | {v.applied_count}/{v.total_rules} | "
            f"{v.formatter_method} | {archived} | {notes} |"
        )
    return "\n".join(lines)


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


PROJECTS_PAGE_SIZE = 10


def list_projects_md(
    assigner: str | None,
    status: str | None,
    search: str | None,
    page: int = 1,
) -> tuple[str, str, int]:
    """
    Render projects table as Markdown, paginated.

    Returns:
      (list_md, page_info_md, total_pages)
    `total_pages` is exposed so the wiring code can clamp the page
    state after filter changes.
    """
    from ..projects import list_projects

    items = list_projects(
        assigner=(assigner or None),
        status=(status or None),
        search=(search or None),
    )
    if not items:
        return (
            "_没有匹配的项目。先点上面的 `🔄 扫描` 按钮?_",
            "",
            1,
        )

    total = len(items)
    total_pages = max(1, (total + PROJECTS_PAGE_SIZE - 1) // PROJECTS_PAGE_SIZE)
    page = max(1, min(int(page or 1), total_pages))
    start = (page - 1) * PROJECTS_PAGE_SIZE
    end = min(start + PROJECTS_PAGE_SIZE, total)
    page_items = items[start:end]

    status_emoji = {
        "进行中": "🟡 进行中", "中标": "🟢 中标",
        "未中标": "🔴 未中标", "结案": "⚪ 结案",
    }
    lines = [
        f"**共 {total} 个项目** · 当前第 {start + 1}–{end} 条",
        "",
        "| ID | 状态 | 下发人 | 代码 | 全称 | 客户 | 文件数 | 终版 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in page_items:
        lines.append(
            f"| {p.id} | {status_emoji.get(p.status, p.status)} | "
            f"{p.assigner} | `{p.name}` | "
            f"{(p.display_name or '')[:40]} | "
            f"{p.customer or '-'} | {p.file_count} | "
            f"{'✅' if p.has_final_quote else ''} |"
        )

    page_info = f"**第 {page} / {total_pages} 页** (每页 {PROJECTS_PAGE_SIZE} 条)"
    return ("\n".join(lines), page_info, total_pages)


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

    # ---- Recorded quote-format versions for this project ----------------
    lines += ["", render_quote_versions_md(proj.id)]

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


# ---------------------------------------------------------------------------
# References folder management (data/References/)
# ---------------------------------------------------------------------------
# This is where the quote rules look for support data:
#   - IT产品BOM编码*.xlsx        — swap_oem_service_line lookup
#   - R3800FT20 G3配置模板*.xlsx  — fill_r3800ft20_template
#   - *.pdf                       — catalog source PDFs
# The 参考文件管理 tab exposes basic CRUD on this directory.

# Extensions we accept via the upload box. Anything else gets rejected
# so users don't accidentally drop a stray Excel temp file (~$*.xlsx) or
# a Word doc and wonder why nothing matches.
_REFERENCE_ALLOWED_SUFFIXES = {".xlsx", ".xls", ".xlsm", ".pdf"}


def _references_dir() -> Path:
    from ..config import PROJECT_ROOT
    return PROJECT_ROOT / "data" / "References"


def _classify_reference(name: str) -> str:
    """Friendly category label keyed off filename patterns."""
    n = name
    nlow = name.lower()
    if "bom" in nlow:
        return "📋 IT产品BOM"
    if "r3800ft20" in nlow.replace(" ", ""):
        return "🎯 FT20 模板(已选型)" if "已选" in n else "📐 FT20 模板(空)"
    if "名录" in n or "承诺函" in n or nlow.endswith(".pdf"):
        return "📄 名录/承诺函"
    if nlow.endswith((".xlsx", ".xls", ".xlsm")):
        return "📊 Excel"
    return "📦 其他"


def _imported_catalog_by_pdf_name() -> dict[str, str]:
    """
    Build {pdf_basename: catalog_name} from the catalog DB.

    PDFs were imported into SQLite (CatalogList rows). Their `source_file`
    column was the path at import time (often Downloads), so the file may
    no longer live there. We key off basename so a PDF moved into
    References/ still matches the catalog it became.
    """
    try:
        from sqlalchemy import select
        from ..storage import get_db
        from ..storage.models import CatalogList
    except Exception:                                                 # noqa: BLE001
        return {}
    try:
        db = get_db()
        out: dict[str, str] = {}
        with db.session() as s:
            for c in s.scalars(select(CatalogList)):
                if c.source_file:
                    out[Path(c.source_file).name] = c.name
        return out
    except Exception:                                                 # noqa: BLE001
        logger.exception("failed to query CatalogList")
        return {}


def list_references_md() -> str:
    """Markdown table of every file in data/References/."""
    from ..config import get_config
    from ..quotes.bom_lookup import resolve_bom_path
    from ..quotes.r3800ft20_template import resolve_template_path

    refs_dir = _references_dir()
    if not refs_dir.exists():
        return f"_目录不存在: `{refs_dir}`(上传第一份文件时会自动创建)_"

    files = sorted(
        [p for p in refs_dir.iterdir() if p.is_file()],
        key=lambda p: -p.stat().st_mtime,
    )
    if not files:
        return f"_`{refs_dir}` 是空的。下方面板上传第一份文件。_"

    cfg = get_config()
    active: set[Path] = set()
    bom = resolve_bom_path(cfg.quotes.bom_path)
    ft20 = resolve_template_path(cfg.quotes.r3800ft20_template_path)
    if bom is not None:
        active.add(bom.resolve())
    if ft20 is not None:
        active.add(ft20.resolve())

    # PDFs that have been imported into the catalog DB.
    pdf_to_catalog = _imported_catalog_by_pdf_name()

    lines = [
        f"### 📂 `data/References/` — 共 {len(files)} 份(按修改时间倒序)",
        "",
        "| 文件名 | 类型 | 大小 | 修改时间 | 生效 / 备注 |",
        "|---|---|---|---|---|",
    ]
    for p in files:
        st = p.stat()
        size_kb = st.st_size // 1024
        mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        kind = _classify_reference(p.name)

        if p.suffix.lower() == ".pdf":
            cat_name = pdf_to_catalog.get(p.name)
            badge = f"✅ 已导入为名录: **{cat_name}**" if cat_name else "⬜ 未导入"
        else:
            badge = "✅" if p.resolve() in active else ""
        lines.append(
            f"| `{p.name}` | {kind} | {size_kb} KB | {mtime} | {badge} |"
        )
    return "\n".join(lines)


def list_reference_filenames() -> list[str]:
    """For populating the delete-picker dropdown."""
    refs_dir = _references_dir()
    if not refs_dir.exists():
        return []
    return sorted([p.name for p in refs_dir.iterdir() if p.is_file()])


def list_reference_pdfs() -> list[str]:
    """PDF filenames in data/References/ — for the catalog-import picker."""
    refs_dir = _references_dir()
    if not refs_dir.exists():
        return []
    return sorted(
        p.name for p in refs_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


def reference_pdf_path(name: str) -> Path | None:
    """Resolve a References PDF filename to its absolute path."""
    if not name:
        return None
    p = _references_dir() / name
    return p if p.exists() else None


def references_status_md() -> str:
    """Show which exact file each rule will pick + imported-catalog status."""
    from ..config import get_config
    from ..quotes.bom_lookup import resolve_bom_path
    from ..quotes.r3800ft20_template import resolve_template_path

    cfg = get_config()
    bom = resolve_bom_path(cfg.quotes.bom_path)
    ft20 = resolve_template_path(cfg.quotes.r3800ft20_template_path)

    def _line(label: str, p: Path | None, hint: str) -> str:
        if p is None:
            return (
                f"- **{label}**: ❌ _没找到_  — 对应规则会跳过\n"
                f"  - 期望路径: `{hint}`"
            )
        return f"- **{label}**: `{p.name}`"

    lines = [
        "#### 🎯 当前生效的参考文件",
        "",
        "**报价规则用(运行时直读磁盘)**",
        _line(
            "OEM 维保 BOM (swap_oem_service_line)",
            bom, cfg.quotes.bom_path or "(未配置)",
        ),
        _line(
            "R3800FT20 模板 (fill_r3800ft20_template)",
            ft20, cfg.quotes.r3800ft20_template_path or "(未配置)",
        ),
    ]

    # Imported catalogs — these were one-time loaded into SQLite; the PDF
    # itself just sits here as audit/reference now.
    pdf_to_catalog = _imported_catalog_by_pdf_name()
    if pdf_to_catalog:
        lines += ["", "**名录(一次性导入数据库,后续从 DB 读)**"]
        for pdf, cat in sorted(pdf_to_catalog.items()):
            in_refs = (_references_dir() / pdf).exists()
            mark = "(✅ 在 References/)" if in_refs else "(⚠️ 不在 References/,原文件可能已挪走)"
            lines.append(f"- **{cat}** ← `{pdf}` {mark}")
    else:
        lines += [
            "",
            "**名录** — _还没有导入任何名录。去「名录管理」标签导入。_",
        ]

    return "\n".join(lines)


def upload_reference_ui(uploaded_path: str | None) -> str:
    """
    Copy a Gradio-uploaded file into data/References/ under its original
    name. Overwrites if a file with the same name already exists — by
    design, since users typically replace BOM monthly under same filename
    convention with a new date stamp.
    """
    if not uploaded_path:
        return "❌ 请先选一份文件。"
    src = Path(uploaded_path)
    if not src.exists():
        return f"❌ 找不到上传的临时文件: `{src}`"

    if src.suffix.lower() not in _REFERENCE_ALLOWED_SUFFIXES:
        allowed = " / ".join(sorted(_REFERENCE_ALLOWED_SUFFIXES))
        return f"❌ 不支持的文件类型 `{src.suffix}`。允许的类型: {allowed}"

    if src.name.startswith("~$"):
        return "❌ 这是 Excel 临时锁文件(`~$` 开头),不要上传。"

    refs_dir = _references_dir()
    refs_dir.mkdir(parents=True, exist_ok=True)
    dest = refs_dir / src.name

    existed = dest.exists()
    try:
        shutil.copy(src, dest)
    except Exception as exc:                                          # noqa: BLE001
        logger.exception("upload_reference copy failed")
        return f"❌ 拷贝失败: `{exc}`"

    # Invalidate the BOM cache so the new file gets picked up immediately
    # without restarting the UI.
    try:
        from ..quotes.bom_lookup import _load_cached
        _load_cached.cache_clear()
    except Exception:                                                 # noqa: BLE001
        pass

    verb = "🔁 覆盖" if existed else "✅ 上传"
    size_kb = dest.stat().st_size // 1024
    return f"{verb}成功:`{dest.name}` ({size_kb} KB) → `data/References/`"


def delete_reference_ui(filename: str | None, confirmed: bool) -> str:
    """Permanently delete a file from data/References/. Requires confirm."""
    if not filename:
        return "_先在下拉里选一份文件。_"
    if not confirmed:
        return "⚠️ 删除前请先勾上「我确认要永久删除」。"

    refs_dir = _references_dir().resolve()
    target = (refs_dir / filename).resolve()

    # Defense against path traversal — must resolve to a direct child of
    # data/References/ AND it must exist as a regular file.
    if target.parent != refs_dir:
        return f"❌ 安全检查失败:`{filename}` 不在 References/ 下"
    if not target.exists():
        return f"❌ 文件不存在: `{filename}`"
    if not target.is_file():
        return f"❌ 不是文件: `{filename}`"

    try:
        target.unlink()
    except Exception as exc:                                          # noqa: BLE001
        logger.exception("delete_reference failed")
        return f"❌ 删除失败: `{exc}`"

    # Invalidate the BOM cache so a re-run picks the fallback file.
    try:
        from ..quotes.bom_lookup import _load_cached
        _load_cached.cache_clear()
    except Exception:                                                 # noqa: BLE001
        pass

    return f"✅ 已永久删除 `{filename}`"


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
    # references
    "list_references_md",
    "list_reference_filenames",
    "list_reference_pdfs",
    "reference_pdf_path",
    "references_status_md",
    "upload_reference_ui",
    "delete_reference_ui",
    # quote versions
    "list_project_picker_choices",
    "scan_and_refresh_project_choices",
    "global_scan_and_refresh",
    "silent_scan_for_timer",
    "render_quote_versions_md",
]
