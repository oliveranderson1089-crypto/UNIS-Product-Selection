"""
Shared CLI utilities used by every subcommand.

Anything that affects the Python runtime (stdout encoding, logging) belongs
here so subcommands don't repeat boilerplate. Anything that produces
user-visible output goes through the `console` exported here.
"""

from __future__ import annotations

import logging
import sys

# Force a UTF-8-capable stdout on Windows BEFORE importing Rich, otherwise
# Chinese characters get mojibake'd by CP936/GBK. Has to happen exactly
# once per process; idempotent.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                                   # noqa: BLE001
        pass

from rich.console import Console                          # noqa: E402

# Single Console instance shared across subcommands so styling is uniform.
console = Console(force_terminal=True, legacy_windows=False)


def setup_logging() -> None:
    """Configure root logging from config.yaml. Safe to call multiple times."""
    from ..config import get_config

    cfg = get_config()
    level = getattr(logging, cfg.logging.level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg.logging.file:
        handlers.append(logging.FileHandler(cfg.logging.file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


__all__ = ["console", "setup_logging"]
