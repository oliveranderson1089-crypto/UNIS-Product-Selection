"""
unisyue.com crawler.

The site is a static-HTML catalog. Each product lives at a URL like:
    https://www.unisyue.com/Autonomous_Controllable/11/UNISS12600-CR-G/2497.html

Strategy:
1. Start from configured `start_paths` (a category landing page).
2. Discover product links via anchor scraping with a URL-shape filter.
3. For each product page, extract product metadata + brochure (彩页) PDF link.
4. Upsert into SQLite; queue PDF downloads for the PDFDownloader.

NOTE: The exact HTML structure of unisyue.com may evolve. We isolate every
selector behind a named function so when the site changes, fixes are local.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import get_config
from ..storage import get_db
from .http import PoliteClient
from .pdf_downloader import PDFDownloader

logger = logging.getLogger(__name__)

# Regex matching the canonical product URL shape:
# /<Category>/<NumericId>/<ModelSlug>/<NumericId>.html
PRODUCT_URL_RE = re.compile(r"/[A-Za-z_]+/\d+/[^/]+/\d+\.html$")

# Brochure links commonly contain 彩页 / brochure / .pdf
BROCHURE_HINT_RE = re.compile(r"(彩页|brochure|datasheet|规格书|参数手册|说明书)", re.I)


@dataclass
class ProductDraft:
    """In-memory representation of a scraped product before DB upsert."""

    model: str
    name: str | None = None
    category: str | None = None
    sub_category: str | None = None
    series: str | None = None
    description: str | None = None
    page_url: str | None = None
    pdf_links: list[tuple[str, str]] = field(default_factory=list)  # (title, url)

    def to_db_payload(self) -> dict:
        # We deliberately do NOT populate spec columns here — those come from
        # the PDF parser. Crawler's job is to know products exist.
        return {
            "model": self.model,
            "series": self.series,
            "category": self.category,
            "sub_category": self.sub_category,
            "name": self.name,
            "description": self.description,
            "page_url": self.page_url,
            # All UNIS products on this site are domestic (自主可控 family).
            "is_domestic": True,
        }


class UnisCrawler:
    """Discover products and fetch their brochures."""

    def __init__(self, client: PoliteClient | None = None) -> None:
        self.cfg = get_config()
        self.client = client or PoliteClient()
        self.downloader = PDFDownloader(self.client)
        self._seen_pages: set[str] = set()

    def close(self) -> None:
        self.client.close()

    # ---- public API ---------------------------------------------------------
    def run(self) -> dict[str, int]:
        """
        Discover products and persist drafts + PDFs.

        Returns counters useful for logging / dashboards.
        """
        product_urls = list(self._discover_product_urls())
        logger.info("Discovered %d product pages.", len(product_urls))

        new_count = 0
        pdf_count = 0
        for url in product_urls:
            try:
                draft = self._scrape_product(url)
            except Exception as exc:                              # noqa: BLE001
                logger.exception("Failed to scrape %s: %s", url, exc)
                continue
            if draft is None:
                continue
            db = get_db()
            db.upsert_product(draft.to_db_payload())
            new_count += 1
            for title, pdf_url in draft.pdf_links:
                if self.downloader.download_for(draft.model, title, pdf_url):
                    pdf_count += 1

        return {"products": new_count, "pdfs": pdf_count}

    # ---- discovery ----------------------------------------------------------
    def _discover_product_urls(self) -> Iterable[str]:
        for start in self.cfg.crawler.start_paths:
            yield from self._walk_category(urljoin(self.cfg.crawler.base_url, start))

    def _walk_category(self, url: str, *, depth: int = 0, max_depth: int = 3) -> Iterable[str]:
        """BFS through category pages, yielding product-page URLs."""
        if url in self._seen_pages or depth > max_depth:
            return
        self._seen_pages.add(url)

        html = self._safe_get_html(url)
        if html is None:
            return
        soup = BeautifulSoup(html, "lxml")

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("javascript:", "mailto:", "#")):
                continue
            absolute = urljoin(url, href)
            if not absolute.startswith(self.cfg.crawler.base_url):
                continue

            if PRODUCT_URL_RE.search(urlparse(absolute).path):
                yield absolute
            elif self._looks_like_category(absolute) and absolute not in self._seen_pages:
                # Recurse into deeper category pages.
                yield from self._walk_category(absolute, depth=depth + 1, max_depth=max_depth)

    @staticmethod
    def _looks_like_category(url: str) -> bool:
        """Heuristic: URLs without a trailing .html numeric id are category-ish."""
        path = urlparse(url).path
        return not path.endswith(".html") and "/Autonomous_Controllable/" in path

    # ---- per-product scrape -------------------------------------------------
    def _scrape_product(self, url: str) -> ProductDraft | None:
        html = self._safe_get_html(url)
        if html is None:
            return None
        soup = BeautifulSoup(html, "lxml")

        model = self._extract_model(soup, url)
        if model is None:
            logger.debug("No model found on %s", url)
            return None

        if any(kw in model for kw in self.cfg.crawler.skip_keywords):
            logger.debug("Skipping %s (matched skip_keywords)", model)
            return None

        draft = ProductDraft(
            model=model,
            name=self._extract_title(soup),
            category=self._infer_category(url, soup),
            series=self._extract_series(model),
            description=self._extract_description(soup),
            page_url=url,
            pdf_links=list(self._extract_pdf_links(soup, url)),
        )

        whitelist = self.cfg.crawler.category_whitelist
        if whitelist and draft.category and draft.category not in whitelist:
            logger.debug("Skipping %s — category %s not in whitelist", model, draft.category)
            return None
        return draft

    # ---- HTML field extractors (isolate site-specific selectors) ----------
    @staticmethod
    def _extract_model(soup: BeautifulSoup, url: str) -> str | None:
        # 1) prefer the URL slug — it's the canonical product code on UNIS site.
        m = re.search(r"/([A-Z0-9][A-Z0-9\-_]+)/\d+\.html$", url)
        if m:
            return m.group(1).replace("UNISS", "UNIS S").replace("UNIS_", "UNIS ").strip()
        # 2) fallback: <h1> / <title>
        for sel in ("h1", "h2", "title"):
            el = soup.find(sel)
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)
        return None

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str | None:
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        t = soup.find("title")
        return t.get_text(strip=True) if t else None

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str | None:
        # First substantial <p> as a description.
        for p in soup.find_all("p"):
            text = p.get_text(" ", strip=True)
            if len(text) > 40:
                return text[:1024]
        return None

    @staticmethod
    def _extract_series(model: str) -> str | None:
        m = re.match(r"(UNIS\s*[A-Z]+\d+)", model)
        return m.group(1) if m else None

    @staticmethod
    def _infer_category(url: str, soup: BeautifulSoup) -> str | None:
        """Map a URL path segment / breadcrumb to our Category vocabulary."""
        text = (soup.get_text(" ", strip=True) + " " + url).lower()
        rules = [
            ("交换机", ("交换机", "switch", "switching")),
            ("路由器", ("路由器", "router")),
            ("服务器", ("服务器", "server")),
            ("存储",   ("存储", "storage", "san", "nas")),
            ("防火墙", ("防火墙", "firewall")),
            ("无线",   ("无线", "wifi", "wlan", "ap控制器")),
        ]
        for cat, kws in rules:
            if any(k in text for k in kws):
                return cat
        return None

    @staticmethod
    def _extract_pdf_links(soup: BeautifulSoup, base: str) -> Iterable[tuple[str, str]]:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.lower().endswith(".pdf"):
                continue
            absolute = urljoin(base, href)
            title = a.get_text(" ", strip=True) or href.rsplit("/", 1)[-1]
            # Prefer obvious brochure links; still emit others as backups.
            if BROCHURE_HINT_RE.search(title) or BROCHURE_HINT_RE.search(href):
                yield (title, absolute)
            else:
                yield (title, absolute)

    # ---- low-level helper ---------------------------------------------------
    def _safe_get_html(self, url: str) -> str | None:
        try:
            resp = self.client.get(url)
        except Exception as exc:                                  # noqa: BLE001
            logger.warning("GET %s failed: %s", url, exc)
            return None
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            logger.debug("Skipping %s (status=%s, ct=%s)",
                         url, resp.status_code, resp.headers.get("content-type"))
            return None
        return resp.text


__all__ = ["UnisCrawler", "ProductDraft"]
