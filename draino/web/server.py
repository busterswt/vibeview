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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse
from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

from .. import node_agent_client
from ..models import NodeState
from ..operations import k8s_ops, openstack_ops
from .api import auth as auth_api
from .api import k8s as k8s_api
from .api import nodes as nodes_api
from .api import runtime as runtime_api
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
from .session import SESSION_TTL, SessionRecord, SessionStore, get_session_record, get_ws_session
from . import ws as ws_api

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
            current_tag_digest = _resolve_remote_track_digest(_UPDATE_REPOSITORY, _IMAGE_TAG)
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
    }

# ── OpenStack resource helpers (called in thread pool) ───────────────────────

def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _get_networks(auth: openstack_ops.OpenStackAuth | None) -> list[dict]:
    """Return all Neutron networks visible to the configured credential."""
    conn = openstack_ops._conn(auth=auth)
    result = []
    for n in conn.network.networks():
        d = n.to_dict() if hasattr(n, "to_dict") else {}
        raw_external = d.get("router:external")
        if raw_external is None:
            raw_external = getattr(n, "is_router_external", False)
        result.append({
            "id":           n.id,
            "name":         n.name or "(unnamed)",
            "status":       n.status or "UNKNOWN",
            "admin_state":  "up" if n.is_admin_state_up else "down",
            "shared":       bool(n.is_shared),
            "external":     _coerce_bool(raw_external),
            "network_type": d.get("provider:network_type") or "",
            "project_id":   n.project_id or "",
            "subnet_count": len(list(n.subnet_ids or [])),
        })
    return result


def _get_network_detail(
    network_id: str,
    auth: openstack_ops.OpenStackAuth | None,
) -> dict:
    """Return subnets and segments for a single Neutron network."""
    conn = openstack_ops._conn(auth=auth)
    network = conn.network.get_network(network_id)
    nd = network.to_dict() if hasattr(network, "to_dict") else {}

    subnets = []
    for subnet_id in (network.subnet_ids or []):
        try:
            s = conn.network.get_subnet(subnet_id)
            subnets.append({
                "id":               s.id,
                "name":             s.name or "",
                "cidr":             s.cidr or "",
                "ip_version":       s.ip_version,
                "gateway_ip":       s.gateway_ip or "",
                "enable_dhcp":      bool(getattr(s, "is_dhcp_enabled", False)),
                "allocation_pools": getattr(s, "allocation_pools", []) or [],
                "dns_nameservers":  getattr(s, "dns_nameservers", []) or [],
                "host_routes":      getattr(s, "host_routes", []) or [],
            })
        except Exception:
            pass

    # Try dedicated Segments API (admin-only in most deployments)
    segments = []
    try:
        for seg in conn.network.segments(network_id=network_id):
            seg_d = seg.to_dict() if hasattr(seg, "to_dict") else {}
            segments.append({
                "id":               seg.id or "",
                "name":             seg.name or "",
                "network_type":     seg_d.get("network_type")     or getattr(seg, "network_type",     "") or "",
                "physical_network": seg_d.get("physical_network") or getattr(seg, "physical_network", "") or "",
                "segmentation_id":  seg_d.get("segmentation_id", getattr(seg, "segmentation_id", None)),
            })
    except Exception:
        pass

    # Fall back to provider attributes on the network object itself
    if not segments:
        nt = nd.get("provider:network_type") or ""
        pn = nd.get("provider:physical_network") or ""
        si = nd.get("provider:segmentation_id")
        if nt or pn or si is not None:
            segments = [{"id": "", "name": "", "network_type": nt, "physical_network": pn, "segmentation_id": si}]

    return {"subnets": subnets, "segments": segments}


def _get_volumes(
    auth: openstack_ops.OpenStackAuth | None,
) -> tuple[list[dict], bool]:
    """Return all Cinder volumes.  Falls back to project-scope on permission error.

    Returns (volumes, all_projects_succeeded).
    """
    conn = openstack_ops._conn(auth=auth)
    all_projects = False
    try:
        vols = list(conn.volume.volumes(all_projects=True))
        all_projects = True
    except Exception:
        vols = list(conn.volume.volumes())

    result = []
    for v in vols:
        att = getattr(v, "attachments", []) or []
        project_id = (
            getattr(v, "os-vol-tenant-attr:tenant_id", None)
            or getattr(v, "project_id", None)
            or ""
        )
        result.append({
            "id":          v.id,
            "name":        v.name or "(no name)",
            "status":      v.status or "UNKNOWN",
            "size_gb":     v.size or 0,
            "volume_type": v.volume_type or "",
            "project_id":  project_id,
            "attached_to": [a.get("server_id", "") for a in att],
            "bootable":    bool(getattr(v, "is_bootable", False)),
            "encrypted":   bool(getattr(v, "encrypted", False)),
        })
    return result, all_projects

# ── FastAPI application ───────────────────────────────────────────────────────


def _get_session_record(request: Request) -> SessionRecord:
    return get_session_record(request, _sessions, _SESSION_COOKIE)


def _get_ws_session(ws: WebSocket) -> SessionRecord | None:
    return get_ws_session(ws, _sessions, _SESSION_COOKIE)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _app_loop
    _app_loop = asyncio.get_running_loop()
    yield


fastapi_app = FastAPI(title="VibeView", lifespan=_lifespan)
auth_api.configure(
    static_dir=_STATIC,
    get_sessions=lambda: _sessions,
    get_app_loop=lambda: _app_loop,
    get_audit_log_path=lambda: _audit_log_path,
)
runtime_api.configure(
    get_session_record=lambda: _get_session_record,
    get_app_update_status=lambda: _get_app_update_status,
    get_public_version_status=lambda: _get_public_version_status,
    get_app_runtime=lambda: _get_app_runtime,
)
nodes_api.configure(
    get_session_record=lambda: _get_session_record,
    get_network_detail=lambda: _get_network_detail,
)
k8s_api.configure(
    get_session_record=lambda: _get_session_record,
)
ws_api.configure(
    get_ws_session=lambda: _get_ws_session,
)
fastapi_app.include_router(auth_api.router)
fastapi_app.include_router(runtime_api.router)
fastapi_app.include_router(nodes_api.router)
fastapi_app.include_router(k8s_api.router)
fastapi_app.include_router(ws_api.router)


@fastapi_app.get("/api/networks")
async def api_networks(request: Request):
    """List Neutron networks (admin sees all; non-admin sees project scope)."""
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, _get_networks, session.server.openstack_auth)
        return {"networks": data, "error": None}
    except Exception as exc:
        return {"networks": [], "error": str(exc)}




@fastapi_app.get("/api/volumes")
async def api_volumes(request: Request):
    """List Cinder volumes (admin sees all projects; non-admin sees own project)."""
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        data, all_projects = await loop.run_in_executor(None, _get_volumes, session.server.openstack_auth)
        return {"volumes": data, "all_projects": all_projects, "error": None}
    except Exception as exc:
        return {"volumes": [], "all_projects": False, "error": str(exc)}


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
