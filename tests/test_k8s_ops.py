from __future__ import annotations

from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException

from draino.operations import k8s_inventory_ops, k8s_ops


def _pod(
    namespace: str,
    name: str,
    *,
    phase: str = "Running",
    owners: list[str] | None = None,
    annotations: dict[str, str] | None = None,
    deletion_timestamp=None,
):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            namespace=namespace,
            name=name,
            owner_references=[
                SimpleNamespace(kind=kind) for kind in (owners or [])
            ],
            annotations=annotations or {},
            deletion_timestamp=deletion_timestamp,
        ),
        status=SimpleNamespace(phase=phase),
    )


def test_drain_node_skips_daemonsets_and_completes(monkeypatch):
    logs: list[str] = []
    pods = [
        _pod("default", "app-1"),
        _pod("kube-system", "ds-1", owners=["DaemonSet"]),
    ]
    remaining = [[pods[1]]]
    evicted: list[tuple[str, str]] = []

    class FakeCore:
        def list_pod_for_all_namespaces(self, field_selector=None):
            if remaining:
                return SimpleNamespace(items=pods if len(evicted) == 0 else remaining.pop(0))
            return SimpleNamespace(items=[])

        def create_namespaced_pod_eviction(self, name, namespace, body):
            evicted.append((namespace, name))

    monkeypatch.setattr(k8s_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_ops.client, "CoreV1Api", lambda api_client: FakeCore())

    k8s_ops.drain_node("node-1", logs.append, timeout=1)

    assert evicted == [("default", "app-1")]
    assert any("skipping 1 DaemonSet pod(s)" in msg for msg in logs)
    assert any("All pods drained" in msg for msg in logs)


def test_drain_node_fails_on_static_mirror_pods(monkeypatch):
    pods = [
        _pod("kube-system", "static-etcd", annotations={"kubernetes.io/config.mirror": "abc"}),
    ]

    class FakeCore:
        def list_pod_for_all_namespaces(self, field_selector=None):
            return SimpleNamespace(items=pods)

    monkeypatch.setattr(k8s_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_ops.client, "CoreV1Api", lambda api_client: FakeCore())

    with pytest.raises(RuntimeError, match="Static/mirror pod\\(s\\) block drain"):
        k8s_ops.drain_node("node-1", lambda msg: None)


def test_drain_node_fails_on_eviction_error(monkeypatch):
    pods = [_pod("default", "app-1")]

    class FakeCore:
        def list_pod_for_all_namespaces(self, field_selector=None):
            return SimpleNamespace(items=pods)

        def create_namespaced_pod_eviction(self, name, namespace, body):
            raise ApiException(status=500, reason="boom")

    monkeypatch.setattr(k8s_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_ops.client, "CoreV1Api", lambda api_client: FakeCore())

    with pytest.raises(RuntimeError, match="Pod eviction failed"):
        k8s_ops.drain_node("node-1", lambda msg: None)


def test_drain_node_fails_on_timeout_with_stuck_terminating_pods(monkeypatch):
    logs: list[str] = []
    initial = [_pod("default", "app-1")]
    remaining = [[_pod("default", "app-1", deletion_timestamp="2026-04-08T12:00:00Z")]]
    ticks = iter([1000.0, 1000.0, 1011.0])

    class FakeCore:
        def list_pod_for_all_namespaces(self, field_selector=None):
            if field_selector == "spec.nodeName=node-1" and remaining and getattr(self, "_evicted", False):
                return SimpleNamespace(items=remaining[0])
            return SimpleNamespace(items=initial)

        def create_namespaced_pod_eviction(self, name, namespace, body):
            self._evicted = True

    monkeypatch.setattr(k8s_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_ops.client, "CoreV1Api", lambda api_client: FakeCore())
    monkeypatch.setattr(k8s_ops.time, "time", lambda: next(ticks))
    monkeypatch.setattr(k8s_ops.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="stuck terminating: default/app-1"):
        k8s_ops.drain_node("node-1", logs.append, timeout=10)


