"""HTTPS node-local reboot agent."""
from __future__ import annotations

from pathlib import Path

import uvicorn

from .node_agent_app import (
    agent_status,
    healthz,
    host_detail,
    host_etcd_status,
    host_metrics,
    host_network_interfaces,
    host_network_stats,
    host_signals,
    node_agent_app,
    reboot,
    readyz,
)
from .node_agent_common import (
    RebootRequest,
    _LOGGER,
    _authorise,
    _env,
    _node_name,
    _read_token,
    _reboot_host,
    _run_host_shell,
)
from .node_agent_host_ops import (
    _HOST_STATIC_DETAIL_TTL,
    _get_cached_static_host_detail,
    _get_dynamic_host_detail,
    _get_etcd_status,
    _get_host_detail,
    _get_host_signals,
    _get_network_interfaces,
    _get_static_host_detail,
)
from .node_agent_metrics_ops import (
    _HOST_METRICS_HISTORY_LIMIT,
    _HOST_METRICS_TTL,
    _get_host_metrics,
    _get_host_network_stats,
)


def run(host: str = "0.0.0.0", port: int = 8443) -> None:
    cert_file = _env("DRAINO_NODE_AGENT_TLS_CERT_FILE")
    key_file = _env("DRAINO_NODE_AGENT_TLS_KEY_FILE")
    token_file = _env("DRAINO_NODE_AGENT_TOKEN_FILE")
    for path in (cert_file, key_file, token_file):
        if not Path(path).exists():
            raise RuntimeError(f"required file does not exist: {path}")
    _LOGGER.info("node agent starting node=%s host=%s port=%s", _node_name(), host, port)
    uvicorn.run(
        node_agent_app,
        host=host,
        port=port,
        log_level="warning",
        ssl_certfile=cert_file,
        ssl_keyfile=key_file,
    )
