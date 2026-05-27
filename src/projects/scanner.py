"""
Filesystem scanner — discovers projects and their files.

Expected layout under `work_dir`:

    <work_dir>/
        <assigner_name>/                  ← person who handed you the project
            <project_code_or_name>/        ← one folder = one Project row
                <files>                    ← tracked as ProjectFile rows
            ...
        <assigner_name>/
            ...

We:
  - Walk one level: assigner dirs only at the top
  - For each assigner, walk one level: project dirs
  - Inside each project, walk recursively but bounded depth so we don't
    chase deeply nested archives accidentally

Idempotent — re-running upserts (no duplicates). Files that disappeared
from disk get DELETED from the DB so the inventory stays honest.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from ..config import get_config
from ..storage import get_db
from ..storage.models import (
    DEFAULT_PROJECT_STATUS,
    Project,
    ProjectFile,
)
from .classifier import classify_file

logger = logging.getLogger(__name__)


# Max depth for project file walk relative to project folder (0 = direct
# children only, 2 = up to two levels). Two is enough for nested
# "终版/" / "客户提供/" subfolders without slipping into bloated archives.
MAX_PROJECT_DEPTH = 2

# File-size cap; anything bigger gets recorded with size only, no sha256
# (computing hash on 100MB+ files makes scans slow without business value).
SHA_SIZE_LIMIT = 50 * 1024 * 1024


@dataclass
class ScanReport:
    work_dir: str
    assigners_seen: int = 0
    projects_seen: int = 0
    projects_new: int = 0
    files_total: int = 0
    files_new: int = 0
    files_removed: int = 0
    skipped: list[str] = field(default_factory=list)


def scan_projects() -> ScanReport:
    """Walk the configured work_dir and upsert projects + files."""
    cfg = get_config()
    root = cfg.projects.work_dir
    report = ScanReport(work_dir=str(root))

    if not root.exists():
        logger.warning("work_dir does not exist: %s", root)
        return report

    skip_top = set(cfg.projects.skip_top_level)
    skip_file_patterns = list(cfg.projects.skip_files)

    db = get_db()
    with db.session() as s:
        # Pre-fetch existing projects so we can detect REMOVALS (project
        # folder deleted) and avoid duplicate rows.
        existing_projects = {p.folder_path: p for p in s.scalars(select(Project))}
        seen_paths: set[str] = set()

        for assigner_dir in sorted(_iter_dirs(root)):
            if assigner_dir.name in skip_top:
                report.skipped.append(f"top: {assigner_dir.name}")
                continue
            report.assigners_seen += 1
            assigner_name = assigner_dir.name

            for project_dir in sorted(_iter_dirs(assigner_dir)):
                report.projects_seen += 1
                folder_path = str(project_dir.resolve())
                seen_paths.add(folder_path)

                proj = existing_projects.get(folder_path)
                files_meta = _scan_project_files(project_dir, skip_file_patterns)
                display = _derive_display_name(project_dir, files_meta)

                if proj is None:
                    proj = Project(
                        name=project_dir.name,
                        display_name=display,
                        assigner=assigner_name,
                        folder_path=folder_path,
                        status=DEFAULT_PROJECT_STATUS,
                    )
                    s.add(proj)
                    s.flush()
                    report.projects_new += 1
                else:
                    # Refresh derived metadata in case files changed
                    proj.display_name = display or proj.display_name
                    proj.assigner = assigner_name
                    # Don't touch status — that's user-curated

                files_changed = _sync_project_files(s, proj, files_meta)
                report.files_total += len(files_meta)
                report.files_new += files_changed["added"]
                report.files_removed += files_changed["removed"]

        # Delete projects whose folder is gone
        for folder_path, proj in existing_projects.items():
            if folder_path not in seen_paths and Path(folder_path).parent.exists():
                # Only delete if the PARENT (work_dir/<assigner>) still
                # exists — otherwise the user might just have unplugged the
                # drive, and we shouldn't wipe their history.
                logger.info("Removing vanished project: %s", folder_path)
                s.delete(proj)

    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@dataclass
class _FileMeta:
    path: Path
    name: str
    kind: str
    is_final: bool
    size: int
    modified: datetime
    sha256: str | None


def _iter_dirs(parent: Path):
    try:
        for child in parent.iterdir():
            if child.is_dir() and not child.name.startswith((".", "$")):
                yield child
    except (PermissionError, FileNotFoundError):
        return


def _is_skipped(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _scan_project_files(
    project_dir: Path,
    skip_patterns: list[str],
) -> list[_FileMeta]:
    out: list[_FileMeta] = []
    for path in _walk_files(project_dir, max_depth=MAX_PROJECT_DEPTH):
        if _is_skipped(path.name, skip_patterns):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        kind, is_final = classify_file(path)
        sha = _maybe_sha256(path, stat.st_size)
        out.append(_FileMeta(
            path=path,
            name=path.name,
            kind=kind,
            is_final=is_final,
            size=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime),
            sha256=sha,
        ))
    return out


def _walk_files(root: Path, *, max_depth: int):
    """Yield files under `root`, capped at max_depth levels."""
    root = root.resolve()
    base_depth = len(root.parts)
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            depth = len(path.parts) - base_depth - 1   # -1 for the filename
            if depth > max_depth:
                continue
            yield path
    except (PermissionError, OSError):
        return


def _maybe_sha256(path: Path, size: int) -> str | None:
    if size > SHA_SIZE_LIMIT:
        return None
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (PermissionError, OSError):
        return None


def _derive_display_name(project_dir: Path, files: list[_FileMeta]) -> str | None:
    """
    Pick a human-friendly project title.

    Heuristic: when the folder name is a short code (like "29JD") but a
    file inside has a long descriptive name (like
    "29JD-测控间显示屏完善建设交换机_20260527.xls"), prefer the file stem
    with date/extension stripped.
    """
    folder_name = project_dir.name
    if len(folder_name) > 12:
        return folder_name      # already descriptive enough

    best = folder_name
    for f in files:
        # Skip images and short names
        if f.kind == "image":
            continue
        stem = f.path.stem
        # Strip trailing _YYYYMMDD and ()-wrapped notes
        stem = re.sub(r"[_\-]?\d{6,8}$", "", stem).strip("_-. ")
        stem = re.sub(r"\([^)]*\)$", "", stem).strip()
        if folder_name.lower() in stem.lower() and len(stem) > len(best):
            best = stem
    return best


def _sync_project_files(session, proj: Project, found: list[_FileMeta]) -> dict[str, int]:
    """Reconcile DB rows with files actually present in `found`."""
    existing = {f.path: f for f in session.scalars(
        select(ProjectFile).where(ProjectFile.project_id == proj.id)
    )}
    seen_paths: set[str] = set()
    added = 0
    for m in found:
        path_str = str(m.path.resolve())
        seen_paths.add(path_str)
        row = existing.get(path_str)
        if row is None:
            session.add(ProjectFile(
                project_id=proj.id,
                name=m.name, path=path_str,
                kind=m.kind, is_final=m.is_final,
                size_bytes=m.size, modified_at=m.modified, sha256=m.sha256,
            ))
            added += 1
        else:
            row.name = m.name
            row.kind = m.kind
            row.is_final = m.is_final
            row.size_bytes = m.size
            row.modified_at = m.modified
            if m.sha256 and m.sha256 != row.sha256:
                row.sha256 = m.sha256

    removed = 0
    for path_str, row in existing.items():
        if path_str not in seen_paths:
            session.delete(row)
            removed += 1
    return {"added": added, "removed": removed}


__all__ = ["scan_projects", "ScanReport"]
