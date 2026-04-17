"""Shared Kubernetes inventory utility helpers."""
from __future__ import annotations

from datetime import datetime, timezone

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


