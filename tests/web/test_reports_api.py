from __future__ import annotations

from fastapi.testclient import TestClient

from draino.models import NodeState
from draino.web import server as web_server
from types import SimpleNamespace


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
        "cmp-a02": NodeState(
            k8s_name="cmp-a02",
            hypervisor="hv-a02",
            is_compute=True,
            availability_zone="az-a",
            compute_status="disabled",
            k8s_cordoned=True,
            vm_count=0,
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
        "db-c03": NodeState(
            k8s_name="db-c03",
            hypervisor="db-c03",
            availability_zone="az-c",
            node_agent_ready=True,
            hosts_mariadb=True,
        ),
    }

    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_k8s_detail",
        lambda node_name, auth=None: {"pod_count": {"cmp-a01": 37, "cmp-a02": 0, "db-c03": 5, "mgmt-b02": 8}[node_name]},
    )

    payload = web_server._build_maintenance_readiness_report(server)

    assert payload["error"] is None
    assert payload["report"]["summary"]["ready_now"] == 1
    assert payload["report"]["summary"]["blocked"] == 2
    assert payload["report"]["summary"]["review"] == 1
    assert payload["report"]["summary"]["reboot_required"] == 1
    assert payload["report"]["items"][0]["node"] == "cmp-a01"
    assert payload["report"]["items"][0]["pod_count"] == 37
    assert payload["report"]["items"][0]["verdict"] == "review"
    assert payload["report"]["items"][1]["verdict"] == "ready"
    assert payload["report"]["items"][2]["nova_status"] == "-"
    assert payload["report"]["items"][2]["verdict"] == "blocked"
    assert payload["report"]["items"][2]["blocking_reason"] == "mariadb requires staggered reboots"
    assert payload["report"]["items"][3]["verdict"] == "blocked"
    assert "etcd requires staggered reboots" in payload["report"]["items"][3]["blocking_reason"]
    assert payload["report"]["debug"]["counts"]["nodes"] == 4
    assert payload["report"]["debug"]["counts"]["k8s_detail_calls"] == 4
    assert payload["report"]["debug"]["timing_ms"]["total"] >= 0
    findings = payload["report"]["findings"]
    assert findings[0]["severity"] == "high"
    assert findings[0]["message"] == "mariadb requires staggered reboots"
    assert findings[1]["severity"] == "high"
    assert "etcd requires staggered reboots" in findings[1]["message"]


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


def test_build_capacity_headroom_report_aggregates_live_compute_state(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    server.node_states = {
        "cmp-a01": NodeState(
            k8s_name="cmp-a01",
            hypervisor="hv-a01",
            is_compute=True,
            availability_zone="az-a",
            aggregates=["general", "ssd"],
            compute_status="disabled",
            k8s_cordoned=True,
            vm_count=14,
            amphora_count=1,
            node_agent_ready=True,
        ),
        "cmp-c03": NodeState(
            k8s_name="cmp-c03",
            hypervisor="hv-c03",
            is_compute=True,
            availability_zone="az-c",
            aggregates=["general"],
            compute_status="up",
            vm_count=22,
            amphora_count=3,
            hosts_mariadb=True,
            node_agent_ready=True,
        ),
    }

    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None, log_cb=None: {
            "hv-a01": {"availability_zone": "az-a", "aggregates": ["general", "ssd"]},
            "hv-c03": {"availability_zone": "az-c", "aggregates": ["general"]},
        },
    )
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {
            "hv-a01": {"vcpus": 96, "vcpus_used": 72, "memory_mb": 524288, "memory_mb_used": 430080},
            "hv-c03": {"vcpus": 128, "vcpus_used": 115, "memory_mb": 524288, "memory_mb_used": 483328},
        }[hypervisor],
    )
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_pod_capacity_summary",
        lambda auth=None: {
            "cmp-a01": {"pods_allocatable": "110", "pod_count": 37},
            "cmp-c03": {"pods_allocatable": "110", "pod_count": 41},
        },
    )

    payload = web_server._build_capacity_headroom_report(server)

    assert payload["error"] is None
    assert payload["report"]["scope"]["computes"] == 2
    assert payload["report"]["scope"]["instances"] == 36
    assert payload["report"]["summary"]["drain_safe_hosts"] == 1
    assert payload["report"]["items"][0]["host"] == "cmp-a01"
    assert payload["report"]["items"][0]["availability_zone"] == "az-a"
    assert payload["report"]["items"][0]["maintenance_status"] == "drain-safe"
    assert payload["report"]["items"][1]["maintenance_status"] == "blocked"
    assert payload["report"]["az_headroom"][1]["availability_zone"] == "az-c"
    assert payload["report"]["az_headroom"][1]["severity"] == "high"
    assert payload["report"]["debug"]["counts"]["computes"] == 2
    assert payload["report"]["debug"]["counts"]["hypervisor_detail_calls"] == 2
    assert payload["report"]["debug"]["counts"]["k8s_detail_calls"] == 1
    assert payload["report"]["debug"]["timing_ms"]["total"] >= 0


