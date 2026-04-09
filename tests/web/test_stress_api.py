from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from draino.web import server as web_server
from draino.web import stress_helpers


def test_stress_options_endpoint_returns_selected_profile_and_filtered_defaults(monkeypatch):
    def fake_start_refresh(self, cached_nodes=None, silent=False):
        self.node_states = {
            "cmp-a": SimpleNamespace(is_compute=True),
            "cmp-b": SimpleNamespace(is_compute=True),
        }

    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", fake_start_refresh)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

        class image:
            @staticmethod
            def images():
                return [
                    SimpleNamespace(id="img-1", name="ubuntu-24.04", status="active", min_disk=10, min_ram=1024, disk_format="qcow2", visibility="public", properties={}),
                ]

        class compute:
            @staticmethod
            def flavors():
                return [
                    SimpleNamespace(id="flavor-too-small", name="m1.tiny", vcpus=1, ram=512, disk=5, ephemeral=0, swap=0, is_public=True),
                    SimpleNamespace(id="flavor-ok", name="m1.small", vcpus=2, ram=2048, disk=20, ephemeral=0, swap=0, is_public=True),
                ]

            @staticmethod
            def keypairs():
                return [SimpleNamespace(name="ops-key", fingerprint="fp-1", type="ssh")]

        class orchestration:
            @staticmethod
            def stacks():
                return []

        class network:
            @staticmethod
            def subnets():
                return [SimpleNamespace(cidr="10.77.70.0/24")]

            @staticmethod
            def networks():
                return []

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

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
        resp = client.get("/api/stress/options?profile=full-host-spread")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["options"]["selected_profile"]["key"] == "full-host-spread"
    assert body["options"]["defaults"]["vm_count"] == 2
    assert body["options"]["defaults"]["image_id"] == "img-1"
    assert body["options"]["defaults"]["flavor_id"] == "flavor-ok"
    assert body["options"]["defaults"]["keypair_name"] == "ops-key"
    assert body["options"]["defaults"]["cidr"] == "10.77.71.0/24"


