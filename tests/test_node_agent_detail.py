from __future__ import annotations

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
