"""
Quote-version service — record, query, delete formatting runs.

Each time `format_quote()` finishes, the caller (CLI or UI) can call
`record_quote_version(report, project_ref=...)` to drop a row into the
`quote_versions` table. The row links to a Project when possible (by
explicit ID, by name, or by inferring from the source file's path).

The recording is a pure side-effect: failure to record never breaks the
formatter — we just log a warning.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from ..storage import get_db
from ..storage.models import Project, QuoteVersion

logger = logging.getLogger(__name__)


@dataclass
class QuoteVersionSummary:
    """Detached view of a QuoteVersion row, safe to use after session exit."""
    id: int
    project_id: int | None
    project_name: str | None
    project_display_name: str | None
    source_file: str
    source_filename: str
    output_file: str
    generated_at: datetime
    formatter_method: str
    conversion_method: str | None
    applied_count: int
    total_rules: int
    rule_report: list[dict] = field(default_factory=list)
    notes: str | None = None


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
def record_quote_version(
    report,
    *,
    project_ref: str | int | None = None,
    auto_infer: bool = True,
    notes: str | None = None,
) -> QuoteVersionSummary | None:
    """
    Persist a QuoteVersion row for a finished `format_quote()` report.

    `report` is a `quotes.FormatReport` (duck-typed; we only read attrs).
    `project_ref` — explicit override (id, numeric string, or project name).
    `auto_infer` — if True AND project_ref is None, try to discover the
        project from the source path's filesystem layout.

    Returns the saved summary, or None on failure (logged, not raised).
    """
    try:
        return _do_record(report, project_ref, auto_infer, notes)
    except Exception:                                                 # noqa: BLE001
        logger.exception("record_quote_version failed")
        return None


def _do_record(report, project_ref, auto_infer, notes) -> QuoteVersionSummary:
    db = get_db()
    with db.session() as s:
        # ---- resolve project link --------------------------------------
        project_id = None
        if project_ref is not None:
            project = _resolve_project(s, project_ref)
            if project is not None:
                project_id = project.id
            else:
                logger.warning(
                    "Project ref %r not found; recording orphan version",
                    project_ref,
                )
        elif auto_infer:
            project = infer_project_from_path(s, Path(report.input_path))
            if project is not None:
                project_id = project.id

        # ---- snapshot rule report as JSON -----------------------------
        rules_json = [
            {
                "name": r.name,
                "applied": r.applied,
                "changes": list(r.changes),
                "warnings": list(r.warnings),
            }
            for r in report.rule_results
        ]

        src_path = Path(report.input_path)
        qv = QuoteVersion(
            project_id=project_id,
            source_file=str(src_path.resolve()),
            source_filename=src_path.name,
            output_file=str(Path(report.output_path).resolve()),
            generated_at=datetime.utcnow(),
            formatter_method=report.method,
            conversion_method=getattr(report, "conversion_method", None),
            applied_count=report.applied_count,
            total_rules=len(report.rule_results),
            rule_report=rules_json,
            notes=notes,
        )
        s.add(qv)
        s.flush()

        return _to_summary(s, qv)


# ---------------------------------------------------------------------------
# Project inference
# ---------------------------------------------------------------------------
def infer_project_from_path(session, file_path: Path) -> Project | None:
    """
    Walk up `file_path` directories and match against any registered project.

    Strategy:
      1. resolve `file_path` to an absolute path
      2. for each ancestor directory, check if any Project's folder_path
         equals it (exact match) or is a parent of it (prefix match)
      3. return the deepest-matching project (most specific wins)

    Returns None if the file isn't inside any tracked project folder.
    """
    try:
        file_path = file_path.resolve()
    except OSError:
        return None

    projects = list(session.scalars(select(Project)))
    if not projects:
        return None

    file_parts = file_path.parts
    best: tuple[int, Project] | None = None
    for proj in projects:
        try:
            pf = Path(proj.folder_path).resolve()
        except OSError:
            continue
        pf_parts = pf.parts
        # file must be UNDER project folder (or equal it — file IS the folder)
        if len(pf_parts) > len(file_parts):
            continue
        if file_parts[:len(pf_parts)] != pf_parts:
            continue
        depth = len(pf_parts)
        if best is None or depth > best[0]:
            best = (depth, proj)
    return best[1] if best else None


def _resolve_project(session, ref: str | int) -> Project | None:
    """Look up Project by integer id or by exact `name`."""
    stmt = select(Project)
    try:
        pid = int(ref)
        stmt = stmt.where(Project.id == pid)
    except (TypeError, ValueError):
        stmt = stmt.where(Project.name == str(ref))
    return session.scalar(stmt)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def list_quote_versions(
    *,
    project_id: int | None = None,
    limit: int = 50,
    include_orphans: bool = True,
) -> list[QuoteVersionSummary]:
    """
    Newest-first list of recorded versions.

    `project_id` filters to one project; pass None to see all.
    `include_orphans` controls whether to include rows where project_id is
    NULL (set False when listing per-project history).
    """
    db = get_db()
    with db.session() as s:
        stmt = select(QuoteVersion).order_by(QuoteVersion.generated_at.desc())
        if project_id is not None:
            stmt = stmt.where(QuoteVersion.project_id == project_id)
        elif not include_orphans:
            stmt = stmt.where(QuoteVersion.project_id != None)        # noqa: E711
        stmt = stmt.limit(limit)
        return [_to_summary(s, qv) for qv in s.scalars(stmt)]


def get_quote_version(version_id: int) -> QuoteVersionSummary | None:
    db = get_db()
    with db.session() as s:
        qv = s.scalar(select(QuoteVersion).where(QuoteVersion.id == version_id))
        if qv is None:
            return None
        return _to_summary(s, qv)


def delete_quote_version(version_id: int) -> bool:
    db = get_db()
    with db.session() as s:
        result = s.execute(
            delete(QuoteVersion).where(QuoteVersion.id == version_id)
        )
        return result.rowcount > 0


def set_quote_version_project(version_id: int, project_ref: str | int | None) -> bool:
    """Reassign (or clear) the project link on a version."""
    db = get_db()
    with db.session() as s:
        qv = s.scalar(select(QuoteVersion).where(QuoteVersion.id == version_id))
        if qv is None:
            return False
        if project_ref in (None, "", "0"):
            qv.project_id = None
            return True
        proj = _resolve_project(s, project_ref)
        if proj is None:
            return False
        qv.project_id = proj.id
        return True


# ---------------------------------------------------------------------------
def _to_summary(session, qv: QuoteVersion) -> QuoteVersionSummary:
    """Materialize a detached summary so the session can close safely."""
    proj_name = None
    proj_display = None
    if qv.project_id is not None:
        proj = session.get(Project, qv.project_id)
        if proj is not None:
            proj_name = proj.name
            proj_display = proj.display_name
    return QuoteVersionSummary(
        id=qv.id,
        project_id=qv.project_id,
        project_name=proj_name,
        project_display_name=proj_display,
        source_file=qv.source_file,
        source_filename=qv.source_filename,
        output_file=qv.output_file,
        generated_at=qv.generated_at,
        formatter_method=qv.formatter_method,
        conversion_method=qv.conversion_method,
        applied_count=qv.applied_count,
        total_rules=qv.total_rules,
        rule_report=list(qv.rule_report or []),
        notes=qv.notes,
    )


__all__ = [
    "QuoteVersionSummary",
    "record_quote_version",
    "infer_project_from_path",
    "list_quote_versions",
    "get_quote_version",
    "delete_quote_version",
    "set_quote_version_project",
]
