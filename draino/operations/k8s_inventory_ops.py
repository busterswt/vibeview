"""Read-only Kubernetes inventory helpers."""
from __future__ import annotations

from kubernetes import client

from .k8s_ops import K8sAuth, _api_client
from . import k8s_inventory_core as _core
from . import k8s_inventory_resources as _resources
from . import k8s_inventory_rollout as _rollout
from . import k8s_inventory_storage as _storage
from . import k8s_inventory_utils as _utils
from . import k8s_inventory_workloads as _workloads


def _sync_inventory_modules() -> None:
    for module in (_core, _resources, _rollout, _storage, _workloads):
        module._api_client = _api_client
        module.client = client
    _workloads.list_k8s_crds = list_k8s_crds


def get_nodes(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _core.get_nodes(auth=auth)


def get_node_k8s_detail(node_name: str, auth: K8sAuth | None = None) -> dict:
    _sync_inventory_modules()
    return _core.get_node_k8s_detail(node_name=node_name, auth=auth)


def get_node_pod_capacity_summary(auth: K8sAuth | None = None) -> dict[str, dict]:
    _sync_inventory_modules()
    return _core.get_node_pod_capacity_summary(auth=auth)


def _parse_cpu_mcpu(value: str | None) -> int | None:
    return _core._parse_cpu_mcpu(value)


def _parse_memory_mib(value: str | None) -> int | None:
    return _core._parse_memory_mib(value)


def _pressure_conditions(node) -> list[str]:
    return _core._pressure_conditions(node)


def _ready_state(node) -> bool:
    return _core._ready_state(node)


def get_k8s_node_health_density_summary(auth: K8sAuth | None = None) -> dict:
    _sync_inventory_modules()
    return _core.get_k8s_node_health_density_summary(auth=auth)


def get_k8s_rollout_health_summary(auth: K8sAuth | None = None) -> dict:
    _sync_inventory_modules()
    return _rollout.get_k8s_rollout_health_summary(auth=auth)


def _parse_replica_count(value) -> int | None:
    return _storage._parse_replica_count(value)


def _split_node_list(value) -> list[str]:
    return _storage._split_node_list(value)


def _extract_replica_details(pv) -> tuple[int | None, list[str]]:
    return _storage._extract_replica_details(pv)


def _list_longhorn_custom_objects(api, plural: str) -> list[dict]:
    return _storage._list_longhorn_custom_objects(api, plural)


def get_k8s_pvc_workload_summary(auth: K8sAuth | None = None) -> dict:
    _sync_inventory_modules()
    return _storage.get_k8s_pvc_workload_summary(auth=auth)


def get_etcd_node_names(auth: K8sAuth | None = None) -> set[str]:
    _sync_inventory_modules()
    return _utils.get_etcd_node_names(auth=auth)


def get_mariadb_node_names(auth: K8sAuth | None = None) -> set[str]:
    _sync_inventory_modules()
    return _utils.get_mariadb_node_names(auth=auth)


def get_pods_on_node(node_name: str, auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _utils.get_pods_on_node(node_name=node_name, auth=auth)


def _ts(obj) -> str | None:
    return _utils._ts(obj)


def _parse_k8s_timestamp(value):
    return _utils._parse_k8s_timestamp(value)


def _deployment_name_from_rs(name: str) -> str:
    return _utils._deployment_name_from_rs(name)


def _pod_owner_label(pod) -> tuple[str, str]:
    return _utils._pod_owner_label(pod)


def list_k8s_namespaces(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_namespaces(auth=auth)


def list_k8s_pods(namespace: str | None = None, auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_pods(namespace=namespace, auth=auth)


def list_k8s_services(namespace: str | None = None, auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_services(namespace=namespace, auth=auth)


def list_k8s_cluster_networks(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_cluster_networks(auth=auth)


def list_k8s_network_domains(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    _resources.list_k8s_services = list_k8s_services
    _resources.list_k8s_gateways = list_k8s_gateways
    _resources.list_k8s_httproutes = list_k8s_httproutes
    return _resources.list_k8s_network_domains(auth=auth)


def list_k8s_kubeovn_vpcs(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_kubeovn_vpcs(auth=auth)


def list_k8s_kubeovn_subnets(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_kubeovn_subnets(auth=auth)


def list_k8s_kubeovn_vlans(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_kubeovn_vlans(auth=auth)


def list_k8s_kubeovn_provider_networks(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_kubeovn_provider_networks(auth=auth)


def list_k8s_kubeovn_provider_subnets(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return [item for item in list_k8s_kubeovn_subnets(auth=auth) if item.get("provider")]


def list_k8s_kubeovn_ips(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_kubeovn_ips(auth=auth)


def list_k8s_pvs(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _storage.list_k8s_pvs(auth=auth)


def list_k8s_pvcs(namespace: str | None = None, auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    items = get_k8s_pvc_workload_summary(auth=auth).get("items") or []
    if namespace:
        items = [item for item in items if item.get("namespace") == namespace]
    return items


def list_k8s_crds(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_crds(auth=auth)


def _condition_status(conditions, condition_type: str) -> str:
    return _resources._condition_status(conditions, condition_type)


def _condition_status_from_dict_list(conditions: list[dict] | None, condition_type: str) -> str:
    return _resources._condition_status_from_dict_list(conditions, condition_type)


def _safe_list_custom_objects(group: str, version: str, plural: str, *, namespaced: bool, auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources._safe_list_custom_objects(group, version, plural, namespaced=namespaced, auth=auth)


def _safe_list_custom_objects_first(group: str, version: str, plurals: list[str], *, namespaced: bool, auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources._safe_list_custom_objects_first(group, version, plurals, namespaced=namespaced, auth=auth)


def list_k8s_gatewayclasses(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_gatewayclasses(auth=auth)


def list_k8s_gateways(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_gateways(auth=auth)


def list_k8s_httproutes(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _resources.list_k8s_httproutes(auth=auth)


def _selector_text(selector: dict | None) -> str:
    return _workloads._selector_text(selector)


def _template_images(template) -> list[str]:
    return _workloads._template_images(template)


def list_k8s_deployments(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _workloads.list_k8s_deployments(auth=auth)


def list_k8s_statefulsets(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _workloads.list_k8s_statefulsets(auth=auth)


def list_k8s_daemonsets(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _workloads.list_k8s_daemonsets(auth=auth)


def _images_and_version(pod_spec) -> tuple[list[str], str]:
    return _workloads._images_and_version(pod_spec)


def _is_operator_workload(name: str, labels: dict[str, str], images: list[str]) -> bool:
    return _workloads._is_operator_workload(name, labels, images)


def list_k8s_operators(auth: K8sAuth | None = None) -> list[dict]:
    _sync_inventory_modules()
    return _workloads.list_k8s_operators(auth=auth)
