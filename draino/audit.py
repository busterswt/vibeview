"""Compliance audit logger — writes structured JSONL entries to a file."""
from __future__ import annotations

import getpass
import json
import logging
import os
import socket
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DEFAULT_PATH = Path.home() / ".draino" / "audit.log"
_LOGGER = logging.getLogger("draino.audit")


class AuditLogger:
    """Thread-safe compliance audit logger.

    Writes one JSON object per line (JSONL) to *path*.  Each entry has:

        timestamp   ISO-8601 UTC time of the event
        user        OS username of the operator
        hostname    Machine running draino
        session_id  UUID, unique per draino invocation
        action      evacuation | drain_quick | undrain | reboot
        node        Kubernetes node name
        event       started | completed | failed | blocked | cancelled
        detail      Free-form context string (step detail, error text, etc.)
    """

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._session_id = str(uuid.uuid4())
        self._user = _get_user()
        self._machine = socket.gethostname()

    # ── Public API ────────────────────────────────────────────────────────────

    def log(
        self,
        action: str,
        node: str,
        event: str,
        detail: str = "",
    ) -> None:
        """Append one audit entry to the log file (thread-safe)."""
        entry: dict = {
            "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user":       self._user,
            "hostname":   self._machine,
            "session_id": self._session_id,
            "action":     action,
            "node":       node,
            "event":      event,
        }
        if detail:
            entry["detail"] = detail
        line = json.dumps(entry, separators=(",", ":")) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        _LOGGER.info(line.rstrip("\n"))

    @property
    def path(self) -> Path:
        return self._path


def _get_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))
