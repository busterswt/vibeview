"""Helpers for surfacing upstream API failures to the web UI."""
from __future__ import annotations


def _exc_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _exc_request_id(exc: Exception) -> str | None:
    for attr in ("request_id", "x_openstack_request_id"):
        value = getattr(exc, attr, None)
        if value:
            return str(value)
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None) or {}
        lowered = {str(key).lower(): value for key, value in headers.items()}
        for header in (
            "x-openstack-request-id",
            "x-compute-request-id",
            "x-request-id",
            "openstack-request-id",
        ):
            value = lowered.get(header)
            if value:
                if isinstance(value, (list, tuple)):
                    return ", ".join(str(item) for item in value if item)
                return str(value)
    return None


def build_api_issue(service: str, operation: str, exc: Exception) -> dict:
    text = str(exc)
    lowered = text.lower()
    resolved_service = service
    if "keystone" in lowered or "auth" in lowered and "token" in lowered:
        resolved_service = "Keystone"
    elif "neutron" in lowered:
        resolved_service = "Neutron"
    elif "nova" in lowered:
        resolved_service = "Nova"
    return {
        "service": resolved_service,
        "operation": operation,
        "status": _exc_status_code(exc),
        "request_id": _exc_request_id(exc),
        "message": text,
        "severity": "high",
    }
