"""
Subcommand group: `quote` — report-sheet editing.

    python -m src.cli quote format <input.xlsx>
    python -m src.cli quote format <input.xlsx> -o output.xlsx
    python -m src.cli quote format <input.xlsx> --skip remove_h3c_logo
    python -m src.cli quote format <input.xlsx> --only drop_fixed_columns
    python -m src.cli quote format <input.xlsx> --project 12
    python -m src.cli quote format <input.xlsx> --no-track

    python -m src.cli quote versions list                 # 最近 20 个版本
    python -m src.cli quote versions list --project 12    # 按项目过滤
    python -m src.cli quote versions show 5               # 看完整规则报告
    python -m src.cli quote versions delete 5             # 永久删除
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
@click.option("--project", "project_ref", default=None,
              help="关联到指定项目(id 或 name)。默认按文件路径自动推断。")
@click.option("--no-track", is_flag=True,
              help="不向数据库写 QuoteVersion 记录(一次性试跑用)")
@click.option("--note", default=None, help="给这次版本加一行备注")
def _format(input_path: Path, output_path: Path | None,
            only: tuple[str, ...], skip: tuple[str, ...],
            project_ref: str | None, no_track: bool,
            note: str | None) -> None:
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

    # ---- Persist a QuoteVersion record unless explicitly disabled --------
    if no_track:
        console.print("[dim]--no-track 已设置,跳过版本记录[/dim]")
        return

    from ..projects import record_quote_version
    summary = record_quote_version(
        report,
        project_ref=project_ref,
        auto_infer=(project_ref is None),
        notes=note,
    )
    if summary is None:
        console.print("[yellow]⚠ 版本记录写入失败(已忽略,看日志)[/yellow]")
        return

    if summary.project_id is not None:
        console.print(
            f"[green]📌 已记录为版本 #{summary.id},"
            f"关联项目 [bold]{summary.project_name}[/bold] "
            f"(id={summary.project_id})[/green]"
        )
    else:
        console.print(
            f"[yellow]📌 已记录为版本 #{summary.id},"
            f"但未匹配到任何项目(orphan)[/yellow]\n"
            f"[dim]文件不在 work_dir 下任何已扫描的项目里;"
            f"可用 `quote versions list` 看到它,或下次 format 时加 --project 关联[/dim]"
        )


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
# `quote versions` — recorded formatter runs
# ---------------------------------------------------------------------------
@cmd.group("versions", help="查看 / 删除已记录的报价版本(QuoteVersion 表)。")
def _versions_grp() -> None:
    pass


@_versions_grp.command("list", help="按时间倒序列出版本。")
@click.option("--project", "project_ref", default=None,
              help="按项目 id 或 name 过滤")
@click.option("--limit", default=20, show_default=True,
              help="最多显示多少条")
@click.option("--orphans-only", is_flag=True,
              help="只显示没有关联项目的孤儿版本")
def _versions_list(project_ref: str | None, limit: int, orphans_only: bool) -> None:
    setup_logging()
    from ..projects import list_quote_versions
    from ..projects.quote_versions import _resolve_project       # noqa: PLC2701
    from ..storage import get_db

    project_id = None
    if project_ref:
        with get_db().session() as s:
            proj = _resolve_project(s, project_ref)
            if proj is None:
                console.print(f"[red]✗ 找不到项目 {project_ref!r}[/red]")
                raise SystemExit(2)
            project_id = proj.id

    rows = list_quote_versions(
        project_id=project_id,
        limit=limit,
        include_orphans=not bool(project_id),
    )
    if orphans_only:
        rows = [r for r in rows if r.project_id is None]

    if not rows:
        console.print("[dim]没有任何版本记录。[/dim]")
        return

    tbl = Table(title=f"📋 报价版本 (共 {len(rows)} 条)",
                header_style="bold magenta")
    tbl.add_column("id", justify="right", style="cyan")
    tbl.add_column("时间")
    tbl.add_column("项目")
    tbl.add_column("源文件")
    tbl.add_column("应用/总规则", justify="right")
    tbl.add_column("方式")
    for r in rows:
        proj_disp = f"[{r.project_id}] {r.project_name}" if r.project_id else "[dim](无)[/dim]"
        tbl.add_row(
            str(r.id),
            r.generated_at.strftime("%Y-%m-%d %H:%M"),
            proj_disp,
            r.source_filename,
            f"{r.applied_count}/{r.total_rules}",
            r.formatter_method,
        )
    console.print(tbl)


@_versions_grp.command("show", help="显示指定版本的完整规则报告。")
@click.argument("version_id", type=int)
def _versions_show(version_id: int) -> None:
    setup_logging()
    from ..projects import get_quote_version

    v = get_quote_version(version_id)
    if v is None:
        console.print(f"[red]✗ 找不到版本 id={version_id}[/red]")
        raise SystemExit(1)

    header = [
        f"id: [cyan]{v.id}[/cyan]",
        f"生成时间: {v.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"项目: " + (
            f"[{v.project_id}] [bold]{v.project_name}[/bold]"
            if v.project_id else "[dim](无)[/dim]"
        ),
        f"源文件: [cyan]{v.source_file}[/cyan]",
        f"输出文件: [green]{v.output_file}[/green]",
        f"方式: {v.formatter_method}"
        + (f" (.xls 转换: {v.conversion_method})" if v.conversion_method else ""),
        f"应用规则: [bold]{v.applied_count}[/bold] / {v.total_rules}",
    ]
    if v.notes:
        header.append(f"备注: {v.notes}")
    console.print(Panel("\n".join(header), title=f"📋 报价版本 #{v.id}",
                        border_style="cyan"))

    for r in v.rule_report:
        applied = r.get("applied", False)
        changes = r.get("changes", [])
        warnings = r.get("warnings", [])
        icon = "[green]✓[/green]" if applied else (
            "[dim]—[/dim]" if not changes else "[yellow]i[/yellow]"
        )
        body = [f"[green]+ {c}[/green]" for c in changes[:10]]
        if len(changes) > 10:
            body.append(f"[dim]+ ... 还有 {len(changes) - 10} 条改动[/dim]")
        body += [f"[yellow]⚠ {w}[/yellow]" for w in warnings]
        if not body:
            body = ["[dim](无变化)[/dim]"]
        console.print(Panel("\n".join(body),
                            title=f"{icon} {r.get('name', '?')}",
                            border_style="green" if applied else "dim"))


@_versions_grp.command("delete", help="永久删除指定版本记录(不动磁盘上的 xlsx)。")
@click.argument("version_id", type=int)
@click.option("--yes", is_flag=True, help="跳过确认")
def _versions_delete(version_id: int, yes: bool) -> None:
    setup_logging()
    from ..projects import delete_quote_version, get_quote_version

    v = get_quote_version(version_id)
    if v is None:
        console.print(f"[red]✗ 找不到版本 id={version_id}[/red]")
        raise SystemExit(1)

    if not yes:
        click.confirm(
            f"删除版本 #{v.id} ({v.source_filename}, {v.generated_at:%Y-%m-%d %H:%M})?",
            abort=True,
        )
    if delete_quote_version(version_id):
        console.print(f"[green]✓ 已删除版本 #{version_id}[/green]")
    else:
        console.print(f"[red]✗ 删除失败[/red]")
        raise SystemExit(1)


@_versions_grp.command("link", help="把已有版本关联/改关联到某个项目。")
@click.argument("version_id", type=int)
@click.argument("project_ref")
def _versions_link(version_id: int, project_ref: str) -> None:
    setup_logging()
    from ..projects import set_quote_version_project
    if set_quote_version_project(version_id, project_ref):
        console.print(
            f"[green]✓ 版本 #{version_id} 已关联到项目 {project_ref}[/green]"
        )
    else:
        console.print(
            f"[red]✗ 失败:版本或项目不存在[/red]"
        )
        raise SystemExit(1)


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
