"""Project management — scan filesystem, track tenders, archive quotes."""

from .scanner import ScanReport, scan_projects
from .service import (
    ProjectSummary,
    get_project,
    list_projects,
    open_in_explorer,
    set_customer,
    set_notes,
    set_status,
)

__all__ = [
    "scan_projects",
    "ScanReport",
    "ProjectSummary",
    "list_projects",
    "get_project",
    "set_status",
    "set_customer",
    "set_notes",
    "open_in_explorer",
]
