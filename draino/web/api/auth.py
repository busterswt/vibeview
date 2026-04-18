"""Authentication and session routes."""
from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse

from ...operations import k8s_ops, openstack_ops
from ..auth_builders import LoginPayload, _build_k8s_auth, _build_openstack_auth, _openstack_payload_has_credentials
from ..inventory import DrainoServer
from ..session import SESSION_TTL, SessionRecord, SessionStore

router = APIRouter()

_static_dir: Path | None = None
_sessions_getter: Callable[[], SessionStore] | None = None
_app_loop_getter: Callable[[], asyncio.AbstractEventLoop | None] | None = None
_audit_log_path_getter: Callable[[], str | None] | None = None
_LOGIN_TIMEOUT_SECONDS = float(os.getenv("DRAINO_LOGIN_TIMEOUT_SECONDS", "15"))


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


def _request_is_secure(request: Request) -> bool:
    forwarded = request.headers.get("forwarded", "")
    if forwarded and "proto=https" in forwarded.lower():
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower() == "https"
    return request.url.scheme.lower() == "https"


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
    openstack_services = {"block_storage": False, "object_store": False}
    if record.server.openstack_auth is not None:
        loop = asyncio.get_running_loop()
        try:
            openstack_services = await loop.run_in_executor(
                None,
                openstack_ops.get_service_endpoint_availability,
                record.server.openstack_auth,
            )
        except Exception:
            openstack_services = {"block_storage": False, "object_store": False}
    return {
        "authenticated": True,
        "username": record.username or None,
        "project_name": record.project_name or None,
        "role_names": record.role_names,
        "is_admin": record.is_admin,
        "has_k8s_auth": record.server.k8s_auth is not None,
        "has_openstack_auth": record.server.openstack_auth is not None,
        "openstack_services": openstack_services,
        "session_mode": "full" if record.server.openstack_auth is not None else "kubernetes_only",
    }


@router.post("/api/session")
async def api_login(payload: LoginPayload, request: Request, response: Response):
    _, sessions = _require_configured()
    k8s_auth = _build_k8s_auth(payload.kubernetes)
    openstack_auth = _build_openstack_auth(payload.openstack) if _openstack_payload_has_credentials(payload.openstack) else None
    loop = asyncio.get_running_loop()

    try:
        initial_nodes = await asyncio.wait_for(
            loop.run_in_executor(None, k8s_ops.get_nodes, k8s_auth),
            timeout=_LOGIN_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Kubernetes authentication timed out after {_LOGIN_TIMEOUT_SECONDS:.0f}s",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Kubernetes authentication failed: {exc}",
        ) from exc

    role_names: list[str] = []
    if openstack_auth is not None:
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, lambda: openstack_ops._conn(auth=openstack_auth).authorize()),
                timeout=_LOGIN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"OpenStack authentication timed out after {_LOGIN_TIMEOUT_SECONDS:.0f}s",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"OpenStack authentication failed: {exc}",
            ) from exc
        try:
            role_names = await asyncio.wait_for(
                loop.run_in_executor(None, openstack_ops.get_current_role_names, openstack_auth),
                timeout=_LOGIN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"OpenStack role lookup timed out after {_LOGIN_TIMEOUT_SECONDS:.0f}s",
            ) from exc
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
        username=(openstack_auth.username if openstack_auth else ""),
        project_name=(openstack_auth.project_name if openstack_auth else ""),
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
        secure=_request_is_secure(request),
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
