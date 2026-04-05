from __future__ import annotations

from draino import worker
from draino.models import NodePhase, NodeState
from draino.operations.k8s_ops import K8sAuth
from draino.operations.openstack_ops import OpenStackAuth


def test_run_drain_quick_propagates_session_auth(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", is_compute=True)
    state.init_quick_drain_steps(is_compute=True)

    k8s_auth = K8sAuth(server="https://cluster.example", token="token-1")
    os_auth = OpenStackAuth(
        auth_url="https://keystone.example/v3",
        username="ops",
        password="secret",
        project_name="admin",
    )

    monkeypatch.setattr(
        worker.k8s_ops,
        "cordon_node",
        lambda name, log, auth=None: events.append(("cordon", auth)),
    )
    monkeypatch.setattr(
        worker.openstack_ops,
        "disable_compute_service",
        lambda hypervisor, log, auth=None: events.append(("disable", auth)),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "drain_node",
        lambda name, log, auth=None: events.append(("drain", auth)),
    )

    worker.run_drain_quick(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
        k8s_auth=k8s_auth,
        openstack_auth=os_auth,
    )

    assert events == [("cordon", k8s_auth), ("disable", os_auth), ("drain", k8s_auth)]
    assert state.phase == NodePhase.IDLE
    assert state.k8s_cordoned is True
    assert state.compute_status == "disabled"


def test_run_undrain_propagates_session_auth(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", is_compute=True, k8s_cordoned=True)
    state.compute_status = "disabled"
    state.init_undrain_steps(is_compute=True)

    k8s_auth = K8sAuth(server="https://cluster.example", token="token-1")
    os_auth = OpenStackAuth(
        auth_url="https://keystone.example/v3",
        username="ops",
        password="secret",
        project_name="admin",
    )

    monkeypatch.setattr(
        worker.openstack_ops,
        "enable_compute_service",
        lambda hypervisor, log, auth=None: events.append(("enable", auth)),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "uncordon_node",
        lambda name, log, auth=None: events.append(("uncordon", auth)),
    )

    worker.run_undrain(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
        k8s_auth=k8s_auth,
        openstack_auth=os_auth,
    )

    assert events == [("enable", os_auth), ("uncordon", k8s_auth)]
    assert state.phase == NodePhase.IDLE
    assert state.k8s_cordoned is False
    assert state.compute_status == "up"
