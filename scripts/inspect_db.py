"""Inspect the catalog — counts + per-category breakdown + coverage gaps.

    python scripts/inspect_db.py
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                  # noqa: BLE001
        pass

from sqlalchemy import select                            # noqa: E402
from rich.console import Console                         # noqa: E402
from rich.table import Table                             # noqa: E402

from src.storage import get_db                           # noqa: E402
from src.storage.models import Product, ProductPDF      # noqa: E402

console = Console(force_terminal=True, legacy_windows=False)


def main() -> None:
    db = get_db()
    with db.session() as s:
        products = list(s.scalars(select(Product)))
        pdfs = list(s.scalars(select(ProductPDF)))

    console.rule(f"[bold]Catalog summary — {len(products)} products, {len(pdfs)} PDFs")

    by_category = Counter(p.category or "(none)" for p in products)
    cat_tbl = Table(title="By category", header_style="bold magenta")
    cat_tbl.add_column("category")
    cat_tbl.add_column("count", justify="right")
    for cat, n in sorted(by_category.items(), key=lambda kv: -kv[1]):
        cat_tbl.add_row(cat, str(n))
    console.print(cat_tbl)

    # Spec coverage per column — what % of products have the field filled.
    fields = [
        "port_count", "port_speed", "uplink_speed", "switching_capacity_gbps",
        "forwarding_rate_mpps", "layer", "poe", "redundant_power", "rack_units",
        "is_domestic",
    ]
    cov_tbl = Table(title="Spec coverage", header_style="bold magenta")
    cov_tbl.add_column("field")
    cov_tbl.add_column("filled", justify="right")
    cov_tbl.add_column("missing", justify="right")
    cov_tbl.add_column("coverage", justify="right")
    n = max(len(products), 1)
    for f in fields:
        filled = sum(1 for p in products if getattr(p, f) is not None)
        cov_tbl.add_row(
            f, str(filled), str(n - filled), f"{filled/n:.0%}",
        )
    console.print(cov_tbl)

    # Per-product summary table (truncated to first 30).
    prod_tbl = Table(title="Products (first 30)", header_style="bold magenta")
    prod_tbl.add_column("model")
    prod_tbl.add_column("category")
    prod_tbl.add_column("ports")
    prod_tbl.add_column("speed")
    prod_tbl.add_column("layer")
    prod_tbl.add_column("Gbps", justify="right")
    prod_tbl.add_column("PDFs", justify="right")
    pdfs_by_product = defaultdict(list)
    for pdf in pdfs:
        pdfs_by_product[pdf.product_id].append(pdf)
    for p in products[:30]:
        prod_tbl.add_row(
            p.model,
            p.category or "-",
            str(p.port_count or "-"),
            p.port_speed or "-",
            p.layer or "-",
            f"{p.switching_capacity_gbps:.0f}" if p.switching_capacity_gbps else "-",
            str(len(pdfs_by_product.get(p.id, []))),
        )
    console.print(prod_tbl)


if __name__ == "__main__":
    main()
