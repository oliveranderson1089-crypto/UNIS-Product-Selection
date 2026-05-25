"""One-shot DB schema initializer.

    python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python scripts/foo.py` without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage import init_schema    # noqa: E402

if __name__ == "__main__":
    init_schema()
    print("Schema initialized.")
