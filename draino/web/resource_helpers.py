"""OpenStack-backed resource helpers for the web UI."""
from __future__ import annotations

from collections import defaultdict

from ..operations import openstack_ops

_OPEN_WORLD_CIDRS = {"0.0.0.0/0", "::/0"}
_ADMIN_PORTS = {22, 3389, 5900, 6443}


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


def _project_names(conn) -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        for project in conn.identity.projects():
            project_id = getattr(project, "id", None) or ""
            if not project_id:
                continue
            names[project_id] = getattr(project, "name", None) or project_id
    except Exception:
        return names
    return names


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


def _lookup_server_brief(conn, server_id: str, cache: dict[str, dict[str, str]]) -> dict[str, str]:
    if not server_id:
        return {"name": "", "compute_host": ""}
    if server_id in cache:
        return cache[server_id]
    try:
        server = conn.compute.get_server(server_id)
        server_data = server.to_dict() if hasattr(server, "to_dict") else {}
        cache[server_id] = {
            "name": getattr(server, "name", None) or server_data.get("name") or "",
            "compute_host": openstack_ops._server_host(server) or "",
        }
    except Exception:
        cache[server_id] = {"name": "", "compute_host": ""}
    return cache[server_id]


def _port_security_groups(port) -> list[str]:
    port_data = port.to_dict() if hasattr(port, "to_dict") else {}
    raw_groups = (
        getattr(port, "security_group_ids", None)
        or getattr(port, "security_groups", None)
        or port_data.get("security_group_ids")
        or port_data.get("security_groups")
        or []
    )
    result: list[str] = []
    for item in raw_groups:
        if isinstance(item, dict):
            group_id = item.get("id") or item.get("security_group_id") or ""
        else:
            group_id = str(item or "")
        if group_id:
            result.append(group_id)
    return result


def _security_group_attachment_map(conn) -> dict[str, dict]:
    attachment_map: dict[str, dict] = defaultdict(lambda: {
        "port_count": 0,
        "instance_count": 0,
        "ports": [],
        "instance_ids": set(),
    })
    network_name_cache: dict[str, str] = {}
    server_cache: dict[str, dict[str, str]] = {}
    try:
        for port in conn.network.ports():
            port_data = port.to_dict() if hasattr(port, "to_dict") else {}
            group_ids = _port_security_groups(port)
            if not group_ids:
                continue
            port_id = getattr(port, "id", None) or port_data.get("id") or ""
            network_id = getattr(port, "network_id", None) or port_data.get("network_id") or ""
            device_owner = getattr(port, "device_owner", None) or port_data.get("device_owner") or ""
            device_id = getattr(port, "device_id", None) or port_data.get("device_id") or ""
            project_id = getattr(port, "project_id", None) or port_data.get("project_id") or ""
            fixed_ips = list(getattr(port, "fixed_ips", None) or port_data.get("fixed_ips") or [])
            server_brief = {"name": "", "compute_host": ""}
            if device_owner.startswith("compute:") and device_id:
                server_brief = _lookup_server_brief(conn, device_id, server_cache)
            attachment = {
                "port_id": port_id,
                "network_id": network_id,
                "network_name": _lookup_network_name(conn, network_id, network_name_cache),
                "device_owner": device_owner,
                "device_id": device_id,
                "instance_name": server_brief.get("name", ""),
                "compute_host": server_brief.get("compute_host", ""),
                "project_id": project_id,
                "fixed_ips": fixed_ips,
            }
            for group_id in group_ids:
                entry = attachment_map[group_id]
                entry["port_count"] += 1
                entry["ports"].append(attachment)
                if device_owner.startswith("compute:") and device_id:
                    entry["instance_ids"].add(device_id)
        for entry in attachment_map.values():
            entry["instance_count"] = len(entry["instance_ids"])
            entry["instance_ids"] = sorted(entry["instance_ids"])
    except Exception:
        return {}
    return dict(attachment_map)


def _normalize_rule_direction(rule, data: dict) -> str:
    return str(getattr(rule, "direction", None) or data.get("direction") or "").lower() or "ingress"


def _normalize_rule_protocol(rule, data: dict) -> str:
    raw = getattr(rule, "protocol", None) or data.get("protocol")
    text = str(raw or "").strip().lower()
    if not text or text in {"any", "all", "-1", "none", "null"}:
        return "any"
    return text


