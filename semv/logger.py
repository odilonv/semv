"""Centralized logging for semv.

Provides a dual-output logger:
- Rich console handler for premium UX (colored, structured)
- Rotating file handler for debug/audit trail

Usage:
    from semv.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Processing %d files", count)
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

LOG_DIR = Path.home() / ".config" / "semv" / "logs"
LOG_FILE = LOG_DIR / "semv.log"

# Module-level console for direct Rich output (progress bars, tables, etc.)
console = Console(stderr=True)

_initialized = False


def _ensure_log_dir():
    """Create log directory if it doesn't exist."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging(verbose: bool = False):
    """Initialize the logging system. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    _ensure_log_dir()

    root_logger = logging.getLogger("semv")
    root_logger.setLevel(logging.DEBUG)

    # Prevent duplicate handlers on re-init
    root_logger.handlers.clear()

    # --- Rich Console Handler (user-facing, INFO+) ---
    console_handler = RichHandler(
        console=console,
        show_time=False,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        level=logging.INFO if not verbose else logging.DEBUG,
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(console_handler)

    # --- Rotating File Handler (debug, audit trail) ---
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(file_handler)

    # Quiet noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "langchain", "langsmith", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the 'semv' namespace.

    Automatically initializes logging on first call.
    """
    if not _initialized:
        setup_logging()
    return logging.getLogger(f"semv.{name}")
