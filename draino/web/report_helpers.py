"""Live report synthesis helpers for the web UI."""
from __future__ import annotations

import csv
import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from ..models import NodeState
from ..operations import k8s_ops, openstack_ops
from .inventory import DrainoServer


def _host_aliases(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    aliases: list[str] = []
    for candidate in (text, text.lower(), text.split(".", 1)[0], text.split(".", 1)[0].lower()):
        if candidate and candidate not in aliases:
            aliases.append(candidate)
    return aliases


def _lookup_host_summary(summaries: dict[str, dict], *candidates: str | None) -> dict:
    for candidate in candidates:
        for alias in _host_aliases(candidate):
            summary = summaries.get(alias)
            if summary:
                return summary
    return {}


def _load_hypervisor_detail(server: DrainoServer, *candidates: str | None) -> dict:
    seen: set[str] = set()
    last_detail: dict = {}
    for candidate in candidates:
        for alias in _host_aliases(candidate):
            if alias in seen:
                continue
            seen.add(alias)
            detail = openstack_ops.get_hypervisor_detail(alias, auth=server.openstack_auth)
            if any(detail.get(key) is not None for key in ("vcpus", "vcpus_used", "memory_mb", "memory_mb_used")):
                return detail
            last_detail = detail
    return last_detail


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
    if state.is_etcd:
        blockers.append("etcd requires staggered reboots")
    if state.hosts_mariadb:
        blockers.append("mariadb requires staggered reboots")
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
        return "-"
    return state.compute_status or "unknown"


def _finding_priority(item: dict) -> tuple[int, int, str]:
    message = (item.get("message") or "").lower()
    severity = item.get("severity")
    severity_rank = 0 if severity == "high" else 1
    if "mariadb requires staggered reboots" in message:
        detail_rank = 0
    elif "etcd requires staggered reboots" in message:
        detail_rank = 1
    else:
        detail_rank = 2
    return (severity_rank, detail_rank, item.get("node") or "")


def build_maintenance_readiness_report(server: DrainoServer) -> dict:
    """Build a live maintenance-readiness report from the running environment."""
    started = time.perf_counter()
    items: list[dict] = []
    states = sorted(server.node_states.values(), key=lambda item: item.k8s_name)
    k8s_detail_ms = 0.0

    for state in states:
        k8s_started = time.perf_counter()
        k8s_detail = k8s_ops.get_node_k8s_detail(state.k8s_name, auth=server.k8s_auth)
        k8s_detail_ms += (time.perf_counter() - k8s_started) * 1000.0
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
    findings.sort(key=_finding_priority)
    findings = findings[:4]

    total = len(items)
    ready_count = sum(1 for item in items if item["verdict"] == "ready")
    blocked_count = sum(1 for item in items if item["verdict"] == "blocked")
    review_count = sum(1 for item in items if item["verdict"] == "review")
    reboot_required_count = sum(1 for item in items if item["reboot_required"])
    no_agent_count = sum(1 for item in items if not item["node_agent_ready"])
    total_ms = (time.perf_counter() - started) * 1000.0

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
            "debug": {
                "timing_ms": {
                    "total": round(total_ms, 1),
                    "k8s_detail": round(k8s_detail_ms, 1),
                },
                "counts": {
                    "nodes": total,
                    "k8s_detail_calls": len(states),
                },
            },
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


def _as_int(value) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _capacity_status(state: NodeState) -> tuple[str, str]:
    blockers = _node_blockers(state)
    if blockers:
        return ("blocked", blockers[0])

    reviews: list[str] = []
    if state.is_edge:
        reviews.append("hosts OVN edge/gateway responsibilities")
    if not state.k8s_cordoned:
        reviews.append("kubernetes node is not cordoned")
    if state.compute_status != "disabled":
        reviews.append("nova compute service is not disabled")
    if reviews:
        return ("review", "; ".join(reviews))
    return ("drain-safe", "host is in maintenance posture")


def _usage_percent(used: int | None, total: int | None) -> float | None:
    if used is None or total in (None, 0):
        return None
    return (used / total) * 100.0


def _headroom_foot(percent_free: float | None, warn_text: str, ok_text: str) -> str:
    if percent_free is None:
        return "Live data incomplete"
    if percent_free < 20:
        return warn_text
    return ok_text


def _severity_for_percent(percent: float | None) -> str:
    if percent is None:
        return "medium"
    if percent >= 85:
        return "high"
    if percent >= 70:
        return "medium"
    return "low"


def build_capacity_headroom_report(server: DrainoServer) -> dict:
    """Build a live capacity and headroom report from current compute state."""
    started = time.perf_counter()
    items: list[dict] = []
    az_map: dict[str, dict] = {}
    total_vcpus = 0
    total_vcpus_free = 0
    total_memory_mb = 0
    total_memory_mb_free = 0
    total_pods = 0
    total_pods_free = 0
    total_instances = 0
    total_running_pods = 0

    compute_states = sorted(
        (state for state in server.node_states.values() if state.is_compute),
        key=lambda item: item.k8s_name,
    )
    host_summary_started = time.perf_counter()
    host_summaries = openstack_ops.get_all_host_summaries(auth=server.openstack_auth)
    host_summary_ms = (time.perf_counter() - host_summary_started) * 1000.0
    k8s_started = time.perf_counter()
    pod_capacity = k8s_ops.get_node_pod_capacity_summary(auth=server.k8s_auth)
    k8s_detail_ms = (time.perf_counter() - k8s_started) * 1000.0

    hv_details: dict[str, dict] = {}
    hypervisor_detail_started = time.perf_counter()
    max_workers = min(8, max(1, len(compute_states)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(_load_hypervisor_detail, server, state.hypervisor, state.k8s_name): state.k8s_name
            for state in compute_states
        }
        for future in as_completed(future_map):
            hv_details[future_map[future]] = future.result()
    hypervisor_detail_ms = (time.perf_counter() - hypervisor_detail_started) * 1000.0

    for state in compute_states:
        summary = _lookup_host_summary(host_summaries, state.hypervisor, state.k8s_name)
        hv_detail = hv_details.get(state.k8s_name, {})
        k8s_detail = pod_capacity.get(state.k8s_name, {})

        vcpus = _as_int(hv_detail.get("vcpus"))
        vcpus_used = _as_int(hv_detail.get("vcpus_used"))
        memory_mb = _as_int(hv_detail.get("memory_mb"))
        memory_mb_used = _as_int(hv_detail.get("memory_mb_used"))
        pods_allocatable = _as_int(k8s_detail.get("pods_allocatable"))
        pod_count = _as_int(k8s_detail.get("pod_count")) or 0

        if vcpus is not None:
            total_vcpus += vcpus
        if vcpus is not None and vcpus_used is not None:
            total_vcpus_free += max(0, vcpus - vcpus_used)
        if memory_mb is not None:
            total_memory_mb += memory_mb
        if memory_mb is not None and memory_mb_used is not None:
            total_memory_mb_free += max(0, memory_mb - memory_mb_used)
        if pods_allocatable is not None:
            total_pods += pods_allocatable
            total_pods_free += max(0, pods_allocatable - pod_count)
        total_instances += state.vm_count or 0
        total_running_pods += pod_count

        az = state.availability_zone or summary.get("availability_zone") or "unknown"
        aggregates = state.aggregates or summary.get("aggregates") or []
        az_entry = az_map.setdefault(az, {"vcpus": 0, "vcpus_used": 0, "hosts": 0})
        az_entry["hosts"] += 1
        if vcpus is not None:
            az_entry["vcpus"] += vcpus
        if vcpus_used is not None:
            az_entry["vcpus_used"] += vcpus_used

        maintenance_status, maintenance_detail = _capacity_status(state)
        items.append({
            "host": state.k8s_name,
            "availability_zone": az,
            "aggregates": aggregates,
            "vm_count": state.vm_count or 0,
            "amphora_count": state.amphora_count or 0,
            "vcpus": vcpus,
            "vcpus_used": vcpus_used,
            "memory_mb": memory_mb,
            "memory_mb_used": memory_mb_used,
            "pods_allocatable": pods_allocatable,
            "pod_count": pod_count,
            "maintenance_status": maintenance_status,
            "maintenance_detail": maintenance_detail,
        })

    az_headroom: list[dict] = []
    for az, values in sorted(az_map.items()):
        percent = _usage_percent(values["vcpus_used"], values["vcpus"])
        az_headroom.append({
            "availability_zone": az,
            "vcpus": values["vcpus"],
            "vcpus_used": values["vcpus_used"],
            "vcpus_percent_used": percent,
            "severity": _severity_for_percent(percent),
        })

    findings: list[dict] = []
    for entry in az_headroom:
        if entry["severity"] == "high":
            findings.append({
                "severity": "high",
                "message": f"{entry['availability_zone']} is above 85% vCPU allocation and should be treated as a maintenance constraint.",
            })
        elif entry["severity"] == "medium":
            findings.append({
                "severity": "medium",
                "message": f"{entry['availability_zone']} is above 70% vCPU allocation and should be watched before maintenance.",
            })
    edge_hosts = [item for item in items if "hosts OVN edge/gateway responsibilities" in item["maintenance_detail"]]
    if edge_hosts:
        findings.append({
            "severity": "medium",
            "message": f"{len(edge_hosts)} compute host(s) carry OVN edge/gateway responsibility and should be reviewed before maintenance.",
        })
    if total_pods and total_pods_free / total_pods >= 0.25:
        findings.append({
            "severity": "low",
            "message": "Pod density remains healthy across compute hosts. Kubernetes is not the current limiting factor.",
        })
    findings = findings[:4]

    free_vcpu_pct = (total_vcpus_free / total_vcpus * 100.0) if total_vcpus else None
    free_ram_pct = (total_memory_mb_free / total_memory_mb * 100.0) if total_memory_mb else None
    free_pod_pct = (total_pods_free / total_pods * 100.0) if total_pods else None
    drain_safe_count = sum(1 for item in items if item["maintenance_status"] == "drain-safe")
    total_ms = (time.perf_counter() - started) * 1000.0

    return {
        "report": {
            "key": "capacity-headroom",
            "title": "Capacity & Headroom Report",
            "subtitle": "Live operator-facing capacity report combining Nova hypervisor allocation, Kubernetes saturation, and cluster-level headroom.",
            "source": "Kubernetes + OpenStack",
            "scope": {
                "computes": len(items),
                "instances": total_instances,
                "pods": total_running_pods,
            },
            "summary": {
                "free_vcpu_pct": free_vcpu_pct,
                "free_ram_pct": free_ram_pct,
                "free_pod_pct": free_pod_pct,
                "drain_safe_hosts": drain_safe_count,
            },
            "summary_foot": {
                "free_vcpu_pct": _headroom_foot(free_vcpu_pct, "Some AZs are tight before maintenance", "Comfortable cluster-wide vCPU headroom"),
                "free_ram_pct": _headroom_foot(free_ram_pct, "Watch memory saturation before maintenance", "Memory headroom remains serviceable"),
                "free_pod_pct": _headroom_foot(free_pod_pct, "Pod density is constraining scheduling", "No immediate pod density issue"),
                "drain_safe_hosts": "Current maintenance concurrency is limited" if drain_safe_count < 3 else "Several hosts are available for staged maintenance",
            },
            "az_headroom": az_headroom,
            "findings": findings,
            "items": items,
            "debug": {
                "timing_ms": {
                    "total": round(total_ms, 1),
                    "openstack_host_summaries": round(host_summary_ms, 1),
                    "openstack_hypervisor_detail": round(hypervisor_detail_ms, 1),
                    "k8s_detail": round(k8s_detail_ms, 1),
                },
                "counts": {
                    "computes": len(items),
                    "hypervisor_detail_calls": len(compute_states),
                    "k8s_detail_calls": 1,
                },
            },
        },
        "error": None,
    }


def render_capacity_headroom_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "host",
        "availability_zone",
        "aggregates",
        "vm_count",
        "amphora_count",
        "vcpus_used",
        "vcpus",
        "memory_mb_used",
        "memory_mb",
        "pod_count",
        "pods_allocatable",
        "maintenance_status",
        "maintenance_detail",
    ])
    for item in report.get("items", []):
        writer.writerow([
            item.get("host", ""),
            item.get("availability_zone", ""),
            ",".join(item.get("aggregates", [])),
            item.get("vm_count", ""),
            item.get("amphora_count", ""),
            item.get("vcpus_used", ""),
            item.get("vcpus", ""),
            item.get("memory_mb_used", ""),
            item.get("memory_mb", ""),
            item.get("pod_count", ""),
            item.get("pods_allocatable", ""),
            item.get("maintenance_status", ""),
            item.get("maintenance_detail", ""),
        ])
    return output.getvalue()


