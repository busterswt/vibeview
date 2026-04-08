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
from .node_inventory_ops import (
    check_etcd_service,
    get_node_hardware_info,
    get_node_host_signals,
    get_node_monitor_metrics,
    get_node_network_stats,
)

LogFn = Callable[[str], None]

_CONTEXT: str | None = None
MANAGED_NOSCHEDULE_TAINT_KEY = "draino.openstack.org/maintenance"
MANAGED_NOSCHEDULE_TAINT_VALUE = "true"
MANAGED_NOSCHEDULE_TAINT_EFFECT = "NoSchedule"


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


from .k8s_inventory_ops import (
    get_etcd_node_names,
    get_mariadb_node_names,
    get_node_k8s_detail,
    get_nodes,
    get_pods_on_node,
    list_k8s_crds,
    list_k8s_namespaces,
    list_k8s_pods,
    list_k8s_pvcs,
    list_k8s_pvs,
    list_k8s_services,
)


def cordon_node(
    name: str,
    log: LogFn,
    auth: K8sAuth | None = None,
) -> None:
    """Mark a node unschedulable."""
    v1 = client.CoreV1Api(_api_client(auth))
    v1.patch_node(name, {"spec": {"unschedulable": True}})
    log(f"Node '{name}' cordoned successfully")


def uncordon_node(
    name: str,
    log: LogFn,
    auth: K8sAuth | None = None,
) -> None:
    """Mark a node schedulable."""
    v1 = client.CoreV1Api(_api_client(auth))
    v1.patch_node(name, {"spec": {"unschedulable": False}})
    log(f"Node '{name}' uncordoned successfully")


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


def has_managed_noschedule_taint(taints: list[dict] | None) -> bool:
    """Return True when Draino's managed NoSchedule taint is present."""
    if not taints:
        return False
    for taint in taints:
        if (
            taint.get("key") == MANAGED_NOSCHEDULE_TAINT_KEY
            and taint.get("effect") == MANAGED_NOSCHEDULE_TAINT_EFFECT
        ):
            return True
    return False


def set_managed_noschedule_taint(
    node_name: str,
    enabled: bool,
    auth: K8sAuth | None = None,
) -> None:
    """Add or remove Draino's managed NoSchedule taint on a K8s node.

    Only the Draino-managed taint is touched so unrelated scheduler policy
    remains intact.
    """
    v1 = client.CoreV1Api(_api_client(auth))
    node = v1.read_node(node_name)
    existing = []
    for taint in node.spec.taints or []:
        item = taint.to_dict() if hasattr(taint, "to_dict") else dict(taint)
        if (
            item.get("key") == MANAGED_NOSCHEDULE_TAINT_KEY
            and item.get("effect") == MANAGED_NOSCHEDULE_TAINT_EFFECT
        ):
            continue
        existing.append(item)

    if enabled:
        existing.append({
            "key": MANAGED_NOSCHEDULE_TAINT_KEY,
            "value": MANAGED_NOSCHEDULE_TAINT_VALUE,
            "effect": MANAGED_NOSCHEDULE_TAINT_EFFECT,
        })

    v1.patch_node(node_name, {"spec": {"taints": existing}})


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


def drain_node(
    name: str,
    log: LogFn,
    timeout: int = 300,
    auth: K8sAuth | None = None,
) -> None:
    """Evict drainable pods from a node and wait for termination.

    DaemonSet pods are ignored. Static/mirror pods are treated as a hard block
    because they cannot be evicted via the API and require node-local action.
    """
    v1 = client.CoreV1Api(_api_client(auth))

    def pod_ref(pod) -> str:
        return f"{pod.metadata.namespace}/{pod.metadata.name}"

    def is_terminal(pod) -> bool:
        return pod.status.phase in ("Succeeded", "Failed")

    def is_daemonset_pod(pod) -> bool:
        return any(ref.kind == "DaemonSet" for ref in (pod.metadata.owner_references or []))

    def is_mirror_pod(pod) -> bool:
        annotations = pod.metadata.annotations or {}
        return "kubernetes.io/config.mirror" in annotations

    log(f"Listing pods on node '{name}'…")
    pods = v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={name}")

    to_evict = []
    skipped_ds = 0
    blocked_mirror: list[str] = []

    for pod in pods.items:
        if is_terminal(pod):
            continue
        if is_mirror_pod(pod):
            blocked_mirror.append(pod_ref(pod))
            continue
        if is_daemonset_pod(pod):
            skipped_ds += 1
        else:
            to_evict.append(pod)

    if blocked_mirror:
        refs = ", ".join(blocked_mirror)
        raise RuntimeError(
            f"Static/mirror pod(s) block drain on '{name}': {refs}"
        )

    log(
        f"Evicting {len(to_evict)} pod(s), "
        f"skipping {skipped_ds} DaemonSet pod(s)"
    )

    eviction_failures: list[str] = []
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
                    detail = f"{ns}/{pname}: HTTP {exc.status} {exc.reason}"
                    eviction_failures.append(detail)
                    log(f"Warning: could not evict {ns}/{pname}: {exc.reason}")
                    break

    if eviction_failures:
        raise RuntimeError(
            f"Pod eviction failed on '{name}': {', '.join(eviction_failures)}"
        )

    log("Waiting for pods to terminate…")
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = v1.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={name}"
        )
        blocking_mirror = [
            p
            for p in remaining.items
            if not is_terminal(p) and is_mirror_pod(p)
        ]
        if blocking_mirror:
            refs = ", ".join(pod_ref(p) for p in blocking_mirror)
            raise RuntimeError(
                f"Static/mirror pod(s) block drain on '{name}': {refs}"
            )

        non_ds_alive = [
            p
            for p in remaining.items
            if not is_terminal(p)
            and not is_daemonset_pod(p)
            and not is_mirror_pod(p)
        ]
        if not non_ds_alive:
            log(f"All pods drained from '{name}'")
            return
        log(f"{len(non_ds_alive)} pod(s) still terminating on '{name}'…")
        time.sleep(10)

    terminating = [pod_ref(p) for p in non_ds_alive if p.metadata.deletion_timestamp]
    remaining_refs = [pod_ref(p) for p in non_ds_alive if not p.metadata.deletion_timestamp]
    details: list[str] = []
    if terminating:
        details.append(f"stuck terminating: {', '.join(terminating)}")
    if remaining_refs:
        details.append(f"remaining: {', '.join(remaining_refs)}")
    suffix = f" ({'; '.join(details)})" if details else ""
    raise RuntimeError(f"Drain timeout reached for '{name}'{suffix}")
