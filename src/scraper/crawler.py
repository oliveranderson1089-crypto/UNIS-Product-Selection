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
#   /<Category>/<NumericId>/<ModelSlug>/<NumericId>.html
# Slug MUST contain at least one letter, so we don't accidentally match
# support-tree URLs like /Service_Support/32/322/119.html where the "slug"
# is purely numeric.
PRODUCT_URL_RE = re.compile(r"/[A-Za-z_]+/\d+/(?=[^/]*[A-Za-z])[^/]+/\d+\.html$")

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
    def run(self, *, max_products: int | None = None) -> dict[str, int]:
        """
        Discover products and persist drafts + PDFs.

        Args:
            max_products: hard cap on how many product pages to scrape this
              run. Useful for incremental smoke-tests against a live site
              without committing to a full crawl. None = no cap.

        Returns counters useful for logging / dashboards.
        """
        product_urls = list(self._discover_product_urls())
        logger.info("Discovered %d product pages.", len(product_urls))
        if max_products is not None:
            product_urls = product_urls[:max_products]
            logger.info("Capped to first %d for this run.", max_products)

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
    # All selectors target the unisyue.com product-page DOM observed during
    # probing. If the site redesigns, fix them HERE — the rest of the
    # crawler is structure-agnostic.

    @staticmethod
    def _extract_model(soup: BeautifulSoup, url: str) -> str | None:
        """
        Prefer the canonical URL slug; the site's HTML often shows multiple
        product names (related-products carousel etc.) which would mislead a
        title-based heuristic.
        """
        m = re.search(r"/([A-Z0-9][A-Z0-9\-_]+)/\d+\.html$", url)
        if m:
            return m.group(1).replace("UNISS", "UNIS S").replace("UNIS_", "UNIS ").strip()
        # Fallback: the in-DOM main product h1 (see _extract_title scoping).
        return UnisCrawler._extract_title(soup, url)

    @staticmethod
    def _extract_title(soup: BeautifulSoup, url: str | None = None) -> str | None:
        """
        Real product name. The page often has several <h1>s (related-products
        carousel, banners…), so:
          1) Prefer the h1 scoped to the product hero (`section.product-images`).
          2) Otherwise <title>, stripped of the trailing site-name + category
             chain. The title format observed is
                "<product-full-name>-<category>-<section>-紫光恒越"
             and product names contain "-" (e.g. S12600-CR-G), so a naive
             split on "-" is wrong — we strip the brand and category suffix
             instead.
          3) Last resort: the first <h1> in the document.
        """
        scoped = soup.select_one("section.product-images h1") \
            or soup.select_one(".product-images h1") \
            or soup.select_one(".product-info-details h1")
        if scoped and scoped.get_text(strip=True):
            return scoped.get_text(strip=True)

        t = soup.find("title")
        if t and t.get_text(strip=True):
            text = t.get_text(strip=True)
            # Strip the brand suffix.
            text = re.sub(r"-\s*紫光[^-]*$", "", text)
            # Strip any remaining trailing "-<Chinese-only-token>" segments
            # (category/section like "交换机", "创新产品"). Product names
            # always contain digits or Latin chars, so segments that are
            # short and pure-Chinese are safe to drop.
            while True:
                m = re.search(r"-\s*([一-鿿]{2,8})\s*$", text)
                if not m:
                    break
                text = text[: m.start()].rstrip(" -")
            return text.strip() or None

        h1 = soup.find("h1")
        return h1.get_text(strip=True) if h1 else None

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str | None:
        """
        Product description lives in the first .product-detail-content
        (tab "产品概述"). Concatenate its <p> children so we get a full
        intro rather than just a one-liner.
        """
        block = soup.select_one("section.product-info-details .product-detail-content") \
            or soup.select_one(".product-detail-content")
        if block:
            paragraphs = [
                p.get_text(" ", strip=True)
                for p in block.find_all("p")
                if p.get_text(strip=True)
            ]
            text = " ".join(paragraphs).strip()
            if text:
                return text[:2048]

        # Fallback: first long <p> anywhere — may be wrong if the page has
        # related-products cards above the real content, but better than nothing.
        for p in soup.find_all("p"):
            text = p.get_text(" ", strip=True)
            if len(text) > 40:
                return text[:2048]
        return None

    @staticmethod
    def _extract_series(model: str) -> str | None:
        m = re.match(r"(UNIS\s*[A-Z]+\d+)", model)
        return m.group(1) if m else None

    @staticmethod
    def _infer_category(url: str, soup: BeautifulSoup) -> str | None:
        """
        Determine the product category. Priority:

          1) Breadcrumb's second-to-last link — e.g.
             "首页 > 创新产品 > 交换机 > <product>"  →  "交换机".
             Reliable because the site itself produced this label.
          2) Frequency-weighted keyword search — a fallback that takes the
             category most-mentioned in body text. First-match was too noisy
             (server pages mention "交换机" in navigation, etc.).
        """
        known = {"交换机", "路由器", "服务器", "存储", "防火墙", "无线", "云平台"}

        # ---- 1) breadcrumb ----
        crumb = soup.find(class_=lambda c: c and "crumb" in c.lower()) \
            or soup.find("nav") \
            or soup.find(class_=lambda c: c and "breadcrumb" in c.lower())
        if crumb:
            links = [a.get_text(strip=True) for a in crumb.find_all("a")
                     if a.get_text(strip=True)]
            # Last <a> is usually the product itself; the one before it is
            # the leaf category. Walk backward over the parent levels.
            for token in reversed(links[:-1]):
                if token in known:
                    return token

        # ---- 2) frequency-weighted keyword fallback ----
        text = (soup.get_text(" ", strip=True) + " " + url).lower()
        rules: list[tuple[str, tuple[str, ...]]] = [
            ("交换机", ("交换机", "switch", "switching")),
            ("路由器", ("路由器", "router")),
            ("服务器", ("服务器", "server")),
            ("存储",   ("存储", "storage", "san", "nas")),
            ("防火墙", ("防火墙", "firewall")),
            ("无线",   ("无线", "wifi", "wlan", "ap控制器")),
        ]
        scores = {cat: sum(text.count(kw) for kw in kws) for cat, kws in rules}
        best = max(scores.items(), key=lambda kv: kv[1])
        return best[0] if best[1] > 0 else None

    @staticmethod
    def _extract_pdf_links(soup: BeautifulSoup, base: str) -> Iterable[tuple[str, str]]:
        """
        Collect all PDF links, then prefer the real brochure (彩页) when one
        exists. UNIS product pages typically attach 5–10 PDFs (user manual,
        install guide, optical-module table…); only the 彩页 is the spec
        sheet we want to parse for selection. Falling back to all PDFs keeps
        us robust against pages that name their brochure differently.
        """
        all_pdfs: list[tuple[str, str]] = []
        brochures: list[tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href.lower().endswith(".pdf"):
                continue
            absolute = urljoin(base, href)
            title = a.get_text(" ", strip=True) or href.rsplit("/", 1)[-1]
            entry = (title, absolute)
            all_pdfs.append(entry)
            if BROCHURE_HINT_RE.search(title) or BROCHURE_HINT_RE.search(absolute):
                brochures.append(entry)

        yield from (brochures if brochures else all_pdfs)

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
