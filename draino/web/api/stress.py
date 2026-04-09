"""Stress view routes."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial

from fastapi import APIRouter, Request

from .api_issues import build_api_issue
from ..stress_helpers import (
    build_stress_catalog,
    build_stress_environment,
    build_stress_options,
    delete_active_stress_stack,
    get_stress_status,
    launch_stress_stack,
    record_stress_action,
)

router = APIRouter()

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None


def configure(*, get_session_record: Callable[[], Callable[[Request], object]]) -> None:
    global _get_session_record_getter
    _get_session_record_getter = get_session_record


def _require_session_record() -> Callable[[Request], object]:
    if _get_session_record_getter is None:
        raise RuntimeError("stress routes are not configured")
    return _get_session_record_getter()


@router.get("/api/stress/environment")
async def api_stress_environment(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    compute_count = sum(1 for state in session.server.node_states.values() if state.is_compute)
    try:
        environment = await loop.run_in_executor(
            None,
            partial(
                build_stress_environment,
                auth=session.server.openstack_auth,
                compute_count=compute_count,
            ),
        )
        return {"environment": environment, "error": None, "api_issue": None}
    except Exception as exc:
        return {
            "environment": None,
            "error": str(exc),
            "api_issue": build_api_issue("Nova", "GET /api/stress/environment", exc),
        }


@router.get("/api/stress/options")
async def api_stress_options(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    compute_count = sum(1 for state in session.server.node_states.values() if state.is_compute)
    profile_key = str(request.query_params.get("profile") or "")
    try:
        options = await loop.run_in_executor(
            None,
            partial(
                build_stress_options,
                auth=session.server.openstack_auth,
                compute_count=compute_count,
                profile_key=profile_key,
            ),
        )
        return {"options": options, "error": None, "api_issue": None}
    except Exception as exc:
        return {
            "options": None,
            "error": str(exc),
            "api_issue": build_api_issue("Nova", "GET /api/stress/options", exc),
        }


@router.get("/api/stress/catalog")
async def api_stress_catalog(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    compute_count = sum(1 for state in session.server.node_states.values() if state.is_compute)
    try:
        catalog = await loop.run_in_executor(
            None,
            partial(
                build_stress_catalog,
                auth=session.server.openstack_auth,
                compute_count=compute_count,
            ),
        )
        return {"catalog": catalog, "error": None, "api_issue": None}
    except Exception as exc:
        return {
            "catalog": None,
            "error": str(exc),
            "api_issue": build_api_issue("Nova", "GET /api/stress/catalog", exc),
        }


@router.get("/api/stress/status")
async def api_stress_status(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            partial(
                get_stress_status,
                auth=session.server.openstack_auth,
            ),
        )
        return {"status": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {
            "status": None,
            "error": str(exc),
            "api_issue": build_api_issue("Nova", "GET /api/stress/status", exc),
        }


@router.post("/api/stress/launch")
async def api_stress_launch(request: Request):
    session = _require_session_record()(request)
    payload = await request.json()
    loop = asyncio.get_running_loop()
    compute_count = sum(1 for state in session.server.node_states.values() if state.is_compute)
    record_stress_action("launch", "request_received", message="Received launch request in Draino", detail=str(payload.get("profile") or ""))
    try:
        data = await loop.run_in_executor(
            None,
            partial(
                launch_stress_stack,
                auth=session.server.openstack_auth,
                compute_count=compute_count,
                payload=payload,
            ),
        )
        return {"status": data, "error": None, "api_issue": None}
    except Exception as exc:
        record_stress_action("launch", "route_failed", status="bad", message="Launch request failed before response", detail=str(exc))
        return {
            "status": None,
            "error": str(exc),
            "api_issue": build_api_issue("Nova", "POST /api/stress/launch", exc),
        }


@router.post("/api/stress/delete")
async def api_stress_delete(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    record_stress_action("delete", "request_received", message="Received delete request in Draino")
    try:
        data = await loop.run_in_executor(
            None,
            partial(
                delete_active_stress_stack,
                auth=session.server.openstack_auth,
            ),
        )
        return {"result": data, "error": None, "api_issue": None}
    except Exception as exc:
        record_stress_action("delete", "route_failed", status="bad", message="Delete request failed before response", detail=str(exc))
        return {
            "result": None,
            "error": str(exc),
            "api_issue": build_api_issue("Nova", "POST /api/stress/delete", exc),
        }
