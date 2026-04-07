"""Kubernetes operations: cordon, drain."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable, Optional

import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.kube_config import KubeConfigLoader

from .. import node_agent_client

LogFn = Callable[[str], None]

_CONTEXT: str | None = None


@dataclass(slots=True)
class K8sAuth:
    mode: str = "token"
    server: str = ""
    token: str = ""
    skip_tls_verify: bool = False
    context: str | None = None
    ca_cert: str | None = None
    client_cert: str | None = None
    client_key: str | None = None
    kubeconfig: dict | None = None
    temp_dir: str | None = None


def configure(context: str | None = None) -> None:
    global _CONTEXT
    _CONTEXT = context


def _load_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config(context=_CONTEXT)


def _auth_temp_dir(auth: K8sAuth) -> str:
    if auth.temp_dir is None:
        auth.temp_dir = tempfile.mkdtemp(prefix="draino-k8s-")
    return auth.temp_dir


def _to_b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _manual_kubeconfig_dict(auth: K8sAuth) -> dict:
    cluster: dict[str, object] = {"server": auth.server.rstrip("/")}
    if auth.skip_tls_verify:
        cluster["insecure-skip-tls-verify"] = True
    elif auth.ca_cert:
        cluster["certificate-authority-data"] = _to_b64(auth.ca_cert)

    user: dict[str, object]
    if auth.mode == "client_cert":
        user = {
            "client-certificate-data": _to_b64(auth.client_cert or ""),
            "client-key-data": _to_b64(auth.client_key or ""),
        }
    else:
        user = {"token": auth.token}

    return {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": "draino", "cluster": cluster}],
        "users": [{"name": "draino-user", "user": user}],
        "contexts": [{
            "name": auth.context or "draino",
            "context": {"cluster": "draino", "user": "draino-user"},
        }],
        "current-context": auth.context or "draino",
    }


def _kubeconfig_dict(auth: K8sAuth) -> dict:
    return auth.kubeconfig if auth.kubeconfig is not None else _manual_kubeconfig_dict(auth)


def _write_kubeconfig(auth: K8sAuth) -> str:
    temp_dir = _auth_temp_dir(auth)
    path = os.path.join(temp_dir, "config.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(_kubeconfig_dict(auth), fh, sort_keys=False)
    return path


def _api_client(auth: K8sAuth | None = None) -> client.ApiClient:
    if auth is None:
        _load_config()
        return client.ApiClient()
    cfg = client.Configuration()
    loader = KubeConfigLoader(
        _kubeconfig_dict(auth),
        active_context=auth.context,
        temp_file_path=_auth_temp_dir(auth),
    )
    loader.load_and_set(cfg)
    return client.ApiClient(cfg)


def _kubectl_base_cmd(auth: K8sAuth | None = None) -> list[str]:
    cmd = ["kubectl"]
    if auth is None:
        if _CONTEXT:
            cmd += ["--context", _CONTEXT]
        return cmd
    cmd += ["--kubeconfig", _write_kubeconfig(auth)]
    if auth.context:
        cmd += ["--context", auth.context]
    return cmd


def _kubectl_plugin_env(auth: K8sAuth | None) -> dict | None:
    """Return an env dict for kubectl plugin subprocess calls.

    kubectl rejects global flags (--kubeconfig, --context) placed before a
    plugin name.  Setting KUBECONFIG as an environment variable sidesteps
    the issue while still pointing kubectl at the right credentials.
    Returns None (inherit parent env unchanged) when auth is None.
    """
    if auth is None:
        return None
    env = os.environ.copy()
    env["KUBECONFIG"] = _write_kubeconfig(auth)
    return env


def get_nodes(auth: K8sAuth | None = None) -> list[dict]:
    """Return a list of node info dicts."""
    v1 = client.CoreV1Api(_api_client(auth))
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
                "taints": [
                    {
                        "key": t.key,
                        "value": t.value or "",
                        "effect": t.effect or "",
                    }
                    for t in (node.spec.taints or [])
                ],
                "ready": ready,
                "ready_since": ready_since,
                "kernel_version": kernel_version,
            }
        )
    return result


def get_node_k8s_detail(
    node_name: str,
    auth: K8sAuth | None = None,
) -> dict:
    """Return detailed K8s node info for the summary tab.

    Fetches node_info (kubelet version, container runtime, OS image,
    architecture), capacity/allocatable (cpu, memory, pods), and live
    pod count.  All values default to None on failure.
    """
    v1 = client.CoreV1Api(_api_client(auth))

    result: dict = {
        "kubelet_version":     None,
        "container_runtime":   None,
        "os_image":            None,
        "architecture":        None,
        "cpu_capacity":        None,
        "memory_capacity_kb":  None,
        "pods_capacity":       None,
        "cpu_allocatable":     None,
        "memory_allocatable_kb": None,
        "pods_allocatable":    None,
        "pod_count":           None,
        "roles":               [],
        "labels":              {},
        "annotations":         {},
        "error":               None,
    }

    try:
        node = v1.read_node(node_name)
    except Exception as e:
        result["error"] = str(e)
        return result

    # node_info
    ni = node.status.node_info
    if ni:
        result["kubelet_version"]   = ni.kubelet_version
        result["container_runtime"] = ni.container_runtime_version
        result["os_image"]          = ni.os_image
        result["architecture"]      = ni.architecture

    def _parse_ki(s: str | None) -> int | None:
        """Convert K8s memory string like '263928792Ki' → KiB int."""
        if not s:
            return None
        s = s.strip()
        if s.endswith("Ki"):
            try:
                return int(s[:-2])
            except Exception:
                return None
        if s.endswith("Mi"):
            try:
                return int(s[:-2]) * 1024
            except Exception:
                return None
        if s.endswith("Gi"):
            try:
                return int(s[:-2]) * 1024 * 1024
            except Exception:
                return None
        try:
            return int(s) // 1024  # bytes → KiB
        except Exception:
            return None

    cap  = node.status.capacity    or {}
    alloc = node.status.allocatable or {}
    result["cpu_capacity"]           = cap.get("cpu")
    result["memory_capacity_kb"]     = _parse_ki(cap.get("memory"))
    result["pods_capacity"]          = cap.get("pods")
    result["cpu_allocatable"]        = alloc.get("cpu")
    result["memory_allocatable_kb"]  = _parse_ki(alloc.get("memory"))
    result["pods_allocatable"]       = alloc.get("pods")

    # Roles from labels  (node-role.kubernetes.io/<role>)
    labels = node.metadata.labels or {}
    roles = [
        k.split("/", 1)[1]
        for k in labels
        if k.startswith("node-role.kubernetes.io/")
    ]
    result["roles"] = roles or ["worker"]
    result["labels"] = dict(sorted(labels.items()))
    result["annotations"] = dict(sorted((node.metadata.annotations or {}).items()))

    # Live pod count (non-terminated)
    try:
        pods = v1.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={node_name}"
        )
        result["pod_count"] = sum(
            1 for p in pods.items
            if p.status.phase not in ("Succeeded", "Failed")
        )
    except Exception:
        pass

    return result


def check_etcd_service(node_name: str, hostname: str | None = None) -> Optional[bool]:
    """Check whether the etcd systemd service is active via the node agent."""
    try:
        result = node_agent_client.get_etcd_status(node_name)
        return result.get("active")
    except Exception:
        return None


def get_node_host_signals(node_name: str, hostname: str | None = None) -> dict:
    """Return lightweight reboot/kernel signals for a node via the node agent."""
    try:
        return node_agent_client.get_host_signals(node_name)
    except Exception as exc:
        return {
            "kernel_version": None,
            "latest_kernel_version": None,
            "reboot_required": False,
            "error": str(exc),
        }


def get_etcd_node_names(auth: K8sAuth | None = None) -> set[str]:
    """Return the set of node names in the etcd role.

    Detects nodes labelled by kubespray with node-role.kubernetes.io/etcd.
    """
    v1 = client.CoreV1Api(_api_client(auth))
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


def cordon_node(
    name: str,
    log: LogFn,
    auth: K8sAuth | None = None,
) -> None:
    """Mark a node unschedulable."""
    v1 = client.CoreV1Api(_api_client(auth))
    v1.patch_node(name, {"spec": {"unschedulable": True}})
    log(f"Node '{name}' cordoned successfully")


def get_pods_on_node(
    node_name: str,
    auth: K8sAuth | None = None,
) -> list[dict]:
    """Return a list of pod info dicts for all pods scheduled on *node_name*."""
    v1 = client.CoreV1Api(_api_client(auth))
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


def uncordon_node(
    name: str,
    log: LogFn,
    auth: K8sAuth | None = None,
) -> None:
    """Mark a node schedulable."""
    v1 = client.CoreV1Api(_api_client(auth))
    v1.patch_node(name, {"spec": {"unschedulable": False}})
    log(f"Node '{name}' uncordoned successfully")


def get_node_hardware_info(node_name: str, hostname: str | None = None) -> dict:
    """Return chassis, CPU, and RAM hardware details via the node agent."""
    result: dict = {
        "hostname":            None,
        "architecture":        None,
        "kernel_version":      None,
        "uptime":              None,
        "vendor":              None,
        "product":             None,
        "bios_version":        None,
        "cpu_model":           None,
        "cpu_sockets":         None,
        "cpu_cores_per_socket": None,
        "cpu_threads_per_core": None,
        "ram_type":            None,
        "ram_speed":           None,
        "ram_total_gb":        None,
        "ram_slots_used":      None,
        "ram_manufacturer":    None,
        "error":               None,
    }

    try:
        result.update(node_agent_client.get_host_detail(node_name))
        result.setdefault("error", None)
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


OVN_ANNOTATION_KEYS = [
    "ovn.kubernetes.io/tunnel-interface",
    "ovn.openstack.org/bridges",
    "ovn.openstack.org/int_bridge",
    "ovn.openstack.org/mappings",
    "ovn.openstack.org/ports",
]


def get_node_ovn_annotations(
    node_name: str,
    auth: K8sAuth | None = None,
) -> dict:
    """Return OVN-related annotations from the K8s node object.

    Returns a dict keyed by annotation name → value (str or None).
    Adds an ``error`` key if the K8s API call fails.
    """
    v1 = client.CoreV1Api(_api_client(auth))
    try:
        node = v1.read_node(node_name)
        ann = node.metadata.annotations or {}
        return {k: ann.get(k) for k in OVN_ANNOTATION_KEYS}
    except Exception as exc:
        return {k: None for k in OVN_ANNOTATION_KEYS} | {"error": str(exc)}


def patch_node_annotation(
    node_name: str,
    key: str,
    value: Optional[str],
    auth: K8sAuth | None = None,
) -> None:
    """Set (or remove, if *value* is None) a single annotation on a K8s node.

    Uses the kubernetes Python client so no subprocess/kubectl required.
    Raises any ApiException on failure.
    """
    v1 = client.CoreV1Api(_api_client(auth))
    body = {"metadata": {"annotations": {key: value}}}
    v1.patch_node(node_name, body)


def get_node_network_interfaces(node_name: str, hostname: str | None = None) -> dict:
    """Return physical and bond network interfaces via the node agent."""
    try:
        return node_agent_client.get_network_interfaces(node_name)
    except Exception as exc:
        return {"interfaces": [], "error": str(exc)}


def get_ovn_port_detail(port_id: str, auth: K8sAuth | None = None) -> dict:
    """Run `kubectl ko nbctl lsp-show <port_id>` and return parsed data.

    Returns a dict with keys: id, type, addresses, port_security,
    up, enabled, tag, external_ids, options, dynamic_addresses.
    Raises RuntimeError if kubectl is unavailable or the command fails.
    """
    import json as _json
    import re as _re

    # ovn-nbctl has no lsp-show; use --format=list list TABLE <name> which
    # looks up by name column directly — avoids the find condition parser
    # mis-treating hyphenated UUIDs as multi-value expressions.
    cmd = ["kubectl", "ko", "nbctl", "--format=list", "list", "Logical_Switch_Port",
           port_id]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                env=_kubectl_plugin_env(auth))
    except FileNotFoundError as exc:
        raise RuntimeError("kubectl not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("kubectl ko nbctl list timed out") from exc

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


def get_ovn_logical_switch(
    network_id: str,
    auth: K8sAuth | None = None,
) -> dict:
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
    cmd = ["kubectl", "ko", "nbctl", "show", ls_name]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                env=_kubectl_plugin_env(auth))
    except FileNotFoundError as exc:
        raise RuntimeError("kubectl not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("kubectl ko nbctl show timed out") from exc

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
                "id":          stripped[len("port "):].split()[0],
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


def _ovsdb_map_to_dict(value) -> dict[str, str]:
    """Convert OVSDB JSON map encoding like ["map", [[k, v], ...]] to a dict."""
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


# ── Cluster-wide resource listings ───────────────────────────────────────────

def _ts(obj) -> str | None:
    ts = obj.metadata.creation_timestamp if obj and obj.metadata else None
    return ts.isoformat() if ts else None


def list_k8s_namespaces(auth: K8sAuth | None = None) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    return [
        {"name": ns.metadata.name, "status": ns.status.phase or "Active", "created": _ts(ns),
         "labels": dict(ns.metadata.labels or {})}
        for ns in v1.list_namespace().items
    ]


def list_k8s_pods(
    namespace: str | None = None,
    auth: K8sAuth | None = None,
) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    raw = v1.list_pod_for_all_namespaces() if not namespace else v1.list_namespaced_pod(namespace)
    result = []
    for pod in raw.items:
        total    = len(pod.spec.containers or [])
        ready    = 0
        restarts = 0
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                if cs.ready: ready += 1
                restarts += cs.restart_count or 0
        result.append({
            "namespace": pod.metadata.namespace,
            "name":      pod.metadata.name,
            "phase":     pod.status.phase or "Unknown",
            "ready":     f"{ready}/{total}",
            "restarts":  restarts,
            "node":      pod.spec.node_name or "",
            "created":   _ts(pod),
        })
    return result


def list_k8s_services(
    namespace: str | None = None,
    auth: K8sAuth | None = None,
) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    raw = v1.list_service_for_all_namespaces() if not namespace else v1.list_namespaced_service(namespace)
    result = []
    for svc in raw.items:
        ports = ", ".join(
            f"{p.port}{'/' + p.protocol if p.protocol != 'TCP' else ''}"
            + (f":{p.node_port}" if p.node_port else "")
            for p in (svc.spec.ports or [])
        )
        ext_ips: list[str] = []
        if svc.status.load_balancer and svc.status.load_balancer.ingress:
            ext_ips = [i.ip or i.hostname or "" for i in svc.status.load_balancer.ingress]
        result.append({
            "namespace":    svc.metadata.namespace,
            "name":         svc.metadata.name,
            "type":         svc.spec.type or "ClusterIP",
            "cluster_ip":   svc.spec.cluster_ip or "",
            "external_ips": [x for x in ext_ips if x],
            "ports":        ports,
            "created":      _ts(svc),
        })
    return result


def list_k8s_pvs(auth: K8sAuth | None = None) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    result = []
    for pv in v1.list_persistent_volume().items:
        claim = ""
        if pv.spec.claim_ref:
            claim = f"{pv.spec.claim_ref.namespace}/{pv.spec.claim_ref.name}"
        result.append({
            "name":           pv.metadata.name,
            "capacity":       (pv.spec.capacity or {}).get("storage", ""),
            "access_modes":   ",".join(pv.spec.access_modes or []),
            "reclaim_policy": pv.spec.persistent_volume_reclaim_policy or "",
            "status":         pv.status.phase or "",
            "claim":          claim,
            "storageclass":   pv.spec.storage_class_name or "",
            "created":        _ts(pv),
        })
    return result


def list_k8s_pvcs(
    namespace: str | None = None,
    auth: K8sAuth | None = None,
) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    raw = (v1.list_persistent_volume_claim_for_all_namespaces() if not namespace
           else v1.list_namespaced_persistent_volume_claim(namespace))
    result = []
    for pvc in raw.items:
        result.append({
            "namespace":    pvc.metadata.namespace,
            "name":         pvc.metadata.name,
            "status":       pvc.status.phase or "",
            "volume":       pvc.spec.volume_name or "",
            "capacity":     (pvc.status.capacity or {}).get("storage", ""),
            "access_modes": ",".join(pvc.spec.access_modes or []),
            "storageclass": pvc.spec.storage_class_name or "",
            "created":      _ts(pvc),
        })
    return result


def list_k8s_crds(auth: K8sAuth | None = None) -> list[dict]:
    api = client.ApiextensionsV1Api(_api_client(auth))
    result = []
    for crd in api.list_custom_resource_definition().items:
        spec = crd.spec
        versions = [v.name for v in (spec.versions or []) if v.served]
        result.append({
            "name":     crd.metadata.name,
            "group":    spec.group,
            "kind":     spec.names.kind,
            "scope":    spec.scope,
            "versions": versions,
            "created":  _ts(crd),
        })
    return result


def drain_node(
    name: str,
    log: LogFn,
    timeout: int = 300,
    auth: K8sAuth | None = None,
) -> None:
    """Evict all non-DaemonSet pods from a node and wait for termination."""
    v1 = client.CoreV1Api(_api_client(auth))

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
