"""Session models and helpers for the web UI."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, WebSocket, status

if TYPE_CHECKING:
    from .inventory import DrainoServer

SESSION_TTL = 60 * 60 * 8


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
            self._sweep_expired_locked(time.time())
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

    def sweep_expired(self, now: float | None = None) -> int:
        check_time = time.time() if now is None else now
        with self._lock:
            return self._sweep_expired_locked(check_time)

    def stats(self, now: float | None = None) -> dict:
        check_time = time.time() if now is None else now
        with self._lock:
            expired = 0
            oldest_age_seconds = 0.0
            newest_age_seconds = 0.0
            for record in self._sessions.values():
                age = max(0.0, check_time - record.last_seen)
                if age > self._ttl_seconds:
                    expired += 1
                oldest_age_seconds = max(oldest_age_seconds, age)
                if newest_age_seconds == 0.0:
                    newest_age_seconds = age
                else:
                    newest_age_seconds = min(newest_age_seconds, age)
            return {
                "ttl_seconds": self._ttl_seconds,
                "stored_count": len(self._sessions),
                "expired_count": expired,
                "active_count": max(0, len(self._sessions) - expired),
                "oldest_age_seconds": oldest_age_seconds if self._sessions else 0.0,
                "newest_age_seconds": newest_age_seconds if self._sessions else 0.0,
            }

    def _sweep_expired_locked(self, check_time: float) -> int:
        expired_ids = [
            session_id
            for session_id, record in self._sessions.items()
            if (check_time - record.last_seen) > self._ttl_seconds
        ]
        for session_id in expired_ids:
            self._sessions.pop(session_id, None)
        return len(expired_ids)


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
