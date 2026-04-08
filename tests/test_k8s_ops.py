from __future__ import annotations

from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException

from draino.operations import k8s_ops


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
