"""Kubernetes rollout and restart health helpers."""
from __future__ import annotations

from datetime import datetime, timezone

from kubernetes import client

from .k8s_ops import K8sAuth, _api_client
from .k8s_inventory_utils import _parse_k8s_timestamp, _pod_owner_label, _ts
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


