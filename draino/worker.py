"""Background workflow runners.  Execute entirely in worker threads."""
from __future__ import annotations

import subprocess
import time
from typing import Callable, Optional

from .models import InstanceInfo, NodePhase, NodeState, StepStatus
from .operations import k8s_ops, openstack_ops

UpdateFn  = Callable[[], None]
LogFn     = Callable[[str], None]
AuditCb   = Callable[[str, str], None]  # (event, detail)

POLL_INTERVAL  = 15    # seconds between migration / drain status polls
MIGRATE_TIMEOUT = 1800  # hard limit for all live migrations (seconds)
EMPTY_TIMEOUT   = 900   # hard limit for hypervisor-empty wait (seconds)

REBOOT_OFFLINE_TIMEOUT = 300   # seconds to wait for node to go NotReady
REBOOT_ONLINE_TIMEOUT  = 600   # seconds to wait for node to return Ready


def run_workflow(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth: Optional[k8s_ops.K8sAuth] = None,
    openstack_auth: Optional[openstack_ops.OpenStackAuth] = None,
) -> None:
    """Execute the full evacuation workflow.  Designed to run in a daemon thread.

    *update_cb* is called (via call_from_thread) whenever state changes.
    *log_cb* receives plain-text log strings for the global event log.
    *audit_cb*, if provided, is called as audit_cb(event, detail) on terminal outcomes.
    """

    def step_set(key: str, status: StepStatus, detail: str = "") -> None:
        step = state.get_step(key)
        if step:
            step.status = status
            if detail:
                step.detail = detail
        update_cb()

    def log(msg: str) -> None:
        state.add_log(msg)
        log_cb(msg)
        update_cb()

    def abort(key: str, reason: str) -> None:
        step_set(key, StepStatus.FAILED, reason)
        state.phase = NodePhase.ERROR
        log(f"Evacuation aborted: {reason}")
        if audit_cb:
            audit_cb("failed", f"step={key} reason={reason}")
        update_cb()

    # ── Initialise ────────────────────────────────────────────────────────
    state.phase = NodePhase.RUNNING
    state.instances = []
    state.init_steps()
    update_cb()

    # ── Step 1: Cordon ────────────────────────────────────────────────────
    step_set("cordon", StepStatus.RUNNING)
    try:
        k8s_ops.cordon_node(state.k8s_name, log, auth=k8s_auth)
        step_set("cordon", StepStatus.SUCCESS)
    except Exception as exc:
        abort("cordon", str(exc))
        return

    # ── Step 2: Disable Nova compute ──────────────────────────────────────
    step_set("disable_nova", StepStatus.RUNNING)
    try:
        openstack_ops.disable_compute_service(state.hypervisor, log, auth=openstack_auth)
        state.compute_status = "disabled"   # reflect immediately in node panel
        step_set("disable_nova", StepStatus.SUCCESS)
    except Exception as exc:
        abort("disable_nova", str(exc))
        return

    # ── Step 3: Enumerate instances ───────────────────────────────────────
    step_set("list_instances", StepStatus.RUNNING)
    try:
        servers = openstack_ops.list_servers_on_host(state.hypervisor, log, auth=openstack_auth)
        amp_map = openstack_ops.get_amphora_lb_mapping(log, auth=openstack_auth)
        for s in servers:
            lb_id  = amp_map.get(s["id"])
            is_amp = s["is_amphora"] or lb_id is not None
            state.instances.append(
                InstanceInfo(
                    id=s["id"],
                    name=s["name"],
                    status=s["status"],
                    is_amphora=is_amp,
                    lb_id=lb_id,
                )
            )
        n_vm  = sum(1 for i in state.instances if not i.is_amphora)
        n_amp = sum(1 for i in state.instances if i.is_amphora)
        # Update the node-panel summary counters
        state.vm_count      = n_vm
        state.amphora_count = n_amp
        step_set("list_instances", StepStatus.SUCCESS, f"{n_vm} VM(s), {n_amp} Amphora")
    except Exception as exc:
        abort("list_instances", str(exc))
        return

    # ── Step 4: Live-migrate regular VMs ──────────────────────────────────
    step_set("migrate_vms", StepStatus.RUNNING)
    vms = [i for i in state.instances if not i.is_amphora]

    if not vms:
        step_set("migrate_vms", StepStatus.SUCCESS, "No VMs to migrate")
    else:
        for inst in vms:
            inst.migration_status = "queued"
            update_cb()
            try:
                openstack_ops.live_migrate_server(inst.id, log, auth=openstack_auth)
                inst.migration_status = "migrating"
            except Exception as live_exc:
                # A 504 timeout means Nova accepted the request but the HTTP
                # response timed out — the instance may already be migrating.
                # Check task_state before attempting a cold fallback; issuing
                # cold migrate against an already-migrating instance gets a 409.
                task_state = openstack_ops.get_server_task_state(inst.id, auth=openstack_auth) or ""
                if "migrat" in task_state.lower():
                    log(
                        f"Live migration for '{inst.name}' timed out but instance "
                        f"is already in task_state '{task_state}' — continuing to poll"
                    )
                    inst.migration_status = "migrating"
                else:
                    log(f"Live migration failed for '{inst.name}': {live_exc} — trying cold migration")
                    try:
                        openstack_ops.cold_migrate_server(inst.id, log, auth=openstack_auth)
                        inst.migration_status = "cold-migrating"
                    except Exception as cold_exc:
                        log(f"Cold migration also failed for '{inst.name}': {cold_exc}")
                        inst.migration_status = "failed"
            update_cb()

        deadline = time.time() + MIGRATE_TIMEOUT
        while time.time() < deadline:
            pending = [
                i for i in vms
                if i.migration_status not in ("complete", "failed")
            ]
            if not pending:
                break

            for inst in pending:
                nova_status = openstack_ops.get_server_status(inst.id, auth=openstack_auth)

                if nova_status == "VERIFY_RESIZE":
                    # Cold migration landed — confirm it automatically
                    inst.migration_status = "confirming"
                    update_cb()
                    try:
                        openstack_ops.confirm_resize_server(inst.id, log, auth=openstack_auth)
                    except Exception as exc:
                        log(f"Confirm resize failed for '{inst.name}': {exc}")
                        inst.migration_status = "failed"
                elif nova_status == "ACTIVE":
                    # Check migration records to confirm it actually moved
                    migs = openstack_ops.get_server_migrations(inst.id, auth=openstack_auth)
                    if migs:
                        latest = migs[0]["status"].lower()
                        if latest in ("completed", "done", "finished"):
                            inst.migration_status = "complete"
                        elif latest in ("error", "failed"):
                            inst.migration_status = "failed"
                            log(f"Migration failed for '{inst.name}'")
                        # else still in-flight; leave status as-is
                    else:
                        inst.migration_status = "complete"
                elif nova_status == "ERROR":
                    inst.migration_status = "failed"
                    log(f"Server '{inst.name}' entered ERROR state")

            done_n   = sum(1 for i in vms if i.migration_status == "complete")
            failed_n = sum(1 for i in vms if i.migration_status == "failed")
            step = state.get_step("migrate_vms")
            if step:
                step.detail = f"{done_n}/{len(vms)} done, {failed_n} failed"
            update_cb()
            time.sleep(POLL_INTERVAL)

        failed_n = sum(1 for i in vms if i.migration_status == "failed")
        done_n   = sum(1 for i in vms if i.migration_status == "complete")
        if failed_n:
            abort("migrate_vms", f"{done_n}/{len(vms)} migrated — {failed_n} failed")
            return
        step_set("migrate_vms", StepStatus.SUCCESS, f"{done_n}/{len(vms)} migrated")

    # ── Step 5: Failover Amphora load balancers ───────────────────────────
    step_set("failover_lbs", StepStatus.RUNNING)
    amphora = [i for i in state.instances if i.is_amphora and i.lb_id]

    if not amphora:
        step_set("failover_lbs", StepStatus.SUCCESS, "No Amphora to fail over")
    else:
        lb_ids     = list({i.lb_id for i in amphora})
        any_failed = False

        for lb_id in lb_ids:
            for inst in amphora:
                if inst.lb_id == lb_id:
                    inst.failover_status = "failing_over"
            update_cb()

            try:
                openstack_ops.failover_loadbalancer(lb_id, log, auth=openstack_auth)
                ok        = openstack_ops.wait_for_lb_active(lb_id, log, auth=openstack_auth)
                fo_status = "complete" if ok else "failed"
                if not ok:
                    any_failed = True
                    log(f"LB {lb_id} did not return to ACTIVE")
            except Exception as exc:
                log(f"Failover error for LB {lb_id}: {exc}")
                fo_status  = "failed"
                any_failed = True

            for inst in amphora:
                if inst.lb_id == lb_id:
                    inst.failover_status = fo_status
            update_cb()

        if any_failed:
            abort("failover_lbs", "One or more LB failovers failed")
            return
        step_set("failover_lbs", StepStatus.SUCCESS, f"{len(lb_ids)} LB(s) failed over")

    # ── Step 6: Wait for hypervisor to be empty ───────────────────────────
    step_set("await_empty", StepStatus.RUNNING)
    deadline = time.time() + EMPTY_TIMEOUT
    count    = -1
    while time.time() < deadline:
        try:
            count = openstack_ops.count_servers_on_host(state.hypervisor, auth=openstack_auth)
        except Exception:
            count = -1
        step = state.get_step("await_empty")
        if step:
            step.detail = (
                f"{count} instance(s) remaining" if count >= 0 else "checking…"
            )
        # Keep node-panel counters live
        if count >= 0:
            state.vm_count      = max(0, count - (state.amphora_count or 0))
            state.amphora_count = state.amphora_count  # unchanged until failover done
        update_cb()
        if count == 0:
            state.vm_count      = 0
            state.amphora_count = 0
            break
        time.sleep(POLL_INTERVAL)
    else:
        abort("await_empty", f"Timeout — {count} instance(s) still present")
        return

    step_set("await_empty", StepStatus.SUCCESS, "Hypervisor empty")

    # ── Step 7: Drain K8s node ────────────────────────────────────────────
    step_set("drain_k8s", StepStatus.RUNNING)
    try:
        k8s_ops.drain_node(state.k8s_name, log, auth=k8s_auth)
        step_set("drain_k8s", StepStatus.SUCCESS)
    except Exception as exc:
        abort("drain_k8s", str(exc))
        return

    # ── Done ──────────────────────────────────────────────────────────────
    state.phase = NodePhase.COMPLETE
    log(f"✓ '{state.k8s_name}' fully evacuated — ready for reboot!")
    if audit_cb:
        audit_cb("completed", "hypervisor empty, K8s node drained")
    update_cb()


