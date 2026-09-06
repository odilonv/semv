"""Session state persistence for semv.

Enables save/resume of organization sessions after interruption.
Sessions are stored in ~/.config/semv/sessions/<session_id>.json

A session captures:
- Scanned files and their hashes
- Files already processed by the agent
- Current proposals
- Batch job status (if applicable)
"""

import json
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from semv.logger import get_logger

logger = get_logger("state")

SESSIONS_DIR = Path.home() / ".config" / "semv" / "sessions"
SESSION_MAX_AGE_DAYS = 7


def _ensure_sessions_dir():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _session_id_for_directory(directory: str) -> str:
    """Generate a deterministic session ID from the target directory path."""
    return hashlib.sha256(directory.encode()).hexdigest()[:16]


class SessionState:
    """Persistent session state for a file organization run."""

    def __init__(self, target_directory: str):
        self.target_directory = target_directory
        self.session_id = _session_id_for_directory(target_directory)
        self.session_file = SESSIONS_DIR / f"{self.session_id}.json"

        # State fields
        self.extracted_files: list[dict] = []       # Files with content/hash extracted
        self.processed_paths: set[str] = set()      # Paths already sent to agent
        self.proposals: dict[str, dict] = {}        # Current proposals
        self.batch_job_id: str | None = None        # Mistral batch job ID
        self.batch_status: str | None = None        # QUEUED/RUNNING/SUCCESS/FAILED
        self.created_at: str = datetime.now().isoformat()
        self.updated_at: str = self.created_at
        self.total_files: int = 0
        self.phase: str = "init"  # init/extracting/deduplicating/processing/done

    def save(self):
        """Persist session state to disk."""
        _ensure_sessions_dir()
        data = {
            "session_id": self.session_id,
            "target_directory": self.target_directory,
            "phase": self.phase,
            "total_files": self.total_files,
            "extracted_files_count": len(self.extracted_files),
            "processed_paths": list(self.processed_paths),
            "proposals": self.proposals,
            "batch_job_id": self.batch_job_id,
            "batch_status": self.batch_status,
            "created_at": self.created_at,
            "updated_at": datetime.now().isoformat(),
        }
        # Don't save full extracted files (too large), just count
        # They can be re-extracted on resume
        try:
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.debug("Session saved: %s (phase=%s)", self.session_id, self.phase)
        except Exception as e:
            logger.error("Failed to save session: %s", e)

    @classmethod
    def load(cls, target_directory: str) -> "SessionState | None":
        """Load a previous session for the given directory, if one exists."""
        session_id = _session_id_for_directory(target_directory)
        session_file = SESSIONS_DIR / f"{session_id}.json"

        if not session_file.exists():
            return None

        try:
            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            state = cls(target_directory)
            state.phase = data.get("phase", "init")
            state.total_files = data.get("total_files", 0)
            state.processed_paths = set(data.get("processed_paths", []))
            state.proposals = data.get("proposals", {})
            state.batch_job_id = data.get("batch_job_id")
            state.batch_status = data.get("batch_status")
            state.created_at = data.get("created_at", state.created_at)
            state.updated_at = data.get("updated_at", state.updated_at)

            logger.info(
                "Resumed session %s (phase=%s, %d proposals cached)",
                session_id[:8],
                state.phase,
                len(state.proposals),
            )
            return state

        except Exception as e:
            logger.warning("Failed to load session %s: %s", session_id[:8], e)
            return None

    def clear(self):
        """Delete this session file."""
        if self.session_file.exists():
            self.session_file.unlink()
            logger.debug("Session cleared: %s", self.session_id[:8])

    @property
    def has_pending_batch(self) -> bool:
        """Check if there's an unfinished batch job."""
        return (
            self.batch_job_id is not None
            and self.batch_status not in (None, "SUCCESS", "FAILED", "CANCELLED")
        )

    @property
    def resumable(self) -> bool:
        """Check if this session can be meaningfully resumed."""
        return self.phase not in ("init", "done") or self.has_pending_batch


def cleanup_old_sessions():
    """Delete sessions older than SESSION_MAX_AGE_DAYS."""
    _ensure_sessions_dir()
    cutoff = datetime.now() - timedelta(days=SESSION_MAX_AGE_DAYS)
    cleaned = 0

    for session_file in SESSIONS_DIR.glob("*.json"):
        try:
            with open(session_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            updated = datetime.fromisoformat(data.get("updated_at", "2000-01-01"))
            if updated < cutoff:
                session_file.unlink()
                cleaned += 1
        except Exception:
            # Corrupted session file, remove it
            session_file.unlink(missing_ok=True)
            cleaned += 1

    if cleaned:
        logger.debug("Cleaned up %d old sessions", cleaned)
