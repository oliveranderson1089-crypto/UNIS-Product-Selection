"""
Catalog importer — end-to-end orchestration.

Given a 名录 file and a name:
  1. Pick an extractor (Claude if available, OCR fallback) or honor `--extractor`.
  2. Extract text from the PDF.
  3. Parse model codes from the text.
  4. Match each code to a Product row (exact / normalized / fuzzy).
  5. Upsert a CatalogList and its CatalogEntry rows in SQLite.
  6. Return a report so the CLI can print actionable stats.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from ..storage import get_db
from ..storage.models import CatalogEntry, CatalogList
from .extractors import get_best_available_extractor, get_extractor
from .matcher import match_codes_to_products
from .model_codes import extract_from_extractor_output

logger = logging.getLogger(__name__)


@dataclass
class ImportReport:
    catalog_name: str
    extractor_used: str
    total_codes: int = 0
    matched: int = 0
    unmatched: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    unmatched_codes: list[str] = field(default_factory=list)


def import_catalog(
    pdf_path: Path,
    *,
    name: str,
    extractor: str | None = None,
    notes: str | None = None,
    replace: bool = True,
) -> ImportReport:
    """
    Import (or re-import) a 名录 file as a named catalog list.

    Args:
        pdf_path: source file path (currently only PDF; Excel TODO).
        name: short name for the catalog (e.g. "2025-V1 政府名录").
              Used in CLI and `select --catalog NAME`.
        extractor: "claude" | "ocr" | None (auto-pick best available).
        notes: free-text annotation stored on the CatalogList row.
        replace: if True and a catalog with the same `name` exists, its
                 entries are wiped and re-imported. If False, ImportError.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    ext = (
        get_extractor(extractor) if extractor
        else get_best_available_extractor()
    )
    logger.info("Importing %s as %r using extractor=%s", pdf_path.name, name, ext.name)

    # ---- 1) extract + parse ------------------------------------------------
    text = ext.extract_text(pdf_path)
    codes = extract_from_extractor_output(text)
    logger.info("Extractor returned %d model code(s)", len(codes))

    sha = _sha256(pdf_path)
    db = get_db()
    report = ImportReport(catalog_name=name, extractor_used=ext.name,
                          total_codes=len(codes))

    # ---- 2) upsert CatalogList ---------------------------------------------
    with db.session() as s:
        existing = s.scalar(select(CatalogList).where(CatalogList.name == name))
        if existing and not replace:
            raise ValueError(f"Catalog {name!r} already exists; pass replace=True to overwrite.")

        if existing:
            s.execute(delete(CatalogEntry).where(CatalogEntry.catalog_id == existing.id))
            existing.source_file = str(pdf_path)
            existing.source_sha256 = sha
            existing.extractor = ext.name
            existing.imported_at = datetime.utcnow()
            existing.notes = notes
            catalog = existing
        else:
            catalog = CatalogList(
                name=name, source_file=str(pdf_path), source_sha256=sha,
                extractor=ext.name, notes=notes,
            )
            s.add(catalog)
            s.flush()

        # ---- 3) match + insert entries -------------------------------------
        matches = match_codes_to_products(s, codes)
        for m in matches:
            s.add(CatalogEntry(
                catalog_id=catalog.id,
                raw_model_code=m.raw_code,
                product_id=m.product_id,
                match_method=m.method,
            ))
            report.by_method[m.method] = report.by_method.get(m.method, 0) + 1
            if m.product_id is None:
                report.unmatched += 1
                report.unmatched_codes.append(m.raw_code)
            else:
                report.matched += 1

    return report


def rematch_all() -> dict[str, ImportReport]:
    """
    Re-run product matching for every catalog without re-extracting from PDFs.

    Useful after a fresh crawl populates new products — codes that were
    "unmatched" might now have a Product to point at.
    """
    db = get_db()
    out: dict[str, ImportReport] = {}
    with db.session() as s:
        catalogs = list(s.scalars(select(CatalogList)))
        for cat in catalogs:
            entries = list(s.scalars(
                select(CatalogEntry).where(CatalogEntry.catalog_id == cat.id)
            ))
            raw_codes = [e.raw_model_code for e in entries]
            matches = match_codes_to_products(s, raw_codes)
            report = ImportReport(
                catalog_name=cat.name, extractor_used=cat.extractor or "?",
                total_codes=len(raw_codes),
            )
            # Update in place
            by_code = {m.raw_code: m for m in matches}
            for e in entries:
                m = by_code.get(e.raw_model_code)
                if m is None:
                    continue
                e.product_id = m.product_id
                e.match_method = m.method
                report.by_method[m.method] = report.by_method.get(m.method, 0) + 1
                if m.product_id is None:
                    report.unmatched += 1
                    report.unmatched_codes.append(e.raw_model_code)
                else:
                    report.matched += 1
            out[cat.name] = report
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = ["import_catalog", "rematch_all", "ImportReport"]
