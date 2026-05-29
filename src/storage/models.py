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


# ---------------------------------------------------------------------------
# Catalog lists (政府采购名录 etc.)
# ---------------------------------------------------------------------------
# A "catalog list" is an authoritative external whitelist — e.g.
# "2025-V1 政府采购名录承诺函". Products on the list are a subset of our
# product catalog (usually a subset of 创新产品).
#
# Two-table design (not just a foreign key on Product):
#   - CatalogList: metadata about the whitelist itself (source, name, when
#     imported, sha256 of source for change detection)
#   - CatalogEntry: one row per model code in the list. `product_id` is
#     nullable because the source might reference codes that aren't (yet)
#     in our product table — we keep the raw code so re-matching after a
#     fresh crawl can fill them in.
#
# This avoids losing data when the source has codes we haven't crawled yet.

class CatalogList(Base):
    __tablename__ = "catalog_lists"

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    name:         Mapped[str] = mapped_column(String(128), unique=True, index=True)
    source_file:  Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extractor:    Mapped[str | None] = mapped_column(String(32), nullable=True)  # "claude" | "ocr"
    imported_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes:        Mapped[str | None] = mapped_column(String(1024), nullable=True)

    entries:      Mapped[list["CatalogEntry"]] = relationship(
        back_populates="catalog", cascade="all, delete-orphan",
    )


class CatalogEntry(Base):
    __tablename__ = "catalog_entries"
    __table_args__ = (
        UniqueConstraint("catalog_id", "raw_model_code", name="uq_entry_per_catalog"),
    )

    id:             Mapped[int] = mapped_column(Integer, primary_key=True)
    catalog_id:     Mapped[int] = mapped_column(
        ForeignKey("catalog_lists.id", ondelete="CASCADE"), index=True,
    )
    raw_model_code: Mapped[str] = mapped_column(String(128), index=True)
    # Nullable: matched product if/when we find it in the catalog.
    product_id:     Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # Audit: what method matched the product (exact / normalized / fuzzy / none)
    match_method:   Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes:          Mapped[str | None] = mapped_column(String(256), nullable=True)

    catalog:        Mapped[CatalogList] = relationship(back_populates="entries")


# ---------------------------------------------------------------------------
# Projects (售前项目跟踪)
# ---------------------------------------------------------------------------
# A "project" maps 1:1 to a folder under the configured `work_dir`, typically:
#   D:\Work\紫光恒越\日常工作\<assigner>\<project_code_or_name>\
# where `assigner` is the person who handed the deal to you (also acts as
# our "owner" of record, since you do the work). Files inside the project
# folder (tender PDFs, requirement images, quote .xls exports) are tracked
# as ProjectFile rows so the UI can show "what's in this project" without
# re-scanning the disk every time.

# Closed set of statuses — exposed by config for easy localization, but
# the canonical Chinese strings live here so DB queries are consistent.
PROJECT_STATUSES = ("进行中", "中标", "未中标", "结案")
DEFAULT_PROJECT_STATUS = "进行中"


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("assigner", "name", name="uq_project_per_assigner"),
    )

    id:            Mapped[int] = mapped_column(Integer, primary_key=True)
    # Short folder name as discovered on disk, e.g. "29JD" or "821中核环保"
    name:          Mapped[str] = mapped_column(String(128), index=True)
    # Long, descriptive name auto-derived from file names inside the folder
    display_name:  Mapped[str | None] = mapped_column(String(256), nullable=True)
    # Person who assigned the project (= the subfolder under work_dir)
    assigner:      Mapped[str] = mapped_column(String(64), index=True)
    # End customer — extractable from project name sometimes, otherwise manual
    customer:      Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Absolute path to project folder
    folder_path:   Mapped[str] = mapped_column(String(512), unique=True, index=True)
    status:        Mapped[str] = mapped_column(String(16), default=DEFAULT_PROJECT_STATUS, index=True)
    notes:         Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:    Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )

    files:         Mapped[list["ProjectFile"]] = relationship(
        back_populates="project", cascade="all, delete-orphan",
    )


class ProjectFile(Base):
    """A single file (quote / tender / requirement / other) inside a project."""

    __tablename__ = "project_files"
    __table_args__ = (
        UniqueConstraint("project_id", "path", name="uq_file_per_project"),
    )

    id:           Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id:   Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True,
    )
    name:         Mapped[str] = mapped_column(String(256))       # file basename
    path:         Mapped[str] = mapped_column(String(1024))      # absolute path
    # Heuristic classification — "quote" / "requirement" / "config" / "other"
    kind:         Mapped[str] = mapped_column(String(32), default="other", index=True)
    is_final:     Mapped[bool] = mapped_column(Boolean, default=False)
    size_bytes:   Mapped[int | None] = mapped_column(Integer, nullable=True)
    modified_at:  Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sha256:       Mapped[str | None] = mapped_column(String(64), nullable=True)

    project:      Mapped[Project] = relationship(back_populates="files")


# ---------------------------------------------------------------------------
# QuoteVersion (报价单格式化版本)
# ---------------------------------------------------------------------------
# Records EACH run of the quote formatter as an immutable version. Two
# reasons we want this instead of just trusting ProjectFile rows:
#
#   1. Audit trail — when a quote went out wrong, you want to know which
#      rules ran on which input, when, with what conversion method.
#   2. Re-runs — the same source .xls may be reformatted multiple times
#      (rule set changed, BOM updated). Each pass produces a new output;
#      we keep them all linked to the same project for traceability.
#
# `project_id` is nullable: not every quote belongs to a tracked project
# (one-off testing, files outside work_dir). Orphan versions are still
# kept so the formatter UI can show recent runs even before project
# linking happens.
#
# `rule_report` is a JSON snapshot of the rule names + applied flag +
# changes + warnings — same shape as the FormatReport returned by the
# formatter. Stored verbatim so the UI can render it the same way later.

class QuoteVersion(Base):
    __tablename__ = "quote_versions"

    id:                Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id:        Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    # Source = the .xls/.xlsx file the user supplied as input
    source_file:       Mapped[str] = mapped_column(String(1024))
    source_filename:   Mapped[str] = mapped_column(String(256), index=True)
    # Output = the .formatted.xlsx the formatter wrote
    output_file:       Mapped[str] = mapped_column(String(1024))

    generated_at:      Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True,
    )
    # "com" or "openpyxl" — which code path actually did the work
    formatter_method:  Mapped[str] = mapped_column(String(16))
    # "com" / "xlrd" / None — only set when the input was a .xls that
    # had to be auto-converted to .xlsx
    conversion_method: Mapped[str | None] = mapped_column(String(16), nullable=True)

    applied_count:     Mapped[int] = mapped_column(Integer, default=0)
    total_rules:       Mapped[int] = mapped_column(Integer, default=0)
    # JSON: list of {name, applied, changes:[...], warnings:[...]}
    rule_report:       Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True,
    )
    notes:             Mapped[str | None] = mapped_column(String(1024), nullable=True)

    project:           Mapped[Project | None] = relationship()


__all__ = [
    "Base", "Product", "ProductPDF", "CrawlRecord",
    "CatalogList", "CatalogEntry",
    "Project", "ProjectFile", "QuoteVersion",
    "PROJECT_STATUSES", "DEFAULT_PROJECT_STATUS",
]
