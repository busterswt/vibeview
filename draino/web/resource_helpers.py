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


def _current_membership_project_ids(conn) -> set[str]:
    project_ids: set[str] = set()
    current_project_id = getattr(conn, "current_project_id", None)
    if current_project_id:
        project_ids.add(current_project_id)
    user_id = getattr(conn, "current_user_id", None)
    if not user_id:
        return project_ids
    try:
        assignments = conn.list_role_assignments(filters={"user": user_id})
    except Exception:
        return project_ids
    for assignment in assignments:
        scope = getattr(assignment, "scope", None)
        project = None
        if isinstance(scope, dict):
            project = scope.get("project")
        elif scope is not None:
            project = getattr(scope, "project", None)
        if isinstance(project, dict):
            project_id = project.get("id")
        else:
            project_id = getattr(project, "id", None) if project is not None else None
        if project_id:
            project_ids.add(project_id)
    return project_ids


def _obj_dict(obj) -> dict:
    if hasattr(obj, "to_dict"):
        try:
            return obj.to_dict()
        except Exception:
            return {}
    if isinstance(obj, dict):
        return obj
    return {}


def _project_entry(project, names: dict[str, str]) -> dict:
    data = _obj_dict(project)
    project_id = getattr(project, "id", None) or data.get("id") or ""
    name = getattr(project, "name", None) or data.get("name") or names.get(project_id) or project_id or "unknown"
    return {
        "project_id": project_id,
        "project_name": name,
        "description": getattr(project, "description", None) or data.get("description") or "",
        "domain_id": getattr(project, "domain_id", None) or data.get("domain_id") or "",
        "enabled": coerce_bool(getattr(project, "is_enabled", None) if getattr(project, "is_enabled", None) is not None else data.get("enabled", True)),
    }


def _safe_quota_limit(entry: object) -> int | None:
    if isinstance(entry, (int, float, str)):
        try:
            return int(entry)
        except (TypeError, ValueError):
            return None
    if isinstance(entry, dict):
        value = entry.get("limit", entry.get("quota", entry.get("max")))
    else:
        value = getattr(entry, "limit", None)
        if value is None:
            value = getattr(entry, "quota", None)
        if value is None:
            value = getattr(entry, "max", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_quota_usage(entry: object) -> int | None:
    if isinstance(entry, (int, float, str)):
        try:
            return int(entry)
        except (TypeError, ValueError):
            return None
    if isinstance(entry, dict):
        value = entry.get("in_use", entry.get("used", entry.get("usage")))
    else:
        value = getattr(entry, "in_use", None)
        if value is None:
            value = getattr(entry, "used", None)
        if value is None:
            value = getattr(entry, "usage", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _quota_usage_map(raw: object) -> dict[str, object]:
    data = _obj_dict(raw)
    usage = data.get("usage")
    if isinstance(usage, dict):
        return usage
    usage = getattr(raw, "usage", None) if raw is not None else None
    if isinstance(usage, dict):
        return usage
    return {}


_QUOTA_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "network": ("network", "networks", "Network", "Networks"),
    "subnet": ("subnet", "subnets", "Subnet", "Subnets"),
    "subnet_pool": ("subnet_pool", "subnet_pools", "Subnet Pool", "Subnet Pools"),
    "port": ("port", "ports", "Port", "Ports"),
    "router": ("router", "routers", "Router", "Routers"),
    "floatingip": ("floatingip", "floating_ip", "floating_ips", "Floating IP", "Floating IPs"),
    "rbac_policy": ("rbac_policy", "rbac_policies", "RBAC Policy", "RBAC Policies"),
    "security_group": ("security_group", "security_groups", "Security Group", "Security Groups"),
    "security_group_rule": ("security_group_rule", "security_group_rules", "Security Group Rule", "Security Group Rules"),
    "trunk": ("trunk", "trunks", "Trunk", "Trunks"),
    "endpoint_group": ("endpoint_group", "endpoint_groups", "Endpoint Group", "Endpoint Groups"),
    "vpnservice": ("vpnservice", "vpnservices", "VPN Service", "VPN Services"),
    "ipsec_site_connection": ("ipsec_site_connection", "ipsec_site_connections", "IPsec Site Connection", "IPsec Site Connections"),
    "ipsecpolicy": ("ipsecpolicy", "ipsecpolicies", "IPsec Policy", "IPsec Policies"),
    "ikepolicy": ("ikepolicy", "ikepolicies", "IKE Policy", "IKE Policies"),
    "load_balancer": ("load_balancer", "load_balancers", "loadbalancer", "Load Balancer", "Load Balancers"),
    "listener": ("listener", "listeners", "Listener", "Listeners"),
    "l7_policy": ("l7_policy", "l7_policies", "L7 Policy", "L7 Policies"),
    "pool": ("pool", "pools", "Pool", "Pools"),
    "member": ("member", "members", "Member", "Members"),
    "health_monitor": ("health_monitor", "health_monitors", "healthmonitor", "Health Monitor", "Health Monitors"),
    "instances": ("instances",),
    "cores": ("cores",),
    "ram": ("ram",),
    "server_groups": ("server_groups",),
    "server_group_members": ("server_group_members",),
    "volumes": ("volumes",),
    "gigabytes": ("gigabytes",),
    "snapshots": ("snapshots",),
    "backups": ("backups",),
    "backup_gigabytes": ("backup_gigabytes",),
}


def _quota_entry(raw: object, data: dict, key: str) -> object:
    aliases = _QUOTA_KEY_ALIASES.get(key, (key,))
    for alias in aliases:
        entry = data.get(alias)
        if entry is None and raw is not None:
            entry = getattr(raw, alias, None)
        if entry is not None:
            return entry
    return None


def _unwrap_quota_result(raw: object) -> object:
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict):
            return first
    return raw


