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
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from ..audit import AuditLogger
from ..models import NodePhase, NodeState
from ..operations import k8s_ops, openstack_ops
from ..render import format_uptime
from .. import worker

_STATIC = Path(__file__).parent / "static"


# ── State serialisation ───────────────────────────────────────────────────────

def _serialise(state: NodeState) -> dict:
    """Convert a NodeState to a JSON-serialisable dict."""
    return {
        "k8s_name":            state.k8s_name,
        "hypervisor":          state.hypervisor,
        "phase":               state.phase.name.lower(),
        "k8s_ready":           state.k8s_ready,
        "k8s_cordoned":        state.k8s_cordoned,
        "kernel_version":      state.kernel_version,
        "uptime":              state.uptime,
        "is_etcd":             state.is_etcd,
        "etcd_healthy":        state.etcd_healthy,
        "etcd_checking":       state.etcd_checking,
        "is_compute":          state.is_compute,
        "compute_status":      state.compute_status,
        "amphora_count":       state.amphora_count,
        "vm_count":            state.vm_count,
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
        cloud:     Optional[str] = None,
        context:   Optional[str] = None,
        audit_log: Optional[str] = None,
    ) -> None:
        self.cloud   = cloud
        self.context = context
        self._audit  = AuditLogger(path=audit_log)

        self.node_states:      dict[str, NodeState] = {}
        self._last_k8s_nodes:  list[dict]           = []
        self._etcd_node_names: set[str]             = set()

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

    def start_refresh(self) -> None:
        self._push({"type": "log", "node": "-", "message": "Refreshing node list…", "color": "dim"})
        threading.Thread(target=self._load_nodes_bg, daemon=True).start()

    def _load_nodes_bg(self) -> None:
        try:
            nodes = k8s_ops.get_nodes()
        except Exception as exc:
            self._push({"type": "log", "node": "-", "message": f"Error loading K8s nodes: {exc}", "color": "error"})
            return

        self._last_k8s_nodes = nodes

        for nd in nodes:
            name     = nd["name"]
            hostname = nd.get("hostname", name)
            if name not in self.node_states:
                self.node_states[name] = NodeState(k8s_name=name, hypervisor=hostname)
            state = self.node_states[name]
            state.k8s_ready      = nd.get("ready", True)
            state.k8s_cordoned   = nd.get("cordoned", False)
            state.kernel_version = nd.get("kernel_version")
            ready_since = nd.get("ready_since")
            if ready_since is not None:
                state.uptime = format_uptime(ready_since)

        # Push K8s-only view immediately so the page shows nodes fast.
        self._push({"type": "full_state", "nodes": {k: _serialise(v) for k, v in self.node_states.items()}})

        def _os_log(msg: str) -> None:
            self._push({"type": "log", "node": "-", "message": msg, "color": "dim"})

        try:
            summaries = openstack_ops.get_all_host_summaries(log_cb=_os_log)
        except Exception as exc:
            self._push({"type": "log", "node": "-", "message": f"OpenStack summary failed: {exc}", "color": "warn"})
            return

        etcd_names = k8s_ops.get_etcd_node_names()
        self._etcd_node_names = etcd_names

        for nd in nodes:
            name     = nd["name"]
            hostname = nd.get("hostname", name)
            summary  = summaries.get(hostname, {})
            state    = self.node_states.get(name)
            if not state:
                continue
            state.is_etcd = name in self._etcd_node_names
            if state.phase == NodePhase.IDLE:
                state.is_compute     = summary.get("is_compute", False)
                state.compute_status = summary.get("compute_status")
                state.amphora_count  = summary.get("amphora_count")
                state.vm_count       = summary.get("vm_count")

        self._push({"type": "full_state", "nodes": {k: _serialise(v) for k, v in self.node_states.items()}})
        self._push({"type": "log", "node": "-", "message": f"Node list refreshed — {len(nodes)} nodes loaded.", "color": "success"})

    # ── Preflight ─────────────────────────────────────────────────────────────

    def start_preflight(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state or not state.is_compute or state.phase != NodePhase.IDLE:
            return
        if state.preflight_loading:
            return
        state.preflight_loading   = True
        state.preflight_instances = []
        self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        threading.Thread(target=self._preflight_bg, args=(node_name,), daemon=True).start()

    def _preflight_bg(self, node_name: str) -> None:
        state = self.node_states.get(node_name)
        if not state:
            return
        try:
            state.preflight_instances = openstack_ops.get_instances_preflight(state.hypervisor)
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
            state.etcd_healthy  = k8s_ops.check_etcd_service(state.hypervisor)
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
            args=(state, self._update_cb(node_name), self._log_cb(node_name), audit_cb),
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
            args=(state, self._update_cb(node_name), self._log_cb(node_name), audit_cb),
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
            args=(state, self._update_cb(node_name), self._log_cb(node_name), audit_cb),
            daemon=True,
        ).start()

    def action_reboot_request(self, node_name: str) -> None:
        """Validate and initiate reboot: etcd preflight if needed, else confirm prompt."""
        state = self.node_states.get(node_name)
        if not state:
            return
        if state.phase in (NodePhase.REBOOTING, NodePhase.RUNNING, NodePhase.UNDRAINING):
            self._push({"type": "log", "node": node_name, "message": "Cannot reboot while operation is in progress.", "color": "warn"})
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
            s.etcd_healthy = k8s_ops.check_etcd_service(s.hypervisor)
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
        state = self.node_states.get(node_name)
        if not state:
            return

        audit_cb = self._audit_cb("reboot", node_name)
        audit_cb("started", f"hypervisor={state.hypervisor}")
        self._push({"type": "log", "node": node_name, "message": f"Rebooting {node_name}…", "color": "important"})

        threading.Thread(
            target=worker.run_reboot,
            args=(state, self._update_cb(node_name), self._log_cb(node_name, "magenta"), audit_cb),
            daemon=True,
        ).start()

    def action_reboot_cancel(self, node_name: str) -> None:
        self._audit.log("reboot", node_name, "cancelled")
        self._push({"type": "log", "node": node_name, "message": "Reboot cancelled.", "color": "dim"})


