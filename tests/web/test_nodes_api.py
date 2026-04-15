from __future__ import annotations

import time

from fastapi.testclient import TestClient
from types import SimpleNamespace

from draino.models import NodeState
from draino.operations import k8s_ops
from draino.web import server as web_server
from draino.web.api import nodes as nodes_api


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


def test_network_detail_endpoint_times_out(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(nodes_api, "_RESOURCE_DETAIL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(web_server, "_get_network_detail", lambda network_id, auth=None: time.sleep(0.05) or {})

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
        resp = client.get("/api/networks/net-1")

    assert resp.status_code == 200
    assert "Timed out after 0s while loading network details" in resp.json()["error"]


def test_node_irq_balance_endpoint_returns_agent_data(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_irq_balance",
        lambda node_name, hostname=None: {
            "interfaces": [{"name": "bond0", "top_cpu": "CPU7", "risk": "medium"}],
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

        resp = client.get("/api/nodes/node-a/irq-balance")

    assert resp.status_code == 200
    assert resp.json()["interfaces"][0]["name"] == "bond0"


def test_node_sar_trends_endpoint_returns_agent_data(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_sar_trends",
        lambda node_name, hostname=None: {
            "summary": {"window_minutes": 15, "cpu_busy_avg": 12.5},
            "interfaces": [{"name": "bond0", "rxdrop": 0.1, "txdrop": 0.0, "rxerr": 0.0, "txerr": 0.0}],
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

        resp = client.get("/api/nodes/node-a/sar-trends")

    assert resp.status_code == 200
    assert resp.json()["summary"]["cpu_busy_avg"] == 12.5


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
                "mac_address": "fa:16:3e:00:00:01",
                "network_id": "net-1",
                "network_name": "tenant-net",
                "fixed_ips": ["10.0.0.12"],
                "dhcp_enabled": True,
                "gateway_target": "router-a",
                "allowed_address_pairs": [{"ip_address": "10.0.0.50", "mac_address": "fa:16:3e:00:00:50"}],
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
    assert body["instance"]["ports"][0]["dhcp_enabled"] is True
    assert body["instance"]["ports"][0]["gateway_target"] == "router-a"


def test_node_instance_port_stats_endpoint_returns_node_agent_data(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_instance_port_stats",
        lambda node_name, port_ids, auth=None, hostname=None: {
            "ports": [{"port_id": "port-1", "interface_name": "tap123", "rx_bytes_per_second": 1234.0, "tx_bytes_per_second": 5678.0}],
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

        resp = client.post("/api/nodes/node-a/instance-port-stats", json={"port_ids": ["port-1"]})

    assert resp.status_code == 200
    assert resp.json()["ports"][0]["port_id"] == "port-1"


def test_node_detail_endpoint_returns_api_issue_on_nova_failure(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    class FakeExc(Exception):
        status_code = 500
        request_id = "req-nova-1"

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(web_server.k8s_ops, "get_node_k8s_detail", lambda node_name, auth=None: {"node": node_name})
    monkeypatch.setattr(web_server.k8s_ops, "get_node_hardware_info", lambda node_name, hostname=None: {"hostname": hostname or node_name})
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: (_ for _ in ()).throw(FakeExc("nova internal server error")),
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

        resp = client.get("/api/nodes/node-a/detail")

    body = resp.json()
    assert resp.status_code == 200
    assert body["api_issue"]["service"] == "Nova"
    assert body["api_issue"]["status"] == 500
    assert body["api_issue"]["request_id"] == "req-nova-1"


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


def test_hypervisor_detail_falls_back_to_placement_inventory(monkeypatch):
    class FakeHypervisor:
        id = "hv-1"

        @staticmethod
        def to_dict():
            return {
                "hypervisor_hostname": "hv-a01.example.com",
                "state": "down",
                "status": "disabled",
            }

    class FakeCompute:
        @staticmethod
        def hypervisors(hypervisor_hostname_pattern=None):
            assert hypervisor_hostname_pattern == "hv-a01.example.com"
            return [FakeHypervisor()]

        @staticmethod
        def get_hypervisor(hypervisor_id):
            assert hypervisor_id == "hv-1"
            return FakeHypervisor()

    class FakeInventory:
        def __init__(self, resource_class, total):
            self.resource_class = resource_class
            self.total = total

    class FakeProvider:
        id = "rp-1"

    class FakePlacement:
        @staticmethod
        def find_resource_provider(name_or_id, ignore_missing=True):
            assert name_or_id == "hv-a01.example.com"
            return FakeProvider()

        @staticmethod
        def resource_provider_inventories(provider):
            assert provider.id == "rp-1"
            return [
                FakeInventory("VCPU", 96),
                FakeInventory("MEMORY_MB", 524288),
            ]

    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"usages": {"VCPU": 72, "MEMORY_MB": 430080}}

    class FakeSession:
        @staticmethod
        def get(url, headers=None):
            assert url.endswith("/resource_providers/rp-1/usages")
            assert headers == {"OpenStack-API-Version": "placement 1.9"}
            return FakeResponse()

    class FakeConn:
        compute = FakeCompute()
        placement = FakePlacement()
        session = FakeSession()

        @staticmethod
        def endpoint_for(service_type):
            assert service_type == "placement"
            return "https://placement.example/v1"

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    detail = web_server.openstack_ops.get_hypervisor_detail("hv-a01.example.com")

    assert detail["vcpus"] == 96
    assert detail["vcpus_used"] == 72
    assert detail["memory_mb"] == 524288
    assert detail["memory_mb_used"] == 430080


def test_instance_network_detail_falls_back_to_flavor_name_lookup(monkeypatch):
    class FakeServer:
        id = "vm-1"
        name = "vm-1"
        status = "ACTIVE"
        image = {"id": "img-1"}
        flavor = {"name": "m1.small"}

        def to_dict(self):
            return {"OS-EXT-AZ:availability_zone": "nova"}

    class FakeCompute:
        @staticmethod
        def get_server(instance_id):
            assert instance_id == "vm-1"
            return FakeServer()

        @staticmethod
        def get_flavor(flavor_id):
            raise AssertionError("get_flavor should not be used without a flavor id")

        @staticmethod
        def find_flavor(flavor_name, ignore_missing=True):
            assert flavor_name == "m1.small"
            assert ignore_missing is True
            return SimpleNamespace(
                id="d5e8faba-aa99-473d-8105-24707e37fa67",
                name="m1.small",
                vcpus=4,
                ram=2048,
                disk=18,
                ephemeral=0,
                swap=0,
            )

    class FakeNetwork:
        @staticmethod
        def ports(device_id=None, network_id=None):
            assert device_id == "vm-1"
            return []

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    detail = web_server.openstack_ops.get_instance_network_detail("vm-1")

    assert detail["flavor"]["id"] == "d5e8faba-aa99-473d-8105-24707e37fa67"
    assert detail["flavor"]["name"] == "m1.small"
    assert detail["flavor"]["vcpus"] == 4
    assert detail["flavor"]["ram_mb"] == 2048
    assert detail["flavor"]["disk_gb"] == 18


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


def test_get_instances_preflight_falls_back_to_flavor_name_lookup(monkeypatch):
    web_server.openstack_ops._flavor_cache.clear()

    class FakeServer:
        def __init__(self, instance_id, name, flavor_name):
            self.id = instance_id
            self.name = name
            self.status = "ACTIVE"
            self.image = {"id": "img-1"}
            self.flavor = {"name": flavor_name}

        def to_dict(self):
            return {}

    class FakeCompute:
        get_flavor_calls = []
        find_flavor_calls = []

        @classmethod
        def get_flavor(cls, flavor_id):
            cls.get_flavor_calls.append(flavor_id)
            raise AssertionError("get_flavor should not be called without a flavor id")

        @classmethod
        def find_flavor(cls, flavor_name, ignore_missing=True):
            cls.find_flavor_calls.append((flavor_name, ignore_missing))
            return SimpleNamespace(
                id="d5e8faba-aa99-473d-8105-24707e37fa67",
                name=flavor_name,
                vcpus=4,
                ram=2048,
            )

    class FakeConn:
        compute = FakeCompute()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(
        web_server.openstack_ops,
        "_servers_on_host",
        lambda conn, hypervisor: [
            FakeServer("vm-1", "vm-1", "m1.small"),
            FakeServer("vm-2", "vm-2", "m1.small"),
        ],
    )

    items = web_server.openstack_ops.get_instances_preflight("hv-a")

    assert items[0]["vcpus"] == 4
    assert items[0]["ram_mb"] == 2048
    assert items[1]["vcpus"] == 4
    assert items[1]["ram_mb"] == 2048
    assert FakeCompute.get_flavor_calls == []
    assert FakeCompute.find_flavor_calls == [("m1.small", True)]


def test_flavor_cache_is_reused_between_preflight_and_detail(monkeypatch):
    web_server.openstack_ops._flavor_cache.clear()

    class FakeServer:
        id = "vm-1"
        name = "vm-1"
        status = "ACTIVE"
        image = {"id": "img-1"}
        flavor = {"name": "m1.small"}

        def to_dict(self):
            return {}

    class FakeCompute:
        find_flavor_calls = []

        @classmethod
        def get_server(cls, instance_id):
            assert instance_id == "vm-1"
            return FakeServer()

        @classmethod
        def get_flavor(cls, flavor_id):
            raise AssertionError("get_flavor should not be called without a flavor id")

        @classmethod
        def find_flavor(cls, flavor_name, ignore_missing=True):
            cls.find_flavor_calls.append((flavor_name, ignore_missing))
            return SimpleNamespace(
                id="d5e8faba-aa99-473d-8105-24707e37fa67",
                name=flavor_name,
                vcpus=4,
                ram=2048,
                disk=18,
                ephemeral=0,
                swap=0,
            )

    class FakeNetwork:
        @staticmethod
        def ports(device_id=None, network_id=None):
            if device_id == "vm-1":
                return []
            raise AssertionError((device_id, network_id))

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(
        web_server.openstack_ops,
        "_servers_on_host",
        lambda conn, hypervisor: [FakeServer()],
    )

    items = web_server.openstack_ops.get_instances_preflight("hv-a")
    detail = web_server.openstack_ops.get_instance_network_detail("vm-1")

    assert items[0]["vcpus"] == 4
    assert detail["flavor"]["ram_mb"] == 2048
    assert FakeCompute.find_flavor_calls == [("m1.small", True)]


def test_instance_network_detail_includes_dhcp_flag_from_subnet(monkeypatch):
    class FakeServer:
        id = "vm-1"
        name = "vm-1"
        status = "ACTIVE"
        image = {"id": "img-1"}
        flavor = {"id": "flavor-1", "name": "m1.large"}

        def to_dict(self):
            return {}

    class FakePort:
        id = "port-1"
        name = "port-1"
        status = "ACTIVE"
        is_admin_state_up = True
        mac_address = "fa:16:3e:00:00:01"
        network_id = "net-1"
        fixed_ips = [{"ip_address": "10.0.0.12", "subnet_id": "subnet-1"}]
        allowed_address_pairs = []
        security_group_ids = ["default"]
        device_owner = "compute:nova"

        def to_dict(self):
            return {"binding:vnic_type": "normal"}

    class FakeNetwork:
        @staticmethod
        def ports(device_id=None):
            assert device_id == "vm-1"
            return [FakePort()]

        @staticmethod
        def get_network(network_id):
            assert network_id == "net-1"
            return SimpleNamespace(name="tenant-net")

        @staticmethod
        def get_subnet(subnet_id):
            assert subnet_id == "subnet-1"
            return SimpleNamespace(is_dhcp_enabled=True)

        @staticmethod
        def ips(port_id=None):
            assert port_id == "port-1"
            return [SimpleNamespace(floating_ip_address="203.0.113.10")]

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
                ephemeral=0,
                swap=0,
            )

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    detail = web_server.openstack_ops.get_instance_network_detail("vm-1")

    assert detail["ports"][0]["dhcp_enabled"] is True
    assert detail["ports"][0]["floating_ips"] == ["203.0.113.10"]


def test_instance_network_detail_resolves_gateway_router_name(monkeypatch):
    class FakeServer:
        id = "vm-1"
        name = "vm-1"
        status = "ACTIVE"
        image = {"id": "img-1"}
        flavor = {"id": "flavor-1", "name": "m1.large"}

        def to_dict(self):
            return {}

    class FakeVmPort:
        id = "port-1"
        name = "port-1"
        status = "ACTIVE"
        is_admin_state_up = True
        mac_address = "fa:16:3e:00:00:01"
        network_id = "net-1"
        fixed_ips = [{"ip_address": "10.0.0.12", "subnet_id": "subnet-1"}]
        allowed_address_pairs = []
        security_group_ids = ["default"]
        device_owner = "compute:nova"
        device_id = "vm-1"

        def to_dict(self):
            return {"binding:vnic_type": "normal"}

    class FakeGatewayPort:
        id = "gw-port-1"
        name = "gw-port-1"
        status = "ACTIVE"
        is_admin_state_up = True
        mac_address = "fa:16:3e:00:00:fe"
        network_id = "net-1"
        fixed_ips = [{"ip_address": "10.0.0.1", "subnet_id": "subnet-1"}]
        allowed_address_pairs = []
        security_group_ids = []
        device_owner = "network:router_interface"
        device_id = "router-1"

        def to_dict(self):
            return {}

    class FakeNetwork:
        @staticmethod
        def ports(device_id=None, network_id=None):
            if device_id == "vm-1":
                return [FakeVmPort()]
            if network_id == "net-1":
                return [FakeVmPort(), FakeGatewayPort()]
            raise AssertionError((device_id, network_id))

        @staticmethod
        def get_network(network_id):
            assert network_id == "net-1"
            return SimpleNamespace(name="tenant-net")

        @staticmethod
        def get_subnet(subnet_id):
            assert subnet_id == "subnet-1"
            return SimpleNamespace(is_dhcp_enabled=True, gateway_ip="10.0.0.1")

        @staticmethod
        def ips(port_id=None):
            assert port_id == "port-1"
            return []

        @staticmethod
        def get_router(router_id):
            assert router_id == "router-1"
            return SimpleNamespace(name="router-a")

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
                ephemeral=0,
                swap=0,
            )

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    detail = web_server.openstack_ops.get_instance_network_detail("vm-1")

    assert detail["ports"][0]["gateway_target"] == "router-a"


def test_instance_network_detail_resolves_gateway_device_id(monkeypatch):
    class FakeServer:
        id = "vm-1"
        name = "vm-1"
        status = "ACTIVE"
        image = {"id": "img-1"}
        flavor = {"id": "flavor-1", "name": "m1.large"}

        def to_dict(self):
            return {}

    class FakeVmPort:
        id = "port-1"
        name = "port-1"
        status = "ACTIVE"
        is_admin_state_up = True
        mac_address = "fa:16:3e:00:00:01"
        network_id = "net-1"
        fixed_ips = [{"ip_address": "10.0.0.12", "subnet_id": "subnet-1"}]
        allowed_address_pairs = []
        security_group_ids = ["default"]
        device_owner = "compute:nova"
        device_id = "vm-1"

        def to_dict(self):
            return {"binding:vnic_type": "normal"}

    class FakeGatewayPort:
        id = "gw-port-1"
        name = "gw-port-1"
        status = "ACTIVE"
        is_admin_state_up = True
        mac_address = "fa:16:3e:00:00:fe"
        network_id = "net-1"
        fixed_ips = [{"ip_address": "10.0.0.1", "subnet_id": "subnet-1"}]
        allowed_address_pairs = []
        security_group_ids = []
        device_owner = "compute:nova"
        device_id = "vm-gateway"

        def to_dict(self):
            return {}

    class FakeNetwork:
        @staticmethod
        def ports(device_id=None, network_id=None):
            if device_id == "vm-1":
                return [FakeVmPort()]
            if network_id == "net-1":
                return [FakeVmPort(), FakeGatewayPort()]
            raise AssertionError((device_id, network_id))

        @staticmethod
        def get_network(network_id):
            assert network_id == "net-1"
            return SimpleNamespace(name="tenant-net")

        @staticmethod
        def get_subnet(subnet_id):
            assert subnet_id == "subnet-1"
            return SimpleNamespace(is_dhcp_enabled=True, gateway_ip="10.0.0.1")

        @staticmethod
        def ips(port_id=None):
            assert port_id == "port-1"
            return []

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
                ephemeral=0,
                swap=0,
            )

    class FakeConn:
        compute = FakeCompute()
        network = FakeNetwork()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    detail = web_server.openstack_ops.get_instance_network_detail("vm-1")

    assert detail["ports"][0]["gateway_target"] == "vm-gateway"


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
