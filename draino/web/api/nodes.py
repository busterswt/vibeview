"""Node-focused API routes."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...operations import k8s_ops, openstack_ops

router = APIRouter()

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None
_get_network_detail_getter: Callable[[], Callable[[str, openstack_ops.OpenStackAuth | None], dict]] | None = None


def configure(
    *,
    get_session_record: Callable[[], Callable[[Request], object]],
    get_network_detail: Callable[[], Callable[[str, openstack_ops.OpenStackAuth | None], dict]],
) -> None:
    global _get_session_record_getter, _get_network_detail_getter
    _get_session_record_getter = get_session_record
    _get_network_detail_getter = get_network_detail


def _require_session_record() -> Callable[[Request], object]:
    if _get_session_record_getter is None:
        raise RuntimeError("node routes are not configured")
    return _get_session_record_getter()


def _require_network_detail() -> Callable[[str, openstack_ops.OpenStackAuth | None], dict]:
    if _get_network_detail_getter is None:
        raise RuntimeError("node routes are not configured")
    return _get_network_detail_getter()


class AnnotationPatch(BaseModel):
    key: str
    value: str | None = None


class ManagedNoSchedulePatch(BaseModel):
    enabled: bool


@router.get("/api/ovn/lsp/{port_id}")
async def api_ovn_port_detail(port_id: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, k8s_ops.get_ovn_port_detail, port_id, session.server.k8s_auth)
        return {"port": data, "error": None}
    except Exception as exc:
        return {"port": None, "error": str(exc)}


@router.get("/api/networks/{network_id}/ovn")
async def api_network_ovn(network_id: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, k8s_ops.get_ovn_logical_switch, network_id, session.server.k8s_auth)
        return {"ovn": data, "error": None}
    except Exception as exc:
        return {"ovn": None, "error": str(exc)}


@router.get("/api/nodes/{node_name}/detail")
async def api_node_detail(node_name: str, request: Request):
    session = _require_session_record()(request)
    server = session.server
    force_refresh = request.query_params.get("refresh", "").strip().lower() in {"1", "true", "yes"}
    if force_refresh:
        server.invalidate_node_detail(node_name)
    cached = server.get_cached_node_detail(node_name)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    state = server.node_states.get(node_name)
    k8s_future = loop.run_in_executor(None, k8s_ops.get_node_k8s_detail, node_name, server.k8s_auth)
    hw_future = loop.run_in_executor(
        None,
        k8s_ops.get_node_hardware_info,
        node_name,
        state.hypervisor if state else None,
    )

    nova: dict = {}
    if state and state.is_compute:
        nova = await loop.run_in_executor(None, openstack_ops.get_hypervisor_detail, state.hypervisor, server.openstack_auth)

    k8s = await k8s_future
    hw = await hw_future
    payload = {"k8s": k8s, "nova": nova, "hw": hw, "error": None}
    server.set_cached_node_detail(node_name, payload)
    return payload


@router.get("/api/nodes/{node_name}/metrics")
async def api_node_metrics(node_name: str, request: Request):
    session = _require_session_record()(request)
    server = session.server
    force_refresh = request.query_params.get("refresh", "").strip().lower() in {"1", "true", "yes"}
    if force_refresh:
        server.invalidate_node_metrics(node_name)
    cached = server.get_cached_node_metrics(node_name)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    state = server.node_states.get(node_name)
    payload = await loop.run_in_executor(
        None,
        k8s_ops.get_node_monitor_metrics,
        node_name,
        state.hypervisor if state else None,
    )
    server.set_cached_node_metrics(node_name, payload)
    return payload


@router.get("/api/nodes/{node_name}/network-stats")
async def api_node_network_stats(node_name: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    state = session.server.node_states.get(node_name)
    return await loop.run_in_executor(
        None,
        k8s_ops.get_node_network_stats,
        node_name,
        state.hypervisor if state else None,
    )


@router.get("/api/nodes/{node_name}/ovn-annotations")
async def api_node_ovn_annotations(node_name: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, k8s_ops.get_node_ovn_annotations, node_name, session.server.k8s_auth)


@router.post("/api/nodes/{node_name}/ovn-annotations")
async def api_patch_ovn_annotation(node_name: str, payload: AnnotationPatch, request: Request):
    session = _require_session_record()(request)
    if payload.key not in k8s_ops.OVN_ANNOTATION_KEYS:
        return {"ok": False, "error": f"Key {payload.key!r} not in allowed OVN annotation keys"}
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, k8s_ops.patch_node_annotation, node_name, payload.key, payload.value, session.server.k8s_auth
        )
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.post("/api/nodes/{node_name}/taints/noschedule")
async def api_patch_managed_noschedule_taint(node_name: str, payload: ManagedNoSchedulePatch, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            k8s_ops.set_managed_noschedule_taint,
            node_name,
            payload.enabled,
            session.server.k8s_auth,
        )
        session.server.start_refresh(silent=True)
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/api/nodes/{node_name}/network-interfaces")
async def api_node_network_interfaces(node_name: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    state = session.server.node_states.get(node_name)
    return await loop.run_in_executor(
        None, k8s_ops.get_node_network_interfaces, node_name, state.hypervisor if state else None
    )


@router.get("/api/nodes/{node_name}/instances/{instance_id}")
async def api_node_instance_detail(node_name: str, instance_id: str, request: Request):
    session = _require_session_record()(request)
    server = session.server
    loop = asyncio.get_running_loop()

    try:
        instance = await loop.run_in_executor(
            None,
            openstack_ops.get_instance_network_detail,
            instance_id,
            server.openstack_auth,
        )
    except Exception as exc:
        return {"instance": None, "error": str(exc)}

    enriched_ports = []
    for port in instance.get("ports", []):
        payload = dict(port)
        ovn = None
        ovn_error = None
        if port.get("id") and port.get("network_id"):
            try:
                ovn = await loop.run_in_executor(
                    None,
                    k8s_ops.get_ovn_port_logical_switch,
                    port["id"],
                    port["network_id"],
                    server.k8s_auth,
                )
            except Exception as exc:
                ovn_error = str(exc)
        payload["ovn"] = ovn
        payload["ovn_error"] = ovn_error
        enriched_ports.append(payload)
    instance["ports"] = enriched_ports

    state = server.node_states.get(node_name)
    if state and state.hypervisor and instance.get("compute_host") and instance["compute_host"] != state.hypervisor:
        instance["node_mismatch"] = {
            "requested_node": node_name,
            "requested_hypervisor": state.hypervisor,
            "actual_hypervisor": instance["compute_host"],
        }

    return {"instance": instance, "error": None}


@router.get("/api/networks/{network_id}")
async def api_network_detail(network_id: str, request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, _require_network_detail(), network_id, session.server.openstack_auth)
        return {"network": data, "error": None}
    except Exception as exc:
        return {"network": None, "error": str(exc)}