def _project_vm_distribution_risk(item: dict) -> tuple[str, str]:
    vm_count = int(item.get("vm_count") or 0)
    host_count = int(item.get("host_count") or 0)
    top_host_pct = float(item.get("top_host_pct") or 0.0)
    top_host_count = int(item.get("top_host_count") or 0)
    dominant_host = bool(item.get("has_dominant_host"))
    if vm_count >= 5 and (host_count <= 1 or (dominant_host and top_host_pct >= 70)):
        return ("high", f"{top_host_count} of {vm_count} VMs are concentrated on {item.get('top_host') or 'one host'}")
    if vm_count >= 5 and (host_count <= 2 or (dominant_host and top_host_pct >= 50)):
        return ("medium", f"{top_host_count} of {vm_count} VMs are weighted to {item.get('top_host') or 'a small host set'}")
    return ("low", "VM distribution is not heavily concentrated")


def build_project_placement_report(server: DrainoServer) -> dict:
    """Build a live per-project VM placement concentration report."""
    started = time.perf_counter()
    distribution_started = time.perf_counter()
    projects = openstack_ops.get_project_vm_distribution(auth=server.openstack_auth)
    distribution_ms = (time.perf_counter() - distribution_started) * 1000.0

    items: list[dict] = []
    findings: list[dict] = []
    high_risk = 0
    medium_risk = 0
    single_host_projects = 0
    total_vms = 0

    for item in projects:
        risk, reason = _project_vm_distribution_risk(item)
        total_vms += int(item.get("vm_count") or 0)
        if item.get("host_count") == 1 and item.get("vm_count", 0) >= 3:
            single_host_projects += 1
        if risk == "high":
            high_risk += 1
            findings.append({
                "severity": "high",
                "node": item.get("project_name") or item.get("project_id") or "",
                "message": reason,
            })
        elif risk == "medium":
            medium_risk += 1
            findings.append({
                "severity": "medium",
                "node": item.get("project_name") or item.get("project_id") or "",
                "message": reason,
            })
        items.append({
            **item,
            "risk": risk,
            "reason": reason,
            "top_host_label": item.get("top_host") or "Even spread",
            "top_hosts_label": ", ".join(
                f"{host['host']} ({host['vm_count']})" for host in item.get("host_counts", [])[:3]
            ) or "—",
        })

    findings = sorted(findings, key=lambda item: (0 if item["severity"] == "high" else 1, item["node"]))[:6]
    largest_project = items[0] if items else None
    total_ms = (time.perf_counter() - started) * 1000.0

    return {
        "report": {
            "key": "project-placement",
            "title": "Project Placement Report",
            "subtitle": "Live tenant-level VM concentration view across compute hosts.",
            "source": "OpenStack Nova",
            "scope": {
                "projects": len(items),
                "instances": total_vms,
            },
            "summary": {
                "projects_at_risk": high_risk + medium_risk,
                "high_risk_projects": high_risk,
                "single_host_projects": single_host_projects,
                "largest_project_vms": largest_project["vm_count"] if largest_project else 0,
            },
            "summary_foot": {
                "projects_at_risk": "Projects whose VMs are overly concentrated on a small host set",
                "high_risk_projects": "Projects with severe single-host or top-host concentration",
                "single_host_projects": "Projects running on only one compute host",
                "largest_project_vms": f"Largest project: {largest_project['project_name']}" if largest_project else "No project data available",
            },
            "findings": findings,
            "items": items,
            "debug": {
                "timing_ms": {
                    "total": round(total_ms, 1),
                    "openstack_project_distribution": round(distribution_ms, 1),
                },
                "counts": {
                    "projects": len(items),
                },
            },
        },
        "error": None,
    }


