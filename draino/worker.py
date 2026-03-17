"""Background workflow runner.  Executes entirely in a worker thread."""
from __future__ import annotations

import time
from typing import Callable

from .models import InstanceInfo, NodePhase, NodeState, StepStatus
from .operations import k8s_ops, openstack_ops

UpdateFn = Callable[[], None]
LogFn    = Callable[[str], None]

POLL_INTERVAL  = 15    # seconds between migration / drain status polls
MIGRATE_TIMEOUT = 1800  # hard limit for all live migrations (seconds)
EMPTY_TIMEOUT   = 900   # hard limit for hypervisor-empty wait (seconds)


def run_workflow(state: NodeState, update_cb: UpdateFn, log_cb: LogFn) -> None:
    """Execute the full evacuation workflow.  Designed to run in a daemon thread.

    *update_cb* is called (via call_from_thread) whenever state changes.
    *log_cb* receives plain-text log strings for the global event log.
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
        update_cb()

    # ── Initialise ────────────────────────────────────────────────────────
    state.phase = NodePhase.RUNNING
    state.instances = []
    state.init_steps()
    update_cb()

    # ── Step 1: Cordon ────────────────────────────────────────────────────
    step_set("cordon", StepStatus.RUNNING)
    try:
        k8s_ops.cordon_node(state.k8s_name, log)
        step_set("cordon", StepStatus.SUCCESS)
    except Exception as exc:
        abort("cordon", str(exc))
        return

    # ── Step 2: Disable Nova compute ──────────────────────────────────────
    step_set("disable_nova", StepStatus.RUNNING)
    try:
        openstack_ops.disable_compute_service(state.hypervisor, log)
        state.compute_status = "disabled"   # reflect immediately in node panel
        step_set("disable_nova", StepStatus.SUCCESS)
    except Exception as exc:
        abort("disable_nova", str(exc))
        return

    # ── Step 3: Enumerate instances ───────────────────────────────────────
    step_set("list_instances", StepStatus.RUNNING)
    try:
        servers = openstack_ops.list_servers_on_host(state.hypervisor, log)
        amp_map = openstack_ops.get_amphora_lb_mapping(log)
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
                openstack_ops.live_migrate_server(inst.id, log)
                inst.migration_status = "migrating"
            except Exception as live_exc:
                # A 504 timeout means Nova accepted the request but the HTTP
                # response timed out — the instance may already be migrating.
                # Check task_state before attempting a cold fallback; issuing
                # cold migrate against an already-migrating instance gets a 409.
                task_state = openstack_ops.get_server_task_state(inst.id) or ""
                if "migrat" in task_state.lower():
                    log(
                        f"Live migration for '{inst.name}' timed out but instance "
                        f"is already in task_state '{task_state}' — continuing to poll"
                    )
                    inst.migration_status = "migrating"
                else:
                    log(f"Live migration failed for '{inst.name}': {live_exc} — trying cold migration")
                    try:
                        openstack_ops.cold_migrate_server(inst.id, log)
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
                nova_status = openstack_ops.get_server_status(inst.id)

                if nova_status == "VERIFY_RESIZE":
                    # Cold migration landed — confirm it automatically
                    inst.migration_status = "confirming"
                    update_cb()
                    try:
                        openstack_ops.confirm_resize_server(inst.id, log)
                    except Exception as exc:
                        log(f"Confirm resize failed for '{inst.name}': {exc}")
                        inst.migration_status = "failed"
                elif nova_status == "ACTIVE":
                    # Check migration records to confirm it actually moved
                    migs = openstack_ops.get_server_migrations(inst.id)
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
                openstack_ops.failover_loadbalancer(lb_id, log)
                ok        = openstack_ops.wait_for_lb_active(lb_id, log)
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
            count = openstack_ops.count_servers_on_host(state.hypervisor)
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
        k8s_ops.drain_node(state.k8s_name, log)
        step_set("drain_k8s", StepStatus.SUCCESS)
    except Exception as exc:
        abort("drain_k8s", str(exc))
        return

    # ── Done ──────────────────────────────────────────────────────────────
    state.phase = NodePhase.COMPLETE
    log(f"✓ '{state.k8s_name}' fully evacuated — ready for reboot!")
    update_cb()
