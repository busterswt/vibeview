from __future__ import annotations

from fastapi.testclient import TestClient
from types import SimpleNamespace

from draino.models import NodeState
from draino.operations import k8s_ops
from draino.web import server as web_server


def test_node_detail_endpoint_uses_cache_and_refresh_bypass(monkeypatch):
    captured: dict[str, int] = {"k8s": 0, "hw": 0, "nova": 0}

    initial_nodes = [{"name": "node-a", "hostname": "hv-a", "ready": True, "cordoned": False}]

    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: initial_nodes)
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_k8s_detail",
        lambda node_name, auth=None: captured.__setitem__("k8s", captured["k8s"] + 1) or {"node": node_name},
    )
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_hardware_info",
        lambda node_name, hostname=None: captured.__setitem__("hw", captured["hw"] + 1) or {"hostname": hostname or node_name},
    )
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: captured.__setitem__("nova", captured["nova"] + 1) or {"hypervisor": hypervisor},
    )

    payload = {
        "kubernetes": {
            "server": "https://cluster.example:6443",
            "token": "token-1",
            "skip_tls_verify": False,
        },
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200

        record = next(iter(web_server._sessions._sessions.values()))
        record.server.node_states["node-a"] = NodeState(k8s_name="node-a", hypervisor="hv-a", is_compute=True)

        first = client.get("/api/nodes/node-a/detail")
        second = client.get("/api/nodes/node-a/detail")
        refreshed = client.get("/api/nodes/node-a/detail?refresh=1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert refreshed.status_code == 200
    assert captured == {"k8s": 2, "hw": 2, "nova": 2}


def test_node_metrics_endpoint_caches_and_refreshes(monkeypatch):
    captured = {"metrics": 0}

    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_monitor_metrics",
        lambda node_name, hostname=None: captured.__setitem__("metrics", captured["metrics"] + 1) or {
            "current": {"load1": 1.5, "filesystems": [{"mount": "/", "available_kb": 1000, "used_percent": 70}]},
            "history": [{"timestamp": 1, "load1": 1.5, "memory_used_percent": 60.0, "root_used_percent": 70}],
            "error": None,
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200

        record = next(iter(web_server._sessions._sessions.values()))
        record.server.node_states["node-a"] = NodeState(k8s_name="node-a", hypervisor="hv-a", is_compute=True)

        first = client.get("/api/nodes/node-a/metrics")
        second = client.get("/api/nodes/node-a/metrics")
        refreshed = client.get("/api/nodes/node-a/metrics?refresh=1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert refreshed.status_code == 200
    assert captured == {"metrics": 2}


def test_node_network_stats_endpoint_returns_agent_data(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_network_stats",
        lambda node_name, hostname=None: {
            "interfaces": [{"name": "bond0", "rx_bytes_per_second": 125000000.0, "tx_bytes_per_second": 62500000.0}],
            "error": None,
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200

        record = next(iter(web_server._sessions._sessions.values()))
        record.server.node_states["node-a"] = NodeState(k8s_name="node-a", hypervisor="hv-a", is_compute=True)

        resp = client.get("/api/nodes/node-a/network-stats")

    assert resp.status_code == 200
    assert resp.json()["interfaces"][0]["name"] == "bond0"


def test_node_instance_detail_endpoint_returns_ports_flavor_and_ovn(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_instance_network_detail",
        lambda instance_id, auth=None: {
            "id": instance_id,
            "name": "vm-1",
            "status": "ACTIVE",
            "compute_host": "hv-a",
            "flavor": {"name": "m1.large", "vcpus": 4, "ram_mb": 8192, "disk_gb": 40, "ephemeral_gb": 0, "swap_mb": 0},
            "ports": [{
                "id": "port-1",
                "network_id": "net-1",
                "network_name": "tenant-net",
                "fixed_ips": ["10.0.0.12"],
                "security_groups": ["default"],
                "floating_ips": ["203.0.113.10"],
            }],
        },
    )
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_ovn_port_logical_switch",
        lambda port_id, network_id, auth=None: {
            "ls_name": "neutron-net-1",
            "ls_uuid": "ls-uuid",
            "port": {"id": port_id, "type": "", "up": True, "enabled": True, "router_port": "", "addresses": ["fa:16:3e:00:00:01 10.0.0.12"]},
        },
    )

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200

        record = next(iter(web_server._sessions._sessions.values()))
        record.server.node_states["node-a"] = NodeState(k8s_name="node-a", hypervisor="hv-a", is_compute=True)

        resp = client.get("/api/nodes/node-a/instances/vm-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["instance"]["flavor"]["name"] == "m1.large"
    assert body["instance"]["ports"][0]["network_name"] == "tenant-net"
    assert body["instance"]["ports"][0]["ovn"]["ls_name"] == "neutron-net-1"


def test_instance_network_detail_reads_flavor_from_object_reference(monkeypatch):
    class FakeServer:
        id = "vm-1"
        name = "vm-1"
        status = "ACTIVE"
        image = {"id": "img-1"}
        flavor = SimpleNamespace(id="flavor-1", original_name="m1.large")

        def to_dict(self):
            return {"OS-EXT-AZ:availability_zone": "nova"}

    class FakeCompute:
        @staticmethod
        def get_server(instance_id):
            assert instance_id == "vm-1"
            return FakeServer()

        @staticmethod
        def get_flavor(flavor_id):
            assert flavor_id == "flavor-1"
            return SimpleNamespace(
                id="flavor-1",
                name="m1.large",
                vcpus=4,
                ram=8192,
                disk=40,
                ephemeral=20,
                swap=0,
            )

    class FakeNetwork:
        @staticmethod
        def ports(device_id=None):
            assert device_id == "vm-1"
            return []

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    detail = web_server.openstack_ops.get_instance_network_detail("vm-1")

    assert detail["flavor"]["name"] == "m1.large"
    assert detail["flavor"]["vcpus"] == 4
    assert detail["flavor"]["ram_mb"] == 8192
    assert detail["flavor"]["disk_gb"] == 40
    assert detail["flavor"]["ephemeral_gb"] == 20


def test_get_instances_preflight_includes_flavor_sizing(monkeypatch):
    class FakeServer:
        def __init__(self, instance_id, name, flavor_id):
            self.id = instance_id
            self.name = name
            self.status = "ACTIVE"
            self.image = {"id": "img-1"}
            self.flavor = SimpleNamespace(id=flavor_id, original_name=f"{flavor_id}-name")

    class FakeCompute:
        calls = []

        @classmethod
        def get_flavor(cls, flavor_id):
            cls.calls.append(flavor_id)
            return SimpleNamespace(
                id=flavor_id,
                name=f"{flavor_id}-name",
                vcpus=4 if flavor_id == "flavor-1" else 8,
                ram=8192 if flavor_id == "flavor-1" else 16384,
            )

    class FakeConn:
        compute = FakeCompute()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(
        web_server.openstack_ops,
        "_servers_on_host",
        lambda conn, hypervisor: [
            FakeServer("vm-1", "vm-1", "flavor-1"),
            FakeServer("vm-2", "vm-2", "flavor-1"),
            FakeServer("vm-3", "vm-3", "flavor-2"),
        ],
    )

    items = web_server.openstack_ops.get_instances_preflight("hv-a")

    assert items[0]["vcpus"] == 4
    assert items[0]["ram_mb"] == 8192
    assert items[2]["vcpus"] == 8
    assert items[2]["ram_mb"] == 16384
    assert FakeCompute.calls == ["flavor-1", "flavor-2"]


def test_patch_managed_noschedule_taint_endpoint(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

    def fake_set_managed_noschedule_taint(node_name, enabled, auth=None):
        captured["node_name"] = node_name
        captured["enabled"] = enabled
        captured["auth"] = auth

    monkeypatch.setattr(web_server.k8s_ops, "set_managed_noschedule_taint", fake_set_managed_noschedule_taint)

    payload = {
        "kubernetes": {"server": "https://cluster.example:6443", "token": "token-1", "skip_tls_verify": False},
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200

        record = next(iter(web_server._sessions._sessions.values()))
        resp = client.post("/api/nodes/node-a/taints/noschedule", json={"enabled": True})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "error": None}
    assert captured["node_name"] == "node-a"
    assert captured["enabled"] is True
    assert captured["auth"] == record.server.k8s_auth


def test_websocket_requires_session_and_uses_session_server(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

    payload = {
        "kubernetes": {
            "server": "https://cluster.example:6443",
            "token": "token-1",
            "skip_tls_verify": False,
        },
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200

        record = next(iter(web_server._sessions._sessions.values()))
        record.server.node_states["node-1"] = NodeState(k8s_name="node-1", hypervisor="hv-1")

        with client.websocket_connect("/ws") as ws:
            message = ws.receive_json()

    assert message["type"] == "full_state"
    assert "node-1" in message["nodes"]


def test_set_managed_noschedule_taint_preserves_unrelated_taints(monkeypatch):
    captured: dict[str, object] = {}

    class FakeTaint:
        def __init__(self, key: str, value: str | None, effect: str):
            self.key = key
            self.value = value
            self.effect = effect

        def to_dict(self):
            return {"key": self.key, "value": self.value, "effect": self.effect}

    class FakeSpec:
        taints = [
            FakeTaint("custom", "value", "NoSchedule"),
            FakeTaint(k8s_ops.MANAGED_NOSCHEDULE_TAINT_KEY, "true", "NoSchedule"),
            FakeTaint("other", None, "PreferNoSchedule"),
        ]

    class FakeNode:
        spec = FakeSpec()

    class FakeCoreV1Api:
        def __init__(self, api_client):
            captured["api_client"] = api_client

        def read_node(self, name: str):
            captured["read_name"] = name
            return FakeNode()

        def patch_node(self, name: str, body: dict):
            captured["patch_name"] = name
            captured["body"] = body

    monkeypatch.setattr(k8s_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_ops.client, "CoreV1Api", FakeCoreV1Api)

    k8s_ops.set_managed_noschedule_taint("node-a", enabled=False)

    assert captured["read_name"] == "node-a"
    assert captured["patch_name"] == "node-a"
    assert captured["body"] == {
        "spec": {
            "taints": [
                {"key": "custom", "value": "value", "effect": "NoSchedule"},
                {"key": "other", "value": None, "effect": "PreferNoSchedule"},
            ]
        }
    }
