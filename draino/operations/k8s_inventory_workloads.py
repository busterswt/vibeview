"""Kubernetes workload and operator inventory helpers."""
from __future__ import annotations

from kubernetes import client

from .k8s_ops import K8sAuth, _api_client
from .k8s_inventory_resources import list_k8s_crds
from .k8s_inventory_utils import _ts
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
