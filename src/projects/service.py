"""
Project service layer — CRUD + queries that the CLI and UI both use.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

from sqlalchemy import select

from ..storage import get_db
from ..storage.models import PROJECT_STATUSES, Project, ProjectFile


@dataclass
class ProjectSummary:
    id: int
    name: str
    display_name: str | None
    assigner: str
    customer: str | None
    status: str
    folder_path: str
    file_count: int
    has_final_quote: bool


def list_projects(
    *,
    assigner: str | None = None,
    status: str | None = None,
    customer_like: str | None = None,
) -> list[ProjectSummary]:
    """Return all projects matching the filters."""
    db = get_db()
    with db.session() as s:
        stmt = select(Project)
        if assigner:
            stmt = stmt.where(Project.assigner == assigner)
        if status:
            stmt = stmt.where(Project.status == status)
        if customer_like:
            stmt = stmt.where(Project.customer.contains(customer_like))
        stmt = stmt.order_by(Project.updated_at.desc())

        out: list[ProjectSummary] = []
        for p in s.scalars(stmt):
            files = list(s.scalars(
                select(ProjectFile).where(ProjectFile.project_id == p.id)
            ))
            has_final = any(
                f.is_final and f.kind == "quote" for f in files
            )
            out.append(ProjectSummary(
                id=p.id, name=p.name, display_name=p.display_name,
                assigner=p.assigner, customer=p.customer,
                status=p.status, folder_path=p.folder_path,
                file_count=len(files), has_final_quote=has_final,
            ))
        return out


def get_project(project_ref: str | int) -> tuple[Project, list[ProjectFile]] | None:
    """
    Look up a project by id (int / numeric string) or by `name`.

    Returns the Project row + its files, or None if not found.
    Both objects are detached from the session — safe to use after the
    with-block exits.
    """
    db = get_db()
    with db.session() as s:
        stmt = select(Project)
        try:
            pid = int(project_ref)
            stmt = stmt.where(Project.id == pid)
        except (TypeError, ValueError):
            stmt = stmt.where(Project.name == str(project_ref))
        proj = s.scalar(stmt)
        if proj is None:
            return None
        files = list(s.scalars(
            select(ProjectFile).where(ProjectFile.project_id == proj.id)
            .order_by(ProjectFile.modified_at.desc().nullslast())
        ))
        s.expunge_all()
        return proj, files


def set_status(project_ref: str | int, new_status: str) -> Project | None:
    if new_status not in PROJECT_STATUSES:
        raise ValueError(
            f"Unknown status {new_status!r}. "
            f"Valid: {', '.join(PROJECT_STATUSES)}"
        )
    db = get_db()
    with db.session() as s:
        stmt = select(Project)
        try:
            pid = int(project_ref)
            stmt = stmt.where(Project.id == pid)
        except (TypeError, ValueError):
            stmt = stmt.where(Project.name == str(project_ref))
        proj = s.scalar(stmt)
        if proj is None:
            return None
        proj.status = new_status
        s.flush()
        s.expunge(proj)
        return proj


def set_customer(project_ref: str | int, customer: str | None) -> Project | None:
    db = get_db()
    with db.session() as s:
        stmt = select(Project)
        try:
            pid = int(project_ref)
            stmt = stmt.where(Project.id == pid)
        except (TypeError, ValueError):
            stmt = stmt.where(Project.name == str(project_ref))
        proj = s.scalar(stmt)
        if proj is None:
            return None
        proj.customer = (customer or "").strip() or None
        s.flush()
        s.expunge(proj)
        return proj


def set_notes(project_ref: str | int, notes: str | None) -> Project | None:
    db = get_db()
    with db.session() as s:
        stmt = select(Project)
        try:
            pid = int(project_ref)
            stmt = stmt.where(Project.id == pid)
        except (TypeError, ValueError):
            stmt = stmt.where(Project.name == str(project_ref))
        proj = s.scalar(stmt)
        if proj is None:
            return None
        proj.notes = (notes or "").strip() or None
        s.flush()
        s.expunge(proj)
        return proj


def open_in_explorer(folder_path: str) -> bool:
    """
    Open the project folder in the OS file manager.

    Returns True if the call dispatched. Doesn't wait or care about the
    file manager's success — fire-and-forget.
    """
    if not folder_path:
        return False
    try:
        if sys.platform == "win32":
            os.startfile(folder_path)               # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder_path])
        else:
            subprocess.Popen(["xdg-open", folder_path])
        return True
    except OSError:
        return False


__all__ = [
    "ProjectSummary",
    "list_projects",
    "get_project",
    "set_status",
    "set_customer",
    "set_notes",
    "open_in_explorer",
]