def test_get_k8s_pvc_workload_summary_cross_references_longhorn_replicas(monkeypatch):
    pod = SimpleNamespace(
        metadata=SimpleNamespace(namespace="db", name="mariadb-0"),
        spec=SimpleNamespace(
            node_name="cmp-a01",
            volumes=[
                SimpleNamespace(
                    persistent_volume_claim=SimpleNamespace(claim_name="data-mariadb-0"),
                ),
            ],
        ),
        status=SimpleNamespace(phase="Running"),
    )
    pvc = SimpleNamespace(
        metadata=SimpleNamespace(namespace="db", name="data-mariadb-0"),
        spec=SimpleNamespace(volume_name="pvc-123", storage_class_name="longhorn-repl3", access_modes=["RWO"]),
        status=SimpleNamespace(phase="Bound", capacity={"storage": "200Gi"}),
    )
    pv = SimpleNamespace(
        metadata=SimpleNamespace(name="pvc-123", annotations={}),
        spec=SimpleNamespace(
            storage_class_name="longhorn-repl3",
            csi=SimpleNamespace(driver="driver.longhorn.io", volume_handle="vol-longhorn-1", volume_attributes={}),
            node_affinity=None,
        ),
    )

    class FakeCore:
        def list_pod_for_all_namespaces(self):
            return SimpleNamespace(items=[pod])

        def list_persistent_volume_claim_for_all_namespaces(self):
            return SimpleNamespace(items=[pvc])

        def list_persistent_volume(self):
            return SimpleNamespace(items=[pv])

    class FakeCustom:
        def list_namespaced_custom_object(self, group, version, namespace, plural):
            assert group == "longhorn.io"
            assert namespace == "longhorn-system"
            if plural == "volumes":
                return {
                    "items": [
                        {
                            "metadata": {"name": "vol-longhorn-1"},
                            "spec": {"numberOfReplicas": 2},
                            "status": {
                                "kubernetesStatus": {
                                    "pvName": "pvc-123",
                                    "pvcName": "data-mariadb-0",
                                    "namespace": "db",
                                },
                            },
                        },
                    ],
                }
            if plural == "replicas":
                return {
                    "items": [
                        {"spec": {"volumeName": "vol-longhorn-1", "nodeID": "cmp-a01"}},
                        {"spec": {"volumeName": "vol-longhorn-1", "nodeID": "cmp-a02"}},
                    ],
                }
            return {"items": []}

    monkeypatch.setattr(k8s_inventory_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_inventory_ops.client, "CoreV1Api", lambda api_client: FakeCore())
    monkeypatch.setattr(k8s_inventory_ops.client, "CustomObjectsApi", lambda api_client: FakeCustom())

    payload = k8s_inventory_ops.get_k8s_pvc_workload_summary()

    assert payload["error"] is None
    assert payload["items"][0]["replica_count"] == 2
    assert payload["items"][0]["replica_nodes"] == ["cmp-a01", "cmp-a02"]
    assert payload["replica_nodes"][0]["node"] == "cmp-a01"


def test_list_k8s_pvcs_reuses_pvc_workload_summary(monkeypatch):
    monkeypatch.setattr(
        k8s_inventory_ops,
        "get_k8s_pvc_workload_summary",
        lambda auth=None: {
            "items": [
                {
                    "namespace": "db",
                    "name": "data-0",
                    "status": "Bound",
                    "volume": "pvc-123",
                    "capacity": "100Gi",
                    "access_modes": "RWO",
                    "storageclass": "longhorn",
                    "replica_count": 2,
                    "replica_nodes": ["cmp-a01", "cmp-a02"],
                    "consumer_pods": ["mariadb-0"],
                    "consumer_nodes": ["cmp-a01"],
                },
                {
                    "namespace": "web",
                    "name": "content-0",
                    "status": "Bound",
                    "volume": "pvc-456",
                    "capacity": "10Gi",
                    "access_modes": "RWX",
                    "storageclass": "nfs",
                    "replica_count": None,
                    "replica_nodes": [],
                    "consumer_pods": ["nginx-0"],
                    "consumer_nodes": ["cmp-b01"],
                },
            ],
            "error": None,
        },
    )

    items = k8s_inventory_ops.list_k8s_pvcs()
    namespaced = k8s_inventory_ops.list_k8s_pvcs(namespace="db")

    assert len(items) == 2
    assert items[0]["replica_nodes"] == ["cmp-a01", "cmp-a02"]
    assert items[0]["consumer_pods"] == ["mariadb-0"]
    assert namespaced == [items[0]]


