from __future__ import annotations

from collections.abc import Iterator

import pytest

from draino.web import server as web_server


@pytest.fixture(autouse=True)
def reset_web_sessions(tmp_path) -> Iterator[None]:
    web_server._sessions = web_server.SessionStore()
    web_server._audit_log_path = str(tmp_path / "audit.log")
    yield
    web_server._sessions = web_server.SessionStore()
    web_server._audit_log_path = None
