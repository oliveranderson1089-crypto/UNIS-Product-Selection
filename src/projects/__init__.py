"""Project management — scan filesystem, track tenders, archive quotes."""

from .quote_versions import (
    QuoteVersionSummary,
    delete_quote_version,
    get_quote_version,
    infer_project_from_path,
    list_quote_versions,
    record_quote_version,
    set_quote_version_project,
)
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
    # quote versions
    "QuoteVersionSummary",
    "record_quote_version",
    "list_quote_versions",
    "get_quote_version",
    "delete_quote_version",
    "set_quote_version_project",
    "infer_project_from_path",
]
