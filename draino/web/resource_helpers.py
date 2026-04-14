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
    connected_router_by_network: dict[str, str] = {}
    try:
        for port in conn.network.ports():
            port_data = port.to_dict() if hasattr(port, "to_dict") else {}
            device_owner = getattr(port, "device_owner", None) or port_data.get("device_owner") or ""
            if not _is_router_interface_owner(device_owner):
                continue
            network_id = getattr(port, "network_id", None) or port_data.get("network_id") or ""
            router_id = getattr(port, "device_id", None) or port_data.get("device_id") or ""
            if network_id and router_id and network_id not in connected_router_by_network:
                connected_router_by_network[network_id] = router_id
    except Exception:
        connected_router_by_network = {}

    result = []
    for network in conn.network.networks():
        data = network.to_dict() if hasattr(network, "to_dict") else {}
        raw_external = data.get("router:external")
        if raw_external is None:
            raw_external = getattr(network, "is_router_external", False)
        network_type = (
            data.get("provider:network_type")
            or getattr(network, "provider_network_type", None)
            or getattr(network, "network_type", None)
            or ""
        )
        result.append({
            "id": network.id,
            "name": network.name or "(unnamed)",
            "status": network.status or "UNKNOWN",
            "admin_state": "up" if network.is_admin_state_up else "down",
            "shared": bool(network.is_shared),
            "external": coerce_bool(raw_external),
            "network_type": network_type,
            "project_id": network.project_id or "",
            "subnet_count": len(list(network.subnet_ids or [])),
            "router_connected": network.id in connected_router_by_network,
            "router_id": connected_router_by_network.get(network.id, ""),
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


def repair_subnet_metadata_port(network_id: str, subnet_id: str, auth: openstack_ops.OpenStackAuth | None) -> dict:
    """Create a missing metadata port for a subnet using the OpenStack SDK."""
    conn = openstack_ops._conn(auth=auth)
    subnet = conn.network.get_subnet(subnet_id)
    project_id = getattr(subnet, "project_id", None) or ""
    port = conn.network.create_port(
        name="metadata-port-repaired-by-vibeview",
        network_id=network_id,
        fixed_ips=[{"subnet_id": subnet_id}],
        device_owner="network:distributed",
        device_id=f"ovnmeta-{network_id}",
        project_id=project_id,
    )
    port_data = port.to_dict() if hasattr(port, "to_dict") else {}
    fixed_ips = list(getattr(port, "fixed_ips", None) or port_data.get("fixed_ips") or [])
    ip_address = ""
    for item in fixed_ips:
        if item.get("subnet_id") == subnet_id:
            ip_address = item.get("ip_address", "") or ""
            break
    return {
        "port_id": getattr(port, "id", None) or port_data.get("id") or "",
        "ip_address": ip_address,
        "status": "ok",
    }


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


def _lookup_floating_ip_by_port_or_address(conn, vip_port_id: str, vip_address: str, cache: dict[str, str]) -> str:
    cache_key = f"{vip_port_id}:{vip_address}"
    if cache_key in cache:
        return cache[cache_key]
    floating_ip = ""
    try:
        if vip_port_id:
            for item in conn.network.ips(port_id=vip_port_id):
                address = getattr(item, "floating_ip_address", None) or ""
                if address:
                    floating_ip = address
                    break
        if not floating_ip and vip_address:
            for item in conn.network.ips():
                fixed_ip = getattr(item, "fixed_ip_address", None) or ""
                if fixed_ip == vip_address:
                    address = getattr(item, "floating_ip_address", None) or ""
                    if address:
                        floating_ip = address
                        break
    except Exception:
        floating_ip = ""
    cache[cache_key] = floating_ip
    return floating_ip


def _lb_listener_ids(lb, data: dict) -> list[str]:
    listeners = data.get("listeners") or getattr(lb, "listeners", None) or []
    ids: list[str] = []
    for item in listeners:
        if isinstance(item, dict):
            listener_id = item.get("id") or ""
        else:
            listener_id = getattr(item, "id", None) or ""
        if listener_id:
            ids.append(listener_id)
    return ids


def _lb_pool_ids(lb, data: dict) -> list[str]:
    pools = data.get("pools") or getattr(lb, "pools", None) or []
    ids: list[str] = []
    for item in pools:
        if isinstance(item, dict):
            pool_id = item.get("id") or ""
        else:
            pool_id = getattr(item, "id", None) or ""
        if pool_id:
            ids.append(pool_id)
    return ids


def get_load_balancers(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    """Return all Octavia load balancers visible to the configured credential."""
    conn = openstack_ops._conn(auth=auth)
    fip_cache: dict[str, str] = {}
    result = []
    for lb in conn.load_balancer.load_balancers():
        data = lb.to_dict() if hasattr(lb, "to_dict") else {}
        vip_address = getattr(lb, "vip_address", None) or data.get("vip_address") or ""
        vip_port_id = getattr(lb, "vip_port_id", None) or data.get("vip_port_id") or ""
        project_id = getattr(lb, "project_id", None) or data.get("project_id") or ""
        listener_ids = _lb_listener_ids(lb, data)
        pool_ids = _lb_pool_ids(lb, data)
        amphora_count = 0
        try:
            amphora_count = sum(
                1 for amp in conn.load_balancer.amphorae()
                if (getattr(amp, "loadbalancer_id", None) or "") == lb.id
            )
        except Exception:
            amphora_count = 0
        result.append({
            "id": lb.id,
            "name": getattr(lb, "name", None) or "(unnamed)",
            "operating_status": getattr(lb, "operating_status", None) or data.get("operating_status") or "UNKNOWN",
            "provisioning_status": getattr(lb, "provisioning_status", None) or data.get("provisioning_status") or "UNKNOWN",
            "vip_address": vip_address,
            "floating_ip": _lookup_floating_ip_by_port_or_address(conn, vip_port_id, vip_address, fip_cache),
            "vip_port_id": vip_port_id,
            "project_id": project_id,
            "listener_count": len(listener_ids),
            "pool_count": len(pool_ids),
            "amphora_count": amphora_count,
        })
    result.sort(key=lambda item: (item["name"], item["id"]))
    return result


def get_load_balancer_detail(lb_id: str, auth: openstack_ops.OpenStackAuth | None) -> dict:
    """Return listeners, pools, and amphora detail for a single Octavia load balancer."""
    conn = openstack_ops._conn(auth=auth)
    lb = conn.load_balancer.get_load_balancer(lb_id)
    lb_data = lb.to_dict() if hasattr(lb, "to_dict") else {}
    vip_port_id = getattr(lb, "vip_port_id", None) or lb_data.get("vip_port_id") or ""
    vip_address = getattr(lb, "vip_address", None) or lb_data.get("vip_address") or ""
    project_id = getattr(lb, "project_id", None) or lb_data.get("project_id") or ""
    floating_ip = _lookup_floating_ip_by_port_or_address(conn, vip_port_id, vip_address, {})
    vip_port: dict[str, object] = {
        "id": vip_port_id,
        "name": "",
        "status": "",
        "network_id": "",
        "subnet_id": "",
        "ip_address": vip_address,
        "mac_address": "",
        "device_owner": "",
        "device_id": "",
        "project_id": project_id,
        "admin_state_up": False,
    }
    if vip_port_id:
        try:
            port = conn.network.get_port(vip_port_id)
            port_data = port.to_dict() if hasattr(port, "to_dict") else {}
            fixed_ips = list(getattr(port, "fixed_ips", None) or port_data.get("fixed_ips") or [])
            primary_ip = next((item.get("ip_address", "") for item in fixed_ips if item.get("ip_address")), "") or vip_address
            primary_subnet = next((item.get("subnet_id", "") for item in fixed_ips if item.get("subnet_id")), "") or ""
            vip_port = {
                "id": getattr(port, "id", None) or port_data.get("id") or vip_port_id,
                "name": getattr(port, "name", None) or port_data.get("name") or "",
                "status": getattr(port, "status", None) or port_data.get("status") or "",
                "network_id": getattr(port, "network_id", None) or port_data.get("network_id") or "",
                "subnet_id": primary_subnet,
                "ip_address": primary_ip,
                "mac_address": getattr(port, "mac_address", None) or port_data.get("mac_address") or "",
                "device_owner": getattr(port, "device_owner", None) or port_data.get("device_owner") or "",
                "device_id": getattr(port, "device_id", None) or port_data.get("device_id") or "",
                "project_id": getattr(port, "project_id", None) or port_data.get("project_id") or project_id,
                "admin_state_up": bool(getattr(port, "is_admin_state_up", port_data.get("admin_state_up", False))),
            }
        except Exception:
            pass

    listeners_by_id: dict[str, object] = {}
    try:
        for item in conn.load_balancer.listeners():
            listeners_by_id[getattr(item, "id", None) or ""] = item
    except Exception:
        pass
    pools_by_id: dict[str, object] = {}
    try:
        for item in conn.load_balancer.pools():
            pools_by_id[getattr(item, "id", None) or ""] = item
    except Exception:
        pass

    listeners = []
    for listener_id in _lb_listener_ids(lb, lb_data):
        listener = listeners_by_id.get(listener_id)
        if listener is None:
            try:
                listener = conn.load_balancer.get_listener(listener_id)
            except Exception:
                listener = None
        if listener is None:
            continue
        listener_data = listener.to_dict() if hasattr(listener, "to_dict") else {}
        listeners.append({
            "id": getattr(listener, "id", None) or listener_id,
            "name": getattr(listener, "name", None) or listener_data.get("name") or "(unnamed)",
            "protocol": getattr(listener, "protocol", None) or listener_data.get("protocol") or "",
            "protocol_port": getattr(listener, "protocol_port", None) or listener_data.get("protocol_port"),
            "default_pool_id": getattr(listener, "default_pool_id", None) or listener_data.get("default_pool_id") or "",
        })

    pools = []
    for pool_id in _lb_pool_ids(lb, lb_data):
        pool = pools_by_id.get(pool_id)
        if pool is None:
            try:
                pool = conn.load_balancer.get_pool(pool_id)
            except Exception:
                pool = None
        if pool is None:
            continue
        pool_data = pool.to_dict() if hasattr(pool, "to_dict") else {}
        member_count = 0
        try:
            member_count = len(list(conn.load_balancer.members(pool.id)))
        except Exception:
            member_count = 0
        hm_text = ""
        healthmonitor_id = getattr(pool, "healthmonitor_id", None) or pool_data.get("healthmonitor_id") or ""
        if healthmonitor_id:
            try:
                hm = conn.load_balancer.get_health_monitor(healthmonitor_id)
                hm_data = hm.to_dict() if hasattr(hm, "to_dict") else {}
                monitor_type = getattr(hm, "type", None) or hm_data.get("type") or ""
                delay = getattr(hm, "delay", None) or hm_data.get("delay")
                timeout = getattr(hm, "timeout", None) or hm_data.get("timeout")
                retries = getattr(hm, "max_retries", None) or hm_data.get("max_retries")
                parts = [monitor_type] if monitor_type else []
                if delay is not None:
                    parts.append(f"delay {delay}")
                if timeout is not None:
                    parts.append(f"timeout {timeout}")
                if retries is not None:
                    parts.append(f"max retries {retries}")
                hm_text = "\n".join(parts)
            except Exception:
                hm_text = ""
        persistence = getattr(pool, "session_persistence", None) or pool_data.get("session_persistence") or {}
        if isinstance(persistence, dict):
            persistence = persistence.get("type") or ""
        tls_enabled = bool(
            getattr(pool, "tls_enabled", None)
            or pool_data.get("tls_enabled")
            or getattr(pool, "tls_container_ref", None)
            or pool_data.get("tls_container_ref")
        )
        pools.append({
            "id": getattr(pool, "id", None) or pool_id,
            "name": getattr(pool, "name", None) or pool_data.get("name") or "(unnamed)",
            "protocol": getattr(pool, "protocol", None) or pool_data.get("protocol") or "",
            "lb_algorithm": getattr(pool, "lb_algorithm", None) or pool_data.get("lb_algorithm") or "",
            "member_count": member_count,
            "admin_state_up": bool(getattr(pool, "is_admin_state_up", pool_data.get("admin_state_up", False))),
            "operating_status": getattr(pool, "operating_status", None) or pool_data.get("operating_status") or "UNKNOWN",
            "healthmonitor": hm_text,
            "session_persistence": persistence or "None",
            "tls_enabled": tls_enabled,
        })

    amphorae = []
    amphora_hosts: set[str] = set()
    for amp in conn.load_balancer.amphorae():
        if (getattr(amp, "loadbalancer_id", None) or "") != lb_id:
            continue
        compute_id = getattr(amp, "compute_id", None) or ""
        compute_host = ""
        image_id = ""
        if compute_id:
            try:
                server = conn.compute.get_server(compute_id)
                compute_host = openstack_ops._server_host(server) or ""
                image = getattr(server, "image", None) or {}
                if isinstance(image, dict):
                    image_id = image.get("id") or ""
            except Exception:
                compute_host = ""
        if compute_host:
            amphora_hosts.add(compute_host)
        amp_data = amp.to_dict() if hasattr(amp, "to_dict") else {}
        amphorae.append({
            "id": getattr(amp, "id", None) or amp_data.get("id") or "",
            "role": getattr(amp, "role", None) or amp_data.get("role") or "",
            "status": getattr(amp, "status", None) or amp_data.get("status") or "",
            "compute_id": compute_id,
            "compute_host": compute_host,
            "lb_network_ip": getattr(amp, "lb_network_ip", None) or amp_data.get("lb_network_ip") or "",
            "ha_ip": getattr(amp, "ha_ip", None) or amp_data.get("ha_ip") or "",
            "vrrp_ip": getattr(amp, "vrrp_ip", None) or amp_data.get("vrrp_ip") or "",
            "image_id": image_id,
        })

    return {
        "id": lb.id,
        "name": getattr(lb, "name", None) or "(unnamed)",
        "operating_status": getattr(lb, "operating_status", None) or lb_data.get("operating_status") or "UNKNOWN",
        "provisioning_status": getattr(lb, "provisioning_status", None) or lb_data.get("provisioning_status") or "UNKNOWN",
        "vip_address": vip_address,
        "floating_ip": floating_ip,
        "vip_port_id": vip_port_id,
        "vip_subnet_id": getattr(lb, "vip_subnet_id", None) or lb_data.get("vip_subnet_id") or "",
        "vip_port": vip_port,
        "project_id": project_id,
        "flavor_id": getattr(lb, "flavor_id", None) or lb_data.get("flavor_id") or "",
        "listeners": listeners,
        "pools": pools,
        "amphorae": amphorae,
        "distinct_host_count": len(amphora_hosts),
        "ha_summary": "HA spread OK" if len(amphora_hosts) >= 2 else ("Single host" if amphorae else "Unknown"),
    }


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