def test_list_k8s_gateway_api_resources(monkeypatch):
    class FakeCore:
        def list_namespace(self):
            return SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name="envoy-gateway"))])

    class FakeCustom:
        def list_cluster_custom_object(self, group, version, plural):
            assert group == "gateway.networking.k8s.io"
            assert version == "v1"
            assert plural == "gatewayclasses"
            return {
                "items": [
                    {
                        "metadata": {"name": "envoy-gateway", "creationTimestamp": "2026-04-14T01:00:00Z"},
                        "spec": {"controllerName": "gateway.envoyproxy.io/gatewayclass-controller"},
                        "status": {"conditions": [{"type": "Accepted", "status": "True"}]},
                    },
                ],
            }

        def list_namespaced_custom_object(self, group, version, namespace, plural):
            assert group == "gateway.networking.k8s.io"
            assert version == "v1"
            assert namespace == "envoy-gateway"
            if plural == "gateways":
                return {
                    "items": [
                        {
                            "metadata": {"namespace": "envoy-gateway", "name": "flex-gateway", "creationTimestamp": "2026-04-14T01:00:00Z"},
                            "spec": {"gatewayClassName": "envoy-gateway", "listeners": [{"name": "https"}, {"name": "http"}]},
                            "status": {
                                "addresses": [{"value": "203.0.113.10"}],
                                "conditions": [{"type": "Accepted", "status": "True"}, {"type": "Programmed", "status": "True"}],
                                "listeners": [{"name": "https", "attachedRoutes": 3}, {"name": "http", "attachedRoutes": 1}],
                            },
                        },
                    ],
                }
            if plural == "httproutes":
                return {
                    "items": [
                        {
                            "metadata": {"namespace": "envoy-gateway", "name": "app-route", "creationTimestamp": "2026-04-14T01:05:00Z"},
                            "spec": {
                                "hostnames": ["app.example.com"],
                                "parentRefs": [{"name": "flex-gateway", "sectionName": "https"}],
                                "rules": [{"backendRefs": [{"name": "app-service", "port": 8080}]}],
                            },
                            "status": {
                                "parents": [{
                                    "conditions": [
                                        {"type": "Accepted", "status": "True"},
                                        {"type": "ResolvedRefs", "status": "True"},
                                    ],
                                }],
                            },
                        },
                    ],
                }
            return {"items": []}

    monkeypatch.setattr(k8s_inventory_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_inventory_ops.client, "CoreV1Api", lambda api_client: FakeCore())
    monkeypatch.setattr(k8s_inventory_ops.client, "CustomObjectsApi", lambda api_client: FakeCustom())

    gatewayclasses = k8s_inventory_ops.list_k8s_gatewayclasses()
    gateways = k8s_inventory_ops.list_k8s_gateways()
    routes = k8s_inventory_ops.list_k8s_httproutes()

    assert gatewayclasses[0]["name"] == "envoy-gateway"
    assert gatewayclasses[0]["accepted"] == "True"
    assert gateways[0]["name"] == "flex-gateway"
    assert gateways[0]["attached_routes"] == 4
    assert gateways[0]["listener_names"] == ["https", "http"]
    assert routes[0]["name"] == "app-route"
    assert routes[0]["parent_refs"] == ["flex-gateway/https"]
    assert routes[0]["backend_refs"] == ["app-service:8080"]


