from __future__ import annotations

from fastapi.testclient import TestClient

from draino.models import NodeState
from draino.web import server as web_server


def test_build_maintenance_readiness_report_aggregates_live_node_state(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    server.node_states = {
        "cmp-a01": NodeState(
            k8s_name="cmp-a01",
            hypervisor="hv-a01",
            is_compute=True,
            availability_zone="az-a",
            compute_status="up",
            vm_count=14,
            reboot_required=True,
            node_agent_ready=True,
        ),
        "mgmt-b02": NodeState(
            k8s_name="mgmt-b02",
            hypervisor="mgmt-b02",
            is_etcd=True,
            availability_zone="az-b",
            node_agent_ready=True,
            etcd_healthy=False,
        ),
    }

    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_k8s_detail",
        lambda node_name, auth=None: {"pod_count": {"cmp-a01": 37, "mgmt-b02": 8}[node_name]},
    )

    payload = web_server._build_maintenance_readiness_report(server)

    assert payload["error"] is None
    assert payload["report"]["summary"]["ready_now"] == 0
    assert payload["report"]["summary"]["blocked"] == 1
    assert payload["report"]["summary"]["review"] == 1
    assert payload["report"]["summary"]["reboot_required"] == 1
    assert payload["report"]["items"][0]["node"] == "cmp-a01"
    assert payload["report"]["items"][0]["pod_count"] == 37
    assert payload["report"]["items"][0]["verdict"] == "review"
    assert payload["report"]["items"][1]["verdict"] == "blocked"


def test_reports_endpoints_return_json_and_csv(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_k8s_detail",
        lambda node_name, auth=None: {"pod_count": 12},
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
        record.server.node_states["cmp-a01"] = NodeState(
            k8s_name="cmp-a01",
            hypervisor="hv-a01",
            is_compute=True,
            availability_zone="az-a",
            compute_status="up",
            vm_count=14,
            reboot_required=True,
            node_agent_ready=True,
        )

        report = client.get("/api/reports/maintenance-readiness")
        export = client.get("/api/reports/maintenance-readiness.csv")

    assert report.status_code == 200
    assert report.json()["report"]["items"][0]["node"] == "cmp-a01"
    assert export.status_code == 200
    assert export.headers["content-disposition"] == 'attachment; filename="maintenance-readiness.csv"'
    assert "cmp-a01" in export.text
