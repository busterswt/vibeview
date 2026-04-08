"""FastAPI web server for VibeView.

All worker logic (worker.py, operations/) is reused unchanged. The operator
interface is delivered as a FastAPI app with WebSocket push updates.

WebSocket message protocol
──────────────────────────
Server → Client:
  {"type": "full_state",         "nodes": {name: NodeDict, ...}}
  {"type": "state_update",       "node": name, "data": NodeDict}
  {"type": "log",                "node": name, "message": str, "color": str}
  {"type": "reboot_confirm_needed", "node": name}
  {"type": "reboot_blocked",     "node": name, "detail": str}

Client → Server:
  {"action": "refresh"}
  {"action": "evacuate",       "node": name}
  {"action": "drain_quick",    "node": name}
  {"action": "undrain",        "node": name}
  {"action": "reboot_request", "node": name}
  {"action": "reboot_confirm", "node": name}
  {"action": "reboot_cancel",  "node": name}
  {"action": "check_etcd",     "node": name}
  {"action": "get_preflight",  "node": name}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import resource
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import (
    HTTPException,
    Request,
)
from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

from .. import node_agent_client
from ..models import NodeState
from ..operations import k8s_ops, openstack_ops
from .app import create_fastapi_app
from .auth_builders import (
    K8sLoginPayload,
    LoginPayload,
    OpenStackLoginPayload,
    _build_k8s_auth,
    _build_openstack_auth,
    _build_openstack_auth_from_clouds_yaml,
    _parse_yaml_document,
    _require,
    _validate_supported_kubeconfig,
)
from . import inventory as inventory_module
from .inventory import DrainoServer, _serialise
from .latency import get_latency_summary
from .resource_helpers import (
    coerce_bool as _coerce_bool,
    get_network_detail as _get_network_detail,
    get_networks as _get_networks,
    get_router_detail as _get_router_detail,
    get_routers as _get_routers,
    get_volumes as _get_volumes,
)
from .session import SESSION_TTL, SessionRecord, SessionStore, get_session_record, get_ws_session

_STATIC = Path(__file__).parent / "static"
_SESSION_COOKIE = "draino_session"
_SESSION_TTL = SESSION_TTL
_HOST_SIGNALS_TTL = inventory_module._HOST_SIGNALS_TTL
_APP_UPDATE_TTL = float(os.getenv("DRAINO_APP_UPDATE_TTL", "300"))
_IMAGE_REPOSITORY = os.getenv("DRAINO_IMAGE_REPOSITORY", "ghcr.io/busterswt/draino-claude")
_IMAGE_TAG = os.getenv("DRAINO_IMAGE_TAG", "main")
_UPDATE_REPOSITORY = os.getenv("DRAINO_UPDATE_REPOSITORY", _IMAGE_REPOSITORY)
_UPDATE_TRACK = os.getenv("DRAINO_UPDATE_TRACK", "main")
_UPDATE_URL = os.getenv(
    "DRAINO_UPDATE_URL",
    "https://github.com/busterswt/draino-claude/blob/main/deploy/genestack/README.md#updating-a-deployment",
)
_POD_NAME = os.getenv("DRAINO_POD_NAME") or os.getenv("HOSTNAME", "")
_POD_NAMESPACE = os.getenv("DRAINO_POD_NAMESPACE", "default")
_app_loop: Optional[asyncio.AbstractEventLoop] = None
_audit_log_path: Optional[str] = None
_LOGGER = logging.getLogger("draino.web")
_update_cache_lock = threading.Lock()
_app_update_cache: tuple[float, dict] | None = None
_runtime_lock = threading.Lock()
_runtime_history: list[dict] = []
_runtime_pod_cache: tuple[float, dict] | None = None
_runtime_prev_sample: tuple[float, float] | None = None


_sessions = SessionStore()


def _normalise_image_digest(image_id: str | None) -> str | None:
    if not image_id:
        return None
    image_id = image_id.strip()
    if "@" in image_id:
        return image_id.rsplit("@", 1)[1]
    if image_id.startswith("sha256:"):
        return image_id
    return None


def _parse_www_authenticate(header: str) -> dict[str, str]:
    if not header or not header.startswith("Bearer "):
        raise RuntimeError("unsupported GHCR auth challenge")
    pairs = re.findall(r'([a-zA-Z_]+)="([^"]+)"', header)
    return {key: value for key, value in pairs}


def _ghcr_json_request(url: str, headers: dict[str, str] | None = None) -> tuple[dict, dict[str, str]]:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return payload, dict(response.headers.items())


def _ghcr_manifest_request(
    repository_path: str,
    reference: str,
    token: str | None = None,
) -> tuple[dict, dict[str, str]]:
    headers = {
        "Accept": ",".join([
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ]),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://ghcr.io/v2/{repository_path}/manifests/{reference}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        if exc.code != 401 or token:
            raise
        challenge = exc.headers.get("WWW-Authenticate", "")
        params = _parse_www_authenticate(challenge)
        token_url = f"{params['realm']}?{urllib.parse.urlencode({'service': params.get('service', ''), 'scope': params.get('scope', '')})}"
        token_payload, _ = _ghcr_json_request(token_url)
        access_token = token_payload.get("token") or token_payload.get("access_token")
        if not access_token:
            raise RuntimeError("GHCR token response did not include a token") from exc
        return _ghcr_manifest_request(repository_path, reference, token=access_token)


def _resolve_remote_track_digest(image_repository: str, reference: str) -> str | None:
    repository_path = image_repository
    if repository_path.startswith("ghcr.io/"):
        repository_path = repository_path[len("ghcr.io/"):]

    _manifest, headers = _ghcr_manifest_request(repository_path, reference)
    return headers.get("Docker-Content-Digest")


def _get_running_image_digest() -> str | None:
    if not _POD_NAME:
        return None
    try:
        config.load_incluster_config()
    except ConfigException:
        return None

    core = client.CoreV1Api()
    pods = core.list_namespaced_pod(namespace=_POD_NAMESPACE, field_selector=f"metadata.name={_POD_NAME}")
    if not pods.items:
        return None

    pod = pods.items[0]
    container_statuses = pod.status.container_statuses or []
    for container_status in container_statuses:
        if container_status.name == "draino":
            return _normalise_image_digest(container_status.image_id)
    if container_statuses:
        return _normalise_image_digest(container_statuses[0].image_id)
    return None


def _compute_update_status() -> dict:
    current_digest = _get_running_image_digest()
    current_tag_digest = None
    latest_digest = None
    error = None
    try:
        latest_digest = _resolve_remote_track_digest(_UPDATE_REPOSITORY, _UPDATE_TRACK)
        if current_digest is None and _IMAGE_TAG:
            current_tag_digest = _resolve_remote_track_digest(_IMAGE_REPOSITORY, _IMAGE_TAG)
    except Exception as exc:  # pragma: no cover - network failure path
        error = str(exc)
        _LOGGER.warning("failed to resolve upstream image digest: %s", exc)

    effective_current_digest = current_digest or current_tag_digest
    update_available = bool(
        effective_current_digest and latest_digest and effective_current_digest != latest_digest
    )
    return {
        "image_repository": _IMAGE_REPOSITORY,
        "update_repository": _UPDATE_REPOSITORY,
        "current_tag": _IMAGE_TAG,
        "current_digest": effective_current_digest,
        "current_digest_source": "running_pod" if current_digest else ("image_tag" if current_tag_digest else None),
        "track": _UPDATE_TRACK,
        "latest_digest": latest_digest,
        "update_available": update_available,
        "update_url": _UPDATE_URL,
        "error": error,
    }


def _get_app_update_status(force: bool = False) -> dict:
    now = time.time()
    global _app_update_cache
    with _update_cache_lock:
        if not force and _app_update_cache and now < _app_update_cache[0]:
            return dict(_app_update_cache[1])
    status = _compute_update_status()
    with _update_cache_lock:
        _app_update_cache = (now + _APP_UPDATE_TTL, dict(status))
    return status


def _get_public_version_status() -> dict:
    meta = _get_app_update_status()
    digest = meta.get("current_digest")
    short_sha = ""
    if isinstance(digest, str) and digest.startswith("sha256:"):
        short_sha = digest[len("sha256:"):][:12]
    return {
        "current_digest": digest,
        "short_sha": short_sha,
        "current_tag": meta.get("current_tag"),
        "current_digest_source": meta.get("current_digest_source"),
    }


def _read_process_rss_bytes() -> int | None:
    status_path = Path("/proc/self/status")
    if status_path.exists():
        try:
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return int(parts[1]) * 1024
        except OSError:
            pass

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if rss <= 0:
        return None
    if os.uname().sysname.lower() == "darwin":
        return int(rss)
    return int(rss) * 1024


def _sample_process_runtime() -> dict:
    global _runtime_prev_sample
    now = time.time()
    monotonic_now = time.monotonic()
    process_now = time.process_time()
    cpu_percent = 0.0

    with _runtime_lock:
        previous = _runtime_prev_sample
        _runtime_prev_sample = (monotonic_now, process_now)

    if previous is not None:
        prev_mono, prev_proc = previous
        wall_delta = monotonic_now - prev_mono
        proc_delta = process_now - prev_proc
        if wall_delta > 0:
            cpu_percent = max(0.0, (proc_delta / wall_delta) * 100.0)

    return {
        "timestamp": now,
        "cpu_percent": round(cpu_percent, 2),
        "rss_bytes": _read_process_rss_bytes(),
    }


def _get_runtime_pod_info(force: bool = False) -> dict:
    now = time.time()
    global _runtime_pod_cache
    with _runtime_lock:
        if not force and _runtime_pod_cache and now < _runtime_pod_cache[0]:
            return dict(_runtime_pod_cache[1])

    if not _POD_NAME:
        return {"requests": {}, "limits": {}, "restart_count": None}

    try:
        config.load_incluster_config()
        core = client.CoreV1Api()
        pod = core.read_namespaced_pod(name=_POD_NAME, namespace=_POD_NAMESPACE)
    except Exception as exc:  # pragma: no cover - cluster access failure path
        _LOGGER.warning("failed to read pod runtime metadata: %s", exc)
        return {"requests": {}, "limits": {}, "restart_count": None}

    requests: dict[str, str] = {}
    limits: dict[str, str] = {}
    restart_count = None

    for container in pod.spec.containers or []:
        if container.name != "draino":
            continue
        resources = container.resources
        requests = dict(resources.requests or {})
        limits = dict(resources.limits or {})
        break

    for container_status in pod.status.container_statuses or []:
        if container_status.name == "draino":
            restart_count = container_status.restart_count
            break

    result = {
        "requests": requests,
        "limits": limits,
        "restart_count": restart_count,
    }
    with _runtime_lock:
        _runtime_pod_cache = (now + 60.0, dict(result))
    return result


def _get_app_runtime() -> dict:
    sample = _sample_process_runtime()
    pod_info = _get_runtime_pod_info()

    with _runtime_lock:
        _runtime_history.append(sample)
        cutoff = sample["timestamp"] - (15 * 60)
        while _runtime_history and (_runtime_history[0]["timestamp"] < cutoff or len(_runtime_history) > 180):
            _runtime_history.pop(0)
        history = [dict(item) for item in _runtime_history]

    return {
        "current": sample,
        "history": history,
        "requests": pod_info.get("requests", {}),
        "limits": pod_info.get("limits", {}),
        "restart_count": pod_info.get("restart_count"),
        "latencies": get_latency_summary(),
    }

# ── FastAPI application ───────────────────────────────────────────────────────


def _get_session_record(request: Request) -> SessionRecord:
    return get_session_record(request, _sessions, _SESSION_COOKIE)


def _get_ws_session(ws: WebSocket) -> SessionRecord | None:
    return get_ws_session(ws, _sessions, _SESSION_COOKIE)


def _set_app_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _app_loop
    _app_loop = loop


fastapi_app = create_fastapi_app(
    static_dir=_STATIC,
    get_sessions=lambda: _sessions,
    get_audit_log_path=lambda: _audit_log_path,
    set_app_loop=_set_app_loop,
    get_session_record=lambda: _get_session_record,
    get_ws_session=lambda: _get_ws_session,
    get_app_update_status=lambda: _get_app_update_status,
    get_public_version_status=lambda: _get_public_version_status,
    get_app_runtime=lambda: _get_app_runtime,
    get_network_detail=lambda: _get_network_detail,
)

# ── Entry point ───────────────────────────────────────────────────────────────

def run(
    cloud:     Optional[str] = None,
    context:   Optional[str] = None,
    audit_log: Optional[str] = None,
    host:      str            = "0.0.0.0",
    port:      int            = 8000,
) -> None:
    """Configure and launch the Draino web server."""
    global _audit_log_path
    _audit_log_path = audit_log
    openstack_ops.configure(cloud=cloud)
    k8s_ops.configure(context=context)
    _LOGGER.info("web ui starting host=%s port=%s", host, port)
    uvicorn.run(fastapi_app, host=host, port=port, log_level="warning")
