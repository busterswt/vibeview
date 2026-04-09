"""Live report synthesis helpers for the web UI."""
from __future__ import annotations

import csv
import io

from ..models import NodeState
from ..operations import k8s_ops
from .inventory import DrainoServer


def _node_roles(state: NodeState) -> list[str]:
    roles: list[str] = []
    if state.is_compute:
        roles.append("compute")
    if state.is_etcd:
        roles.append("etcd")
    if state.hosts_mariadb:
        roles.append("mariadb")
    if state.is_edge:
        roles.append("edge")
    if not roles:
        roles.append("host")
    return roles


def _node_blockers(state: NodeState) -> list[str]:
    blockers: list[str] = []
    if state.phase.name.lower() != "idle":
        blockers.append(f"workflow {state.phase.name.lower()} is active")
    if not state.k8s_ready:
        blockers.append("kubernetes node is not ready")
    if state.compute_status == "down":
        blockers.append("nova compute service is down")
    if state.node_agent_ready is False:
        blockers.append("node-agent is unavailable")
    if state.is_etcd and state.etcd_healthy is not True:
        blockers.append("etcd health requires review")
    return blockers


def _node_reviews(state: NodeState) -> list[str]:
    reviews: list[str] = []
    if not state.k8s_cordoned:
        reviews.append("kubernetes node is not cordoned")
    if any((taint.get("effect") or "") == "NoSchedule" for taint in (state.k8s_taints or [])):
        reviews.append("NoSchedule taint is present")
    if state.is_compute and state.compute_status != "disabled":
        reviews.append("nova compute service is not disabled")
    if state.reboot_required:
        reviews.append("reboot is required")
    if state.hosts_mariadb:
        reviews.append("hosts MariaDB cluster workloads")
    if state.is_edge:
        reviews.append("hosts OVN edge/gateway responsibilities")
    return reviews


def _in_maintenance_posture(state: NodeState) -> bool:
    if not state.k8s_cordoned:
        return False
    if state.is_compute and state.compute_status != "disabled":
        return False
    return True


def _k8s_status_label(state: NodeState) -> str:
    if not state.k8s_ready:
        return "not-ready"
    if state.k8s_cordoned:
        return "cordoned"
    return "ready"


def _nova_status_label(state: NodeState) -> str:
    if not state.is_compute:
        return "n/a"
    return state.compute_status or "unknown"


def build_maintenance_readiness_report(server: DrainoServer) -> dict:
    """Build a live maintenance-readiness report from the running environment."""
    items: list[dict] = []
    states = sorted(server.node_states.values(), key=lambda item: item.k8s_name)

    for state in states:
        k8s_detail = k8s_ops.get_node_k8s_detail(state.k8s_name, auth=server.k8s_auth)
        blockers = _node_blockers(state)
        reviews = _node_reviews(state)
        if blockers:
            verdict = "blocked"
        elif _in_maintenance_posture(state):
            verdict = "ready"
        else:
            verdict = "review"

        if blockers:
            reason = "; ".join(blockers)
        elif verdict == "review" and reviews:
            reason = "; ".join(reviews)
        else:
            reason = "none"

        items.append({
            "node": state.k8s_name,
            "availability_zone": state.availability_zone or "—",
            "roles": _node_roles(state),
            "nova_status": _nova_status_label(state),
            "k8s_status": _k8s_status_label(state),
            "vm_count": state.vm_count if state.vm_count is not None else 0,
            "pod_count": k8s_detail.get("pod_count"),
            "reboot_required": bool(state.reboot_required),
            "node_agent_ready": bool(state.node_agent_ready),
            "verdict": verdict,
            "blocking_reason": reason,
            "is_compute": state.is_compute,
        })

    findings: list[dict] = []
    for item in items:
        if item["verdict"] == "blocked":
            severity = "high"
        elif item["verdict"] == "review":
            severity = "medium"
        else:
            continue
        findings.append({
            "severity": severity,
            "node": item["node"],
            "message": item["blocking_reason"],
        })
    findings = findings[:4]

    total = len(items)
    ready_count = sum(1 for item in items if item["verdict"] == "ready")
    blocked_count = sum(1 for item in items if item["verdict"] == "blocked")
    review_count = sum(1 for item in items if item["verdict"] == "review")
    reboot_required_count = sum(1 for item in items if item["reboot_required"])
    no_agent_count = sum(1 for item in items if not item["node_agent_ready"])

    return {
        "report": {
            "key": "maintenance-readiness",
            "title": "Maintenance Readiness Report",
            "subtitle": "Live environment snapshot for node maintenance planning.",
            "source": "Kubernetes + OpenStack + OVN + node-agent",
            "scope": {
                "nodes": total,
                "computes": sum(1 for item in items if item["is_compute"]),
            },
            "summary": {
                "ready_now": ready_count,
                "blocked": blocked_count,
                "review": review_count,
                "reboot_required": reboot_required_count,
                "no_agent": no_agent_count,
            },
            "findings": findings,
            "items": items,
        },
        "error": None,
    }


def render_maintenance_readiness_csv(report: dict) -> str:
    """Render the maintenance-readiness report to CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "node",
        "availability_zone",
        "roles",
        "nova_status",
        "k8s_status",
        "vm_count",
        "pod_count",
        "reboot_required",
        "node_agent_ready",
        "verdict",
        "blocking_reason",
    ])
    for item in report.get("items", []):
        writer.writerow([
            item.get("node", ""),
            item.get("availability_zone", ""),
            ",".join(item.get("roles", [])),
            item.get("nova_status", ""),
            item.get("k8s_status", ""),
            item.get("vm_count", ""),
            item.get("pod_count", ""),
            "yes" if item.get("reboot_required") else "no",
            "yes" if item.get("node_agent_ready") else "no",
            item.get("verdict", ""),
            item.get("blocking_reason", ""),
        ])
    return output.getvalue()
