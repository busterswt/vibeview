from __future__ import annotations

from draino import worker
from draino.models import NodePhase, NodeState, StepStatus
from draino.operations.k8s_ops import K8sAuth
from draino.operations.openstack_ops import OpenStackAuth


def _fake_clock(*values: float):
    remaining = list(values)
    last = values[-1]

    def _time() -> float:
        nonlocal last
        if remaining:
            last = remaining.pop(0)
        return last

    return _time


def _fake_nodes(*payloads: list[dict]):
    remaining = list(payloads)
    last = payloads[-1]

    def _get_nodes(auth=None):
        nonlocal last
        if remaining:
            last = remaining.pop(0)
        return last

    return _get_nodes


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


def test_run_reboot_uncordons_node_after_return(monkeypatch):
    events: list[tuple[str, object]] = []
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=True)

    k8s_auth = K8sAuth(server="https://cluster.example", token="token-1")
    clock = _fake_clock(1000.0, 1000.0, 1301.0, 1301.0, 1301.0, 1302.0, 1302.0)

    monkeypatch.setattr(worker, "issue_reboot", lambda state, log: events.append(("reboot", None)))
    monkeypatch.setattr(worker.time, "time", clock)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        worker.k8s_ops,
        "get_nodes",
        _fake_nodes(
            [{"name": "node-1", "ready": True, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": True, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": True, "ready_since": "after-reboot"}],
        ),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "uncordon_node",
        lambda name, log, auth=None: events.append(("uncordon", auth)),
    )

    worker.run_reboot(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
        k8s_auth=k8s_auth,
    )

    assert events == [("reboot", None), ("uncordon", k8s_auth)]
    assert state.phase == NodePhase.IDLE
    assert state.k8s_cordoned is False
    assert state.get_step("uncordon").status == StepStatus.SUCCESS


def test_run_reboot_fails_if_auto_uncordon_fails(monkeypatch):
    state = NodeState(k8s_name="node-1", hypervisor="hv-1", k8s_cordoned=True)
    clock = _fake_clock(1000.0, 1000.0, 1301.0, 1301.0, 1301.0, 1302.0, 1302.0)

    monkeypatch.setattr(worker, "issue_reboot", lambda state, log: None)
    monkeypatch.setattr(worker.time, "time", clock)
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        worker.k8s_ops,
        "get_nodes",
        _fake_nodes(
            [{"name": "node-1", "ready": True, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": True, "ready_since": "before-reboot"}],
            [{"name": "node-1", "ready": True, "ready_since": "after-reboot"}],
        ),
    )
    monkeypatch.setattr(
        worker.k8s_ops,
        "uncordon_node",
        lambda name, log, auth=None: (_ for _ in ()).throw(RuntimeError("uncordon failed")),
    )

    worker.run_reboot(
        state,
        update_cb=lambda: None,
        log_cb=lambda msg: None,
    )

    assert state.phase == NodePhase.ERROR
    assert state.k8s_cordoned is True
    assert state.get_step("uncordon").status == StepStatus.FAILED
