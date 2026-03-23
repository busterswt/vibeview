"""Kubernetes operations: cordon, drain."""
from __future__ import annotations

import time
from typing import Callable

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

LogFn = Callable[[str], None]

_CONTEXT: str | None = None


def configure(context: str | None = None) -> None:
    global _CONTEXT
    _CONTEXT = context


def _load_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config(context=_CONTEXT)


def get_nodes() -> list[dict]:
    """Return a list of node info dicts."""
    _load_config()
    v1 = client.CoreV1Api()
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
                "ready": ready,
                "ready_since": ready_since,
                "kernel_version": kernel_version,
            }
        )
    return result


def cordon_node(name: str, log: LogFn) -> None:
    """Mark a node unschedulable."""
    _load_config()
    v1 = client.CoreV1Api()
    v1.patch_node(name, {"spec": {"unschedulable": True}})
    log(f"Node '{name}' cordoned successfully")


def uncordon_node(name: str, log: LogFn) -> None:
    """Mark a node schedulable."""
    _load_config()
    v1 = client.CoreV1Api()
    v1.patch_node(name, {"spec": {"unschedulable": False}})
    log(f"Node '{name}' uncordoned successfully")


def drain_node(name: str, log: LogFn, timeout: int = 300) -> None:
    """Evict all non-DaemonSet pods from a node and wait for termination."""
    _load_config()
    v1 = client.CoreV1Api()

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
