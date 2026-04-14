"""Live report routes."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from .api_issues import build_api_issue
from ..report_helpers import (
    build_capacity_headroom_report,
    build_k8s_node_health_density_report,
    build_k8s_pvc_workload_report,
    build_k8s_rollout_health_report,
    build_maintenance_readiness_report,
    build_nova_activity_capacity_report,
    build_placement_risk_report,
    build_project_placement_report,
    render_capacity_headroom_csv,
    render_k8s_node_health_density_csv,
    render_k8s_pvc_workload_csv,
    render_k8s_rollout_health_csv,
    render_maintenance_readiness_csv,
    render_nova_activity_capacity_csv,
    render_placement_risk_csv,
    render_project_placement_csv,
)

router = APIRouter()

_get_session_record_getter: Callable[[], Callable[[Request], object]] | None = None


def configure(*, get_session_record: Callable[[], Callable[[Request], object]]) -> None:
    global _get_session_record_getter
    _get_session_record_getter = get_session_record


def _require_session_record() -> Callable[[Request], object]:
    if _get_session_record_getter is None:
        raise RuntimeError("report routes are not configured")
    return _get_session_record_getter()


@router.get("/api/reports/maintenance-readiness")
async def api_report_maintenance_readiness(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(None, build_maintenance_readiness_report, session.server)
        report["api_issue"] = None
        return report
    except Exception as exc:
        return {"report": None, "error": str(exc), "api_issue": None}


@router.get("/api/reports/maintenance-readiness.csv")
async def api_report_maintenance_readiness_csv(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, build_maintenance_readiness_report, session.server)
    csv_text = await loop.run_in_executor(None, render_maintenance_readiness_csv, report["report"])
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="maintenance-readiness.csv"'},
    )


@router.get("/api/reports/capacity-headroom")
async def api_report_capacity_headroom(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(None, build_capacity_headroom_report, session.server)
        report["api_issue"] = None
        return report
    except Exception as exc:
        return {"report": None, "error": str(exc), "api_issue": build_api_issue("Nova", "GET capacity-headroom report", exc)}


@router.get("/api/reports/capacity-headroom.csv")
async def api_report_capacity_headroom_csv(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, build_capacity_headroom_report, session.server)
    csv_text = await loop.run_in_executor(None, render_capacity_headroom_csv, report["report"])
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="capacity-headroom.csv"'},
    )


@router.get("/api/reports/nova-activity-capacity")
async def api_report_nova_activity_capacity(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(None, build_nova_activity_capacity_report, session.server)
        report["api_issue"] = None
        return report
    except Exception as exc:
        return {"report": None, "error": str(exc), "api_issue": build_api_issue("Nova", "GET nova-activity-capacity report", exc)}


@router.get("/api/reports/nova-activity-capacity.csv")
async def api_report_nova_activity_capacity_csv(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, build_nova_activity_capacity_report, session.server)
    csv_text = await loop.run_in_executor(None, render_nova_activity_capacity_csv, report["report"])
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="nova-activity-capacity.csv"'},
    )


@router.get("/api/reports/k8s-node-health-density")
async def api_report_k8s_node_health_density(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(None, build_k8s_node_health_density_report, session.server)
        report["api_issue"] = None
        return report
    except Exception as exc:
        return {"report": None, "error": str(exc), "api_issue": None}


@router.get("/api/reports/k8s-node-health-density.csv")
async def api_report_k8s_node_health_density_csv(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, build_k8s_node_health_density_report, session.server)
    csv_text = await loop.run_in_executor(None, render_k8s_node_health_density_csv, report["report"])
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="k8s-node-health-density.csv"'},
    )


@router.get("/api/reports/k8s-pvc-workload")
async def api_report_k8s_pvc_workload(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(None, build_k8s_pvc_workload_report, session.server)
        report["api_issue"] = None
        return report
    except Exception as exc:
        return {"report": None, "error": str(exc), "api_issue": None}


@router.get("/api/reports/k8s-pvc-workload.csv")
async def api_report_k8s_pvc_workload_csv(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, build_k8s_pvc_workload_report, session.server)
    csv_text = await loop.run_in_executor(None, render_k8s_pvc_workload_csv, report["report"])
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="k8s-pvc-workload.csv"'},
    )


@router.get("/api/reports/k8s-rollout-health")
async def api_report_k8s_rollout_health(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(None, build_k8s_rollout_health_report, session.server)
        report["api_issue"] = None
        return report
    except Exception as exc:
        return {"report": None, "error": str(exc), "api_issue": None}


@router.get("/api/reports/k8s-rollout-health.csv")
async def api_report_k8s_rollout_health_csv(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, build_k8s_rollout_health_report, session.server)
    csv_text = await loop.run_in_executor(None, render_k8s_rollout_health_csv, report["report"])
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="k8s-rollout-health.csv"'},
    )


@router.get("/api/reports/project-placement")
async def api_report_project_placement(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(None, build_project_placement_report, session.server)
        report["api_issue"] = None
        return report
    except Exception as exc:
        return {"report": None, "error": str(exc), "api_issue": build_api_issue("Nova", "GET project-placement report", exc)}


@router.get("/api/reports/project-placement.csv")
async def api_report_project_placement_csv(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, build_project_placement_report, session.server)
    csv_text = await loop.run_in_executor(None, render_project_placement_csv, report["report"])
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="project-placement.csv"'},
    )


@router.get("/api/reports/placement-risk")
async def api_report_placement_risk(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    try:
        report = await loop.run_in_executor(None, build_placement_risk_report, session.server)
        report["api_issue"] = None
        return report
    except Exception as exc:
        return {"report": None, "error": str(exc), "api_issue": None}


@router.get("/api/reports/placement-risk.csv")
async def api_report_placement_risk_csv(request: Request):
    session = _require_session_record()(request)
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, build_placement_risk_report, session.server)
    csv_text = await loop.run_in_executor(None, render_placement_risk_csv, report["report"])
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="placement-risk.csv"'},
    )