def run_reboot(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth: Optional[k8s_ops.K8sAuth] = None,
) -> None:
    """Issue a reboot via SSH and track downtime.  Runs in a daemon thread."""

    def step_set(key: str, status: StepStatus, detail: str = "") -> None:
        step = state.get_step(key)
        if step:
            step.status = status
            if detail:
                step.detail = detail
        update_cb()

    def log(msg: str) -> None:
        state.add_log(msg)
        log_cb(msg)
        update_cb()

    def abort(key: str, reason: str) -> None:
        step_set(key, StepStatus.FAILED, reason)
        state.phase = NodePhase.ERROR
        log(f"Reboot aborted: {reason}")
        if audit_cb:
            audit_cb("failed", f"step={key} reason={reason}")
        update_cb()

    state.phase = NodePhase.REBOOTING
    state.reboot_start    = time.time()
    state.reboot_downtime = None
    state.etcd_healthy    = False   # definitely not serving etcd during reboot
    state.init_reboot_steps()
    update_cb()

    # Capture pre-reboot ready_since so we can detect any change (avoids
    # clock-skew issues with timestamp comparisons).
    baseline_ready_since = None
    try:
        for nd in k8s_ops.get_nodes(auth=k8s_auth):
            if nd["name"] == state.k8s_name:
                baseline_ready_since = nd.get("ready_since")
                break
    except Exception:
        pass

    # ── Step 1: SSH reboot ────────────────────────────────────────────────
    step_set("ssh_reboot", StepStatus.RUNNING)
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
        # SSH drops when the node reboots — this is expected and counts as success.
        pass
    except Exception as exc:
        abort("ssh_reboot", str(exc))
        return

    log(f"Reboot command sent to '{state.hypervisor}'")
    step_set("ssh_reboot", StepStatus.SUCCESS)

    # ── Step 2: Wait for node to go offline (best-effort) ─────────────────
    # Fast reboots may never register NotReady in K8s (kubelet reconnects
    # before the node-monitor grace period fires).  We try for 120 s and
    # skip gracefully rather than aborting, then proceed to await_online.
    step_set("await_offline", StepStatus.RUNNING)
    offline_deadline = time.time() + 120
    went_offline     = False

    while time.time() < offline_deadline:
        try:
            for nd in k8s_ops.get_nodes(auth=k8s_auth):
                if nd["name"] == state.k8s_name and not nd["ready"]:
                    went_offline = True
                    break
        except Exception:
            pass

        elapsed = int(time.time() - state.reboot_start)
        step = state.get_step("await_offline")
        if step:
            step.detail = f"{elapsed}s elapsed"
        update_cb()

        if went_offline:
            break
        time.sleep(5)

    if went_offline:
        step_set("await_offline", StepStatus.SUCCESS, "Node offline")
    else:
        step_set("await_offline", StepStatus.SKIPPED, "Not detected — proceeding")

    # ── Step 3: Wait for node to return online ────────────────────────────
    # Detect recovery by watching for ready_since to change from the
    # baseline captured before the reboot.  This is clock-skew safe.
    step_set("await_online", StepStatus.RUNNING)
    deadline   = time.time() + REBOOT_ONLINE_TIMEOUT
    came_back  = False

    while time.time() < deadline:
        try:
            for nd in k8s_ops.get_nodes(auth=k8s_auth):
                if nd["name"] != state.k8s_name or not nd["ready"]:
                    continue
                ready_since = nd.get("ready_since")
                if ready_since is not None and ready_since != baseline_ready_since:
                    state.reboot_downtime = time.time() - state.reboot_start
                    came_back = True
                    break
        except Exception:
            pass

        if came_back:
            break

        elapsed = int(time.time() - state.reboot_start)
        step = state.get_step("await_online")
        if step:
            step.detail = f"{elapsed}s elapsed"
        update_cb()
        time.sleep(5)

    if not came_back:
        abort("await_online", "Timeout — node did not return online")
        return

    dt = int(state.reboot_downtime)
    step_set("await_online", StepStatus.SUCCESS, f"Online — downtime {dt}s")
    state.phase        = NodePhase.IDLE
    state.etcd_healthy = None   # needs re-verification on next selection
    log(f"✓ '{state.k8s_name}' back online — total downtime: {dt}s")
    if audit_cb:
        audit_cb("completed", f"downtime={dt}s")
    update_cb()


