from __future__ import annotations

import time

from fastapi.testclient import TestClient

from draino.web import server as web_server
from draino.web.api import resources as resource_api


def test_networks_endpoint_returns_api_issue_on_neutron_failure(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    class FakeExc(Exception):
        status_code = 504
        request_id = "req-neutron-1"

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(resource_api, "get_networks", lambda auth=None: (_ for _ in ()).throw(FakeExc("upstream request timeout")))

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
        resp = client.get("/api/networks")

    body = resp.json()
    assert resp.status_code == 200
    assert body["api_issue"]["service"] == "Neutron"
    assert body["api_issue"]["status"] == 504
    assert body["api_issue"]["request_id"] == "req-neutron-1"


def test_networks_endpoint_reads_request_id_from_response_headers(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    class FakeResponse:
        headers = {"X-Openstack-Request-Id": "req-neutron-header-1"}

    class FakeExc(Exception):
        status_code = 500
        response = FakeResponse()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(resource_api, "get_networks", lambda auth=None: (_ for _ in ()).throw(FakeExc("neutron internal error")))

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
        resp = client.get("/api/networks")

    body = resp.json()
    assert resp.status_code == 200
    assert body["api_issue"]["request_id"] == "req-neutron-header-1"


def test_repair_metadata_port_endpoint_returns_created_port(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "repair_subnet_metadata_port",
        lambda network_id, subnet_id, auth=None: {
            "port_id": "port-1",
            "ip_address": "10.0.0.2",
            "status": "ok",
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
        resp = client.post("/api/networks/net-1/subnets/subnet-1/repair-metadata-port")

    body = resp.json()
    assert resp.status_code == 200
    assert body["metadata_port"]["status"] == "ok"
    assert body["metadata_port"]["port_id"] == "port-1"


def test_load_balancers_endpoint_returns_items(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_load_balancers",
        lambda auth=None: [{
            "id": "lb-1",
            "name": "public-lb",
            "operating_status": "ONLINE",
            "provisioning_status": "ACTIVE",
            "vip_address": "10.10.0.5",
            "floating_ip": "198.51.100.25",
            "vip_port_id": "vip-port-1",
            "project_id": "proj-1",
            "listener_count": 1,
            "pool_count": 1,
            "amphora_count": 2,
        }],
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
        resp = client.get("/api/load-balancers")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["load_balancers"][0]["floating_ip"] == "198.51.100.25"
    assert body["load_balancers"][0]["amphora_count"] == 2


def test_volume_snapshot_and_backup_endpoints_return_items(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(resource_api, "get_volume_snapshots", lambda auth=None: [{
        "id": "snap-1",
        "name": "snap-db",
        "status": "available",
        "size_gb": 120,
        "volume_id": "vol-1",
        "project_id": "proj-1",
        "created_at": "2026-04-18T02:00:00Z",
    }])
    monkeypatch.setattr(resource_api, "get_volume_backups", lambda auth=None: [{
        "id": "backup-1",
        "name": "backup-db",
        "status": "available",
        "size_gb": 120,
        "volume_id": "vol-1",
        "project_id": "proj-1",
        "created_at": "2026-04-18T03:00:00Z",
        "is_incremental": False,
        "container": "",
    }])

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
        snapshot_resp = client.get("/api/volume-snapshots")
        backup_resp = client.get("/api/volume-backups")

    assert snapshot_resp.status_code == 200
    assert snapshot_resp.json()["snapshots"][0]["id"] == "snap-1"
    assert backup_resp.status_code == 200
    assert backup_resp.json()["backups"][0]["id"] == "backup-1"


def test_volume_retype_endpoint_requests_action(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "retype_volume",
        lambda volume_id, target_type, migration_policy="on-demand", auth=None: {
            "volume_id": volume_id,
            "target_type": target_type,
            "migration_policy": migration_policy,
            "status": "requested",
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
        resp = client.post("/api/volumes/vol-1/retype", json={"target_type": "gold-backend-b", "migration_policy": "on-demand"})

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["result"]["volume_id"] == "vol-1"
    assert body["result"]["target_type"] == "gold-backend-b"


def test_projects_endpoints_return_inventory(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_projects",
        lambda auth=None, search="": [{
            "project_id": "proj-1",
            "project_name": "production",
            "instance_count": 4,
            "network_count": 2,
            "volume_count": 5,
            "security_group_count": 3,
            "floating_ip_count": 1,
            "load_balancer_count": 1,
            "host_count": 2,
            "top_host": "cmp-12",
            "top_host_pct": 50.0,
        }],
    )
    monkeypatch.setattr(
        resource_api,
        "get_project_inventory",
        lambda project_id, auth=None, section="instances": {
            "summary": {"project_id": project_id, "project_name": "production"},
            "instances": [{"id": "vm-1", "name": "api-01"}],
            "networks": [{"id": "net-1", "name": "prod-api"}],
            "volumes": [{"id": "vol-1", "name": "rootdisk"}],
            "security_groups": [{"id": "sg-1", "name": "web"}],
            "floating_ips": [{"id": "fip-1", "floating_ip_address": "198.51.100.10"}],
            "load_balancers": [{"id": "lb-1", "name": "public-lb"}],
            "quotas": {"compute": {"instances": {"used": 4, "limit": 20}}},
            "all_projects": True,
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
        projects_resp = client.get("/api/projects")
        inventory_resp = client.get("/api/projects/proj-1/inventory")

    assert projects_resp.status_code == 200
    assert projects_resp.json()["projects"][0]["project_name"] == "production"
    assert inventory_resp.status_code == 200
    assert inventory_resp.json()["inventory"]["instances"][0]["id"] == "vm-1"
    assert inventory_resp.json()["inventory"]["quotas"]["compute"]["instances"]["limit"] == 20


def test_project_inventory_endpoint_forwards_section(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    captured = {}
    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_project_inventory",
        lambda project_id, auth=None, section="instances": captured.update({"project_id": project_id, "section": section}) or {"summary": {"project_id": project_id}},
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
        resp = client.get("/api/projects/proj-1/inventory?section=networks")

    assert resp.status_code == 200
    assert captured == {"project_id": "proj-1", "section": "networks"}


def test_projects_endpoint_forwards_search(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    captured = {}

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_projects",
        lambda auth=None, search="": captured.update({"search": search}) or [],
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
        resp = client.get("/api/projects?search=prod")

    assert resp.status_code == 200
    assert captured["search"] == "prod"


def test_instance_detail_endpoint_returns_enriched_ports(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api.openstack_ops,
        "get_instance_network_detail",
        lambda instance_id, auth=None: {
            "id": instance_id,
            "name": "api-01",
            "compute_host": "cmp-12",
            "ports": [{
                "id": "port-1",
                "network_id": "net-1",
                "fixed_ips": ["10.0.0.5"],
                "floating_ips": ["198.51.100.10"],
            }],
        },
    )
    monkeypatch.setattr(
        resource_api.k8s_ops,
        "get_ovn_port_logical_switch",
        lambda port_id, network_id, auth=None: {"ls_name": "neutron-net-1", "port": {"type": "normal"}},
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
        resp = client.get("/api/instances/vm-1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["instance"]["id"] == "vm-1"
    assert body["instance"]["ports"][0]["ovn"]["ls_name"] == "neutron-net-1"


def test_load_balancer_detail_endpoint_returns_detail(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_load_balancer_detail",
        lambda lb_id, auth=None: {
            "id": lb_id,
            "name": "public-lb",
            "operating_status": "ONLINE",
            "provisioning_status": "ACTIVE",
            "vip_address": "10.10.0.5",
            "floating_ip": "198.51.100.25",
            "vip_port_id": "vip-port-1",
            "vip_subnet_id": "subnet-1",
            "vip_port": {
                "id": "vip-port-1",
                "name": "octavia-lb-vip",
                "status": "ACTIVE",
                "network_id": "net-1",
                "subnet_id": "subnet-1",
                "ip_address": "10.10.0.5",
                "mac_address": "fa:16:3e:11:22:33",
                "device_owner": "Octavia",
                "device_id": "lb-1",
                "project_id": "proj-1",
                "admin_state_up": True,
            },
            "project_id": "proj-1",
            "flavor_id": "amphora-small",
            "listeners": [{"id": "listener-1", "name": "https"}],
            "pools": [{"id": "pool-1", "name": "web-pool"}],
            "amphorae": [{"id": "amp-1", "role": "MASTER"}],
            "distinct_host_count": 2,
            "ha_summary": "HA spread OK",
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
        resp = client.get("/api/load-balancers/lb-1")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["load_balancer"]["id"] == "lb-1"
    assert body["load_balancer"]["vip_port"]["id"] == "vip-port-1"
    assert body["load_balancer"]["vip_port"]["mac_address"] == "fa:16:3e:11:22:33"
    assert body["load_balancer"]["ha_summary"] == "HA spread OK"
    assert body["load_balancer"]["pools"][0]["id"] == "pool-1"


def test_load_balancer_detail_endpoint_times_out(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(resource_api, "_RESOURCE_DETAIL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(resource_api, "get_load_balancer_detail", lambda lb_id, auth=None: time.sleep(0.05) or {})

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
        resp = client.get("/api/load-balancers/lb-1")

    assert resp.status_code == 200
    assert "Timed out after 0s while loading load balancer details" in resp.json()["error"]


def test_security_groups_endpoint_returns_items(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_security_groups",
        lambda auth=None: [{
            "id": "sg-1",
            "name": "allow-all",
            "project_id": "proj-1",
            "project_name": "production",
            "rule_count": 2,
            "flagged_rule_count": 1,
            "attachment_port_count": 3,
            "attachment_instance_count": 2,
            "audit": {
                "severity": "critical",
                "score": 100,
                "findings": [{"severity": "critical", "summary": "any:any 0.0.0.0/0", "count": 1}],
                "has_open_world_ingress": True,
                "has_any_any_open_world": True,
                "has_unused": False,
            },
        }],
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
        resp = client.get("/api/security-groups")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["security_groups"][0]["audit"]["severity"] == "critical"
    assert body["security_groups"][0]["project_name"] == "production"


def test_security_group_detail_endpoint_returns_detail(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        resource_api,
        "get_security_group_detail",
        lambda group_id, auth=None: {
            "id": group_id,
            "name": "web-frontend",
            "project_id": "proj-1",
            "project_name": "production",
            "rule_count": 2,
            "flagged_rule_count": 1,
            "attachment_port_count": 1,
            "attachment_instance_count": 1,
            "audit": {
                "severity": "high",
                "score": 50,
                "findings": [{"severity": "high", "summary": "tcp:22 0.0.0.0/0", "count": 1}],
                "has_open_world_ingress": True,
                "has_any_any_open_world": False,
                "has_unused": False,
            },
            "rules": [{
                "id": "rule-1",
                "direction": "ingress",
                "protocol": "tcp",
                "port_range": "22",
                "remote_ip_prefix": "0.0.0.0/0",
                "audit": {"flagged": True, "severity": "high", "summary": "tcp:22 0.0.0.0/0"},
            }],
            "attachments": [{
                "port_id": "port-1",
                "device_owner": "compute:nova",
                "device_id": "server-1",
            }],
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
        resp = client.get("/api/security-groups/sg-1")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["security_group"]["id"] == "sg-1"
    assert body["security_group"]["rules"][0]["audit"]["summary"] == "tcp:22 0.0.0.0/0"
    assert body["security_group"]["attachments"][0]["device_id"] == "server-1"
