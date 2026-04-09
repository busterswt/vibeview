"""Inventory refresh and cache helpers."""
from __future__ import annotations

import os
import threading
import time

from .. import node_agent_client
from ..models import NodePhase, NodeState
from ..operations import k8s_ops, openstack_ops
from ..time_utils import format_uptime
from .latency import measure_latency
from .serialise import serialise_state as _serialise

_OPENSTACK_SUMMARY_TTL = float(os.getenv("DRAINO_OPENSTACK_SUMMARY_TTL", "60"))
_HOST_SIGNALS_TTL = int(os.getenv("DRAINO_HOST_SIGNALS_TTL", "300"))
_NODE_DETAIL_TTL = float(os.getenv("DRAINO_NODE_DETAIL_TTL", "30"))
_NODE_METRICS_TTL = float(os.getenv("DRAINO_NODE_METRICS_TTL", "30"))
_OVN_EDGE_TTL = float(os.getenv("DRAINO_OVN_EDGE_TTL", "60"))
_MARIADB_NODE_TTL = float(os.getenv("DRAINO_MARIADB_NODE_TTL", "60"))
_DEFAULT_GET_MARIADB_NODE_NAMES = k8s_ops.get_mariadb_node_names


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


class InventoryRefreshMixin:
    def start_refresh(self, cached_nodes: list[dict] | None = None, silent: bool = False) -> None:
        if not silent:
            self._push({"type": "log", "node": "-", "message": "Refreshing node list…", "color": "dim"})
        threading.Thread(target=self._load_nodes_bg, args=(cached_nodes, silent), daemon=True).start()

    def _apply_k8s_nodes(self, nodes: list[dict]) -> bool:
        self._last_k8s_nodes = nodes
        previous_names = set(self.node_states)
        current_names = {node["name"] for node in nodes}

        for removed_name in previous_names - current_names:
            self.node_states.pop(removed_name, None)
            self._host_signal_refresh_at.pop(removed_name, None)
            self._node_detail_cache.pop(removed_name, None)
            self._node_metrics_cache.pop(removed_name, None)

        for node in nodes:
            name = node["name"]
            hostname = node.get("hostname", name)
            if name not in self.node_states:
                self.node_states[name] = NodeState(k8s_name=name, hypervisor=hostname)
            state = self.node_states[name]
            state.k8s_ready = node.get("ready", True)
            state.k8s_cordoned = node.get("cordoned", False)
            state.k8s_taints = list(node.get("taints", []))
            state.kernel_version = node.get("kernel_version")
            ready_since = node.get("ready_since")
            if ready_since is not None:
                state.uptime = format_uptime(ready_since)

        return previous_names != current_names

    def _push_inventory_state(self, force_full_state: bool = False) -> None:
        if force_full_state:
            self._push({"type": "full_state", "nodes": {name: _serialise(state) for name, state in self.node_states.items()}})
            return
        for node_name, state in self.node_states.items():
            self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})

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

    def _load_nodes_bg(self, cached_nodes: list[dict] | None = None, silent: bool = False) -> None:
        with measure_latency("node_list_refresh"):
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
            for node in nodes:
                state = self.node_states.get(node["name"])
                if state:
                    state.node_agent_ready = node["name"] in ready_node_agents

            def _os_log(message: str) -> None:
                self._push({"type": "log", "node": "-", "message": message, "color": "dim"})

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

            self._etcd_node_names = k8s_ops.get_etcd_node_names(auth=self.k8s_auth)

            for node in nodes:
                name = node["name"]
                hostname = node.get("hostname", name)
                summary = _lookup_host_summary(summaries, hostname, name)
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
