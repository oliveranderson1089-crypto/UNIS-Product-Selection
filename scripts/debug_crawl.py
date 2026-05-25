"""
Crawler probe — fetch ONE URL and dump everything the crawler would extract.

Usage:
    python scripts/debug_crawl.py <url>
    python scripts/debug_crawl.py https://www.unisyue.com/Autonomous_Controllable/11/UNISS12600-CR-G/2497.html
    python scripts/debug_crawl.py https://www.unisyue.com/Autonomous_Controllable/11/

Outputs three sections so it's obvious which selector is wrong when one is:
    [HTTP]      status, size, content-type
    [STRUCTURE] dom shape (h1/h2/title, breadcrumb, # of links, # of pdfs)
    [PARSED]    what the crawler's extractors think they found
    [DISCOVERY] product URLs and PDF links it would queue
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scraper.crawler import (    # noqa: E402
    BROCHURE_HINT_RE,
    PRODUCT_URL_RE,
    UnisCrawler,
)
from src.scraper.http import PoliteClient    # noqa: E402

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                  # noqa: BLE001
        pass

console = Console(force_terminal=True, legacy_windows=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="URL to probe")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print extra diagnostic info (DEBUG logs)")
    parser.add_argument("--dump-html", type=Path, default=None,
                        help="Save the raw HTML to this path for offline inspection")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # ---- HTTP ----------------------------------------------------------------
    client = PoliteClient()
    try:
        resp = client.get(args.url)
    finally:
        client.close()

    http_tbl = Table(show_header=False, box=None)
    http_tbl.add_row("URL", args.url)
    http_tbl.add_row("status", str(resp.status_code))
    http_tbl.add_row("content-type", resp.headers.get("content-type", ""))
    http_tbl.add_row("size (bytes)", str(len(resp.content)))
    http_tbl.add_row("encoding", resp.encoding or "?")
    console.print(Panel(http_tbl, title="[HTTP]", border_style="cyan"))

    if resp.status_code != 200 or "text/html" not in (resp.headers.get("content-type") or ""):
        console.print("[red]Not an HTML 200 — aborting structural probe.[/red]")
        return 1

    html = resp.text
    if args.dump_html:
        args.dump_html.write_text(html, encoding="utf-8")
        console.print(f"[dim]Raw HTML saved to {args.dump_html}[/dim]")

    soup = BeautifulSoup(html, "lxml")

    # ---- STRUCTURE -----------------------------------------------------------
    all_links = soup.find_all("a", href=True)
    pdf_links = [a for a in all_links if a["href"].lower().endswith(".pdf")]
    internal_links = [
        a for a in all_links
        if urljoin(args.url, a["href"]).startswith("https://www.unisyue.com")
    ]
    breadcrumb = soup.find(class_=lambda c: c and "crumb" in c.lower()) \
        or soup.find("nav") \
        or soup.find(class_=lambda c: c and "breadcrumb" in c.lower())

    struct = Table(show_header=False, box=None)
    struct.add_row("<title>",   _safe_text(soup.find("title")))
    struct.add_row("<h1>",      _safe_text(soup.find("h1")))
    struct.add_row("<h2>",      _safe_text(soup.find("h2")))
    struct.add_row("breadcrumb",_safe_text(breadcrumb))
    struct.add_row("total <a>",     str(len(all_links)))
    struct.add_row("internal <a>",  str(len(internal_links)))
    struct.add_row("PDF <a>",       str(len(pdf_links)))
    struct.add_row("<table> count", str(len(soup.find_all("table"))))
    console.print(Panel(struct, title="[STRUCTURE]", border_style="cyan"))

    # ---- PARSED via crawler extractors --------------------------------------
    parsed = Table(show_header=False, box=None)
    parsed.add_row("URL matches PRODUCT_URL_RE",
                   _bool(bool(PRODUCT_URL_RE.search(urlparse(args.url).path))))
    parsed.add_row("model (URL slug heuristic)",
                   _safe(UnisCrawler._extract_model(soup, args.url)))
    parsed.add_row("title",       _safe(UnisCrawler._extract_title(soup, args.url)))
    parsed.add_row("category",    _safe(UnisCrawler._infer_category(args.url, soup)))
    parsed.add_row("description", _truncate(UnisCrawler._extract_description(soup), 120))
    console.print(Panel(parsed, title="[PARSED]", border_style="green"))

    # ---- DISCOVERY: PDFs and product URLs -----------------------------------
    pdf_tbl = Table(title="PDF links found", header_style="bold magenta",
                    show_header=True)
    pdf_tbl.add_column("#", style="dim", width=3)
    pdf_tbl.add_column("hint", width=8)
    pdf_tbl.add_column("title")
    pdf_tbl.add_column("url", overflow="fold")
    for i, (title, pdf_url) in enumerate(UnisCrawler._extract_pdf_links(soup, args.url), 1):
        is_brochure = bool(BROCHURE_HINT_RE.search(title) or BROCHURE_HINT_RE.search(pdf_url))
        pdf_tbl.add_row(str(i), "✓" if is_brochure else "?", title or "-", pdf_url)
    console.print(pdf_tbl if pdf_tbl.row_count else "[yellow]No PDF links discovered.[/yellow]")

    # Product URL discovery on this page (useful when probing a category page)
    product_urls: list[str] = []
    for a in all_links:
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(args.url, href)
        if not absolute.startswith("https://www.unisyue.com"):
            continue
        if PRODUCT_URL_RE.search(urlparse(absolute).path):
            product_urls.append(absolute)

    if product_urls:
        prod_tbl = Table(title=f"Product-shaped URLs ({len(product_urls)})",
                         header_style="bold magenta")
        prod_tbl.add_column("#", style="dim", width=3)
        prod_tbl.add_column("url", overflow="fold")
        for i, u in enumerate(sorted(set(product_urls)), 1):
            prod_tbl.add_row(str(i), u)
        console.print(prod_tbl)
    else:
        console.print("[yellow]No product-shaped URLs on this page "
                      "(this is normal for a product page; should be non-empty on a category page).[/yellow]")

    return 0


# ---- helpers ---------------------------------------------------------------
def _safe(v) -> str:
    return "[dim]None[/dim]" if v is None else str(v)


def _safe_text(node) -> str:
    if node is None:
        return "[dim]None[/dim]"
    return node.get_text(" ", strip=True) or "[dim](empty)[/dim]"


def _truncate(s, n: int) -> str:
    if not s:
        return "[dim]None[/dim]"
    return s if len(s) <= n else s[:n] + "…"


def _bool(b: bool) -> str:
    return "[green]True[/green]" if b else "[red]False[/red]"


if __name__ == "__main__":
    sys.exit(main())