def _normalize_rule_ports(rule, data: dict) -> tuple[int | None, int | None, str]:
    min_port = getattr(rule, "port_range_min", None)
    max_port = getattr(rule, "port_range_max", None)
    if min_port is None:
        min_port = data.get("port_range_min")
    if max_port is None:
        max_port = data.get("port_range_max")
    try:
        min_value = int(min_port) if min_port is not None else None
    except (TypeError, ValueError):
        min_value = None
    try:
        max_value = int(max_port) if max_port is not None else None
    except (TypeError, ValueError):
        max_value = None
    if min_value is None and max_value is None:
        return None, None, "any"
    if min_value is not None and max_value is not None and min_value == max_value:
        return min_value, max_value, str(min_value)
    if min_value is not None and max_value is not None:
        return min_value, max_value, f"{min_value}-{max_value}"
    value = min_value if min_value is not None else max_value
    return min_value, max_value, str(value) if value is not None else "any"


def _audit_security_group_rule(rule, data: dict) -> dict:
    direction = _normalize_rule_direction(rule, data)
    protocol = _normalize_rule_protocol(rule, data)
    min_port, max_port, port_label = _normalize_rule_ports(rule, data)
    remote_ip_prefix = str(
        getattr(rule, "remote_ip_prefix", None)
        or data.get("remote_ip_prefix")
        or ""
    ).strip()
    ethertype = str(getattr(rule, "ethertype", None) or data.get("ethertype") or "").upper()
    remote_group_id = str(
        getattr(rule, "remote_group_id", None)
        or data.get("remote_group_id")
        or data.get("remote_group")
        or ""
    ).strip()
    open_world = remote_ip_prefix in _OPEN_WORLD_CIDRS
    any_port = min_port is None and max_port is None
    admin_exposed = any(
        port in _ADMIN_PORTS
        for port in (min_port, max_port)
        if isinstance(port, int)
    )
    flagged = False
    severity = "clean"
    category = ""
    reason = ""
    summary = ""

    if direction == "ingress" and open_world and protocol == "any" and any_port:
        flagged = True
        severity = "critical"
        category = "open-world-any-any"
        summary = f"any:any {remote_ip_prefix}"
        reason = "Ingress from any source to any protocol and any port."
    elif direction == "ingress" and open_world:
        flagged = True
        severity = "high"
        category = "open-world-ingress"
        summary = f"{protocol}:{port_label} {remote_ip_prefix}"
        reason = "Ingress from the entire internet."
        if admin_exposed:
            category = "public-admin-port"
            reason = "Administrative port exposed to the entire internet."

    return {
        "direction": direction,
        "ethertype": ethertype or "",
        "protocol": protocol,
        "port_range_min": min_port,
        "port_range_max": max_port,
        "port_range": port_label,
        "remote_ip_prefix": remote_ip_prefix,
        "remote_group_id": remote_group_id,
        "is_open_world": open_world,
        "flagged": flagged,
        "severity": severity,
        "category": category,
        "summary": summary,
        "reason": reason,
    }


def _security_group_rules(group) -> list[dict]:
    rules = []
    for rule in getattr(group, "security_group_rules", None) or []:
        data = rule.to_dict() if hasattr(rule, "to_dict") else (rule if isinstance(rule, dict) else {})
        audit = _audit_security_group_rule(rule, data)
        rules.append({
            "id": getattr(rule, "id", None) or data.get("id") or "",
            "direction": audit["direction"],
            "ethertype": audit["ethertype"],
            "protocol": audit["protocol"],
            "port_range_min": audit["port_range_min"],
            "port_range_max": audit["port_range_max"],
            "port_range": audit["port_range"],
            "remote_ip_prefix": audit["remote_ip_prefix"],
            "remote_group_id": audit["remote_group_id"],
            "normalized": {
                "open_world": audit["is_open_world"],
                "summary": audit["summary"],
            },
            "audit": {
                "flagged": audit["flagged"],
                "severity": audit["severity"],
                "category": audit["category"],
                "reason": audit["reason"],
                "summary": audit["summary"],
            },
        })
    rules.sort(
        key=lambda item: (
            {"critical": 0, "high": 1, "medium": 2, "clean": 3}.get(item["audit"]["severity"], 4),
            item["direction"],
            item["protocol"],
            item["port_range"],
            item["remote_ip_prefix"],
            item["remote_group_id"],
        )
    )
    return rules


