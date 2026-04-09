"""Helpers for the Stress view and Heat-based test orchestration."""
from __future__ import annotations

import json
import random
import time
from collections import deque
from datetime import datetime, timezone
from ipaddress import ip_network
from statistics import mean
from typing import Any

from ..operations import openstack_ops

STRESS_STACK_PREFIX = "vibe-stress-"
STRESS_DEFAULT_KEYPAIR_NAME = "vibe-stress-key"
STRESS_DEFAULT_SECURITY_GROUP_NAME = "vibe-stress-secgroup"
STRESS_TRACE_LIMIT = 24
_STRESS_ACTION_TRACE: deque[dict[str, Any]] = deque(maxlen=STRESS_TRACE_LIMIT)

STRESS_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "key": "full-host-spread",
        "label": "Full Host Spread",
        "description": "Best-effort one VM per compute host for scheduler and placement validation.",
        "icon": "🧭",
        "default_vm_count": None,
        "min_vm_count": 1,
        "max_vm_count": 200,
        "supports_auto_cidr": True,
        "supports_auto_keypair": True,
    },
    {
        "key": "burst",
        "label": "Burst",
        "description": "High-count VM launch test against shared network plumbing.",
        "icon": "⚡",
        "default_vm_count": 20,
        "min_vm_count": 1,
        "max_vm_count": 200,
        "supports_auto_cidr": True,
        "supports_auto_keypair": True,
    },
    {
        "key": "small-distribution",
        "label": "Small Distribution",
        "description": "Quick scheduler sanity test with a small spread set.",
        "icon": "🧪",
        "default_vm_count": 5,
        "min_vm_count": 1,
        "max_vm_count": 50,
        "supports_auto_cidr": True,
        "supports_auto_keypair": True,
    },
)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def record_stress_action(action: str, stage: str, *, status: str = "info", message: str = "", detail: str = "") -> None:
    _STRESS_ACTION_TRACE.appendleft({
        "at": _now_utc().strftime("%H:%M:%S"),
        "action": action,
        "stage": stage,
        "status": status,
        "message": message,
        "detail": detail,
    })


def get_stress_action_trace() -> list[dict[str, Any]]:
    return list(_STRESS_ACTION_TRACE)


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _elapsed_seconds(start: Any, end: Any) -> int | None:
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    if start_dt is None:
        return None
    if end_dt is None:
        end_dt = _now_utc()
    elapsed = int(round((end_dt - start_dt).total_seconds()))
    return max(0, elapsed)


def _format_seconds(value: int | None) -> str:
    if value is None:
        return "—"
    if value < 60:
        return f"{value}s"
    minutes, seconds = divmod(value, 60)
    return f"{minutes}m {seconds:02d}s"


