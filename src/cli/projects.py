"""
Subcommand group: `projects` — sales-opportunity tracking.

    python -m src.cli projects scan                       # 扫工作目录
    python -m src.cli projects list                       # 表格列出
    python -m src.cli projects list --assigner 漆森骅
    python -m src.cli projects list --status 进行中
    python -m src.cli projects show <id-or-name>          # 详情 + 文件
    python -m src.cli projects status <id-or-name> 中标   # 改状态
    python -m src.cli projects customer <id> "中石化"     # 设客户
    python -m src.cli projects notes <id> "提示原话"      # 记笔记
    python -m src.cli projects open <id-or-name>          # 打开项目文件夹
"""

from __future__ import annotations

import click
from rich.panel import Panel
from rich.table import Table

from ._common import console, setup_logging


@click.group(name="projects", help="项目/标书/报价单归档与状态跟踪。")
def cmd() -> None:
    pass


# ---------------------------------------------------------------------------
@cmd.command("scan", help="扫描配置的 work_dir,自动发现项目和文件。")
def _scan() -> None:
    setup_logging()
    from ..projects import scan_projects

    with console.status("扫描中..."):
        report = scan_projects()
    console.print(Panel(
        f"工作目录: [cyan]{report.work_dir}[/cyan]\n"
        f"发现人员目录: {report.assigners_seen}\n"
        f"项目数: {report.projects_seen}  (新增 [green]{report.projects_new}[/green])\n"
        f"文件数: {report.files_total}  (新增 [green]{report.files_new}[/green],"
        f"已删 [yellow]{report.files_removed}[/yellow])",
        title="扫描完成", border_style="green",
    ))


@cmd.command("list", help="列出项目(支持 --assigner / --status / --customer 过滤)。")
@click.option("--assigner", help="按下发人过滤")
@click.option("--status", help="按状态过滤(进行中 / 中标 / 未中标 / 结案)")
@click.option("--customer", help="按客户名包含匹配")
def _list(assigner: str | None, status: str | None, customer: str | None) -> None:
    setup_logging()
    from ..projects import list_projects

    items = list_projects(assigner=assigner, status=status, customer_like=customer)
    if not items:
        console.print("[yellow]没有匹配的项目。先 `projects scan` 一遍?[/yellow]")
        return
    tbl = Table(title=f"项目 ({len(items)})", header_style="bold magenta")
    for h in ("ID", "状态", "下发人", "代码", "全称", "客户", "文件数", "终版"):
        tbl.add_column(h)
    for p in items:
        tbl.add_row(
            str(p.id),
            _status_color(p.status),
            p.assigner,
            p.name,
            (p.display_name or "")[:50],
            p.customer or "-",
            str(p.file_count),
            "✓" if p.has_final_quote else "",
        )
    console.print(tbl)


@cmd.command("show", help="显示一个项目的所有细节(基础信息 + 文件清单)。")
@click.argument("project")
def _show(project: str) -> None:
    setup_logging()
    from ..projects import get_project

    found = get_project(project)
    if not found:
        console.print(f"[red]找不到项目 {project!r}。先 `projects list` 看可用 ID/代码。[/red]")
        raise SystemExit(1)
    proj, files = found

    header = (
        f"[bold cyan]{proj.name}[/bold cyan]   "
        f"<{_status_color(proj.status)}>   id={proj.id}\n"
        f"全称: {proj.display_name or '-'}\n"
        f"下发人: [cyan]{proj.assigner}[/cyan]   客户: {proj.customer or '-'}\n"
        f"路径: [dim]{proj.folder_path}[/dim]\n"
        f"更新: [dim]{proj.updated_at}[/dim]"
        + (f"\n备注: {proj.notes}" if proj.notes else "")
    )
    console.print(Panel(header, border_style="cyan"))

    if not files:
        console.print("[yellow](项目文件夹空)[/yellow]")
        return

    tbl = Table(title=f"文件 ({len(files)})", header_style="bold magenta")
    for h in ("类型", "终版", "文件名", "大小", "修改时间"):
        tbl.add_column(h)
    for f in files:
        tbl.add_row(
            _kind_color(f.kind),
            "✓" if f.is_final else "",
            f.name,
            _format_size(f.size_bytes),
            f.modified_at.strftime("%Y-%m-%d %H:%M") if f.modified_at else "-",
        )
    console.print(tbl)


@cmd.command("status", help="修改项目状态(进行中 / 中标 / 未中标 / 结案)。")
@click.argument("project")
@click.argument("new_status")
def _status(project: str, new_status: str) -> None:
    setup_logging()
    from ..projects import set_status

    try:
        proj = set_status(project, new_status)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)
    if proj is None:
        console.print(f"[red]找不到项目 {project!r}。[/red]")
        raise SystemExit(1)
    console.print(f"[green]✓[/green] 项目 [cyan]{proj.name}[/cyan] 状态改为 "
                  f"{_status_color(new_status)}")


@cmd.command("customer", help="设置项目的客户名称。")
@click.argument("project")
@click.argument("customer")
def _customer(project: str, customer: str) -> None:
    setup_logging()
    from ..projects import set_customer

    proj = set_customer(project, customer)
    if proj is None:
        console.print(f"[red]找不到项目 {project!r}。[/red]")
        raise SystemExit(1)
    console.print(f"[green]✓[/green] 项目 [cyan]{proj.name}[/cyan] 客户设为 "
                  f"[bold]{proj.customer or '(已清空)'}[/bold]")


@cmd.command("notes", help="给项目加备注(覆盖之前的备注)。")
@click.argument("project")
@click.argument("notes")
def _notes(project: str, notes: str) -> None:
    setup_logging()
    from ..projects import set_notes

    proj = set_notes(project, notes)
    if proj is None:
        console.print(f"[red]找不到项目 {project!r}。[/red]")
        raise SystemExit(1)
    console.print(f"[green]✓[/green] 备注已保存。")


@cmd.command("open", help="在系统文件管理器中打开项目文件夹。")
@click.argument("project")
def _open(project: str) -> None:
    setup_logging()
    from ..projects import get_project, open_in_explorer

    found = get_project(project)
    if not found:
        console.print(f"[red]找不到项目 {project!r}。[/red]")
        raise SystemExit(1)
    proj, _ = found
    ok = open_in_explorer(proj.folder_path)
    console.print(
        f"[green]✓[/green] 已唤起文件管理器: [dim]{proj.folder_path}[/dim]"
        if ok else
        f"[red]✗[/red] 打不开: [dim]{proj.folder_path}[/dim]"
    )


# ---- helpers ---------------------------------------------------------------
def _status_color(s: str) -> str:
    return {
        "进行中": "[yellow]进行中[/yellow]",
        "中标":   "[bold green]中标[/bold green]",
        "未中标": "[red]未中标[/red]",
        "结案":   "[dim]结案[/dim]",
    }.get(s, s)


def _kind_color(k: str) -> str:
    return {
        "quote":       "[cyan]报价单[/cyan]",
        "requirement": "[magenta]需求[/magenta]",
        "config":      "[blue]配置[/blue]",
        "image":       "[dim]图片[/dim]",
        "other":       "[dim]其他[/dim]",
    }.get(k, k)


def _format_size(b: int | None) -> str:
    if b is None:
        return "-"
    if b < 1024:
        return f"{b} B"
    if b < 1024**2:
        return f"{b/1024:.1f} KB"
    return f"{b/1024**2:.1f} MB"