def test_stress_options_endpoint_reports_active_stack(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

        class image:
            @staticmethod
            def images():
                return []

        class compute:
            @staticmethod
            def flavors():
                return []

            @staticmethod
            def keypairs():
                return []

        class orchestration:
            @staticmethod
            def stacks():
                return [
                    SimpleNamespace(stack_name="vibe-stress-20260409-141522", status="CREATE_COMPLETE", creation_time="2026-04-09T14:15:22Z", updated_time="2026-04-09T14:20:00Z", description="stress"),
                ]

        class network:
            @staticmethod
            def subnets():
                return []

            @staticmethod
            def networks():
                return []

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

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
        resp = client.get("/api/stress/options?profile=burst")

    body = resp.json()
    assert resp.status_code == 200
    assert body["options"]["guardrail"]["active"] is True
    assert body["options"]["selected_profile"]["key"] == "burst"
    assert body["options"]["guardrail"]["stack"]["stack_name"] == "vibe-stress-20260409-141522"


def test_stress_launch_status_and_delete_endpoints(monkeypatch):
    def fake_start_refresh(self, cached_nodes=None, silent=False):
        self.node_states = {
            "cmp-a": SimpleNamespace(is_compute=True),
            "cmp-b": SimpleNamespace(is_compute=True),
        }

    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", fake_start_refresh)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])
    monkeypatch.setattr(stress_helpers, "_stress_test_id", lambda: "20260409-141522")

    state = {"active": False}

    class FakeOrchestration:
        def create_stack(self, **kwargs):
            state["active"] = True
            state["create_kwargs"] = kwargs
            return SimpleNamespace(id="stack-1")

        def delete_stack(self, stack_ref):
            state["deleted_ref"] = getattr(stack_ref, "id", None) or getattr(stack_ref, "stack_name", None) or stack_ref
            state["active"] = False

        def stacks(self):
            if not state["active"]:
                return []
            return [
                SimpleNamespace(
                    id="stack-1",
                    stack_name="vibe-stress-20260409-141522",
                    status="CREATE_COMPLETE",
                    creation_time="2026-04-09T14:15:22Z",
                    updated_time="2026-04-09T14:22:36Z",
                    description="stress",
                    parameters={
                        "test_id": "20260409-141522",
                        "profile": "small-distribution",
                        "vm_count": 2,
                        "network_cidr": "10.77.71.0/24",
                    },
                    outputs=[],
                )
            ]

        def resources(self, stack_ref):
            assert stack_ref == "stack-1"
            return [
                SimpleNamespace(resource_name="stress_net", resource_type="OS::Neutron::Net", physical_resource_id="net-1", resource_status="CREATE_COMPLETE", creation_time="2026-04-09T14:15:22Z", updated_time="2026-04-09T14:15:25Z"),
                SimpleNamespace(resource_name="stress_subnet", resource_type="OS::Neutron::Subnet", physical_resource_id="subnet-1", resource_status="CREATE_COMPLETE", creation_time="2026-04-09T14:15:25Z", updated_time="2026-04-09T14:15:27Z"),
                SimpleNamespace(resource_name="stress_router", resource_type="OS::Neutron::Router", physical_resource_id="router-1", resource_status="CREATE_COMPLETE", creation_time="2026-04-09T14:15:27Z", updated_time="2026-04-09T14:15:32Z"),
                SimpleNamespace(resource_name="stress_vm_01", resource_type="OS::Nova::Server", physical_resource_id="server-1", resource_status="CREATE_COMPLETE", creation_time="2026-04-09T14:15:40Z", updated_time="2026-04-09T14:16:20Z"),
                SimpleNamespace(resource_name="stress_vm_02", resource_type="OS::Nova::Server", physical_resource_id="server-2", resource_status="CREATE_COMPLETE", creation_time="2026-04-09T14:15:42Z", updated_time="2026-04-09T14:16:30Z"),
            ]

    class FakeConn:
        def authorize(self):
            return None

        image = SimpleNamespace(images=lambda: [
            SimpleNamespace(id="img-1", name="ubuntu-24.04", status="active", min_disk=10, min_ram=1024, disk_format="qcow2", visibility="public", properties={}),
        ])

        class compute:
            @staticmethod
            def flavors():
                return [
                    SimpleNamespace(id="flavor-ok", name="m1.small", vcpus=2, ram=2048, disk=20, ephemeral=0, swap=0, is_public=True),
                ]

            @staticmethod
            def keypairs():
                return [SimpleNamespace(name="ops-key", fingerprint="fp-1", type="ssh")]

            @staticmethod
            def get_server(server_id):
                mapping = {
                    "server-1": SimpleNamespace(id="server-1", name="vibe-stress-20260409-141522-vm-01", status="ACTIVE", compute_host="cmp-a.example.com", addresses={"stress-net": [{"addr": "10.77.71.5"}]}, to_dict=lambda: {"addresses": {"stress-net": [{"addr": "10.77.71.5"}]}}),
                    "server-2": SimpleNamespace(id="server-2", name="vibe-stress-20260409-141522-vm-02", status="ACTIVE", compute_host="cmp-b.example.com", addresses={"stress-net": [{"addr": "10.77.71.6"}]}, to_dict=lambda: {"addresses": {"stress-net": [{"addr": "10.77.71.6"}]}}),
                }
                return mapping[server_id]

        orchestration = FakeOrchestration()
        network = SimpleNamespace(
            subnets=lambda: [],
            networks=lambda: [SimpleNamespace(id="ext-net-1", name="public", is_router_external=True, to_dict=lambda: {"router:external": True})],
        )

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "get_current_role_names", lambda auth=None: ["admin"])

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
        launch = client.post("/api/stress/launch", json={
            "profile": "small-distribution",
            "vm_count": 2,
            "image_id": "img-1",
            "flavor_id": "flavor-ok",
            "keypair_mode": "existing",
            "keypair_name": "ops-key",
            "cidr_mode": "manual",
            "cidr": "10.77.71.0/24",
            "external_network_id": "ext-net-1",
        })
        status = client.get("/api/stress/status")
        deleted = client.post("/api/stress/delete")

    launch_body = launch.json()
    assert launch.status_code == 200
    assert launch_body["error"] is None
    assert launch_body["status"]["active"] is True
    assert launch_body["status"]["test"]["requested_vms"] == 2
    assert state["create_kwargs"]["name"] == "vibe-stress-20260409-141522"
    assert state["create_kwargs"]["parameters"]["external_network_id"] == "ext-net-1"

    status_body = status.json()
    assert status.status_code == 200
    assert status_body["status"]["summary"]["plumbing_elapsed"] == "10s"
    assert status_body["status"]["summary"]["avg_vm_build"] == "44s"
    assert status_body["status"]["servers"][0]["host"] == "cmp-a.example.com"
    assert status_body["status"]["distribution"][0]["share_pct"] == 50.0

    delete_body = deleted.json()
    assert deleted.status_code == 200
    assert delete_body["result"]["deleted"] is True
    assert state["deleted_ref"] == "stack-1"
