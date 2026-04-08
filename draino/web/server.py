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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Set

import uvicorn
import yaml
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse
from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException
from pydantic import BaseModel

from .. import node_agent_client
from ..models import NodeState
from ..operations import k8s_ops, openstack_ops
from . import inventory as inventory_module
from .inventory import DrainoServer, _serialise

_STATIC = Path(__file__).parent / "static"
_SESSION_COOKIE = "draino_session"
_SESSION_TTL = 60 * 60 * 12
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


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    server: "DrainoServer"
    username: str
    project_name: str
    role_names: list[str]
    is_admin: bool
    created_at: float
    last_seen: float


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = threading.Lock()

    def put(self, record: SessionRecord) -> None:
        with self._lock:
            self._sessions[record.session_id] = record

    def get(self, session_id: str | None) -> SessionRecord | None:
        if not session_id:
            return None
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return None
            now = time.time()
            if now - record.last_seen > _SESSION_TTL:
                self._sessions.pop(session_id, None)
                return None
            record.last_seen = now
            return record

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)


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
    record = _sessions.get(request.cookies.get(_SESSION_COOKIE))
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return record


def _get_ws_session(ws: WebSocket) -> SessionRecord | None:
    return _sessions.get(ws.cookies.get(_SESSION_COOKIE))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _app_loop
    _app_loop = asyncio.get_running_loop()
    yield


fastapi_app = FastAPI(title="VibeView", lifespan=_lifespan)


class K8sLoginPayload(BaseModel):
    mode: str = "token"
    server: str | None = None
    token: str | None = None
    skip_tls_verify: bool = False
    ca_cert: str | None = None
    client_cert: str | None = None
    client_key: str | None = None
    kubeconfig_yaml: str | None = None
    context: str | None = None


class OpenStackLoginPayload(BaseModel):
    mode: str = "password"
    auth_url: str | None = None
    username: str | None = None
    password: str | None = None
    project_name: str | None = None
    user_domain_name: str = "Default"
    project_domain_name: str = "Default"
    region_name: str | None = None
    interface: str | None = None
    skip_tls_verify: bool = False
    application_credential_id: str | None = None
    application_credential_secret: str | None = None
    clouds_yaml: str | None = None
    cloud_name: str | None = None


class LoginPayload(BaseModel):
    kubernetes: K8sLoginPayload
    openstack: OpenStackLoginPayload


def _require(value: str | None, label: str) -> str:
    result = (value or "").strip()
    if not result:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    return result


