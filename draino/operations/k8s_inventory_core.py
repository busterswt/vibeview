"""Core Kubernetes inventory and node health helpers."""
from __future__ import annotations

from kubernetes import client

from .k8s_ops import K8sAuth, _api_client


_COMPUTE_LABEL_KEY = "openstack-compute-node"
_NETWORK_LABEL_KEY = "openstack-network-node"
_ENABLED_LABEL_VALUE = "enabled"
_AZ_ANNOTATION_KEY = "ovn.openstack.org/availability_zones"


def _label_enabled(labels: dict | None, key: str) -> bool:
    value = str((labels or {}).get(key, "")).strip().lower()
    return value == _ENABLED_LABEL_VALUE


def _availability_zone(annotations: dict | None) -> str | None:
    raw = str((annotations or {}).get(_AZ_ANNOTATION_KEY, "")).strip()
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return parts[0] if parts else None


def get_nodes(auth: K8sAuth | None = None) -> list[dict]:
    """Return a list of node info dicts."""
    v1 = client.CoreV1Api(_api_client(auth))
    raw = v1.list_node()
    result: list[dict] = []
    for node in raw.items:
        name: str = node.metadata.name
        labels = node.metadata.labels or {}
        annotations = node.metadata.annotations or {}
        hostname: str = labels.get("kubernetes.io/hostname", name)
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
                        "key": taint.key,
                        "value": taint.value or "",
                        "effect": taint.effect or "",
                    }
                    for taint in (node.spec.taints or [])
                ],
                "ready": ready,
                "ready_since": ready_since,
                "kernel_version": kernel_version,
                "is_compute": _label_enabled(labels, _COMPUTE_LABEL_KEY),
                "is_network": _label_enabled(labels, _NETWORK_LABEL_KEY),
                "availability_zone": _availability_zone(annotations),
            }
        )
    return result


def get_node_k8s_detail(node_name: str, auth: K8sAuth | None = None) -> dict:
    """Return detailed K8s node info for the summary tab."""
    v1 = client.CoreV1Api(_api_client(auth))

    result: dict = {
        "kubelet_version": None,
        "container_runtime": None,
        "os_image": None,
        "architecture": None,
        "cpu_capacity": None,
        "memory_capacity_kb": None,
        "pods_capacity": None,
        "cpu_allocatable": None,
        "memory_allocatable_kb": None,
        "pods_allocatable": None,
        "pod_count": None,
        "roles": [],
        "labels": {},
        "annotations": {},
        "error": None,
    }

    try:
        node = v1.read_node(node_name)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    node_info = node.status.node_info
    if node_info:
        result["kubelet_version"] = node_info.kubelet_version
        result["container_runtime"] = node_info.container_runtime_version
        result["os_image"] = node_info.os_image
        result["architecture"] = node_info.architecture

    def _parse_ki(value: str | None) -> int | None:
        if not value:
            return None
        value = value.strip()
        if value.endswith("Ki"):
            try:
                return int(value[:-2])
            except Exception:
                return None
        if value.endswith("Mi"):
            try:
                return int(value[:-2]) * 1024
            except Exception:
                return None
        if value.endswith("Gi"):
            try:
                return int(value[:-2]) * 1024 * 1024
            except Exception:
                return None
        try:
            return int(value) // 1024
        except Exception:
            return None

    capacity = node.status.capacity or {}
    allocatable = node.status.allocatable or {}
    result["cpu_capacity"] = capacity.get("cpu")
    result["memory_capacity_kb"] = _parse_ki(capacity.get("memory"))
    result["pods_capacity"] = capacity.get("pods")
    result["cpu_allocatable"] = allocatable.get("cpu")
    result["memory_allocatable_kb"] = _parse_ki(allocatable.get("memory"))
    result["pods_allocatable"] = allocatable.get("pods")

    labels = node.metadata.labels or {}
    roles = [
        key.split("/", 1)[1]
        for key in labels
        if key.startswith("node-role.kubernetes.io/")
    ]
    result["roles"] = roles or ["worker"]
    result["labels"] = dict(sorted(labels.items()))
    result["annotations"] = dict(sorted((node.metadata.annotations or {}).items()))

    try:
        pods = v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}")
        result["pod_count"] = sum(1 for pod in pods.items if pod.status.phase not in ("Succeeded", "Failed"))
    except Exception:
        pass

    return result


def get_node_pod_capacity_summary(auth: K8sAuth | None = None) -> dict[str, dict]:
    """Return per-node pod allocatable/count data using bulk K8s queries."""
    v1 = client.CoreV1Api(_api_client(auth))
    result: dict[str, dict] = {}

    try:
        raw_nodes = v1.list_node()
        for node in raw_nodes.items:
            name = node.metadata.name
            allocatable = node.status.allocatable or {}
            result[name] = {
                "pods_allocatable": allocatable.get("pods"),
                "pod_count": 0,
            }
    except Exception:
        return result

    try:
        raw_pods = v1.list_pod_for_all_namespaces()
        for pod in raw_pods.items:
            if pod.status.phase in ("Succeeded", "Failed"):
                continue
            node_name = pod.spec.node_name if pod.spec else None
            if not node_name:
                continue
            entry = result.setdefault(node_name, {"pods_allocatable": None, "pod_count": 0})
            entry["pod_count"] += 1
    except Exception:
        pass

    return result


