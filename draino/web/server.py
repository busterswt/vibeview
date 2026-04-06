"""FastAPI web server for Draino.

Replaces the Textual TUI with a browser-based UI.  All worker logic
(worker.py, operations/) is reused unchanged.  The Textual layer is
swapped for a FastAPI app + WebSocket push model.

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
import secrets
import threading
import time
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
from pydantic import BaseModel

from .. import worker
from ..audit import AuditLogger
from ..models import NodePhase, NodeState
from ..operations import k8s_ops, openstack_ops
from ..reboot import is_ready_for_reboot
from ..render import format_uptime

_STATIC = Path(__file__).parent / "static"
_SESSION_COOKIE = "draino_session"
_SESSION_TTL = 60 * 60 * 12
_HOST_SIGNALS_TTL = int(os.getenv("DRAINO_HOST_SIGNALS_TTL", "300"))
_app_loop: Optional[asyncio.AbstractEventLoop] = None
_audit_log_path: Optional[str] = None
_LOGGER = logging.getLogger("draino.web")


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


# ── State serialisation ───────────────────────────────────────────────────────

def _serialise(state: NodeState) -> dict:
    """Convert a NodeState to a JSON-serialisable dict."""
    return {
        "k8s_name":            state.k8s_name,
        "hypervisor":          state.hypervisor,
        "phase":               state.phase.name.lower(),
        "k8s_ready":           state.k8s_ready,
        "k8s_cordoned":        state.k8s_cordoned,
        "k8s_taints":          list(state.k8s_taints),
        "kernel_version":      state.kernel_version,
        "latest_kernel_version": state.latest_kernel_version,
        "uptime":              state.uptime,
        "reboot_required":     state.reboot_required,
        "is_etcd":             state.is_etcd,
        "etcd_healthy":        state.etcd_healthy,
        "etcd_checking":       state.etcd_checking,
        "is_compute":          state.is_compute,
        "compute_status":      state.compute_status,
        "amphora_count":       state.amphora_count,
        "vm_count":            state.vm_count,
        "availability_zone":   state.availability_zone,
        "aggregates":          state.aggregates,
        "preflight_loading":   state.preflight_loading,
        "preflight_instances": state.preflight_instances,
        "reboot_start":        state.reboot_start,
        "reboot_downtime":     state.reboot_downtime,
        "steps": [
            {
                "key":    s.key,
                "label":  s.label,
                "status": s.status.name.lower(),
                "detail": s.detail,
            }
            for s in state.steps
        ],
        "instances": [
            {
                "id":               i.id,
                "name":             i.name,
                "status":           i.status,
                "is_amphora":       i.is_amphora,
                "migration_status": i.migration_status,
                "lb_id":            i.lb_id,
                "failover_status":  i.failover_status,
            }
            for i in state.instances
        ],
        "log_buffer": list(state.log_buffer[-100:]),
    }


# ── Server class ──────────────────────────────────────────────────────────────

class DrainoServer:
    """Holds all runtime state and coordinates workers with WebSocket clients."""

    def __init__(
        self,
        k8s_auth:  Optional[k8s_ops.K8sAuth] = None,
        openstack_auth: Optional[openstack_ops.OpenStackAuth] = None,
        role_names: Optional[list[str]] = None,
        audit_log: Optional[str] = None,
    ) -> None:
        self.k8s_auth = k8s_auth
        self.openstack_auth = openstack_auth
        self.role_names = role_names or []
        self.is_admin = any(role.lower() == "admin" for role in self.role_names)
        self._audit  = AuditLogger(path=audit_log)

        self.node_states:      dict[str, NodeState] = {}
        self._last_k8s_nodes:  list[dict]           = []
        self._etcd_node_names: set[str]             = set()
        self._host_signal_refresh_at: dict[str, float] = {}

        self._clients: Set[WebSocket]                        = set()
        self._loop:    Optional[asyncio.AbstractEventLoop]   = None

    # ── WebSocket lifecycle ───────────────────────────────────────────────────

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        # Send current state to the newly connected client.
        await ws.send_text(json.dumps({
            "type":  "full_state",
            "nodes": {k: _serialise(v) for k, v in self.node_states.items()},
        }))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    # ── Broadcast helpers ─────────────────────────────────────────────────────

    def _push(self, msg: dict) -> None:
        """Queue a broadcast from any thread (thread-safe)."""
        if msg.get("type") == "log":
            _LOGGER.info(
                "node=%s color=%s message=%s",
                msg.get("node"),
                msg.get("color"),
                msg.get("message"),
            )
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    async def _broadcast(self, msg: dict) -> None:
        data = json.dumps(msg)
        dead: Set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── Worker callback factories ─────────────────────────────────────────────

    def _update_cb(self, node_name: str):
        def cb() -> None:
            state = self.node_states.get(node_name)
            if state:
                self._push({
                    "type": "state_update",
                    "node": node_name,
                    "data": _serialise(state),
                })
        return cb

    def _log_cb(self, node_name: str, color: str = "cyan"):
        def cb(msg: str) -> None:
            self._push({"type": "log", "node": node_name, "message": msg, "color": color})
        return cb

    def _audit_cb(self, action: str, node_name: str):
        def cb(event: str, detail: str = "") -> None:
            self._audit.log(action, node_name, event, detail)
        return cb

    # ── Node loading ──────────────────────────────────────────────────────────

    def start_refresh(self, cached_nodes: Optional[list[dict]] = None, silent: bool = False) -> None:
        if not silent:
            self._push({"type": "log", "node": "-", "message": "Refreshing node list…", "color": "dim"})
        threading.Thread(target=self._load_nodes_bg, args=(cached_nodes, silent), daemon=True).start()

    def _apply_k8s_nodes(self, nodes: list[dict]) -> None:
        self._last_k8s_nodes = nodes

        for nd in nodes:
            name     = nd["name"]
            hostname = nd.get("hostname", name)
            if name not in self.node_states:
                self.node_states[name] = NodeState(k8s_name=name, hypervisor=hostname)
            state = self.node_states[name]
            state.k8s_ready      = nd.get("ready", True)
            state.k8s_cordoned   = nd.get("cordoned", False)
            state.k8s_taints     = list(nd.get("taints", []))
            state.kernel_version = nd.get("kernel_version")
            ready_since = nd.get("ready_since")
            if ready_since is not None:
                state.uptime = format_uptime(ready_since)

        # Push K8s-only view immediately so the page shows nodes fast.
        self._push({"type": "full_state", "nodes": {k: _serialise(v) for k, v in self.node_states.items()}})

    def _load_nodes_bg(self, cached_nodes: Optional[list[dict]] = None, silent: bool = False) -> None:
        nodes = cached_nodes
        if nodes is None:
            try:
                nodes = k8s_ops.get_nodes(auth=self.k8s_auth)
            except Exception as exc:
                self._push({"type": "log", "node": "-", "message": f"Error loading K8s nodes: {exc}", "color": "error"})
                return

        self._apply_k8s_nodes(nodes)

        def _os_log(msg: str) -> None:
            self._push({"type": "log", "node": "-", "message": msg, "color": "dim"})

        try:
            summaries = openstack_ops.get_all_host_summaries(log_cb=_os_log, auth=self.openstack_auth)
        except Exception as exc:
            self._push({"type": "log", "node": "-", "message": f"OpenStack summary failed: {exc}", "color": "warn"})
            return

        etcd_names = k8s_ops.get_etcd_node_names(auth=self.k8s_auth)
        self._etcd_node_names = etcd_names
        now = time.time()

        for nd in nodes:
            name     = nd["name"]
            hostname = nd.get("hostname", name)
            summary  = summaries.get(hostname, {})
            state    = self.node_states.get(name)
            if not state:
                continue
            state.is_etcd          = name in self._etcd_node_names
            state.availability_zone = summary.get("availability_zone")
            state.aggregates        = summary.get("aggregates", [])
            if state.phase == NodePhase.IDLE:
                state.is_compute     = summary.get("is_compute", False)
                state.compute_status = summary.get("compute_status")
                state.amphora_count  = summary.get("amphora_count")
                state.vm_count       = summary.get("vm_count")
            if self._should_refresh_host_signals(name, now=now, force=not silent):
                signals = k8s_ops.get_node_host_signals(name, hostname)
                if signals.get("kernel_version"):
                    state.kernel_version = signals.get("kernel_version")
                state.latest_kernel_version = signals.get("latest_kernel_version")
                state.reboot_required = bool(signals.get("reboot_required", False))
                self._host_signal_refresh_at[name] = now

        self._push({"type": "full_state", "nodes": {k: _serialise(v) for k, v in self.node_states.items()}})
        if not silent:
            self._push({"type": "log", "node": "-", "message": f"Node list refreshed — {len(nodes)} nodes loaded.", "color": "success"})

    def _should_refresh_host_signals(self, node_name: str, now: float, force: bool = False) -> bool:
        if force:
            return True
        last_refresh = self._host_signal_refresh_at.get(node_name)
        if last_refresh is None:
            return True
        return (now - last_refresh) >= _HOST_SIGNALS_TTL

    # ── Preflight ─────────────────────────────────────────────────────────────

    def start_preflight(self, node_name: str, silent: bool = False) -> None:
        """Fetch instances on the hypervisor.

        silent=True keeps the existing list visible while refreshing in the
        background (no spinner, no list clear).  Used for periodic auto-refresh.
        """
        state = self.node_states.get(node_name)
        if not state or not state.is_compute or state.phase != NodePhase.IDLE:
            return
        if state.preflight_loading:
            return
        state.preflight_loading = True
        if not silent:
            state.preflight_instances = []
            self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        threading.Thread(target=self._preflight_bg, args=(node_name,), daemon=True).start()

    def _preflight_bg(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return
        try:
            state.preflight_instances = openstack_ops.get_instances_preflight(
                state.hypervisor,
                auth=self.openstack_auth,
            )
        except Exception as exc:
            state.preflight_instances = []
            self._push({"type": "log", "node": "-", "message": f"Preflight failed for {node_name}: {exc}", "color": "warn"})
        finally:
            state.preflight_loading = False
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})

    # ── etcd health check ─────────────────────────────────────────────────────

    def start_etcd_check(self) -> None:
        threading.Thread(target=self._etcd_check_bg, daemon=True).start()

    def _etcd_check_bg(self) -> None:
        etcd_states = [s for s in self.node_states.values() if s.is_etcd]
        # Mark all etcd nodes as "checking" first so the UI can show a spinner
        for state in etcd_states:
            state.etcd_checking = True
            self._push({"type": "state_update", "node": state.k8s_name, "data": _serialise(state)})
        for state in etcd_states:
            state.etcd_healthy  = k8s_ops.check_etcd_service(state.k8s_name, state.hypervisor)
            state.etcd_checking = False
            self._push({"type": "state_update", "node": state.k8s_name, "data": _serialise(state)})

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_evacuate(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return
        if not state.is_compute:
            self._push({"type": "log", "node": node_name, "message": "Not a compute node — no evacuation needed.", "color": "warn"})
            return
        if state.phase == NodePhase.RUNNING:
            self._push({"type": "log", "node": node_name, "message": "Evacuation already in progress.", "color": "warn"})
            return

        state.phase               = NodePhase.RUNNING
        state.instances           = []
        state.log_buffer          = []
        state.preflight_instances = []
        state.preflight_loading   = False
        state.init_steps()

        audit_cb = self._audit_cb("evacuation", node_name)
        audit_cb("started")
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        self._push({"type": "log", "node": node_name, "message": f"Starting evacuation of {node_name}…", "color": "important"})

        threading.Thread(
            target=worker.run_workflow,
            args=(state, self._update_cb(node_name), self._log_cb(node_name), audit_cb, self.k8s_auth, self.openstack_auth),
            daemon=True,
        ).start()

    def action_drain_quick(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state or state.phase == NodePhase.RUNNING:
            return

        state.phase = NodePhase.RUNNING
        state.init_quick_drain_steps(state.is_compute)

        audit_cb = self._audit_cb("drain_quick", node_name)
        audit_cb("started")
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        self._push({"type": "log", "node": node_name, "message": f"Draining {node_name}…", "color": "important"})

        threading.Thread(
            target=worker.run_drain_quick,
            args=(state, self._update_cb(node_name), self._log_cb(node_name), audit_cb, self.k8s_auth, self.openstack_auth),
            daemon=True,
        ).start()

    def action_undrain(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state or state.phase == NodePhase.RUNNING:
            return

        state.phase = NodePhase.UNDRAINING
        state.init_undrain_steps(state.is_compute)

        audit_cb = self._audit_cb("undrain", node_name)
        audit_cb("started")
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        self._push({"type": "log", "node": node_name, "message": f"Undraining {node_name}…", "color": "important"})

        threading.Thread(
            target=worker.run_undrain,
            args=(state, self._update_cb(node_name), self._log_cb(node_name), audit_cb, self.k8s_auth, self.openstack_auth),
            daemon=True,
        ).start()

    def action_reboot_request(self, node_name: str) -> None:
        """Validate and initiate reboot: etcd preflight if needed, else confirm prompt."""
        if not self.is_admin:
            detail = "Reboot requires the OpenStack 'admin' role."
            self._audit.log("reboot", node_name, "blocked", detail)
            self._push({"type": "log", "node": node_name, "message": detail, "color": "warn"})
            return
        state = self.node_states.get(node_name)
        if not state:
            return
        reboot_ready, detail = is_ready_for_reboot(state)
        if not reboot_ready:
            self._push({"type": "log", "node": node_name, "message": detail, "color": "warn"})
            return

        if state.is_etcd:
            self._push({"type": "log", "node": "-", "message": "Checking etcd quorum before reboot…", "color": "dim"})
            threading.Thread(target=self._etcd_reboot_preflight_bg, args=(node_name,), daemon=True).start()
        else:
            self._push({"type": "reboot_confirm_needed", "node": node_name})

    def _etcd_reboot_preflight_bg(self, node_name: str) -> None:
        etcd_states   = [s for s in self.node_states.values() if s.is_etcd]
        etcd_total    = len(etcd_states)
        quorum_needed = (etcd_total // 2) + 1

        for s in etcd_states:
            s.etcd_healthy = k8s_ops.check_etcd_service(s.k8s_name, s.hypervisor)
            self._push({"type": "state_update", "node": s.k8s_name, "data": _serialise(s)})

        healthy_count = sum(1 for s in etcd_states if s.etcd_healthy is True)
        this_state    = self.node_states.get(node_name)
        this_healthy  = this_state is not None and this_state.etcd_healthy is True
        remaining     = healthy_count - (1 if this_healthy else 0)

        if remaining < quorum_needed:
            detail = (
                f"{healthy_count}/{etcd_total} healthy, "
                f"rebooting would leave {remaining} (quorum={quorum_needed})"
            )
            self._audit.log("reboot", node_name, "blocked", detail)
            self._push({"type": "reboot_blocked", "node": node_name, "detail": detail})
        else:
            self._push({"type": "reboot_confirm_needed", "node": node_name})

    def action_reboot_confirm(self, node_name: str) -> None:
        if not self.is_admin:
            detail = "Reboot requires the OpenStack 'admin' role."
            self._audit.log("reboot", node_name, "blocked", detail)
            self._push({"type": "log", "node": node_name, "message": detail, "color": "warn"})
            return
        state = self.node_states.get(node_name)
        if not state:
            return

        audit_cb = self._audit_cb("reboot", node_name)
        audit_cb("started", f"hypervisor={state.hypervisor}")
        self._push({"type": "log", "node": node_name, "message": f"Rebooting {node_name}…", "color": "important"})

        threading.Thread(
            target=worker.run_reboot,
            args=(state, self._update_cb(node_name), self._log_cb(node_name, "magenta"), audit_cb, self.k8s_auth),
            daemon=True,
        ).start()

    def action_reboot_cancel(self, node_name: str) -> None:
        self._audit.log("reboot", node_name, "cancelled")
        self._push({"type": "log", "node": node_name, "message": "Reboot cancelled.", "color": "dim"})

    # ── Individual instance migration ─────────────────────────────────────────

    def action_migrate_instance(self, node_name: str, instance_id: str) -> None:
        """Live-migrate a single instance outside of a full evacuation workflow."""
        state = self.node_states.get(node_name)
        if not state or state.phase.name.lower() != "idle":
            self._push({"type": "log", "node": node_name,
                        "message": "Cannot migrate: node is not idle.", "color": "warn"})
            return
        self._push({
            "type": "instance_migrate_status",
            "node": node_name,
            "instance_id": instance_id,
            "status": "migrating",
        })
        threading.Thread(
            target=self._migrate_instance_bg,
            args=(node_name, instance_id),
            daemon=True,
        ).start()

    def _migrate_instance_bg(self, node_name: str, instance_id: str) -> None:
        log = self._log_cb(node_name)
        try:
            openstack_ops.live_migrate_server(instance_id, log, auth=self.openstack_auth)
        except Exception as exc:
            log(f"Migration trigger failed for {instance_id}: {exc}")
            self._push({"type": "instance_migrate_status", "node": node_name,
                        "instance_id": instance_id, "status": "error"})
            return

        # Poll for completion (up to 10 minutes)
        deadline = time.time() + 600
        time.sleep(3)
        while time.time() < deadline:
            task_state = openstack_ops.get_server_task_state(instance_id, auth=self.openstack_auth)
            srv_status = openstack_ops.get_server_status(instance_id, auth=self.openstack_auth)
            if srv_status == "ERROR":
                log(f"Instance {instance_id} entered ERROR state during migration")
                self._push({"type": "instance_migrate_status", "node": node_name,
                            "instance_id": instance_id, "status": "error"})
                return
            if task_state is None and srv_status == "ACTIVE":
                log(f"Instance {instance_id} migrated successfully")
                self._push({"type": "instance_migrate_status", "node": node_name,
                            "instance_id": instance_id, "status": "complete"})
                # Refresh the preflight list so the moved instance disappears
                self.start_preflight(node_name)
                return
            time.sleep(5)

        log(f"Timeout waiting for instance {instance_id} to finish migrating")
        self._push({"type": "instance_migrate_status", "node": node_name,
                    "instance_id": instance_id, "status": "error"})


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


fastapi_app = FastAPI(title="Draino", lifespan=_lifespan)


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
    return {"k8s": k8s, "nova": nova, "hw": hw, "error": None}


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
