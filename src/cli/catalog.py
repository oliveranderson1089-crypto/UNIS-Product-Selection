"""
Subcommand group: `catalog` — manage product whitelists (政府名录 / 创新名录…).

Placeholder for Phase 2. Concrete commands will land as:

    python -m src.cli catalog import <pdf>     # parse 名录 PDF → catalog list
    python -m src.cli catalog list             # show all loaded catalogs
    python -m src.cli catalog show <name>      # show products in a catalog
    python -m src.cli catalog rebuild          # rebuild from source files
"""

from __future__ import annotations

import click


@click.group(name="catalog", help="名录(政府采购清单等)管理 — Phase 2 占位。")
def cmd() -> None:
    pass


@cmd.command("list", help="列出已注册的名录(尚未实现)。")
def _list() -> None:
    click.echo("[未实现] catalog list — 等 Phase 2")


@cmd.command("import", help="导入名录文件(PDF/Excel)(尚未实现)。")
@click.argument("path", type=click.Path(exists=True))
def _import(path: str) -> None:
    click.echo(f"[未实现] catalog import {path} — 等 Phase 2")
