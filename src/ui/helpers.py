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


__all__ = [
    "run_selection",
    "list_catalog_names",
    "list_catalogs_summary",
    "import_catalog_via_ui",
    "show_catalog_md",
    "rematch_all_ui",
]