# ── OpenStack resource helpers (called in thread pool) ───────────────────────

def _get_networks() -> list[dict]:
    """Return all Neutron networks visible to the configured credential."""
    conn = openstack_ops._conn()
    result = []
    for n in conn.network.networks():
        d = n.to_dict() if hasattr(n, "to_dict") else {}
        result.append({
            "id":           n.id,
            "name":         n.name or "(unnamed)",
            "status":       n.status or "UNKNOWN",
            "admin_state":  "up" if n.is_admin_state_up else "down",
            "shared":       bool(n.is_shared),
            "external":     bool(d.get("router:external", False)),
            "network_type": d.get("provider:network_type") or "",
            "project_id":   n.project_id or "",
            "subnet_count": len(list(n.subnet_ids or [])),
        })
    return result


def _get_network_detail(network_id: str) -> dict:
    """Return subnets and segments for a single Neutron network."""
    conn = openstack_ops._conn()
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


def _get_volumes() -> tuple[list[dict], bool]:
    """Return all Cinder volumes.  Falls back to project-scope on permission error.

    Returns (volumes, all_projects_succeeded).
    """
    conn = openstack_ops._conn()
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

_server: Optional[DrainoServer] = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if _server is not None:
        _server.set_loop(asyncio.get_running_loop())
        _server.start_refresh()
    yield


fastapi_app = FastAPI(title="Draino", lifespan=_lifespan)


@fastapi_app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@fastapi_app.get("/api/networks")
async def api_networks():
    """List Neutron networks (admin sees all; non-admin sees project scope)."""
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, _get_networks)
        return {"networks": data, "error": None}
    except Exception as exc:
        return {"networks": [], "error": str(exc)}


@fastapi_app.get("/api/networks/{network_id}")
async def api_network_detail(network_id: str):
    """Return subnets and segments for a single network."""
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, _get_network_detail, network_id)
        return {"network": data, "error": None}
    except Exception as exc:
        return {"network": None, "error": str(exc)}


@fastapi_app.get("/api/volumes")
async def api_volumes():
    """List Cinder volumes (admin sees all projects; non-admin sees own project)."""
    loop = asyncio.get_running_loop()
    try:
        data, all_projects = await loop.run_in_executor(None, _get_volumes)
        return {"volumes": data, "all_projects": all_projects, "error": None}
    except Exception as exc:
        return {"volumes": [], "all_projects": False, "error": str(exc)}


@fastapi_app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await _server.connect(ws)
    try:
        while True:
            raw    = await ws.receive_text()
            msg    = json.loads(raw)
            action = msg.get("action")
            node   = msg.get("node")

            if   action == "refresh":                              _server.start_refresh()
            elif action == "evacuate"       and node:             _server.action_evacuate(node)
            elif action == "drain_quick"    and node:             _server.action_drain_quick(node)
            elif action == "undrain"        and node:             _server.action_undrain(node)
            elif action == "reboot_request" and node:             _server.action_reboot_request(node)
            elif action == "reboot_confirm" and node:             _server.action_reboot_confirm(node)
            elif action == "reboot_cancel"  and node:             _server.action_reboot_cancel(node)
            elif action == "check_etcd":                          _server.start_etcd_check()
            elif action == "get_preflight"  and node:             _server.start_preflight(node)
            elif action == "get_pods"       and node:
                threading.Thread(
                    target=_serve_pods, args=(_server, ws, node), daemon=True
                ).start()

    except WebSocketDisconnect:
        _server.disconnect(ws)


def _serve_pods(server: DrainoServer, ws: WebSocket, node_name: str) -> None:
    """Fetch pods in a thread and push back as a 'pods' message."""
    try:
        raw_pods = k8s_ops.get_pods_on_node(node_name)
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
    global _server
    openstack_ops.configure(cloud=cloud)
    k8s_ops.configure(context=context)
    _server = DrainoServer(cloud=cloud, context=context, audit_log=audit_log)
    _server._audit.log("session", "-", "started", f"web ui host={host} port={port}")
    print(f"Draino web UI → http://{host}:{port}")
    uvicorn.run(fastapi_app, host=host, port=port, log_level="warning")
