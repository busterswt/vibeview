"""Kubernetes resource, Kube-OVN, and Gateway API inventory helpers."""
from __future__ import annotations

import ipaddress

from kubernetes import client

from .k8s_ops import K8sAuth, _api_client
from .k8s_inventory_utils import _deployment_name_from_rs, _ts
from .k8s_inventory_storage import get_k8s_pvc_workload_summary
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


def list_k8s_cluster_networks(auth: K8sAuth | None = None) -> list[dict]:
    v1 = client.CoreV1Api(_api_client(auth))
    try:
        raw_nodes = v1.list_node().items
        raw_services = v1.list_service_for_all_namespaces().items
    except Exception:
        return []

    pod_cidrs: dict[str, dict] = {}
    for node in raw_nodes:
        cidrs = list(getattr(getattr(node, "spec", None), "pod_cidrs", None) or [])
        single = getattr(getattr(node, "spec", None), "pod_cidr", None)
        if single and single not in cidrs:
            cidrs.append(single)
        for cidr in [str(value).strip() for value in cidrs if str(value).strip()]:
            entry = pod_cidrs.setdefault(cidr, {"nodes": set()})
            entry["nodes"].add(node.metadata.name)

    lb_ips: list[str] = []
    for service in raw_services:
        status = getattr(service, "status", None)
        lb = getattr(status, "load_balancer", None)
        ingress = getattr(lb, "ingress", None) or []
        for item in ingress:
            value = getattr(item, "ip", None) or getattr(item, "hostname", None) or ""
            if value:
                lb_ips.append(str(value))

    result: list[dict] = []
    for cidr, meta in sorted(pod_cidrs.items()):
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except Exception:
            network = None
        lb_matches = 0
        if network and network.version == 4:
            for value in lb_ips:
                try:
                    if ipaddress.ip_address(value) in network:
                        lb_matches += 1
                except Exception:
                    continue
        result.append({
            "name": f"pod-network-{cidr}",
            "network_type": "Pod CIDR",
            "cidr": cidr,
            "node_count": len(meta["nodes"]),
            "nodes": sorted(meta["nodes"]),
            "load_balancer_ips": lb_matches,
        })
    return result


def list_k8s_network_domains(auth: K8sAuth | None = None) -> list[dict]:
    services = list_k8s_services(auth=auth)
    gateways = list_k8s_gateways(auth=auth)
    routes = list_k8s_httproutes(auth=auth)

    domains: dict[str, dict] = {}
    for service in services:
        namespace = service.get("namespace") or "default"
        item = domains.setdefault(namespace, {
            "namespace": namespace,
            "service_count": 0,
            "lb_count": 0,
            "gateway_count": 0,
            "route_count": 0,
            "_external_endpoints": set(),
            "_service_names": set(),
            "_gateway_names": set(),
            "_route_names": set(),
        })
        item["service_count"] += 1
        item["_service_names"].add(service.get("name") or "")
        if service.get("type") == "LoadBalancer":
            item["lb_count"] += 1
        for value in service.get("external_ips") or []:
            if value:
                item["_external_endpoints"].add(str(value))

    for gateway in gateways:
        namespace = gateway.get("namespace") or "default"
        item = domains.setdefault(namespace, {
            "namespace": namespace,
            "service_count": 0,
            "lb_count": 0,
            "gateway_count": 0,
            "route_count": 0,
            "_external_endpoints": set(),
            "_service_names": set(),
            "_gateway_names": set(),
            "_route_names": set(),
        })
        item["gateway_count"] += 1
        item["_gateway_names"].add(gateway.get("name") or "")
        for value in gateway.get("addresses") or []:
            if value:
                item["_external_endpoints"].add(str(value))

    for route in routes:
        namespace = route.get("namespace") or "default"
        item = domains.setdefault(namespace, {
            "namespace": namespace,
            "service_count": 0,
            "lb_count": 0,
            "gateway_count": 0,
            "route_count": 0,
            "_external_endpoints": set(),
            "_service_names": set(),
            "_gateway_names": set(),
            "_route_names": set(),
        })
        item["route_count"] += 1
        item["_route_names"].add(route.get("name") or "")

    result: list[dict] = []
    for namespace, item in sorted(domains.items()):
        result.append({
            "namespace": namespace,
            "name": namespace,
            "service_count": item["service_count"],
            "lb_count": item["lb_count"],
            "gateway_count": item["gateway_count"],
            "route_count": item["route_count"],
            "external_endpoints": sorted(item["_external_endpoints"]),
            "service_names": sorted(value for value in item["_service_names"] if value),
            "gateway_names": sorted(value for value in item["_gateway_names"] if value),
            "route_names": sorted(value for value in item["_route_names"] if value),
        })
    return result


def list_k8s_kubeovn_vpcs(auth: K8sAuth | None = None) -> list[dict]:
    result = []
    for item in _safe_list_custom_objects("kubeovn.io", "v1", "vpcs", namespaced=False, auth=auth):
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        namespaces = sorted(str(value) for value in (spec.get("namespaces") or []) if str(value))
        static_routes = spec.get("staticRoutes") or spec.get("static_routes") or []
        policy_routes = spec.get("policyRoutes") or spec.get("policy_routes") or []
        subnets = sorted(str(value) for value in (status.get("subnets") or spec.get("subnets") or []) if str(value))
        result.append({
            "name": metadata.get("name", ""),
            "default": bool(spec.get("default")),
            "namespace_count": len(namespaces),
            "namespaces": namespaces,
            "subnet_count": len(subnets),
            "subnets": subnets,
            "static_route_count": len(static_routes),
            "policy_route_count": len(policy_routes),
            "standby": bool(status.get("standby", False)),
            "created": metadata.get("creationTimestamp", ""),
        })
    return result