def test_list_k8s_cluster_networks_summarizes_pod_cidrs(monkeypatch):
    node_a = SimpleNamespace(
        metadata=SimpleNamespace(name="cmp-a01"),
        spec=SimpleNamespace(pod_cidr="10.244.1.0/24", pod_cidrs=["10.244.1.0/24"]),
    )
    node_b = SimpleNamespace(
        metadata=SimpleNamespace(name="cmp-a02"),
        spec=SimpleNamespace(pod_cidr="10.244.2.0/24", pod_cidrs=["10.244.2.0/24"]),
    )
    service = SimpleNamespace(
        status=SimpleNamespace(
            load_balancer=SimpleNamespace(ingress=[SimpleNamespace(ip="10.244.1.55")]),
        ),
    )

    class FakeCore:
        def list_node(self):
            return SimpleNamespace(items=[node_a, node_b])

        def list_service_for_all_namespaces(self):
            return SimpleNamespace(items=[service])

    monkeypatch.setattr(k8s_inventory_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_inventory_ops.client, "CoreV1Api", lambda api_client: FakeCore())

    items = k8s_inventory_ops.list_k8s_cluster_networks()

    assert len(items) == 2
    assert items[0]["cidr"] == "10.244.1.0/24"
    assert items[0]["node_count"] == 1
    assert items[0]["load_balancer_ips"] == 1


def test_list_k8s_network_domains_groups_services_gateways_and_routes(monkeypatch):
    monkeypatch.setattr(
        k8s_inventory_ops,
        "list_k8s_services",
        lambda auth=None, namespace=None: [
            {
                "namespace": "web",
                "name": "frontend",
                "type": "LoadBalancer",
                "external_ips": ["203.0.113.10"],
            },
            {
                "namespace": "web",
                "name": "api",
                "type": "ClusterIP",
                "external_ips": [],
            },
        ],
    )
    monkeypatch.setattr(
        k8s_inventory_ops,
        "list_k8s_gateways",
        lambda auth=None: [
            {
                "namespace": "web",
                "name": "public",
                "addresses": ["203.0.113.10"],
            },
        ],
    )
    monkeypatch.setattr(
        k8s_inventory_ops,
        "list_k8s_httproutes",
        lambda auth=None: [
            {
                "namespace": "web",
                "name": "frontend",
            },
        ],
    )

    items = k8s_inventory_ops.list_k8s_network_domains()

    assert items == [{
        "namespace": "web",
        "name": "web",
        "service_count": 2,
        "lb_count": 1,
        "gateway_count": 1,
        "route_count": 1,
        "external_endpoints": ["203.0.113.10"],
        "service_names": ["api", "frontend"],
        "gateway_names": ["public"],
        "route_names": ["frontend"],
    }]


def test_list_k8s_kubeovn_vpcs_and_subnets(monkeypatch):
    class FakeCustom:
        def list_cluster_custom_object(self, group, version, plural):
            assert group == "kubeovn.io"
            assert version == "v1"
            if plural == "vpcs":
                return {
                    "items": [
                        {
                            "metadata": {"name": "tenant-a", "creationTimestamp": "2026-04-15T01:00:00Z"},
                            "spec": {
                                "default": False,
                                "namespaces": ["apps", "db"],
                                "staticRoutes": [{"policy": "dst"}],
                                "policyRoutes": [{"match": "ip"}],
                            },
                            "status": {"subnets": ["tenant-a-apps", "tenant-a-db"], "standby": True},
                        },
                    ],
                }
            if plural == "subnets":
                return {
                    "items": [
                        {
                            "metadata": {"name": "tenant-a-apps", "creationTimestamp": "2026-04-15T01:05:00Z"},
                            "spec": {
                                "cidrBlock": "10.244.10.0/24",
                                "gateway": "10.244.10.1",
                                "protocol": "IPv4",
                                "vpc": "tenant-a",
                                "provider": "ovn",
                                "natOutgoing": True,
                                "private": False,
                                "default": False,
                                "namespaces": ["apps"],
                                "excludeIps": ["10.244.10.1"],
                            },
                            "status": {"availableIPs": "240", "usingIPs": "12"},
                        },
                    ],
                }
            return {"items": []}

    monkeypatch.setattr(k8s_inventory_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_inventory_ops.client, "CustomObjectsApi", lambda api_client: FakeCustom())

    vpcs = k8s_inventory_ops.list_k8s_kubeovn_vpcs()
    subnets = k8s_inventory_ops.list_k8s_kubeovn_subnets()

    assert vpcs == [{
        "name": "tenant-a",
        "default": False,
        "namespace_count": 2,
        "namespaces": ["apps", "db"],
        "subnet_count": 2,
        "subnets": ["tenant-a-apps", "tenant-a-db"],
        "static_route_count": 1,
        "policy_route_count": 1,
        "standby": True,
        "created": "2026-04-15T01:00:00Z",
    }]
    assert subnets == [{
        "name": "tenant-a-apps",
        "cidr": "10.244.10.0/24",
        "gateway": "10.244.10.1",
        "protocol": "IPv4",
        "vpc": "tenant-a",
        "provider": "ovn",
        "nat_outgoing": True,
        "private": False,
        "default": False,
        "namespace_count": 1,
        "namespaces": ["apps"],
        "exclude_ip_count": 1,
        "available_ips": "240",
        "used_ips": "12",
        "created": "2026-04-15T01:05:00Z",
    }]


