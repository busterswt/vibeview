"""Inventory state management for the web UI."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Set

from fastapi import WebSocket

from ..audit import AuditLogger
from ..models import NodeState
from ..operations import k8s_ops, openstack_ops
from .inventory_actions import InventoryActionsMixin
from .inventory_refresh import (
    _HOST_SIGNALS_TTL,
    InventoryRefreshMixin,
)
from .serialise import serialise_state as _serialise

_LOGGER = logging.getLogger("draino.web")


class DrainoServer(InventoryRefreshMixin, InventoryActionsMixin):
    """Holds runtime state and coordinates workers with WebSocket clients."""

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
            "type": "full_state",
            "nodes": {name: _serialise(state) for name, state in self.node_states.items()},
        }))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    def _push(self, msg: dict) -> None:
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
                self._push({"type": "state_update", "node": node_name, "data": _serialise(state)})
        return cb

    def _log_cb(self, node_name: str, color: str = "cyan"):
        def cb(msg: str) -> None:
            self._push({"type": "log", "node": node_name, "message": msg, "color": color})
        return cb

    def _audit_cb(self, action: str, node_name: str):
        def cb(event: str, detail: str = "") -> None:
            self._audit.log(action, node_name, event, detail)
        return cb

    def cache_stats(self) -> dict:
        return {
            "node_count": len(self.node_states),
            "node_detail_entries": len(self._node_detail_cache),
            "node_metrics_entries": len(self._node_metrics_cache),
            "host_signal_entries": len(self._host_signal_refresh_at),
            "has_openstack_summary_cache": self._openstack_summary_cache is not None,
            "has_ovn_edge_cache": self._ovn_edge_cache is not None,
            "has_mariadb_node_cache": self._mariadb_node_cache is not None,
            "client_count": len(self._clients),
        }

    def clear_runtime_caches(self) -> dict:
        cleared = {
            "node_detail_entries": len(self._node_detail_cache),
            "node_metrics_entries": len(self._node_metrics_cache),
            "host_signal_entries": len(self._host_signal_refresh_at),
            "openstack_summary_cache": 1 if self._openstack_summary_cache is not None else 0,
            "ovn_edge_cache": 1 if self._ovn_edge_cache is not None else 0,
            "mariadb_node_cache": 1 if self._mariadb_node_cache is not None else 0,
        }
        self._node_detail_cache.clear()
        self._node_metrics_cache.clear()
        self._host_signal_refresh_at.clear()
        self._openstack_summary_cache = None
        self._ovn_edge_cache = None
        self._mariadb_node_cache = None
        return cleared
