"""Node state serialisation helpers for the web UI."""
from __future__ import annotations

from ..models import NodeState


def serialise_state(state: NodeState) -> dict:
    """Convert a NodeState to a JSON-serialisable dict."""
    return {
        "k8s_name": state.k8s_name,
        "hypervisor": state.hypervisor,
        "phase": state.phase.name.lower(),
        "k8s_ready": state.k8s_ready,
        "k8s_cordoned": state.k8s_cordoned,
        "k8s_taints": list(state.k8s_taints),
        "kernel_version": state.kernel_version,
        "latest_kernel_version": state.latest_kernel_version,
        "uptime": state.uptime,
        "reboot_required": state.reboot_required,
        "node_agent_ready": state.node_agent_ready,
        "is_edge": state.is_edge,
        "is_network": state.is_network,
        "is_etcd": state.is_etcd,
        "hosts_mariadb": state.hosts_mariadb,
        "etcd_healthy": state.etcd_healthy,
        "etcd_checking": state.etcd_checking,
        "is_compute": state.is_compute,
        "compute_status": state.compute_status,
        "compute_missing_from_openstack": state.compute_missing_from_openstack,
        "amphora_count": state.amphora_count,
        "vm_count": state.vm_count,
        "availability_zone": state.availability_zone,
        "aggregates": state.aggregates,
        "preflight_loading": state.preflight_loading,
        "preflight_instances": state.preflight_instances,
        "reboot_start": state.reboot_start,
        "reboot_downtime": state.reboot_downtime,
        "steps": [
            {
                "key": step.key,
                "label": step.label,
                "status": step.status.name.lower(),
                "detail": step.detail,
            }
            for step in state.steps
        ],
        "instances": [
            {
                "id": instance.id,
                "name": instance.name,
                "status": instance.status,
                "is_amphora": instance.is_amphora,
                "migration_status": instance.migration_status,
                "lb_id": instance.lb_id,
                "failover_status": instance.failover_status,
            }
            for instance in state.instances
        ],
        "log_buffer": list(state.log_buffer[-100:]),
    }