def _percent(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(value / total * 100.0, 1)


def _server_host(server) -> str:
    return openstack_ops._server_host(server) or ""


def _server_addresses(server) -> list[str]:
    addresses = []
    data = server.to_dict() if hasattr(server, "to_dict") else {}
    for entries in (getattr(server, "addresses", None) or data.get("addresses") or {}).values():
        for item in entries or []:
            addr = item.get("addr") if isinstance(item, dict) else getattr(item, "addr", None)
            if addr:
                addresses.append(str(addr))
    return addresses


def _is_external_network(network) -> bool:
    if _coerce_bool(getattr(network, "is_router_external", None)):
        return True
    if _coerce_bool(getattr(network, "router_external", None)):
        return True
    data = network.to_dict() if hasattr(network, "to_dict") else {}
    for key in ("router:external", "is_router_external", "router_external"):
        if _coerce_bool(data.get(key)):
            return True
    return False


def _list_external_networks(auth: openstack_ops.OpenStackAuth | None) -> list[dict[str, Any]]:
    conn = openstack_ops._conn(auth=auth)
    items: list[dict[str, Any]] = []
    for network in conn.network.networks():
        if not _is_external_network(network):
            continue
        items.append({
            "id": getattr(network, "id", "") or "",
            "name": getattr(network, "name", "") or getattr(network, "id", "") or "external",
        })
    items.sort(key=lambda item: item["name"].lower())
    return items


def list_stress_images(auth: openstack_ops.OpenStackAuth | None) -> list[dict[str, Any]]:
    conn = openstack_ops._conn(auth=auth)
    items: list[dict[str, Any]] = []
    image_api = getattr(conn, "image", None)
    if image_api is None:
        return items
    for image in image_api.images():
        status = str(getattr(image, "status", "") or "").lower()
        if status and status != "active":
            continue
        visibility = getattr(image, "visibility", None)
        items.append({
            "id": getattr(image, "id", "") or "",
            "name": getattr(image, "name", "") or getattr(image, "id", "") or "Unnamed image",
            "status": status or "unknown",
            "visibility": visibility or "unknown",
            "min_disk_gb": _coerce_int(getattr(image, "min_disk", None)) or 0,
            "min_ram_mb": _coerce_int(getattr(image, "min_ram", None)) or 0,
            "disk_format": getattr(image, "disk_format", None) or "",
            "os_distro": (getattr(image, "properties", None) or {}).get("os_distro", ""),
        })
    items.sort(key=lambda item: item["name"].lower())
    return items


def list_stress_flavors(auth: openstack_ops.OpenStackAuth | None) -> list[dict[str, Any]]:
    conn = openstack_ops._conn(auth=auth)
    items: list[dict[str, Any]] = []
    for flavor in conn.compute.flavors():
        items.append({
            "id": getattr(flavor, "id", "") or "",
            "name": getattr(flavor, "name", "") or getattr(flavor, "id", "") or "Unnamed flavor",
            "vcpus": _coerce_int(getattr(flavor, "vcpus", None)) or 0,
            "ram_mb": _coerce_int(getattr(flavor, "ram", None)) or 0,
            "disk_gb": _coerce_int(getattr(flavor, "disk", None)) or 0,
            "ephemeral_gb": _coerce_int(getattr(flavor, "ephemeral", None)) or 0,
            "swap_mb": _coerce_int(getattr(flavor, "swap", None)) or 0,
            "is_public": bool(getattr(flavor, "is_public", True)),
        })
    items.sort(key=lambda item: (item["vcpus"], item["ram_mb"], item["disk_gb"], item["name"].lower()))
    return items


def list_stress_keypairs(auth: openstack_ops.OpenStackAuth | None) -> list[dict[str, Any]]:
    conn = openstack_ops._conn(auth=auth)
    items: list[dict[str, Any]] = []
    for keypair in conn.compute.keypairs():
        items.append({
            "name": getattr(keypair, "name", "") or "",
            "fingerprint": getattr(keypair, "fingerprint", "") or "",
            "type": getattr(keypair, "type", "") or "",
        })
    items.sort(key=lambda item: item["name"].lower())
    return items


def _find_active_stress_stack_obj(conn) -> Any | None:
    orchestration = getattr(conn, "orchestration", None)
    if orchestration is None:
        return None
    candidates: list[Any] = []
    for stack in orchestration.stacks():
        name = getattr(stack, "stack_name", None) or getattr(stack, "name", None) or ""
        if not name.startswith(STRESS_STACK_PREFIX):
            continue
        status = str(getattr(stack, "status", "") or "").upper()
        if status == "DELETE_COMPLETE":
            continue
        candidates.append(stack)
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            str(getattr(item, "updated_time", None) or getattr(item, "updated_at", None) or ""),
            str(getattr(item, "creation_time", None) or getattr(item, "created_at", None) or ""),
            getattr(item, "stack_name", None) or getattr(item, "name", None) or "",
        ),
        reverse=True,
    )
    return candidates[0]


def _stack_summary(stack: Any) -> dict[str, Any]:
    return {
        "id": getattr(stack, "id", "") or "",
        "stack_name": getattr(stack, "stack_name", None) or getattr(stack, "name", None) or "",
        "status": getattr(stack, "status", "") or "UNKNOWN",
        "created_at": str(getattr(stack, "creation_time", None) or getattr(stack, "created_at", None) or ""),
        "updated_at": str(getattr(stack, "updated_time", None) or getattr(stack, "updated_at", None) or ""),
        "description": getattr(stack, "description", "") or "",
        "parameters": getattr(stack, "parameters", None) or {},
        "outputs": getattr(stack, "outputs", None) or [],
    }