def render_project_placement_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "project_name",
        "project_id",
        "vm_count",
        "host_count",
        "top_host",
        "top_host_count",
        "top_host_pct",
        "risk",
        "reason",
        "top_hosts",
    ])
    for item in report.get("items", []):
        writer.writerow([
            item.get("project_name", ""),
            item.get("project_id", ""),
            item.get("vm_count", ""),
            item.get("host_count", ""),
            item.get("top_host", ""),
            item.get("top_host_count", ""),
            round(float(item.get("top_host_pct") or 0.0), 1),
            item.get("risk", ""),
            item.get("reason", ""),
            item.get("top_hosts_label", ""),
        ])
    return output.getvalue()


def _safe_pct(numerator: float | int | None, denominator: float | int | None) -> float | None:
    try:
        if numerator is None or denominator in (None, 0):
            return None
        return (float(numerator) / float(denominator)) * 100.0
    except Exception:
        return None


def build_nova_activity_capacity_report(server: DrainoServer) -> dict:
    """Build an API-only Nova + Placement current-state and recent-activity report."""
    started = time.perf_counter()

    activity_started = time.perf_counter()
    activity = openstack_ops.get_nova_activity_snapshot(auth=server.openstack_auth, window_hours=24)
    activity_ms = (time.perf_counter() - activity_started) * 1000.0

    placement_started = time.perf_counter()
    placement = openstack_ops.get_placement_capacity_snapshot(auth=server.openstack_auth)
    placement_ms = (time.perf_counter() - placement_started) * 1000.0

    active_instances = activity.get("active_instances", [])
    deleted_visible = activity.get("deleted_visible", [])
    recent = activity.get("recent_activity", {})
    placement_items = placement.get("items", [])

    status_counts: dict[str, int] = {}
    project_map: dict[str, dict] = {}
    host_map: dict[str, dict] = {}
    az_project_mix: dict[str, dict] = {}

    for item in active_instances:
        status = str(item.get("status") or "UNKNOWN").upper()
        project_id = item.get("project_id") or "unknown"
        project_name = item.get("project_name") or project_id
        host = item.get("host") or "unknown"
        az = item.get("availability_zone") or "unknown"

        status_counts[status] = status_counts.get(status, 0) + 1

        project_entry = project_map.setdefault(project_id, {
            "project_id": project_id,
            "project_name": project_name,
            "active_instances": 0,
            "hosts": set(),
            "azs": set(),
        })
        project_entry["active_instances"] += 1
        project_entry["hosts"].add(host)
        project_entry["azs"].add(az)

        host_entry = host_map.setdefault(host, {
            "hypervisor": host,
            "active_instances": 0,
            "projects": set(),
        })
        host_entry["active_instances"] += 1
        host_entry["projects"].add(project_name)

        mix = az_project_mix.setdefault(az, {})
        mix[project_id] = mix.get(project_id, 0) + 1

    recent_changed = int(recent.get("changed") or 0)
    recent_created = int(recent.get("created") or 0)
    recent_deleted = int(recent.get("deleted") or 0)
    recent_updated = int(recent.get("updated") or 0)

    for item in deleted_visible:
        project_id = item.get("project_id") or "unknown"
        project_name = item.get("project_name") or project_id
        entry = project_map.setdefault(project_id, {
            "project_id": project_id,
            "project_name": project_name,
            "active_instances": 0,
            "hosts": set(),
            "azs": set(),
        })
        entry["deleted_visible"] = int(entry.get("deleted_visible") or 0) + 1

    for item in active_instances + deleted_visible:
        project_id = item.get("project_id") or "unknown"
        entry = project_map.setdefault(project_id, {
            "project_id": project_id,
            "project_name": item.get("project_name") or project_id,
            "active_instances": 0,
            "hosts": set(),
            "azs": set(),
        })
        created_at = str(item.get("created_at") or "")
        updated_at = str(item.get("updated_at") or "")
        deleted_at = str(item.get("deleted_at") or "")
        if deleted_at and _parse_window_ts(deleted_at, activity.get("since")):
            entry["recent_deletes"] = int(entry.get("recent_deletes") or 0) + 1
        elif created_at and _parse_window_ts(created_at, activity.get("since")):
            entry["recent_creates"] = int(entry.get("recent_creates") or 0) + 1
        elif updated_at and _parse_window_ts(updated_at, activity.get("since")):
            entry["recent_updates"] = int(entry.get("recent_updates") or 0) + 1

    project_items: list[dict] = []
    for entry in project_map.values():
        recent_creates = int(entry.get("recent_creates") or 0)
        recent_deletes = int(entry.get("recent_deletes") or 0)
        recent_updates = int(entry.get("recent_updates") or 0)
        recent_changes = recent_creates + recent_deletes + recent_updates
        signal = "stable"
        if recent_creates + recent_deletes >= 20:
            signal = "high-churn"
        elif recent_changes >= 10:
            signal = "growing"
        project_items.append({
            "project_id": entry["project_id"],
            "project_name": entry["project_name"],
            "active_instances": entry["active_instances"],
            "host_count": len(entry["hosts"]),
            "availability_zone_count": len(entry["azs"]),
            "recent_creates": recent_creates,
            "recent_deletes": recent_deletes,
            "recent_updates": recent_updates,
            "recent_changes": recent_changes,
            "deleted_visible": int(entry.get("deleted_visible") or 0),
            "signal": signal,
        })
    project_items.sort(key=lambda item: (-item["active_instances"], -item["recent_changes"], item["project_name"]))

    placement_by_host = {item.get("hypervisor"): item for item in placement_items}
    hypervisor_items: list[dict] = []
    unresolved_host_instances = 0
    for host, entry in host_map.items():
        if not host or host == "unknown":
            unresolved_host_instances += int(entry.get("active_instances") or 0)
            continue
        placement_item = placement_by_host.get(host, {})
        vcpu_pct = _safe_pct(placement_item.get("vcpus_used"), placement_item.get("vcpus_effective") or placement_item.get("vcpus"))
        ram_pct = _safe_pct(placement_item.get("memory_mb_used"), placement_item.get("memory_mb_effective") or placement_item.get("memory_mb"))
        hypervisor_items.append({
            "hypervisor": host,
            "availability_zone": placement_item.get("availability_zone") or "unknown",
            "active_instances": entry["active_instances"],
            "project_count": len(entry["projects"]),
            "vcpus_used_pct": vcpu_pct,
            "memory_mb_used_pct": ram_pct,
        })
    hypervisor_items.sort(key=lambda item: (-item["active_instances"], -(item.get("memory_mb_used_pct") or 0), item["hypervisor"]))

    az_items_map: dict[str, dict] = {}
    total_vcpus_effective = 0.0
    total_vcpus_used = 0.0
    total_ram_effective = 0.0
    total_ram_used = 0.0
    placement_hotspots: list[dict] = []
    for item in placement_items:
        az = item.get("availability_zone") or "unknown"
        entry = az_items_map.setdefault(az, {
            "availability_zone": az,
            "vcpus_effective": 0.0,
            "vcpus_used": 0.0,
            "memory_mb_effective": 0.0,
            "memory_mb_used": 0.0,
            "hosts": 0,
        })
        vcpus_effective = float(item.get("vcpus_effective") or item.get("vcpus") or 0.0)
        vcpus_used = float(item.get("vcpus_used") or 0.0)
        memory_effective = float(item.get("memory_mb_effective") or item.get("memory_mb") or 0.0)
        memory_used = float(item.get("memory_mb_used") or 0.0)
        entry["hosts"] += 1
        entry["vcpus_effective"] += vcpus_effective
        entry["vcpus_used"] += vcpus_used
        entry["memory_mb_effective"] += memory_effective
        entry["memory_mb_used"] += memory_used
        total_vcpus_effective += vcpus_effective
        total_vcpus_used += vcpus_used
        total_ram_effective += memory_effective
        total_ram_used += memory_used

        host_vcpu_pct = _safe_pct(vcpus_used, vcpus_effective)
        host_ram_pct = _safe_pct(memory_used, memory_effective)
        if (host_vcpu_pct or 0) >= 85 or (host_ram_pct or 0) >= 85:
            placement_hotspots.append({
                "hypervisor": item.get("hypervisor") or "",
                "availability_zone": az,
                "vcpus_used_pct": host_vcpu_pct,
                "memory_mb_used_pct": host_ram_pct,
            })

    az_items = sorted(
        ({
            **entry,
            "vcpus_used_pct": _safe_pct(entry["vcpus_used"], entry["vcpus_effective"]),
            "memory_mb_used_pct": _safe_pct(entry["memory_mb_used"], entry["memory_mb_effective"]),
            "project_count": len(az_project_mix.get(entry["availability_zone"], {})),
        } for entry in az_items_map.values()),
        key=lambda item: item["availability_zone"],
    )
    placement_hotspots.sort(key=lambda item: (-(item.get("memory_mb_used_pct") or 0), -(item.get("vcpus_used_pct") or 0), item["hypervisor"]))

    deleted_items = [{
        "name": item.get("name") or item.get("id") or "",
        "project_name": item.get("project_name") or item.get("project_id") or "",
        "host": item.get("host") or "—",
        "deleted_at": item.get("deleted_at") or item.get("updated_at") or "",
        "flavor": item.get("flavor") or "—",
    } for item in deleted_visible[:25]]

    findings: list[dict] = []
    hottest_az = max(az_items, key=lambda item: ((item.get("vcpus_used_pct") or 0) + (item.get("memory_mb_used_pct") or 0)), default=None)
    if hottest_az and ((hottest_az.get("vcpus_used_pct") or 0) >= 85 or (hottest_az.get("memory_mb_used_pct") or 0) >= 85):
        findings.append({
            "severity": "high",
            "message": f"Compute AZ {hottest_az['availability_zone']} is under elevated pressure from current Placement usage.",
        })
    churn_project = max(project_items, key=lambda item: item["recent_creates"] + item["recent_deletes"], default=None)
    if churn_project and (churn_project["recent_creates"] + churn_project["recent_deletes"]) >= 20:
        findings.append({
            "severity": "high",
            "message": f"Project {churn_project['project_name']} shows elevated 24h create/delete churn.",
        })
    if deleted_visible:
        findings.append({
            "severity": "medium",
            "message": f"{len(deleted_visible)} deleted instances remain queryable in Nova. Useful operationally, but not durable history.",
        })
    if unresolved_host_instances:
        findings.append({
            "severity": "medium",
            "message": f"{unresolved_host_instances} active instances did not expose a compute host in the Nova response.",
        })
    hottest_host = placement_hotspots[0] if placement_hotspots else None
    if hottest_host:
        findings.append({
            "severity": "medium",
            "message": f"Hypervisor {hottest_host['hypervisor']} is among the hottest current Placement consumers.",
        })

    status_items = [{"status": key, "count": value} for key, value in sorted(status_counts.items(), key=lambda item: (-item[1], item[0]))]
    summary = {
        "active_instances": len(active_instances),
        "deleted_visible": len(deleted_visible),
        "changed_since_window": recent_changed,
        "vcpu_headroom_pct": _safe_pct(max(0.0, total_vcpus_effective - total_vcpus_used), total_vcpus_effective),
        "ram_headroom_pct": _safe_pct(max(0.0, total_ram_effective - total_ram_used), total_ram_effective),
        "recent_creates": recent_created,
        "recent_deletes": recent_deleted,
        "recent_updates": recent_updated,
    }
    total_ms = (time.perf_counter() - started) * 1000.0

    return {
        "report": {
            "key": "nova-activity-capacity",
            "title": "Nova Activity & Capacity Snapshot",
            "subtitle": "Current Nova estate, recent queryable instance changes, and Placement-backed headroom. This report is API-derived only and does not persist historical snapshots.",
            "source": "OpenStack Nova + Placement",
            "scope": {
                "instances": len(active_instances),
                "deleted_visible": len(deleted_visible),
                "hypervisors": len(placement_items),
                "projects": len(project_items),
                "window_hours": int(activity.get("window_hours") or 24),
                "deleted_window_days": int(activity.get("deleted_window_days") or 15),
            },
            "summary": summary,
            "summary_foot": {
                "active_instances": f"{status_counts.get('ACTIVE', 0)} ACTIVE / {max(0, len(active_instances) - status_counts.get('ACTIVE', 0))} other current states",
                "deleted_visible": f"Deleted servers queryable from the last {int(activity.get('deleted_window_days') or 15)} days",
                "changed_since_window": f"{recent_created} creates / {recent_deleted} deletes / {recent_updated} updates",
                "vcpu_headroom_pct": f"{int(round(total_vcpus_used))} used / {int(round(total_vcpus_effective or 0))} effective",
                "ram_headroom_pct": f"{int(round(total_ram_used))} MiB used / {int(round(total_ram_effective or 0))} MiB effective",
            },
            "recent_activity": {
                "changed": recent_changed,
                "created": recent_created,
                "deleted": recent_deleted,
                "updated": recent_updated,
            },
            "findings": findings[:6],
            "az_items": az_items,
            "status_items": status_items,
            "project_items": project_items[:20],
            "hypervisor_items": hypervisor_items[:20],
            "deleted_items": deleted_items,
            "placement_hotspots": placement_hotspots[:10],
            "debug": {
                "timing_ms": {
                    "total": round(total_ms, 1),
                    "nova_activity": round(activity_ms, 1),
                    "placement_capacity": round(placement_ms, 1),
                },
                "counts": {
                    "active_instances": len(active_instances),
                    "deleted_visible": len(deleted_visible),
                    "recent_changed": recent_changed,
                    "hypervisors": len(placement_items),
                    "projects": len(project_items),
                    "unresolved_host_instances": unresolved_host_instances,
                },
            },
        },
        "error": None,
    }


