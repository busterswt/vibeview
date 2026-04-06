"""Reboot backend helpers."""
from __future__ import annotations

import json
import os
import secrets
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException

from .models import NodePhase, NodeState

LogFn = Callable[[str], None]


@dataclass(slots=True)
class RebootBackendConfig:
    mode: str = "ssh"
    agent_namespace: str = "default"
    agent_service_name: str = "draino-node-agent"
    agent_label_selector: str = ""
    agent_port: int = 8443
    agent_ca_file: str = ""
    agent_token_file: str = ""
    agent_request_timeout: float = 10.0


def load_backend_config_from_env() -> RebootBackendConfig:
    return RebootBackendConfig(
        mode=os.getenv("DRAINO_REBOOT_BACKEND", "ssh").strip().lower() or "ssh",
        agent_namespace=os.getenv("DRAINO_NODE_AGENT_NAMESPACE", "default"),
        agent_service_name=os.getenv("DRAINO_NODE_AGENT_SERVICE_NAME", "draino-node-agent"),
        agent_label_selector=os.getenv("DRAINO_NODE_AGENT_LABEL_SELECTOR", ""),
        agent_port=int(os.getenv("DRAINO_NODE_AGENT_PORT", "8443")),
        agent_ca_file=os.getenv("DRAINO_NODE_AGENT_CA_FILE", ""),
        agent_token_file=os.getenv("DRAINO_NODE_AGENT_TOKEN_FILE", ""),
        agent_request_timeout=float(os.getenv("DRAINO_NODE_AGENT_TIMEOUT", "10")),
    )


def is_ready_for_reboot(state: NodeState) -> tuple[bool, str]:
    if state.phase in (NodePhase.RUNNING, NodePhase.REBOOTING, NodePhase.UNDRAINING):
        return False, "Cannot reboot while an operation is in progress."

    if not state.k8s_cordoned:
        return False, "Node must be cordoned and drained before reboot."

    if not state.is_compute:
        return True, ""

    if state.compute_status != "disabled":
        return False, "Nova compute service must be disabled before reboot."

    if state.vm_count is None or state.amphora_count is None:
        return False, "VM evacuation state is unknown; complete evacuation before reboot."

    if state.vm_count != 0 or state.amphora_count != 0:
        return False, "Compute node must be drained of VMs and pods before reboot."

    return True, ""


def issue_reboot(state: NodeState, log: LogFn, config: RebootBackendConfig | None = None) -> None:
    cfg = config or load_backend_config_from_env()
    if cfg.mode == "node-agent":
        _issue_node_agent_reboot(state, log, cfg)
        return
    _issue_ssh_reboot(state, log)


def _issue_ssh_reboot(state: NodeState, log: LogFn) -> None:
    try:
        subprocess.run(
            [
                "ssh",
                "-o", "ConnectTimeout=10",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                state.hypervisor,
                "sudo", "reboot",
            ],
            timeout=15,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        pass
    log(f"Reboot command sent to '{state.hypervisor}' via SSH")


def _issue_node_agent_reboot(
    state: NodeState,
    log: LogFn,
    config: RebootBackendConfig,
) -> None:
    if not config.agent_token_file or not config.agent_ca_file:
        raise RuntimeError("node-agent backend is not configured with token/CA files")

    pod_dns = _discover_agent_pod_dns(state.k8s_name, config)
    token = _read_secret_file(config.agent_token_file)
    request_id = secrets.token_hex(8)
    payload = json.dumps(
        {
            "request_id": request_id,
            "expected_node": state.k8s_name,
            "hypervisor": state.hypervisor,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://{pod_dns}:{config.agent_port}/reboot",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    ssl_context = ssl.create_default_context(cafile=config.agent_ca_file)

    try:
        with urllib.request.urlopen(
            request,
            timeout=config.agent_request_timeout,
            context=ssl_context,
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"node-agent reboot request failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"node-agent reboot request failed: {exc.reason}") from exc

    if not body.get("accepted"):
        raise RuntimeError("node-agent rejected the reboot request")
    log(f"Reboot command accepted by node-agent for '{state.k8s_name}'")


def _discover_agent_pod_dns(node_name: str, backend_config: RebootBackendConfig) -> str:
    try:
        config.load_incluster_config()
    except ConfigException as exc:
        raise RuntimeError("node-agent backend requires in-cluster Kubernetes access") from exc

    core = client.CoreV1Api()
    pods = core.list_namespaced_pod(
        namespace=backend_config.agent_namespace,
        label_selector=backend_config.agent_label_selector,
        field_selector=f"spec.nodeName={node_name}",
    )

    for pod in pods.items:
        if pod.status.phase != "Running":
            continue
        conditions = pod.status.conditions or []
        ready = any(cond.type == "Ready" and cond.status == "True" for cond in conditions)
        if ready and pod.metadata and pod.metadata.name:
            return (
                f"{pod.metadata.name}."
                f"{backend_config.agent_service_name}."
                f"{backend_config.agent_namespace}.svc"
            )

    raise RuntimeError(f"no ready node-agent pod found for node '{node_name}'")


def _read_secret_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().strip()
