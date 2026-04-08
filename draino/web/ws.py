"""WebSocket handlers for the web UI."""
from __future__ import annotations

import json
import threading
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..operations import k8s_ops
from .latency import measure_latency

router = APIRouter()

_get_ws_session_getter: Callable[[], Callable[[WebSocket], object | None]] | None = None


def configure(*, get_ws_session: Callable[[], Callable[[WebSocket], object | None]]) -> None:
    global _get_ws_session_getter
    _get_ws_session_getter = get_ws_session


def _require_ws_session() -> Callable[[WebSocket], object | None]:
    if _get_ws_session_getter is None:
        raise RuntimeError("websocket routes are not configured")
    return _get_ws_session_getter()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    session = _require_ws_session()(ws)
    if session is None:
        await ws.close(code=4401)
        return
    server = session.server
    await server.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            action = msg.get("action")
            node = msg.get("node")

            if action == "refresh":
                server.start_refresh()
            elif action == "refresh_silent":
                server.start_refresh(silent=True)
            elif action == "evacuate" and node:
                server.action_evacuate(node)
            elif action == "drain_quick" and node:
                server.action_drain_quick(node)
            elif action == "undrain" and node:
                server.action_undrain(node)
            elif action == "reboot_request" and node:
                server.action_reboot_request(node)
            elif action == "reboot_confirm" and node:
                server.action_reboot_confirm(node)
            elif action == "reboot_cancel" and node:
                server.action_reboot_cancel(node)
            elif action == "check_etcd":
                server.start_etcd_check()
            elif action == "get_preflight" and node:
                server.start_preflight(node)
            elif action == "refresh_preflight" and node:
                server.start_preflight(node, silent=True)
            elif action == "migrate_instance" and node:
                instance_id = msg.get("instance_id")
                if instance_id:
                    server.action_migrate_instance(node, instance_id)
            elif action == "get_pods" and node:
                threading.Thread(target=_serve_pods, args=(server, node), daemon=True).start()
    except WebSocketDisconnect:
        server.disconnect(ws)


def _serve_pods(server, node_name: str) -> None:
    try:
        with measure_latency("pods_list"):
            raw_pods = k8s_ops.get_pods_on_node(node_name, auth=server.k8s_auth)
            pods = []
            for pod in raw_pods:
                payload = dict(pod)
                created_at = payload.get("created_at")
                if created_at is not None and hasattr(created_at, "isoformat"):
                    payload["created_at"] = created_at.isoformat()
                pods.append(payload)
    except Exception as exc:
        pods = [{"error": str(exc)}]
    server._push({"type": "pods", "node": node_name, "pods": pods})
