"""
Stable workspace directory for quote-format runs.

Why this exists:
  Gradio's file-upload widget drops uploads into a temp dir whose path
  looks like:
      C:\\Users\\<u>\\AppData\\Local\\Temp\\gradio\\<long-random-hash>\\file.xls
  Excel COM (and sometimes openpyxl's xlrd fallback) chokes on these
  paths — long random component, no-touch parent dir, occasional ACL
  oddities under AppData. The visible symptom is "COM formatter failed,
  fell back to openpyxl" + the COM-only rules getting skipped.

  We sidestep all of that by copying the uploaded file into a stable,
  project-owned workspace under data/quote_workspace/ BEFORE running
  the formatter. The output file lands in the same workspace subdir, so:
    - Excel COM works (path is short, predictable, owned by us)
    - Both source + output persist after the Gradio session closes (no
      more "I forgot to download and the temp got reaped")
    - Each run is isolated in its own timestamped subdir = natural audit
      trail

Layout:
    data/quote_workspace/
        20260529_064641__中核建中核燃料元件/
            中核建中核燃料元件-生产管理系统..._20260529.xls
            中核建中核燃料元件-生产管理系统..._20260529.formatted.xlsx
        20260529_071233__24台物理服务器/
            ...
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from ..config import PROJECT_ROOT

logger = logging.getLogger(__name__)

# Project-owned workspace. Lives inside data/ so it gets gitignored
# automatically by the existing `data/*.xlsx` patterns + the explicit
# `data/References/` is the only carve-out.
_WORKSPACE_ROOT = PROJECT_ROOT / "data" / "quote_workspace"

# Strip Windows-illegal FS chars from a stem so the workspace subdir
# name is always creatable.
_SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|]')


def workspace_root() -> Path:
    """Return the workspace root, creating it lazily."""
    _WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return _WORKSPACE_ROOT


def stage_input(source: Path) -> Path:
    """
    Copy `source` into a fresh timestamped subdir of the workspace and
    return the new path. Caller should pass the returned path to
    `format_quote()` so the output ends up alongside.

    Subdir name: `<YYYYMMDD_HHMMSS>__<safe-stem-truncated>`.
    """
    source = Path(source)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem_safe = _SAFE_NAME_RE.sub("_", source.stem)[:60].strip("_- ") or "quote"
    target_dir = workspace_root() / f"{ts}__{stem_safe}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copy(source, target)
    logger.info("staged input %s -> %s", source.name, target)
    return target


__all__ = ["stage_input", "workspace_root"]
