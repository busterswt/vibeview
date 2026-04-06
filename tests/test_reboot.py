from __future__ import annotations

from draino import node_agent_client, reboot
from draino.models import NodeState


def test_compute_node_requires_full_evacuation_before_reboot():
    state = NodeState(
        k8s_name="node-1",
        hypervisor="hv-1",
        is_compute=True,
        k8s_cordoned=True,
        compute_status="disabled",
        vm_count=1,
        amphora_count=0,
    )

    ready, detail = reboot.is_ready_for_reboot(state)

    assert ready is False
    assert "drained of VMs and pods" in detail


def test_non_compute_node_can_reboot_after_cordon():
    state = NodeState(
        k8s_name="node-2",
        hypervisor="hv-2",
        is_compute=False,
        k8s_cordoned=True,
    )

    ready, detail = reboot.is_ready_for_reboot(state)

    assert ready is True
    assert detail == ""


def test_issue_reboot_uses_node_agent(monkeypatch):
    monkeypatch.setattr(node_agent_client, "enabled", lambda: True)
    monkeypatch.setattr(
        node_agent_client,
        "post_reboot",
        lambda node_name, hypervisor: {"accepted": True, "node": node_name, "hypervisor": hypervisor},
    )

    state = NodeState(k8s_name="node-1", hypervisor="hv-1")
    log_messages: list[str] = []
    reboot.issue_reboot(state, log_messages.append)

    assert "accepted by node-agent" in log_messages[0]
