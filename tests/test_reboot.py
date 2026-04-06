from __future__ import annotations

import json

from draino import reboot
from draino.models import NodeState


def test_compute_node_requires_full_evacuation_before_reboot():
    state = NodeState(
        k8s_name="node-1",
        hypervisor="hv-1",
        is_compute=True,
        k8s_cordoned=True,
        compute_status="disabled",
        vm_count=1,
        amphora_count=0,
    )

    ready, detail = reboot.is_ready_for_reboot(state)

    assert ready is False
    assert "drained of VMs and pods" in detail


def test_non_compute_node_can_reboot_after_cordon():
    state = NodeState(
        k8s_name="node-2",
        hypervisor="hv-2",
        is_compute=False,
        k8s_cordoned=True,
    )

    ready, detail = reboot.is_ready_for_reboot(state)

    assert ready is True
    assert detail == ""


def test_issue_reboot_uses_node_agent(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    ca_file = tmp_path / "ca.crt"
    token_file.write_text("secret-token", encoding="utf-8")
    ca_file.write_text("unused", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_discover(node_name: str, cfg: reboot.RebootBackendConfig) -> str:
        captured["node_name"] = node_name
        captured["selector"] = cfg.agent_label_selector
        return "agent-0.draino-node-agent.default.svc"

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"accepted": True}).encode("utf-8")

    def fake_urlopen(request, timeout=None, context=None):
        captured["url"] = request.full_url
        captured["auth"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        captured["context"] = context
        return FakeResponse()

    monkeypatch.setattr(reboot, "_discover_agent_pod_dns", fake_discover)
    monkeypatch.setattr(reboot.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(reboot.ssl, "create_default_context", lambda cafile=None: {"cafile": cafile})

    state = NodeState(k8s_name="node-1", hypervisor="hv-1")
    cfg = reboot.RebootBackendConfig(
        mode="node-agent",
        agent_namespace="default",
        agent_service_name="draino-node-agent",
        agent_label_selector="app.kubernetes.io/component=node-agent",
        agent_port=8443,
        agent_ca_file=str(ca_file),
        agent_token_file=str(token_file),
        agent_request_timeout=9.0,
    )

    log_messages: list[str] = []
    reboot.issue_reboot(state, log_messages.append, cfg)

    assert captured["node_name"] == "node-1"
    assert captured["url"] == "https://agent-0.draino-node-agent.default.svc:8443/reboot"
    assert captured["auth"] == "Bearer secret-token"
    assert captured["timeout"] == 9.0
    assert captured["context"] == {"cafile": str(ca_file)}
    assert "accepted by node-agent" in log_messages[0]
