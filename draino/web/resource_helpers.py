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