def _parse_window_ts(value: str, since_text: str | None) -> bool:
    since_dt = None
    if since_text:
        normalized = since_text[:-1] + "+00:00" if since_text.endswith("Z") else since_text
        try:
            since_dt = datetime.fromisoformat(normalized)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
        except Exception:
            since_dt = None
    if since_dt is None:
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        event_dt = datetime.fromisoformat(normalized)
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)
        return event_dt >= since_dt
    except Exception:
        return False


def render_nova_activity_capacity_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "project_name",
        "project_id",
        "active_instances",
        "recent_creates",
        "recent_deletes",
        "recent_updates",
        "recent_changes",
        "deleted_visible",
        "signal",
    ])
    for item in report.get("project_items", []):
        writer.writerow([
            item.get("project_name", ""),
            item.get("project_id", ""),
            item.get("active_instances", ""),
            item.get("recent_creates", ""),
            item.get("recent_deletes", ""),
            item.get("recent_updates", ""),
            item.get("recent_changes", ""),
            item.get("deleted_visible", ""),
            item.get("signal", ""),
        ])
    return output.getvalue()


def _state_failure_domains(state: NodeState) -> tuple[str, str]:
    az = state.availability_zone or "unknown"
    aggregate = (state.aggregates or ["—"])[0]
    return az, aggregate


