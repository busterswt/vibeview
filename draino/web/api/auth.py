"""Authentication and session routes."""
from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse

from ...operations import k8s_ops, openstack_ops
from ..auth_builders import LoginPayload, _build_k8s_auth, _build_openstack_auth
from ..inventory import DrainoServer
from ..session import SESSION_TTL, SessionRecord, SessionStore

router = APIRouter()

_static_dir: Path | None = None
_sessions_getter: Callable[[], SessionStore] | None = None
_app_loop_getter: Callable[[], asyncio.AbstractEventLoop | None] | None = None
_audit_log_path_getter: Callable[[], str | None] | None = None


def configure(
    *,
    static_dir: Path,
    get_sessions: Callable[[], SessionStore],
    get_app_loop: Callable[[], asyncio.AbstractEventLoop | None],
    get_audit_log_path: Callable[[], str | None],
) -> None:
    global _static_dir, _sessions_getter, _app_loop_getter, _audit_log_path_getter
    _static_dir = static_dir
    _sessions_getter = get_sessions
    _app_loop_getter = get_app_loop
    _audit_log_path_getter = get_audit_log_path


def _require_configured() -> tuple[Path, SessionStore]:
    if _static_dir is None or _sessions_getter is None:
        raise RuntimeError("auth routes are not configured")
    return _static_dir, _sessions_getter()


def _get_app_loop() -> asyncio.AbstractEventLoop | None:
    if _app_loop_getter is None:
        return None
    return _app_loop_getter()


def _get_audit_log_path() -> str | None:
    if _audit_log_path_getter is None:
        return None
    return _audit_log_path_getter()


@router.get("/")
async def index(request: Request):
    static_dir, sessions = _require_configured()
    record = sessions.get(request.cookies.get("draino_session"))
    if record is not None:
        return RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(static_dir / "login.html")


@router.get("/app")
async def app(request: Request):
    static_dir, sessions = _require_configured()
    record = sessions.get(request.cookies.get("draino_session"))
    if record is None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(static_dir / "index.html")


@router.get("/api/session")
async def api_session(request: Request):
    _, sessions = _require_configured()
    record = sessions.get(request.cookies.get("draino_session"))
    if record is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "username": record.username,
        "project_name": record.project_name,
        "role_names": record.role_names,
        "is_admin": record.is_admin,
    }


@router.post("/api/session")
async def api_login(payload: LoginPayload, response: Response):
    _, sessions = _require_configured()
    k8s_auth = _build_k8s_auth(payload.kubernetes)
    openstack_auth = _build_openstack_auth(payload.openstack)

    try:
        initial_nodes = k8s_ops.get_nodes(auth=k8s_auth)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Kubernetes authentication failed: {exc}",
        ) from exc

    try:
        openstack_ops._conn(auth=openstack_auth).authorize()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"OpenStack authentication failed: {exc}",
        ) from exc

    role_names = openstack_ops.get_current_role_names(auth=openstack_auth)
    server = DrainoServer(
        k8s_auth=k8s_auth,
        openstack_auth=openstack_auth,
        role_names=role_names,
        audit_log=_get_audit_log_path(),
    )
    app_loop = _get_app_loop()
    if app_loop is not None:
        server.set_loop(app_loop)
    server._audit.log("session", "-", "started", "web ui user-authenticated session")
    session_id = secrets.token_urlsafe(32)
    now = time.time()
    sessions.put(SessionRecord(
        session_id=session_id,
        server=server,
        username=openstack_auth.username,
        project_name=openstack_auth.project_name,
        role_names=role_names,
        is_admin=server.is_admin,
        created_at=now,
        last_seen=now,
    ))
    response.set_cookie(
        key="draino_session",
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=SESSION_TTL,
    )
    server.start_refresh(cached_nodes=initial_nodes)
    return {"ok": True}


@router.delete("/api/session")
async def api_logout(request: Request, response: Response):
    _, sessions = _require_configured()
    sessions.delete(request.cookies.get("draino_session"))
    response.delete_cookie("draino_session")
    return {"ok": True}
