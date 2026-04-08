"""Full evacuation workflow implementation."""
from __future__ import annotations

from typing import Callable, Optional

from .models import InstanceInfo, NodePhase, NodeState, StepStatus
from . import workflows_maintenance

UpdateFn = Callable[[], None]
LogFn = Callable[[str], None]
AuditCb = Callable[[str, str], None]

POLL_INTERVAL = 15
MIGRATE_TIMEOUT = 1800
EMPTY_TIMEOUT = 900


def run_workflow(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    *,
    k8s_ops,
    openstack_ops,
    time_module,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth=None,
    openstack_auth=None,
) -> None:
    ctx = workflows_maintenance.WorkflowContext(
        state=state,
        update_cb=update_cb,
        log_cb=log_cb,
        audit_cb=audit_cb,
        abort_prefix="Evacuation aborted",
    )

    state.phase = NodePhase.RUNNING
    state.instances = []
    state.init_steps()
    update_cb()

    ctx.step_set("cordon", StepStatus.RUNNING)
    try:
        k8s_ops.cordon_node(state.k8s_name, ctx.log, auth=k8s_auth)
        ctx.step_set("cordon", StepStatus.SUCCESS)
    except Exception as exc:
        ctx.abort("cordon", str(exc))
        return

    ctx.step_set("disable_nova", StepStatus.RUNNING)
    try:
        openstack_ops.disable_compute_service(state.hypervisor, ctx.log, auth=openstack_auth)
        state.compute_status = "disabled"
        ctx.step_set("disable_nova", StepStatus.SUCCESS)
    except Exception as exc:
        ctx.abort("disable_nova", str(exc))
        return

    ctx.step_set("list_instances", StepStatus.RUNNING)
    try:
        servers = openstack_ops.list_servers_on_host(state.hypervisor, ctx.log, auth=openstack_auth)
        amp_map = openstack_ops.get_amphora_lb_mapping(ctx.log, auth=openstack_auth)
        for s in servers:
            lb_id = amp_map.get(s["id"])
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
        n_vm = sum(1 for i in state.instances if not i.is_amphora)
        n_amp = sum(1 for i in state.instances if i.is_amphora)
        state.vm_count = n_vm
        state.amphora_count = n_amp
        ctx.step_set("list_instances", StepStatus.SUCCESS, f"{n_vm} VM(s), {n_amp} Amphora")
    except Exception as exc:
        ctx.abort("list_instances", str(exc))
        return

    ctx.step_set("migrate_vms", StepStatus.RUNNING)
    vms = [i for i in state.instances if not i.is_amphora]

    if not vms:
        ctx.step_set("migrate_vms", StepStatus.SUCCESS, "No VMs to migrate")
    else:
        for inst in vms:
            inst.migration_status = "queued"
            update_cb()
            try:
                openstack_ops.live_migrate_server(inst.id, ctx.log, auth=openstack_auth)
                inst.migration_status = "migrating"
            except Exception as live_exc:
                task_state = openstack_ops.get_server_task_state(inst.id, auth=openstack_auth) or ""
                if "migrat" in task_state.lower():
                    ctx.log(
                        f"Live migration for '{inst.name}' timed out but instance "
                        f"is already in task_state '{task_state}' — continuing to poll"
                    )
                    inst.migration_status = "migrating"
                else:
                    ctx.log(f"Live migration failed for '{inst.name}': {live_exc} — trying cold migration")
                    try:
                        openstack_ops.cold_migrate_server(inst.id, ctx.log, auth=openstack_auth)
                        inst.migration_status = "cold-migrating"
                    except Exception as cold_exc:
                        ctx.log(f"Cold migration also failed for '{inst.name}': {cold_exc}")
                        inst.migration_status = "failed"
            update_cb()

        deadline = time_module.time() + MIGRATE_TIMEOUT
        while time_module.time() < deadline:
            pending = [i for i in vms if i.migration_status not in ("complete", "failed")]
            if not pending:
                break

            for inst in pending:
                nova_status = openstack_ops.get_server_status(inst.id, auth=openstack_auth)

                if nova_status == "VERIFY_RESIZE":
                    inst.migration_status = "confirming"
                    update_cb()
                    try:
                        openstack_ops.confirm_resize_server(inst.id, ctx.log, auth=openstack_auth)
                    except Exception as exc:
                        ctx.log(f"Confirm resize failed for '{inst.name}': {exc}")
                        inst.migration_status = "failed"
                elif nova_status == "ACTIVE":
                    migs = openstack_ops.get_server_migrations(inst.id, auth=openstack_auth)
                    if migs:
                        latest = migs[0]["status"].lower()
                        if latest in ("completed", "done", "finished"):
                            inst.migration_status = "complete"
                        elif latest in ("error", "failed"):
                            inst.migration_status = "failed"
                            ctx.log(f"Migration failed for '{inst.name}'")
                    else:
                        inst.migration_status = "complete"
                elif nova_status == "ERROR":
                    inst.migration_status = "failed"
                    ctx.log(f"Server '{inst.name}' entered ERROR state")

            done_n = sum(1 for i in vms if i.migration_status == "complete")
            failed_n = sum(1 for i in vms if i.migration_status == "failed")
            step = state.get_step("migrate_vms")
            if step:
                step.detail = f"{done_n}/{len(vms)} done, {failed_n} failed"
            update_cb()
            time_module.sleep(POLL_INTERVAL)

        failed_n = sum(1 for i in vms if i.migration_status == "failed")
        done_n = sum(1 for i in vms if i.migration_status == "complete")
        if failed_n:
            ctx.abort("migrate_vms", f"{done_n}/{len(vms)} migrated — {failed_n} failed")
            return
        ctx.step_set("migrate_vms", StepStatus.SUCCESS, f"{done_n}/{len(vms)} migrated")

    ctx.step_set("failover_lbs", StepStatus.RUNNING)
    amphora = [i for i in state.instances if i.is_amphora and i.lb_id]

    if not amphora:
        ctx.step_set("failover_lbs", StepStatus.SUCCESS, "No Amphora to fail over")
    else:
        lb_ids = list({i.lb_id for i in amphora})
        any_failed = False

        for lb_id in lb_ids:
            for inst in amphora:
                if inst.lb_id == lb_id:
                    inst.failover_status = "failing_over"
            update_cb()

            try:
                openstack_ops.failover_loadbalancer(lb_id, ctx.log, auth=openstack_auth)
                ok = openstack_ops.wait_for_lb_active(lb_id, ctx.log, auth=openstack_auth)
                fo_status = "complete" if ok else "failed"
                if not ok:
                    any_failed = True
                    ctx.log(f"LB {lb_id} did not return to ACTIVE")
            except Exception as exc:
                ctx.log(f"Failover error for LB {lb_id}: {exc}")
                fo_status = "failed"
                any_failed = True

            for inst in amphora:
                if inst.lb_id == lb_id:
                    inst.failover_status = fo_status
            update_cb()

        if any_failed:
            ctx.abort("failover_lbs", "One or more LB failovers failed")
            return
        ctx.step_set("failover_lbs", StepStatus.SUCCESS, f"{len(lb_ids)} LB(s) failed over")

    ctx.step_set("await_empty", StepStatus.RUNNING)
    deadline = time_module.time() + EMPTY_TIMEOUT
    count = -1
    while time_module.time() < deadline:
        try:
            count = openstack_ops.count_servers_on_host(state.hypervisor, auth=openstack_auth)
        except Exception:
            count = -1
        step = state.get_step("await_empty")
        if step:
            step.detail = f"{count} instance(s) remaining" if count >= 0 else "checking…"
        if count >= 0:
            state.vm_count = max(0, count - (state.amphora_count or 0))
            state.amphora_count = state.amphora_count
        update_cb()
        if count == 0:
            state.vm_count = 0
            state.amphora_count = 0
            break
        time_module.sleep(POLL_INTERVAL)
    else:
        ctx.abort("await_empty", f"Timeout — {count} instance(s) still present")
        return

    ctx.step_set("await_empty", StepStatus.SUCCESS, "Hypervisor empty")

    ctx.step_set("drain_k8s", StepStatus.RUNNING)
    try:
        k8s_ops.drain_node(state.k8s_name, ctx.log, auth=k8s_auth)
        ctx.step_set("drain_k8s", StepStatus.SUCCESS)
    except Exception as exc:
        ctx.abort("drain_k8s", str(exc))
        return

    state.phase = NodePhase.COMPLETE
    ctx.log(f"✓ '{state.k8s_name}' fully evacuated — ready for reboot!")
    if audit_cb:
        audit_cb("completed", "hypervisor empty, K8s node drained")
    update_cb()