def test_get_all_host_summaries_reads_aggregate_availability_zone_attribute(monkeypatch):
    class FakeConn:
        class compute:
            @staticmethod
            def services(binary=None):
                return [SimpleNamespace(host="hv-a01", state="up", status="enabled")]

            @staticmethod
            def servers(all_projects=True):
                return []

            @staticmethod
            def aggregates():
                return [
                    SimpleNamespace(
                        name="general",
                        availability_zone="az-a",
                        metadata={},
                        hosts=["hv-a01"],
                    )
                ]

        class load_balancer:
            @staticmethod
            def amphorae():
                return []

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    payload = web_server.openstack_ops.get_all_host_summaries()

    assert payload["hv-a01"]["availability_zone"] == "az-a"
    assert payload["hv-a01"]["aggregates"] == ["general"]


def test_capacity_reports_endpoints_return_json_and_csv(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None, log_cb=None: {
            "hv-a01": {"availability_zone": "az-a", "aggregates": ["general"]},
        },
    )
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_hypervisor_detail",
        lambda hypervisor, auth=None: {"vcpus": 96, "vcpus_used": 40, "memory_mb": 524288, "memory_mb_used": 200000},
    )
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_pod_capacity_summary",
        lambda auth=None: {"cmp-a01": {"pods_allocatable": "110", "pod_count": 12}},
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
            aggregates=["general"],
            compute_status="disabled",
            k8s_cordoned=True,
            vm_count=14,
            amphora_count=1,
            node_agent_ready=True,
        )

        report = client.get("/api/reports/capacity-headroom")
        export = client.get("/api/reports/capacity-headroom.csv")

    assert report.status_code == 200
    assert report.json()["report"]["items"][0]["host"] == "cmp-a01"
    assert export.status_code == 200
    assert export.headers["content-disposition"] == 'attachment; filename="capacity-headroom.csv"'
    assert "cmp-a01" in export.text