def _normalize_quota_section(raw: object, keys: list[str]) -> dict[str, dict[str, int | None]]:
    raw = _unwrap_quota_result(raw)
    data = _obj_dict(raw)
    usage_map = _quota_usage_map(raw)
    normalized: dict[str, dict[str, int | None]] = {}
    for key in keys:
        entry = _quota_entry(raw, data, key)
        if entry is None:
            continue
        usage_value = _safe_quota_usage(usage_map.get(key))
        if isinstance(entry, dict):
            used = _safe_quota_usage(entry)
            normalized[key] = {
                "limit": _safe_quota_limit(entry),
                "used": used if used is not None else usage_value,
            }
        elif isinstance(entry, (int, float, str)):
            try:
                limit = int(entry)
            except (TypeError, ValueError):
                limit = None
            normalized[key] = {
                "limit": limit,
                "used": _safe_quota_usage(usage_map.get(key)),
            }
        else:
            used = _safe_quota_usage(entry)
            normalized[key] = {
                "limit": _safe_quota_limit(entry),
                "used": used if used is not None else usage_value,
            }
    return normalized


def _backfill_compute_quota_usage(project_id: str, auth: openstack_ops.OpenStackAuth | None, quotas: dict) -> dict:
    compute = quotas.get("compute")
    if not isinstance(compute, dict):
        return quotas
    needed = [
        key for key in ("instances", "cores", "ram")
        if isinstance(compute.get(key), dict) and compute[key].get("used") is None
    ]
    if not needed:
        return quotas
    instances = [item for item in get_project_instances(auth) if item.get("project_id") == project_id]
    usage = {
        "instances": len(instances),
        "cores": sum(int(item.get("vcpus") or 0) for item in instances),
        "ram": sum(int(item.get("ram_mb") or 0) for item in instances),
    }
    quotas["compute"] = {
        **compute,
        **{
            key: {
                **compute[key],
                "used": usage[key],
            }
            for key in needed
        },
    }
    return quotas


_EDITABLE_PROJECT_QUOTA_KEYS: dict[str, set[str]] = {
    "compute": {"instances", "cores", "ram", "server_groups", "server_group_members"},
    "network": {
        "network",
        "subnet",
        "subnet_pool",
        "port",
        "router",
        "floatingip",
        "rbac_policy",
        "security_group",
        "security_group_rule",
        "trunk",
        "endpoint_group",
        "vpnservice",
        "ipsec_site_connection",
        "ipsecpolicy",
        "ikepolicy",
        "load_balancer",
        "listener",
        "l7_policy",
        "pool",
        "member",
        "health_monitor",
    },
    "block_storage": {"volumes", "gigabytes", "snapshots", "backups", "backup_gigabytes"},
}


def _quota_payload_key(section: str, resource: str) -> str:
    return _QUOTA_KEY_ALIASES.get(resource, (resource,))[0]


def _raise_response_error(response, context: str) -> None:
    ok = getattr(response, "ok", None)
    if ok is True:
        return
    status_code = getattr(response, "status_code", None)
    if status_code is not None and int(status_code) < 400:
        return
    message = getattr(response, "text", None) or getattr(response, "reason", None) or ""
    detail = f"{context} failed"
    if status_code is not None:
        detail += f" with HTTP {status_code}"
    if message:
        detail += f": {message}"
    raise RuntimeError(detail)


def _session_put(conn, service_type: str, path: str, body: dict) -> None:
    session = getattr(conn, "session", None)
    if session is None:
        raise RuntimeError(f"OpenStack session unavailable for {service_type} quota update")
    endpoint = conn.endpoint_for(service_type)
    response = session.put(
        f"{str(endpoint).rstrip('/')}/{path.lstrip('/')}",
        json=body,
        endpoint_filter={"service_type": service_type},
    )
    _raise_response_error(response, f"{service_type} quota update")


def _update_compute_quota(conn, project_id: str, resource: str, limit: int) -> None:
    payload = {resource: limit}
    methods = [
        getattr(conn.compute, "update_quota_set", None),
        getattr(conn.compute, "set_quota_set", None),
    ]
    for method in methods:
        if not callable(method):
            continue
        try:
            method(project_id, **payload)
            return
        except TypeError:
            try:
                method(project_id, payload)
                return
            except TypeError:
                continue
    _session_put(conn, "compute", f"os-quota-sets/{project_id}", {"quota_set": payload})


def _update_network_quota(conn, project_id: str, resource: str, limit: int) -> None:
    payload = {_quota_payload_key("network", resource): limit}
    methods = [
        getattr(conn.network, "update_quota", None),
        getattr(conn.network, "set_quota", None),
    ]
    for method in methods:
        if not callable(method):
            continue
        try:
            method(project_id, **payload)
            return
        except TypeError:
            try:
                method(project_id, payload)
                return
            except TypeError:
                continue
    for path in (f"quotas/{project_id}", f"quotas/{project_id}.json"):
        try:
            _session_put(conn, "network", path, {"quota": payload})
            return
        except Exception:
            continue
    raise RuntimeError("Neutron quota update failed")


