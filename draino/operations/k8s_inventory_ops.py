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


def _parse_replica_count(value) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits)
    except Exception:
        return None


def _split_node_list(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip().strip("[]")
    if not text:
        return []
    parts = [part.strip().strip('"').strip("'") for part in text.replace(";", ",").split(",")]
    return [part for part in parts if part]


def _extract_replica_details(pv) -> tuple[int | None, list[str]]:
    candidates: list[dict] = []
    spec = getattr(pv, "spec", None)
    metadata = getattr(pv, "metadata", None)
    csi = getattr(spec, "csi", None) if spec else None
    if csi and getattr(csi, "volume_attributes", None):
        candidates.append(dict(csi.volume_attributes))
    if metadata and getattr(metadata, "annotations", None):
        candidates.append(dict(metadata.annotations))

    replica_count = None
    replica_nodes: list[str] = []
    count_keys = (
        "numberOfReplicas",
        "number_of_replicas",
        "replicaCount",
        "replicas",
    )
    node_keys = (
        "replicaNodes",
        "replica_nodes",
        "replicaNodeNames",
        "replica_node_names",
        "nodes",
    )
    for mapping in candidates:
        lower_map = {str(key).lower(): value for key, value in mapping.items()}
        if replica_count is None:
            for key in count_keys:
                replica_count = _parse_replica_count(lower_map.get(key.lower()))
                if replica_count is not None:
                    break
        if not replica_nodes:
            for key in node_keys:
                replica_nodes = _split_node_list(lower_map.get(key.lower()))
                if replica_nodes:
                    break
        if replica_count is not None and replica_nodes:
            break

    node_affinity = getattr(spec, "node_affinity", None) if spec else None
    if not replica_nodes and node_affinity:
        required = getattr(node_affinity, "required", None)
        terms = getattr(required, "node_selector_terms", None) or []
        for term in terms:
            for expr in getattr(term, "match_expressions", None) or []:
                key = getattr(expr, "key", "")
                if key in ("kubernetes.io/hostname", "node", "topology.kubernetes.io/hostname"):
                    replica_nodes = [str(value).strip() for value in (getattr(expr, "values", None) or []) if str(value).strip()]
                    break
            if replica_nodes:
                break

    return replica_count, replica_nodes


def get_k8s_pvc_workload_summary(auth: K8sAuth | None = None) -> dict:
    """Return live PVC workload and placement data for reporting."""
    v1 = client.CoreV1Api(_api_client(auth))
    result = {
        "items": [],
        "storage_classes": [],
        "replica_nodes": [],
        "error": None,
    }
    try:
        raw_pods = v1.list_pod_for_all_namespaces()
        raw_pvcs = v1.list_persistent_volume_claim_for_all_namespaces()
        raw_pvs = v1.list_persistent_volume()
    except Exception as exc:
        result["error"] = str(exc)
        return result

    pv_map = {pv.metadata.name: pv for pv in raw_pvs.items}
    pvc_consumers: dict[tuple[str, str], list[dict]] = {}
    node_replica_counts: dict[str, dict[str, set]] = {}

    for pod in raw_pods.items:
        if pod.status.phase in ("Succeeded", "Failed"):
            continue
        pod_name = pod.metadata.name
        namespace = pod.metadata.namespace
        node_name = getattr(pod.spec, "node_name", None) or ""
        for volume in getattr(pod.spec, "volumes", None) or []:
            claim = getattr(volume, "persistent_volume_claim", None)
            if not claim or not claim.claim_name:
                continue
            key = (namespace, claim.claim_name)
            pvc_consumers.setdefault(key, []).append({
                "pod": pod_name,
                "node": node_name,
            })

    items: list[dict] = []
    storage_class_summary: dict[str, dict] = {}
    for pvc in raw_pvcs.items:
        namespace = pvc.metadata.namespace
        name = pvc.metadata.name
        key = (namespace, name)
        volume_name = pvc.spec.volume_name or ""
        pv = pv_map.get(volume_name)
        storageclass = pvc.spec.storage_class_name or (pv.spec.storage_class_name if pv and pv.spec else "") or ""
        consumers = pvc_consumers.get(key, [])
        consumer_nodes = sorted({item["node"] for item in consumers if item.get("node")})
        consumer_pods = [item["pod"] for item in consumers if item.get("pod")]
        replica_count, replica_nodes = _extract_replica_details(pv) if pv else (None, [])
        item = {
            "namespace": namespace,
            "name": name,
            "status": pvc.status.phase or "",
            "volume": volume_name,
            "capacity": (pvc.status.capacity or {}).get("storage", ""),
            "access_modes": ",".join(pvc.spec.access_modes or []),
            "storageclass": storageclass,
            "replica_count": replica_count,
            "replica_nodes": replica_nodes,
            "consumer_pods": consumer_pods,
            "consumer_nodes": consumer_nodes,
            "consumer_count": len(consumers),
        }
        items.append(item)

        sc_entry = storage_class_summary.setdefault(storageclass or "—", {
            "storageclass": storageclass or "—",
            "pvc_count": 0,
            "replica_counts": [],
            "consumer_nodes": {},
        })
        sc_entry["pvc_count"] += 1
        if replica_count is not None:
            sc_entry["replica_counts"].append(replica_count)
        for node_name in consumer_nodes:
            sc_entry["consumer_nodes"][node_name] = sc_entry["consumer_nodes"].get(node_name, 0) + 1

        for node_name in replica_nodes:
            node_entry = node_replica_counts.setdefault(node_name, {"pvcs": set(), "consumers": set(), "namespaces": set()})
            node_entry["pvcs"].add(f"{namespace}/{name}")
            node_entry["namespaces"].add(namespace)
        for node_name in consumer_nodes:
            node_entry = node_replica_counts.setdefault(node_name, {"pvcs": set(), "consumers": set(), "namespaces": set()})
            node_entry["consumers"].add(f"{namespace}/{name}")
            node_entry["namespaces"].add(namespace)

    storage_classes = []
    for item in storage_class_summary.values():
        consumer_nodes = sorted(item["consumer_nodes"].items(), key=lambda entry: (-entry[1], entry[0]))
        avg_replicas = None
        if item["replica_counts"]:
            avg_replicas = round(sum(item["replica_counts"]) / len(item["replica_counts"]), 1)
        storage_classes.append({
            "storageclass": item["storageclass"],
            "pvc_count": item["pvc_count"],
            "typical_replicas": avg_replicas,
            "top_consumer_nodes": ", ".join(node for node, _count in consumer_nodes[:3]),
        })
    storage_classes.sort(key=lambda item: (-item["pvc_count"], item["storageclass"]))

    replica_nodes = [
        {
            "node": node,
            "pvc_count": len(values["pvcs"]),
            "consumer_count": len(values["consumers"]),
            "namespace_count": len(values["namespaces"]),
        }
        for node, values in node_replica_counts.items()
    ]
    replica_nodes.sort(key=lambda item: (-item["pvc_count"], -item["consumer_count"], item["node"]))

    result["items"] = sorted(items, key=lambda item: (item["namespace"], item["name"]))
    result["storage_classes"] = storage_classes
    result["replica_nodes"] = replica_nodes
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
