"""OVN query helpers used by Draino."""
from __future__ import annotations

import json
import re
import subprocess

from .k8s_ops import K8sAuth, _kubectl_plugin_env


def get_ovn_port_detail(port_id: str, auth: K8sAuth | None = None) -> dict:
    """Run `kubectl ko nbctl list logical_switch_port <id>` and parse the result."""
    import json as _json

    cmd = ["kubectl", "ko", "nbctl", "list", "logical_switch_port", port_id]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=_kubectl_plugin_env(auth))
    except FileNotFoundError as exc:
        raise RuntimeError("kubectl not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("kubectl ko nbctl list timed out") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"nbctl list exited with code {result.returncode}")

    if not result.stdout.strip():
        raise RuntimeError(f"No logical switch port found with name {port_id!r}")

    def _parse_ovn_map(value: str) -> dict:
        value = value.strip().strip("{}")
        out: dict = {}
        for match in re.finditer(r'([\w:.\-]+)\s*=\s*"([^"]*)"', value):
            out[match.group(1)] = match.group(2)
        for match in re.finditer(r'([\w:.\-]+)\s*=\s*([^",}\s]+)', value):
            if match.group(1) not in out:
                out[match.group(1)] = match.group(2)
        return out

    data: dict = {
        "id": port_id,
        "type": "",
        "addresses": [],
        "port_security": [],
        "up": None,
        "enabled": None,
        "tag": None,
        "external_ids": {},
        "options": {},
        "dynamic_addresses": "",
    }

    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if key == "type":
            data["type"] = value.strip('"')
        elif key == "addresses":
            try:
                data["addresses"] = _json.loads(value)
            except Exception:
                data["addresses"] = [value.strip('"')] if value and value != "[]" else []
        elif key == "port_security":
            try:
                data["port_security"] = _json.loads(value)
            except Exception:
                data["port_security"] = [value.strip('"')] if value and value != "[]" else []
        elif key == "up":
            if value in ("true", "false"):
                data["up"] = (value == "true")
        elif key == "enabled":
            if value in ("true", "false"):
                data["enabled"] = (value == "true")
        elif key == "tag":
            try:
                data["tag"] = int(value)
            except Exception:
                pass
        elif key == "dynamic_addresses":
            stripped = value.strip('"')
            if stripped:
                data["dynamic_addresses"] = stripped
        elif key in ("external_ids", "options"):
            data[key] = _parse_ovn_map(value)

    return data


def get_ovn_logical_switch(network_id: str, auth: K8sAuth | None = None) -> dict:
    """Run `kubectl ko nbctl show neutron-<network_id>` and return parsed data."""
    import json as _json

    logical_switch_name = f"neutron-{network_id}"
    cmd = ["kubectl", "ko", "nbctl", "show", logical_switch_name]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=_kubectl_plugin_env(auth))
    except FileNotFoundError as exc:
        raise RuntimeError("kubectl not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("kubectl ko nbctl show timed out") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"kubectl ko exited with code {result.returncode}")

    logical_switch_uuid = ""
    ports: list[dict] = []
    current: dict | None = None

    for line in result.stdout.splitlines():
        content = line.rstrip()
        stripped = content.lstrip()
        if not stripped:
            continue
        indent = len(content) - len(stripped)

        if indent == 0 and stripped.startswith("switch "):
            parts = stripped.split(None, 2)
            logical_switch_uuid = parts[1] if len(parts) > 1 else ""
            current = None
        elif indent == 4 and stripped.startswith("port "):
            if current is not None:
                ports.append(current)
            current = {
                "id": stripped[len("port "):].split()[0],
                "type": "",
                "addresses": [],
                "router_port": "",
            }
        elif indent == 8 and current is not None and ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "type":
                current["type"] = value
            elif key == "router-port":
                current["router_port"] = value.strip('"')
            elif key == "addresses":
                try:
                    current["addresses"] = _json.loads(value)
                except Exception:
                    current["addresses"] = [value.strip('"')]

    if current is not None:
        ports.append(current)

    return {"ls_name": logical_switch_name, "ls_uuid": logical_switch_uuid, "ports": ports}


def get_ovn_port_logical_switch(port_id: str, network_id: str, auth: K8sAuth | None = None) -> dict:
    """Return the logical switch attachment for a specific Neutron port."""
    logical_switch = get_ovn_logical_switch(network_id, auth=auth)
    port = next((item for item in logical_switch["ports"] if item.get("id") == port_id), None)
    if port is None:
        raise RuntimeError(f"Logical switch {logical_switch['ls_name']!r} does not contain port {port_id!r}")
    return {
        "port_id": port_id,
        "network_id": network_id,
        "ls_name": logical_switch["ls_name"],
        "ls_uuid": logical_switch["ls_uuid"],
        "port": port,
    }


def get_ovn_logical_router(router_id: str, auth: K8sAuth | None = None) -> dict:
    """Run `kubectl ko nbctl show neutron-<router_id>` and return parsed data."""
    import json as _json

    logical_router_name = f"neutron-{router_id}"
    cmd = ["kubectl", "ko", "nbctl", "show", logical_router_name]
    chassis_host_cache: dict[str, str] = {}

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env=_kubectl_plugin_env(auth))
    except FileNotFoundError as exc:
        raise RuntimeError("kubectl not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("kubectl ko nbctl show timed out") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"kubectl ko exited with code {result.returncode}")

    logical_router_uuid = ""
    ports: list[dict] = []
    current: dict | None = None

    for line in result.stdout.splitlines():
        content = line.rstrip()
        stripped = content.lstrip()
        if not stripped:
            continue
        indent = len(content) - len(stripped)

        if indent == 0 and stripped.startswith("router "):
            parts = stripped.split(None, 2)
            logical_router_uuid = parts[1] if len(parts) > 1 else ""
            current = None
        elif indent == 4 and stripped.startswith("port "):
            if current is not None:
                ports.append(current)
            current = {
                "id": stripped[len("port "):].split()[0],
                "mac": "",
                "networks": [],
                "peer": "",
                "gateway_chassis": [],
                "gateway_hosts": [],
            }
        elif indent == 8 and current is not None and ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "mac":
                current["mac"] = value.strip('"')
            elif key == "peer":
                current["peer"] = value.strip('"')
            elif key == "gateway chassis":
                chassis_name = value.strip('"')
                if chassis_name:
                    current["gateway_chassis"].append(chassis_name)
                    if chassis_name not in chassis_host_cache:
                        chassis_host_cache[chassis_name] = _get_ovn_chassis_hostname(chassis_name, auth=auth)
                    current["gateway_hosts"].append(chassis_host_cache[chassis_name])
            elif key == "networks":
                try:
                    current["networks"] = _json.loads(value)
                except Exception:
                    current["networks"] = [value.strip('"')] if value and value != "[]" else []

    if current is not None:
        ports.append(current)

    return {"lr_name": logical_router_name, "lr_uuid": logical_router_uuid, "ports": ports}


def _ovsdb_map_to_dict(value) -> dict[str, str]:
    if not isinstance(value, list) or len(value) != 2 or value[0] != "map":
        return {}
    entries = value[1]
    if not isinstance(entries, list):
        return {}
    out: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 2:
            continue
        key, val = entry
        out[str(key)] = str(val)
    return out


def _get_ovn_chassis_hostname(chassis_name: str, auth: K8sAuth | None = None) -> str:
    """Return the hostname for one OVN chassis name via kubectl ko sbctl find."""
    cmd = ["kubectl", "ko", "sbctl", "--format=json", "find", "chassis", f"name={chassis_name}"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            env=_kubectl_plugin_env(auth),
        )
    except FileNotFoundError as exc:
        raise RuntimeError("kubectl not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("kubectl ko sbctl list timed out") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"kubectl ko sbctl exited with code {result.returncode}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from kubectl ko sbctl: {exc}") from exc

    headings = payload.get("headings")
    rows = payload.get("data")
    if not isinstance(headings, list) or not isinstance(rows, list):
        raise RuntimeError("unexpected kubectl ko sbctl JSON shape")

    try:
        hostname_idx = headings.index("hostname")
    except ValueError as exc:
        raise RuntimeError("required Chassis columns not present in kubectl ko sbctl output") from exc

    for row in rows:
        if not isinstance(row, list):
            continue
        if hostname_idx >= len(row):
            continue
        hostname = row[hostname_idx]
        if isinstance(hostname, str) and hostname:
            return hostname
    return chassis_name


def get_ovn_edge_nodes(auth: K8sAuth | None = None) -> set[str]:
    """Return chassis hostnames marked with enable-chassis-as-gw via kubectl ko."""
    cmd = ["kubectl", "ko", "sbctl", "--format=json", "list", "Chassis"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            env=_kubectl_plugin_env(auth),
        )
    except FileNotFoundError as exc:
        raise RuntimeError("kubectl not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("kubectl ko sbctl list timed out") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"kubectl ko sbctl exited with code {result.returncode}")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from kubectl ko sbctl: {exc}") from exc

    headings = payload.get("headings")
    rows = payload.get("data")
    if not isinstance(headings, list) or not isinstance(rows, list):
        raise RuntimeError("unexpected kubectl ko sbctl JSON shape")

    try:
        hostname_idx = headings.index("hostname")
        other_config_idx = headings.index("other_config")
    except ValueError as exc:
        raise RuntimeError("required Chassis columns not present in kubectl ko sbctl output") from exc

    edge_nodes: set[str] = set()
    for row in rows:
        if not isinstance(row, list):
            continue
        if hostname_idx >= len(row) or other_config_idx >= len(row):
            continue
        hostname = row[hostname_idx]
        if not isinstance(hostname, str):
            continue
        other_config = _ovsdb_map_to_dict(row[other_config_idx])
        cms_options = other_config.get("ovn-cms-options", "")
        options = {item.strip() for item in cms_options.split(",") if item.strip()}
        if "enable-chassis-as-gw" in options:
            edge_nodes.add(hostname)

    return edge_nodes
