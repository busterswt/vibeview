"""Data models shared across the application."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class StepStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED  = auto()
    SKIPPED = auto()


class NodePhase(Enum):
    IDLE     = auto()
    RUNNING  = auto()
    COMPLETE = auto()
    ERROR    = auto()


@dataclass
class InstanceInfo:
    id: str
    name: str
    status: str                              # Nova power/VM status
    is_amphora: bool = False
    migration_status: Optional[str] = None  # queued | migrating | complete | failed
    lb_id: Optional[str] = None             # Octavia LB ID (amphora only)
    failover_status: Optional[str] = None   # failing_over | complete | failed


@dataclass
class WorkflowStep:
    key: str
    label: str
    status: StepStatus = StepStatus.PENDING
    detail: str = ""


@dataclass
class NodeState:
    k8s_name: str
    hypervisor: str                          # Nova hypervisor hostname

    # Draino workflow phase
    phase: NodePhase = NodePhase.IDLE

    # ── OpenStack summary (populated by background refresh) ──────────────
    # compute_status: None=loading | "up" | "disabled" | "down"
    compute_status: Optional[str] = None
    amphora_count: Optional[int] = None
    vm_count: Optional[int] = None

    # ── Workflow detail ───────────────────────────────────────────────────
    steps: list[WorkflowStep] = field(default_factory=list)
    instances: list[InstanceInfo] = field(default_factory=list)
    log_buffer: list[str] = field(default_factory=list)

    def add_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_buffer.append(f"[{ts}] {msg}")

    def get_step(self, key: str) -> Optional[WorkflowStep]:
        return next((s for s in self.steps if s.key == key), None)

    def init_steps(self) -> None:
        self.steps = [
            WorkflowStep("cordon",         "Cordon K8s node"),
            WorkflowStep("disable_nova",   "Disable Nova compute service"),
            WorkflowStep("list_instances", "Enumerate instances on hypervisor"),
            WorkflowStep("migrate_vms",    "Live-migrate non-Amphora instances"),
            WorkflowStep("failover_lbs",   "Failover Amphora load balancers"),
            WorkflowStep("await_empty",    "Wait for hypervisor to empty"),
            WorkflowStep("drain_k8s",      "Drain K8s node (evict pods)"),
        ]
