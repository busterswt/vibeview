"""Shared node-agent auth, shell, and reboot state helpers."""
from __future__ import annotations

import logging
import os
import subprocess
import threading

from fastapi import HTTPException, status
from pydantic import BaseModel

_state_lock = threading.Lock()
_reboot_in_progress = False
_LOGGER = logging.getLogger("draino.node_agent")


class RebootRequest(BaseModel):
    request_id: str
    expected_node: str | None = None
    hypervisor: str | None = None


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _read_token() -> str:
    with open(_env("DRAINO_NODE_AGENT_TOKEN_FILE"), "r", encoding="utf-8") as fh:
        return fh.read().strip()


def _node_name() -> str:
    return _env("DRAINO_NODE_NAME")


def _authorise(authorization: str | None) -> None:
    expected = f"Bearer {_read_token()}"
    if authorization != expected:
        _LOGGER.warning("unauthorised request node=%s", os.getenv("DRAINO_NODE_NAME", "unknown"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorised",
        )


def _reboot_host() -> None:
    global _reboot_in_progress
    try:
        _LOGGER.info("reboot command starting node=%s", _node_name())
        subprocess.run(
            ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "reboot"],
            timeout=15,
            capture_output=True,
            check=False,
        )
    finally:
        _LOGGER.info("reboot command finished node=%s", _node_name())
        with _state_lock:
            _reboot_in_progress = False


def _run_host_shell(script: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "sh", "-lc", script],
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )
