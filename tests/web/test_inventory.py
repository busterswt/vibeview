from __future__ import annotations

from draino.models import NodeState
from draino.web import server as web_server


def test_refresh_marks_missing_node_agents(monkeypatch, tmp_path):
    server = web_server.DrainoServer(
        k8s_auth=None,
        openstack_auth=None,
        role_names=["admin"],
        audit_log=str(tmp_path / "audit.log"),
    )
    nodes = [
        {"name": "node-a", "hostname": "hv-a", "ready": True, "cordoned": False, "taints": [], "kernel_version": None},
        {"name": "node-b", "hostname": "hv-b", "ready": True, "cordoned": False, "taints": [], "kernel_version": None},
    ]

    monkeypatch.setattr(web_server.node_agent_client, "get_ready_node_names", lambda: {"node-a"})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(web_server.k8s_ops, "get_node_host_signals", lambda node_name, hostname=None: {"kernel_version": None, "latest_kernel_version": None, "reboot_required": False})
    monkeypatch.setattr(web_server.k8s_ops, "get_ovn_edge_nodes", lambda auth=None: set())
    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(server, "_push", lambda message: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert server.node_states["node-a"].node_agent_ready is True
    assert server.node_states["node-b"].node_agent_ready is False


def test_reboot_request_requires_admin_role(tmp_path):
    server = web_server.DrainoServer(
        role_names=["member"],
        audit_log=str(tmp_path / "audit.log"),
    )
    state = NodeState(k8s_name="node-1", hypervisor="hv-1")
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append

    server.action_reboot_request("node-1")

    assert pushed == [{
        "type": "log",
        "node": "node-1",
        "message": "Reboot requires the OpenStack 'admin' role.",
        "color": "warn",
    }]


def test_reboot_request_requires_node_to_be_drained(tmp_path):
    server = web_server.DrainoServer(
        role_names=["admin"],
        audit_log=str(tmp_path / "audit.log"),
    )
    state = NodeState(
        k8s_name="node-1",
        hypervisor="hv-1",
        is_compute=True,
        k8s_cordoned=True,
        compute_status="disabled",
        vm_count=2,
        amphora_count=0,
    )
    server.node_states["node-1"] = state

    pushed: list[dict] = []
    server._push = pushed.append

    server.action_reboot_request("node-1")

    assert pushed == [{
        "type": "log",
        "node": "node-1",
        "message": "Compute node must be drained of VMs and pods before reboot.",
        "color": "warn",
    }]


def test_load_nodes_bg_skips_host_signals_during_silent_refresh_until_ttl(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    signal_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: signal_calls.append((node_name, hostname)) or {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(server, "_push", lambda msg: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=False)
    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert signal_calls == [("node-1", "hv-1")]


def test_load_nodes_bg_refreshes_host_signals_again_after_ttl(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    signal_calls: list[tuple[str, str]] = []
    now_values = iter([1000.0, 1000.0 + web_server._HOST_SIGNALS_TTL + 1])

    monkeypatch.setattr(web_server.time, "time", lambda: next(now_values))
    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: signal_calls.append((node_name, hostname)) or {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(server, "_push", lambda msg: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)
    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert signal_calls == [("node-1", "hv-1"), ("node-1", "hv-1")]


def test_load_nodes_bg_uses_state_updates_when_membership_is_unchanged(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    server.node_states["node-1"] = NodeState(k8s_name="node-1", hypervisor="hv-1")
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    pushed: list[dict] = []

    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(web_server.k8s_ops, "get_mariadb_node_names", lambda auth=None: {"node-1"})
    monkeypatch.setattr(web_server.k8s_ops, "get_ovn_edge_nodes", lambda auth=None: {"node-1"})
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(server, "_push", pushed.append)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert [msg["type"] for msg in pushed] == ["state_update", "state_update"]
    assert all(msg["node"] == "node-1" for msg in pushed)
    assert server.node_states["node-1"].is_edge is True
    assert server.node_states["node-1"].hosts_mariadb is True


def test_load_nodes_bg_uses_full_state_when_membership_changes(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    server.node_states["node-old"] = NodeState(k8s_name="node-old", hypervisor="hv-old")
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    pushed: list[dict] = []

    monkeypatch.setattr(web_server.openstack_ops, "get_all_host_summaries", lambda log_cb=None, auth=None: {})
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(server, "_push", pushed.append)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert [msg["type"] for msg in pushed] == ["full_state", "full_state"]
    assert "node-old" not in server.node_states
    assert "node-1" in server.node_states


def test_load_nodes_bg_reuses_cached_openstack_summaries_on_silent_refresh(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    summary_calls: list[str] = []
    now_values = iter([1000.0, 1005.0])

    monkeypatch.setattr(web_server.time, "time", lambda: next(now_values))
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_all_host_summaries",
        lambda log_cb=None, auth=None: summary_calls.append("called") or {"hv-1": {"is_compute": True, "compute_status": "up", "vm_count": 0, "amphora_count": 0}},
    )
    monkeypatch.setattr(server, "_push", lambda msg: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)
    server._load_nodes_bg(cached_nodes=nodes, silent=True)

    assert summary_calls == ["called"]


def test_load_nodes_bg_manual_refresh_bypasses_cached_openstack_summaries(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    nodes = [{"name": "node-1", "hostname": "hv-1", "ready": True, "cordoned": False}]

    summary_calls: list[str] = []
    now_values = iter([1000.0, 1005.0])

    monkeypatch.setattr(web_server.time, "time", lambda: next(now_values))
    monkeypatch.setattr(web_server.k8s_ops, "get_etcd_node_names", lambda auth=None: set())
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_host_signals",
        lambda node_name, hostname=None: {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
        },
    )
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_all_host_summaries",
        lambda log_cb=None, auth=None: summary_calls.append("called") or {"hv-1": {"is_compute": True, "compute_status": "up", "vm_count": 0, "amphora_count": 0}},
    )
    monkeypatch.setattr(server, "_push", lambda msg: None)

    server._load_nodes_bg(cached_nodes=nodes, silent=True)
    server._load_nodes_bg(cached_nodes=nodes, silent=False)

    assert summary_calls == ["called", "called"]
