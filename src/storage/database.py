"""
SQLite engine + session helpers + a small query API.

We expose a `Database` class for callers that want explicit lifecycles and
a `get_db()` singleton for everyday use.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_config
from .models import Base, Product

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, sqlite_path: Path):
        self.sqlite_path = sqlite_path
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(
            f"sqlite:///{sqlite_path}",
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
        self._Session = sessionmaker(self.engine, expire_on_commit=False, future=True)

    # ---- schema -----------------------------------------------------------
    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    # ---- session ----------------------------------------------------------
    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ---- product CRUD -----------------------------------------------------
    def upsert_product(self, payload: dict) -> Product:
        """
        Insert or update by `model` (the human-readable product code, unique).
        """
        model_key = payload["model"]
        with self.session() as s:
            existing = s.scalar(select(Product).where(Product.model == model_key))
            if existing is None:
                p = Product(**payload)
                s.add(p)
                s.flush()
                return p
            for k, v in payload.items():
                if k == "id":
                    continue
                setattr(existing, k, v)
            s.flush()
            return existing

    def all_products(self, category: str | None = None, limit: int | None = None) -> list[Product]:
        stmt = select(Product)
        if category:
            stmt = stmt.where(Product.category == category)
        if limit:
            stmt = stmt.limit(limit)
        with self.session() as s:
            return list(s.scalars(stmt))

    def find_products(
        self,
        *,
        section: str | None = None,
        category: str | None = None,
        min_port_count: int | None = None,
        port_speed: str | None = None,
        layer: str | None = None,
        poe: bool | None = None,
        is_domestic: bool | None = None,
        max_price: float | None = None,
        catalog_name: str | None = None,
        limit: int = 200,
    ) -> list[Product]:
        """
        Coarse SQL pre-filter. The matcher does fine-grained scoring on top.
        Keep this conservative — anything we filter out here is invisible to
        the matcher.

        catalog_name: if set, restricts results to products that appear in
        the named CatalogList (政府名录 etc.). Unmatched entries in the
        catalog are skipped.
        """
        from .models import CatalogEntry, CatalogList   # local: avoid cycle
        stmt = select(Product)
        if section:
            stmt = stmt.where(Product.section == section)
        if category:
            stmt = stmt.where(Product.category == category)
        if min_port_count is not None:
            stmt = stmt.where(
                (Product.port_count == None) | (Product.port_count >= min_port_count)  # noqa: E711
            )
        if port_speed is not None:
            stmt = stmt.where(
                (Product.port_speed == None) | (Product.port_speed == port_speed)       # noqa: E711
            )
        if layer is not None:
            stmt = stmt.where(
                (Product.layer == None) | (Product.layer == layer)                      # noqa: E711
            )
        if poe is not None:
            stmt = stmt.where((Product.poe == None) | (Product.poe == poe))             # noqa: E711
        if is_domestic is True:
            stmt = stmt.where(Product.is_domestic == True)                              # noqa: E712
        if max_price is not None:
            stmt = stmt.where(
                (Product.list_price_cny == None) | (Product.list_price_cny <= max_price)  # noqa: E711
            )
        if catalog_name is not None:
            # Inner join via CatalogEntry to restrict to products that are
            # actually in the named whitelist.
            stmt = (
                stmt.join(CatalogEntry, CatalogEntry.product_id == Product.id)
                .join(CatalogList, CatalogList.id == CatalogEntry.catalog_id)
                .where(CatalogList.name == catalog_name)
                .where(CatalogEntry.product_id != None)                                   # noqa: E711
            )
        stmt = stmt.limit(limit)
        with self.session() as s:
            return list(s.scalars(stmt))

    def bulk_insert_seed(self, rows: Iterable[dict]) -> int:
        n = 0
        for r in rows:
            self.upsert_product(r)
            n += 1
        return n


# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_db() -> Database:
    cfg = get_config()
    db = Database(cfg.storage.sqlite_path)
    db.init_schema()
    return db


def init_schema() -> None:
    """Public hook for the `scripts/init_db.py` entry point."""
    get_db().init_schema()


__all__ = ["Database", "get_db", "init_schema"]