def _project_placement_row_risk(vm_count: int, pod_count: int, amphora_count: int) -> tuple[str, str]:
    reasons: list[str] = []
    risk = "low"
    if vm_count >= 40:
        reasons.append("high VM density")
        risk = "high"
    elif vm_count >= 30:
        reasons.append("elevated VM density")
        risk = "medium"
    if pod_count >= 70:
        reasons.append("high pod density")
        risk = "high"
    elif pod_count >= 50:
        reasons.append("elevated pod density")
        if risk == "low":
            risk = "medium"
    if amphora_count >= 5:
        reasons.append("amphora concentration")
        risk = "high"
    if not reasons:
        reasons.append("density is within expected range")
    return risk, "; ".join(reasons)


def build_placement_risk_report(server: DrainoServer) -> dict:
    """Build a live placement-risk report from current node state."""
    started = time.perf_counter()
    pod_started = time.perf_counter()
    pod_capacity = k8s_ops.get_node_pod_capacity_summary(auth=server.k8s_auth)
    pod_ms = (time.perf_counter() - pod_started) * 1000.0

    states = sorted(server.node_states.values(), key=lambda item: item.k8s_name)
    compute_states = [state for state in states if state.is_compute]
    control_states = [state for state in states if state.is_etcd or state.hosts_mariadb]
    edge_states = [state for state in compute_states if state.is_edge]

    az_role_counts: dict[tuple[str, str], int] = {}
    agg_role_counts: dict[tuple[str, str], int] = {}
    for state in control_states:
        role = "etcd" if state.is_etcd else "mariadb"
        az, aggregate = _state_failure_domains(state)
        az_role_counts[(role, az)] = az_role_counts.get((role, az), 0) + 1
        agg_role_counts[(role, aggregate)] = agg_role_counts.get((role, aggregate), 0) + 1

    findings: list[dict] = []
    control_plane_items: list[dict] = []
    edge_items: list[dict] = []
    density_items: list[dict] = []

    high_density_hosts = 0
    high_risk_control = 0
    high_risk_edge = 0

    for state in control_states:
        role = "etcd" if state.is_etcd else "mariadb"
        az, aggregate = _state_failure_domains(state)
        reasons = [
            "etcd requires staggered reboots" if role == "etcd" else "mariadb requires staggered reboots"
        ]
        if az_role_counts.get((role, az), 0) > 1:
            reasons.append(f"{role} shares AZ {az}")
        if aggregate != "—" and agg_role_counts.get((role, aggregate), 0) > 1:
            reasons.append(f"{role} shares aggregate {aggregate}")
        if not _in_maintenance_posture(state):
            reasons.append("node is not yet in maintenance posture")
        risk = "high"
        if role == "etcd" and state.etcd_healthy is not True:
            reasons.append("etcd health requires review")
        high_risk_control += 1
        control_plane_items.append({
            "node": state.k8s_name,
            "role": role,
            "availability_zone": az,
            "aggregate": aggregate,
            "k8s_status": _k8s_status_label(state),
            "nova_status": _nova_status_label(state),
            "maintenance": "ready" if _in_maintenance_posture(state) else "review",
            "risk": risk,
            "reason": "; ".join(reasons),
        })

    for state in edge_states:
        az, aggregate = _state_failure_domains(state)
        pod_count = _as_int(pod_capacity.get(state.k8s_name, {}).get("pod_count")) or 0
        reasons = ["hosts OVN edge/gateway responsibilities"]
        risk = "medium"
        if (state.amphora_count or 0) >= 5:
            reasons.append("gateway host carries elevated amphora density")
            risk = "high"
        if state.vm_count and state.vm_count >= 30:
            reasons.append("gateway host also carries elevated VM density")
            risk = "high"
        if not _in_maintenance_posture(state):
            reasons.append("edge host is not yet in maintenance posture")
        if risk == "high":
            high_risk_edge += 1
        edge_items.append({
            "node": state.k8s_name,
            "availability_zone": az,
            "aggregate": aggregate,
            "amphora_count": state.amphora_count or 0,
            "vm_count": state.vm_count or 0,
            "maintenance": "ready" if _in_maintenance_posture(state) else "review",
            "risk": risk,
            "reason": "; ".join(reasons),
            "pod_count": pod_count,
        })

    for state in compute_states:
        pod_count = _as_int(pod_capacity.get(state.k8s_name, {}).get("pod_count")) or 0
        risk, reason = _project_placement_row_risk(state.vm_count or 0, pod_count, state.amphora_count or 0)
        if risk == "low":
            continue
        high_density_hosts += 1
        az, _aggregate = _state_failure_domains(state)
        density_items.append({
            "node": state.k8s_name,
            "availability_zone": az,
            "vm_count": state.vm_count or 0,
            "pod_count": pod_count,
            "amphora_count": state.amphora_count or 0,
            "maintenance": "ready" if _in_maintenance_posture(state) else "review",
            "risk": risk,
            "reason": reason,
        })

    for item in control_plane_items:
        findings.append({"severity": "high", "node": item["node"], "message": item["reason"]})
    for item in edge_items:
        if item["risk"] == "high":
            findings.append({"severity": "medium", "node": item["node"], "message": item["reason"]})
    for item in density_items:
        if item["risk"] == "high":
            findings.append({"severity": "medium", "node": item["node"], "message": item["reason"]})
    findings = sorted(findings, key=lambda item: (0 if item["severity"] == "high" else 1, item["node"]))[:6]

    total_ms = (time.perf_counter() - started) * 1000.0
    return {
        "report": {
            "key": "placement-risk",
            "title": "Placement Risk Report",
            "subtitle": "Live concentration and maintenance blast-radius view for clustered control-plane roles, OVN edge duties, and high-density compute hosts.",
            "source": "Kubernetes + OpenStack + OVN",
            "scope": {
                "nodes": len(states),
                "computes": len(compute_states),
                "critical_nodes": len(control_states),
                "edge_hosts": len(edge_states),
            },
            "summary": {
                "etcd_risk": "high" if any(state.is_etcd for state in states) else "low",
                "mariadb_hosts": sum(1 for state in states if state.hosts_mariadb),
                "gateway_hosts": len(edge_states),
                "density_hotspots": high_density_hosts,
            },
            "summary_foot": {
                "etcd_risk": "Quorum-sensitive placement needs operator sequencing" if any(state.is_etcd for state in states) else "No immediate quorum concentration detected",
                "mariadb_hosts": "Staggered reboots required" if any(state.hosts_mariadb for state in states) else "No MariaDB cluster workloads detected",
                "gateway_hosts": "Gateway duties concentrated on too few hosts" if len(edge_states) < 3 else "Gateway duties spread across multiple hosts",
                "density_hotspots": "Elevated VM / pod / amphora concentration" if high_density_hosts else "No severe density outliers",
            },
            "findings": findings,
            "control_plane_items": control_plane_items,
            "edge_items": edge_items,
            "density_items": density_items,
            "debug": {
                "timing_ms": {
                    "total": round(total_ms, 1),
                    "k8s_pod_summary": round(pod_ms, 1),
                },
                "counts": {
                    "critical_nodes": len(control_plane_items),
                    "edge_hosts": len(edge_items),
                    "density_items": len(density_items),
                    "high_risk_control": high_risk_control,
                    "high_risk_edge": high_risk_edge,
                },
            },
        },
        "error": None,
    }


