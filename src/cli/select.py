"""
Subcommand: `select` — product selection from free-form requirements.

    python -m src.cli select "48口万兆三层核心交换机"
    python -m src.cli select --doc requirements.docx
    python -m src.cli select --image spec.png --ai
    python -m src.cli select "国产化接入交换机 24口千兆 PoE" --ai --top 3

Backwards compatibility: `python -m src.cli.select "..."` still works.
"""

from __future__ import annotations

from pathlib import Path

import click

from ._common import console, setup_logging
from ..config import get_config
from ..requirement.schema import parse_requirement
from ..requirement.rule_parser import _iter_field_lines
from ..selector import AIMatcher, RuleMatcher
from ..selector.base import MatchResult


# ---------------------------------------------------------------------------
# Rendering helpers (kept module-local; not part of the public API)
# ---------------------------------------------------------------------------
def _render_requirement(req) -> None:
    from rich.panel import Panel
    body = "\n".join(_iter_field_lines(req)) or "[未解析到结构化字段]"
    console.print(Panel(body, title="解析后的需求", border_style="cyan"))


def _render_results(results: list[MatchResult]) -> None:
    from rich.panel import Panel
    from rich.table import Table

    if not results:
        console.print(Panel(
            "[red]没有产品匹配该需求[/red]\n请检查目录是否已抓取,或放宽部分条件再试。",
            title="结果", border_style="red"))
        return

    tbl = Table(title=f"推荐产品 Top {len(results)}",
                show_header=True, header_style="bold magenta")
    tbl.add_column("#", style="dim", width=3)
    tbl.add_column("型号", style="bold")
    tbl.add_column("评分", justify="right")
    tbl.add_column("类别")
    tbl.add_column("端口")
    tbl.add_column("层级")
    tbl.add_column("国产", justify="center")
    for i, r in enumerate(results, start=1):
        p = r.product
        ports = "-"
        if p.port_count and p.port_speed:
            ports = f"{p.port_count}×{p.port_speed}"
        elif p.port_count:
            ports = f"{p.port_count}口"
        tbl.add_row(
            str(i), p.model, f"{r.score:.2%}",
            p.category or "-", ports, p.layer or "-",
            "✓" if p.is_domestic else "",
        )
    console.print(tbl)

    for i, r in enumerate(results, start=1):
        body_lines = [f"[green]• {x}[/green]" for x in r.reasons]
        body_lines += [f"[yellow]⚠ {x}[/yellow]" for x in r.warnings]
        if r.product.page_url:
            body_lines.append(f"[dim]产品页:{r.product.page_url}[/dim]")
        console.print(Panel(
            "\n".join(body_lines),
            title=f"#{i}  {r.product.model}   评分 {r.score:.2%}",
            border_style="green" if r.score >= 0.7 else "yellow",
        ))


# ---------------------------------------------------------------------------
# Click command — registered by main.py into the top-level group.
# ---------------------------------------------------------------------------
@click.command(name="select", help="根据需求(文本/文档/图片)推荐 UNIS 产品。")
@click.argument("text", required=False)
@click.option("--doc", "document",
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="上传文档(PDF/DOCX/XLSX/TXT/CSV/MD)作为需求来源")
@click.option("--image",
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="上传图片作为需求来源(需 --ai 且配置 Claude key)")
@click.option("--ai/--no-ai", default=None,
              help="是否启用 AI 模式(默认按 config.yaml 决定)")
@click.option("--top", "top_k", type=int, default=None, help="返回 Top N 产品")
@click.option("--section",
              type=click.Choice(["innovation", "general"], case_sensitive=False),
              default=None,
              help="限定区:innovation=创新型 / general=通用型")
@click.option("--catalog", "catalog_name", default=None,
              help="限定到某份名录(对应 catalog import 时的 --name)")
def cmd(text, document, image, ai, top_k, section, catalog_name):
    setup_logging()
    cfg = get_config()

    if not (text or document or image):
        click.echo("请提供至少一种输入:文本参数、--doc 文件、或 --image 图片。")
        raise SystemExit(2)

    if image and not ai:
        console.print(
            "[yellow]提示:图片输入需要 --ai 才能解析。当前忽略图片,仅按文本/文档解析。[/yellow]"
        )

    use_ai = ai if ai is not None else (cfg.selector.default_mode == "ai")
    top_k = top_k or cfg.selector.top_k
    section = section.lower() if section else None

    with console.status("解析需求中..."):
        req = parse_requirement(
            text=text,
            document_path=str(document) if document else None,
            image_path=str(image) if image else None,
            use_ai=use_ai,
        )
    _render_requirement(req)

    if req.is_empty():
        console.print(
            "[red]未能从输入中解析出可用约束。请尝试加上端口数、速率、层级等关键参数。[/red]"
        )
        raise SystemExit(1)

    # Apply scope to the underlying RuleMatcher (AIMatcher delegates to it).
    if use_ai:
        matcher = AIMatcher()
        matcher.rule.section = section
        matcher.rule.catalog_name = catalog_name
    else:
        matcher = RuleMatcher(section=section, catalog_name=catalog_name)

    scope_bits = []
    if section: scope_bits.append(f"section={section}")
    if catalog_name: scope_bits.append(f"catalog={catalog_name}")
    scope_str = f" [{', '.join(scope_bits)}]" if scope_bits else ""

    with console.status(f"匹配产品中(引擎: {'AI' if use_ai else '规则'}){scope_str}..."):
        results = matcher.match(req, top_k=top_k)
    _render_results(results)


# Back-compat: keep `python -m src.cli.select "..."` working
if __name__ == "__main__":
    cmd()