def _parse_yaml_document(source: str, label: str) -> dict:
    try:
        data = yaml.safe_load(source) or {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: expected a YAML mapping")
    return data


def _build_k8s_auth(payload: K8sLoginPayload) -> k8s_ops.K8sAuth:
    mode = (payload.mode or "token").strip().lower()
    if mode == "token":
        return k8s_ops.K8sAuth(
            mode="token",
            server=_require(payload.server, "Kubernetes API server URL"),
            token=_require(payload.token, "Kubernetes bearer token"),
            skip_tls_verify=payload.skip_tls_verify,
            ca_cert=(payload.ca_cert or "").strip() or None,
        )
    if mode == "client_cert":
        return k8s_ops.K8sAuth(
            mode="client_cert",
            server=_require(payload.server, "Kubernetes API server URL"),
            skip_tls_verify=payload.skip_tls_verify,
            ca_cert=(payload.ca_cert or "").strip() or None,
            client_cert=_require(payload.client_cert, "Kubernetes client certificate"),
            client_key=_require(payload.client_key, "Kubernetes client key"),
        )
    if mode == "kubeconfig":
        kubeconfig = _parse_yaml_document(
            _require(payload.kubeconfig_yaml, "Kubeconfig"),
            "kubeconfig",
        )
        context_name = (payload.context or "").strip() or None
        _validate_supported_kubeconfig(kubeconfig, context_name)
        return k8s_ops.K8sAuth(mode="kubeconfig", kubeconfig=kubeconfig, context=context_name)
    raise HTTPException(status_code=400, detail=f"Unsupported Kubernetes auth mode: {mode}")


def _validate_supported_kubeconfig(kubeconfig: dict, context_name: str | None) -> None:
    contexts = {item.get("name"): item.get("context", {}) for item in kubeconfig.get("contexts", [])}
    if not contexts:
        raise HTTPException(status_code=400, detail="Invalid kubeconfig: no contexts defined")
    active_context = context_name or kubeconfig.get("current-context") or next(iter(contexts))
    context = contexts.get(active_context)
    if not isinstance(context, dict):
        raise HTTPException(status_code=400, detail=f"Invalid kubeconfig: context {active_context!r} not found")

    clusters = {item.get("name"): item.get("cluster", {}) for item in kubeconfig.get("clusters", [])}
    users = {item.get("name"): item.get("user", {}) for item in kubeconfig.get("users", [])}
    cluster = clusters.get(context.get("cluster"))
    user = users.get(context.get("user"))
    if not isinstance(cluster, dict) or not cluster.get("server"):
        raise HTTPException(status_code=400, detail="Invalid kubeconfig: selected context has no cluster server")
    if not isinstance(user, dict):
        raise HTTPException(status_code=400, detail="Invalid kubeconfig: selected context has no user")
    if user.get("exec") or user.get("auth-provider"):
        raise HTTPException(
            status_code=400,
            detail="Unsupported kubeconfig: exec/auth-provider plugins are not supported in the web UI",
        )
    unsupported_paths = [
        cluster.get("certificate-authority"),
        user.get("client-certificate"),
        user.get("client-key"),
        user.get("tokenFile"),
    ]
    if any(unsupported_paths):
        raise HTTPException(
            status_code=400,
            detail="Unsupported kubeconfig: local file references are not supported; use inline data or upload certificates directly",
        )
    has_token = bool(user.get("token"))
    has_client_cert = bool(user.get("client-certificate-data")) and bool(user.get("client-key-data"))
    if not has_token and not has_client_cert:
        raise HTTPException(
            status_code=400,
            detail="Unsupported kubeconfig: selected user must contain an inline token or inline client certificate/key",
        )


def _build_openstack_auth(payload: OpenStackLoginPayload) -> openstack_ops.OpenStackAuth:
    mode = (payload.mode or "password").strip().lower()
    if mode == "password":
        return openstack_ops.OpenStackAuth(
            mode="password",
            auth_url=_require(payload.auth_url, "OpenStack auth URL"),
            username=_require(payload.username, "OpenStack username"),
            password=_require(payload.password, "OpenStack password"),
            project_name=_require(payload.project_name, "OpenStack project name"),
            user_domain_name=(payload.user_domain_name or "").strip() or "Default",
            project_domain_name=(payload.project_domain_name or "").strip() or "Default",
            region_name=(payload.region_name or "").strip() or None,
            interface=(payload.interface or "").strip() or None,
            skip_tls_verify=payload.skip_tls_verify,
        )
    if mode == "application_credential":
        return openstack_ops.OpenStackAuth(
            mode="application_credential",
            auth_url=_require(payload.auth_url, "OpenStack auth URL"),
            application_credential_id=_require(
                payload.application_credential_id,
                "OpenStack application credential ID",
            ),
            application_credential_secret=_require(
                payload.application_credential_secret,
                "OpenStack application credential secret",
            ),
            region_name=(payload.region_name or "").strip() or None,
            interface=(payload.interface or "").strip() or None,
            skip_tls_verify=payload.skip_tls_verify,
        )
    if mode == "clouds_yaml":
        config_data = _parse_yaml_document(
            _require(payload.clouds_yaml, "clouds.yaml"),
            "clouds.yaml",
        )
        return _build_openstack_auth_from_clouds_yaml(config_data, payload.cloud_name)
    raise HTTPException(status_code=400, detail=f"Unsupported OpenStack auth mode: {mode}")


def _build_openstack_auth_from_clouds_yaml(
    config_data: dict,
    cloud_name: str | None,
) -> openstack_ops.OpenStackAuth:
    clouds = config_data.get("clouds")
    if not isinstance(clouds, dict) or not clouds:
        raise HTTPException(status_code=400, detail="Invalid clouds.yaml: no clouds mapping found")

    selected_cloud = (cloud_name or "").strip()
    if not selected_cloud:
        if len(clouds) != 1:
            raise HTTPException(
                status_code=400,
                detail="clouds.yaml contains multiple clouds; specify a cloud name",
            )
        selected_cloud = next(iter(clouds))

    cloud = clouds.get(selected_cloud)
    if not isinstance(cloud, dict):
        raise HTTPException(status_code=400, detail=f"clouds.yaml cloud {selected_cloud!r} not found")

    auth = cloud.get("auth")
    if not isinstance(auth, dict):
        raise HTTPException(status_code=400, detail="Invalid clouds.yaml: selected cloud has no auth section")

    region_name = str(cloud.get("region_name", "")).strip() or None
    interface = str(cloud.get("interface", "")).strip() or None
    skip_tls_verify = cloud.get("verify") is False
    if auth.get("application_credential_id") and auth.get("application_credential_secret"):
        return openstack_ops.OpenStackAuth(
            mode="application_credential",
            auth_url=_require(str(auth.get("auth_url", "")), "OpenStack auth URL"),
            application_credential_id=_require(
                str(auth.get("application_credential_id", "")),
                "OpenStack application credential ID",
            ),
            application_credential_secret=_require(
                str(auth.get("application_credential_secret", "")),
                "OpenStack application credential secret",
            ),
            region_name=region_name,
            interface=interface,
            skip_tls_verify=skip_tls_verify,
        )

    return openstack_ops.OpenStackAuth(
        mode="password",
        auth_url=_require(str(auth.get("auth_url", "")), "OpenStack auth URL"),
        username=_require(str(auth.get("username", "")), "OpenStack username"),
        password=str(auth.get("password", "")),
        project_name=_require(str(auth.get("project_name", "")), "OpenStack project name"),
        user_domain_name=str(auth.get("user_domain_name", "Default")).strip() or "Default",
        project_domain_name=str(auth.get("project_domain_name", "Default")).strip() or "Default",
        region_name=region_name,
        interface=interface,
        skip_tls_verify=skip_tls_verify,
    )


@fastapi_app.get("/")
async def index(request: Request):
    record = _sessions.get(request.cookies.get(_SESSION_COOKIE))
    if record is not None:
        return RedirectResponse(url="/app", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(_STATIC / "login.html")


@fastapi_app.get("/app")
async def app(request: Request):
    record = _sessions.get(request.cookies.get(_SESSION_COOKIE))
    if record is None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return FileResponse(_STATIC / "index.html")


@fastapi_app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@fastapi_app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ready"}


@fastapi_app.get("/api/session")
async def api_session(request: Request):
    record = _sessions.get(request.cookies.get(_SESSION_COOKIE))
    if record is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "username": record.username,
        "project_name": record.project_name,
        "role_names": record.role_names,
        "is_admin": record.is_admin,
    }


@fastapi_app.get("/api/app-meta")
async def api_app_meta(request: Request):
    _get_session_record(request)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_app_update_status)


