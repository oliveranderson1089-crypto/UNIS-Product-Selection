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
import re
import shutil
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
    archived_path: str | None = None


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
            if project is None:
                # Path didn't match (e.g. file lives in Downloads). Fall
                # back to filename-prefix matching against project names.
                project = infer_project_from_filename(
                    s, Path(report.input_path).name,
                )
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
# Filename-prefix inference (fallback when the file isn't under work_dir)
# ---------------------------------------------------------------------------
# H3C 配置器 exports follow a naming convention:
#   <project_id_or_short_name>-<description>_<YYYYMMDD>.xls
# e.g.:
#   29JD-测控间显示屏完善建设交换机_20260527.xls         → 29JD
#   中国原子能工业-生产管理系统安全可靠服务器_20260529.xls → 中国原子能工业
#   W1241-服务器_20260525.xls                            → W1241
# The leading separator-delimited token is almost always the project's
# folder name or a prefix of it. We exploit that for inference.
#
# Match priority:
#   1. Whole cleaned stem == project.name OR project.display_name (exact)
#   2. First token == project.name (exact, case-insensitive)
#   3. project.name startswith(token) (project name extends token, e.g.
#      "中国原子能工业有限公司" extends "中国原子能工业")
# At each tier, if more than one DISTINCT project matches, we return None
# (ambiguous — let the user pick manually rather than risk wrong link).

# Stamps the configurator and the user typically append to filenames.
_DATE_SUFFIX_RE   = re.compile(r"[_\-\s]?\d{6,8}$")
_PARENS_NUM_RE    = re.compile(r"[_\-\s]?\(\d+\)$")
_FINAL_MARKER_RE  = re.compile(
    r"[_\-\s]?(终版|最终版?|final|已选型|定稿|确认版)$", re.IGNORECASE,
)
# Token separators in H3C export naming
_TOKEN_SPLIT_RE   = re.compile(r"[-_ ]")

# Minimum length for a token to be considered a meaningful match. Two
# characters is too loose (would match e.g. "10" against many things).
_MIN_TOKEN_LEN = 3


def infer_project_from_filename(session, filename: str) -> Project | None:
    """
    Pick a Project whose name matches the filename's leading token.

    Returns None when nothing matches OR when the match is ambiguous
    (two+ distinct projects tie). Always safe: never guesses wrong by
    picking arbitrarily.
    """
    stem = Path(filename).stem
    cleaned = _clean_filename_stem(stem)
    if not cleaned:
        return None

    projects = list(session.scalars(select(Project)))
    if not projects:
        return None

    cleaned_lower = cleaned.lower()

    # ----- Tier 1: full cleaned stem matches a project name/display ----
    full_matches: list[Project] = []
    for p in projects:
        for cand in (p.display_name, p.name):
            if cand and cleaned_lower == cand.strip().lower():
                full_matches.append(p)
                break
    unique = {p.id: p for p in full_matches}
    if len(unique) == 1:
        return next(iter(unique.values()))
    if len(unique) > 1:
        return None   # ambiguous

    # ----- Tier 2: leading token matches a project name ----------------
    token = _TOKEN_SPLIT_RE.split(cleaned, maxsplit=1)[0].strip()
    if not token or len(token) < _MIN_TOKEN_LEN:
        return None

    token_lower = token.lower()
    exact: list[Project] = []
    startswith: list[Project] = []
    for p in projects:
        name = (p.name or "").strip()
        disp = (p.display_name or "").strip()
        name_l = name.lower()
        disp_l = disp.lower()

        if name and (name_l == token_lower or disp_l == token_lower):
            exact.append(p)
            continue
        # project.name or its display extends the token
        if name and len(name) >= _MIN_TOKEN_LEN and name_l.startswith(token_lower):
            startswith.append(p)
            continue
        if disp and len(disp) >= _MIN_TOKEN_LEN and disp_l.startswith(token_lower):
            startswith.append(p)
            continue

    pool = exact or startswith
    unique = {p.id: p for p in pool}
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None   # zero matches OR ambiguous


def _clean_filename_stem(stem: str) -> str:
    """Strip trailing date stamps, '(2)' suffixes, and final-version markers."""
    s = stem
    # Apply each cleanup repeatedly until no more match (handles
    # "_20260527 (2)" style chains)
    for _ in range(3):
        before = s
        s = _PARENS_NUM_RE.sub("", s).strip()
        s = _DATE_SUFFIX_RE.sub("", s).strip()
        s = _FINAL_MARKER_RE.sub("", s).strip()
        s = s.strip("_- ")
        if s == before:
            break
    return s


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
        archived_path=qv.archived_path,
    )


# ---------------------------------------------------------------------------
# Archive — copy the formatted output into the linked project's folder
# ---------------------------------------------------------------------------
def archive_quote_to_project(version_id: int) -> Path | None:
    """
    Copy the version's `output_file` into its linked project's folder.

    Behaviors:
      - No-op (return None) if the version has no project_id, the
        output file is gone, or the project folder is gone.
      - If the destination already exists with a different content, the
        copy is renamed `<stem>_<timestamp><suffix>` to preserve the
        prior file.
      - Updates the QuoteVersion's `archived_path` to the destination so
        the project history table can show "📂 已归档" with a link.

    Returns the destination Path on success, None on any skip.
    """
    db = get_db()
    with db.session() as s:
        qv = s.scalar(select(QuoteVersion).where(QuoteVersion.id == version_id))
        if qv is None or qv.project_id is None:
            logger.info("archive: version #%s has no project link", version_id)
            return None

        proj = s.get(Project, qv.project_id)
        if proj is None:
            logger.warning("archive: project id=%s not found", qv.project_id)
            return None

        src = Path(qv.output_file)
        if not src.exists():
            logger.warning("archive: output file gone: %s", src)
            return None

        proj_folder = Path(proj.folder_path)
        if not proj_folder.exists():
            logger.warning("archive: project folder gone: %s", proj_folder)
            return None

        dest = proj_folder / src.name
        # If a file with this name already exists AND it's a different
        # file, timestamp-suffix the new one so prior versions survive.
        if dest.exists() and dest.resolve() != src.resolve():
            try:
                same = (dest.stat().st_size == src.stat().st_size and
                        dest.stat().st_mtime == src.stat().st_mtime)
            except OSError:
                same = False
            if not same:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                dest = proj_folder / f"{src.stem}_{stamp}{src.suffix}"

        try:
            if dest.resolve() != src.resolve():
                shutil.copy(src, dest)
            qv.archived_path = str(dest.resolve())
            return dest
        except Exception:                                             # noqa: BLE001
            logger.exception("archive copy failed: %s -> %s", src, dest)
            return None


__all__ = [
    "QuoteVersionSummary",
    "record_quote_version",
    "infer_project_from_path",
    "infer_project_from_filename",
    "list_quote_versions",
    "get_quote_version",
    "delete_quote_version",
    "set_quote_version_project",
    "archive_quote_to_project",
]
