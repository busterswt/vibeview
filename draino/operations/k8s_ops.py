"""Kubernetes operations: cordon, drain."""
from __future__ import annotations

import subprocess
import time
from typing import Callable, Optional

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

LogFn = Callable[[str], None]

_CONTEXT: str | None = None


def configure(context: str | None = None) -> None:
    global _CONTEXT
    _CONTEXT = context


def _load_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config(context=_CONTEXT)


def get_nodes() -> list[dict]:
    """Return a list of node info dicts."""
    _load_config()
    v1 = client.CoreV1Api()
    raw = v1.list_node()
    result: list[dict] = []
    for node in raw.items:
        name: str = node.metadata.name
        hostname: str = node.metadata.labels.get("kubernetes.io/hostname", name)
        unschedulable: bool = bool(node.spec.unschedulable)
        ready = False
        ready_since = None
        for cond in node.status.conditions or []:
            if cond.type == "Ready":
                ready = cond.status == "True"
                if ready:
                    ready_since = cond.last_transition_time
        node_info = node.status.node_info
        kernel_version: str | None = node_info.kernel_version if node_info else None
        result.append(
            {
                "name": name,
                "hostname": hostname,
                "cordoned": unschedulable,
                "ready": ready,
                "ready_since": ready_since,
                "kernel_version": kernel_version,
            }
        )
    return result


def check_etcd_service(hostname: str) -> Optional[bool]:
    """SSH to *hostname* and check whether the etcd systemd service is active.

    Returns True if active, False if inactive/failed, None if the check
    could not be completed (SSH unreachable, timeout, etc.).
    """
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "ConnectTimeout=5",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                hostname,
                "systemctl", "is-active", "etcd",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return None


def get_etcd_node_names() -> set[str]:
    """Return the set of node names in the etcd role.

    Detects nodes labelled by kubespray with node-role.kubernetes.io/etcd.
    """
    _load_config()
    v1 = client.CoreV1Api()
    result: set[str] = set()
    try:
        nodes = v1.list_node(
            label_selector="node-role.kubernetes.io/etcd"
        )
        for node in nodes.items:
            result.add(node.metadata.name)
    except Exception:
        pass
    return result


def cordon_node(name: str, log: LogFn) -> None:
    """Mark a node unschedulable."""
    _load_config()
    v1 = client.CoreV1Api()
    v1.patch_node(name, {"spec": {"unschedulable": True}})
    log(f"Node '{name}' cordoned successfully")


def get_pods_on_node(node_name: str) -> list[dict]:
    """Return a list of pod info dicts for all pods scheduled on *node_name*."""
    _load_config()
    v1 = client.CoreV1Api()
    raw = v1.list_pod_for_all_namespaces(
        field_selector=f"spec.nodeName={node_name}"
    )
    result: list[dict] = []
    for pod in raw.items:
        ready_count = 0
        total_count = 0
        restarts = 0
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                total_count += 1
                if cs.ready:
                    ready_count += 1
                restarts += cs.restart_count or 0
        elif pod.spec.containers:
            total_count = len(pod.spec.containers)
        result.append({
            "namespace":   pod.metadata.namespace,
            "name":        pod.metadata.name,
            "phase":       pod.status.phase or "Unknown",
            "ready_count": ready_count,
            "total_count": total_count,
            "restarts":    restarts,
            "created_at":  pod.metadata.creation_timestamp,
        })
    return result


def uncordon_node(name: str, log: LogFn) -> None:
    """Mark a node schedulable."""
    _load_config()
    v1 = client.CoreV1Api()
    v1.patch_node(name, {"spec": {"unschedulable": False}})
    log(f"Node '{name}' uncordoned successfully")


