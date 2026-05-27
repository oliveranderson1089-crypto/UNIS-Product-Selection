"""
Subcommand: `crawl` — run a catalog refresh (scrape + download + parse).

    python -m src.cli crawl                 # full refresh
    python -m src.cli crawl --max 5         # smoke-test: 5 products
    python -m src.cli crawl --max 5 -v      # with DEBUG logging
"""

from __future__ import annotations

import logging

import click

from ._common import console, setup_logging


@click.command(name="crawl", help="抓取 unisyue.com 全量产品 + 彩页 + 解析规格。")
@click.option("--max", "max_products", type=int, default=None,
              help="冒烟测试:只处理前 N 个产品")
@click.option("-v", "--verbose", is_flag=True, help="启用 DEBUG 日志")
def cmd(max_products: int | None, verbose: bool) -> None:
    setup_logging()
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    from ..scheduler import run_full_refresh
    with console.status("抓取中(礼貌延迟 1.5s/请求,需要几分钟)…"):
        report = run_full_refresh(max_products=max_products)
    console.print(f"[green]完成:[/green] {report}")


if __name__ == "__main__":
    cmd()
