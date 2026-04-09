"""OpenStack-backed resource helpers for the web UI."""
from __future__ import annotations

from ..operations import openstack_ops


def coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def get_networks(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    """Return all Neutron networks visible to the configured credential."""
    conn = openstack_ops._conn(auth=auth)
    result = []
    for network in conn.network.networks():
        data = network.to_dict() if hasattr(network, "to_dict") else {}
        raw_external = data.get("router:external")
        if raw_external is None:
            raw_external = getattr(network, "is_router_external", False)
        result.append({
            "id": network.id,
            "name": network.name or "(unnamed)",
            "status": network.status or "UNKNOWN",
            "admin_state": "up" if network.is_admin_state_up else "down",
            "shared": bool(network.is_shared),
            "external": coerce_bool(raw_external),
            "network_type": data.get("provider:network_type") or "",
            "project_id": network.project_id or "",
            "subnet_count": len(list(network.subnet_ids or [])),
        })
    return result


def get_network_detail(network_id: str, auth: openstack_ops.OpenStackAuth | None) -> dict:
    """Return subnets and segments for a single Neutron network."""
    conn = openstack_ops._conn(auth=auth)
    network = conn.network.get_network(network_id)
    network_data = network.to_dict() if hasattr(network, "to_dict") else {}
    metadata_ports_by_subnet: dict[str, dict] = {}

    try:
        for port in conn.network.ports(network_id=network_id):
            port_data = port.to_dict() if hasattr(port, "to_dict") else {}
            device_owner = getattr(port, "device_owner", None) or port_data.get("device_owner") or ""
            device_id = getattr(port, "device_id", None) or port_data.get("device_id") or ""
            if device_owner != "network:distributed":
                continue
            if not str(device_id).startswith("ovnmeta"):
                continue
            if not str(device_id).endswith(network_id):
                continue
            fixed_ips = list(getattr(port, "fixed_ips", None) or port_data.get("fixed_ips") or [])
            for fixed_ip in fixed_ips:
                subnet_id = fixed_ip.get("subnet_id") or ""
                if not subnet_id or subnet_id in metadata_ports_by_subnet:
                    continue
                metadata_ports_by_subnet[subnet_id] = {
                    "port_id": getattr(port, "id", None) or port_data.get("id") or "",
                    "ip_address": fixed_ip.get("ip_address", "") or "",
                    "status": "ok",
                }
    except Exception:
        pass

    subnets = []
    for subnet_id in (network.subnet_ids or []):
        try:
            subnet = conn.network.get_subnet(subnet_id)
            subnets.append({
                "id": subnet.id,
                "name": subnet.name or "",
                "cidr": subnet.cidr or "",
                "ip_version": subnet.ip_version,
                "gateway_ip": subnet.gateway_ip or "",
                "enable_dhcp": bool(getattr(subnet, "is_dhcp_enabled", False)),
                "allocation_pools": getattr(subnet, "allocation_pools", []) or [],
                "dns_nameservers": getattr(subnet, "dns_nameservers", []) or [],
                "host_routes": getattr(subnet, "host_routes", []) or [],
                "metadata_port": metadata_ports_by_subnet.get(subnet_id, {"port_id": "", "ip_address": "", "status": "missing"}),
            })
        except Exception:
            pass

    segments = []
    try:
        for segment in conn.network.segments(network_id=network_id):
            segment_data = segment.to_dict() if hasattr(segment, "to_dict") else {}
            segments.append({
                "id": segment.id or "",
                "name": segment.name or "",
                "network_type": segment_data.get("network_type") or getattr(segment, "network_type", "") or "",
                "physical_network": segment_data.get("physical_network") or getattr(segment, "physical_network", "") or "",
                "segmentation_id": segment_data.get("segmentation_id", getattr(segment, "segmentation_id", None)),
            })
    except Exception:
        pass

    if not segments:
        network_type = network_data.get("provider:network_type") or ""
        physical_network = network_data.get("provider:physical_network") or ""
        segmentation_id = network_data.get("provider:segmentation_id")
        if network_type or physical_network or segmentation_id is not None:
            segments = [{
                "id": "",
                "name": "",
                "network_type": network_type,
                "physical_network": physical_network,
                "segmentation_id": segmentation_id,
            }]

    return {"subnets": subnets, "segments": segments}


def _lookup_network_name(conn, network_id: str, cache: dict[str, str]) -> str:
    if not network_id:
        return ""
    if network_id in cache:
        return cache[network_id]
    try:
        network = conn.network.get_network(network_id)
        cache[network_id] = getattr(network, "name", None) or "(unnamed)"
    except Exception:
        cache[network_id] = ""
    return cache[network_id]


def _lookup_subnet_detail(conn, subnet_id: str, cache: dict[str, dict]) -> dict:
    if not subnet_id:
        return {}
    if subnet_id in cache:
        return cache[subnet_id]
    try:
        subnet = conn.network.get_subnet(subnet_id)
        cache[subnet_id] = {
            "id": getattr(subnet, "id", None) or subnet_id,
            "name": getattr(subnet, "name", None) or "",
            "cidr": getattr(subnet, "cidr", None) or "",
            "gateway_ip": getattr(subnet, "gateway_ip", None) or "",
            "enable_dhcp": bool(getattr(subnet, "is_dhcp_enabled", False)),
        }
    except Exception:
        cache[subnet_id] = {
            "id": subnet_id,
            "name": "",
            "cidr": "",
            "gateway_ip": "",
            "enable_dhcp": False,
        }
    return cache[subnet_id]


def _iter_router_ports(conn, router_id: str) -> list:
    try:
        return list(conn.network.ports(device_id=router_id))
    except Exception:
        return []


def _is_router_interface_owner(owner: str) -> bool:
    owner = owner or ""
    return "router" in owner and "interface" in owner


def get_routers(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    """Return all Neutron routers visible to the configured credential."""
    conn = openstack_ops._conn(auth=auth)
    network_name_cache: dict[str, str] = {}
    result = []
    for router in conn.network.routers():
        data = router.to_dict() if hasattr(router, "to_dict") else {}
        gateway = data.get("external_gateway_info") or getattr(router, "external_gateway_info", None) or {}
        external_network_id = gateway.get("network_id") or ""
        ports = _iter_router_ports(conn, router.id)
        interface_count = sum(
            1
            for port in ports
            if _is_router_interface_owner((getattr(port, "device_owner", None) or "") or "")
        )
        result.append({
            "id": router.id,
            "name": getattr(router, "name", None) or "(unnamed)",
            "status": getattr(router, "status", None) or data.get("status") or "UNKNOWN",
            "admin_state": "up" if getattr(router, "is_admin_state_up", data.get("admin_state_up", False)) else "down",
            "ha": coerce_bool(data.get("ha", getattr(router, "ha", False))),
            "distributed": coerce_bool(data.get("distributed", getattr(router, "distributed", False))),
            "project_id": getattr(router, "project_id", None) or data.get("project_id") or "",
            "external_network_id": external_network_id,
            "external_network_name": _lookup_network_name(conn, external_network_id, network_name_cache),
            "external_gateway_ips": [item.get("ip_address", "") for item in gateway.get("external_fixed_ips", []) if item.get("ip_address")],
            "interface_count": interface_count,
            "route_count": len(data.get("routes") or getattr(router, "routes", []) or []),
        })
    return result


def get_router_detail(router_id: str, auth: openstack_ops.OpenStackAuth | None) -> dict:
    """Return connected subnet, route, and gateway detail for one router."""
    conn = openstack_ops._conn(auth=auth)
    router = conn.network.get_router(router_id)
    router_data = router.to_dict() if hasattr(router, "to_dict") else {}
    network_name_cache: dict[str, str] = {}
    subnet_cache: dict[str, dict] = {}

    gateway = router_data.get("external_gateway_info") or getattr(router, "external_gateway_info", None) or {}
    external_network_id = gateway.get("network_id") or ""
    external_fixed_ips = []
    for item in gateway.get("external_fixed_ips", []) or []:
        subnet_id = item.get("subnet_id", "") or ""
        subnet = _lookup_subnet_detail(conn, subnet_id, subnet_cache) if subnet_id else {}
        external_fixed_ips.append({
            "subnet_id": subnet_id,
            "subnet_name": subnet.get("name", ""),
            "cidr": subnet.get("cidr", ""),
            "ip_address": item.get("ip_address", "") or "",
        })

    interfaces = []
    connected_subnets = []
    for port in _iter_router_ports(conn, router_id):
        port_data = port.to_dict() if hasattr(port, "to_dict") else {}
        device_owner = getattr(port, "device_owner", None) or port_data.get("device_owner") or ""
        if not _is_router_interface_owner(device_owner):
            continue
        network_id = getattr(port, "network_id", None) or port_data.get("network_id") or ""
        network_name = _lookup_network_name(conn, network_id, network_name_cache)
        fixed_ips = list(getattr(port, "fixed_ips", None) or port_data.get("fixed_ips") or [])
        for item in fixed_ips:
            subnet_id = item.get("subnet_id", "") or ""
            subnet = _lookup_subnet_detail(conn, subnet_id, subnet_cache) if subnet_id else {}
            entry = {
                "port_id": getattr(port, "id", None) or "",
                "network_id": network_id,
                "network_name": network_name,
                "subnet_id": subnet_id,
                "subnet_name": subnet.get("name", ""),
                "cidr": subnet.get("cidr", ""),
                "gateway_ip": subnet.get("gateway_ip", ""),
                "enable_dhcp": subnet.get("enable_dhcp", False),
                "ip_address": item.get("ip_address", "") or "",
                "device_owner": device_owner,
            }
            interfaces.append(entry)
            connected_subnets.append(entry)

    routes = []
    for route in (router_data.get("routes") or getattr(router, "routes", []) or []):
        routes.append({
            "destination": route.get("destination", "") or "",
            "nexthop": route.get("nexthop", "") or "",
        })

    return {
        "id": router.id,
        "name": getattr(router, "name", None) or "(unnamed)",
        "status": getattr(router, "status", None) or router_data.get("status") or "UNKNOWN",
        "admin_state": "up" if getattr(router, "is_admin_state_up", router_data.get("admin_state_up", False)) else "down",
        "ha": coerce_bool(router_data.get("ha", getattr(router, "ha", False))),
        "distributed": coerce_bool(router_data.get("distributed", getattr(router, "distributed", False))),
        "project_id": getattr(router, "project_id", None) or router_data.get("project_id") or "",
        "external_gateway": {
            "network_id": external_network_id,
            "network_name": _lookup_network_name(conn, external_network_id, network_name_cache),
            "enable_snat": coerce_bool(gateway.get("enable_snat")),
            "external_fixed_ips": external_fixed_ips,
        },
        "interface_count": len(interfaces),
        "route_count": len(routes),
        "connected_subnets": connected_subnets,
        "routes": routes,
    }


def get_volumes(auth: openstack_ops.OpenStackAuth | None) -> tuple[list[dict], bool]:
    """Return all Cinder volumes with project-scope fallback."""
    conn = openstack_ops._conn(auth=auth)
    all_projects = False
    try:
        volumes = list(conn.volume.volumes(all_projects=True))
        all_projects = True
    except Exception:
        volumes = list(conn.volume.volumes())

    result = []
    for volume in volumes:
        attachments = getattr(volume, "attachments", []) or []
        project_id = (
            getattr(volume, "os-vol-tenant-attr:tenant_id", None)
            or getattr(volume, "project_id", None)
            or ""
        )
        result.append({
            "id": volume.id,
            "name": volume.name or "(no name)",
            "status": volume.status or "UNKNOWN",
            "size_gb": volume.size or 0,
            "volume_type": volume.volume_type or "",
            "project_id": project_id,
            "attached_to": [attachment.get("server_id", "") for attachment in attachments],
            "bootable": bool(getattr(volume, "is_bootable", False)),
            "encrypted": bool(getattr(volume, "encrypted", False)),
        })
    return result, all_projects
