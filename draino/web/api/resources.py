"""OpenStack resource routes."""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from fastapi import APIRouter, Request

from ...operations import k8s_ops
from .api_issues import build_api_issue
from ..resource_helpers import (
    get_load_balancer_detail,
    get_load_balancers,
    get_network_detail,
    get_networks,
    get_router_detail,
    get_routers,
    get_swift_containers,
    get_volumes,
    repair_subnet_metadata_port,
)

router = APIRouter()
_RESOURCE_DETAIL_TIMEOUT_SECONDS = float(os.getenv("DRAINO_RESOURCE_DETAIL_TIMEOUT_SECONDS", "10"))

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None


def configure(*, get_session_record: Callable[[], Callable[[Request], object]]) -> None:
    global _get_session_record_getter
    _get_session_record_getter = get_session_record


def _require_session_record() -> Callable[[Request], object]:
    if _get_session_record_getter is None:
        raise RuntimeError("resource routes are not configured")
    return _get_session_record_getter()


async def _run_with_timeout(func, *args):
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, func, *args),
        timeout=_RESOURCE_DETAIL_TIMEOUT_SECONDS,
    )


@router.get("/api/networks")
async def api_networks(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_networks, session.server.openstack_auth)
        return {"networks": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"networks": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/networks", exc)}


@router.get("/api/volumes")
async def api_volumes(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data, all_projects = await loop.run_in_executor(None, get_volumes, session.server.openstack_auth)
        return {"volumes": data, "all_projects": all_projects, "error": None, "api_issue": None}
    except Exception as exc:
        return {"volumes": [], "all_projects": False, "error": str(exc), "api_issue": None}


@router.get("/api/swift-containers")
async def api_swift_containers(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_swift_containers, session.server.openstack_auth)
        return {"containers": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"containers": [], "error": str(exc), "api_issue": None}


@router.get("/api/load-balancers")
async def api_load_balancers(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_load_balancers, session.server.openstack_auth)
        return {"load_balancers": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"load_balancers": [], "error": str(exc), "api_issue": build_api_issue("Octavia", "GET /v2/lbaas/loadbalancers", exc)}


@router.get("/api/load-balancers/{lb_id}")
async def api_load_balancer_detail(lb_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_load_balancer_detail, lb_id, session.server.openstack_auth)
        return {"load_balancer": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "load_balancer": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading load balancer details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"load_balancer": None, "error": str(exc), "api_issue": build_api_issue("Octavia", f"GET /v2/lbaas/loadbalancers/{lb_id}", exc)}


@router.post("/api/networks/{network_id}/subnets/{subnet_id}/repair-metadata-port")
async def api_repair_subnet_metadata_port(network_id: str, subnet_id: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            repair_subnet_metadata_port,
            network_id,
            subnet_id,
            session.server.openstack_auth,
        )
        return {"metadata_port": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {
            "metadata_port": None,
            "error": str(exc),
            "api_issue": build_api_issue("Neutron", f"POST /v2.0/ports repair metadata port for subnet {subnet_id}", exc),
        }


@router.get("/api/routers")
async def api_routers(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_routers, session.server.openstack_auth)
        return {"routers": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"routers": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/routers", exc)}


@router.get("/api/routers/{router_id}")
async def api_router_detail(router_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_router_detail, router_id, session.server.openstack_auth)
        return {"router": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "router": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading router details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"router": None, "error": str(exc), "api_issue": build_api_issue("Neutron", f"GET /v2.0/routers/{router_id}", exc)}


@router.get("/api/routers/{router_id}/ovn")
async def api_router_ovn(router_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(k8s_ops.get_ovn_logical_router, router_id, session.server.k8s_auth)
        return {"ovn": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {"ovn": None, "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading OVN router detail", "api_issue": None}
    except Exception as exc:
        return {"ovn": None, "error": str(exc), "api_issue": None}
