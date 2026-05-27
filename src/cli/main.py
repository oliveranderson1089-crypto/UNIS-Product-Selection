"""
Top-level CLI group.

    python -m src.cli                # show help
    python -m src.cli select "..."   # product selection
    python -m src.cli crawl --max 5  # catalog refresh
    python -m src.cli inspect        # catalog audit
    python -m src.cli catalog ...    # 名录 管理 (Phase 2)
    python -m src.cli projects ...   # 项目管理 (Phase 4)
    python -m src.cli quote ...      # 报价单编辑 (Phase 5)

Each subcommand lives in its own module and exposes a `cmd` Click command.
Adding a new top-level feature = create a new `src/cli/<name>.py` with a
`cmd` and one line in `_register_subcommands()`.
"""

from __future__ import annotations

import click


@click.group(
    help=(
        "UNIS 产品选型 / 项目管理 / 报价单编辑工具集。\n\n"
        "运行 `python -m src.cli <子命令> --help` 查看具体用法。"
    ),
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(package_name=None, version=None, message="UNIS-Product-Selection (dev)")
def app() -> None:
    pass


def _register_subcommands() -> None:
    """Wire every subcommand into the top-level group. Single place to extend."""
    from . import catalog, crawl, inspect_cmd, projects, quote, select
    app.add_command(select.cmd)
    app.add_command(crawl.cmd)
    app.add_command(inspect_cmd.cmd)
    app.add_command(catalog.cmd)
    app.add_command(projects.cmd)
    app.add_command(quote.cmd)


_register_subcommands()


if __name__ == "__main__":
    app()
