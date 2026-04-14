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
    list_k8s_daemonsets,
    list_k8s_deployments,
    get_etcd_node_names,
    list_k8s_gatewayclasses,
    list_k8s_gateways,
    list_k8s_httproutes,
    get_k8s_node_health_density_summary,
    get_k8s_rollout_health_summary,
    list_k8s_operators,
    list_k8s_statefulsets,
    get_k8s_pvc_workload_summary,
    get_mariadb_node_names,
    get_node_k8s_detail,
    get_node_pod_capacity_summary,
    get_nodes,
    get_pods_on_node,
    list_k8s_crds,
    list_k8s_namespaces,
    list_k8s_pods,
    list_k8s_pvcs,
    list_k8s_pvs,
    list_k8s_services,
)
from .ovn_ops import (
    get_ovs_interface_port_bindings,
    get_ovn_edge_nodes,
    get_ovn_logical_router,
    get_ovn_logical_switch,
    get_ovn_port_detail,
    get_ovn_port_logical_switch,
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


def get_node_irq_balance(node_name: str, hostname: str | None = None) -> dict:
    """Return NIC IRQ balance signals via the node agent."""
    try:
        return node_agent_client.get_host_irq_balance(node_name)
    except Exception as exc:
        return {"interfaces": [], "error": str(exc)}


def get_node_sar_trends(node_name: str, hostname: str | None = None) -> dict:
    """Return summarized SAR trends via the node agent."""
    try:
        return node_agent_client.get_host_sar_trends(node_name)
    except Exception as exc:
        return {"summary": None, "interfaces": [], "error": str(exc)}


def _port_interface_candidates(port_id: str, ovs_interface_name: str | None = None) -> list[str]:
    short = (port_id or "")[:11]
    candidates: list[str] = []
    if short:
        candidates.append(f"tap{short}")
    if ovs_interface_name:
        candidates.append(ovs_interface_name)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def get_node_instance_port_stats(
    node_name: str,
    port_ids: list[str],
    auth: K8sAuth | None = None,
    hostname: str | None = None,
) -> dict:
    """Return host interface stats for the requested Neutron ports on one node."""
    try:
        bindings = get_ovs_interface_port_bindings(node_name, auth=auth)
    except Exception as exc:
        return {"ports": [], "error": str(exc)}

    requested_port_ids = [port_id for port_id in port_ids if port_id]
    port_to_candidates = {
        port_id: _port_interface_candidates(port_id, bindings.get(port_id))
        for port_id in requested_port_ids
    }
    interface_names = sorted({name for names in port_to_candidates.values() for name in names})

    try:
        iface_stats = node_agent_client.get_host_interface_stats(node_name, interface_names)
    except Exception as exc:
        return {"ports": [], "error": str(exc)}

    interfaces = iface_stats.get("interfaces", []) or []
    stats_by_name = {item.get("name"): item for item in interfaces if item.get("name")}
    ports: list[dict] = []
    for port_id in requested_port_ids:
        candidates = port_to_candidates.get(port_id, [])
        stats = next((stats_by_name[name] for name in candidates if name in stats_by_name), None)
        iface_name = stats.get("name") if stats else (candidates[0] if candidates else None)
        if not iface_name:
            continue
        ports.append({
            "port_id": port_id,
            "interface_name": iface_name,
            "operstate": stats.get("operstate") if stats else None,
            "rx_bytes": stats.get("rx_bytes") if stats else None,
            "tx_bytes": stats.get("tx_bytes") if stats else None,
            "rx_bytes_per_second": stats.get("rx_bytes_per_second") if stats else None,
            "tx_bytes_per_second": stats.get("tx_bytes_per_second") if stats else None,
        })

    return {
        "ports": ports,
        "error": iface_stats.get("error"),
        "unsupported": bool(iface_stats.get("unsupported")),
        "message": iface_stats.get("message"),
    }


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