def _update_block_storage_quota(conn, project_id: str, resource: str, limit: int) -> None:
    payload = {_quota_payload_key("block_storage", resource): limit}
    methods = [
        getattr(conn.block_storage, "update_quota_set", None),
        getattr(conn.block_storage, "set_quota_set", None),
    ]
    for method in methods:
        if not callable(method):
            continue
        try:
            method(project_id, **payload)
            return
        except TypeError:
            try:
                method(project_id, payload)
                return
            except TypeError:
                continue
    _session_put(conn, "block-storage", f"os-quota-sets/{project_id}", {"quota_set": payload})


def update_project_quota_limit(
    project_id: str,
    section: str,
    resource: str,
    limit: int | str,
    auth: openstack_ops.OpenStackAuth | None,
) -> dict:
    normalized_section = str(section or "").strip().lower()
    normalized_resource = str(resource or "").strip().lower()
    if normalized_section not in _EDITABLE_PROJECT_QUOTA_KEYS:
        raise ValueError(f"Unsupported quota section: {section}")
    if normalized_resource not in _EDITABLE_PROJECT_QUOTA_KEYS[normalized_section]:
        raise ValueError(f"Unsupported quota resource for {normalized_section}: {resource}")
    try:
        normalized_limit = int(str(limit).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Quota limit must be an integer for {normalized_section}.{normalized_resource}") from exc

    conn = openstack_ops._conn(auth=auth)
    if normalized_section == "compute":
        _update_compute_quota(conn, project_id, normalized_resource, normalized_limit)
    elif normalized_section == "network":
        _update_network_quota(conn, project_id, normalized_resource, normalized_limit)
    else:
        _update_block_storage_quota(conn, project_id, normalized_resource, normalized_limit)
    return get_project_inventory(project_id, auth, section="quota")


def get_project_quota_summary(project_id: str, auth: openstack_ops.OpenStackAuth | None) -> dict:
    conn = openstack_ops._conn(auth=auth)
    summary = {
        "compute": {},
        "network": {},
        "block_storage": {},
    }
    try:
        try:
            quota = conn.compute.get_quota_set(project_id, usage=True)
        except TypeError:
            quota = conn.compute.get_quota_set(project_id)
        except Exception:
            quota = conn.compute.get_quota_set(project_id)
        summary["compute"] = _normalize_quota_section(
            quota,
            ["instances", "cores", "ram", "server_groups", "server_group_members"],
        )
    except Exception:
        pass
    try:
        try:
            quota = conn.network.get_quota(project_id, details=True)
        except TypeError:
            quota = conn.network.get_quota(project_id)
        except Exception:
            quota = conn.network.get_quota(project_id)
        summary["network"] = _normalize_quota_section(
            quota,
            [
                "network",
                "subnet",
                "subnet_pool",
                "port",
                "router",
                "floatingip",
                "rbac_policy",
                "security_group",
                "security_group_rule",
                "trunk",
                "endpoint_group",
                "vpnservice",
                "ipsec_site_connection",
                "ipsecpolicy",
                "ikepolicy",
                "load_balancer",
                "listener",
                "l7_policy",
                "pool",
                "member",
                "health_monitor",
            ],
        )
    except Exception:
        pass
    try:
        quota = conn.block_storage.get_quota_set(project_id, usage=True)
        summary["block_storage"] = _normalize_quota_section(
            quota,
            ["volumes", "gigabytes", "snapshots", "backups", "backup_gigabytes"],
        )
    except Exception:
        pass
    return summary


def get_project_instances(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    conn = openstack_ops._conn(auth=auth)
    project_names = _project_names(conn)
    result: list[dict] = []
    for server in openstack_ops._iter_servers(conn, all_projects=True, details=True):
        if openstack_ops._server_deleted(server):
            continue
        data = _obj_dict(server)
        project_id = openstack_ops._server_project_id(server) or "unknown"
        flavor_ref = getattr(server, "flavor", None) or data.get("flavor") or {}
        flavor = openstack_ops._resolve_flavor_data(conn, flavor_ref)
        addresses = data.get("addresses") or {}
        networks: list[dict] = []
        floating_ips: list[str] = []
        fixed_ips: list[str] = []
        for network_name, entries in addresses.items():
            ip_list: list[str] = []
            for item in entries or []:
                address = item.get("addr") or ""
                if not address:
                    continue
                if str(item.get("OS-EXT-IPS:type") or "").lower() == "floating":
                    floating_ips.append(address)
                else:
                    fixed_ips.append(address)
                    ip_list.append(address)
            networks.append({
                "name": network_name,
                "addresses": ip_list,
            })
        result.append({
            "id": getattr(server, "id", None) or data.get("id") or "",
            "name": getattr(server, "name", None) or data.get("name") or "",
            "status": str(getattr(server, "status", None) or data.get("status") or "").upper() or "UNKNOWN",
            "project_id": project_id,
            "project_name": project_names.get(project_id) or project_id,
            "compute_host": openstack_ops._server_host(server) or "",
            "availability_zone": getattr(server, "availability_zone", None) or data.get("OS-EXT-AZ:availability_zone") or "",
            "created_at": getattr(server, "created_at", None) or data.get("created_at") or "",
            "updated_at": getattr(server, "updated_at", None) or data.get("updated_at") or "",
            "is_volume_backed": not bool(getattr(server, "image", None) or data.get("image")),
            "flavor": flavor,
            "vcpus": flavor.get("vcpus"),
            "ram_mb": flavor.get("ram_mb"),
            "disk_gb": flavor.get("disk_gb"),
            "networks": networks,
            "fixed_ips": sorted(dict.fromkeys(fixed_ips)),
            "floating_ips": sorted(dict.fromkeys(floating_ips)),
        })
    result.sort(key=lambda item: (item["project_name"], item["name"], item["id"]))
    return result


def _search_score(query: str, fields: list[object]) -> int:
    needle = str(query or "").strip().lower()
    if not needle:
        return 0
    best = 0
    for raw in fields:
        value = str(raw or "").strip().lower()
        if not value:
            continue
        if value == needle:
            best = max(best, 120)
        elif value.startswith(needle):
            best = max(best, 95)
        elif any(part.startswith(needle) for part in value.replace("/", " ").replace(":", " ").replace(".", " ").replace("-", " ").replace("_", " ").split()):
            best = max(best, 82)
        elif needle in value:
            best = max(best, 70)
    return best


def search_resources(auth: openstack_ops.OpenStackAuth | None, query: str, limit: int = 20) -> list[dict]:
    needle = str(query or "").strip()
    if not needle:
        return []
    results: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, item_id: str, label: str, subtext: str, match: list[object], **extra) -> None:
        score = _search_score(needle, match)
        if score <= 0:
            return
        key = (kind, item_id or label)
        if key in seen:
            return
        seen.add(key)
        results.append({
            "kind": kind,
            "id": item_id,
            "label": label,
            "subtext": subtext,
            "score": score,
            **extra,
        })

    for item in get_projects(auth, search=needle):
        add(
            "project",
            item.get("project_id", ""),
            item.get("project_name") or item.get("project_id") or "Project",
            item.get("project_id", ""),
            [item.get("project_name"), item.get("project_id"), item.get("description")],
            project_id=item.get("project_id", ""),
        )

    for item in get_project_instances(auth):
        add(
            "instance",
            item.get("id", ""),
            item.get("name") or item.get("id") or "Instance",
            f"{item.get('project_name') or item.get('project_id') or 'Project'} • {item.get('compute_host') or item.get('status') or 'UNKNOWN'}",
            [
                item.get("name"),
                item.get("id"),
                item.get("project_name"),
                item.get("project_id"),
                item.get("compute_host"),
                item.get("status"),
                *list(item.get("fixed_ips") or []),
                *list(item.get("floating_ips") or []),
            ],
            project_id=item.get("project_id", ""),
            compute_host=item.get("compute_host", ""),
        )

    for item in get_networks(auth):
        add(
            "network",
            item.get("id", ""),
            item.get("name") or item.get("id") or "Network",
            f"{item.get('network_type') or 'Network'} • {item.get('status') or 'UNKNOWN'}",
            [item.get("name"), item.get("id"), item.get("network_type"), item.get("status"), item.get("project_id")],
            project_id=item.get("project_id", ""),
        )

    for item in get_routers(auth):
        add(
            "router",
            item.get("id", ""),
            item.get("name") or item.get("id") or "Router",
            f"{item.get('status') or 'UNKNOWN'} • {item.get('external_network_name') or item.get('external_network_id') or 'no external network'}",
            [item.get("name"), item.get("id"), item.get("status"), item.get("external_network_name"), item.get("external_network_id"), item.get("project_id")],
            project_id=item.get("project_id", ""),
        )

    for item in get_ports(auth):
        add(
            "port",
            item.get("id", ""),
            item.get("name") or item.get("id") or "Port",
            f"{item.get('network_name') or item.get('network_id') or 'Port'} • {item.get('attached_name') or item.get('device_owner') or item.get('status') or 'UNKNOWN'}",
            [
                item.get("name"),
                item.get("id"),
                item.get("network_name"),
                item.get("network_id"),
                item.get("attached_name"),
                item.get("attached_id"),
                item.get("device_owner"),
                item.get("project_id"),
                item.get("mac_address"),
                *list(item.get("fixed_ip_addresses") or []),
                *[ip.get("address") for ip in list(item.get("floating_ips") or [])],
            ],
            project_id=item.get("project_id", ""),
            attached_kind=item.get("attached_kind", ""),
            attached_id=item.get("attached_id", ""),
            compute_host=item.get("compute_host", ""),
        )

    for item in get_floating_ips(auth):
        add(
            "floatingip",
            item.get("id", ""),
            item.get("floating_ip_address") or item.get("id") or "Floating IP",
            f"{item.get('project_name') or item.get('project_id') or 'Project'} • {item.get('instance_name') or item.get('fixed_ip_address') or item.get('status') or 'Floating IP'}",
            [
                item.get("id"),
                item.get("floating_ip_address"),
                item.get("fixed_ip_address"),
                item.get("instance_name"),
                item.get("instance_id"),
                item.get("port_id"),
                item.get("status"),
                item.get("project_id"),
            ],
            project_id=item.get("project_id", ""),
            instance_id=item.get("instance_id", ""),
            compute_host=item.get("compute_host", ""),
            port_id=item.get("port_id", ""),
        )

    for item in get_load_balancers(auth):
        add(
            "loadbalancer",
            item.get("id", ""),
            item.get("name") or item.get("id") or "Load Balancer",
            f"{item.get('vip_address') or 'no VIP'} • {item.get('operating_status') or 'UNKNOWN'}",
            [item.get("name"), item.get("id"), item.get("vip_address"), item.get("floating_ip"), item.get("project_id"), item.get("operating_status"), item.get("provisioning_status")],
            project_id=item.get("project_id", ""),
        )

    for item in get_security_groups(auth):
        add(
            "securitygroup",
            item.get("id", ""),
            item.get("name") or item.get("id") or "Security Group",
            f"{item.get('project_name') or item.get('project_id') or 'Security Group'} • {item.get('audit', {}).get('severity') or 'unknown'}",
            [item.get("name"), item.get("id"), item.get("project_name"), item.get("project_id"), item.get("description"), item.get("audit", {}).get("severity")],
            project_id=item.get("project_id", ""),
        )

    volumes, _all_projects = get_volumes(auth)
    for item in volumes:
        add(
            "volume",
            item.get("id", ""),
            item.get("name") or item.get("id") or "Volume",
            f"{item.get('volume_type') or 'Volume'} • {item.get('status') or 'UNKNOWN'} • {item.get('size_gb') or 0} GB",
            [item.get("name"), item.get("id"), item.get("volume_type"), item.get("status"), item.get("backend_name"), item.get("project_id")],
            project_id=item.get("project_id", ""),
        )

    kind_rank = {
        "node": 0,
        "instance": 1,
        "project": 2,
        "network": 3,
        "router": 4,
        "port": 5,
        "floatingip": 6,
        "loadbalancer": 7,
        "securitygroup": 8,
        "volume": 9,
    }
    results.sort(key=lambda item: (-int(item.get("score", 0)), kind_rank.get(str(item.get("kind")), 50), str(item.get("label") or "")))
    return results[: max(1, int(limit or 20))]


def get_floating_ips(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    conn = openstack_ops._conn(auth=auth)
    project_names = _project_names(conn)
    network_name_cache: dict[str, str] = {}
    port_cache: dict[str, dict] = {}
    server_cache: dict[str, dict[str, str]] = {}
    result: list[dict] = []
    try:
        ips = conn.network.ips()
    except Exception:
        return result
    for ip in ips:
        data = _obj_dict(ip)
        project_id = getattr(ip, "project_id", None) or data.get("project_id") or data.get("tenant_id") or ""
        floating_network_id = getattr(ip, "floating_network_id", None) or data.get("floating_network_id") or ""
        port_id = getattr(ip, "port_id", None) or data.get("port_id") or ""
        instance_id = ""
        instance_name = ""
        compute_host = ""
        if port_id:
            if port_id not in port_cache:
                try:
                    port = conn.network.get_port(port_id)
                    port_cache[port_id] = _obj_dict(port)
                except Exception:
                    port_cache[port_id] = {}
            port_data = port_cache.get(port_id, {})
            device_owner = str(port_data.get("device_owner") or "")
            device_id = str(port_data.get("device_id") or "")
            if device_owner.startswith("compute:") and device_id:
                server_brief = _lookup_server_brief(conn, device_id, server_cache)
                instance_id = device_id
                instance_name = server_brief.get("name", "") or device_id
                compute_host = server_brief.get("compute_host", "")
        result.append({
            "id": getattr(ip, "id", None) or data.get("id") or "",
            "floating_ip_address": getattr(ip, "floating_ip_address", None) or data.get("floating_ip_address") or "",
            "fixed_ip_address": getattr(ip, "fixed_ip_address", None) or data.get("fixed_ip_address") or "",
            "status": getattr(ip, "status", None) or data.get("status") or "",
            "project_id": project_id,
            "project_name": project_names.get(project_id) or project_id or "unknown",
            "floating_network_id": floating_network_id,
            "floating_network_name": _lookup_network_name(conn, floating_network_id, network_name_cache),
            "port_id": port_id,
            "router_id": getattr(ip, "router_id", None) or data.get("router_id") or "",
            "instance_id": instance_id,
            "instance_name": instance_name,
            "compute_host": compute_host,
            "description": getattr(ip, "description", None) or data.get("description") or "",
        })
    result.sort(key=lambda item: (item["project_name"], item["floating_ip_address"], item["id"]))
    return result


def _floating_ip_map_by_port(conn) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    try:
        for ip in conn.network.ips():
            data = _obj_dict(ip)
            port_id = getattr(ip, "port_id", None) or data.get("port_id") or ""
            if not port_id:
                continue
            result[port_id].append({
                "id": getattr(ip, "id", None) or data.get("id") or "",
                "address": getattr(ip, "floating_ip_address", None) or data.get("floating_ip_address") or "",
                "status": getattr(ip, "status", None) or data.get("status") or "",
            })
    except Exception:
        return {}
    return dict(result)


def _port_attached_resource(conn, device_owner: str, device_id: str, server_cache: dict[str, dict[str, str]], router_name_cache: dict[str, str]) -> dict[str, str]:
    owner = str(device_owner or "")
    if owner.startswith("compute:") and device_id:
        server = _lookup_server_brief(conn, device_id, server_cache)
        return {
            "kind": "instance",
            "id": device_id,
            "name": server.get("name", "") or device_id,
            "compute_host": server.get("compute_host", ""),
        }
    if "router" in owner and device_id:
        return {
            "kind": "router",
            "id": device_id,
            "name": _router_name(conn, device_id, router_name_cache) or device_id,
            "compute_host": "",
        }
    if owner.startswith("network:") and device_id:
        return {
            "kind": "network-service",
            "id": device_id,
            "name": device_id,
            "compute_host": "",
        }
    if owner.startswith("loadbalancer:") and device_id:
        return {
            "kind": "load-balancer",
            "id": device_id,
            "name": device_id,
            "compute_host": "",
        }
    return {
        "kind": "",
        "id": device_id or "",
        "name": device_id or "",
        "compute_host": "",
    }


def _port_record(conn, port, network_name_cache: dict[str, str], subnet_cache: dict[str, dict], router_cache: dict[str, dict], router_name_cache: dict[str, str], server_cache: dict[str, dict[str, str]], floating_ip_map: dict[str, list[dict[str, str]]]) -> dict:
    port_data = port.to_dict() if hasattr(port, "to_dict") else {}
    port_id = getattr(port, "id", None) or port_data.get("id") or ""
    network_id = getattr(port, "network_id", None) or port_data.get("network_id") or ""
    project_id = getattr(port, "project_id", None) or port_data.get("project_id") or ""
    device_owner = getattr(port, "device_owner", None) or port_data.get("device_owner") or ""
    device_id = getattr(port, "device_id", None) or port_data.get("device_id") or ""
    fixed_ips = list(getattr(port, "fixed_ips", None) or port_data.get("fixed_ips") or [])
    subnets = []
    fixed_ip_addresses = []
    for item in fixed_ips:
        subnet_id = item.get("subnet_id", "") or ""
        subnet_detail = _lookup_subnet_detail(conn, subnet_id, subnet_cache) if subnet_id else {}
        ip_address = item.get("ip_address", "") or ""
        if ip_address:
            fixed_ip_addresses.append(ip_address)
        subnets.append({
            "id": subnet_id,
            "name": subnet_detail.get("name", ""),
            "cidr": subnet_detail.get("cidr", ""),
            "ip_address": ip_address,
        })
    attached = _port_attached_resource(conn, device_owner, device_id, server_cache, router_name_cache)
    router_meta = router_cache.get(network_id, {}) if isinstance(router_cache.get(network_id, {}), dict) else {}
    connected_router_id = router_meta.get("id", "") or ""
    return {
        "id": port_id,
        "name": getattr(port, "name", None) or port_data.get("name") or "",
        "status": getattr(port, "status", None) or port_data.get("status") or "UNKNOWN",
        "admin_state": "up" if bool(getattr(port, "is_admin_state_up", port_data.get("admin_state_up", False))) else "down",
        "project_id": project_id,
        "network_id": network_id,
        "network_name": _lookup_network_name(conn, network_id, network_name_cache),
        "device_owner": device_owner,
        "device_id": device_id,
        "mac_address": getattr(port, "mac_address", None) or port_data.get("mac_address") or "",
        "fixed_ips": fixed_ips,
        "fixed_ip_addresses": fixed_ip_addresses,
        "subnets": subnets,
        "security_group_ids": _port_security_groups(port),
        "floating_ips": floating_ip_map.get(port_id, []),
        "attached_kind": attached.get("kind", ""),
        "attached_id": attached.get("id", ""),
        "attached_name": attached.get("name", ""),
        "compute_host": attached.get("compute_host", ""),
        "connected_router_id": connected_router_id,
        "connected_router_name": _router_name(conn, connected_router_id, router_name_cache) if connected_router_id else "",
    }


def get_ports(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    conn = openstack_ops._conn(auth=auth)
    network_name_cache: dict[str, str] = {}
    subnet_cache: dict[str, dict] = {}
    router_cache = _network_router_map(conn)
    router_name_cache: dict[str, str] = {}
    server_cache: dict[str, dict[str, str]] = {}
    floating_ip_map = _floating_ip_map_by_port(conn)
    result: list[dict] = []
    try:
        for port in conn.network.ports():
            result.append(_port_record(conn, port, network_name_cache, subnet_cache, router_cache, router_name_cache, server_cache, floating_ip_map))
    except Exception:
        return result
    result.sort(key=lambda item: (item.get("network_name", ""), item.get("attached_name", ""), item.get("name", ""), item.get("id", "")))
    return result


def get_port_detail(port_id: str, auth: openstack_ops.OpenStackAuth | None) -> dict:
    conn = openstack_ops._conn(auth=auth)
    network_name_cache: dict[str, str] = {}
    subnet_cache: dict[str, dict] = {}
    router_cache = _network_router_map(conn)
    router_name_cache: dict[str, str] = {}
    server_cache: dict[str, dict[str, str]] = {}
    floating_ip_map = _floating_ip_map_by_port(conn)
    port = conn.network.get_port(port_id)
    item = _port_record(conn, port, network_name_cache, subnet_cache, router_cache, router_name_cache, server_cache, floating_ip_map)
    security_groups = []
    for group_id in item.get("security_group_ids", []):
        try:
            group = conn.network.get_security_group(group_id)
            group_data = group.to_dict() if hasattr(group, "to_dict") else {}
            security_groups.append({
                "id": getattr(group, "id", None) or group_data.get("id") or group_id,
                "name": getattr(group, "name", None) or group_data.get("name") or group_id,
            })
        except Exception:
            security_groups.append({"id": group_id, "name": group_id})
    item["security_groups"] = security_groups
    return item


def get_projects(auth: openstack_ops.OpenStackAuth | None, search: str = "") -> list[dict]:
    conn = openstack_ops._conn(auth=auth)
    project_names = _project_names(conn)
    membership_project_ids = _current_membership_project_ids(conn)
    search_text = str(search or "").strip().lower()
    items: list[dict] = []
    try:
        for project in conn.identity.projects():
            entry = _project_entry(project, project_names)
            project_id = entry["project_id"]
            if not project_id:
                continue
            if search_text:
                haystack = f'{entry["project_name"]} {project_id} {entry["description"]}'.lower()
                if search_text not in haystack:
                    continue
            elif membership_project_ids and project_id not in membership_project_ids:
                continue
            items.append(entry)
    except Exception:
        pass
    items.sort(key=lambda item: (item.get("project_name") or item.get("project_id") or "").lower())
    return items


def _project_summary_header(conn, project_id: str, project_names: dict[str, str]) -> dict:
    try:
        raw_project = conn.identity.get_project(project_id)
    except Exception:
        raw_project = None
    return _project_entry(raw_project, project_names) if raw_project is not None else {
        "project_id": project_id,
        "project_name": project_names.get(project_id) or project_id,
        "description": "",
        "domain_id": "",
        "enabled": True,
    }


def get_project_inventory(project_id: str, auth: openstack_ops.OpenStackAuth | None, section: str = "instances") -> dict:
    conn = openstack_ops._conn(auth=auth)
    project_names = _project_names(conn)
    membership_project_ids = _current_membership_project_ids(conn)
    summary = _project_summary_header(conn, project_id, project_names)
    summary["member_visible"] = (not membership_project_ids or project_id in membership_project_ids)
    payload = {"summary": summary}
    normalized = str(section or "instances").strip().lower()
    if normalized == "overview":
        quotas = _backfill_compute_quota_usage(project_id, auth, get_project_quota_summary(project_id, auth))
        placement = next((item for item in openstack_ops.get_project_vm_distribution(auth) if item.get("project_id") == project_id), None)
        payload["overview"] = {
            "quotas": quotas,
            "placement": placement or None,
        }
        return payload
    if normalized == "instances":
        payload["instances"] = [item for item in get_project_instances(auth) if item.get("project_id") == project_id]
        return payload
    if normalized == "networking":
        payload["networks"] = [item for item in get_networks(auth) if item.get("project_id") == project_id]
        payload["routers"] = [item for item in get_routers(auth) if item.get("project_id") == project_id]
        payload["ports"] = [item for item in get_ports(auth) if item.get("project_id") == project_id]
        payload["floating_ips"] = [item for item in get_floating_ips(auth) if item.get("project_id") == project_id]
        payload["load_balancers"] = [item for item in get_load_balancers(auth) if item.get("project_id") == project_id]
        return payload
    if normalized == "storage":
        volumes, all_projects = get_volumes(auth)
        payload["volumes"] = [item for item in volumes if item.get("project_id") == project_id]
        payload["all_projects"] = all_projects
        return payload
    if normalized == "security":
        payload["security_groups"] = [item for item in get_security_groups(auth) if item.get("project_id") == project_id]
        return payload
    if normalized == "quota":
        payload["quotas"] = _backfill_compute_quota_usage(project_id, auth, get_project_quota_summary(project_id, auth))
        placement = next((item for item in openstack_ops.get_project_vm_distribution(auth) if item.get("project_id") == project_id), None)
        payload["placement"] = placement or None
        return payload
    return payload


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
        volume_data = volume.to_dict() if hasattr(volume, "to_dict") else {}
        host_value = volume_data.get("os-vol-host-attr:host") or ""
        host_detail = _volume_host_detail(host_value)
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
            "multiattach": coerce_bool(getattr(volume, "is_multiattach", volume_data.get("multiattach", False))),
            "backend_host": host_detail["service_host"],
            "backend_name": host_detail["backend"],
            "backend_pool": host_detail["pool"],
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


def _volume_host_detail(host_value: str) -> dict[str, str]:
    text = str(host_value or "").strip()
    if not text:
        return {"raw": "", "service_host": "", "backend": "", "pool": ""}
    service_host, backend_pool = text, ""
    if "@" in text:
        service_host, backend_pool = text.split("@", 1)
    backend, pool = backend_pool, ""
    if "#" in backend_pool:
        backend, pool = backend_pool.split("#", 1)
    return {
        "raw": text,
        "service_host": service_host,
        "backend": backend,
        "pool": pool,
    }


def _volume_type_backend_name(type_detail: dict) -> str:
    specs = type_detail.get("extra_specs") or {}
    if not isinstance(specs, dict):
        return ""
    return str(
        specs.get("volume_backend_name")
        or specs.get("capabilities:volume_backend_name")
        or ""
    ).strip()


def _list_volume_types(conn) -> list[dict]:
    items = []
    try:
        for item in conn.volume.types():
            detail = _volume_type_detail(conn, getattr(item, "id", None) or getattr(item, "name", None) or "")
            if not detail:
                continue
            detail["backend_name"] = _volume_type_backend_name(detail)
            items.append(detail)
    except Exception:
        return []
    items.sort(key=lambda item: (item.get("name", ""), item.get("id", "")))
    return items


def _volume_snapshot_row(snapshot) -> dict:
    data = snapshot.to_dict() if hasattr(snapshot, "to_dict") else {}
    project_id = (
        getattr(snapshot, "project_id", None)
        or getattr(snapshot, "os-extended-snapshot-attributes:project_id", None)
        or data.get("project_id")
        or data.get("os-extended-snapshot-attributes:project_id")
        or ""
    )
    return {
        "id": getattr(snapshot, "id", None) or data.get("id") or "",
        "name": getattr(snapshot, "name", None) or data.get("name") or "(no name)",
        "status": getattr(snapshot, "status", None) or data.get("status") or "UNKNOWN",
        "size_gb": getattr(snapshot, "size", None) or data.get("size") or 0,
        "volume_id": getattr(snapshot, "volume_id", None) or data.get("volume_id") or "",
        "project_id": project_id,
        "created_at": getattr(snapshot, "created_at", None) or data.get("created_at") or "",
        "description": getattr(snapshot, "description", None) or data.get("description") or "",
    }


def _volume_backup_row(backup) -> dict:
    data = backup.to_dict() if hasattr(backup, "to_dict") else {}
    return {
        "id": getattr(backup, "id", None) or data.get("id") or "",
        "name": getattr(backup, "name", None) or data.get("name") or "(no name)",
        "status": getattr(backup, "status", None) or data.get("status") or "UNKNOWN",
        "size_gb": getattr(backup, "size", None) or data.get("size") or 0,
        "volume_id": getattr(backup, "volume_id", None) or data.get("volume_id") or "",
        "project_id": getattr(backup, "project_id", None) or data.get("project_id") or "",
        "created_at": getattr(backup, "created_at", None) or data.get("created_at") or "",
        "is_incremental": coerce_bool(getattr(backup, "is_incremental", data.get("is_incremental", False))),
        "container": getattr(backup, "container", None) or data.get("container") or "",
        "description": getattr(backup, "description", None) or data.get("description") or "",
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
    type_detail["backend_name"] = _volume_type_backend_name(type_detail)
    host_detail = _volume_host_detail(volume_data.get("os-vol-host-attr:host") or "")
    volume_types = _list_volume_types(conn)

    snapshots: list[dict] = []
    backups: list[dict] = []
    try:
        snapshots = [
            _volume_snapshot_row(item)
            for item in conn.volume.snapshots(details=False)
            if getattr(item, "volume_id", None) == volume_id
        ]
    except Exception:
        snapshots = []
    try:
        backups = [
            _volume_backup_row(item)
            for item in conn.volume.backups(details=False)
            if getattr(item, "volume_id", None) == volume_id
        ]
    except Exception:
        backups = []
    snapshots.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    backups.sort(key=lambda item: item.get("created_at", ""), reverse=True)

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
        "backend_host": host_detail["service_host"],
        "backend_name": host_detail["backend"],
        "backend_pool": host_detail["pool"],
        "attachments": attachments,
        "metadata": {str(key): value for key, value in metadata.items()},
        "volume_type_detail": type_detail,
        "available_volume_types": volume_types,
        "snapshot_count": len(snapshots),
        "backup_count": len(backups),
        "snapshots": snapshots,
        "backups": backups,
    }


def get_volume_snapshots(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    """Return Cinder volume snapshots."""
    conn = openstack_ops._conn(auth=auth)
    items: list[dict] = []
    try:
        for snapshot in conn.volume.snapshots(details=False):
            items.append(_volume_snapshot_row(snapshot))
    except Exception:
        return []
    items.sort(key=lambda item: (item.get("status", ""), item.get("name", ""), item.get("id", "")))
    return items


def get_volume_backups(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    """Return Cinder volume backups."""
    conn = openstack_ops._conn(auth=auth)
    items: list[dict] = []
    try:
        for backup in conn.volume.backups(details=False):
            items.append(_volume_backup_row(backup))
    except Exception:
        return []
    items.sort(key=lambda item: (item.get("status", ""), item.get("name", ""), item.get("id", "")))
    return items


def retype_volume(
    volume_id: str,
    target_type: str,
    migration_policy: str = "on-demand",
    auth: openstack_ops.OpenStackAuth | None = None,
) -> dict:
    """Request a Cinder volume retype."""
    conn = openstack_ops._conn(auth=auth)
    target = str(target_type or "").strip()
    if not target:
        raise ValueError("target_type is required")
    policy = str(migration_policy or "on-demand").strip().lower()
    if policy not in {"on-demand", "never"}:
        raise ValueError("migration_policy must be 'on-demand' or 'never'")

    volume_proxy = getattr(conn, "volume", None)
    block_proxy = getattr(conn, "block_storage", None)

    for proxy in [volume_proxy, block_proxy]:
        if proxy is None:
            continue
        fn = getattr(proxy, "retype_volume", None)
        if callable(fn):
            fn(volume_id, target, migration_policy=policy)
            return {
                "volume_id": volume_id,
                "target_type": target,
                "migration_policy": policy,
                "status": "requested",
            }

    action_body = {"os-retype": {"new_type": target, "migration_policy": policy}}
    if block_proxy is not None:
        post = getattr(block_proxy, "post", None)
        if callable(post):
            post(f"/volumes/{volume_id}/action", json=action_body)
            return {
                "volume_id": volume_id,
                "target_type": target,
                "migration_policy": policy,
                "status": "requested",
            }

    session = getattr(conn, "session", None)
    endpoint_for = getattr(conn, "endpoint_for", None)
    if session is not None and callable(endpoint_for):
        endpoint = str(endpoint_for("block-storage") or "").rstrip("/")
        if endpoint:
            response = session.post(
                f"{endpoint}/volumes/{volume_id}/action",
                json=action_body,
                microversion=None,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return {
                "volume_id": volume_id,
                "target_type": target,
                "migration_policy": policy,
                "status": "requested",
            }

    raise RuntimeError("Cinder retype is not available from the current SDK connection")


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