def test_list_k8s_kubeovn_vlans_and_provider_networks(monkeypatch):
    class FakeCustom:
        def list_cluster_custom_object(self, group, version, plural):
            assert group == "kubeovn.io"
            assert version == "v1"
            if plural == "vlans":
                return {
                    "items": [
                        {
                            "metadata": {"name": "tenant-a-vlan", "creationTimestamp": "2026-04-15T02:00:00Z"},
                            "spec": {"provider": "physnet1", "id": 120},
                            "status": {"subnets": ["tenant-a-apps"]},
                        },
                    ],
                }
            if plural in ("provider-networks", "providernetworks"):
                return {
                    "items": [
                        {
                            "metadata": {"name": "physnet1", "creationTimestamp": "2026-04-15T02:05:00Z"},
                            "spec": {
                                "defaultInterface": "bond0.120",
                                "excludeNodes": ["cmp-a03"],
                                "customInterfaces": {"cmp-a01": "bond0.120", "cmp-a02": "bond0.120"},
                            },
                            "status": {"readyNodes": ["cmp-a01", "cmp-a02"]},
                        },
                    ],
                }
            return {"items": []}

    monkeypatch.setattr(k8s_inventory_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_inventory_ops.client, "CustomObjectsApi", lambda api_client: FakeCustom())

    vlans = k8s_inventory_ops.list_k8s_kubeovn_vlans()
    provider_networks = k8s_inventory_ops.list_k8s_kubeovn_provider_networks()

    assert vlans == [{
        "name": "tenant-a-vlan",
        "provider": "physnet1",
        "vlan_id": 120,
        "subnet_count": 1,
        "subnets": ["tenant-a-apps"],
        "created": "2026-04-15T02:00:00Z",
    }]
    assert provider_networks == [{
        "name": "physnet1",
        "default_interface": "bond0.120",
        "nic_count": 2,
        "exclude_node_count": 1,
        "ready_node_count": 2,
        "exclude_nodes": ["cmp-a03"],
        "ready_nodes": ["cmp-a01", "cmp-a02"],
        "created": "2026-04-15T02:05:00Z",
    }]