def _parse_cpu_mcpu(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("m"):
            return int(float(text[:-1]))
        return int(float(text) * 1000.0)
    except Exception:
        return None


def _parse_memory_mib(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    units = {
        "Ki": 1 / 1024,
        "Mi": 1,
        "Gi": 1024,
        "Ti": 1024 * 1024,
        "K": 1000 / (1024 * 1024),
        "M": 1000 * 1000 / (1024 * 1024),
        "G": 1000 * 1000 * 1000 / (1024 * 1024),
    }
    try:
        for suffix, multiplier in units.items():
            if text.endswith(suffix):
                return int(float(text[:-len(suffix)]) * multiplier)
        return int(int(text) / (1024 * 1024))
    except Exception:
        return None


def _pressure_conditions(node) -> list[str]:
    conditions: list[str] = []
    for cond in node.status.conditions or []:
        if cond.type == "Ready":
            continue
        if cond.status == "True":
            conditions.append(cond.type)
    return conditions


def _ready_state(node) -> bool:
    for cond in node.status.conditions or []:
        if cond.type == "Ready":
            return cond.status == "True"
    return False


def get_k8s_node_health_density_summary(auth: K8sAuth | None = None) -> dict:
    """Return bulk Kubernetes node/workload density data for reporting."""
    v1 = client.CoreV1Api(_api_client(auth))
    result = {
        "nodes": [],
        "version_counts": {},
        "condition_counts": {},
        "error": None,
    }
    try:
        raw_nodes = v1.list_node()
        raw_pods = v1.list_pod_for_all_namespaces()
        raw_pvcs = v1.list_persistent_volume_claim_for_all_namespaces()
    except Exception as exc:
        result["error"] = str(exc)
        return result

    pvc_map: dict[tuple[str, str], dict] = {}
    for pvc in raw_pvcs.items:
        pvc_map[(pvc.metadata.namespace, pvc.metadata.name)] = {
            "storageclass": pvc.spec.storage_class_name or "",
        }

    node_data: dict[str, dict] = {}
    for node in raw_nodes.items:
        name = node.metadata.name
        info = node.status.node_info
        allocatable = node.status.allocatable or {}
        kubelet_version = info.kubelet_version if info else ""
        runtime = info.container_runtime_version if info else ""
        ready = _ready_state(node)
        conditions = _pressure_conditions(node)
        for cond in conditions:
            result["condition_counts"][cond] = result["condition_counts"].get(cond, 0) + 1
        if not ready:
            result["condition_counts"]["NotReady"] = result["condition_counts"].get("NotReady", 0) + 1
        if bool(node.spec.unschedulable):
            result["condition_counts"]["Cordoned"] = result["condition_counts"].get("Cordoned", 0) + 1
        result["version_counts"][kubelet_version] = result["version_counts"].get(kubelet_version, 0) + 1
        node_data[name] = {
            "node": name,
            "ready": ready,
            "kubelet_version": kubelet_version or "unknown",
            "container_runtime": runtime or "unknown",
            "runtime_label": (runtime or "unknown").split("://", 1)[0],
            "pods_allocatable": allocatable.get("pods"),
            "cpu_allocatable_mcpu": _parse_cpu_mcpu(allocatable.get("cpu")),
            "memory_allocatable_mib": _parse_memory_mib(allocatable.get("memory")),
            "pod_count": 0,
            "pvc_pod_count": 0,
            "pvc_claim_count": 0,
            "namespace_count": 0,
            "cpu_requests_mcpu": 0,
            "memory_requests_mib": 0,
            "conditions": conditions,
            "cordoned": bool(node.spec.unschedulable),
            "_namespaces": set(),
            "_pvc_claims": set(),
        }

    for pod in raw_pods.items:
        if pod.status.phase in ("Succeeded", "Failed"):
            continue
        node_name = pod.spec.node_name if pod.spec else None
        if not node_name or node_name not in node_data:
            continue
        item = node_data[node_name]
        item["pod_count"] += 1
        item["_namespaces"].add(pod.metadata.namespace)

        pod_has_pvc = False
        for volume in pod.spec.volumes or []:
            claim = getattr(volume, "persistent_volume_claim", None)
            if not claim or not claim.claim_name:
                continue
            pod_has_pvc = True
            item["_pvc_claims"].add((pod.metadata.namespace, claim.claim_name))
            _ = pvc_map.get((pod.metadata.namespace, claim.claim_name))
        if pod_has_pvc:
            item["pvc_pod_count"] += 1

        for container in pod.spec.containers or []:
            requests = (container.resources.requests or {}) if container.resources else {}
            item["cpu_requests_mcpu"] += _parse_cpu_mcpu(requests.get("cpu")) or 0
            item["memory_requests_mib"] += _parse_memory_mib(requests.get("memory")) or 0

    for item in node_data.values():
        item["namespace_count"] = len(item.pop("_namespaces"))
        item["pvc_claim_count"] = len(item.pop("_pvc_claims"))

    result["nodes"] = sorted(node_data.values(), key=lambda item: item["node"])
    return result

