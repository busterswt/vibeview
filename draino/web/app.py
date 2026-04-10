"""FastAPI app assembly for the web UI."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.staticfiles import StaticFiles

from .api import auth as auth_api
from .api import k8s as k8s_api
from .api import nodes as nodes_api
from .api import reports as reports_api
from .api import resources as resources_api
from .api import runtime as runtime_api
from .api import stress as stress_api
from .session import SessionStore
from . import ws as ws_api


def create_fastapi_app(
    *,
    static_dir: Path,
    get_sessions: Callable[[], SessionStore],
    get_audit_log_path: Callable[[], str | None],
    set_app_loop: Callable[[asyncio.AbstractEventLoop], None],
    get_session_record: Callable[[], Callable[[Request], object]],
    get_ws_session: Callable[[], Callable[[WebSocket], object | None]],
    get_app_update_status: Callable[[], Callable[[], dict]],
    get_public_version_status: Callable[[], Callable[[], dict]],
    get_app_runtime: Callable[[], Callable[[], dict]],
    get_runtime_diagnostics: Callable[[], Callable[[object], dict]],
    clear_runtime_diagnostics: Callable[[], Callable[[object, str], dict]],
    get_network_detail: Callable[[], Callable[[str, object | None], dict]],
) -> FastAPI:
    current_loop: asyncio.AbstractEventLoop | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal current_loop
        current_loop = asyncio.get_running_loop()
        set_app_loop(current_loop)
        yield

    app = FastAPI(title="VibeView", lifespan=lifespan)

    auth_api.configure(
        static_dir=static_dir,
        get_sessions=get_sessions,
        get_app_loop=lambda: current_loop,
        get_audit_log_path=get_audit_log_path,
    )
    runtime_api.configure(
        get_session_record=get_session_record,
        get_app_update_status=get_app_update_status,
        get_public_version_status=get_public_version_status,
        get_app_runtime=get_app_runtime,
        get_runtime_diagnostics=get_runtime_diagnostics,
        clear_runtime_diagnostics=clear_runtime_diagnostics,
    )
    nodes_api.configure(
        get_session_record=get_session_record,
        get_network_detail=get_network_detail,
    )
    k8s_api.configure(
        get_session_record=get_session_record,
    )
    resources_api.configure(
        get_session_record=get_session_record,
    )
    reports_api.configure(
        get_session_record=get_session_record,
    )
    stress_api.configure(
        get_session_record=get_session_record,
    )
    ws_api.configure(
        get_ws_session=get_ws_session,
    )

    app.include_router(auth_api.router)
    app.include_router(runtime_api.router)
    app.include_router(nodes_api.router)
    app.include_router(k8s_api.router)
    app.include_router(resources_api.router)
    app.include_router(reports_api.router)
    app.include_router(stress_api.router)
    app.include_router(ws_api.router)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    return app
