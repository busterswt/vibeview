"""Live report routes."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..report_helpers import build_maintenance_readiness_report, render_maintenance_readiness_csv

router = APIRouter()

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None


def configure(*, get_session_record: Callable[[], Callable[[Request], object]]) -> None:
    global _get_session_record_getter
    _get_session_record_getter = get_session_record


def _require_session_record() -> Callable[[Request], object]:
    if _get_session_record_getter is None:
        raise RuntimeError("report routes are not configured")
    return _get_session_record_getter()


@router.get("/api/reports/maintenance-readiness")
async def api_report_maintenance_readiness(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, build_maintenance_readiness_report, session.server)


@router.get("/api/reports/maintenance-readiness.csv")
async def api_report_maintenance_readiness_csv(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, build_maintenance_readiness_report, session.server)
    csv_text = await loop.run_in_executor(None, render_maintenance_readiness_csv, report["report"])
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="maintenance-readiness.csv"'},
    )
