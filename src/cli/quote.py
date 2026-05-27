"""
Subcommand group: `quote` — report-sheet editing.

    python -m src.cli quote format <input.xlsx>
    python -m src.cli quote format <input.xlsx> -o output.xlsx
    python -m src.cli quote format <input.xlsx> --skip remove_h3c_logo
    python -m src.cli quote format <input.xlsx> --only drop_fixed_columns
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.panel import Panel
from rich.table import Table

from ._common import console, setup_logging


@click.group(name="quote", help="报价单编辑 — 应用通用规则和服务器规则。")
def cmd() -> None:
    pass


@cmd.command("format", help="按预设规则格式化 H3C 配置器导出的 .xlsx。")
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", "output_path",
              type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="输出路径,默认 <input>.formatted.xlsx")
@click.option("--only", multiple=True,
              help="只跑指定规则(可多次,如 --only fill_empty_model --only remove_h3c_logo)")
@click.option("--skip", multiple=True,
              help="跳过指定规则")
def _format(input_path: Path, output_path: Path | None,
            only: tuple[str, ...], skip: tuple[str, ...]) -> None:
    setup_logging()
    from ..quotes import DEFAULT_RULES, format_quote
    from ..quotes.exceptions import QuoteError

    rules = list(DEFAULT_RULES)
    if only:
        rules = [r for r in rules if r.name in only]
        if not rules:
            console.print(f"[red]没有匹配的规则名 {list(only)}[/red]")
            console.print(f"可用: {[r.name for r in DEFAULT_RULES]}")
            raise SystemExit(2)
    if skip:
        rules = [r for r in rules if r.name not in skip]

    try:
        with console.status(f"格式化 {input_path.name}..."):
            report = format_quote(input_path, output_path, rules=rules)
    except QuoteError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise SystemExit(1)

    _render_report(report)


@cmd.command("list-rules", help="列出全部可用的规则及其触发条件。")
def _list_rules() -> None:
    from ..quotes import DEFAULT_RULES

    tbl = Table(title="可用规则", header_style="bold magenta")
    tbl.add_column("name", style="cyan")
    tbl.add_column("说明")
    for r in DEFAULT_RULES:
        tbl.add_row(r.name, r.description)
    console.print(tbl)


# ---------------------------------------------------------------------------
def _render_report(report) -> None:
    console.print(Panel(
        f"输入: [cyan]{report.input_path}[/cyan]\n"
        f"输出: [green]{report.output_path}[/green]\n"
        f"应用规则: [bold]{report.applied_count}[/bold] / {len(report.rule_results)}",
        title="✓ 格式化完成", border_style="green",
    ))

    for r in report.rule_results:
        icon = "[green]✓[/green]" if r.applied else (
            "[dim]—[/dim]" if not r.changes else "[yellow]i[/yellow]"
        )
        body_lines = [f"[green]+ {c}[/green]" for c in r.changes[:10]]
        if len(r.changes) > 10:
            body_lines.append(f"[dim]+ ... 还有 {len(r.changes) - 10} 条改动[/dim]")
        body_lines += [f"[yellow]⚠ {w}[/yellow]" for w in r.warnings]
        if not body_lines:
            body_lines = ["[dim](无变化)[/dim]"]
        console.print(Panel(
            "\n".join(body_lines),
            title=f"{icon} {r.name}",
            border_style="green" if r.applied else "dim",
        ))