def run_drain_quick(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth: Optional[k8s_ops.K8sAuth] = None,
    openstack_auth: Optional[openstack_ops.OpenStackAuth] = None,
) -> None:
    """Cordon, optionally disable Nova, then drain pods.  Runs in a daemon thread."""

    def step_set(key: str, status: StepStatus, detail: str = "") -> None:
        step = state.get_step(key)
        if step:
            step.status = status
            if detail:
                step.detail = detail
        update_cb()

    def log(msg: str) -> None:
        state.add_log(msg)
        log_cb(msg)
        update_cb()

    def abort(key: str, reason: str) -> None:
        step_set(key, StepStatus.FAILED, reason)
        state.phase = NodePhase.ERROR
        log(f"Drain aborted: {reason}")
        if audit_cb:
            audit_cb("failed", f"step={key} reason={reason}")
        update_cb()

    step_set("cordon", StepStatus.RUNNING)
    try:
        k8s_ops.cordon_node(state.k8s_name, log, auth=k8s_auth)
        state.k8s_cordoned = True
        step_set("cordon", StepStatus.SUCCESS)
    except Exception as exc:
        abort("cordon", str(exc))
        return

    if state.is_compute:
        step_set("disable_nova", StepStatus.RUNNING)
        try:
            openstack_ops.disable_compute_service(state.hypervisor, log, auth=openstack_auth)
            state.compute_status = "disabled"
            step_set("disable_nova", StepStatus.SUCCESS)
        except Exception as exc:
            abort("disable_nova", str(exc))
            return

    step_set("drain_k8s", StepStatus.RUNNING)
    try:
        k8s_ops.drain_node(state.k8s_name, log, auth=k8s_auth)
        step_set("drain_k8s", StepStatus.SUCCESS)
    except Exception as exc:
        abort("drain_k8s", str(exc))
        return

    state.phase = NodePhase.IDLE
    log(f"✓ '{state.k8s_name}' drained — cordoned, nova disabled, pods evicted")
    if audit_cb:
        audit_cb("completed", "cordoned, nova disabled, pods evicted")
    update_cb()


