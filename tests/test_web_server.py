from __future__ import annotations

from fastapi.testclient import TestClient

from draino.models import NodeState
from draino.operations import k8s_ops
from draino.operations.k8s_ops import K8sAuth
from draino.operations.openstack_ops import OpenStackAuth
from draino.web import server as web_server


def test_get_networks_coerces_external_flag_strings(monkeypatch):
    class FakeNetwork:
        def __init__(self, network_id: str, external_value, fallback_value=False):
            self.id = network_id
            self.name = f"net-{network_id}"
            self.status = "ACTIVE"
            self.is_admin_state_up = True
            self.is_shared = False
            self.project_id = "proj-1"
            self.subnet_ids = []
            self.is_router_external = fallback_value
            self._external_value = external_value

        def to_dict(self):
            return {"router:external": self._external_value}

    class FakeNetworkAPI:
        @staticmethod
        def networks():
            return [
                FakeNetwork("1", "False"),
                FakeNetwork("2", "true"),
                FakeNetwork("3", None, fallback_value=True),
            ]

    class FakeConn:
        network = FakeNetworkAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    items = web_server._get_networks(auth=None)

    assert items[0]["external"] is False
    assert items[1]["external"] is True
    assert items[2]["external"] is True


def test_serialise_includes_k8s_taints():
    state = NodeState(k8s_name="node-1", hypervisor="hv-1")
    state.k8s_taints = [{"key": "key", "value": "value", "effect": "NoSchedule"}]
    state.is_edge = True
    state.node_agent_ready = False

    data = web_server._serialise(state)

    assert data["k8s_taints"] == [{"key": "key", "value": "value", "effect": "NoSchedule"}]
    assert data["is_edge"] is True
    assert data["node_agent_ready"] is False


def test_session_endpoint_reports_unauthenticated():
    with TestClient(web_server.fastapi_app) as client:
        resp = client.get("/api/session")

    assert resp.status_code == 200
    assert resp.json() == {"authenticated": False}


def test_normalise_image_digest_handles_kubernetes_image_ids():
    assert web_server._normalise_image_digest("docker-pullable://ghcr.io/busterswt/draino-claude@sha256:abc123") == "sha256:abc123"
    assert web_server._normalise_image_digest("sha256:def456") == "sha256:def456"
    assert web_server._normalise_image_digest("ghcr.io/busterswt/draino-claude:main") is None


def test_resolve_remote_track_digest_uses_top_level_manifest_digest(monkeypatch):
    monkeypatch.setattr(
        web_server,
        "_ghcr_manifest_request",
        lambda repository_path, reference, token=None: (
            {
                "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
                "manifests": [
                    {"digest": "sha256:child1", "platform": {"os": "linux", "architecture": "amd64"}},
                    {"digest": "sha256:child2", "platform": {"os": "linux", "architecture": "arm64"}},
                ],
            },
            {"Docker-Content-Digest": "sha256:toplevel"},
        ),
    )

    digest = web_server._resolve_remote_track_digest("ghcr.io/busterswt/draino-claude", "main")

    assert digest == "sha256:toplevel"


def test_compute_update_status_falls_back_to_configured_tag_digest(monkeypatch):
    monkeypatch.setattr(web_server, "_get_running_image_digest", lambda: None)
    monkeypatch.setattr(web_server, "_IMAGE_TAG", "0.1.0")
    monkeypatch.setattr(web_server, "_UPDATE_TRACK", "main")
    monkeypatch.setattr(web_server, "_IMAGE_REPOSITORY", "ghcr.io/example/draino")
    monkeypatch.setattr(web_server, "_UPDATE_REPOSITORY", "ghcr.io/upstream/draino")
    monkeypatch.setattr(
        web_server,
        "_resolve_remote_track_digest",
        lambda repo, ref: {
            ("ghcr.io/upstream/draino", "0.1.0"): "sha256:old",
            ("ghcr.io/upstream/draino", "main"): "sha256:new",
        }[(repo, ref)],
    )

    status = web_server._compute_update_status()

    assert status["current_digest"] == "sha256:old"
    assert status["current_digest_source"] == "image_tag"
    assert status["latest_digest"] == "sha256:new"
    assert status["update_available"] is True
    assert status["update_repository"] == "ghcr.io/upstream/draino"


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


def test_health_and_readiness_endpoints():
    with TestClient(web_server.fastapi_app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


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

        namespaces = client.get("/api/k8s/namespaces")
        assert namespaces.status_code == 200
        assert namespaces.json()["items"][0]["name"] == "default"
        assert captured["api_k8s_auth"] == captured["login_k8s_auth"]


def test_app_meta_endpoint_returns_update_status(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server,
        "_get_app_update_status",
        lambda force=False: {
            "current_tag": "0.1.0",
            "current_digest": "sha256:111",
            "track": "main",
            "latest_digest": "sha256:222",
            "update_available": True,
            "update_url": "https://example.com/update",
            "error": None,
        },
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

        meta = client.get("/api/app-meta")

    assert meta.status_code == 200
    assert meta.json()["update_available"] is True
    assert meta.json()["track"] == "main"


def test_version_endpoint_returns_short_sha_without_auth(monkeypatch):
    monkeypatch.setattr(
        web_server,
        "_get_public_version_status",
        lambda: {
            "current_digest": "sha256:1234567890abcdef",
            "short_sha": "1234567890ab",
            "current_tag": "main",
            "current_digest_source": "running_pod",
        },
    )

    with TestClient(web_server.fastapi_app) as client:
        version = client.get("/api/version")

    assert version.status_code == 200
    assert version.json()["short_sha"] == "1234567890ab"


def test_app_runtime_endpoint_returns_runtime_snapshot(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server,
        "_get_app_runtime",
        lambda: {
            "current": {"cpu_percent": 12.5, "rss_bytes": 104857600, "timestamp": 1000.0},
            "history": [{"cpu_percent": 5.0, "rss_bytes": 52428800, "timestamp": 900.0}],
            "requests": {"cpu": "250m", "memory": "512Mi"},
            "limits": {"cpu": "1", "memory": "1Gi"},
            "restart_count": 1,
        },
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

        runtime = client.get("/api/app-runtime")

    assert runtime.status_code == 200
    assert runtime.json()["current"]["cpu_percent"] == 12.5
    assert runtime.json()["limits"]["memory"] == "1Gi"


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
