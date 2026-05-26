"""
Dump everything an extracted PDF contains so we can design table-aware specs.

    python scripts/debug_pdf.py <path-to-pdf>
    python scripts/debug_pdf.py --product UNIS-S5800X-EI-G    # by product code

Prints:
  [TEXT]    first 600 chars of body text
  [TABLES]  every table as markdown, with shape and page number
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                  # noqa: BLE001
        pass

from rich.console import Console                       # noqa: E402
from rich.panel import Panel                           # noqa: E402
from rich.table import Table as RichTable              # noqa: E402

from src.extractors.pdf import extract_pdf             # noqa: E402

console = Console(force_terminal=True, legacy_windows=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, help="Path to PDF")
    parser.add_argument("--product", help="Look up the brochure PDF for this product code (DB)")
    parser.add_argument("--max-tables", type=int, default=10)
    args = parser.parse_args()

    pdf_path = args.path
    if not pdf_path and args.product:
        from sqlalchemy import select
        from src.storage import get_db
        from src.storage.models import Product, ProductPDF
        db = get_db()
        with db.session() as s:
            p = s.scalar(select(Product).where(Product.model == args.product))
            if not p:
                console.print(f"[red]No product named {args.product!r}[/red]")
                return 1
            for pdf in p.pdfs:
                if "彩页" in (pdf.title or "") or "彩页" in (pdf.url or "") \
                        or pdf.local_path:
                    pdf_path = Path(pdf.local_path)
                    if pdf_path.exists():
                        break

    if not pdf_path or not pdf_path.exists():
        console.print("[red]Provide a PDF path or --product with a downloaded brochure[/red]")
        return 1

    console.print(Panel(str(pdf_path), title="[FILE]", border_style="cyan"))

    content = extract_pdf(pdf_path)

    # ---- TEXT ---------------------------------------------------------------
    body = content.text or "(empty)"
    console.print(Panel(body[:600] + ("…" if len(body) > 600 else ""),
                        title=f"[TEXT]  total {len(body)} chars",
                        border_style="cyan"))

    # ---- TABLES -------------------------------------------------------------
    if not content.tables:
        console.print("[yellow]No tables detected.[/yellow]")
        return 0

    for i, t in enumerate(content.tables[:args.max_tables], 1):
        rows = len(t.rows)
        cols = max(len(r) for r in t.rows) if t.rows else 0
        rt = RichTable(
            title=f"Table {i}  page={t.page}  shape={rows}×{cols}",
            header_style="bold magenta",
            show_lines=True,
        )
        header = t.rows[0] if t.rows else []
        for h in header:
            rt.add_column(h or "(blank)", overflow="fold")
        for row in t.rows[1:50]:                      # cap to keep output sane
            rt.add_row(*[c or "" for c in row])
        console.print(rt)

    if len(content.tables) > args.max_tables:
        console.print(f"[dim]... and {len(content.tables) - args.max_tables} more tables hidden.[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