def test_build_k8s_node_health_density_report_summarises_live_k8s_state(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {
                    "node": "cmp-a03",
                    "ready": False,
                    "kubelet_version": "v1.31.2",
                    "runtime_label": "containerd 2.0",
                    "pods_allocatable": 110,
                    "pod_count": 61,
                    "pvc_pod_count": 4,
                    "pvc_claim_count": 5,
                    "namespace_count": 5,
                    "cpu_allocatable_mcpu": 100000,
                    "cpu_requests_mcpu": 71000,
                    "memory_allocatable_mib": 100000,
                    "memory_requests_mib": 86000,
                    "conditions": ["MemoryPressure"],
                    "cordoned": False,
                },
                {
                    "node": "cmp-b02",
                    "ready": True,
                    "kubelet_version": "v1.30.7",
                    "runtime_label": "containerd 2.0",
                    "pods_allocatable": 110,
                    "pod_count": 58,
                    "pvc_pod_count": 3,
                    "pvc_claim_count": 3,
                    "namespace_count": 7,
                    "cpu_allocatable_mcpu": 100000,
                    "cpu_requests_mcpu": 64000,
                    "memory_allocatable_mib": 100000,
                    "memory_requests_mib": 61000,
                    "conditions": [],
                    "cordoned": False,
                },
                {
                    "node": "cmp-c01",
                    "ready": True,
                    "kubelet_version": "v1.31.2",
                    "runtime_label": "containerd 2.0",
                    "pods_allocatable": 110,
                    "pod_count": 92,
                    "pvc_pod_count": 11,
                    "pvc_claim_count": 14,
                    "namespace_count": 8,
                    "cpu_allocatable_mcpu": 100000,
                    "cpu_requests_mcpu": 82000,
                    "memory_allocatable_mib": 100000,
                    "memory_requests_mib": 78000,
                    "conditions": [],
                    "cordoned": False,
                },
            ],
            "version_counts": {"v1.31.2": 2, "v1.30.7": 1},
            "condition_counts": {"NotReady": 1, "MemoryPressure": 1, "Cordoned": 0},
            "error": None,
        },
    )

    payload = web_server._build_k8s_node_health_density_report(server)

    assert payload["error"] is None
    assert payload["report"]["summary"]["ready_nodes"] == 2
    assert payload["report"]["summary"]["version_drift"] == 1
    assert payload["report"]["summary"]["high_pod_density"] == 1
    assert payload["report"]["summary"]["pvc_hotspots"] == 1
    assert payload["report"]["items"][0]["risk"] == "high"
    assert payload["report"]["items"][1]["risk"] == "high"
    assert payload["report"]["items"][2]["risk"] == "medium"
    assert payload["report"]["version_items"][0]["is_majority"] is True
    assert payload["report"]["pvc_items"][0]["node"] == "cmp-c01"
    assert payload["report"]["debug"]["counts"]["nodes"] == 3


