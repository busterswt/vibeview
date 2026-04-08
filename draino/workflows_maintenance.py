"""Maintenance workflow implementations shared by the worker entrypoints."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .models import NodePhase, NodeState, StepStatus

UpdateFn = Callable[[], None]
LogFn = Callable[[str], None]
AuditCb = Callable[[str, str], None]

REBOOT_OFFLINE_TIMEOUT = 300
REBOOT_ONLINE_TIMEOUT = 1200


@dataclass
class WorkflowContext:
    state: NodeState
    update_cb: UpdateFn
    log_cb: LogFn
    audit_cb: Optional[AuditCb]
    abort_prefix: str

    def step_set(self, key: str, status: StepStatus, detail: str = "") -> None:
        step = self.state.get_step(key)
        if step:
            step.status = status
            if detail:
                step.detail = detail
        self.update_cb()

    def log(self, msg: str) -> None:
        self.state.add_log(msg)
        self.log_cb(msg)
        self.update_cb()

    def abort(self, key: str, reason: str) -> None:
        self.step_set(key, StepStatus.FAILED, reason)
        self.state.phase = NodePhase.ERROR
        self.log(f"{self.abort_prefix}: {reason}")
        if self.audit_cb:
            self.audit_cb("failed", f"step={key} reason={reason}")
        self.update_cb()


def run_reboot(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    *,
    k8s_ops,
    issue_reboot_fn,
    time_module,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth=None,
) -> None:
    ctx = WorkflowContext(
        state=state,
        update_cb=update_cb,
        log_cb=log_cb,
        audit_cb=audit_cb,
        abort_prefix="Reboot aborted",
    )

    state.phase = NodePhase.REBOOTING
    state.reboot_start = time_module.time()
    state.reboot_downtime = None
    state.etcd_healthy = False
    state.init_reboot_steps()
    update_cb()

    baseline_ready_since = None
    try:
        for nd in k8s_ops.get_nodes(auth=k8s_auth):
            if nd["name"] == state.k8s_name:
                baseline_ready_since = nd.get("ready_since")
                break
    except Exception:
        pass

    ctx.step_set("issue_reboot", StepStatus.RUNNING)
    try:
        issue_reboot_fn(state, ctx.log)
    except Exception as exc:
        ctx.abort("issue_reboot", str(exc))
        return

    ctx.step_set("issue_reboot", StepStatus.SUCCESS)

    ctx.step_set("await_offline", StepStatus.RUNNING)
    offline_deadline = time_module.time() + REBOOT_OFFLINE_TIMEOUT
    went_offline = False

    while time_module.time() < offline_deadline:
        try:
            for nd in k8s_ops.get_nodes(auth=k8s_auth):
                if nd["name"] == state.k8s_name and not nd["ready"]:
                    went_offline = True
                    break
        except Exception:
            pass

        elapsed = int(time_module.time() - state.reboot_start)
        step = state.get_step("await_offline")
        if step:
            step.detail = f"{elapsed}s elapsed"
        update_cb()

        if went_offline:
            break
        time_module.sleep(5)

    if went_offline:
        ctx.step_set("await_offline", StepStatus.SUCCESS, "Node offline")
    else:
        ctx.step_set("await_offline", StepStatus.SKIPPED, "Not detected — proceeding")

    ctx.step_set("await_online", StepStatus.RUNNING)
    deadline = time_module.time() + REBOOT_ONLINE_TIMEOUT
    came_back = False

    while time_module.time() < deadline:
        try:
            for nd in k8s_ops.get_nodes(auth=k8s_auth):
                if nd["name"] != state.k8s_name or not nd["ready"]:
                    continue
                ready_since = nd.get("ready_since")
                if ready_since is not None and ready_since != baseline_ready_since:
                    state.reboot_downtime = time_module.time() - state.reboot_start
                    came_back = True
                    break
        except Exception:
            pass

        if came_back:
            break

        elapsed = int(time_module.time() - state.reboot_start)
        step = state.get_step("await_online")
        if step:
            step.detail = f"{elapsed}s elapsed"
        update_cb()
        time_module.sleep(5)

    if not came_back:
        ctx.abort("await_online", "Timeout — node did not return online")
        return

    dt = int(state.reboot_downtime)
    ctx.step_set("await_online", StepStatus.SUCCESS, f"Online — downtime {dt}s")
    ctx.step_set("uncordon", StepStatus.RUNNING)
    try:
        k8s_ops.uncordon_node(state.k8s_name, ctx.log, auth=k8s_auth)
        state.k8s_cordoned = False
        ctx.step_set("uncordon", StepStatus.SUCCESS)
    except Exception as exc:
        ctx.abort("uncordon", str(exc))
        return

    state.phase = NodePhase.IDLE
    state.etcd_healthy = None
    ctx.log(f"✓ '{state.k8s_name}' back online and uncordoned — total downtime: {dt}s")
    if audit_cb:
        audit_cb("completed", f"downtime={dt}s auto_uncordoned=true")
    update_cb()


def run_drain_quick(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    *,
    k8s_ops,
    openstack_ops,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth=None,
    openstack_auth=None,
) -> None:
    ctx = WorkflowContext(
        state=state,
        update_cb=update_cb,
        log_cb=log_cb,
        audit_cb=audit_cb,
        abort_prefix="Drain aborted",
    )

    ctx.step_set("cordon", StepStatus.RUNNING)
    try:
        k8s_ops.cordon_node(state.k8s_name, ctx.log, auth=k8s_auth)
        state.k8s_cordoned = True
        ctx.step_set("cordon", StepStatus.SUCCESS)
    except Exception as exc:
        ctx.abort("cordon", str(exc))
        return

    if state.is_compute:
        ctx.step_set("disable_nova", StepStatus.RUNNING)
        try:
            openstack_ops.disable_compute_service(state.hypervisor, ctx.log, auth=openstack_auth)
            state.compute_status = "disabled"
            ctx.step_set("disable_nova", StepStatus.SUCCESS)
        except Exception as exc:
            ctx.abort("disable_nova", str(exc))
            return

    ctx.step_set("drain_k8s", StepStatus.RUNNING)
    try:
        k8s_ops.drain_node(state.k8s_name, ctx.log, auth=k8s_auth)
        ctx.step_set("drain_k8s", StepStatus.SUCCESS)
    except Exception as exc:
        ctx.abort("drain_k8s", str(exc))
        return

    state.phase = NodePhase.IDLE
    ctx.log(f"✓ '{state.k8s_name}' drained — cordoned, nova disabled, pods evicted")
    if audit_cb:
        audit_cb("completed", "cordoned, nova disabled, pods evicted")
    update_cb()


def run_undrain(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    *,
    k8s_ops,
    openstack_ops,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth=None,
    openstack_auth=None,
) -> None:
    ctx = WorkflowContext(
        state=state,
        update_cb=update_cb,
        log_cb=log_cb,
        audit_cb=audit_cb,
        abort_prefix="Undrain aborted",
    )

    if state.is_compute:
        ctx.step_set("enable_nova", StepStatus.RUNNING)
        try:
            openstack_ops.enable_compute_service(state.hypervisor, ctx.log, auth=openstack_auth)
            state.compute_status = "up"
            ctx.step_set("enable_nova", StepStatus.SUCCESS)
        except Exception as exc:
            ctx.abort("enable_nova", str(exc))
            return

    ctx.step_set("uncordon", StepStatus.RUNNING)
    try:
        k8s_ops.uncordon_node(state.k8s_name, ctx.log, auth=k8s_auth)
        state.k8s_cordoned = False
        ctx.step_set("uncordon", StepStatus.SUCCESS)
    except Exception as exc:
        ctx.abort("uncordon", str(exc))
        return

    state.phase = NodePhase.IDLE
    ctx.log(f"✓ '{state.k8s_name}' undrained — nova enabled, node uncordoned")
    if audit_cb:
        audit_cb("completed", "nova enabled, node uncordoned")
    update_cb()
