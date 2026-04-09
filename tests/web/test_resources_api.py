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