def list_k8s_kubeovn_subnets(auth: K8sAuth | None = None) -> list[dict]:
    result = []
    for item in _safe_list_custom_objects("kubeovn.io", "v1", "subnets", namespaced=False, auth=auth):
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        namespaces = sorted(str(value) for value in (spec.get("namespaces") or []) if str(value))
        exclude_ips = spec.get("excludeIps") or spec.get("exclude_ips") or []
        result.append({
            "name": metadata.get("name", ""),
            "cidr": spec.get("cidrBlock", "") or spec.get("cidr", ""),
            "gateway": spec.get("gateway", ""),
            "protocol": spec.get("protocol", ""),
            "vpc": spec.get("vpc", "") or "ovn-cluster",
            "provider": spec.get("provider", ""),
            "nat_outgoing": bool(spec.get("natOutgoing")),
            "private": bool(spec.get("private")),
            "default": bool(spec.get("default")),
            "namespace_count": len(namespaces),
            "namespaces": namespaces,
            "exclude_ip_count": len(exclude_ips),
            "available_ips": status.get("availableIPs", "") or status.get("availableIps", ""),
            "used_ips": status.get("usingIPs", "") or status.get("usingIps", ""),
            "created": metadata.get("creationTimestamp", ""),
        })
    return result


def list_k8s_kubeovn_vlans(auth: K8sAuth | None = None) -> list[dict]:
    result = []
    for item in _safe_list_custom_objects("kubeovn.io", "v1", "vlans", namespaced=False, auth=auth):
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        subnets = sorted(str(value) for value in (status.get("subnets") or spec.get("subnets") or []) if str(value))
        result.append({
            "name": metadata.get("name", ""),
            "provider": spec.get("provider", ""),
            "vlan_id": spec.get("id", "") or spec.get("vlanId", "") or spec.get("vlanID", "") or spec.get("vlan", ""),
            "subnet_count": len(subnets),
            "subnets": subnets,
            "created": metadata.get("creationTimestamp", ""),
        })
    return result


def list_k8s_kubeovn_provider_networks(auth: K8sAuth | None = None) -> list[dict]:
    result = []
    items = _safe_list_custom_objects_first("kubeovn.io", "v1", ["provider-networks", "providernetworks"], namespaced=False, auth=auth)
    for item in items:
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        ready_nodes = sorted(str(value) for value in (status.get("readyNodes") or status.get("ready_nodes") or []) if str(value))
        exclude_nodes = sorted(str(value) for value in (spec.get("excludeNodes") or spec.get("exclude_nodes") or []) if str(value))
        custom_interfaces = spec.get("customInterfaces") or spec.get("custom_interfaces") or {}
        result.append({
            "name": metadata.get("name", ""),
            "default_interface": spec.get("defaultInterface", "") or spec.get("default_interface", ""),
            "nic_count": len(custom_interfaces),
            "exclude_node_count": len(exclude_nodes),
            "ready_node_count": len(ready_nodes),
            "exclude_nodes": exclude_nodes,
            "ready_nodes": ready_nodes,
            "created": metadata.get("creationTimestamp", ""),
        })
    return result


def list_k8s_kubeovn_provider_subnets(auth: K8sAuth | None = None) -> list[dict]:
    return [item for item in list_k8s_kubeovn_subnets(auth=auth) if item.get("provider")]


def list_k8s_kubeovn_ips(auth: K8sAuth | None = None) -> list[dict]:
    result = []
    for item in _safe_list_custom_objects("kubeovn.io", "v1", "ips", namespaced=False, auth=auth):
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        result.append({
            "name": metadata.get("name", ""),
            "namespace": spec.get("namespace", "") or metadata.get("namespace", ""),
            "pod_name": spec.get("podName", "") or spec.get("pod_name", ""),
            "node_name": spec.get("nodeName", "") or spec.get("node_name", ""),
            "subnet": spec.get("subnet", ""),
            "v4_ip": spec.get("v4IPAddress", "") or spec.get("v4IpAddress", "") or spec.get("ipAddress", ""),
            "v6_ip": spec.get("v6IPAddress", "") or spec.get("v6IpAddress", ""),
            "mac_address": spec.get("macAddress", "") or spec.get("mac_address", ""),
            "attach_subnets": sorted(str(value) for value in (spec.get("attachSubnets") or spec.get("attach_subnets") or []) if str(value)),
            "attach_ips": sorted(str(value) for value in (spec.get("attachIPs") or spec.get("attach_ips") or []) if str(value)),
            "created": metadata.get("creationTimestamp", ""),
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


def _safe_list_custom_objects_first(group: str, version: str, plurals: list[str], *, namespaced: bool, auth: K8sAuth | None = None) -> list[dict]:
    for plural in plurals:
        try:
            items = _safe_list_custom_objects(group, version, plural, namespaced=namespaced, auth=auth)
        except Exception:
            continue
        if items:
            return items
    return []


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


