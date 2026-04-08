"""Session models and helpers for the web UI."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, WebSocket, status

if TYPE_CHECKING:
    from .inventory import DrainoServer

SESSION_TTL = 60 * 60 * 12


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    server: "DrainoServer"
    username: str
    project_name: str
    role_names: list[str]
    is_admin: bool
    created_at: float
    last_seen: float


class SessionStore:
    def __init__(self, ttl_seconds: float = SESSION_TTL) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = threading.Lock()

    def put(self, record: SessionRecord) -> None:
        with self._lock:
            self._sessions[record.session_id] = record

    def get(self, session_id: str | None) -> SessionRecord | None:
        if not session_id:
            return None
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            now = time.time()
            if now - record.last_seen > self._ttl_seconds:
                self._sessions.pop(session_id, None)
                return None
            record.last_seen = now
            return record

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)


def get_session_record(request: Request, sessions: SessionStore, cookie_name: str) -> SessionRecord:
    record = sessions.get(request.cookies.get(cookie_name))
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return record


def get_ws_session(ws: WebSocket, sessions: SessionStore, cookie_name: str) -> SessionRecord | None:
    return sessions.get(ws.cookies.get(cookie_name))
