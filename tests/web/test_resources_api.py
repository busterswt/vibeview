from __future__ import annotations

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
    assert body["load_balancer"]["ha_summary"] == "HA spread OK"
    assert body["load_balancer"]["pools"][0]["id"] == "pool-1"
