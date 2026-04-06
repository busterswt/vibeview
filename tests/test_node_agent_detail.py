from __future__ import annotations

import json

from draino import node_agent_client
from draino.operations import k8s_ops


def test_get_node_hardware_info_uses_node_agent(monkeypatch):
    monkeypatch.setattr(k8s_ops.node_agent_client, "enabled", lambda: True)
    monkeypatch.setattr(
        k8s_ops.node_agent_client,
        "get_host_detail",
        lambda node_name: {
            "hostname": node_name,
            "architecture": "x86_64",
            "kernel_version": "6.8.0",
            "uptime": "3 days, 2 hours",
            "vendor": "Dell Inc.",
            "product": "PowerEdge",
            "bios_version": "1.2.3",
            "cpu_model": "Xeon",
            "cpu_sockets": 2,
            "cpu_cores_per_socket": 16,
            "cpu_threads_per_core": 2,
            "ram_type": "DDR5",
            "ram_speed": "4800 MT/s",
            "ram_total_gb": 512,
            "ram_slots_used": 16,
            "ram_manufacturer": "Samsung",
            "error": None,
        },
    )

    result = k8s_ops.get_node_hardware_info("node-1", "hv-1")

    assert result["hostname"] == "node-1"
    assert result["architecture"] == "x86_64"
    assert result["uptime"] == "3 days, 2 hours"
    assert result["vendor"] == "Dell Inc."


def test_get_network_interfaces_uses_node_agent(monkeypatch):
    monkeypatch.setattr(k8s_ops.node_agent_client, "enabled", lambda: True)
    monkeypatch.setattr(
        k8s_ops.node_agent_client,
        "get_network_interfaces",
        lambda node_name: {
            "interfaces": [{"name": "bond0", "type": "bond", "members": ["eth0", "eth1"]}],
            "error": None,
        },
    )

    result = k8s_ops.get_node_network_interfaces("node-1", "hv-1")

    assert result["error"] is None
    assert result["interfaces"][0]["name"] == "bond0"


def test_check_etcd_service_uses_node_agent(monkeypatch):
    monkeypatch.setattr(k8s_ops.node_agent_client, "enabled", lambda: True)
    monkeypatch.setattr(
        k8s_ops.node_agent_client,
        "get_etcd_status",
        lambda node_name: {"active": True, "error": None},
    )

    assert k8s_ops.check_etcd_service("node-1", "hv-1") is True


def test_node_agent_client_uses_pod_ip_and_disables_hostname_check(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    ca_file = tmp_path / "ca.crt"
    token_file.write_text("secret-token", encoding="utf-8")
    ca_file.write_text("ca", encoding="utf-8")

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        node_agent_client,
        "_discover_agent_pod_host",
        lambda node_name, cfg: "10.0.0.42",
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"active": True, "error": None}).encode("utf-8")

    class FakeSSLContext:
        def __init__(self):
            self.check_hostname = True

    ssl_ctx = FakeSSLContext()

    def fake_urlopen(request, timeout=None, context=None):
        captured["url"] = request.full_url
        captured["auth"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        captured["context"] = context
        return FakeResponse()

    monkeypatch.setattr(node_agent_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(node_agent_client.ssl, "create_default_context", lambda cafile=None: ssl_ctx)

    result = node_agent_client._request_json(
        "node-1",
        "GET",
        "/host/etcd",
        agent_config=node_agent_client.NodeAgentConfig(
            namespace="draino",
            service_name="draino-node-agent",
            label_selector="app=node-agent",
            port=8443,
            ca_file=str(ca_file),
            token_file=str(token_file),
            request_timeout=5.0,
        ),
    )

    assert result["active"] is True
    assert captured["url"] == "https://10.0.0.42:8443/host/etcd"
    assert captured["auth"] == "Bearer secret-token"
    assert captured["timeout"] == 5.0
    assert captured["context"] is ssl_ctx
    assert ssl_ctx.check_hostname is False