def test_list_k8s_kubeovn_provider_subnets_and_ips(monkeypatch):
    monkeypatch.setattr(
        k8s_inventory_ops,
        "list_k8s_kubeovn_subnets",
        lambda auth=None: [
            {
                "name": "tenant-a-apps",
                "cidr": "10.42.0.0/24",
                "gateway": "10.42.0.1",
                "protocol": "IPv4",
                "vpc": "tenant-a",
                "provider": "",
                "namespace_count": 1,
                "namespaces": ["tenant-a"],
                "available_ips": "240",
                "used_ips": "12",
                "created": "2026-04-15T02:00:00Z",
            },
            {
                "name": "tenant-a-provider",
                "cidr": "172.18.0.0/24",
                "gateway": "172.18.0.1",
                "protocol": "IPv4",
                "vpc": "tenant-a",
                "provider": "physnet1",
                "namespace_count": 1,
                "namespaces": ["tenant-a"],
                "available_ips": "220",
                "used_ips": "22",
                "created": "2026-04-15T02:05:00Z",
            },
        ],
    )

    class FakeCustom:
        def list_cluster_custom_object(self, group, version, plural):
            assert group == "kubeovn.io"
            assert version == "v1"
            assert plural == "ips"
            return {
                "items": [
                    {
                        "metadata": {"name": "pod.web.frontend", "creationTimestamp": "2026-04-15T02:10:00Z"},
                        "spec": {
                            "namespace": "web",
                            "podName": "frontend-0",
                            "nodeName": "cmp-a01",
                            "subnet": "tenant-a-provider",
                            "v4IPAddress": "172.18.0.55",
                            "macAddress": "fa:16:3e:aa:bb:cc",
                            "attachSubnets": ["tenant-a-apps"],
                            "attachIPs": ["10.42.0.55"],
                        },
                    },
                ],
            }

    monkeypatch.setattr(k8s_inventory_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_inventory_ops.client, "CustomObjectsApi", lambda api_client: FakeCustom())

    provider_subnets = k8s_inventory_ops.list_k8s_kubeovn_provider_subnets()
    ips = k8s_inventory_ops.list_k8s_kubeovn_ips()

    assert provider_subnets == [{
        "name": "tenant-a-provider",
        "cidr": "172.18.0.0/24",
        "gateway": "172.18.0.1",
        "protocol": "IPv4",
        "vpc": "tenant-a",
        "provider": "physnet1",
        "namespace_count": 1,
        "namespaces": ["tenant-a"],
        "available_ips": "220",
        "used_ips": "22",
        "created": "2026-04-15T02:05:00Z",
    }]
    assert ips == [{
        "name": "pod.web.frontend",
        "namespace": "web",
        "pod_name": "frontend-0",
        "node_name": "cmp-a01",
        "subnet": "tenant-a-provider",
        "v4_ip": "172.18.0.55",
        "v6_ip": "",
        "mac_address": "fa:16:3e:aa:bb:cc",
        "attach_subnets": ["tenant-a-apps"],
        "attach_ips": ["10.42.0.55"],
        "created": "2026-04-15T02:10:00Z",
    }]


def test_list_k8s_operators_derives_version_from_workloads(monkeypatch):
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(
            namespace="longhorn-system",
            name="longhorn-manager",
            labels={"app.kubernetes.io/name": "longhorn-manager"},
            creation_timestamp=None,
        ),
        spec=SimpleNamespace(
            replicas=1,
            template=SimpleNamespace(
                spec=SimpleNamespace(
                    containers=[SimpleNamespace(image="longhornio/longhorn-manager:v1.7.2")],
                ),
            ),
        ),
        status=SimpleNamespace(replicas=1, ready_replicas=1),
    )

    class FakeApps:
        def list_deployment_for_all_namespaces(self):
            return SimpleNamespace(items=[deployment])

        def list_daemon_set_for_all_namespaces(self):
            return SimpleNamespace(items=[])

        def list_stateful_set_for_all_namespaces(self):
            return SimpleNamespace(items=[])

    class FakeApiExtensions:
        def list_custom_resource_definition(self):
            return SimpleNamespace(items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="volumes.longhorn.io", creation_timestamp=None),
                    spec=SimpleNamespace(
                        group="longhorn.io",
                        names=SimpleNamespace(kind="Volume"),
                        scope="Namespaced",
                        versions=[SimpleNamespace(name="v1beta2", served=True)],
                    ),
                ),
            ])

    monkeypatch.setattr(k8s_inventory_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_inventory_ops.client, "AppsV1Api", lambda api_client: FakeApps())
    monkeypatch.setattr(k8s_inventory_ops.client, "ApiextensionsV1Api", lambda api_client: FakeApiExtensions())

    items = k8s_inventory_ops.list_k8s_operators()

    assert items[0]["name"] == "longhorn-manager"
    assert items[0]["kind"] == "Deployment"
    assert items[0]["version"] == "v1.7.2"
    assert items[0]["ready"] == "1/1"
    assert items[0]["managed_crds"] >= 1