@fastapi_app.get("/api/version")
async def api_version():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_public_version_status)


@fastapi_app.get("/api/app-runtime")
async def api_app_runtime(request: Request):
    _get_session_record(request)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_app_runtime)


@fastapi_app.post("/api/session")
async def api_login(payload: LoginPayload, response: Response):
    k8s_auth = _build_k8s_auth(payload.kubernetes)
    openstack_auth = _build_openstack_auth(payload.openstack)

    try:
        initial_nodes = k8s_ops.get_nodes(auth=k8s_auth)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Kubernetes authentication failed: {exc}",
        ) from exc

    try:
        openstack_ops._conn(auth=openstack_auth).authorize()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"OpenStack authentication failed: {exc}",
        ) from exc

    role_names = openstack_ops.get_current_role_names(auth=openstack_auth)
    server = DrainoServer(
        k8s_auth=k8s_auth,
        openstack_auth=openstack_auth,
        role_names=role_names,
        audit_log=_audit_log_path,
    )
    if _app_loop is not None:
        server.set_loop(_app_loop)
    server._audit.log("session", "-", "started", "web ui user-authenticated session")
    session_id = secrets.token_urlsafe(32)
    _sessions.put(SessionRecord(
        session_id=session_id,
        server=server,
        username=openstack_auth.username,
        project_name=openstack_auth.project_name,
        role_names=role_names,
        is_admin=server.is_admin,
        created_at=time.time(),
        last_seen=time.time(),
    ))
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=_SESSION_TTL,
    )
    server.start_refresh(cached_nodes=initial_nodes)
    return {"ok": True}


@fastapi_app.delete("/api/session")
async def api_logout(request: Request, response: Response):
    _sessions.delete(request.cookies.get(_SESSION_COOKIE))
    response.delete_cookie(_SESSION_COOKIE)
    return {"ok": True}


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


