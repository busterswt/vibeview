"""FastAPI app wiring for the node agent."""
from __future__ import annotations

import threading

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from . import node_agent_common
from .node_agent_common import RebootRequest
from .node_agent_host_ops import (
    _get_etcd_status,
    _get_host_detail,
    _get_host_signals,
    _get_network_interfaces,
)
from .node_agent_metrics_ops import (
    _get_host_instance_port_stats,
    _get_host_irq_balance,
    _get_host_metrics,
    _get_host_network_stats,
    _get_named_interface_stats,
)

node_agent_app = FastAPI(title="VibeView Node Agent")


class InterfaceStatsRequest(BaseModel):
    interfaces: list[str]


@node_agent_app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@node_agent_app.get("/readyz")
def readyz() -> dict[str, str]:
    return {"status": "ready"}


@node_agent_app.get("/status")
def agent_status(authorization: str | None = Header(default=None)) -> dict[str, object]:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info("status requested node=%s", node_agent_common._node_name())
    return {
        "node": node_agent_common._node_name(),
        "reboot_in_progress": node_agent_common._reboot_in_progress,
    }


@node_agent_app.get("/host/detail")
def host_detail(authorization: str | None = Header(default=None)) -> dict:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info("host detail requested node=%s", node_agent_common._node_name())
    return _get_host_detail()


@node_agent_app.get("/host/network-interfaces")
def host_network_interfaces(authorization: str | None = Header(default=None)) -> dict:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info("network interfaces requested node=%s", node_agent_common._node_name())
    return _get_network_interfaces()


@node_agent_app.get("/host/etcd")
def host_etcd_status(authorization: str | None = Header(default=None)) -> dict:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info("etcd status requested node=%s", node_agent_common._node_name())
    return _get_etcd_status()


@node_agent_app.get("/host/signals")
def host_signals(authorization: str | None = Header(default=None)) -> dict:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info("host signals requested node=%s", node_agent_common._node_name())
    return _get_host_signals()


@node_agent_app.get("/host/metrics")
def host_metrics(authorization: str | None = Header(default=None)) -> dict:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info("host metrics requested node=%s", node_agent_common._node_name())
    return _get_host_metrics()


@node_agent_app.get("/host/network-stats")
def host_network_stats(authorization: str | None = Header(default=None)) -> dict:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info("host network stats requested node=%s", node_agent_common._node_name())
    return _get_host_network_stats()


@node_agent_app.get("/host/irq-balance")
def host_irq_balance(authorization: str | None = Header(default=None)) -> dict:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info("host irq balance requested node=%s", node_agent_common._node_name())
    return _get_host_irq_balance()


@node_agent_app.get("/host/instance-port-stats")
def host_instance_port_stats(authorization: str | None = Header(default=None)) -> dict:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info("instance port stats requested node=%s", node_agent_common._node_name())
    return _get_host_instance_port_stats()


@node_agent_app.post("/host/interface-stats")
def host_interface_stats(payload: InterfaceStatsRequest, authorization: str | None = Header(default=None)) -> dict:
    node_agent_common._authorise(authorization)
    node_agent_common._LOGGER.info(
        "named interface stats requested node=%s count=%s",
        node_agent_common._node_name(),
        len(payload.interfaces or []),
    )
    return _get_named_interface_stats(payload.interfaces or [])


@node_agent_app.post("/reboot", status_code=status.HTTP_202_ACCEPTED)
def reboot(
    payload: RebootRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    node_agent_common._authorise(authorization)

    node_name = node_agent_common._node_name()
    if payload.expected_node and payload.expected_node != node_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"request targeted '{payload.expected_node}' but this agent serves '{node_name}'",
        )

    with node_agent_common._state_lock:
        if node_agent_common._reboot_in_progress:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="reboot already in progress",
            )
        node_agent_common._reboot_in_progress = True

    node_agent_common._LOGGER.info("reboot accepted node=%s request_id=%s", node_name, payload.request_id)
    threading.Thread(target=node_agent_common._reboot_host, daemon=True).start()
    return {"accepted": True, "node": node_name, "request_id": payload.request_id}
