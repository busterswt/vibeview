"""Background workflow runners.  Execute entirely in worker threads."""
from __future__ import annotations

import time
from typing import Callable, Optional

from .models import NodeState
from .operations import k8s_ops, openstack_ops
from .reboot import issue_reboot
from . import workflows_evacuate, workflows_maintenance

UpdateFn = Callable[[], None]
LogFn = Callable[[str], None]
AuditCb = Callable[[str, str], None]


def run_workflow(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth: Optional[k8s_ops.K8sAuth] = None,
    openstack_auth: Optional[openstack_ops.OpenStackAuth] = None,
) -> None:
    """Execute the full evacuation workflow. Designed to run in a daemon thread."""
    workflows_evacuate.run_workflow(
        state,
        update_cb,
        log_cb,
        k8s_ops=k8s_ops,
        openstack_ops=openstack_ops,
        time_module=time,
        audit_cb=audit_cb,
        k8s_auth=k8s_auth,
        openstack_auth=openstack_auth,
    )


def run_reboot(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth: Optional[k8s_ops.K8sAuth] = None,
) -> None:
    """Issue a reboot and track downtime.  Runs in a daemon thread."""
    workflows_maintenance.run_reboot(
        state,
        update_cb,
        log_cb,
        k8s_ops=k8s_ops,
        issue_reboot_fn=issue_reboot,
        time_module=time,
        audit_cb=audit_cb,
        k8s_auth=k8s_auth,
    )


def run_drain_quick(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth: Optional[k8s_ops.K8sAuth] = None,
    openstack_auth: Optional[openstack_ops.OpenStackAuth] = None,
) -> None:
    """Cordon, optionally disable Nova, then drain pods.  Runs in a daemon thread."""
    workflows_maintenance.run_drain_quick(
        state,
        update_cb,
        log_cb,
        k8s_ops=k8s_ops,
        openstack_ops=openstack_ops,
        audit_cb=audit_cb,
        k8s_auth=k8s_auth,
        openstack_auth=openstack_auth,
    )


def run_undrain(
    state: NodeState,
    update_cb: UpdateFn,
    log_cb: LogFn,
    audit_cb: Optional[AuditCb] = None,
    k8s_auth: Optional[k8s_ops.K8sAuth] = None,
    openstack_auth: Optional[openstack_ops.OpenStackAuth] = None,
) -> None:
    """Enable Nova (if compute) then uncordon.  Runs in a daemon thread."""
    workflows_maintenance.run_undrain(
        state,
        update_cb,
        log_cb,
        k8s_ops=k8s_ops,
        openstack_ops=openstack_ops,
        audit_cb=audit_cb,
        k8s_auth=k8s_auth,
        openstack_auth=openstack_auth,
    )