def run_undrain(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth: Optional[k8s_ops.K8sAuth] = None,
    openstack_auth: Optional[openstack_ops.OpenStackAuth] = None,
) -> None:
    """Enable Nova (if compute) then uncordon.  Runs in a daemon thread."""

    def step_set(key: str, status: StepStatus, detail: str = "") -> None:
        step = state.get_step(key)
        if step:
            step.status = status
            if detail:
                step.detail = detail
        update_cb()

    def log(msg: str) -> None:
        state.add_log(msg)
        log_cb(msg)
        update_cb()

    def abort(key: str, reason: str) -> None:
        step_set(key, StepStatus.FAILED, reason)
        state.phase = NodePhase.ERROR
        log(f"Undrain aborted: {reason}")
        if audit_cb:
            audit_cb("failed", f"step={key} reason={reason}")
        update_cb()

    if state.is_compute:
        step_set("enable_nova", StepStatus.RUNNING)
        try:
            openstack_ops.enable_compute_service(state.hypervisor, log, auth=openstack_auth)
            state.compute_status = "up"
            step_set("enable_nova", StepStatus.SUCCESS)
        except Exception as exc:
            abort("enable_nova", str(exc))
            return

    step_set("uncordon", StepStatus.RUNNING)
    try:
        k8s_ops.uncordon_node(state.k8s_name, log, auth=k8s_auth)
        state.k8s_cordoned = False
        step_set("uncordon", StepStatus.SUCCESS)
    except Exception as exc:
        abort("uncordon", str(exc))
        return

    state.phase = NodePhase.IDLE
    log(f"✓ '{state.k8s_name}' undrained — nova enabled, node uncordoned")
    if audit_cb:
        audit_cb("completed", "nova enabled, node uncordoned")
    update_cb()
