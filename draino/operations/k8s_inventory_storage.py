"""Kubernetes storage inventory helpers."""
from __future__ import annotations

from kubernetes import client

from .k8s_ops import K8sAuth, _api_client
from .k8s_inventory_utils import _ts
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


