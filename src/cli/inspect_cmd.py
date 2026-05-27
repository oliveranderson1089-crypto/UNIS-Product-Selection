"""
Subcommand: `inspect` — print a one-shot catalog audit.

    python -m src.cli inspect
"""

from __future__ import annotations

from collections import Counter, defaultdict

import click
from sqlalchemy import select
from rich.table import Table

from ._common import console, setup_logging


@click.command(name="inspect", help="打印产品库概览:数量、字段覆盖率、前 30 条。")
@click.option("--limit", type=int, default=30, help="表格显示前 N 条产品")
def cmd(limit: int) -> None:
    setup_logging()
    from ..storage import get_db
    from ..storage.models import Product, ProductPDF

    db = get_db()
    with db.session() as s:
        products = list(s.scalars(select(Product)))
        pdfs = list(s.scalars(select(ProductPDF)))

    console.rule(f"[bold]Catalog — {len(products)} 产品 / {len(pdfs)} PDF")

    # section x category
    by_sc: Counter[tuple[str, str]] = Counter(
        (p.section or "-", p.category or "-") for p in products
    )
    sc_tbl = Table(title="按 section × category", header_style="bold magenta")
    sc_tbl.add_column("section"); sc_tbl.add_column("category"); sc_tbl.add_column("count", justify="right")
    for (sec, cat), n in sorted(by_sc.items(), key=lambda kv: (-kv[1], kv[0])):
        sc_tbl.add_row(sec, cat, str(n))
    console.print(sc_tbl)

    # field coverage
    fields = [
        "port_count", "port_speed", "uplink_speed", "switching_capacity_gbps",
        "forwarding_rate_mpps", "layer", "poe", "redundant_power", "rack_units",
        "is_domestic",
    ]
    cov = Table(title="规格覆盖率", header_style="bold magenta")
    cov.add_column("field"); cov.add_column("filled", justify="right")
    cov.add_column("missing", justify="right"); cov.add_column("coverage", justify="right")
    n = max(len(products), 1)
    for f in fields:
        filled = sum(1 for p in products if getattr(p, f) is not None)
        cov.add_row(f, str(filled), str(n - filled), f"{filled/n:.0%}")
    console.print(cov)

    # first N products
    pdfs_by_pid: dict[int, list] = defaultdict(list)
    for pdf in pdfs:
        pdfs_by_pid[pdf.product_id].append(pdf)
    prod = Table(title=f"前 {min(limit, len(products))} 条产品", header_style="bold magenta")
    for h in ("section", "category", "model", "ports", "speed", "layer", "PDFs"):
        prod.add_column(h)
    for p in products[:limit]:
        ports = "-"
        if p.port_count and p.port_speed:
            ports = f"{p.port_count}×{p.port_speed}"
        elif p.port_count:
            ports = f"{p.port_count}口"
        prod.add_row(
            p.section or "-", p.category or "-", p.model, ports,
            p.port_speed or "-", p.layer or "-",
            str(len(pdfs_by_pid.get(p.id, []))),
        )
    console.print(prod)


if __name__ == "__main__":
    cmd()
