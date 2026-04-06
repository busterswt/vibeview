"""HTTPS node-local reboot agent."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

_state_lock = threading.Lock()
_reboot_in_progress = False


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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorised",
        )


def _reboot_host() -> None:
    global _reboot_in_progress
    try:
        subprocess.run(
            ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "reboot"],
            timeout=15,
            capture_output=True,
            check=False,
        )
    finally:
        with _state_lock:
            _reboot_in_progress = False


node_agent_app = FastAPI(title="Draino Node Agent")


@node_agent_app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@node_agent_app.get("/readyz")
def readyz() -> dict[str, str]:
    return {"status": "ready"}


@node_agent_app.get("/status")
def agent_status(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _authorise(authorization)
    return {
        "node": _node_name(),
        "reboot_in_progress": _reboot_in_progress,
    }


@node_agent_app.post("/reboot", status_code=status.HTTP_202_ACCEPTED)
def reboot(
    payload: RebootRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    global _reboot_in_progress
    _authorise(authorization)

    node_name = _node_name()
    if payload.expected_node and payload.expected_node != node_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"request targeted '{payload.expected_node}' but this agent serves '{node_name}'",
        )

    with _state_lock:
        if _reboot_in_progress:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="reboot already in progress",
            )
        _reboot_in_progress = True

    threading.Thread(target=_reboot_host, daemon=True).start()
    return {"accepted": True, "node": node_name, "request_id": payload.request_id}


def run(host: str = "0.0.0.0", port: int = 8443) -> None:
    cert_file = _env("DRAINO_NODE_AGENT_TLS_CERT_FILE")
    key_file = _env("DRAINO_NODE_AGENT_TLS_KEY_FILE")
    token_file = _env("DRAINO_NODE_AGENT_TOKEN_FILE")
    for path in (cert_file, key_file, token_file):
        if not Path(path).exists():
            raise RuntimeError(f"required file does not exist: {path}")
    uvicorn.run(
        node_agent_app,
        host=host,
        port=port,
        log_level="warning",
        ssl_certfile=cert_file,
        ssl_keyfile=key_file,
    )