def render_placement_risk_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "section",
        "node",
        "role",
        "availability_zone",
        "aggregate",
        "vm_count",
        "pod_count",
        "amphora_count",
        "maintenance",
        "risk",
        "reason",
    ])
    for item in report.get("control_plane_items", []):
        writer.writerow([
            "control-plane",
            item.get("node", ""),
            item.get("role", ""),
            item.get("availability_zone", ""),
            item.get("aggregate", ""),
            "",
            "",
            "",
            item.get("maintenance", ""),
            item.get("risk", ""),
            item.get("reason", ""),
        ])
    for item in report.get("edge_items", []):
        writer.writerow([
            "edge",
            item.get("node", ""),
            "edge",
            item.get("availability_zone", ""),
            item.get("aggregate", ""),
            item.get("vm_count", ""),
            item.get("pod_count", ""),
            item.get("amphora_count", ""),
            item.get("maintenance", ""),
            item.get("risk", ""),
            item.get("reason", ""),
        ])
    for item in report.get("density_items", []):
        writer.writerow([
            "density",
            item.get("node", ""),
            "compute",
            item.get("availability_zone", ""),
            "",
            item.get("vm_count", ""),
            item.get("pod_count", ""),
            item.get("amphora_count", ""),
            item.get("maintenance", ""),
            item.get("risk", ""),
            item.get("reason", ""),
        ])
    return output.getvalue()


def _pct(used: int | None, total: int | None) -> float | None:
    if used is None or total in (None, 0):
        return None
    return round((used / total) * 100.0, 1)