def _summarize_security_group(group, project_names: dict[str, str], attachment_map: dict[str, dict]) -> dict:
    data = group.to_dict() if hasattr(group, "to_dict") else {}
    group_id = getattr(group, "id", None) or data.get("id") or ""
    project_id = getattr(group, "project_id", None) or data.get("project_id") or ""
    rules = _security_group_rules(group)
    attachments = attachment_map.get(group_id, {})
    port_count = int(attachments.get("port_count", 0) or 0)
    instance_count = int(attachments.get("instance_count", 0) or 0)
    critical_rules = [item for item in rules if item["audit"]["severity"] == "critical"]
    high_rules = [item for item in rules if item["audit"]["severity"] == "high"]
    flagged_rules = [item for item in rules if item["audit"]["flagged"]]
    findings: list[dict] = []
    if critical_rules:
        findings.append({
            "severity": "critical",
            "category": "open-world-any-any",
            "summary": critical_rules[0]["audit"]["summary"],
            "count": len(critical_rules),
        })
    if high_rules:
        findings.append({
            "severity": "high",
            "category": high_rules[0]["audit"]["category"],
            "summary": high_rules[0]["audit"]["summary"],
            "count": len(high_rules),
        })
    if port_count == 0:
        findings.append({
            "severity": "medium",
            "category": "unused",
            "summary": "0 attachments",
            "count": 1,
        })
    severity = "clean"
    if critical_rules:
        severity = "critical"
    elif high_rules:
        severity = "high"
    elif port_count == 0:
        severity = "medium"
    score = len(critical_rules) * 100 + len(high_rules) * 50 + (20 if port_count == 0 else 0)
    return {
        "id": group_id,
        "name": getattr(group, "name", None) or data.get("name") or "(unnamed)",
        "description": getattr(group, "description", None) or data.get("description") or "",
        "project_id": project_id,
        "project_name": project_names.get(project_id) or project_id or "unknown",
        "revision_number": data.get("revision_number", getattr(group, "revision_number", None)),
        "stateful": coerce_bool(data.get("stateful", getattr(group, "stateful", True))),
        "rule_count": len(rules),
        "ingress_rule_count": sum(1 for item in rules if item["direction"] == "ingress"),
        "egress_rule_count": sum(1 for item in rules if item["direction"] == "egress"),
        "flagged_rule_count": len(flagged_rules),
        "attachment_port_count": port_count,
        "attachment_instance_count": instance_count,
        "audit": {
            "severity": severity,
            "score": score,
            "findings": findings,
            "has_open_world_ingress": any(
                item["direction"] == "ingress" and item["normalized"]["open_world"]
                for item in rules
            ),
            "has_any_any_open_world": any(item["audit"]["category"] == "open-world-any-any" for item in rules),
            "has_unused": port_count == 0,
        },
        "rules": rules,
        "attachments": attachments.get("ports", []),
    }


def _security_group_direct_references(item: dict) -> set[str]:
    refs = set()
    for rule in item.get("rules", []) or []:
        remote_group_id = str(rule.get("remote_group_id") or "").strip()
        if remote_group_id:
            refs.add(remote_group_id)
    return refs


def _security_group_reference_graph(items: list[dict]) -> tuple[dict[str, dict], dict[str, set[str]], dict[str, set[str]]]:
    by_id = {str(item.get("id") or ""): item for item in items if item.get("id")}
    adjacency = {group_id: _security_group_direct_references(item) for group_id, item in by_id.items()}
    reverse: dict[str, set[str]] = defaultdict(set)
    for source_id, refs in adjacency.items():
        for ref_id in refs:
            reverse[ref_id].add(source_id)
    return by_id, adjacency, dict(reverse)


def _security_group_reference_depth(adjacency: dict[str, set[str]], target_group_id: str) -> tuple[int, bool]:
    cycle_detected = False

    def walk_depth(group_id: str, path: tuple[str, ...]) -> int:
        nonlocal cycle_detected
        refs = adjacency.get(group_id, set())
        if not refs:
            return 0
        best = 0
        for ref_id in refs:
            if ref_id in path:
                cycle_detected = True
                continue
            best = max(best, 1 + walk_depth(ref_id, path + (ref_id,)))
        return best
    return walk_depth(target_group_id, (target_group_id,)), cycle_detected


def _security_group_reachable_refs(adjacency: dict[str, set[str]], target_group_id: str) -> set[str]:
    def walk_reachable(group_id: str, seen: set[str]) -> set[str]:
        for ref_id in adjacency.get(group_id, set()):
            if ref_id in seen:
                continue
            seen.add(ref_id)
            walk_reachable(ref_id, seen)
        return seen
    return walk_reachable(target_group_id, set())


