"""OpenStack resource routes."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Request

from ..resource_helpers import get_network_detail, get_networks, get_volumes

router = APIRouter()

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None


def configure(*, get_session_record: Callable[[], Callable[[Request], object]]) -> None:
    global _get_session_record_getter
    _get_session_record_getter = get_session_record


def _require_session_record() -> Callable[[Request], object]:
    if _get_session_record_getter is None:
        raise RuntimeError("resource routes are not configured")
    return _get_session_record_getter()


@router.get("/api/networks")
async def api_networks(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_networks, session.server.openstack_auth)
        return {"networks": data, "error": None}
    except Exception as exc:
        return {"networks": [], "error": str(exc)}


@router.get("/api/volumes")
async def api_volumes(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data, all_projects = await loop.run_in_executor(None, get_volumes, session.server.openstack_auth)
        return {"volumes": data, "all_projects": all_projects, "error": None}
    except Exception as exc:
        return {"volumes": [], "all_projects": False, "error": str(exc)}
