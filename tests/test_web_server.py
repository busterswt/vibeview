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

    initial_nodes = [{"name": "node-a", "hostname": "hv-a", "ready": True, "cordoned": False}]

    def fake_get_nodes(auth=None):
        captured["login_k8s_auth"] = auth
        return initial_nodes

    class FakeConn:
        def authorize(self):
            captured["authorized"] = True

    def fake_conn(auth=None):
        captured["login_os_auth"] = auth
        return FakeConn()

    def fake_refresh(self, cached_nodes=None):
        captured["refreshed"] = True
        captured["cached_nodes"] = cached_nodes

    def fake_list_namespaces(auth=None):
        captured["api_k8s_auth"] = auth
        return [{"name": "default", "status": "Active", "created": None, "labels": {}}]

    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", fake_get_nodes)
    monkeypatch.setattr(web_server.openstack_ops, "_conn", fake_conn)
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["member", "admin"])
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
        assert captured["cached_nodes"] == initial_nodes
        assert isinstance(captured["login_k8s_auth"], K8sAuth)
        assert isinstance(captured["login_os_auth"], OpenStackAuth)
        assert captured["authorized"] is True

        session = client.get("/api/session")
        assert session.status_code == 200
        assert session.json()["authenticated"] is True
        assert session.json()["username"] == "ops-user"
        assert session.json()["project_name"] == "admin"
        assert session.json()["is_admin"] is True
        assert session.json()["role_names"] == ["member", "admin"]

        namespaces = client.get("/api/k8s/namespaces")
        assert namespaces.status_code == 200
        assert namespaces.json()["items"][0]["name"] == "default"
        assert captured["api_k8s_auth"] == captured["login_k8s_auth"]


def test_websocket_requires_session_and_uses_session_server(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None: None)
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