def _security_group_reference_items(ref_ids: set[str], by_id: dict[str, dict]) -> list[dict]:
    return sorted(
        [
            {
            "id": ref_id,
            "name": by_id.get(ref_id, {}).get("name") or ref_id,
            "project_name": by_id.get(ref_id, {}).get("project_name") or by_id.get(ref_id, {}).get("project_id") or "",
            }
            for ref_id in ref_ids
        ],
        key=lambda item: (item["project_name"], item["name"], item["id"]),
    )


def _security_group_referenced_by_items(source_ids: set[str], by_id: dict[str, dict]) -> list[dict]:
    return sorted(
        [
            {
            "id": source_id,
            "name": by_id.get(source_id, {}).get("name") or source_id,
            "project_name": by_id.get(source_id, {}).get("project_name") or by_id.get(source_id, {}).get("project_id") or "",
            "flagged_rule_count": by_id.get(source_id, {}).get("flagged_rule_count", 0),
            "attachment_instance_count": by_id.get(source_id, {}).get("attachment_instance_count", 0),
            }
            for source_id in source_ids
        ],
        key=lambda item: (item["project_name"], item["name"], item["id"]),
    )


def _security_group_control_plane_complexity(
    direct_refs: set[str],
    reachable_refs: set[str],
    inbound_refs: set[str],
    depth: int,
    cycle_detected: bool,
) -> dict:
    score = (
        len(direct_refs) * 3
        + len(inbound_refs) * 2
        + len(reachable_refs)
        + depth * 2
        + (8 if cycle_detected else 0)
    )
    if cycle_detected or depth >= 3 or len(direct_refs) >= 5 or len(inbound_refs) >= 10:
        level = "high"
    elif depth >= 2 or len(direct_refs) >= 3 or len(inbound_refs) >= 5 or len(reachable_refs) >= 6:
        level = "elevated"
    else:
        level = "low"

    reasons: list[str] = []
    if len(direct_refs) >= 3:
        reasons.append(f"{len(direct_refs)} direct remote-group references")
    if depth >= 2:
        reasons.append(f"reference graph depth {depth}")
    if len(inbound_refs) >= 5:
        reasons.append(f"referenced by {len(inbound_refs)} groups")
    if cycle_detected:
        reasons.append("cycle detected in reference graph")

    return {
        "level": level,
        "score": score,
        "cycle_detected": cycle_detected,
        "reasons": reasons,
    }


def _security_group_reference_details(
    items: list[dict],
    target_group_id: str,
) -> dict:
    by_id, adjacency, reverse = _security_group_reference_graph(items)
    direct_refs = adjacency.get(target_group_id, set())
    reachable_refs = _security_group_reachable_refs(adjacency, target_group_id)
    depth, cycle_detected = _security_group_reference_depth(adjacency, target_group_id)
    inbound_refs = reverse.get(target_group_id, set())

    return {
        "remote_group_fanout": {
            "direct_group_count": len(direct_refs),
            "transitive_group_count": len(reachable_refs),
            "groups": _security_group_reference_items(direct_refs, by_id),
        },
        "reference_graph_depth": depth,
        "referenced_by": _security_group_referenced_by_items(inbound_refs, by_id),
        "control_plane_complexity": _security_group_control_plane_complexity(
            direct_refs,
            reachable_refs,
            inbound_refs,
            depth,
            cycle_detected,
        ),
    }


def _load_security_group_graph_groups(conn, target_group_id: str) -> list:
    groups_by_id: dict[str, object] = {}
    try:
        for item in conn.network.security_groups():
            group_id = getattr(item, "id", None) or ""
            if not group_id:
                continue
            try:
                groups_by_id[group_id] = conn.network.get_security_group(group_id)
            except Exception:
                groups_by_id[group_id] = item
    except Exception:
        pass

    if target_group_id and target_group_id not in groups_by_id:
        groups_by_id[target_group_id] = conn.network.get_security_group(target_group_id)

    return list(groups_by_id.values())


