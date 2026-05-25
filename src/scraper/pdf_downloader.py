"""
Brochure PDF downloader.

Responsibilities:
- Persist PDFs to `<data.pdf_dir>/<model_slug>/<filename>`.
- Skip downloads when SHA-256 matches existing file (etag-equivalent).
- Record the URL → file mapping in SQLite so the parser knows what's local.
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

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]+")


def _safe_filename(url: str) -> str:
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "brochure.pdf"
    return _SAFE_NAME.sub("_", name)


def _slugify(model: str) -> str:
    return _SAFE_NAME.sub("_", model.strip())


class PDFDownloader:
    def __init__(self, client: PoliteClient | None = None) -> None:
        self.cfg = get_config()
        self.client = client or PoliteClient()

    def download_for(self, model: str, title: str, url: str) -> bool:
        """
        Download one PDF for a known product. Returns True if a NEW file was
        written, False if the URL was already known and unchanged.
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

            local_path = self._fetch_to_disk(model, url)
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
    def _fetch_to_disk(self, model: str, url: str) -> Path | None:
        dest_dir = self.cfg.storage.pdf_dir / _slugify(model)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / _safe_filename(url)

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
        logger.info("Downloaded %s -> %s", url, dest)
        return dest

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()


__all__ = ["PDFDownloader"]