@fastapi_app.get("/api/ovn/lsp/{port_id}")
async def api_ovn_port_detail(port_id: str, request: Request):
    """Return OVN logical switch port detail for a given port UUID."""
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, k8s_ops.get_ovn_port_detail, port_id, session.server.k8s_auth)
        return {"port": data, "error": None}
    except Exception as exc:
        return {"port": None, "error": str(exc)}


@fastapi_app.get("/api/networks/{network_id}/ovn")
async def api_network_ovn(network_id: str, request: Request):
    """Return OVN logical switch and ports for a Neutron network."""
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, k8s_ops.get_ovn_logical_switch, network_id, session.server.k8s_auth)
        return {"ovn": data, "error": None}
    except Exception as exc:
        return {"ovn": None, "error": str(exc)}


@fastapi_app.get("/api/nodes/{node_name}/detail")
async def api_node_detail(node_name: str, request: Request):
    """Return detailed K8s + Nova + hardware stats for the summary tab."""
    session = _get_session_record(request)
    server = session.server
    force_refresh = request.query_params.get("refresh", "").strip().lower() in {"1", "true", "yes"}
    if force_refresh:
        server.invalidate_node_detail(node_name)
    cached = server.get_cached_node_detail(node_name)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    state = server.node_states.get(node_name)
    k8s_future = loop.run_in_executor(None, k8s_ops.get_node_k8s_detail, node_name, server.k8s_auth)
    hw_future  = loop.run_in_executor(
        None,
        k8s_ops.get_node_hardware_info,
        node_name,
        state.hypervisor if state else None,
    )

    nova: dict = {}
    if state and state.is_compute:
        nova = await loop.run_in_executor(None, openstack_ops.get_hypervisor_detail, state.hypervisor, server.openstack_auth)

    k8s = await k8s_future
    hw  = await hw_future
    payload = {"k8s": k8s, "nova": nova, "hw": hw, "error": None}
    server.set_cached_node_detail(node_name, payload)
    return payload


@fastapi_app.get("/api/nodes/{node_name}/metrics")
async def api_node_metrics(node_name: str, request: Request):
    """Return lightweight node monitor metrics from the node agent."""
    session = _get_session_record(request)
    server = session.server
    force_refresh = request.query_params.get("refresh", "").strip().lower() in {"1", "true", "yes"}
    if force_refresh:
        server.invalidate_node_metrics(node_name)
    cached = server.get_cached_node_metrics(node_name)
    if cached is not None:
        return cached
    loop = asyncio.get_running_loop()
    state = server.node_states.get(node_name)
    payload = await loop.run_in_executor(
        None,
        k8s_ops.get_node_monitor_metrics,
        node_name,
        state.hypervisor if state else None,
    )
    server.set_cached_node_metrics(node_name, payload)
    return payload


@fastapi_app.get("/api/nodes/{node_name}/network-stats")
async def api_node_network_stats(node_name: str, request: Request):
    """Return lightweight per-interface throughput counters and rates."""
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    state = session.server.node_states.get(node_name)
    return await loop.run_in_executor(
        None,
        k8s_ops.get_node_network_stats,
        node_name,
        state.hypervisor if state else None,
    )


@fastapi_app.get("/api/nodes/{node_name}/ovn-annotations")
async def api_node_ovn_annotations(node_name: str, request: Request):
    """Return OVN-related annotations from the K8s node."""
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, k8s_ops.get_node_ovn_annotations, node_name, session.server.k8s_auth)
    return result


class AnnotationPatch(BaseModel):
    key: str
    value: Optional[str] = None


@fastapi_app.post("/api/nodes/{node_name}/ovn-annotations")
async def api_patch_ovn_annotation(node_name: str, payload: AnnotationPatch, request: Request):
    """Set or remove a single OVN annotation on a K8s node."""
    session = _get_session_record(request)
    if payload.key not in k8s_ops.OVN_ANNOTATION_KEYS:
        return {"ok": False, "error": f"Key {payload.key!r} not in allowed OVN annotation keys"}
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, k8s_ops.patch_node_annotation, node_name, payload.key, payload.value, session.server.k8s_auth
        )
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


class ManagedNoSchedulePatch(BaseModel):
    enabled: bool


