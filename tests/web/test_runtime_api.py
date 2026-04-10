from __future__ import annotations

from fastapi.testclient import TestClient

from draino.web import server as web_server
from draino.web import latency as web_latency


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
            ("ghcr.io/example/draino", "0.1.0"): "sha256:old",
            ("ghcr.io/upstream/draino", "main"): "sha256:new",
        }[(repo, ref)],
    )

    status = web_server._compute_update_status()

    assert status["current_digest"] == "sha256:old"
    assert status["current_digest_source"] == "image_tag"
    assert status["latest_digest"] == "sha256:new"
    assert status["update_available"] is True
    assert status["update_repository"] == "ghcr.io/upstream/draino"


def test_compute_update_status_resolves_current_tag_from_image_repository(monkeypatch):
    monkeypatch.setattr(web_server, "_get_running_image_digest", lambda: None)
    monkeypatch.setattr(web_server, "_IMAGE_TAG", "0.1.0")
    monkeypatch.setattr(web_server, "_UPDATE_TRACK", "main")
    monkeypatch.setattr(web_server, "_IMAGE_REPOSITORY", "ghcr.io/local/draino")
    monkeypatch.setattr(web_server, "_UPDATE_REPOSITORY", "ghcr.io/upstream/draino")

    calls: list[tuple[str, str]] = []

    def fake_resolve(repo, ref):
        calls.append((repo, ref))
        return {
            ("ghcr.io/local/draino", "0.1.0"): "sha256:old",
            ("ghcr.io/upstream/draino", "main"): "sha256:new",
        }[(repo, ref)]

    monkeypatch.setattr(web_server, "_resolve_remote_track_digest", fake_resolve)

    status = web_server._compute_update_status()

    assert calls == [
        ("ghcr.io/upstream/draino", "main"),
        ("ghcr.io/local/draino", "0.1.0"),
    ]
    assert status["current_digest"] == "sha256:old"
    assert status["update_available"] is True


def test_health_and_readiness_endpoints():
    with TestClient(web_server.fastapi_app) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}


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


def test_app_meta_endpoint_forces_fresh_update_check(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

    calls: list[bool] = []

    def fake_update_status(force=False):
        calls.append(force)
        return {
            "current_tag": "0.1.0",
            "current_digest": "sha256:111",
            "track": "main",
            "latest_digest": "sha256:222",
            "update_available": True,
            "update_url": "https://example.com/update",
            "error": None,
        }

    monkeypatch.setattr(web_server, "_get_app_update_status", fake_update_status)

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
    assert calls == [True]


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
    monkeypatch.setattr(
        web_server,
        "_get_runtime_diagnostics",
        lambda record: {
            "sessions": {"ttl_seconds": 28800, "active_count": 1, "stored_count": 1, "expired_count": 0},
            "current_session": {"node_count": 2, "node_detail_entries": 1, "node_metrics_entries": 1, "host_signal_entries": 1},
            "all_sessions": {"node_count": 2, "node_detail_entries": 1, "node_metrics_entries": 1, "client_count": 0},
            "global_caches": {"node_agent_endpoint_cache": 1, "openstack_flavor_cache": 2, "runtime_history_samples": 3},
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
    assert runtime.json()["diagnostics"]["sessions"]["ttl_seconds"] == 28800


def test_app_runtime_clear_endpoint_returns_updated_diagnostics(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

    captured = {"action": None}
    monkeypatch.setattr(
        web_server,
        "_clear_runtime_diagnostics",
        lambda record, action: captured.__setitem__("action", action) or {"action": action, "cleared": {"expired_sessions": 2}},
    )
    monkeypatch.setattr(
        web_server,
        "_get_runtime_diagnostics",
        lambda record: {
            "sessions": {"ttl_seconds": 28800, "active_count": 1, "stored_count": 1, "expired_count": 0},
            "current_session": {"node_count": 0, "node_detail_entries": 0, "node_metrics_entries": 0, "host_signal_entries": 0},
            "all_sessions": {"node_count": 0, "node_detail_entries": 0, "node_metrics_entries": 0, "client_count": 0},
            "global_caches": {"node_agent_endpoint_cache": 0, "openstack_flavor_cache": 0, "runtime_history_samples": 0},
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
        cleared = client.post("/api/app-runtime/clear", json={"action": "sweep_expired_sessions"})

    assert cleared.status_code == 200
    assert captured["action"] == "sweep_expired_sessions"
    assert cleared.json()["ok"] is True
    assert cleared.json()["result"]["cleared"]["expired_sessions"] == 2


def test_get_app_runtime_includes_latency_summary():
    web_latency.record_latency("node_detail", 120.0)
    web_latency.record_latency("node_detail", 180.0)

    runtime = web_server._get_app_runtime()

    assert "latencies" in runtime
    assert runtime["latencies"]["node_detail"]["count"] >= 2
    assert runtime["latencies"]["node_detail"]["last_ms"] >= 120.0