def find_active_stress_stack(auth: openstack_ops.OpenStackAuth | None) -> dict[str, Any] | None:
    conn = openstack_ops._conn(auth=auth)
    stack = _find_active_stress_stack_obj(conn)
    return _stack_summary(stack) if stack is not None else None


def suggest_stress_cidr() -> str:
    pools = (
        (10, random.randint(64, 223), random.randint(0, 255)),
        (172, random.randint(16, 31), random.randint(0, 255)),
        (192, 168, random.randint(0, 255)),
    )
    first, second, third = random.choice(pools)
    return f"{first}.{second}.{third}.0/24"


def build_stress_environment(
    *,
    auth: openstack_ops.OpenStackAuth | None,
    compute_count: int,
) -> dict[str, Any]:
    catalog = build_stress_catalog(auth=auth, compute_count=compute_count)
    images = list_stress_images(auth)
    flavors = list_stress_flavors(auth)
    keypairs = list_stress_keypairs(auth)
    external_networks = _list_external_networks(auth)
    default_image_id = images[0]["id"] if images else ""
    compatible_flavors = flavors
    if images:
        image = images[0]
        compatible_flavors = [
            flavor for flavor in flavors
            if flavor["disk_gb"] >= image["min_disk_gb"] and flavor["ram_mb"] >= image["min_ram_mb"]
        ] or flavors
    default_flavor_id = compatible_flavors[0]["id"] if compatible_flavors else (flavors[0]["id"] if flavors else "")
    default_keypair_name = keypairs[0]["name"] if keypairs else STRESS_DEFAULT_KEYPAIR_NAME
    return {
        "images": images,
        "flavors": flavors,
        "keypairs": keypairs,
        "external_networks": external_networks,
        "guardrail": catalog["guardrail"],
        "defaults": {
            "image_id": default_image_id,
            "flavor_id": default_flavor_id,
            "keypair_mode": "existing" if keypairs else "auto",
            "keypair_name": default_keypair_name,
            "generated_keypair_name": STRESS_DEFAULT_KEYPAIR_NAME,
            "cidr_mode": "auto",
            "cidr": suggest_stress_cidr(),
            "external_network_id": external_networks[0]["id"] if external_networks else "",
        },
        "limits": {
            "compute_count": compute_count,
        },
    }


def build_stress_options(
    *,
    auth: openstack_ops.OpenStackAuth | None,
    compute_count: int,
    profile_key: str,
) -> dict[str, Any]:
    catalog = build_stress_catalog(auth=auth, compute_count=compute_count)
    environment = build_stress_environment(auth=auth, compute_count=compute_count)
    profiles = list(catalog["profiles"])
    selected_profile = next((profile for profile in profiles if profile["key"] == profile_key), None)
    if selected_profile is None:
        raise ValueError(f"Unknown stress profile: {profile_key}")

    return {
        "selected_profile": selected_profile,
        "images": environment["images"],
        "flavors": environment["flavors"],
        "keypairs": environment["keypairs"],
        "external_networks": environment["external_networks"],
        "guardrail": environment["guardrail"],
        "defaults": {
            "profile": selected_profile["key"],
            "vm_count": selected_profile["default_vm_count"],
            "image_id": environment["defaults"]["image_id"],
            "flavor_id": environment["defaults"]["flavor_id"],
            "keypair_mode": environment["defaults"]["keypair_mode"],
            "keypair_name": environment["defaults"]["keypair_name"],
            "generated_keypair_name": environment["defaults"]["generated_keypair_name"],
            "cidr_mode": environment["defaults"]["cidr_mode"],
            "cidr": environment["defaults"]["cidr"],
            "external_network_id": environment["defaults"]["external_network_id"],
        },
        "limits": environment["limits"],
    }


