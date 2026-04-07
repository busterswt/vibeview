"""Internal client for talking to the node-local reboot agent."""
from __future__ import annotations

import json
import os
import secrets
import ssl
import threading
import time
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
    endpoint_ttl: float = 30.0


_endpoint_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


def enabled() -> bool:
    return True


def load_config_from_env() -> NodeAgentConfig:
    return NodeAgentConfig(
        namespace=os.getenv("DRAINO_NODE_AGENT_NAMESPACE", "default"),
        service_name=os.getenv("DRAINO_NODE_AGENT_SERVICE_NAME", "draino-node-agent"),
        label_selector=os.getenv("DRAINO_NODE_AGENT_LABEL_SELECTOR", ""),
        port=int(os.getenv("DRAINO_NODE_AGENT_PORT", "8443")),
        ca_file=os.getenv("DRAINO_NODE_AGENT_CA_FILE", ""),
        token_file=os.getenv("DRAINO_NODE_AGENT_TOKEN_FILE", ""),
        request_timeout=float(os.getenv("DRAINO_NODE_AGENT_TIMEOUT", "10")),
        endpoint_ttl=float(os.getenv("DRAINO_NODE_AGENT_ENDPOINT_TTL", "30")),
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


def get_host_signals(node_name: str) -> dict:
    return _request_json(node_name, "GET", "/host/signals")


def get_host_metrics(node_name: str) -> dict:
    return _request_json(node_name, "GET", "/host/metrics")


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
        _invalidate_cached_agent_pod_host(node_name)
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"node-agent request failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        _invalidate_cached_agent_pod_host(node_name)
        raise RuntimeError(f"node-agent request failed: {exc.reason}") from exc


def _discover_agent_pod_host(node_name: str, agent_config: NodeAgentConfig) -> str:
    cached = _get_cached_agent_pod_host(node_name, agent_config)
    if cached:
        return cached

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
            _set_cached_agent_pod_host(node_name, pod.status.pod_ip, agent_config)
            return pod.status.pod_ip

    raise RuntimeError(f"no ready node-agent pod found for node '{node_name}'")


def _get_cached_agent_pod_host(node_name: str, agent_config: NodeAgentConfig) -> str | None:
    now = time.time()
    with _cache_lock:
        cached = _endpoint_cache.get(node_name)
        if not cached:
            return None
        pod_host, expires_at = cached
        if now >= expires_at:
            _endpoint_cache.pop(node_name, None)
            return None
        return pod_host


def _set_cached_agent_pod_host(node_name: str, pod_host: str, agent_config: NodeAgentConfig) -> None:
    with _cache_lock:
        _endpoint_cache[node_name] = (pod_host, time.time() + agent_config.endpoint_ttl)


def _invalidate_cached_agent_pod_host(node_name: str) -> None:
    with _cache_lock:
        _endpoint_cache.pop(node_name, None)


def _read_secret_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().strip()