def get_security_groups(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    """Return Neutron security groups with audit annotations."""
    conn = openstack_ops._conn(auth=auth)
    project_names = _project_names(conn)
    attachment_map = _security_group_attachment_map(conn)
    items = [
        _summarize_security_group(group, project_names, attachment_map)
        for group in conn.network.security_groups()
    ]
    for item in items:
        item.pop("rules", None)
        item.pop("attachments", None)
    items.sort(
        key=lambda item: (
            {"critical": 0, "high": 1, "medium": 2, "clean": 3}.get(item["audit"]["severity"], 4),
            -int(item["audit"]["score"]),
            item["project_name"],
            item["name"],
            item["id"],
        )
    )
    return items


def get_security_group_detail(group_id: str, auth: openstack_ops.OpenStackAuth | None) -> dict:
    """Return one Neutron security group with rules and attachment detail."""
    conn = openstack_ops._conn(auth=auth)
    group = conn.network.get_security_group(group_id)
    groups = _load_security_group_graph_groups(conn, group_id)
    project_names = _project_names(conn)
    attachment_map = _security_group_attachment_map(conn)
    item = _summarize_security_group(group, project_names, attachment_map)
    graph_items = [_summarize_security_group(entry, project_names, attachment_map) for entry in groups]
    item.update(_security_group_reference_details(graph_items, group_id))
    item["attachments"].sort(
        key=lambda row: (
            row.get("device_owner", ""),
            row.get("device_id", ""),
            row.get("port_id", ""),
        )
    )
    return item


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


def _safe_get_router(conn, router_id: str):
    try:
        return conn.network.get_router(router_id)
    except Exception:
        return None


def _router_name(conn, router_id: str, cache: dict[str, str]) -> str:
    if not router_id:
        return ""
    if router_id in cache:
        return cache[router_id]
    router = _safe_get_router(conn, router_id)
    if router is None:
        cache[router_id] = router_id
    else:
        router_data = router.to_dict() if hasattr(router, "to_dict") else {}
        cache[router_id] = getattr(router, "name", None) or router_data.get("name") or router_id
    return cache[router_id]


def _network_router_map(conn) -> dict[str, dict]:
    result: dict[str, dict] = {}
    try:
        for port in conn.network.ports():
            port_data = port.to_dict() if hasattr(port, "to_dict") else {}
            owner = getattr(port, "device_owner", None) or port_data.get("device_owner") or ""
            if not _is_router_interface_owner(owner):
                continue
            network_id = getattr(port, "network_id", None) or port_data.get("network_id") or ""
            router_id = getattr(port, "device_id", None) or port_data.get("device_id") or ""
            if network_id and router_id and network_id not in result:
                result[network_id] = {"id": router_id, "name": ""}
    except Exception:
        return result
    return result


def _ports_with_fixed_ip(conn, ip_address: str) -> list:
    if not ip_address:
        return []
    try:
        ports = list(conn.network.ports(fixed_ips=f"ip_address={ip_address}"))
        if ports:
            return ports
    except Exception:
        pass
    try:
        result = []
        for port in conn.network.ports():
            port_data = port.to_dict() if hasattr(port, "to_dict") else {}
            fixed_ips = list(getattr(port, "fixed_ips", None) or port_data.get("fixed_ips") or [])
            if any(item.get("ip_address") == ip_address for item in fixed_ips):
                result.append(port)
        return result
    except Exception:
        return []


def _count_network_consumers(conn, network_id: str) -> dict[str, int]:
    counts = {"port_count": 0, "instance_count": 0, "load_balancer_count": 0}
    if not network_id:
        return counts
    seen_instances: set[str] = set()
    seen_lbs: set[str] = set()
    try:
        for port in conn.network.ports(network_id=network_id):
            port_data = port.to_dict() if hasattr(port, "to_dict") else {}
            owner = getattr(port, "device_owner", None) or port_data.get("device_owner") or ""
            device_id = getattr(port, "device_id", None) or port_data.get("device_id") or ""
            counts["port_count"] += 1
            if owner.startswith("compute:") and device_id:
                seen_instances.add(device_id)
            if "loadbalancer" in owner.lower() or "octavia" in owner.lower():
                seen_lbs.add(device_id or (getattr(port, "id", None) or port_data.get("id") or ""))
        counts["instance_count"] = len(seen_instances)
        counts["load_balancer_count"] = len({item for item in seen_lbs if item})
    except Exception:
        pass
    return counts


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
    network_name_cache: dict[str, str] = {}
    subnet_cache: dict[str, dict] = {}
    router_cache = _network_router_map(conn)
    router_name_cache: dict[str, str] = {}
    vip_port: dict[str, object] = {
        "id": vip_port_id,
        "name": "",
        "status": "",
        "network_id": "",
        "network_name": "",
        "subnet_id": "",
        "subnet_name": "",
        "subnet_cidr": "",
        "ip_address": vip_address,
        "mac_address": "",
        "device_owner": "",
        "device_id": "",
        "project_id": project_id,
        "admin_state_up": False,
        "router_id": "",
        "router_name": "",
    }
    if vip_port_id:
        try:
            port = conn.network.get_port(vip_port_id)
            port_data = port.to_dict() if hasattr(port, "to_dict") else {}
            fixed_ips = list(getattr(port, "fixed_ips", None) or port_data.get("fixed_ips") or [])
            primary_ip = next((item.get("ip_address", "") for item in fixed_ips if item.get("ip_address")), "") or vip_address
            primary_subnet = next((item.get("subnet_id", "") for item in fixed_ips if item.get("subnet_id")), "") or ""
            subnet_detail = _lookup_subnet_detail(conn, primary_subnet, subnet_cache) if primary_subnet else {}
            network_id = getattr(port, "network_id", None) or port_data.get("network_id") or ""
            router_meta = router_cache.get(network_id, {})
            router_id = router_meta.get("id", "") if isinstance(router_meta, dict) else ""
            vip_port = {
                "id": getattr(port, "id", None) or port_data.get("id") or vip_port_id,
                "name": getattr(port, "name", None) or port_data.get("name") or "",
                "status": getattr(port, "status", None) or port_data.get("status") or "",
                "network_id": network_id,
                "network_name": _lookup_network_name(conn, network_id, network_name_cache),
                "subnet_id": primary_subnet,
                "subnet_name": subnet_detail.get("name", ""),
                "subnet_cidr": subnet_detail.get("cidr", ""),
                "ip_address": primary_ip,
                "mac_address": getattr(port, "mac_address", None) or port_data.get("mac_address") or "",
                "device_owner": getattr(port, "device_owner", None) or port_data.get("device_owner") or "",
                "device_id": getattr(port, "device_id", None) or port_data.get("device_id") or "",
                "project_id": getattr(port, "project_id", None) or port_data.get("project_id") or project_id,
                "admin_state_up": bool(getattr(port, "is_admin_state_up", port_data.get("admin_state_up", False))),
                "router_id": router_id,
                "router_name": _router_name(conn, router_id, router_name_cache) if router_id else "",
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
            "members": [],
        })
        member_rows = []
        try:
            for member in conn.load_balancer.members(pool.id):
                member_data = member.to_dict() if hasattr(member, "to_dict") else {}
                address = getattr(member, "address", None) or member_data.get("address") or ""
                protocol_port = getattr(member, "protocol_port", None) or member_data.get("protocol_port")
                operating_status = getattr(member, "operating_status", None) or member_data.get("operating_status") or "UNKNOWN"
                instance_name = ""
                compute_host = ""
                matched_port_id = ""
                for matched_port in _ports_with_fixed_ip(conn, address):
                    matched_port_data = matched_port.to_dict() if hasattr(matched_port, "to_dict") else {}
                    matched_port_id = getattr(matched_port, "id", None) or matched_port_data.get("id") or ""
                    owner = getattr(matched_port, "device_owner", None) or matched_port_data.get("device_owner") or ""
                    device_id = getattr(matched_port, "device_id", None) or matched_port_data.get("device_id") or ""
                    if owner.startswith("compute:") and device_id:
                        try:
                            server = conn.compute.get_server(device_id)
                            server_data = server.to_dict() if hasattr(server, "to_dict") else {}
                            instance_name = getattr(server, "name", None) or server_data.get("name") or device_id
                            compute_host = openstack_ops._server_host(server) or ""
                        except Exception:
                            instance_name = device_id
                        break
                member_rows.append({
                    "id": getattr(member, "id", None) or member_data.get("id") or "",
                    "name": getattr(member, "name", None) or member_data.get("name") or "",
                    "address": address,
                    "protocol_port": protocol_port,
                    "operating_status": operating_status,
                    "instance_id": device_id if instance_name or compute_host else "",
                    "instance_name": instance_name,
                    "compute_host": compute_host,
                    "port_id": matched_port_id,
                })
        except Exception:
            member_rows = []
        pools[-1]["members"] = member_rows

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
        "joins": {
            "vip_network_name": vip_port.get("network_name", ""),
            "vip_subnet_name": vip_port.get("subnet_name", ""),
            "vip_subnet_cidr": vip_port.get("subnet_cidr", ""),
            "router_id": vip_port.get("router_id", ""),
            "router_name": vip_port.get("router_name", ""),
            "member_count_online": sum(
                1 for pool in pools for member in (pool.get("members") or [])
                if str(member.get("operating_status") or "").upper() in {"ONLINE", "ACTIVE", "UP"}
            ),
            "member_count_total": sum(len(pool.get("members") or []) for pool in pools),
        },
    }