def build_stress_catalog(
    *,
    auth: openstack_ops.OpenStackAuth | None,
    compute_count: int,
) -> dict[str, Any]:
    active_stack = find_active_stress_stack(auth)
    profiles: list[dict[str, Any]] = []
    for profile in STRESS_PROFILES:
        default_vm_count = profile["default_vm_count"]
        if profile["key"] == "full-host-spread":
            default_vm_count = max(1, compute_count or 1)
        elif default_vm_count is None:
            default_vm_count = max(1, min(compute_count or 1, 20))
        profile_item = dict(profile)
        profile_item["default_vm_count"] = default_vm_count
        profiles.append(profile_item)
    return {
        "profiles": profiles,
        "guardrail": {
            "active": active_stack is not None,
            "stack": active_stack,
            "message": (
                "Delete the existing stack before launching a new stress test."
                if active_stack is not None else
                "No active stress stack detected."
            ),
            "stack_prefix": STRESS_STACK_PREFIX,
        },
        "limits": {
            "compute_count": compute_count,
        },
        "trace": get_stress_action_trace(),
    }


def _stress_test_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def _profile_by_key(profile_key: str) -> dict[str, Any]:
    for profile in STRESS_PROFILES:
        if profile["key"] == profile_key:
            return dict(profile)
    raise ValueError(f"Unknown stress profile: {profile_key}")


def _validate_cidr(cidr: str) -> str:
    try:
        return str(ip_network(cidr, strict=False))
    except Exception as exc:
        raise ValueError("CIDR must be a valid network range") from exc


def _build_stress_template(config: dict[str, Any]) -> dict[str, Any]:
    vm_count = int(config["vm_count"])
    resources: dict[str, Any] = {
        "stress_net": {
            "type": "OS::Neutron::Net",
            "properties": {
                "name": {"get_param": "network_name"},
            },
        },
        "stress_subnet": {
            "type": "OS::Neutron::Subnet",
            "properties": {
                "name": {"get_param": "subnet_name"},
                "network_id": {"get_resource": "stress_net"},
                "cidr": {"get_param": "network_cidr"},
                "ip_version": 4,
                "enable_dhcp": True,
                "dns_nameservers": ["1.1.1.1", "8.8.8.8"],
            },
        },
        "stress_router": {
            "type": "OS::Neutron::Router",
            "properties": {
                "name": {"get_param": "router_name"},
                "external_gateway_info": {
                    "network": {"get_param": "external_network_id"},
                },
            },
        },
        "stress_router_interface": {
            "type": "OS::Neutron::RouterInterface",
            "properties": {
                "router_id": {"get_resource": "stress_router"},
                "subnet": {"get_resource": "stress_subnet"},
            },
        },
        "stress_secgroup": {
            "type": "OS::Neutron::SecurityGroup",
            "properties": {
                "name": {"get_param": "security_group_name"},
                "description": "VibeView stress test security group",
                "rules": [
                    {"direction": "ingress", "ethertype": "IPv4", "protocol": "icmp"},
                    {"direction": "ingress", "ethertype": "IPv4", "protocol": "tcp", "port_range_min": 22, "port_range_max": 22},
                ],
            },
        },
    }
    if config["keypair_mode"] == "auto":
        resources["stress_keypair"] = {
            "type": "OS::Nova::KeyPair",
            "properties": {
                "name": {"get_param": "key_name"},
                "save_private_key": False,
            },
        }
    for index in range(1, vm_count + 1):
        suffix = f"{index:02d}"
        port_name = f"stress_port_{suffix}"
        server_name = f"stress_vm_{suffix}"
        resources[port_name] = {
            "type": "OS::Neutron::Port",
            "properties": {
                "name": {
                    "str_replace": {
                        "template": f"$PREFIX-port-{suffix}",
                        "params": {"$PREFIX": {"get_param": "name_prefix"}},
                    },
                },
                "network_id": {"get_resource": "stress_net"},
                "security_groups": [{"get_resource": "stress_secgroup"}],
            },
        }
        resources[server_name] = {
            "type": "OS::Nova::Server",
            "properties": {
                "name": {
                    "str_replace": {
                        "template": f"$PREFIX-vm-{suffix}",
                        "params": {"$PREFIX": {"get_param": "name_prefix"}},
                    },
                },
                "image": {"get_param": "image"},
                "flavor": {"get_param": "flavor"},
                "key_name": {"get_param": "key_name"},
                "networks": [{"port": {"get_resource": port_name}}],
                "metadata": {
                    "vibeview:stress-test": "true",
                    "vibeview:test-id": {"get_param": "test_id"},
                    "vibeview:stack": {"get_param": "stack_name"},
                    "vibeview:profile": {"get_param": "profile"},
                },
            },
        }
    outputs = {
        "test_id": {"value": {"get_param": "test_id"}},
        "stack_name": {"value": {"get_param": "stack_name"}},
        "profile": {"value": {"get_param": "profile"}},
        "requested_vms": {"value": {"get_param": "vm_count"}},
    }
    parameters = {
        "test_id": {"type": "string"},
        "stack_name": {"type": "string"},
        "profile": {"type": "string"},
        "name_prefix": {"type": "string"},
        "image": {"type": "string"},
        "flavor": {"type": "string"},
        "key_name": {"type": "string"},
        "key_mode": {"type": "string"},
        "vm_count": {"type": "number"},
        "network_cidr": {"type": "string"},
        "network_name": {"type": "string"},
        "subnet_name": {"type": "string"},
        "router_name": {"type": "string"},
        "security_group_name": {"type": "string"},
        "external_network_id": {"type": "string"},
    }
    return {
        "heat_template_version": "2018-08-31",
        "description": "VibeView Heat stress test stack",
        "parameters": parameters,
        "resources": resources,
        "outputs": outputs,
    }