@fastapi_app.post("/api/nodes/{node_name}/taints/noschedule")
async def api_patch_managed_noschedule_taint(
    node_name: str,
    payload: ManagedNoSchedulePatch,
    request: Request,
):
    """Add or remove Draino's managed NoSchedule taint on a K8s node."""
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            k8s_ops.set_managed_noschedule_taint,
            node_name,
            payload.enabled,
            session.server.k8s_auth,
        )
        session.server.start_refresh(silent=True)
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@fastapi_app.get("/api/nodes/{node_name}/network-interfaces")
async def api_node_network_interfaces(node_name: str, request: Request):
    """Return physical and bond network interfaces discovered from the host."""
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    state = session.server.node_states.get(node_name)
    result = await loop.run_in_executor(
        None, k8s_ops.get_node_network_interfaces, node_name, state.hypervisor if state else None
    )
    return result


@fastapi_app.get("/api/k8s/namespaces")
async def api_k8s_namespaces(request: Request):
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_namespaces, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@fastapi_app.get("/api/k8s/pods")
async def api_k8s_pods(request: Request, namespace: Optional[str] = None):
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_pods, namespace, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@fastapi_app.get("/api/k8s/services")
async def api_k8s_services(request: Request, namespace: Optional[str] = None):
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_services, namespace, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@fastapi_app.get("/api/k8s/pvs")
async def api_k8s_pvs(request: Request):
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_pvs, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@fastapi_app.get("/api/k8s/pvcs")
async def api_k8s_pvcs(request: Request, namespace: Optional[str] = None):
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_pvcs, namespace, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@fastapi_app.get("/api/k8s/crds")
async def api_k8s_crds(request: Request):
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        return {"items": await loop.run_in_executor(None, k8s_ops.list_k8s_crds, session.server.k8s_auth), "error": None}
    except Exception as exc:
        return {"items": [], "error": str(exc)}


@fastapi_app.get("/api/networks/{network_id}")
async def api_network_detail(network_id: str, request: Request):
    """Return subnets and segments for a single network."""
    session = _get_session_record(request)
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, _get_network_detail, network_id, session.server.openstack_auth)
        return {"network": data, "error": None}
    except Exception as exc:
        return {"network": None, "error": str(exc)}


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


@fastapi_app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    session = _get_ws_session(ws)
    if session is None:
        await ws.close(code=4401)
        return
    server = session.server
    await server.connect(ws)
    try:
        while True:
            raw    = await ws.receive_text()
            msg    = json.loads(raw)
            action = msg.get("action")
            node   = msg.get("node")

            if   action == "refresh":                              server.start_refresh()
            elif action == "refresh_silent":                       server.start_refresh(silent=True)
            elif action == "evacuate"       and node:             server.action_evacuate(node)
            elif action == "drain_quick"    and node:             server.action_drain_quick(node)
            elif action == "undrain"        and node:             server.action_undrain(node)
            elif action == "reboot_request" and node:             server.action_reboot_request(node)
            elif action == "reboot_confirm" and node:             server.action_reboot_confirm(node)
            elif action == "reboot_cancel"  and node:             server.action_reboot_cancel(node)
            elif action == "check_etcd":                          server.start_etcd_check()
            elif action == "get_preflight"     and node:            server.start_preflight(node)
            elif action == "refresh_preflight"  and node:            server.start_preflight(node, silent=True)
            elif action == "migrate_instance" and node:
                iid = msg.get("instance_id")
                if iid: server.action_migrate_instance(node, iid)
            elif action == "get_pods"       and node:
                threading.Thread(
                    target=_serve_pods, args=(server, ws, node), daemon=True
                ).start()

    except WebSocketDisconnect:
        server.disconnect(ws)


def _serve_pods(server: DrainoServer, ws: WebSocket, node_name: str) -> None:
    """Fetch pods in a thread and push back as a 'pods' message."""
    try:
        raw_pods = k8s_ops.get_pods_on_node(node_name, auth=server.k8s_auth)
        pods = []
        for p in raw_pods:
            p2 = dict(p)
            ca = p2.get("created_at")
            if ca is not None and hasattr(ca, "isoformat"):
                p2["created_at"] = ca.isoformat()
            pods.append(p2)
    except Exception as exc:
        pods = [{"error": str(exc)}]
    server._push({"type": "pods", "node": node_name, "pods": pods})


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
