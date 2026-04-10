"""Runtime and release metadata routes."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..latency import measure_latency

router = APIRouter()

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None
_get_app_update_status_getter: Callable[[], Callable[[], dict]] | None = None
_get_public_version_status_getter: Callable[[], Callable[[], dict]] | None = None
_get_app_runtime_getter: Callable[[], Callable[[], dict]] | None = None
_get_runtime_diagnostics_getter: Callable[[], Callable[[object], dict]] | None = None
_clear_runtime_diagnostics_getter: Callable[[], Callable[[object, str], dict]] | None = None


class RuntimeClearAction(BaseModel):
    action: str


def configure(
    *,
    get_session_record: Callable[[], Callable[[Request], object]],
    get_app_update_status: Callable[[], Callable[[], dict]],
    get_public_version_status: Callable[[], Callable[[], dict]],
    get_app_runtime: Callable[[], Callable[[], dict]],
    get_runtime_diagnostics: Callable[[], Callable[[object], dict]],
    clear_runtime_diagnostics: Callable[[], Callable[[object, str], dict]],
) -> None:
    global _get_session_record_getter, _get_app_update_status_getter, _get_public_version_status_getter, _get_app_runtime_getter, _get_runtime_diagnostics_getter, _clear_runtime_diagnostics_getter
    _get_session_record_getter = get_session_record
    _get_app_update_status_getter = get_app_update_status
    _get_public_version_status_getter = get_public_version_status
    _get_app_runtime_getter = get_app_runtime
    _get_runtime_diagnostics_getter = get_runtime_diagnostics
    _clear_runtime_diagnostics_getter = clear_runtime_diagnostics


def _require_configured() -> tuple[
    Callable[[Request], object],
    Callable[[], dict],
    Callable[[], dict],
    Callable[[], dict],
    Callable[[object], dict],
    Callable[[object, str], dict],
]:
    if (
        _get_session_record_getter is None
        or _get_app_update_status_getter is None
        or _get_public_version_status_getter is None
        or _get_app_runtime_getter is None
        or _get_runtime_diagnostics_getter is None
        or _clear_runtime_diagnostics_getter is None
    ):
        raise RuntimeError("runtime routes are not configured")
    return (
        _get_session_record_getter(),
        _get_app_update_status_getter(),
        _get_public_version_status_getter(),
        _get_app_runtime_getter(),
        _get_runtime_diagnostics_getter(),
        _clear_runtime_diagnostics_getter(),
    )


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ready"}


@router.get("/api/app-meta")
async def api_app_meta(request: Request):
    with measure_latency("app_meta"):
        get_session_record, get_app_update_status, _, _, _, _ = _require_configured()
        get_session_record(request)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: get_app_update_status(force=True))


@router.get("/api/version")
async def api_version():
    _, _, get_public_version_status, _, _, _ = _require_configured()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_public_version_status)


@router.get("/api/app-runtime")
async def api_app_runtime(request: Request):
    with measure_latency("app_runtime"):
        get_session_record, _, _, get_app_runtime, get_runtime_diagnostics, _ = _require_configured()
        record = get_session_record(request)
        loop = asyncio.get_running_loop()
        runtime = await loop.run_in_executor(None, get_app_runtime)
        diagnostics = await loop.run_in_executor(None, get_runtime_diagnostics, record)
        payload = dict(runtime)
        payload["diagnostics"] = diagnostics
        return payload


@router.post("/api/app-runtime/clear")
async def api_app_runtime_clear(payload: RuntimeClearAction, request: Request):
    with measure_latency("app_runtime"):
        get_session_record, _, _, _, get_runtime_diagnostics, clear_runtime_diagnostics = _require_configured()
        record = get_session_record(request)
        loop = asyncio.get_running_loop()
        cleared = await loop.run_in_executor(None, clear_runtime_diagnostics, record, payload.action)
        diagnostics = await loop.run_in_executor(None, get_runtime_diagnostics, record)
        return {"ok": True, "result": cleared, "diagnostics": diagnostics}