def _majority_version(version_counts: dict[str, int]) -> str:
    if not version_counts:
        return "unknown"
    return sorted(version_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _k8s_node_risk(item: dict, majority_version: str) -> tuple[str, str]:
    reasons: list[str] = []
    risk = "low"
    if not item.get("ready"):
        reasons.append("node is NotReady")
        risk = "high"
    if item.get("conditions"):
        reasons.append(", ".join(item["conditions"]))
        risk = "high"
    kubelet_version = item.get("kubelet_version") or "unknown"
    if majority_version != "unknown" and kubelet_version != majority_version:
        reasons.append("kubelet version drift")
        if risk == "low":
            risk = "high"
    pods_pct = item.get("pods_pct")
    if isinstance(pods_pct, (int, float)) and pods_pct >= 85:
        reasons.append("high pod density")
        risk = "high"
    elif isinstance(pods_pct, (int, float)) and pods_pct >= 70 and risk == "low":
        reasons.append("elevated pod density")
        risk = "medium"
    pvc_pod_count = int(item.get("pvc_pod_count") or 0)
    if pvc_pod_count >= 10:
        reasons.append("PVC-backed workload concentration")
        if risk == "low":
            risk = "medium"
    cpu_req_pct = item.get("cpu_req_pct")
    if isinstance(cpu_req_pct, (int, float)) and cpu_req_pct >= 80:
        reasons.append("high CPU request load")
        if risk == "low":
            risk = "medium"
    mem_req_pct = item.get("mem_req_pct")
    if isinstance(mem_req_pct, (int, float)) and mem_req_pct >= 80:
        reasons.append("high memory request load")
        if risk == "low":
            risk = "medium"
    if item.get("cordoned") and (isinstance(pods_pct, (int, float)) and pods_pct >= 70):
        reasons.append("cordoned while still dense")
        if risk == "low":
            risk = "medium"
    if not reasons:
        reasons.append("No standout conditions")
    return risk, "; ".join(reasons)


def build_k8s_node_health_density_report(server: DrainoServer) -> dict:
    started = time.perf_counter()
    data_started = time.perf_counter()
    summary = k8s_ops.get_k8s_node_health_density_summary(auth=server.k8s_auth)
    data_ms = (time.perf_counter() - data_started) * 1000.0
    if summary.get("error"):
        return {"report": None, "error": summary["error"]}

    nodes = summary.get("nodes", [])
    version_counts = summary.get("version_counts", {})
    condition_counts = summary.get("condition_counts", {})
    majority_version = _majority_version(version_counts)
    total_nodes = len(nodes)

    items: list[dict] = []
    findings: list[dict] = []
    ready_count = 0
    version_drift = 0
    high_pod_density = 0
    pvc_hotspots = 0

    for node in nodes:
        pods_allocatable = _as_int(node.get("pods_allocatable"))
        pod_count = int(node.get("pod_count") or 0)
        pvc_pod_count = int(node.get("pvc_pod_count") or 0)
        cpu_req_pct = _pct(node.get("cpu_requests_mcpu"), node.get("cpu_allocatable_mcpu"))
        mem_req_pct = _pct(node.get("memory_requests_mib"), node.get("memory_allocatable_mib"))
        pods_pct = _pct(pod_count, pods_allocatable)
        if node.get("ready"):
            ready_count += 1
        if node.get("kubelet_version") != majority_version:
            version_drift += 1
        if isinstance(pods_pct, (int, float)) and pods_pct >= 70:
            high_pod_density += 1
        if pvc_pod_count >= 10:
            pvc_hotspots += 1
        item = {
            "node": node["node"],
            "ready": node.get("ready"),
            "kubelet_version": node.get("kubelet_version") or "unknown",
            "runtime": node.get("runtime_label") or node.get("container_runtime") or "unknown",
            "pod_count": pod_count,
            "pods_pct": pods_pct,
            "pvc_pod_count": pvc_pod_count,
            "pvc_claim_count": int(node.get("pvc_claim_count") or 0),
            "namespace_count": int(node.get("namespace_count") or 0),
            "cpu_req_pct": cpu_req_pct,
            "mem_req_pct": mem_req_pct,
            "conditions": node.get("conditions") or [],
            "cordoned": bool(node.get("cordoned")),
        }
        risk, reason = _k8s_node_risk(item, majority_version)
        item["risk"] = risk
        item["reason"] = reason
        items.append(item)
        if risk != "low":
            findings.append({
                "severity": "high" if risk == "high" else "medium",
                "node": item["node"],
                "message": reason,
            })

    version_items = [
        {
            "kubelet_version": version,
            "node_count": count,
            "nodes": ", ".join(item["node"] for item in items if item["kubelet_version"] == version),
            "is_majority": version == majority_version,
        }
        for version, count in sorted(version_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    pvc_items = sorted(
        [item for item in items if item["pvc_pod_count"] > 0],
        key=lambda item: (-item["pvc_pod_count"], -item["pvc_claim_count"], item["node"]),
    )[:10]
    findings = sorted(findings, key=lambda item: (0 if item["severity"] == "high" else 1, item["node"]))[:4]
    total_ms = (time.perf_counter() - started) * 1000.0

    return {
        "report": {
            "key": "k8s-node-health-density",
            "title": "Kubernetes Node Health & Density",
            "subtitle": "Live node posture, kubelet version drift, pod density, PVC-backed workload concentration, and standout conditions for fast operator review.",
            "source": "Kubernetes",
            "scope": {
                "nodes": total_nodes,
            },
            "summary": {
                "ready_nodes": ready_count,
                "version_drift": version_drift,
                "high_pod_density": high_pod_density,
                "pvc_hotspots": pvc_hotspots,
            },
            "summary_foot": {
                "ready_nodes": f"{ready_count} / {total_nodes} nodes reporting Ready",
                "version_drift": f"Majority version: {majority_version}",
                "high_pod_density": "Pods above 70% allocatable",
                "pvc_hotspots": "PVC-backed pods concentrated on few nodes",
            },
            "findings": findings,
            "items": items,
            "version_items": version_items,
            "pvc_items": pvc_items,
            "condition_counts": condition_counts,
            "debug": {
                "timing_ms": {
                    "total": round(total_ms, 1),
                    "k8s_node_density_summary": round(data_ms, 1),
                },
                "counts": {
                    "nodes": total_nodes,
                    "versions": len(version_items),
                },
            },
        },
        "error": None,
    }


def render_k8s_node_health_density_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "node",
        "ready",
        "kubelet_version",
        "runtime",
        "pod_count",
        "pods_pct",
        "pvc_pod_count",
        "pvc_claim_count",
        "namespace_count",
        "cpu_req_pct",
        "mem_req_pct",
        "conditions",
        "risk",
        "reason",
    ])
    for item in report.get("items", []):
        writer.writerow([
            item.get("node", ""),
            "yes" if item.get("ready") else "no",
            item.get("kubelet_version", ""),
            item.get("runtime", ""),
            item.get("pod_count", ""),
            item.get("pods_pct", ""),
            item.get("pvc_pod_count", ""),
            item.get("pvc_claim_count", ""),
            item.get("namespace_count", ""),
            item.get("cpu_req_pct", ""),
            item.get("mem_req_pct", ""),
            ",".join(item.get("conditions", [])),
            item.get("risk", ""),
            item.get("reason", ""),
        ])
    return output.getvalue()


def _pvc_report_risk(item: dict) -> tuple[str, str]:
    status = item.get("status") or ""
    replica_count = item.get("replica_count")
    replica_nodes = item.get("replica_nodes") or []
    consumer_count = int(item.get("consumer_count") or 0)
    access_modes = item.get("access_modes") or ""
    if status.lower() != "bound":
        return ("high", "PVC is not bound")
    if consumer_count == 0:
        return ("medium", "No active consumer pod detected for a bound claim")
    if replica_count and replica_count >= 2 and len(set(replica_nodes)) < replica_count:
        return ("high", "Replica placement is concentrated on too few nodes")
    if consumer_count == 1 and "RWX" not in access_modes and len(item.get("consumer_nodes") or []) == 1:
        return ("medium", "Consumer workload is pinned to a single node")
    if "RWX" in access_modes and len(item.get("consumer_nodes") or []) <= 1:
        return ("medium", "Shared-access storage is effectively single-node in current use")
    return ("low", "Replica spread and consumer placement look healthy")


def build_k8s_pvc_workload_report(server: DrainoServer) -> dict:
    started = time.perf_counter()
    data_started = time.perf_counter()
    summary = k8s_ops.get_k8s_pvc_workload_summary(auth=server.k8s_auth)
    data_ms = (time.perf_counter() - data_started) * 1000.0
    if summary.get("error"):
        return {"report": None, "error": summary["error"]}

    raw_items = summary.get("items", [])
    items: list[dict] = []
    findings: list[dict] = []
    bound_count = 0
    replica_skew = 0
    single_node_use = 0
    orphan_count = 0

    for raw in raw_items:
        item = {
            **raw,
            "full_name": f"{raw.get('namespace', '')} / {raw.get('name', '')}",
            "replica_nodes_label": ", ".join(raw.get("replica_nodes") or []) or "—",
            "consumer_pod_label": ", ".join((raw.get("consumer_pods") or [])[:2]) or "—",
            "consumer_node_label": ", ".join(raw.get("consumer_nodes") or []) or "—",
        }
        risk, reason = _pvc_report_risk(item)
        item["risk"] = risk
        item["reason"] = reason
        items.append(item)

        if str(item.get("status", "")).lower() == "bound":
            bound_count += 1
        if item.get("replica_count") and len(set(item.get("replica_nodes") or [])) < int(item.get("replica_count") or 0):
            replica_skew += 1
        if int(item.get("consumer_count") or 0) <= 1 and item.get("consumer_nodes"):
            single_node_use += 1
        if str(item.get("status", "")).lower() != "bound" or int(item.get("consumer_count") or 0) == 0:
            orphan_count += 1
        if risk != "low":
            findings.append({
                "severity": "high" if risk == "high" else "medium",
                "node": item["full_name"],
                "message": reason,
            })

    storage_items = summary.get("storage_classes", [])
    replica_node_items = summary.get("replica_nodes", [])[:10]
    findings = sorted(findings, key=lambda item: (0 if item["severity"] == "high" else 1, item["node"]))[:4]
    total = len(items)
    total_ms = (time.perf_counter() - started) * 1000.0

    return {
        "report": {
            "key": "k8s-pvc-workload",
            "title": "Kubernetes PVC Placement & Workload",
            "subtitle": "Live PVC inventory with storage class, replica placement, consuming workloads, and node locality so operators can spot replica skew, single-node concentration, and storage hotspots at a glance.",
            "source": "Kubernetes",
            "scope": {
                "pvcs": total,
            },
            "summary": {
                "bound_pvcs": bound_count,
                "replica_skew": replica_skew,
                "single_node_use": single_node_use,
                "orphan_unbound": orphan_count,
            },
            "summary_foot": {
                "bound_pvcs": f"{bound_count} / {total} claims are bound and in service",
                "replica_skew": "Claims with replicas weighted to too few nodes",
                "single_node_use": "Consumer workload pinned to one node or host set",
                "orphan_unbound": "Claims needing cleanup or workload review",
            },
            "findings": findings,
            "items": items,
            "storage_items": storage_items,
            "replica_node_items": replica_node_items,
            "debug": {
                "timing_ms": {
                    "total": round(total_ms, 1),
                    "k8s_pvc_workload_summary": round(data_ms, 1),
                },
                "counts": {
                    "pvcs": total,
                    "storage_classes": len(storage_items),
                    "replica_nodes": len(summary.get("replica_nodes", [])),
                },
            },
        },
        "error": None,
    }


def render_k8s_pvc_workload_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "namespace",
        "name",
        "storageclass",
        "capacity",
        "access_modes",
        "replica_count",
        "replica_nodes",
        "consumer_pods",
        "consumer_nodes",
        "status",
        "risk",
        "reason",
    ])
    for item in report.get("items", []):
        writer.writerow([
            item.get("namespace", ""),
            item.get("name", ""),
            item.get("storageclass", ""),
            item.get("capacity", ""),
            item.get("access_modes", ""),
            item.get("replica_count", ""),
            ",".join(item.get("replica_nodes", [])),
            ",".join(item.get("consumer_pods", [])),
            ",".join(item.get("consumer_nodes", [])),
            item.get("status", ""),
            item.get("risk", ""),
            item.get("reason", ""),
        ])
    return output.getvalue()


