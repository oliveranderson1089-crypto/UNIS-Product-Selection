"""
Subcommand group: `quote` — quote-sheet editing & formatting.

Placeholder for Phase 5. Concrete commands will land as:

    python -m src.cli quote format <quote.xls>          # 应用通用清理规则
    python -m src.cli quote format <quote.xls> --rule server   # 加服务器专属规则
    python -m src.cli quote attach <project-id> <quote.xls>   # 关联到项目
"""

from __future__ import annotations

import click


@click.group(name="quote", help="报价单编辑(读 H3C 配置器 .xls → 格式化)— Phase 5 占位。")
def cmd() -> None:
    pass


@cmd.command("format", help="按预设规则格式化报价单(尚未实现)。")
@click.argument("path", type=click.Path(exists=True))
def _format(path: str) -> None:
    click.echo(f"[未实现] quote format {path} — 等 Phase 5")
