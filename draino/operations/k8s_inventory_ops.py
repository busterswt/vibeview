"""Read-only Kubernetes inventory helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import re

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


def get_k8s_rollout_health_summary(auth: K8sAuth | None = None) -> dict:
    """Return workload rollout and recent restart standout data."""
    apps = client.AppsV1Api(_api_client(auth))
    v1 = client.CoreV1Api(_api_client(auth))
    result = {
        "workloads": [],
        "recent_restarts": [],
        "fatal_counts": {},
        "counts": {
            "deployments": 0,
            "statefulsets": 0,
            "daemonsets": 0,
            "broken_rollouts": 0,
            "fatal_signals": 0,
        },
        "error": None,
    }
    try:
        deployments = apps.list_deployment_for_all_namespaces().items
        statefulsets = apps.list_stateful_set_for_all_namespaces().items
        daemonsets = apps.list_daemon_set_for_all_namespaces().items
        pods = v1.list_pod_for_all_namespaces().items
    except Exception as exc:
        result["error"] = str(exc)
        return result

    now = datetime.now(timezone.utc)
    workloads: list[dict] = []
    workload_keys: set[tuple[str, str, str]] = set()

    for item in deployments:
        desired = int(getattr(item.spec, "replicas", None) or 0)
        ready = int(getattr(item.status, "ready_replicas", None) or 0)
        updated = int(getattr(item.status, "updated_replicas", None) or 0)
        available = int(getattr(item.status, "available_replicas", None) or 0)
        unavailable = int(getattr(item.status, "unavailable_replicas", None) or 0)
        risk = "low"
        reasons: list[str] = []
        if unavailable > 0 or ready < desired:
            risk = "high"
            reasons.append("rollout below desired count")
        elif updated < desired or available < desired:
            risk = "medium"
            reasons.append("updated or available replicas lag desired")
        workloads.append({
            "namespace": item.metadata.namespace,
            "name": item.metadata.name,
            "kind": "Deployment",
            "ready": ready,
            "desired": desired,
            "updated": updated,
            "available": available,
            "unavailable": unavailable,
            "revision_drift": "",
            "risk": risk,
            "reason": "; ".join(reasons) or "healthy rollout",
            "created": _ts(item),
        })
        workload_keys.add((item.metadata.namespace, "Deployment", item.metadata.name))

    for item in statefulsets:
        desired = int(getattr(item.spec, "replicas", None) or 0)
        ready = int(getattr(item.status, "ready_replicas", None) or 0)
        current = int(getattr(item.status, "current_replicas", None) or 0)
        updated = int(getattr(item.status, "updated_replicas", None) or 0)
        current_revision = getattr(item.status, "current_revision", None) or ""
        update_revision = getattr(item.status, "update_revision", None) or ""
        drift = current_revision != update_revision and bool(current_revision and update_revision)
        unavailable = max(0, desired - ready)
        risk = "low"
        reasons: list[str] = []
        if drift and ready < desired:
            risk = "high"
            reasons.append("revision drift with incomplete rollout")
        elif ready < desired or updated < desired:
            risk = "medium"
            reasons.append("replicas lag desired state")
        workloads.append({
            "namespace": item.metadata.namespace,
            "name": item.metadata.name,
            "kind": "StatefulSet",
            "ready": ready,
            "desired": desired,
            "updated": updated,
            "available": current,
            "unavailable": unavailable,
            "revision_drift": f"{current_revision} → {update_revision}" if drift else "",
            "risk": risk,
            "reason": "; ".join(reasons) or "healthy rollout",
            "created": _ts(item),
        })
        workload_keys.add((item.metadata.namespace, "StatefulSet", item.metadata.name))

    for item in daemonsets:
        desired = int(getattr(item.status, "desired_number_scheduled", None) or 0)
        current = int(getattr(item.status, "current_number_scheduled", None) or 0)
        ready = int(getattr(item.status, "number_ready", None) or 0)
        available = int(getattr(item.status, "number_available", None) or 0)
        unavailable = int(getattr(item.status, "number_unavailable", None) or 0)
        misscheduled = int(getattr(item.status, "number_misscheduled", None) or 0)
        risk = "low"
        reasons: list[str] = []
        if unavailable > 0 or misscheduled > 0:
            risk = "high" if misscheduled > 0 else "medium"
            if unavailable > 0:
                reasons.append("daemonset coverage gap")
            if misscheduled > 0:
                reasons.append("misscheduled pods present")
        workloads.append({
            "namespace": item.metadata.namespace,
            "name": item.metadata.name,
            "kind": "DaemonSet",
            "ready": ready,
            "desired": desired,
            "updated": current,
            "available": available,
            "unavailable": unavailable,
            "misscheduled": misscheduled,
            "revision_drift": "",
            "risk": risk,
            "reason": "; ".join(reasons) or "healthy coverage",
            "created": _ts(item),
        })
        workload_keys.add((item.metadata.namespace, "DaemonSet", item.metadata.name))

    recent_restarts: list[dict] = []
    fatal_counts: dict[str, int] = {}
    fatal_reasons = {"OOMKilled", "Error", "CrashLoopBackOff", "ImagePullBackOff", "CreateContainerConfigError", "CreateContainerError"}

    for pod in pods:
        namespace = pod.metadata.namespace
        pod_name = pod.metadata.name
        node = getattr(getattr(pod, "spec", None), "node_name", None) or ""
        owner_kind, owner_name = _pod_owner_label(pod)
        statuses = list(getattr(getattr(pod, "status", None), "container_statuses", None) or [])
        statuses.extend(getattr(getattr(pod, "status", None), "init_container_statuses", None) or [])
        restart_count = 0
        last_finished_at: datetime | None = None
        last_reason = ""
        last_exit_code = ""
        risk = "low"
        current_fatal = ""

        for status in statuses:
            restart_count += int(getattr(status, "restart_count", None) or 0)
            last_state = getattr(status, "last_state", None)
            terminated = getattr(last_state, "terminated", None) if last_state else None
            waiting = getattr(getattr(status, "state", None), "waiting", None)
            if waiting and getattr(waiting, "reason", None) in fatal_reasons:
                current_fatal = getattr(waiting, "reason", None) or ""
            finished_at = _parse_k8s_timestamp(getattr(terminated, "finished_at", None) if terminated else None)
            if finished_at and (last_finished_at is None or finished_at > last_finished_at):
                last_finished_at = finished_at
                last_reason = getattr(terminated, "reason", None) or ""
                exit_code = getattr(terminated, "exit_code", None)
                last_exit_code = "" if exit_code is None else str(exit_code)

        if current_fatal:
            last_reason = current_fatal
            risk = "high"
        if last_reason in fatal_reasons:
            risk = "high"
            fatal_counts[last_reason] = fatal_counts.get(last_reason, 0) + 1
        if restart_count <= 0 and not last_reason:
            continue

        minutes_window = ""
        if last_finished_at:
            delta = (now - last_finished_at).total_seconds()
            if delta <= 300:
                minutes_window = "5m"
            elif delta <= 900:
                minutes_window = "15m"
            elif delta <= 1800:
                minutes_window = "30m"
            elif not current_fatal:
                continue
        elif not current_fatal:
            continue

        if risk != "high" and minutes_window == "5m":
            risk = "medium"
        elif risk == "low":
            risk = "info"

        recent_restarts.append({
            "namespace": namespace,
            "pod": pod_name,
            "owner_name": owner_name,
            "owner_kind": owner_kind,
            "node": node,
            "restart_count": restart_count,
            "window": minutes_window or "now",
            "last_reason": last_reason or "Unknown",
            "last_exit_code": last_exit_code or "—",
            "risk": risk,
        })

    workloads.sort(key=lambda item: (0 if item["risk"] == "high" else 1 if item["risk"] == "medium" else 2, item["namespace"], item["name"]))
    recent_restarts.sort(key=lambda item: (0 if item["risk"] == "high" else 1 if item["risk"] == "medium" else 2, {"5m": 0, "15m": 1, "30m": 2}.get(item["window"], 3), -item["restart_count"], item["namespace"], item["pod"]))

    result["workloads"] = workloads
    result["recent_restarts"] = recent_restarts
    result["fatal_counts"] = dict(sorted(fatal_counts.items(), key=lambda item: (-item[1], item[0])))
    result["counts"] = {
        "deployments": len(deployments),
        "statefulsets": len(statefulsets),
        "daemonsets": len(daemonsets),
        "broken_rollouts": sum(1 for item in workloads if item["risk"] in {"high", "medium"}),
        "fatal_signals": sum(fatal_counts.values()),
    }
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


def _list_longhorn_custom_objects(api, plural: str) -> list[dict]:
    for version in ("v1beta2", "v1beta1"):
        try:
            payload = api.list_namespaced_custom_object(
                group="longhorn.io",
                version=version,
                namespace="longhorn-system",
                plural=plural,
            )
        except Exception:
            continue
        if isinstance(payload, dict):
            return list(payload.get("items", []) or [])
    return []


def get_k8s_pvc_workload_summary(auth: K8sAuth | None = None) -> dict:
    """Return live PVC workload and placement data for reporting."""
    api_client = _api_client(auth)
    v1 = client.CoreV1Api(api_client)
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

    longhorn_volumes: dict[str, dict] = {}
    longhorn_volume_by_pv: dict[str, str] = {}
    longhorn_volume_by_pvc: dict[tuple[str, str], str] = {}
    longhorn_replica_nodes: dict[str, list[str]] = {}
    try:
        custom = client.CustomObjectsApi(api_client)
        for item in _list_longhorn_custom_objects(custom, "volumes"):
            metadata = item.get("metadata", {}) if isinstance(item, dict) else {}
            spec = item.get("spec", {}) if isinstance(item, dict) else {}
            status = item.get("status", {}) if isinstance(item, dict) else {}
            volume_name = metadata.get("name") or ""
            if not volume_name:
                continue
            kube_status = status.get("kubernetesStatus", {}) or {}
            pv_name = kube_status.get("pvName") or ""
            pvc_name = kube_status.get("pvcName") or ""
            pvc_namespace = kube_status.get("namespace") or kube_status.get("pvcNamespace") or ""
            longhorn_volumes[volume_name] = {
                "name": volume_name,
                "pv_name": pv_name,
                "pvc_name": pvc_name,
                "pvc_namespace": pvc_namespace,
                "replica_count": _parse_replica_count(spec.get("numberOfReplicas")),
            }
            if pv_name:
                longhorn_volume_by_pv[pv_name] = volume_name
            if pvc_namespace and pvc_name:
                longhorn_volume_by_pvc[(pvc_namespace, pvc_name)] = volume_name
        for item in _list_longhorn_custom_objects(custom, "replicas"):
            spec = item.get("spec", {}) if isinstance(item, dict) else {}
            volume_name = spec.get("volumeName") or ""
            node_id = spec.get("nodeID") or spec.get("nodeId") or spec.get("node") or ""
            if not volume_name or not node_id:
                continue
            longhorn_replica_nodes.setdefault(volume_name, []).append(str(node_id))
    except Exception:
        pass

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
        csi = getattr(getattr(pv, "spec", None), "csi", None) if pv else None
        csi_driver = getattr(csi, "driver", None) or ""
        volume_handle = getattr(csi, "volume_handle", None) or getattr(csi, "volumeHandle", None) or ""
        longhorn_volume_name = ""
        if volume_handle and volume_handle in longhorn_volumes:
            longhorn_volume_name = volume_handle
        elif volume_name and volume_name in longhorn_volume_by_pv:
            longhorn_volume_name = longhorn_volume_by_pv[volume_name]
        elif key in longhorn_volume_by_pvc:
            longhorn_volume_name = longhorn_volume_by_pvc[key]
        elif "longhorn" in str(storageclass).lower() or "longhorn" in str(csi_driver).lower():
            longhorn_volume_name = volume_handle or longhorn_volume_by_pv.get(volume_name, "")
        if longhorn_volume_name:
            replica_nodes = longhorn_replica_nodes.get(longhorn_volume_name, []) or replica_nodes
            longhorn_count = longhorn_volumes.get(longhorn_volume_name, {}).get("replica_count")
            replica_count = len(replica_nodes) or longhorn_count or replica_count
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


def _parse_k8s_timestamp(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _deployment_name_from_rs(name: str) -> str:
    if re.search(r"-[0-9a-f]{8,10}$", name):
        return name.rsplit("-", 1)[0]
    return name


def _pod_owner_label(pod) -> tuple[str, str]:
    owners = getattr(getattr(pod, "metadata", None), "owner_references", None) or []
    if not owners:
        return ("Pod", getattr(getattr(pod, "metadata", None), "name", "") or "")
    owner = owners[0]
    kind = getattr(owner, "kind", "") or "Pod"
    name = getattr(owner, "name", "") or getattr(getattr(pod, "metadata", None), "name", "")
    if kind == "ReplicaSet":
        return ("Deployment", _deployment_name_from_rs(name))
    return (kind, name)


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
    summary = get_k8s_pvc_workload_summary(auth=auth)
    items = summary.get("items") or []
    if namespace:
        items = [item for item in items if item.get("namespace") == namespace]
    return items


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


def _condition_status(conditions, condition_type: str) -> str:
    for cond in conditions or []:
        cond_kind = getattr(cond, "type", None) if not isinstance(cond, dict) else cond.get("type")
        if cond_kind != condition_type:
            continue
        return getattr(cond, "status", None) if not isinstance(cond, dict) else cond.get("status", "")
    return ""


def _condition_status_from_dict_list(conditions: list[dict] | None, condition_type: str) -> str:
    for cond in conditions or []:
        if cond.get("type") == condition_type:
            return str(cond.get("status") or "")
    return ""


def _safe_list_custom_objects(group: str, version: str, plural: str, *, namespaced: bool, auth: K8sAuth | None = None) -> list[dict]:
    api = client.CustomObjectsApi(_api_client(auth))
    if namespaced:
        core = client.CoreV1Api(_api_client(auth))
        items: list[dict] = []
        for namespace in core.list_namespace().items:
            chunk = api.list_namespaced_custom_object(group, version, namespace.metadata.name, plural)
            items.extend(chunk.get("items") or [])
        return items
    return (api.list_cluster_custom_object(group, version, plural) or {}).get("items") or []


def list_k8s_gatewayclasses(auth: K8sAuth | None = None) -> list[dict]:
    result = []
    for item in _safe_list_custom_objects("gateway.networking.k8s.io", "v1", "gatewayclasses", namespaced=False, auth=auth):
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        conditions = status.get("conditions") or []
        result.append({
            "name": (item.get("metadata") or {}).get("name", ""),
            "controller": spec.get("controllerName", ""),
            "accepted": _condition_status_from_dict_list(conditions, "Accepted") or "Unknown",
            "created": ((item.get("metadata") or {}).get("creationTimestamp") or ""),
        })
    return result


def list_k8s_gateways(auth: K8sAuth | None = None) -> list[dict]:
    result = []
    for item in _safe_list_custom_objects("gateway.networking.k8s.io", "v1", "gateways", namespaced=True, auth=auth):
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        addresses = [addr.get("value", "") for addr in (status.get("addresses") or []) if addr.get("value")]
        listeners = status.get("listeners") or []
        attached_routes = sum(int(listener.get("attachedRoutes") or 0) for listener in listeners)
        listener_names = [listener.get("name", "") for listener in listeners if listener.get("name")]
        accepted = _condition_status_from_dict_list(status.get("conditions") or [], "Accepted") or "Unknown"
        programmed = _condition_status_from_dict_list(status.get("conditions") or [], "Programmed") or "Unknown"
        result.append({
            "namespace": metadata.get("namespace", ""),
            "name": metadata.get("name", ""),
            "gateway_class": spec.get("gatewayClassName", ""),
            "addresses": addresses,
            "listener_count": len(spec.get("listeners") or []),
            "listener_names": listener_names,
            "attached_routes": attached_routes,
            "accepted": accepted,
            "programmed": programmed,
            "created": metadata.get("creationTimestamp", ""),
        })
    return result


def list_k8s_httproutes(auth: K8sAuth | None = None) -> list[dict]:
    result = []
    for item in _safe_list_custom_objects("gateway.networking.k8s.io", "v1", "httproutes", namespaced=True, auth=auth):
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        parent_refs = []
        for ref in spec.get("parentRefs") or []:
            name = ref.get("name", "")
            section = ref.get("sectionName", "")
            parent_refs.append(f"{name}/{section}" if section else name)
        backend_refs = []
        for rule in spec.get("rules") or []:
            for backend in rule.get("backendRefs") or []:
                backend_name = backend.get("name", "")
                backend_port = backend.get("port")
                if backend_name:
                    backend_refs.append(f"{backend_name}:{backend_port}" if backend_port else backend_name)
        route_parents = status.get("parents") or []
        accepted = "Unknown"
        resolved_refs = "Unknown"
        if route_parents:
            accepted = _condition_status_from_dict_list(route_parents[0].get("conditions") or [], "Accepted") or "Unknown"
            resolved_refs = _condition_status_from_dict_list(route_parents[0].get("conditions") or [], "ResolvedRefs") or "Unknown"
        result.append({
            "namespace": metadata.get("namespace", ""),
            "name": metadata.get("name", ""),
            "hostnames": spec.get("hostnames") or [],
            "parent_refs": parent_refs,
            "rules": len(spec.get("rules") or []),
            "backend_refs": backend_refs,
            "accepted": accepted,
            "resolved_refs": resolved_refs,
            "created": metadata.get("creationTimestamp", ""),
        })
    return result


def _selector_text(selector: dict | None) -> str:
    match_labels = (selector or {}).get("match_labels")
    if not match_labels and isinstance(selector, dict):
        match_labels = selector.get("matchLabels")
    if not isinstance(match_labels, dict) or not match_labels:
        return "—"
    return ",".join(f"{key}={value}" for key, value in sorted(match_labels.items()))


def _template_images(template) -> list[str]:
    pod_spec = getattr(getattr(template, "spec", None), "containers", None) or []
    return [container.image for container in pod_spec if getattr(container, "image", None)]


def list_k8s_deployments(auth: K8sAuth | None = None) -> list[dict]:
    api = client.AppsV1Api(_api_client(auth))
    result: list[dict] = []
    for item in api.list_deployment_for_all_namespaces().items:
        spec = item.spec
        status = item.status
        strategy = getattr(spec.strategy, "type", None) or "RollingUpdate"
        rolling = getattr(spec.strategy, "rolling_update", None)
        max_unavailable = getattr(rolling, "max_unavailable", None)
        max_surge = getattr(rolling, "max_surge", None)
        result.append({
            "namespace": item.metadata.namespace,
            "name": item.metadata.name,
            "ready": int(getattr(status, "ready_replicas", None) or 0),
            "desired": int(getattr(spec, "replicas", None) or 0),
            "updated": int(getattr(status, "updated_replicas", None) or 0),
            "available": int(getattr(status, "available_replicas", None) or 0),
            "unavailable": int(getattr(status, "unavailable_replicas", None) or 0),
            "strategy": strategy,
            "max_unavailable": str(max_unavailable) if max_unavailable is not None else "—",
            "max_surge": str(max_surge) if max_surge is not None else "—",
            "selector": _selector_text(getattr(spec, "selector", None)),
            "images": _template_images(spec.template),
            "created": _ts(item),
        })
    return result


def list_k8s_statefulsets(auth: K8sAuth | None = None) -> list[dict]:
    api = client.AppsV1Api(_api_client(auth))
    result: list[dict] = []
    for item in api.list_stateful_set_for_all_namespaces().items:
        spec = item.spec
        status = item.status
        result.append({
            "namespace": item.metadata.namespace,
            "name": item.metadata.name,
            "ready": int(getattr(status, "ready_replicas", None) or 0),
            "desired": int(getattr(spec, "replicas", None) or 0),
            "current": int(getattr(status, "current_replicas", None) or 0),
            "updated": int(getattr(status, "updated_replicas", None) or 0),
            "service_name": getattr(spec, "service_name", None) or "—",
            "update_strategy": getattr(getattr(spec, "update_strategy", None), "type", None) or "RollingUpdate",
            "current_revision": getattr(status, "current_revision", None) or "—",
            "update_revision": getattr(status, "update_revision", None) or "—",
            "pvc_templates": [tpl.metadata.name for tpl in (getattr(spec, "volume_claim_templates", None) or []) if getattr(getattr(tpl, "metadata", None), "name", None)],
            "selector": _selector_text(getattr(spec, "selector", None)),
            "images": _template_images(spec.template),
            "created": _ts(item),
        })
    return result


def list_k8s_daemonsets(auth: K8sAuth | None = None) -> list[dict]:
    api = client.AppsV1Api(_api_client(auth))
    result: list[dict] = []
    for item in api.list_daemon_set_for_all_namespaces().items:
        spec = item.spec
        status = item.status
        node_selector = dict(getattr(getattr(spec, "template", None), "spec", None).node_selector or {})
        tolerations = getattr(getattr(spec.template, "spec", None), "tolerations", None) or []
        result.append({
            "namespace": item.metadata.namespace,
            "name": item.metadata.name,
            "desired": int(getattr(status, "desired_number_scheduled", None) or 0),
            "current": int(getattr(status, "current_number_scheduled", None) or 0),
            "ready": int(getattr(status, "number_ready", None) or 0),
            "available": int(getattr(status, "number_available", None) or 0),
            "unavailable": int(getattr(status, "number_unavailable", None) or 0),
            "misscheduled": int(getattr(status, "number_misscheduled", None) or 0),
            "update_strategy": getattr(getattr(spec, "update_strategy", None), "type", None) or "RollingUpdate",
            "selector": _selector_text(getattr(spec, "selector", None)),
            "node_selector": ",".join(f"{key}={value}" for key, value in sorted(node_selector.items())) or "—",
            "tolerations": len(tolerations),
            "images": _template_images(spec.template),
            "created": _ts(item),
        })
    return result


def _images_and_version(pod_spec) -> tuple[list[str], str]:
    images = [container.image for container in (getattr(pod_spec, "containers", None) or []) if getattr(container, "image", None)]
    versions = []
    for image in images:
        if "@" in image:
            versions.append(image.split("@", 1)[1])
        elif ":" in image.rsplit("/", 1)[-1]:
            versions.append(image.rsplit(":", 1)[1])
        else:
            versions.append("latest")
    unique_versions = sorted(set(versions))
    return images, ", ".join(unique_versions)


def _is_operator_workload(name: str, labels: dict[str, str], images: list[str]) -> bool:
    name_l = name.lower()
    if any(token in name_l for token in ("operator", "controller", "manager")):
        return True
    for value in labels.values():
        text = str(value).lower()
        if any(token in text for token in ("operator", "controller", "manager")):
            return True
    for image in images:
        text = image.lower()
        if any(token in text for token in ("operator", "controller", "manager")):
            return True
    return False


def list_k8s_operators(auth: K8sAuth | None = None) -> list[dict]:
    api = client.AppsV1Api(_api_client(auth))
    crds = list_k8s_crds(auth)

    def _crd_matches(name: str) -> int:
        tokens = [token for token in name.lower().replace("_", "-").split("-") if token and token not in {"operator", "controller", "manager"}]
        if not tokens:
            return 0
        matched = 0
        for crd in crds:
            haystack = " ".join([crd.get("name", ""), crd.get("group", ""), crd.get("kind", "")]).lower()
            if any(token in haystack for token in tokens):
                matched += 1
        return matched

    items: list[dict] = []
    for kind, workload_list in (
        ("Deployment", api.list_deployment_for_all_namespaces().items),
        ("DaemonSet", api.list_daemon_set_for_all_namespaces().items),
        ("StatefulSet", api.list_stateful_set_for_all_namespaces().items),
    ):
        for item in workload_list:
            labels = dict(item.metadata.labels or {})
            images, version = _images_and_version(item.spec.template.spec)
            name = item.metadata.name
            if not _is_operator_workload(name, labels, images):
                continue
            ready = getattr(item.status, "ready_replicas", None) or 0
            desired = (
                getattr(item.status, "replicas", None)
                or getattr(item.status, "desired_number_scheduled", None)
                or getattr(item.spec, "replicas", None)
                or 0
            )
            items.append({
                "namespace": item.metadata.namespace,
                "name": name,
                "kind": kind,
                "ready": f"{ready}/{desired}",
                "version": version or "unknown",
                "images": images,
                "managed_crds": _crd_matches(name),
                "created": _ts(item),
            })
    items.sort(key=lambda entry: (entry["namespace"], entry["name"]))
    return items
