"""Inventory state management for the web UI."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Optional, Set

from fastapi import WebSocket

from .. import node_agent_client, worker
from ..audit import AuditLogger
from ..models import NodePhase, NodeState
from ..operations import k8s_ops, openstack_ops
from ..reboot import is_ready_for_reboot
from ..time_utils import format_uptime

_OPENSTACK_SUMMARY_TTL = float(os.getenv("DRAINO_OPENSTACK_SUMMARY_TTL", "60"))
_HOST_SIGNALS_TTL = int(os.getenv("DRAINO_HOST_SIGNALS_TTL", "300"))
_NODE_DETAIL_TTL = float(os.getenv("DRAINO_NODE_DETAIL_TTL", "30"))
_NODE_METRICS_TTL = float(os.getenv("DRAINO_NODE_METRICS_TTL", "30"))
_OVN_EDGE_TTL = float(os.getenv("DRAINO_OVN_EDGE_TTL", "60"))
_MARIADB_NODE_TTL = float(os.getenv("DRAINO_MARIADB_NODE_TTL", "60"))
_DEFAULT_GET_MARIADB_NODE_NAMES = k8s_ops.get_mariadb_node_names

_LOGGER = logging.getLogger("draino.web")


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
        "node_agent_ready":    state.node_agent_ready,
        "is_edge":             state.is_edge,
        "is_etcd":             state.is_etcd,
        "hosts_mariadb":       state.hosts_mariadb,
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


class DrainoServer:
    """Holds all runtime state and coordinates workers with WebSocket clients."""

    def __init__(
        self,
        k8s_auth: Optional[k8s_ops.K8sAuth] = None,
        openstack_auth: Optional[openstack_ops.OpenStackAuth] = None,
        role_names: Optional[list[str]] = None,
        audit_log: Optional[str] = None,
    ) -> None:
        self.k8s_auth = k8s_auth
        self.openstack_auth = openstack_auth
        self.role_names = role_names or []
        self.is_admin = any(role.lower() == "admin" for role in self.role_names)
        self._audit = AuditLogger(path=audit_log)

        self.node_states: dict[str, NodeState] = {}
        self._last_k8s_nodes: list[dict] = []
        self._etcd_node_names: set[str] = set()
        self._openstack_summary_cache: tuple[float, dict[str, dict]] | None = None
        self._ovn_edge_cache: tuple[float, set[str]] | None = None
        self._mariadb_node_cache: tuple[float, set[str]] | None = None
        self._host_signal_refresh_at: dict[str, float] = {}
        self._node_detail_cache: dict[str, tuple[float, dict]] = {}
        self._node_metrics_cache: dict[str, tuple[float, dict]] = {}

        self._clients: Set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        await ws.send_text(json.dumps({
            "type":  "full_state",
            "nodes": {k: _serialise(v) for k, v in self.node_states.items()},
        }))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

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

    def start_refresh(self, cached_nodes: Optional[list[dict]] = None, silent: bool = False) -> None:
        if not silent:
            self._push({"type": "log", "node": "-", "message": "Refreshing node list…", "color": "dim"})
        threading.Thread(target=self._load_nodes_bg, args=(cached_nodes, silent), daemon=True).start()

    def _apply_k8s_nodes(self, nodes: list[dict]) -> bool:
        self._last_k8s_nodes = nodes
        previous_names = set(self.node_states)
        current_names = {nd["name"] for nd in nodes}

        for removed_name in previous_names - current_names:
            self.node_states.pop(removed_name, None)
            self._host_signal_refresh_at.pop(removed_name, None)
            self._node_detail_cache.pop(removed_name, None)
            self._node_metrics_cache.pop(removed_name, None)

        for nd in nodes:
            name = nd["name"]
            hostname = nd.get("hostname", name)
            if name not in self.node_states:
                self.node_states[name] = NodeState(k8s_name=name, hypervisor=hostname)
            state = self.node_states[name]
            state.k8s_ready = nd.get("ready", True)
            state.k8s_cordoned = nd.get("cordoned", False)
            state.k8s_taints = list(nd.get("taints", []))
            state.kernel_version = nd.get("kernel_version")
            ready_since = nd.get("ready_since")
            if ready_since is not None:
                state.uptime = format_uptime(ready_since)

        return previous_names != current_names

    def _push_inventory_state(self, force_full_state: bool = False) -> None:
        if force_full_state:
            self._push({"type": "full_state", "nodes": {k: _serialise(v) for k, v in self.node_states.items()}})
            return
        for node_name, state in self.node_states.items():
            self._push({
                "type": "state_update",
                "node": node_name,
                "data": _serialise(state),
            })

    def _get_cached_openstack_summaries(self, now: float, force: bool = False) -> dict[str, dict] | None:
        if force or self._openstack_summary_cache is None:
            return None
        expires_at, payload = self._openstack_summary_cache
        if now >= expires_at:
            self._openstack_summary_cache = None
            return None
        return payload

    def _set_cached_openstack_summaries(self, summaries: dict[str, dict], now: float) -> None:
        self._openstack_summary_cache = (now + _OPENSTACK_SUMMARY_TTL, summaries)

    def _get_cached_ovn_edge_nodes(self, now: float, force: bool = False) -> set[str] | None:
        if force or self._ovn_edge_cache is None:
            return None
        expires_at, payload = self._ovn_edge_cache
        if now >= expires_at:
            self._ovn_edge_cache = None
            return None
        return payload

    def _set_cached_ovn_edge_nodes(self, edge_nodes: set[str], now: float) -> None:
        self._ovn_edge_cache = (now + _OVN_EDGE_TTL, set(edge_nodes))

    def _get_cached_mariadb_nodes(self, now: float, force: bool = False) -> set[str] | None:
        if force or self._mariadb_node_cache is None:
            return None
        expires_at, payload = self._mariadb_node_cache
        if now >= expires_at:
            self._mariadb_node_cache = None
            return None
        return payload

    def _set_cached_mariadb_nodes(self, mariadb_nodes: set[str], now: float) -> None:
        self._mariadb_node_cache = (now + _MARIADB_NODE_TTL, set(mariadb_nodes))

    def _load_nodes_bg(self, cached_nodes: Optional[list[dict]] = None, silent: bool = False) -> None:
        now = time.time()
        nodes = cached_nodes
        if nodes is None:
            try:
                nodes = k8s_ops.get_nodes(auth=self.k8s_auth)
            except Exception as exc:
                self._push({"type": "log", "node": "-", "message": f"Error loading K8s nodes: {exc}", "color": "error"})
                return

        membership_changed = self._apply_k8s_nodes(nodes)
        self._push_inventory_state(force_full_state=membership_changed)

        try:
            ready_node_agents = node_agent_client.get_ready_node_names()
        except Exception as exc:
            ready_node_agents = set()
            if not silent:
                self._push({"type": "log", "node": "-", "message": f"Node-agent readiness probe failed: {exc}", "color": "warn"})
        for nd in nodes:
            state = self.node_states.get(nd["name"])
            if state:
                state.node_agent_ready = nd["name"] in ready_node_agents

        def _os_log(msg: str) -> None:
            self._push({"type": "log", "node": "-", "message": msg, "color": "dim"})

        edge_nodes = self._get_cached_ovn_edge_nodes(now=now, force=not silent)
        if edge_nodes is None:
            try:
                edge_nodes = k8s_ops.get_ovn_edge_nodes(auth=self.k8s_auth)
                self._set_cached_ovn_edge_nodes(edge_nodes, now=now)
            except Exception as exc:
                edge_nodes = set()
                if not silent:
                    self._push({"type": "log", "node": "-", "message": f"OVN edge-node probe failed: {exc}", "color": "warn"})

        edge_aliases = {name.lower() for name in edge_nodes}
        edge_aliases |= {name.split(".", 1)[0].lower() for name in edge_nodes}

        mariadb_nodes = self._get_cached_mariadb_nodes(now=now, force=not silent)
        if mariadb_nodes is None:
            if self.k8s_auth is None and k8s_ops.get_mariadb_node_names is _DEFAULT_GET_MARIADB_NODE_NAMES:
                mariadb_nodes = set()
                self._set_cached_mariadb_nodes(mariadb_nodes, now=now)
            else:
                try:
                    mariadb_nodes = k8s_ops.get_mariadb_node_names(auth=self.k8s_auth)
                    self._set_cached_mariadb_nodes(mariadb_nodes, now=now)
                except Exception as exc:
                    mariadb_nodes = set()
                    if not silent:
                        self._push({"type": "log", "node": "-", "message": f"MariaDB pod placement probe failed: {exc}", "color": "warn"})

        summaries = self._get_cached_openstack_summaries(now=now, force=not silent)
        if summaries is None:
            try:
                summaries = openstack_ops.get_all_host_summaries(log_cb=_os_log, auth=self.openstack_auth)
            except Exception as exc:
                self._push({"type": "log", "node": "-", "message": f"OpenStack summary failed: {exc}", "color": "warn"})
                return
            self._set_cached_openstack_summaries(summaries, now=now)

        etcd_names = k8s_ops.get_etcd_node_names(auth=self.k8s_auth)
        self._etcd_node_names = etcd_names

        for nd in nodes:
            name = nd["name"]
            hostname = nd.get("hostname", name)
            summary = summaries.get(hostname, {})
            state = self.node_states.get(name)
            if not state:
                continue
            candidates = {
                state.k8s_name.lower(),
                state.k8s_name.split(".", 1)[0].lower(),
                hostname.lower(),
                hostname.split(".", 1)[0].lower(),
            }
            state.is_edge = any(candidate in edge_aliases for candidate in candidates if candidate)
            state.is_etcd = name in self._etcd_node_names
            state.hosts_mariadb = any(candidate in mariadb_nodes for candidate in candidates if candidate)
            state.availability_zone = summary.get("availability_zone")
            state.aggregates = summary.get("aggregates", [])
            if state.phase == NodePhase.IDLE:
                state.is_compute = summary.get("is_compute", False)
                state.compute_status = summary.get("compute_status")
                state.amphora_count = summary.get("amphora_count")
                state.vm_count = summary.get("vm_count")
            if self._should_refresh_host_signals(name, now=now, force=not silent):
                signals = k8s_ops.get_node_host_signals(name, hostname)
                if signals.get("kernel_version"):
                    state.kernel_version = signals.get("kernel_version")
                state.latest_kernel_version = signals.get("latest_kernel_version")
                state.reboot_required = bool(signals.get("reboot_required", False))
                self._host_signal_refresh_at[name] = now

        self._push_inventory_state(force_full_state=membership_changed)
        if not silent:
            self._push({"type": "log", "node": "-", "message": f"Node list refreshed — {len(nodes)} nodes loaded.", "color": "success"})

    def _should_refresh_host_signals(self, node_name: str, now: float, force: bool = False) -> bool:
        if force:
            return True
        last_refresh = self._host_signal_refresh_at.get(node_name)
        if last_refresh is None:
            return True
        return (now - last_refresh) >= _HOST_SIGNALS_TTL

    def get_cached_node_detail(self, node_name: str, now: float | None = None) -> dict | None:
        entry = self._node_detail_cache.get(node_name)
        if entry is None:
            return None
        check_time = time.time() if now is None else now
        expires_at, payload = entry
        if check_time >= expires_at:
            self._node_detail_cache.pop(node_name, None)
            return None
        return payload

    def set_cached_node_detail(self, node_name: str, payload: dict, now: float | None = None) -> None:
        cache_time = time.time() if now is None else now
        self._node_detail_cache[node_name] = (cache_time + _NODE_DETAIL_TTL, payload)

    def invalidate_node_detail(self, node_name: str) -> None:
        self._node_detail_cache.pop(node_name, None)

    def get_cached_node_metrics(self, node_name: str, now: float | None = None) -> dict | None:
        entry = self._node_metrics_cache.get(node_name)
        if entry is None:
            return None
        check_time = time.time() if now is None else now
        expires_at, payload = entry
        if check_time >= expires_at:
            self._node_metrics_cache.pop(node_name, None)
            return None
        return payload

    def set_cached_node_metrics(self, node_name: str, payload: dict, now: float | None = None) -> None:
        cache_time = time.time() if now is None else now
        self._node_metrics_cache[node_name] = (cache_time + _NODE_METRICS_TTL, payload)

    def invalidate_node_metrics(self, node_name: str) -> None:
        self._node_metrics_cache.pop(node_name, None)

    def start_preflight(self, node_name: str, silent: bool = False) -> None:
        """Fetch instances on the hypervisor."""
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

    def start_etcd_check(self) -> None:
        threading.Thread(target=self._etcd_check_bg, daemon=True).start()

    def _etcd_check_bg(self) -> None:
        etcd_states = [s for s in self.node_states.values() if s.is_etcd]
        for state in etcd_states:
            state.etcd_checking = True
            self._push({"type": "state_update", "node": state.k8s_name, "data": _serialise(state)})
        for state in etcd_states:
            state.etcd_healthy = k8s_ops.check_etcd_service(state.k8s_name, state.hypervisor)
            state.etcd_checking = False
            self._push({"type": "state_update", "node": state.k8s_name, "data": _serialise(state)})

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

        state.phase = NodePhase.RUNNING
        state.instances = []
        state.log_buffer = []
        state.preflight_instances = []
        state.preflight_loading = False
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
        etcd_states = [s for s in self.node_states.values() if s.is_etcd]
        etcd_total = len(etcd_states)
        quorum_needed = (etcd_total // 2) + 1

        for s in etcd_states:
            s.etcd_healthy = k8s_ops.check_etcd_service(s.k8s_name, s.hypervisor)
            self._push({"type": "state_update", "node": s.k8s_name, "data": _serialise(s)})

        healthy_count = sum(1 for s in etcd_states if s.etcd_healthy is True)
        this_state = self.node_states.get(node_name)
        this_healthy = this_state is not None and this_state.etcd_healthy is True
        remaining = healthy_count - (1 if this_healthy else 0)

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
            task_state = openstack_ops.get_server_task_state(instance_id, auth=self.openstack_auth) or ""
            if "migrat" in task_state.lower():
                log(f"Live migration for {instance_id} timed out but instance is already migrating")
            else:
                log(f"Live migration failed for {instance_id}: {exc} — trying cold migration")
                try:
                    openstack_ops.cold_migrate_server(instance_id, log, auth=self.openstack_auth)
                except Exception as cold_exc:
                    log(f"Migration trigger failed for {instance_id}: {cold_exc}")
                    self._push({"type": "instance_migrate_status", "node": node_name,
                                "instance_id": instance_id, "status": "error"})
                    return

        deadline = time.time() + 600
        time.sleep(3)
        while time.time() < deadline:
            task_state = openstack_ops.get_server_task_state(instance_id, auth=self.openstack_auth)
            srv_status = openstack_ops.get_server_status(instance_id, auth=self.openstack_auth)
            if srv_status == "VERIFY_RESIZE":
                try:
                    openstack_ops.confirm_resize_server(instance_id, log, auth=self.openstack_auth)
                except Exception as exc:
                    log(f"Confirm resize failed for {instance_id}: {exc}")
                    self._push({"type": "instance_migrate_status", "node": node_name,
                                "instance_id": instance_id, "status": "error"})
                    return
            if srv_status == "ERROR":
                log(f"Instance {instance_id} entered ERROR state during migration")
                self._push({"type": "instance_migrate_status", "node": node_name,
                            "instance_id": instance_id, "status": "error"})
                return
            if task_state is None and srv_status == "ACTIVE":
                log(f"Instance {instance_id} migrated successfully")
                self._push({"type": "instance_migrate_status", "node": node_name,
                            "instance_id": instance_id, "status": "complete"})
                self.start_preflight(node_name)
                return
            time.sleep(5)

        log(f"Timeout waiting for instance {instance_id} to finish migrating")
        self._push({"type": "instance_migrate_status", "node": node_name,
                    "instance_id": instance_id, "status": "error"})
