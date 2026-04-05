from __future__ import annotations

from fastapi.testclient import TestClient

from draino.models import NodeState
from draino.operations.k8s_ops import K8sAuth
from draino.operations.openstack_ops import OpenStackAuth
from draino.web import server as web_server


def test_session_endpoint_reports_unauthenticated():
    with TestClient(web_server.fastapi_app) as client:
        resp = client.get("/api/session")

    assert resp.status_code == 200
    assert resp.json() == {"authenticated": False}


def test_login_creates_session_and_gates_api(monkeypatch):
    captured: dict[str, object] = {}

    def fake_get_nodes(auth=None):
        captured["login_k8s_auth"] = auth
        return []

    class FakeConn:
        def authorize(self):
            captured["authorized"] = True

    def fake_conn(auth=None):
        captured["login_os_auth"] = auth
        return FakeConn()

    def fake_refresh(self):
        captured["refreshed"] = True

    def fake_list_namespaces(auth=None):
        captured["api_k8s_auth"] = auth
        return [{"name": "default", "status": "Active", "created": None, "labels": {}}]

    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", fake_get_nodes)
    monkeypatch.setattr(web_server.openstack_ops, "_conn", fake_conn)
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", fake_refresh)
    monkeypatch.setattr(web_server.k8s_ops, "list_k8s_namespaces", fake_list_namespaces)

    payload = {
        "kubernetes": {
            "server": "https://cluster.example:6443",
            "token": "token-1",
            "skip_tls_verify": True,
        },
        "openstack": {
            "auth_url": "https://keystone.example/v3",
            "username": "ops-user",
            "password": "secret",
            "project_name": "admin",
            "user_domain_name": "Default",
            "project_domain_name": "Default",
            "region_name": "RegionOne",
            "interface": "public",
        },
    }

    with TestClient(web_server.fastapi_app) as client:
        unauthorized = client.get("/api/k8s/namespaces")
        assert unauthorized.status_code == 401

        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        assert login.json() == {"ok": True}
        assert captured["refreshed"] is True
        assert isinstance(captured["login_k8s_auth"], K8sAuth)
        assert isinstance(captured["login_os_auth"], OpenStackAuth)
        assert captured["authorized"] is True

        session = client.get("/api/session")
        assert session.status_code == 200
        assert session.json()["authenticated"] is True
        assert session.json()["username"] == "ops-user"
        assert session.json()["project_name"] == "admin"

        namespaces = client.get("/api/k8s/namespaces")
        assert namespaces.status_code == 200
        assert namespaces.json()["items"][0]["name"] == "default"
        assert captured["api_k8s_auth"] == captured["login_k8s_auth"]


def test_websocket_requires_session_and_uses_session_server(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

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
