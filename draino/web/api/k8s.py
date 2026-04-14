"""Kubernetes inventory API routes."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Request

from ...operations import k8s_ops

router = APIRouter()

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None


def configure(*, get_session_record: Callable[[], Callable[[Request], object]]) -> None:
    global _get_session_record_getter
    _get_session_record_getter = get_session_record


def _require_session_record() -> Callable[[Request], object]:
    if _get_session_record_getter is None:
        raise RuntimeError("k8s routes are not configured")
    return _get_session_record_getter()


@router.get("/api/k8s/namespaces")
async def api_k8s_namespaces(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_namespaces, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/pods")
async def api_k8s_pods(request: Request, namespace: str | None = None):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_pods, namespace, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/services")
async def api_k8s_services(request: Request, namespace: str | None = None):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_services, namespace, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/deployments")
async def api_k8s_deployments(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_deployments, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/statefulsets")
async def api_k8s_statefulsets(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_statefulsets, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/daemonsets")
async def api_k8s_daemonsets(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_daemonsets, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/gatewayclasses")
async def api_k8s_gatewayclasses(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_gatewayclasses, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/gateways")
async def api_k8s_gateways(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_gateways, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/httproutes")
async def api_k8s_httproutes(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_httproutes, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/pvs")
async def api_k8s_pvs(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_pvs, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/pvcs")
async def api_k8s_pvcs(request: Request, namespace: str | None = None):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_pvcs, namespace, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/operators")
async def api_k8s_operators(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_operators, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@router.get("/api/k8s/crds")
async def api_k8s_crds(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_crds, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}