def _build_stack_payload(*, options: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    profile_key = str(payload.get("profile") or options["defaults"]["profile"] or "").strip()
    profile = _profile_by_key(profile_key)
    image_id = str(payload.get("image_id") or options["defaults"]["image_id"] or "").strip()
    flavor_id = str(payload.get("flavor_id") or options["defaults"]["flavor_id"] or "").strip()
    if not image_id or not any(item["id"] == image_id for item in options["images"]):
        raise ValueError("Select a valid image")
    if not flavor_id or not any(item["id"] == flavor_id for item in options["flavors"]):
        raise ValueError("Select a valid flavor")
    image = next(item for item in options["images"] if item["id"] == image_id)
    flavor = next(item for item in options["flavors"] if item["id"] == flavor_id)
    if flavor["disk_gb"] < image["min_disk_gb"] or flavor["ram_mb"] < image["min_ram_mb"]:
        raise ValueError("Selected flavor does not meet the image disk or memory requirements")

    vm_count = _coerce_int(payload.get("vm_count"))
    if vm_count is None:
        vm_count = int(profile["default_vm_count"] or options["limits"]["compute_count"] or 1)
    vm_count = max(int(profile["min_vm_count"]), min(int(profile["max_vm_count"]), vm_count))

    keypair_mode = str(payload.get("keypair_mode") or options["defaults"]["keypair_mode"] or "existing").strip().lower()
    if keypair_mode not in {"existing", "auto"}:
        raise ValueError("Keypair mode must be existing or auto")
    keypair_name = str(payload.get("keypair_name") or "").strip()
    if keypair_mode == "existing":
        if not keypair_name or not any(item["name"] == keypair_name for item in options["keypairs"]):
            raise ValueError("Select a valid existing keypair")
    else:
        keypair_name = ""

    cidr_mode = str(payload.get("cidr_mode") or options["defaults"]["cidr_mode"] or "auto").strip().lower()
    if cidr_mode == "manual":
        cidr = _validate_cidr(str(payload.get("cidr") or "").strip())
    else:
        cidr = str(options["defaults"]["cidr"] or suggest_stress_cidr(None))

    external_network_id = str(payload.get("external_network_id") or options["defaults"]["external_network_id"] or "").strip()
    if not external_network_id or not any(item["id"] == external_network_id for item in options["external_networks"]):
        raise ValueError("No external network is available for router creation")

    test_id = _stress_test_id()
    stack_name = f"{STRESS_STACK_PREFIX}{test_id}"
    name_prefix = stack_name
    generated_key_name = f"{stack_name}-key"
    return {
        "test_id": test_id,
        "stack_name": stack_name,
        "profile": profile_key,
        "name_prefix": name_prefix,
        "image_id": image_id,
        "flavor_id": flavor_id,
        "key_name": generated_key_name if keypair_mode == "auto" else keypair_name,
        "keypair_mode": keypair_mode,
        "vm_count": vm_count,
        "cidr": cidr,
        "external_network_id": external_network_id,
        "network_name": f"{name_prefix}-net",
        "subnet_name": f"{name_prefix}-subnet",
        "router_name": f"{name_prefix}-router",
        "security_group_name": f"{name_prefix}-secgroup",
    }


def launch_stress_stack(
    *,
    auth: openstack_ops.OpenStackAuth | None,
    compute_count: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    conn = openstack_ops._conn(auth=auth)
    record_stress_action("launch", "validation", message="Validating launch request")
    if _find_active_stress_stack_obj(conn) is not None:
        record_stress_action("launch", "blocked", status="warn", message="Launch blocked by active stress stack")
        raise ValueError("An active stress stack already exists; delete it before launching a new one")
    options = build_stress_options(
        auth=auth,
        compute_count=compute_count,
        profile_key=str(payload.get("profile") or ""),
    )
    stack_payload = _build_stack_payload(options=options, payload=payload)
    record_stress_action("launch", "validated", status="good", message="Launch request validated", detail=stack_payload["stack_name"])
    template = _build_stress_template(stack_payload)
    parameters = {
        "test_id": stack_payload["test_id"],
        "stack_name": stack_payload["stack_name"],
        "profile": stack_payload["profile"],
        "name_prefix": stack_payload["name_prefix"],
        "image": stack_payload["image_id"],
        "flavor": stack_payload["flavor_id"],
        "key_name": stack_payload["key_name"],
        "key_mode": stack_payload["keypair_mode"],
        "vm_count": stack_payload["vm_count"],
        "network_cidr": stack_payload["cidr"],
        "network_name": stack_payload["network_name"],
        "subnet_name": stack_payload["subnet_name"],
        "router_name": stack_payload["router_name"],
        "security_group_name": stack_payload["security_group_name"],
        "external_network_id": stack_payload["external_network_id"],
    }
    try:
        record_stress_action("launch", "calling_heat", message="Calling Heat create_stack", detail=stack_payload["stack_name"])
        conn.orchestration.create_stack(
            name=stack_payload["stack_name"],
            template=json.dumps(template),
            parameters=parameters,
            timeout=60,
            wait=False,
        )
        record_stress_action("launch", "heat_accepted", status="good", message="Heat accepted create_stack request", detail=stack_payload["stack_name"])
    except Exception as exc:
        record_stress_action("launch", "failed", status="bad", message="Heat create_stack failed", detail=str(exc))
        raise
    return get_stress_status(auth=auth)


def delete_active_stress_stack(auth: openstack_ops.OpenStackAuth | None) -> dict[str, Any]:
    conn = openstack_ops._conn(auth=auth)
    record_stress_action("delete", "validation", message="Looking up active stress stack")
    stack = _find_active_stress_stack_obj(conn)
    if stack is None:
        record_stress_action("delete", "blocked", status="warn", message="No active stress stack found for deletion")
        return {"deleted": False, "stack_name": "", "message": "No active stress stack detected."}
    stack_name = getattr(stack, "stack_name", None) or getattr(stack, "name", None) or ""
    try:
        record_stress_action("delete", "calling_heat", message="Calling Heat delete_stack", detail=stack_name)
        conn.orchestration.delete_stack(stack)
        record_stress_action("delete", "heat_accepted", status="good", message="Heat accepted delete_stack request", detail=stack_name)
    except Exception as exc:
        record_stress_action("delete", "failed", status="bad", message="Heat delete_stack failed", detail=str(exc))
        raise
    return {
        "deleted": True,
        "stack_name": stack_name,
        "message": "Stress stack deletion requested.",
    }


def _stack_resources(conn, stack: Any) -> list[Any]:
    orchestration = getattr(conn, "orchestration", None)
    if orchestration is None:
        return []
    stack_ref = getattr(stack, "id", None) or getattr(stack, "stack_name", None) or getattr(stack, "name", None)
    for attr in ("resources", "stack_resources"):
        method = getattr(orchestration, attr, None)
        if method is None:
            continue
        return list(method(stack_ref))
    return []


def _stack_events(conn, stack: Any) -> list[Any]:
    orchestration = getattr(conn, "orchestration", None)
    if orchestration is None:
        return []
    stack_ref = getattr(stack, "id", None) or getattr(stack, "stack_name", None) or getattr(stack, "name", None)
    for attr in ("events", "stack_events"):
        method = getattr(orchestration, attr, None)
        if method is None:
            continue
        try:
            return list(method(stack_ref))
        except TypeError:
            try:
                return list(method(stack=stack_ref))
            except TypeError:
                continue
    return []


def _event_resource_name(event: Any) -> str:
    return (
        getattr(event, "logical_resource_id", None)
        or getattr(event, "resource_name", None)
        or ""
    )


def _event_status(event: Any) -> str:
    return str(getattr(event, "resource_status", None) or getattr(event, "status", None) or "")


def _event_time_value(event: Any) -> Any:
    return getattr(event, "event_time", None) or getattr(event, "created_at", None) or getattr(event, "updated_at", None)


def _event_elapsed_by_resource(events: list[Any]) -> dict[str, int]:
    starts: dict[str, Any] = {}
    elapsed: dict[str, int] = {}
    for event in sorted(events, key=lambda item: _parse_time(_event_time_value(item)) or datetime.max.replace(tzinfo=timezone.utc)):
        name = _event_resource_name(event)
        if not name:
            continue
        status = _event_status(event).upper()
        event_time = _event_time_value(event)
        if status == "CREATE_IN_PROGRESS" and name not in starts:
            starts[name] = event_time
            continue
        if status in {"CREATE_COMPLETE", "CREATE_FAILED"} and name in starts and name not in elapsed:
            duration = _elapsed_seconds(starts[name], event_time)
            if duration is not None:
                elapsed[name] = duration
    return elapsed


def _resource_note(resource: dict[str, Any], server_info: dict[str, Any] | None = None) -> str:
    resource_type = resource["type"]
    if resource_type == "OS::Neutron::Subnet":
        return resource.get("cidr") or ""
    if resource_type == "OS::Neutron::Router":
        return "External gateway attached"
    if resource_type == "OS::Neutron::Net":
        return "Primary tenant network"
    if resource_type == "OS::Nova::Server" and server_info:
        host = _server_host(server_info)
        return f"Placed on {host}" if host else ""
    return ""


def _server_map_for_resources(conn, resources: list[dict[str, Any]]) -> dict[str, Any]:
    server_ids = [item["physical_id"] for item in resources if item["type"] == "OS::Nova::Server" and item["physical_id"]]
    if not server_ids:
        return {}
    result: dict[str, Any] = {}
    for server_id in server_ids:
        try:
            result[server_id] = conn.compute.get_server(server_id)
        except Exception:
            continue
    return result


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


def get_stress_status(auth: openstack_ops.OpenStackAuth | None) -> dict[str, Any]:
    conn = openstack_ops._conn(auth=auth)
    stack = _find_active_stress_stack_obj(conn)
    if stack is None:
        return {
            "active": False,
            "test": None,
            "summary": None,
            "resources": [],
            "servers": [],
            "distribution": [],
            "error": None,
        }

    stack_data = _stack_summary(stack)
    parameters = stack_data.get("parameters") or {}
    requested_vms = _coerce_int(parameters.get("vm_count")) or 0
    resources_raw = _stack_resources(conn, stack)
    event_elapsed = _event_elapsed_by_resource(_stack_events(conn, stack))
    resources: list[dict[str, Any]] = []
    for resource in resources_raw:
        resources.append({
            "logical_name": getattr(resource, "resource_name", None) or getattr(resource, "logical_resource_id", None) or "",
            "type": getattr(resource, "resource_type", None) or "",
            "physical_id": getattr(resource, "physical_resource_id", None) or getattr(resource, "physical_id", None) or "",
            "status": getattr(resource, "resource_status", None) or getattr(resource, "status", None) or "UNKNOWN",
            "created_at": str(getattr(resource, "creation_time", None) or getattr(resource, "created_at", None) or ""),
            "updated_at": str(getattr(resource, "updated_time", None) or getattr(resource, "updated_at", None) or ""),
        })
    server_map = _server_map_for_resources(conn, resources)

    server_rows: list[dict[str, Any]] = []
    distribution_counts: dict[str, int] = {}
    resource_rows: list[dict[str, Any]] = []
    server_elapsed_values: list[int] = []
    plumbing_elapsed_values: list[int] = []

    for item in resources:
        elapsed_s = event_elapsed.get(item["logical_name"])
        if elapsed_s is None:
            elapsed_s = _elapsed_seconds(item["created_at"], item["updated_at"])
        server_info = None
        if item["type"] == "OS::Nova::Server":
            server_info = server_map.get(item["physical_id"])
            host = _server_host(server_info) if server_info is not None else ""
            status = str(getattr(server_info, "status", None) or "UNKNOWN") if server_info is not None else "UNKNOWN"
            addresses = _server_addresses(server_info) if server_info is not None else []
            server_rows.append({
                "name": (getattr(server_info, "name", None) if server_info is not None else "") or item["logical_name"].replace("_", "-"),
                "server_id": item["physical_id"],
                "host": host,
                "status": status,
                "elapsed_s": elapsed_s,
                "elapsed": _format_seconds(elapsed_s),
                "ip": addresses[0] if addresses else "—",
            })
            if host:
                distribution_counts[host] = distribution_counts.get(host, 0) + 1
            if elapsed_s is not None:
                server_elapsed_values.append(elapsed_s)
        elif item["type"].startswith("OS::Neutron::"):
            if elapsed_s is not None:
                plumbing_elapsed_values.append(elapsed_s)
        resource_rows.append({
            "type": item["type"],
            "logical_name": item["logical_name"],
            "physical_id": item["physical_id"],
            "status": item["status"],
            "elapsed_s": elapsed_s,
            "elapsed": _format_seconds(elapsed_s),
            "notes": _resource_note(item, server_info=server_info),
            "cidr": parameters.get("network_cidr", "") if item["type"] == "OS::Neutron::Subnet" else "",
        })

    created_vms = len(server_rows)
    distribution = [
        {"host": host, "vm_count": count, "share_pct": _percent(count, created_vms)}
        for host, count in sorted(distribution_counts.items(), key=lambda entry: (-entry[1], entry[0]))
    ]
    stack_elapsed_s = _elapsed_seconds(stack_data.get("created_at"), stack_data.get("updated_at"))
    summary = {
        "stack_elapsed_s": stack_elapsed_s,
        "stack_elapsed": _format_seconds(stack_elapsed_s),
        "plumbing_elapsed_s": sum(plumbing_elapsed_values) if plumbing_elapsed_values else None,
        "plumbing_elapsed": _format_seconds(sum(plumbing_elapsed_values)) if plumbing_elapsed_values else "—",
        "avg_vm_build_s": round(mean(server_elapsed_values)) if server_elapsed_values else None,
        "avg_vm_build": _format_seconds(round(mean(server_elapsed_values))) if server_elapsed_values else "—",
        "p95_vm_build_s": _percentile(server_elapsed_values, 95),
        "p95_vm_build": _format_seconds(_percentile(server_elapsed_values, 95)),
        "slowest_vm_build_s": max(server_elapsed_values) if server_elapsed_values else None,
        "slowest_vm_build": _format_seconds(max(server_elapsed_values)) if server_elapsed_values else "—",
    }
    return {
        "active": True,
        "test": {
            "test_id": parameters.get("test_id", ""),
            "stack_name": stack_data["stack_name"],
            "profile": parameters.get("profile", ""),
            "status": stack_data["status"],
            "requested_vms": requested_vms,
            "created_vms": created_vms,
        },
        "summary": summary,
        "resources": sorted(resource_rows, key=lambda item: (item["type"] != "OS::Nova::Server", item["logical_name"])),
        "servers": sorted(server_rows, key=lambda item: item["name"]),
        "distribution": distribution,
        "error": None,
    }