def test_list_k8s_workload_resources(monkeypatch):
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(namespace="apps", name="web", creation_timestamp=None),
        spec=SimpleNamespace(
            replicas=3,
            selector={"matchLabels": {"app": "web"}},
            strategy=SimpleNamespace(type="RollingUpdate", rolling_update=SimpleNamespace(max_unavailable="25%", max_surge="25%")),
            template=SimpleNamespace(spec=SimpleNamespace(containers=[SimpleNamespace(image="nginx:1.29")])),
        ),
        status=SimpleNamespace(ready_replicas=2, updated_replicas=3, available_replicas=2, unavailable_replicas=1),
    )
    statefulset = SimpleNamespace(
        metadata=SimpleNamespace(namespace="db", name="mariadb", creation_timestamp=None),
        spec=SimpleNamespace(
            replicas=3,
            service_name="mariadb-headless",
            selector={"matchLabels": {"app": "mariadb"}},
            update_strategy=SimpleNamespace(type="RollingUpdate"),
            volume_claim_templates=[SimpleNamespace(metadata=SimpleNamespace(name="data"))],
            template=SimpleNamespace(spec=SimpleNamespace(containers=[SimpleNamespace(image="mariadb:11.4")])),
        ),
        status=SimpleNamespace(ready_replicas=2, current_replicas=3, updated_replicas=2, current_revision="rev-a", update_revision="rev-b"),
    )
    daemonset = SimpleNamespace(
        metadata=SimpleNamespace(namespace="infra", name="node-agent", creation_timestamp=None),
        spec=SimpleNamespace(
            selector={"matchLabels": {"app": "node-agent"}},
            update_strategy=SimpleNamespace(type="RollingUpdate"),
            template=SimpleNamespace(
                spec=SimpleNamespace(
                    node_selector={"node-role.kubernetes.io/worker": "true"},
                    tolerations=[SimpleNamespace(), SimpleNamespace()],
                    containers=[SimpleNamespace(image="example/node-agent:v2")],
                ),
            ),
        ),
        status=SimpleNamespace(
            desired_number_scheduled=5,
            current_number_scheduled=5,
            number_ready=4,
            number_available=4,
            number_unavailable=1,
            number_misscheduled=0,
        ),
    )

    class FakeApps:
        def list_deployment_for_all_namespaces(self):
            return SimpleNamespace(items=[deployment])

        def list_stateful_set_for_all_namespaces(self):
            return SimpleNamespace(items=[statefulset])

        def list_daemon_set_for_all_namespaces(self):
            return SimpleNamespace(items=[daemonset])

    monkeypatch.setattr(k8s_inventory_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_inventory_ops.client, "AppsV1Api", lambda api_client: FakeApps())

    deployments = k8s_inventory_ops.list_k8s_deployments()
    statefulsets = k8s_inventory_ops.list_k8s_statefulsets()
    daemonsets = k8s_inventory_ops.list_k8s_daemonsets()

    assert deployments[0]["name"] == "web"
    assert deployments[0]["ready"] == 2
    assert deployments[0]["desired"] == 3
    assert deployments[0]["strategy"] == "RollingUpdate"
    assert deployments[0]["images"] == ["nginx:1.29"]

    assert statefulsets[0]["name"] == "mariadb"
    assert statefulsets[0]["service_name"] == "mariadb-headless"
    assert statefulsets[0]["pvc_templates"] == ["data"]
    assert statefulsets[0]["current_revision"] == "rev-a"

    assert daemonsets[0]["name"] == "node-agent"
    assert daemonsets[0]["desired"] == 5
    assert daemonsets[0]["ready"] == 4
    assert daemonsets[0]["node_selector"] == "node-role.kubernetes.io/worker=true"
    assert daemonsets[0]["tolerations"] == 2
