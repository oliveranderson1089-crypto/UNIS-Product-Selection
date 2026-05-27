"""
ORM models for the product catalog.

We intentionally keep the schema thin and JSON-friendly:
- "Hard" fields are columns so SQL filtering / ordering is cheap.
- Everything else lives in `extra_specs` (JSON) so the crawler doesn't need
  a schema migration every time a new spec appears in a brochure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Product(Base):
    """One row per product model (e.g. UNIS S12600-CR-G)."""

    __tablename__ = "products"

    id:           Mapped[int]      = mapped_column(Integer, primary_key=True)
    model:        Mapped[str]      = mapped_column(String(128), unique=True, index=True)
    series:       Mapped[str | None] = mapped_column(String(128), nullable=True)
    # ---- where on the site this product lives ----
    # section is "innovation" (创新产品) or "general" (通用产品), or NULL for
    # demo/seed data. Used to scope queries like "show me 创新 switches only".
    section:      Mapped[str | None] = mapped_column(String(32),  nullable=True, index=True)
    category:     Mapped[str | None] = mapped_column(String(64),  nullable=True, index=True)
    sub_category: Mapped[str | None] = mapped_column(String(64),  nullable=True, index=True)
    name:         Mapped[str | None] = mapped_column(String(256), nullable=True)
    description:  Mapped[str | None] = mapped_column(String(2048), nullable=True)
    page_url:     Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ---- common queryable specs -------------------------------------------
    port_count:               Mapped[int | None]   = mapped_column(Integer, nullable=True, index=True)
    port_speed:               Mapped[str | None]   = mapped_column(String(16), nullable=True, index=True)
    uplink_speed:             Mapped[str | None]   = mapped_column(String(16), nullable=True)
    switching_capacity_gbps:  Mapped[float | None] = mapped_column(Float, nullable=True)
    forwarding_rate_mpps:     Mapped[float | None] = mapped_column(Float, nullable=True)
    layer:                    Mapped[str | None]   = mapped_column(String(8), nullable=True)
    poe:                      Mapped[bool | None]  = mapped_column(Boolean, nullable=True)
    redundant_power:          Mapped[bool | None]  = mapped_column(Boolean, nullable=True)
    rack_units:               Mapped[int | None]   = mapped_column(Integer, nullable=True)
    is_domestic:              Mapped[bool | None]  = mapped_column(Boolean, nullable=True, index=True)

    # ---- compute/storage placeholders (used when category expands) ---------
    cpu_cores:    Mapped[int | None]   = mapped_column(Integer, nullable=True)
    memory_gb:    Mapped[int | None]   = mapped_column(Integer, nullable=True)
    storage_tb:   Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---- pricing (optional, often unknown) --------------------------------
    list_price_cny: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ---- extras: anything the parser found that doesn't fit above ---------
    extra_specs:  Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # ---- bookkeeping ------------------------------------------------------
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:   Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )

    pdfs:         Mapped[list["ProductPDF"]] = relationship(
        back_populates="product", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:                    # pragma: no cover
        return f"<Product {self.model!r}>"


class ProductPDF(Base):
    """A brochure / datasheet PDF associated with a product."""

    __tablename__ = "product_pdfs"
    __table_args__ = (UniqueConstraint("product_id", "url", name="uq_pdf_per_url"),)

    id:         Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    title:      Mapped[str | None] = mapped_column(String(256), nullable=True)
    url:        Mapped[str]        = mapped_column(String(512))
    local_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sha256:     Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    product:    Mapped[Product] = relationship(back_populates="pdfs")


class CrawlRecord(Base):
    """Per-URL crawl log so we know what we've seen and when."""

    __tablename__ = "crawl_records"

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    url:          Mapped[str] = mapped_column(String(512), unique=True, index=True)
    last_status:  Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    etag:         Mapped[str | None] = mapped_column(String(128), nullable=True)
    note:         Mapped[str | None] = mapped_column(String(512), nullable=True)


__all__ = ["Base", "Product", "ProductPDF", "CrawlRecord"]
