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
