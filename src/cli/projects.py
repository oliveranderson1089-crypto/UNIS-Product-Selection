"""
Subcommand group: `projects` — project / opportunity management.

Placeholder for Phase 4. Concrete commands will land as:

    python -m src.cli projects new <name>       # 新建项目
    python -m src.cli projects list             # 项目列表(可按状态过滤)
    python -m src.cli projects show <id>        # 查看项目详情 + 所有报价版本
    python -m src.cli projects status <id> <s>  # 改状态:进行中/中标/未中标/结案
"""

from __future__ import annotations

import click


@click.group(name="projects", help="项目管理(标书/客户/状态)— Phase 4 占位。")
def cmd() -> None:
    pass


@cmd.command("list", help="列出所有项目(尚未实现)。")
def _list() -> None:
    click.echo("[未实现] projects list — 等 Phase 4")


@cmd.command("new", help="新建项目(尚未实现)。")
@click.argument("name")
def _new(name: str) -> None:
    click.echo(f"[未实现] projects new {name} — 等 Phase 4")