def test_k8s_node_health_density_reports_endpoints_return_json_and_csv(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_k8s_node_health_density_summary",
        lambda auth=None: {
            "nodes": [
                {
                    "node": "cmp-a03",
                    "ready": False,
                    "kubelet_version": "v1.31.2",
                    "runtime_label": "containerd 2.0",
                    "pods_allocatable": 110,
                    "pod_count": 61,
                    "pvc_pod_count": 4,
                    "pvc_claim_count": 5,
                    "namespace_count": 5,
                    "cpu_allocatable_mcpu": 100000,
                    "cpu_requests_mcpu": 71000,
                    "memory_allocatable_mib": 100000,
                    "memory_requests_mib": 86000,
                    "conditions": ["MemoryPressure"],
                    "cordoned": False,
                },
            ],
            "version_counts": {"v1.31.2": 1},
            "condition_counts": {"NotReady": 1, "MemoryPressure": 1, "Cordoned": 0},
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
        report = client.get("/api/reports/k8s-node-health-density")
        export = client.get("/api/reports/k8s-node-health-density.csv")

    assert report.status_code == 200
    assert report.json()["report"]["items"][0]["node"] == "cmp-a03"
    assert export.status_code == 200
    assert export.headers["content-disposition"] == 'attachment; filename="k8s-node-health-density.csv"'
    assert "cmp-a03" in export.text


def test_build_capacity_headroom_report_resolves_short_host_aliases(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    server.node_states = {
        "cmp-a01.example.com": NodeState(
            k8s_name="cmp-a01.example.com",
            hypervisor="hv-a01.example.com",
            is_compute=True,
            compute_status="disabled",
            k8s_cordoned=True,
            vm_count=14,
            node_agent_ready=True,
        ),
    }

    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_all_host_summaries",
        lambda auth=None, log_cb=None: {
            "hv-a01": {"availability_zone": "az-a", "aggregates": ["general", "ssd"]},
        },
    )

    def fake_hv_detail(hypervisor, auth=None):
        if hypervisor == "hv-a01":
            return {"vcpus": 96, "vcpus_used": 72, "memory_mb": 524288, "memory_mb_used": 430080}
        return {"vcpus": None, "vcpus_used": None, "memory_mb": None, "memory_mb_used": None}

    monkeypatch.setattr(web_server.openstack_ops, "get_hypervisor_detail", fake_hv_detail)
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_pod_capacity_summary",
        lambda auth=None: {"cmp-a01.example.com": {"pods_allocatable": "110", "pod_count": 37}},
    )

    payload = web_server._build_capacity_headroom_report(server)

    assert payload["error"] is None
    item = payload["report"]["items"][0]
    assert item["availability_zone"] == "az-a"
    assert item["aggregates"] == ["general", "ssd"]
    assert item["vcpus"] == 96
    assert item["memory_mb"] == 524288


def test_build_project_placement_report_flags_concentrated_projects(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_project_vm_distribution",
        lambda auth=None: [
            {
                "project_id": "proj-a",
                "project_name": "tenant-a",
                "vm_count": 8,
                "host_count": 2,
                "top_host": "cmp-a01",
                "top_host_count": 6,
                "top_host_pct": 75.0,
                "has_dominant_host": True,
                "host_counts": [{"host": "cmp-a01", "vm_count": 6}, {"host": "cmp-a02", "vm_count": 2}],
            },
            {
                "project_id": "proj-b",
                "project_name": "tenant-b",
                "vm_count": 6,
                "host_count": 3,
                "top_host": "cmp-b01",
                "top_host_count": 3,
                "top_host_pct": 50.0,
                "has_dominant_host": True,
                "host_counts": [{"host": "cmp-b01", "vm_count": 3}, {"host": "cmp-b02", "vm_count": 2}, {"host": "cmp-b03", "vm_count": 1}],
            },
        ],
    )

    payload = web_server._build_project_placement_report(server)

    assert payload["error"] is None
    assert payload["report"]["summary"]["projects_at_risk"] == 2
    assert payload["report"]["summary"]["high_risk_projects"] == 1
    assert payload["report"]["items"][0]["risk"] == "high"
    assert payload["report"]["items"][0]["top_hosts_label"] == "cmp-a01 (6), cmp-a02 (2)"
    assert payload["report"]["findings"][0]["severity"] == "high"


def test_project_placement_reports_endpoints_return_json_and_csv(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.openstack_ops,
        "get_project_vm_distribution",
        lambda auth=None: [
            {
                "project_id": "proj-a",
                "project_name": "tenant-a",
                "vm_count": 8,
                "host_count": 2,
                "top_host": "cmp-a01",
                "top_host_count": 6,
                "top_host_pct": 75.0,
                "has_dominant_host": True,
                "host_counts": [{"host": "cmp-a01", "vm_count": 6}, {"host": "cmp-a02", "vm_count": 2}],
            },
        ],
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
        report = client.get("/api/reports/project-placement")
        export = client.get("/api/reports/project-placement.csv")

    assert report.status_code == 200
    assert report.json()["report"]["items"][0]["project_name"] == "tenant-a"
    assert export.status_code == 200
    assert export.headers["content-disposition"] == 'attachment; filename="project-placement.csv"'
    assert "tenant-a" in export.text


def test_get_project_vm_distribution_excludes_error_instances(monkeypatch):
    class FakeServer:
        def __init__(self, server_id, project_id, host, status):
            self.id = server_id
            self.project_id = project_id
            self.compute_host = host
            self.status = status

        def to_dict(self):
            return {
                "project_id": self.project_id,
                "OS-EXT-SRV-ATTR:host": self.compute_host,
                "status": self.status,
            }

    class FakeIdentity:
        @staticmethod
        def projects():
            return [SimpleNamespace(id="proj-a", name="tenant-a")]

    class FakeCompute:
        @staticmethod
        def servers(all_projects=True):
            assert all_projects is True
            return [
                FakeServer("vm-1", "proj-a", "cmp-a01", "ACTIVE"),
                FakeServer("vm-2", "proj-a", "cmp-a01", "ERROR"),
                FakeServer("vm-3", "proj-a", "cmp-a02", "SHUTOFF"),
            ]

    class FakeConn:
        identity = FakeIdentity()
        compute = FakeCompute()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    items = web_server.openstack_ops.get_project_vm_distribution()

    assert len(items) == 1
    assert items[0]["project_name"] == "tenant-a"
    assert items[0]["vm_count"] == 2
    assert items[0]["host_count"] == 2
    assert items[0]["top_host"] == ""
    assert items[0]["has_dominant_host"] is False


def test_build_placement_risk_report_summarises_control_edge_and_density(monkeypatch, tmp_path):
    server = web_server.DrainoServer(audit_log=str(tmp_path / "audit.log"))
    server.node_states = {
        "cmp-a01": NodeState(
            k8s_name="cmp-a01",
            hypervisor="hv-a01",
            is_compute=True,
            is_etcd=True,
            availability_zone="az-a",
            aggregates=["general"],
            compute_status="up",
            vm_count=18,
            amphora_count=0,
            node_agent_ready=True,
        ),
        "cmp-a02": NodeState(
            k8s_name="cmp-a02",
            hypervisor="hv-a02",
            is_compute=True,
            hosts_mariadb=True,
            availability_zone="az-a",
            aggregates=["general"],
            compute_status="up",
            vm_count=35,
            amphora_count=0,
            node_agent_ready=True,
        ),
        "cmp-gw01": NodeState(
            k8s_name="cmp-gw01",
            hypervisor="hv-gw01",
            is_compute=True,
            is_edge=True,
            availability_zone="az-b",
            aggregates=["edge"],
            compute_status="up",
            vm_count=16,
            amphora_count=5,
            node_agent_ready=True,
        ),
    }

    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_pod_capacity_summary",
        lambda auth=None: {
            "cmp-a01": {"pods_allocatable": "110", "pod_count": 8},
            "cmp-a02": {"pods_allocatable": "110", "pod_count": 33},
            "cmp-gw01": {"pods_allocatable": "110", "pod_count": 18},
        },
    )

    payload = web_server._build_placement_risk_report(server)

    assert payload["error"] is None
    assert payload["report"]["summary"]["etcd_risk"] == "high"
    assert payload["report"]["summary"]["mariadb_hosts"] == 1
    assert payload["report"]["summary"]["gateway_hosts"] == 1
    assert payload["report"]["control_plane_items"][0]["risk"] == "high"
    assert payload["report"]["edge_items"][0]["risk"] == "high"
    assert payload["report"]["density_items"][0]["risk"] in {"medium", "high"}
    assert payload["report"]["debug"]["counts"]["critical_nodes"] == 2


def test_placement_risk_reports_endpoints_return_json_and_csv(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])
    monkeypatch.setattr(
        web_server.k8s_ops,
        "get_node_pod_capacity_summary",
        lambda auth=None: {"cmp-a01": {"pods_allocatable": "110", "pod_count": 8}},
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
            is_etcd=True,
            availability_zone="az-a",
            aggregates=["general"],
            compute_status="disabled",
            k8s_cordoned=True,
            vm_count=14,
            node_agent_ready=True,
        )

        report = client.get("/api/reports/placement-risk")
        export = client.get("/api/reports/placement-risk.csv")

    assert report.status_code == 200
    assert report.json()["report"]["control_plane_items"][0]["node"] == "cmp-a01"
    assert export.status_code == 200
    assert export.headers["content-disposition"] == 'attachment; filename="placement-risk.csv"'
    assert "cmp-a01" in export.text