def build_k8s_rollout_health_report(server: DrainoServer) -> dict:
    started = time.perf_counter()
    data_started = time.perf_counter()
    summary = k8s_ops.get_k8s_rollout_health_summary(auth=server.k8s_auth)
    data_ms = (time.perf_counter() - data_started) * 1000.0
    if summary.get("error"):
        return {"report": None, "error": summary["error"]}

    workloads = summary.get("workloads", [])
    recent_restarts = summary.get("recent_restarts", [])
    fatal_counts = summary.get("fatal_counts", {})
    counts = summary.get("counts", {})

    broken_rollouts = int(counts.get("broken_rollouts") or 0)
    recent_restart_15m = sum(1 for item in recent_restarts if item.get("window") in {"5m", "15m"})
    fatal_signals = int(counts.get("fatal_signals") or 0)
    misscheduled_coverage = sum(1 for item in workloads if item.get("kind") == "DaemonSet" and ((item.get("misscheduled") or 0) > 0 or (item.get("unavailable") or 0) > 0))

    findings: list[dict] = []
    for item in workloads:
        if item.get("risk") not in {"high", "medium"}:
            continue
        findings.append({
            "severity": "high" if item.get("risk") == "high" else "medium",
            "node": f"{item.get('namespace', '')} / {item.get('name', '')}",
            "message": item.get("reason") or "Rollout risk detected",
        })
    for item in recent_restarts:
        if item.get("risk") not in {"high", "medium"}:
            continue
        findings.append({
            "severity": "high" if item.get("risk") == "high" else "medium",
            "node": f"{item.get('namespace', '')} / {item.get('pod', '')}",
            "message": f"{item.get('last_reason') or 'restart'} in the last {item.get('window') or 'unknown window'}",
        })
    findings = sorted(findings, key=lambda item: (0 if item["severity"] == "high" else 1, item["node"]))[:4]

    fatal_items = [{"reason": reason, "count": count} for reason, count in fatal_counts.items()]
    total_workloads = len(workloads)
    total_ms = (time.perf_counter() - started) * 1000.0

    return {
        "report": {
            "key": "k8s-rollout-health",
            "title": "Kubernetes Rollout Health",
            "subtitle": "Live workload risk view for operators, highlighting broken rollouts, stalled updates, recent restarts, and fatal restart reasons without requiring stored state.",
            "source": "Kubernetes",
            "scope": {
                "workloads": total_workloads,
                "pods_with_restart_signals": len(recent_restarts),
            },
            "summary": {
                "broken_rollouts": broken_rollouts,
                "recent_restarts": recent_restart_15m,
                "fatal_signals": fatal_signals,
                "misscheduled_coverage": misscheduled_coverage,
            },
            "summary_foot": {
                "broken_rollouts": "Unavailable replicas or rollout progress below desired state",
                "recent_restarts": "Pods restarted in the last 15 minutes from current status only",
                "fatal_signals": "OOMKilled, CrashLoopBackOff, ImagePullBackOff, and related fatal reasons",
                "misscheduled_coverage": "DaemonSets with misscheduled pods or missing node coverage",
            },
            "findings": findings,
            "workload_items": workloads,
            "restart_items": recent_restarts,
            "fatal_items": fatal_items,
            "debug": {
                "timing_ms": {
                    "total": round(total_ms, 1),
                    "k8s_rollout_health_summary": round(data_ms, 1),
                },
                "counts": {
                    "workloads": total_workloads,
                    "deployments": int(counts.get("deployments") or 0),
                    "statefulsets": int(counts.get("statefulsets") or 0),
                    "daemonsets": int(counts.get("daemonsets") or 0),
                    "restart_items": len(recent_restarts),
                },
            },
        },
        "error": None,
    }


def render_k8s_rollout_health_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "namespace",
        "name",
        "kind",
        "ready",
        "desired",
        "updated",
        "available",
        "unavailable",
        "revision_drift",
        "risk",
        "reason",
    ])
    for item in report.get("workload_items", []):
        writer.writerow([
            item.get("namespace", ""),
            item.get("name", ""),
            item.get("kind", ""),
            item.get("ready", ""),
            item.get("desired", ""),
            item.get("updated", ""),
            item.get("available", ""),
            item.get("unavailable", ""),
            item.get("revision_drift", ""),
            item.get("risk", ""),
            item.get("reason", ""),
        ])
    return output.getvalue()
