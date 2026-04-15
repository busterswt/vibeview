from __future__ import annotations

from fastapi.testclient import TestClient

from draino.operations.k8s_ops import K8sAuth
from draino.operations.openstack_ops import OpenStackAuth
from draino.web import server as web_server


def test_session_endpoint_reports_unauthenticated():
    with TestClient(web_server.fastapi_app) as client:
        resp = client.get("/api/session")

    assert resp.status_code == 200
    assert resp.json() == {"authenticated": False}


def test_root_serves_login_when_unauthenticated():
    with TestClient(web_server.fastapi_app) as client:
        resp = client.get("/")

    assert resp.status_code == 200
    assert "Authenticate Access" in resp.text


def test_app_requires_authenticated_session():
    with TestClient(web_server.fastapi_app) as client:
        resp = client.get("/app", follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_build_k8s_auth_from_kubeconfig():
    payload = web_server.K8sLoginPayload(
        mode="kubeconfig",
        kubeconfig_yaml="""
apiVersion: v1
kind: Config
clusters:
  - name: demo
    cluster:
      server: https://cluster.example:6443
contexts:
  - name: demo
    context:
      cluster: demo
      user: demo-user
current-context: demo
users:
  - name: demo-user
    user:
      token: abc123
""",
    )

    auth = web_server._build_k8s_auth(payload)

    assert isinstance(auth, K8sAuth)
    assert auth.mode == "kubeconfig"
    assert auth.kubeconfig["current-context"] == "demo"


def test_build_openstack_auth_from_clouds_yaml_uses_app_credentials():
    payload = web_server.OpenStackLoginPayload(
        mode="clouds_yaml",
        clouds_yaml="""
clouds:
  demo:
    auth:
      auth_url: https://keystone.example/v3
      application_credential_id: app-id
      application_credential_secret: app-secret
    region_name: RegionOne
    interface: public
""",
        cloud_name="demo",
    )

    auth = web_server._build_openstack_auth(payload)

    assert isinstance(auth, OpenStackAuth)
    assert auth.mode == "application_credential"
    assert auth.application_credential_id == "app-id"
    assert auth.region_name == "RegionOne"


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

    def fake_refresh(self, cached_nodes=None, silent=False):
        captured["refreshed"] = True
        captured["cached_nodes"] = cached_nodes
        captured["silent"] = silent

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
        assert session.json()["has_k8s_auth"] is True
        assert session.json()["has_openstack_auth"] is True
        assert session.json()["session_mode"] == "full"

        namespaces = client.get("/api/k8s/namespaces")
        assert namespaces.status_code == 200
        assert namespaces.json()["items"][0]["name"] == "default"
        assert captured["api_k8s_auth"] == captured["login_k8s_auth"]


def test_login_allows_k8s_only_session(monkeypatch):
    captured: dict[str, object] = {}

    def fake_get_nodes(auth=None):
        captured["login_k8s_auth"] = auth
        return [{"name": "node-a", "hostname": "hv-a", "ready": True, "cordoned": False}]

    def fake_refresh(self, cached_nodes=None, silent=False):
        captured["refreshed"] = True
        captured["cached_nodes"] = cached_nodes

    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", fake_get_nodes)
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", fake_refresh)

    payload = {
        "kubernetes": {
            "server": "https://cluster.example:6443",
            "token": "token-1",
            "skip_tls_verify": True,
        },
        "openstack": None,
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        assert login.json() == {"ok": True}
        assert isinstance(captured["login_k8s_auth"], K8sAuth)

        session = client.get("/api/session")
        assert session.status_code == 200
        body = session.json()
        assert body["authenticated"] is True
        assert body["has_k8s_auth"] is True
        assert body["has_openstack_auth"] is False
        assert body["session_mode"] == "kubernetes_only"
        assert body["username"] is None
        assert body["project_name"] is None
        assert body["role_names"] == []
        assert body["is_admin"] is False


def test_k8s_networking_endpoints_return_items(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [{"name": "node-a", "hostname": "hv-a", "ready": True, "cordoned": False}])
    monkeypatch.setattr(web_server.k8s_ops, "list_k8s_cluster_networks", lambda auth=None: [{"name": "pod-network-10.244.1.0/24", "cidr": "10.244.1.0/24"}])
    monkeypatch.setattr(web_server.k8s_ops, "list_k8s_network_domains", lambda auth=None: [{"namespace": "web", "name": "web"}])
    monkeypatch.setattr(web_server.k8s_ops, "list_k8s_kubeovn_vpcs", lambda auth=None: [{"name": "tenant-a"}])
    monkeypatch.setattr(web_server.k8s_ops, "list_k8s_kubeovn_subnets", lambda auth=None: [{"name": "tenant-a-apps"}])
    monkeypatch.setattr(web_server.k8s_ops, "list_k8s_kubeovn_vlans", lambda auth=None: [{"name": "tenant-a-vlan"}])
    monkeypatch.setattr(web_server.k8s_ops, "list_k8s_kubeovn_provider_networks", lambda auth=None: [{"name": "physnet1"}])
    monkeypatch.setattr(web_server.k8s_ops, "list_k8s_kubeovn_provider_subnets", lambda auth=None: [{"name": "tenant-a-provider"}])
    monkeypatch.setattr(web_server.k8s_ops, "list_k8s_kubeovn_ips", lambda auth=None: [{"name": "pod.web.frontend"}])

    payload = {
        "kubernetes": {
            "server": "https://cluster.example:6443",
            "token": "token-1",
            "skip_tls_verify": True,
        },
        "openstack": None,
    }

    with TestClient(web_server.fastapi_app) as client:
        login = client.post("/api/session", json=payload)
        assert login.status_code == 200
        vpcs = client.get("/api/k8s/vpcs")
        subnets = client.get("/api/k8s/subnets")
        vlans = client.get("/api/k8s/vlans")
        provider_networks = client.get("/api/k8s/provider-networks")
        provider_subnets = client.get("/api/k8s/provider-subnets")
        ips = client.get("/api/k8s/ips")
        cluster_networks = client.get("/api/k8s/cluster-networks")
        network_domains = client.get("/api/k8s/network-domains")

    assert vpcs.status_code == 200
    assert vpcs.json()["items"][0]["name"] == "tenant-a"
    assert subnets.status_code == 200
    assert subnets.json()["items"][0]["name"] == "tenant-a-apps"
    assert vlans.status_code == 200
    assert vlans.json()["items"][0]["name"] == "tenant-a-vlan"
    assert provider_networks.status_code == 200
    assert provider_networks.json()["items"][0]["name"] == "physnet1"
    assert provider_subnets.status_code == 200
    assert provider_subnets.json()["items"][0]["name"] == "tenant-a-provider"
    assert ips.status_code == 200
    assert ips.json()["items"][0]["name"] == "pod.web.frontend"
    assert cluster_networks.status_code == 200
    assert cluster_networks.json()["items"][0]["cidr"] == "10.244.1.0/24"
    assert network_domains.status_code == 200
    assert network_domains.json()["items"][0]["namespace"] == "web"
