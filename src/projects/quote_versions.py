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
# Match priority (each tier returns None if 2+ DISTINCT projects tie —
# ambiguous, let the user pick manually rather than risk a wrong link):
#   1.  Whole cleaned stem == project.name OR display_name (exact)
#   2a. project.name is a SEPARATOR-BOUNDED prefix of the cleaned stem —
#       "57" matches "57-GPU服务器" but NOT "57S-第四批存储" (the boundary
#       check is what makes short 2-char codes safe). Longest name wins.
#   2b. the filename's leading token is a prefix of project.name —
#       "中国原子能工业" → "中国原子能工业有限公司" (folder name longer than
#       what the export put in the filename). Legacy long-token path.

# Stamps the configurator and the user typically append to filenames.
_DATE_SUFFIX_RE   = re.compile(r"[_\-\s]?\d{6,8}$")
_PARENS_NUM_RE    = re.compile(r"[_\-\s]?\(\d+\)$")
_FINAL_MARKER_RE  = re.compile(
    r"[_\-\s]?(终版|最终版?|final|已选型|定稿|确认版)$", re.IGNORECASE,
)
# Token separators in H3C export naming
_TOKEN_SPLIT_RE   = re.compile(r"[-_ ]")
# Same set, as literals, for the boundary-prefix check in Tier 2a.
_SEPS = ("-", "_", " ")

# Minimum length for a project NAME to be eligible for boundary-prefix
# matching (Tier 2a). Short H3C codes are routinely 2 chars ("57", "97"),
# and the separator boundary keeps them safe, so 2 is allowed here.
_MIN_NAME_LEN = 2

# Minimum length for a filename TOKEN to drive the looser "project name
# EXTENDS the token" match (Tier 2b). Kept at 3 so a 2-char token can't
# sweep up every project that happens to start with those two chars.
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
        return None   # ambiguous exact match — don't fall through to looser tiers

    # ----- Tier 2a: project name is a separator-bounded prefix ----------
    # "57" matches "57-GPU服务器" (boundary after the code) but NOT
    # "57S-第四批存储" (no boundary — "S" follows). Score by matched-name
    # length so the most specific name wins ("585" over a stray "58").
    best_len = 0
    best: list[Project] = []
    for p in projects:
        matched = 0
        for cand in (p.name, p.display_name):
            if not cand:
                continue
            cl = cand.strip().lower()
            if len(cl) < _MIN_NAME_LEN:
                continue
            if cleaned_lower == cl or any(
                cleaned_lower.startswith(cl + sep) for sep in _SEPS
            ):
                matched = max(matched, len(cl))
        if matched == 0:
            continue
        if matched > best_len:
            best_len, best = matched, [p]
        elif matched == best_len:
            best.append(p)
    if best:
        unique = {p.id: p for p in best}
        if len(unique) == 1:
            return next(iter(unique.values()))
        return None   # 2+ distinct projects tie on name length — ambiguous

    # ----- Tier 2b: filename leading token is a prefix of project name --
    # Handles the inverse: the folder name is LONGER than what the export
    # wrote into the filename (e.g. token "中国原子能工业" →
    # project "中国原子能工业有限公司").
    token = _TOKEN_SPLIT_RE.split(cleaned, maxsplit=1)[0].strip()
    if not token or len(token) < _MIN_TOKEN_LEN:
        return None
    token_lower = token.lower()
    extends: list[Project] = []
    for p in projects:
        for cand in (p.name, p.display_name):
            if not cand:
                continue
            cl = cand.strip().lower()
            if len(cl) >= _MIN_TOKEN_LEN and cl.startswith(token_lower):
                extends.append(p)
                break
    unique = {p.id: p for p in extends}
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
# Strips the formatter's ".formatted" infix so the cleaned stem lines up
# with sub-folder names (output is "<name>_<date>.formatted.xlsx").
_FORMATTED_INFIX_RE = re.compile(r"\.formatted$", re.IGNORECASE)


