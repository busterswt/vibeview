"""Internal client for talking to the node-local reboot agent."""
from __future__ import annotations

import json
import os
import secrets
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException


@dataclass(slots=True)
class NodeAgentConfig:
    namespace: str = "default"
    service_name: str = "draino-node-agent"
    label_selector: str = ""
    port: int = 8443
    ca_file: str = ""
    token_file: str = ""
    request_timeout: float = 10.0


def enabled() -> bool:
    return (os.getenv("DRAINO_REBOOT_BACKEND", "ssh").strip().lower() or "ssh") == "node-agent"


def load_config_from_env() -> NodeAgentConfig:
    return NodeAgentConfig(
        namespace=os.getenv("DRAINO_NODE_AGENT_NAMESPACE", "default"),
        service_name=os.getenv("DRAINO_NODE_AGENT_SERVICE_NAME", "draino-node-agent"),
        label_selector=os.getenv("DRAINO_NODE_AGENT_LABEL_SELECTOR", ""),
        port=int(os.getenv("DRAINO_NODE_AGENT_PORT", "8443")),
        ca_file=os.getenv("DRAINO_NODE_AGENT_CA_FILE", ""),
        token_file=os.getenv("DRAINO_NODE_AGENT_TOKEN_FILE", ""),
        request_timeout=float(os.getenv("DRAINO_NODE_AGENT_TIMEOUT", "10")),
    )


def post_reboot(node_name: str, hypervisor: str) -> dict:
    return _request_json(
        node_name,
        "POST",
        "/reboot",
        {
            "request_id": secrets.token_hex(8),
            "expected_node": node_name,
            "hypervisor": hypervisor,
        },
    )


def get_host_detail(node_name: str) -> dict:
    return _request_json(node_name, "GET", "/host/detail")


def get_network_interfaces(node_name: str) -> dict:
    return _request_json(node_name, "GET", "/host/network-interfaces")


def get_etcd_status(node_name: str) -> dict:
    return _request_json(node_name, "GET", "/host/etcd")


def _request_json(
    node_name: str,
    method: str,
    path: str,
    payload: dict | None = None,
    agent_config: NodeAgentConfig | None = None,
) -> dict:
    config_data = agent_config or load_config_from_env()
    if not config_data.token_file or not config_data.ca_file:
        raise RuntimeError("node-agent client is not configured with token/CA files")

    pod_host = _discover_agent_pod_host(node_name, config_data)
    token = _read_secret_file(config_data.token_file)
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://{pod_host}:{config_data.port}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    ssl_context = ssl.create_default_context(cafile=config_data.ca_file)
    ssl_context.check_hostname = False

    try:
        with urllib.request.urlopen(
            request,
            timeout=config_data.request_timeout,
            context=ssl_context,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"node-agent request failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"node-agent request failed: {exc.reason}") from exc


def _discover_agent_pod_host(node_name: str, agent_config: NodeAgentConfig) -> str:
    try:
        config.load_incluster_config()
    except ConfigException as exc:
        raise RuntimeError("node-agent client requires in-cluster Kubernetes access") from exc

    core = client.CoreV1Api()
    pods = core.list_namespaced_pod(
        namespace=agent_config.namespace,
        label_selector=agent_config.label_selector,
        field_selector=f"spec.nodeName={node_name}",
    )

    for pod in pods.items:
        if pod.status.phase != "Running":
            continue
        conditions = pod.status.conditions or []
        ready = any(cond.type == "Ready" and cond.status == "True" for cond in conditions)
        if ready and pod.status and pod.status.pod_ip:
            return pod.status.pod_ip

    raise RuntimeError(f"no ready node-agent pod found for node '{node_name}'")


def _read_secret_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().strip()
