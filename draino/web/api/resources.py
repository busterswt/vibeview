"""OpenStack resource routes."""
from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...operations import k8s_ops, openstack_ops
from .api_issues import build_api_issue
from ..resource_helpers import (
    get_floating_ips,
    get_port_detail,
    get_ports,
    get_project_instances,
    get_project_inventory,
    get_projects,
    get_load_balancer_detail,
    get_load_balancers,
    get_network_detail,
    get_networks,
    retype_volume,
    get_security_group_detail,
    get_security_groups,
    get_router_detail,
    get_routers,
    get_swift_containers,
    get_volume_backups,
    get_volume_detail,
    get_volume_snapshots,
    get_volumes,
    repair_subnet_metadata_port,
    search_resources,
    update_project_quota_limit,
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


@router.get("/api/projects")
async def api_projects(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    search = str(request.query_params.get("search") or "")
    try:
        data = await loop.run_in_executor(None, get_projects, session.server.openstack_auth, search)
        return {"projects": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"projects": [], "error": str(exc), "api_issue": build_api_issue("OpenStack", "GET aggregated project inventory", exc)}


@router.get("/api/search")
async def api_search(request: Request):
    session = _require_session_record()(request)
    query = str(request.query_params.get("q") or "").strip()
    try:
        limit = int(request.query_params.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    if not query:
        return {"results": [], "error": None, "api_issue": None}
    try:
        data = await _run_with_timeout(search_resources, session.server.openstack_auth, query, max(1, min(limit, 50)))
        return {"results": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "results": [],
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while searching resources",
            "api_issue": None,
        }
    except Exception as exc:
        return {"results": [], "error": str(exc), "api_issue": build_api_issue("OpenStack", f"GET aggregated search for query {query}", exc)}


@router.get("/api/projects/{project_id}/inventory")
async def api_project_inventory(project_id: str, request: Request):
    session = _require_session_record()(request)
    section = str(request.query_params.get("section") or "instances")
    try:
        data = await _run_with_timeout(get_project_inventory, project_id, session.server.openstack_auth, section)
        return {"inventory": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "inventory": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading project inventory",
            "api_issue": None,
        }
    except Exception as exc:
        return {"inventory": None, "error": str(exc), "api_issue": build_api_issue("OpenStack", f"GET aggregated inventory for project {project_id}", exc)}


@router.post("/api/projects/{project_id}/quota")
async def api_project_quota_update(project_id: str, request: Request):
    session = _require_session_record()(request)
    if not session.is_admin:
        return JSONResponse(
            status_code=403,
            content={
                "inventory": None,
                "error": "Quota modification requires the OpenStack 'admin' role.",
                "api_issue": None,
            },
        )
    payload = await request.json()
    section = str(payload.get("section") or "").strip().lower()
    resource = str(payload.get("resource") or "").strip().lower()
    limit = payload.get("limit")
    service = {
        "compute": "Nova",
        "network": "Neutron",
        "block_storage": "Cinder",
    }.get(section, "OpenStack")
    try:
        data = await _run_with_timeout(
            update_project_quota_limit,
            project_id,
            section,
            resource,
            limit,
            session.server.openstack_auth,
        )
        return {"inventory": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "inventory": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while updating project quota",
            "api_issue": None,
        }
    except Exception as exc:
        return {
            "inventory": None,
            "error": str(exc),
            "api_issue": build_api_issue(service, f"PUT quota {section}.{resource} for project {project_id}", exc),
        }


@router.get("/api/instances/{instance_id}")
async def api_instance_detail(instance_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(openstack_ops.get_instance_network_detail, instance_id, session.server.openstack_auth)
        loop = asyncio.get_running_loop()
        enriched_ports = []
        for port in data.get("ports", []):
            payload = dict(port)
            ovn = None
            ovn_error = None
            if session.server.k8s_auth and port.get("id") and port.get("network_id"):
                try:
                    ovn = await loop.run_in_executor(
                        None,
                        k8s_ops.get_ovn_port_logical_switch,
                        port["id"],
                        port["network_id"],
                        session.server.k8s_auth,
                    )
                except Exception as exc:
                    ovn_error = str(exc)
            payload["ovn"] = ovn
            payload["ovn_error"] = ovn_error
            enriched_ports.append(payload)
        data["ports"] = enriched_ports
        return {"instance": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "instance": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading instance details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"instance": None, "error": str(exc), "api_issue": build_api_issue("Nova", f"GET /servers/{instance_id}", exc)}


@router.get("/api/floating-ips")
async def api_floating_ips(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_floating_ips, session.server.openstack_auth)
        return {"floating_ips": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"floating_ips": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/floatingips", exc)}


@router.get("/api/ports")
async def api_ports(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_ports, session.server.openstack_auth)
        return {"ports": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"ports": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/ports", exc)}


@router.get("/api/ports/{port_id}")
async def api_port_detail(port_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_port_detail, port_id, session.server.openstack_auth)
        return {"port": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "port": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading port details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"port": None, "error": str(exc), "api_issue": build_api_issue("Neutron", f"GET /v2.0/ports/{port_id}", exc)}


@router.get("/api/volumes/{volume_id}")
async def api_volume_detail(volume_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_volume_detail, volume_id, session.server.openstack_auth)
        return {"volume": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "volume": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading volume details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"volume": None, "error": str(exc), "api_issue": build_api_issue("Cinder", f"GET /v3/volumes/{volume_id}", exc)}


@router.post("/api/volumes/{volume_id}/retype")
async def api_volume_retype(volume_id: str, request: Request):
    session = _require_session_record()(request)
    payload = await request.json()
    target_type = str(payload.get("target_type") or "").strip()
    migration_policy = str(payload.get("migration_policy") or "on-demand").strip().lower()
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(
            None,
            retype_volume,
            volume_id,
            target_type,
            migration_policy,
            session.server.openstack_auth,
        )
        return {"result": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {
            "result": None,
            "error": str(exc),
            "api_issue": build_api_issue("Cinder", f"POST /v3/volumes/{volume_id}/action os-retype", exc),
        }


@router.get("/api/volume-snapshots")
async def api_volume_snapshots(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_volume_snapshots, session.server.openstack_auth)
        return {"snapshots": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"snapshots": [], "error": str(exc), "api_issue": build_api_issue("Cinder", "GET /v3/snapshots", exc)}


@router.get("/api/volume-backups")
async def api_volume_backups(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_volume_backups, session.server.openstack_auth)
        return {"backups": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"backups": [], "error": str(exc), "api_issue": build_api_issue("Cinder", "GET /v3/backups", exc)}


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


@router.get("/api/security-groups")
async def api_security_groups(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, get_security_groups, session.server.openstack_auth)
        return {"security_groups": data, "error": None, "api_issue": None}
    except Exception as exc:
        return {"security_groups": [], "error": str(exc), "api_issue": build_api_issue("Neutron", "GET /v2.0/security-groups", exc)}


@router.get("/api/security-groups/{group_id}")
async def api_security_group_detail(group_id: str, request: Request):
    session = _require_session_record()(request)
    try:
        data = await _run_with_timeout(get_security_group_detail, group_id, session.server.openstack_auth)
        return {"security_group": data, "error": None, "api_issue": None}
    except TimeoutError:
        return {
            "security_group": None,
            "error": f"Timed out after {_RESOURCE_DETAIL_TIMEOUT_SECONDS:.0f}s while loading security group details",
            "api_issue": None,
        }
    except Exception as exc:
        return {"security_group": None, "error": str(exc), "api_issue": build_api_issue("Neutron", f"GET /v2.0/security-groups/{group_id}", exc)}


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