def get_ovn_port_detail(port_id: str) -> dict:
    """Run `kubectl ko nbctl lsp-show <port_id>` and return parsed data.

    Returns a dict with keys: id, type, addresses, port_security,
    up, enabled, tag, external_ids, options, dynamic_addresses.
    Raises RuntimeError if kubectl is unavailable or the command fails.
    """
    import json as _json
    import re as _re

    cmd = ["kubectl"]
    if _CONTEXT:
        cmd += ["--context", _CONTEXT]
    # ovn-nbctl has no lsp-show; use --format=list list TABLE <name> which
    # looks up by name column directly — avoids the find condition parser
    # mis-treating hyphenated UUIDs as multi-value expressions.
    cmd += ["ko", "nbctl", "--format=list", "list", "Logical_Switch_Port",
            port_id]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        raise RuntimeError("kubectl not found in PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("kubectl ko nbctl list timed out")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"nbctl list exited with code {result.returncode}")

    if not result.stdout.strip():
        raise RuntimeError(f"No logical switch port found with name {port_id!r}")

    def _parse_ovn_map(s: str) -> dict:
        """Parse OVN map format: {key="value", key2=value2}"""
        s = s.strip().strip("{}")
        out: dict = {}
        for m in _re.finditer(r'([\w:.\-]+)\s*=\s*"([^"]*)"', s):
            out[m.group(1)] = m.group(2)
        for m in _re.finditer(r'([\w:.\-]+)\s*=\s*([^",}\s]+)', s):
            if m.group(1) not in out:
                out[m.group(1)] = m.group(2)
        return out

    data: dict = {
        "id":                port_id,
        "type":              "",
        "addresses":         [],
        "port_security":     [],
        "up":                None,
        "enabled":           None,
        "tag":               None,
        "external_ids":      {},
        "options":           {},
        "dynamic_addresses": "",
    }

    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue

        if key == "type":
            data["type"] = val.strip('"')
        elif key == "addresses":
            try:
                data["addresses"] = _json.loads(val)
            except Exception:
                data["addresses"] = [val.strip('"')] if val and val != "[]" else []
        elif key == "port_security":
            try:
                data["port_security"] = _json.loads(val)
            except Exception:
                data["port_security"] = [val.strip('"')] if val and val != "[]" else []
        elif key == "up":
            if val in ("true", "false"):
                data["up"] = (val == "true")
        elif key == "enabled":
            if val in ("true", "false"):
                data["enabled"] = (val == "true")
        elif key == "tag":
            try:
                data["tag"] = int(val)
            except Exception:
                pass
        elif key == "dynamic_addresses":
            v = val.strip('"')
            if v:
                data["dynamic_addresses"] = v
        elif key in ("external_ids", "options"):
            data[key] = _parse_ovn_map(val)

    return data


def get_ovn_logical_switch(network_id: str) -> dict:
    """Run `kubectl ko nbctl show neutron-<network_id>` and return parsed data.

    Returns:
        {
            "ls_name": "neutron-<uuid>",
            "ls_uuid": "<ovn-internal-uuid>",
            "ports": [
                {
                    "id":          "<port-name>",   # Neutron port UUID for VM/router ports
                    "type":        "",              # "" | "router" | "localnet" | ...
                    "addresses":   ["mac ip", ...],
                    "router_port": "",
                }
            ]
        }
    Raises RuntimeError if kubectl is not available or the command fails.
    """
    import json as _json

    ls_name = f"neutron-{network_id}"
    cmd = ["kubectl"]
    if _CONTEXT:
        cmd += ["--context", _CONTEXT]
    cmd += ["ko", "nbctl", "show", ls_name]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        raise RuntimeError("kubectl not found in PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("kubectl ko nbctl show timed out")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"kubectl ko exited with code {result.returncode}")

    # ── Parse the nbctl show output ──────────────────────────────────────────
    # Format:
    #   switch <ovn-uuid> (neutron-<network-id>)
    #       port <port-name>
    #           type: router
    #           addresses: ["fa:16:3e:... 10.0.0.1"]
    lines = result.stdout.splitlines()
    ls_uuid = ""
    ports: list[dict] = []
    current: dict | None = None

    for line in lines:
        content = line.rstrip()
        stripped = content.lstrip()
        if not stripped:
            continue
        indent = len(content) - len(stripped)

        if indent == 0 and stripped.startswith("switch "):
            parts = stripped.split(None, 2)
            ls_uuid = parts[1] if len(parts) > 1 else ""
            current = None

        elif indent == 4 and stripped.startswith("port "):
            if current is not None:
                ports.append(current)
            current = {
                "id":          stripped[len("port "):],
                "type":        "",
                "addresses":   [],
                "router_port": "",
            }

        elif indent == 8 and current is not None and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if key == "type":
                current["type"] = val
            elif key == "router-port":
                current["router_port"] = val.strip('"')
            elif key == "addresses":
                try:
                    current["addresses"] = _json.loads(val)
                except Exception:
                    current["addresses"] = [val.strip('"')]

    if current is not None:
        ports.append(current)

    return {"ls_name": ls_name, "ls_uuid": ls_uuid, "ports": ports}


def drain_node(name: str, log: LogFn, timeout: int = 300) -> None:
    """Evict all non-DaemonSet pods from a node and wait for termination."""
    _load_config()
    v1 = client.CoreV1Api()

    log(f"Listing pods on node '{name}'…")
    pods = v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={name}")

    to_evict = []
    skipped_ds = 0

    for pod in pods.items:
        if pod.status.phase in ("Succeeded", "Failed"):
            continue
        is_ds = any(
            ref.kind == "DaemonSet"
            for ref in (pod.metadata.owner_references or [])
        )
        if is_ds:
            skipped_ds += 1
        else:
            to_evict.append(pod)

    log(
        f"Evicting {len(to_evict)} pod(s), "
        f"skipping {skipped_ds} DaemonSet pod(s)"
    )

    for pod in to_evict:
        ns    = pod.metadata.namespace
        pname = pod.metadata.name
        eviction = client.V1Eviction(
            metadata=client.V1ObjectMeta(name=pname, namespace=ns)
        )
        for attempt in range(2):
            try:
                v1.create_namespaced_pod_eviction(
                    name=pname, namespace=ns, body=eviction
                )
                log(f"Evicted {ns}/{pname}")
                break
            except ApiException as exc:
                if exc.status == 429 and attempt == 0:
                    log(f"PodDisruptionBudget delay for {ns}/{pname}, retrying…")
                    time.sleep(5)
                elif exc.status == 404:
                    break  # already gone
                else:
                    log(f"Warning: could not evict {ns}/{pname}: {exc.reason}")
                    break

    log("Waiting for pods to terminate…")
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = v1.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={name}"
        )
        non_ds_alive = [
            p
            for p in remaining.items
            if p.status.phase not in ("Succeeded", "Failed")
            and not any(
                r.kind == "DaemonSet"
                for r in (p.metadata.owner_references or [])
            )
        ]
        if not non_ds_alive:
            log(f"All pods drained from '{name}'")
            return
        log(f"{len(non_ds_alive)} pod(s) still terminating on '{name}'…")
        time.sleep(10)

    log(f"WARNING: drain timeout reached for '{name}' — some pods may remain")
