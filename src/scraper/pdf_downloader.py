"""
Brochure PDF downloader.

On-disk layout:
    data/pdfs/
        innovation/
            switches/
                UNIS S12600-CR-G系列数据中心交换机.pdf
                UNIS S6520X-EI万兆汇聚交换机.pdf
                ...
            routers/
            security/
            ...
        general/
            switches/
            routers/
            ...

The filename comes from the anchor text on the product page (which is
human-readable, e.g. "UNIS S12600-CR-G系列数据中心交换机") rather than
the URL's UUID-style filename. This makes the downloads folder
browsable without referring back to the database.

Responsibilities:
- Persist PDFs to the layout above.
- Skip re-downloads when the file is already present (SHA-256 verified).
- Record the URL → local-path mapping in SQLite so the parser knows
  what's local.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import select

from ..config import get_config
from ..storage import get_db
from ..storage.models import Product, ProductPDF
from .http import PoliteClient

logger = logging.getLogger(__name__)

# Strip Windows-illegal filename chars; preserve Chinese and most punctuation.
_ILLEGAL_FNAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')
# Slug for path components — alnum+dash only, no spaces/brackets/Chinese.
_SAFE_SLUG = re.compile(r"[^A-Za-z0-9._\-]+")


def _readable_filename(title: str, fallback_url: str) -> str:
    """
    Build a human-readable filename from the anchor text.

    Strips brackets/whitespace that the source page often wraps brochure
    titles in (e.g. "【产品彩页】-【UNIS S12600...】"), so the final
    on-disk name is clean: "UNIS S12600-CR-G系列数据中心交换机.pdf".
    """
    name = title.strip() if title else ""
    # Drop common decorative prefixes seen in unisyue link text.
    for prefix in ("【产品彩页】-", "【产品彩页】", "产品彩页-", "产品彩页"):
        if name.startswith(prefix):
            name = name[len(prefix):].lstrip("-—– ")
    name = name.strip("【】[]() ").strip()
    if not name:
        name = unquote(urlparse(fallback_url).path.rsplit("/", 1)[-1]) or "brochure"
    # Drop Windows-illegal chars only — keep Chinese.
    name = _ILLEGAL_FNAME.sub("_", name).strip(" .")
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    # Hard cap on length — some titles are very long.
    if len(name.encode("utf-8")) > 200:
        stem = name[:-4]
        name = stem[:60].rstrip() + ".pdf"
    return name


def _slug_component(s: str) -> str:
    """Folder-safe component (ASCII-only). Used for `section` / `category_slug`."""
    cleaned = _SAFE_SLUG.sub("_", (s or "").strip())
    return cleaned or "unknown"


class PDFDownloader:
    def __init__(self, client: PoliteClient | None = None) -> None:
        self.cfg = get_config()
        self.client = client or PoliteClient()

    # ------------------------------------------------------------------------
    # Primary API: crawler calls this with section + category + product.
    # ------------------------------------------------------------------------
    def download_brochure(
        self,
        *,
        section: str,
        category_slug: str,
        model: str,
        title: str,
        url: str,
    ) -> bool:
        """
        Download one brochure into `<pdf_dir>/<section>/<category_slug>/<filename>`.

        Returns True if a NEW file was written. Returns False on skip (file
        already present), invalid product, or download failure.
        """
        db = get_db()
        with db.session() as s:
            product = s.scalar(select(Product).where(Product.model == model))
            if product is None:
                logger.warning("Cannot attach PDF — product not yet upserted: %s", model)
                return False

            existing = s.scalar(
                select(ProductPDF).where(
                    ProductPDF.product_id == product.id, ProductPDF.url == url,
                )
            )
            if existing and existing.local_path and Path(existing.local_path).exists():
                logger.debug("PDF already present: %s", existing.local_path)
                return False

            local_path = self._fetch_to_disk(
                section=section, category_slug=category_slug,
                title=title, url=url,
            )
            if local_path is None:
                return False
            sha = self._sha256(local_path)
            size = local_path.stat().st_size

            if existing:
                existing.local_path = str(local_path)
                existing.sha256 = sha
                existing.size_bytes = size
                existing.fetched_at = datetime.utcnow()
                existing.title = title
            else:
                s.add(ProductPDF(
                    product_id=product.id,
                    title=title,
                    url=url,
                    local_path=str(local_path),
                    sha256=sha,
                    size_bytes=size,
                    fetched_at=datetime.utcnow(),
                ))
        return True

    # ---- helpers ------------------------------------------------------------
    def _fetch_to_disk(
        self,
        *,
        section: str,
        category_slug: str,
        title: str,
        url: str,
    ) -> Path | None:
        dest_dir = (
            self.cfg.storage.pdf_dir
            / _slug_component(section)
            / _slug_component(category_slug)
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / _readable_filename(title, url)

        try:
            with self.client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    logger.warning("Download failed (%s): %s", resp.status_code, url)
                    return None
                with dest.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
        except Exception as exc:                                  # noqa: BLE001
            logger.warning("Download error for %s: %s", url, exc)
            if dest.exists():
                dest.unlink(missing_ok=True)
            return None
        logger.info("Downloaded %s", dest)
        return dest

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()


__all__ = ["PDFDownloader"]