def get_router_detail(router_id: str, auth: openstack_ops.OpenStackAuth | None) -> dict:
    """Return connected subnet, route, and gateway detail for one router."""
    conn = openstack_ops._conn(auth=auth)
    router = conn.network.get_router(router_id)
    router_data = router.to_dict() if hasattr(router, "to_dict") else {}
    network_name_cache: dict[str, str] = {}
    subnet_cache: dict[str, dict] = {}
    subnet_consumers: list[dict] = []

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

    for entry in connected_subnets:
        counts = _count_network_consumers(conn, entry.get("network_id", ""))
        subnet_consumers.append({
            "network_id": entry.get("network_id", ""),
            "network_name": entry.get("network_name", ""),
            "subnet_id": entry.get("subnet_id", ""),
            "subnet_name": entry.get("subnet_name", ""),
            "cidr": entry.get("cidr", ""),
            "instance_count": counts["instance_count"],
            "load_balancer_count": counts["load_balancer_count"],
            "port_count": counts["port_count"],
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
        "subnet_consumers": subnet_consumers,
        "routes": routes,
        "joins": {
            "routed_instance_count": sum(item.get("instance_count", 0) for item in subnet_consumers),
            "routed_load_balancer_count": sum(item.get("load_balancer_count", 0) for item in subnet_consumers),
            "attached_network_count": len({item.get("network_id", "") for item in subnet_consumers if item.get("network_id", "")}),
            "external_ip_count": len(external_fixed_ips),
        },
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


def _safe_call(obj, name: str, *args, **kwargs):
    fn = getattr(obj, name, None)
    if not callable(fn):
        return None
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def _volume_project_id(volume) -> str:
    return (
        getattr(volume, "os-vol-tenant-attr:tenant_id", None)
        or getattr(volume, "project_id", None)
        or ""
    )


def _volume_type_detail(conn, volume_type_ref: object) -> dict:
    if not volume_type_ref:
        return {}
    volume_proxy = conn.volume
    type_obj = None
    candidates: list[str] = []
    if isinstance(volume_type_ref, dict):
        candidates.extend([
            str(volume_type_ref.get("id") or "").strip(),
            str(volume_type_ref.get("name") or "").strip(),
        ])
    else:
        candidates.append(str(volume_type_ref).strip())
    for candidate in [value for value in candidates if value]:
        type_obj = _safe_call(volume_proxy, "get_type", candidate)
        if type_obj is not None:
            break
    if type_obj is None:
        return {}

    type_data = type_obj.to_dict() if hasattr(type_obj, "to_dict") else {}
    qos_id = (
        type_data.get("qos_specs_id")
        or type_data.get("qos_specs")
        or getattr(type_obj, "qos_specs_id", None)
        or getattr(type_obj, "qos_specs", None)
        or ""
    )
    qos_policy = {}
    qos_candidates = [qos_id] if isinstance(qos_id, str) else []
    if isinstance(qos_id, dict):
        qos_policy = {str(key): value for key, value in qos_id.items()}
    for candidate in [value for value in qos_candidates if value]:
        qos_obj = _safe_call(volume_proxy, "get_qos_specs", candidate)
        if qos_obj is None:
            continue
        qos_data = qos_obj.to_dict() if hasattr(qos_obj, "to_dict") else {}
        specs = qos_data.get("specs") or qos_data.get("qos_specs") or {}
        if isinstance(specs, dict):
            qos_policy = {str(key): value for key, value in specs.items()}
        if qos_data.get("name") and "name" not in qos_policy:
            qos_policy["name"] = qos_data.get("name")
        break

    extra_specs = type_data.get("extra_specs") or getattr(type_obj, "extra_specs", None) or {}
    if not isinstance(extra_specs, dict):
        extra_specs = {}
    return {
        "id": getattr(type_obj, "id", None) or type_data.get("id") or "",
        "name": getattr(type_obj, "name", None) or type_data.get("name") or "",
        "description": getattr(type_obj, "description", None) or type_data.get("description") or "",
        "is_public": bool(getattr(type_obj, "is_public", type_data.get("is_public", False))),
        "extra_specs": {str(key): value for key, value in extra_specs.items()},
        "qos_policy": qos_policy,
    }


def _attachment_detail(conn, attachment: dict) -> dict:
    server_id = attachment.get("server_id", "") or ""
    instance_name = ""
    if server_id:
        try:
            server = conn.compute.get_server(server_id)
            if server is not None:
                instance_name = getattr(server, "name", None) or ""
        except Exception:
            instance_name = ""
    return {
        "server_id": server_id,
        "server_name": instance_name,
        "attachment_id": attachment.get("attachment_id", "") or attachment.get("id", "") or "",
        "host_name": attachment.get("host_name", "") or "",
        "device": attachment.get("device", "") or "",
        "attached_at": attachment.get("attached_at", "") or "",
    }


def get_volume_detail(volume_id: str, auth: openstack_ops.OpenStackAuth | None) -> dict:
    """Return detailed Cinder volume information for the drawer view."""
    conn = openstack_ops._conn(auth=auth)
    volume = conn.volume.get_volume(volume_id)
    volume_data = volume.to_dict() if hasattr(volume, "to_dict") else {}
    attachments = [
        _attachment_detail(conn, dict(item))
        for item in (getattr(volume, "attachments", None) or volume_data.get("attachments") or [])
        if isinstance(item, dict)
    ]
    metadata = volume_data.get("metadata") or getattr(volume, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    type_detail = _volume_type_detail(conn, getattr(volume, "volume_type", None) or volume_data.get("volume_type") or "")

    snapshot_count = 0
    backup_count = 0
    try:
        snapshot_count = sum(
            1 for item in conn.volume.snapshots(details=False)
            if getattr(item, "volume_id", None) == volume_id
        )
    except Exception:
        snapshot_count = 0
    try:
        backup_count = sum(
            1 for item in conn.volume.backups(details=False)
            if getattr(item, "volume_id", None) == volume_id
        )
    except Exception:
        backup_count = 0

    return {
        "id": getattr(volume, "id", None) or volume_data.get("id") or volume_id,
        "name": getattr(volume, "name", None) or volume_data.get("name") or "(no name)",
        "status": getattr(volume, "status", None) or volume_data.get("status") or "UNKNOWN",
        "size_gb": getattr(volume, "size", None) or volume_data.get("size") or 0,
        "description": getattr(volume, "description", None) or volume_data.get("description") or "",
        "volume_type": getattr(volume, "volume_type", None) or volume_data.get("volume_type") or "",
        "project_id": _volume_project_id(volume),
        "bootable": coerce_bool(getattr(volume, "is_bootable", volume_data.get("bootable", False))),
        "encrypted": coerce_bool(getattr(volume, "encrypted", volume_data.get("encrypted", False))),
        "multiattach": coerce_bool(getattr(volume, "is_multiattach", volume_data.get("multiattach", False))),
        "availability_zone": getattr(volume, "availability_zone", None) or volume_data.get("availability_zone") or "",
        "created_at": getattr(volume, "created_at", None) or volume_data.get("created_at") or "",
        "updated_at": getattr(volume, "updated_at", None) or volume_data.get("updated_at") or "",
        "source_volid": volume_data.get("source_volid") or getattr(volume, "source_volid", None) or "",
        "snapshot_id": volume_data.get("snapshot_id") or getattr(volume, "snapshot_id", None) or "",
        "replication_status": volume_data.get("replication_status") or getattr(volume, "replication_status", None) or "",
        "consistencygroup_id": volume_data.get("consistencygroup_id") or getattr(volume, "consistencygroup_id", None) or "",
        "os-vol-host-attr:host": volume_data.get("os-vol-host-attr:host") or "",
        "attachments": attachments,
        "metadata": {str(key): value for key, value in metadata.items()},
        "volume_type_detail": type_detail,
        "snapshot_count": snapshot_count,
        "backup_count": backup_count,
    }


def get_swift_containers(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    """Return Swift containers visible to the configured credential."""
    conn = openstack_ops._conn(auth=auth)
    result = []
    for container in conn.object_store.containers():
        data = container.to_dict() if hasattr(container, "to_dict") else {}
        result.append({
            "name": getattr(container, "name", None) or data.get("name") or "",
            "object_count": (
                getattr(container, "object_count", None)
                or data.get("object_count")
                or data.get("x_container_object_count")
                or 0
            ),
            "bytes_used": (
                getattr(container, "bytes_used", None)
                or data.get("bytes_used")
                or data.get("x_container_bytes_used")
                or 0
            ),
            "is_public": bool(
                data.get("read_ACL")
                or data.get("x_container_read")
                or data.get("public")
            ),
        })
    result.sort(key=lambda item: item["name"])
    return result
