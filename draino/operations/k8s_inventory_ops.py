"""Read-only Kubernetes inventory helpers."""
from __future__ import annotations

from kubernetes import client

from .k8s_ops import K8sAuth, _api_client


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
                        "key": taint.key,
                        "value": taint.value or "",
                        "effect": taint.effect or "",
                    }
                    for taint in (node.spec.taints or [])
                ],
                "ready": ready,
                "ready_since": ready_since,
                "kernel_version": kernel_version,
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


def get_etcd_node_names(auth: K8sAuth | None = None) -> set[str]:
    """Return the set of node names in the etcd role."""
    v1 = client.CoreV1Api(_api_client(auth))
    result: set[str] = set()
    try:
        nodes = v1.list_node(label_selector="node-role.kubernetes.io/etcd")
        for node in nodes.items:
            result.add(node.metadata.name)
    except Exception:
        pass
    return result


def get_mariadb_node_names(auth: K8sAuth | None = None) -> set[str]:
    """Return node names currently hosting mariadb-cluster pods."""
    v1 = client.CoreV1Api(_api_client(auth))
    result: set[str] = set()
    raw = v1.list_pod_for_all_namespaces()

    def _looks_like_mariadb(pod) -> bool:
        labels = pod.metadata.labels or {}
        pod_name = (pod.metadata.name or "").lower()
        instance_label = str(labels.get("app.kubernetes.io/instance", "")).lower()
        label_values = " ".join(str(value).lower() for value in labels.values())
        haystack = " ".join(part for part in (pod_name, label_values) if part)

        # Backup and restore jobs often reuse MariaDB images/labels but should
        # not mark the node as hosting the active cluster.
        if any(token in haystack for token in ("backup", "restore")):
            return False

        if instance_label == "mariadb-cluster":
            return True
        return "mariadb-cluster" in pod_name

    for pod in raw.items:
        if pod.status.phase in ("Succeeded", "Failed"):
            continue
        if not pod.spec or not pod.spec.node_name:
            continue
        if _looks_like_mariadb(pod):
            result.add(pod.spec.node_name)
    return result


def get_pods_on_node(node_name: str, auth: K8sAuth | None = None) -> list[dict]:
    """Return pod info dicts for all pods scheduled on a node."""
    v1 = client.CoreV1Api(_api_client(auth))
    raw = v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}")
    result: list[dict] = []
    for pod in raw.items:
        ready_count = 0
        total_count = 0
        restarts = 0
        if pod.status.container_statuses:
            for container_status in pod.status.container_statuses:
                total_count += 1
                if container_status.ready:
                    ready_count += 1
                restarts += container_status.restart_count or 0
        elif pod.spec.containers:
            total_count = len(pod.spec.containers)
        result.append({
            "namespace": pod.metadata.namespace,
            "name": pod.metadata.name,
            "phase": pod.status.phase or "Unknown",
            "ready_count": ready_count,
            "total_count": total_count,
            "restarts": restarts,
            "created_at": pod.metadata.creation_timestamp,
        })
    return result


def _ts(obj) -> str | None:
    timestamp = obj.metadata.creation_timestamp if obj and obj.metadata else None
    return timestamp.isoformat() if timestamp else None


def list_k8s_namespaces(auth: K8sAuth | None = None) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    return [
        {
            "name": namespace.metadata.name,
            "status": namespace.status.phase or "Active",
            "created": _ts(namespace),
            "labels": dict(namespace.metadata.labels or {}),
        }
        for namespace in v1.list_namespace().items
    ]


def list_k8s_pods(namespace: str | None = None, auth: K8sAuth | None = None) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    raw = v1.list_pod_for_all_namespaces() if not namespace else v1.list_namespaced_pod(namespace)
    result = []
    for pod in raw.items:
        total = len(pod.spec.containers or [])
        ready = 0
        restarts = 0
        if pod.status.container_statuses:
            for container_status in pod.status.container_statuses:
                if container_status.ready:
                    ready += 1
                restarts += container_status.restart_count or 0
        result.append({
            "namespace": pod.metadata.namespace,
            "name": pod.metadata.name,
            "phase": pod.status.phase or "Unknown",
            "ready": f"{ready}/{total}",
            "restarts": restarts,
            "node": pod.spec.node_name or "",
            "created": _ts(pod),
        })
    return result


def list_k8s_services(namespace: str | None = None, auth: K8sAuth | None = None) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    raw = v1.list_service_for_all_namespaces() if not namespace else v1.list_namespaced_service(namespace)
    result = []
    for service in raw.items:
        ports = ", ".join(
            f"{port.port}{'/' + port.protocol if port.protocol != 'TCP' else ''}"
            + (f":{port.node_port}" if port.node_port else "")
            for port in (service.spec.ports or [])
        )
        external_ips: list[str] = []
        if service.status.load_balancer and service.status.load_balancer.ingress:
            external_ips = [ingress.ip or ingress.hostname or "" for ingress in service.status.load_balancer.ingress]
        result.append({
            "namespace": service.metadata.namespace,
            "name": service.metadata.name,
            "type": service.spec.type or "ClusterIP",
            "cluster_ip": service.spec.cluster_ip or "",
            "external_ips": [value for value in external_ips if value],
            "ports": ports,
            "created": _ts(service),
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
            "name": pv.metadata.name,
            "capacity": (pv.spec.capacity or {}).get("storage", ""),
            "access_modes": ",".join(pv.spec.access_modes or []),
            "reclaim_policy": pv.spec.persistent_volume_reclaim_policy or "",
            "status": pv.status.phase or "",
            "claim": claim,
            "storageclass": pv.spec.storage_class_name or "",
            "created": _ts(pv),
        })
    return result


def list_k8s_pvcs(namespace: str | None = None, auth: K8sAuth | None = None) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    raw = (
        v1.list_persistent_volume_claim_for_all_namespaces()
        if not namespace
        else v1.list_namespaced_persistent_volume_claim(namespace)
    )
    result = []
    for pvc in raw.items:
        result.append({
            "namespace": pvc.metadata.namespace,
            "name": pvc.metadata.name,
            "status": pvc.status.phase or "",
            "volume": pvc.spec.volume_name or "",
            "capacity": (pvc.status.capacity or {}).get("storage", ""),
            "access_modes": ",".join(pvc.spec.access_modes or []),
            "storageclass": pvc.spec.storage_class_name or "",
            "created": _ts(pvc),
        })
    return result


def list_k8s_crds(auth: K8sAuth | None = None) -> list[dict]:
    api = client.ApiextensionsV1Api(_api_client(auth))
    result = []
    for crd in api.list_custom_resource_definition().items:
        spec = crd.spec
        versions = [version.name for version in (spec.versions or []) if version.served]
        result.append({
            "name": crd.metadata.name,
            "group": spec.group,
            "kind": spec.names.kind,
            "scope": spec.scope,
            "versions": versions,
            "created": _ts(crd),
        })
    return result