def _resolve_archive_dir(proj_folder: Path, output_filename: str) -> Path:
    """
    Pick the deepest sensible folder under `proj_folder` to drop the archive.

    The work-dir layout allows an optional sub-project level:

        <assigner>/<customer>/                   ← proj_folder (level 1)
        <assigner>/<customer>/<sub_project>/     ← optional (level 2)

    When `proj_folder` has sub-folders and one matches the output file's
    name, the file is archived INTO that sub-folder; otherwise it lands
    directly in `proj_folder`. This implements the user's rule: "归档时
    继续查找是否还有下一级,没有才存到当前的一级文件夹".

    Matching (deliberately conservative — never guess wrong):
      - exact: cleaned stem == sub-folder name                  (best)
      - prefix: cleaned stem startswith sub-folder name         (folder is
        a meaningful prefix of the file, e.g. file "57-GPU服务器_20260529"
        → folder "57-GPU服务器")
    A bare "folder name extends the stem" is NOT matched (too loose for
    short codes). If two sub-folders tie for best, fall back to proj_folder.
    """
    try:
        subdirs = [
            d for d in proj_folder.iterdir()
            if d.is_dir() and not d.name.startswith((".", "$"))
        ]
    except OSError:
        return proj_folder
    if not subdirs:
        return proj_folder

    stem = Path(output_filename).stem            # "<name>_<date>.formatted"
    stem = _FORMATTED_INFIX_RE.sub("", stem)     # drop ".formatted"
    stem = _clean_filename_stem(stem)            # drop date / (n) / 终版
    if not stem:
        return proj_folder
    stem_l = stem.lower()

    scored: list[tuple[int, Path]] = []
    for d in subdirs:
        dn = d.name.strip().lower()
        if not dn:
            continue
        if stem_l == dn:
            scored.append((10_000, d))                       # exact match
        elif len(dn) >= _MIN_TOKEN_LEN and stem_l.startswith(dn):
            scored.append((len(dn), d))                      # folder is a prefix
    if not scored:
        return proj_folder
    scored.sort(key=lambda t: t[0], reverse=True)
    # Ambiguity guard: two equally-good matches → don't guess, use level 1.
    if len(scored) >= 2 and scored[0][0] == scored[1][0]:
        return proj_folder
    return scored[0][1]


def _safe_dest(dest_dir: Path, src: Path) -> Path:
    """Destination path inside `dest_dir`, timestamp-suffixed on conflict.

    If a different file with the same name already exists, append
    `_<timestamp>` to the stem so prior archived versions survive.
    """
    dest = dest_dir / src.name
    if dest.exists() and dest.resolve() != src.resolve():
        try:
            same = (dest.stat().st_size == src.stat().st_size and
                    dest.stat().st_mtime == src.stat().st_mtime)
        except OSError:
            same = False
        if not same:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = dest_dir / f"{src.stem}_{stamp}{src.suffix}"
    return dest


def archive_quote_file(
    output_path: str | Path,
    *,
    project_ref: str | int | None = None,
    input_path: str | Path | None = None,
) -> tuple[Path | None, str]:
    """
    Copy a formatted output into its project's folder — drilling into a
    matching sub-project sub-folder when one exists.

    Independent of any QuoteVersion row, so archiving works even when the
    user turned OFF "记录此次版本". Project resolution priority:
      1. explicit `project_ref` (id or name)
      2. infer from `input_path`'s filesystem location (work_dir layout)
      3. infer from the input/​output filename's leading token

    Returns `(dest_path_or_None, human_status_message)`. The message is
    ready to surface in the UI report.
    """
    src = Path(output_path)
    if not src.exists():
        return None, "归档跳过:输出文件不存在"

    db = get_db()
    with db.session() as s:
        proj: Project | None = None
        ref = project_ref.strip() if isinstance(project_ref, str) else project_ref
        if ref:
            proj = _resolve_project(s, ref)
            if proj is None:
                return None, f"归档跳过:找不到指定项目 `{project_ref}`"
        else:
            if input_path is not None:
                proj = infer_project_from_path(s, Path(input_path))
            if proj is None:
                fname = Path(input_path).name if input_path else src.name
                proj = infer_project_from_filename(s, fname)
            if proj is None:
                return None, "归档跳过:没有关联到任何项目(打开自动关联或手动选项目即可)"

        proj_folder = Path(proj.folder_path)
        if not proj_folder.exists():
            return None, f"归档跳过:项目文件夹不存在 `{proj_folder}`"

        target_dir = _resolve_archive_dir(proj_folder, src.name)
        dest = _safe_dest(target_dir, src)
        try:
            if dest.resolve() != src.resolve():
                shutil.copy(src, dest)
        except Exception:                                             # noqa: BLE001
            logger.exception("archive copy failed: %s -> %s", src, dest)
            return None, "归档失败:复制文件出错(详见日志)"

        drilled = target_dir.resolve() != proj_folder.resolve()
        label = "子项目文件夹" if drilled else "项目文件夹"
        return dest, f"已归档到{label}:`{dest}`"


def archive_quote_to_project(version_id: int) -> Path | None:
    """
    Archive a recorded version's output into its linked project folder and
    stamp the version's `archived_path`.

    Thin wrapper over `archive_quote_file` (which does the drill-down). Kept
    for callers that work in terms of version rows. Returns the destination
    Path, or None on any skip.
    """
    summary = get_quote_version(version_id)
    if summary is None or summary.project_id is None:
        logger.info("archive: version #%s has no project link", version_id)
        return None

    dest, _msg = archive_quote_file(
        summary.output_file,
        project_ref=summary.project_id,
        input_path=summary.source_file,
    )
    if dest is not None:
        db = get_db()
        with db.session() as s:
            qv = s.scalar(select(QuoteVersion).where(QuoteVersion.id == version_id))
            if qv is not None:
                qv.archived_path = str(dest.resolve())
    return dest


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
    "archive_quote_file",
]
