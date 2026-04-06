"""Reboot backend helpers."""
from __future__ import annotations

import subprocess
from typing import Callable

from . import node_agent_client
from .models import NodePhase, NodeState

LogFn = Callable[[str], None]


def is_ready_for_reboot(state: NodeState) -> tuple[bool, str]:
    if state.phase in (NodePhase.RUNNING, NodePhase.REBOOTING, NodePhase.UNDRAINING):
        return False, "Cannot reboot while an operation is in progress."

    if not state.k8s_cordoned:
        return False, "Node must be cordoned and drained before reboot."

    if not state.is_compute:
        return True, ""

    if state.compute_status != "disabled":
        return False, "Nova compute service must be disabled before reboot."

    if state.vm_count is None or state.amphora_count is None:
        return False, "VM evacuation state is unknown; complete evacuation before reboot."

    if state.vm_count != 0 or state.amphora_count != 0:
        return False, "Compute node must be drained of VMs and pods before reboot."

    return True, ""


def issue_reboot(state: NodeState, log: LogFn) -> None:
    if node_agent_client.enabled():
        _issue_node_agent_reboot(state, log)
        return
    _issue_ssh_reboot(state, log)


def _issue_ssh_reboot(state: NodeState, log: LogFn) -> None:
    try:
        subprocess.run(
            [
                "ssh",
                "-o", "ConnectTimeout=10",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                state.hypervisor,
                "sudo", "reboot",
            ],
            timeout=15,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        pass
    log(f"Reboot command sent to '{state.hypervisor}' via SSH")


def _issue_node_agent_reboot(state: NodeState, log: LogFn) -> None:
    body = node_agent_client.post_reboot(state.k8s_name, state.hypervisor)
    if not body.get("accepted"):
        raise RuntimeError("node-agent rejected the reboot request")
    log(f"Reboot command accepted by node-agent for '{state.k8s_name}'")
