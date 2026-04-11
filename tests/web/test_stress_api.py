from __future__ import annotations

from ipaddress import ip_network
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
        resp = client.get("/api/stress/options?profile=full-host-spread")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["options"]["selected_profile"]["key"] == "full-host-spread"
    assert body["options"]["defaults"]["vm_count"] == 2
    assert body["options"]["defaults"]["image_id"] == "img-1"
    assert body["options"]["defaults"]["flavor_id"] == "flavor-ok"
    assert body["options"]["defaults"]["keypair_name"] == "ops-key"
    cidr = ip_network(body["options"]["defaults"]["cidr"], strict=False)
    assert cidr.prefixlen == 24
    assert cidr.is_private


def test_stress_environment_endpoint_returns_shared_cloud_options(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

        class image:
            @staticmethod
            def images():
                return [SimpleNamespace(id="img-1", name="ubuntu-24.04", status="active", min_disk=10, min_ram=1024, disk_format="qcow2", visibility="public", properties={})]

        class compute:
            @staticmethod
            def flavors():
                return [SimpleNamespace(id="flavor-ok", name="m1.small", vcpus=2, ram=2048, disk=20, ephemeral=0, swap=0, is_public=True)]

            @staticmethod
            def keypairs():
                return [SimpleNamespace(name="ops-key", fingerprint="fp-1", type="ssh")]

        class orchestration:
            @staticmethod
            def stacks():
                return []

        class network:
            @staticmethod
            def networks():
                return [SimpleNamespace(id="ext-net-1", name="public", is_router_external=True, to_dict=lambda: {"router:external": True})]

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
        resp = client.get("/api/stress/environment")

    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["environment"]["defaults"]["image_id"] == "img-1"
    assert body["environment"]["defaults"]["flavor_id"] == "flavor-ok"
    assert body["environment"]["defaults"]["keypair_name"] == "ops-key"
    assert body["environment"]["external_networks"][0]["id"] == "ext-net-1"


def test_stress_catalog_includes_lb_nginx_e2e_profile(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])

    class FakeConn:
        def authorize(self):
            return None

        class orchestration:
            @staticmethod
            def stacks():
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
        resp = client.get("/api/stress/catalog")

    body = resp.json()
    profile = next((item for item in body["catalog"]["profiles"] if item["key"] == "lb-nginx-e2e"), None)
    assert resp.status_code == 200
    assert profile is not None
    assert profile["label"] == "LB Nginx E2E"
    assert profile["default_vm_count"] == 3


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
        status_detail = client.get("/api/stress/status?include_details=1")
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
    assert status_body["status"]["servers"] == []
    assert status_body["status"]["distribution"] == []

    status_detail_body = status_detail.json()
    assert status_detail.status_code == 200
    assert status_detail_body["status"]["servers"][0]["host"] == "cmp-a.example.com"
    assert status_detail_body["status"]["distribution"][0]["share_pct"] == 50.0

    delete_body = deleted.json()
    assert deleted.status_code == 200
    assert delete_body["result"]["deleted"] is True
    assert state["deleted_ref"] == "stack-1"


def test_stress_catalog_returns_action_trace(monkeypatch):
    monkeypatch.setattr(web_server.DrainoServer, "start_refresh", lambda self, cached_nodes=None, silent=False: None)
    monkeypatch.setattr(web_server.k8s_ops, "get_nodes", lambda auth=None: [])
    stress_helpers._STRESS_ACTION_TRACE.clear()
    stress_helpers.record_stress_action("launch", "request_received", message="Received launch request in Draino", detail="burst")
    stress_helpers.record_stress_action("launch", "calling_heat", message="Calling Heat create_stack", detail="vibe-stress-20260409-141522")

    class FakeConn:
        def authorize(self):
            return None

        class orchestration:
            @staticmethod
            def stacks():
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
        resp = client.get("/api/stress/catalog")

    body = resp.json()
    assert resp.status_code == 200
    assert body["catalog"]["trace"][0]["stage"] == "calling_heat"
    assert body["catalog"]["trace"][1]["stage"] == "request_received"


def test_launch_stress_stack_auto_keypair_supplies_public_key(monkeypatch):
    monkeypatch.setattr(stress_helpers, "_stress_test_id", lambda: "20260409-210101")

    class FakeOrchestration:
        def __init__(self):
            self.create_kwargs = None

        def stacks(self):
            return []

        def create_stack(self, **kwargs):
            self.create_kwargs = kwargs
            return SimpleNamespace(id="stack-1")

    class FakeConn:
        def __init__(self):
            self.orchestration = FakeOrchestration()
            self.network = SimpleNamespace(
                networks=lambda: [SimpleNamespace(id="ext-net-1", name="public", is_router_external=True, to_dict=lambda: {"router:external": True})],
            )

        def authorize(self):
            return None

        class image:
            @staticmethod
            def images():
                return [SimpleNamespace(id="img-1", name="ubuntu", status="active", min_disk=10, min_ram=1024, disk_format="qcow2", visibility="public", properties={})]

        class compute:
            @staticmethod
            def flavors():
                return [SimpleNamespace(id="flavor-1", name="m1.small", vcpus=2, ram=2048, disk=20, ephemeral=0, swap=0, is_public=True)]

            @staticmethod
            def keypairs():
                return [SimpleNamespace(name="ops-key", fingerprint="fp-1", type="ssh")]

    fake_conn = FakeConn()
    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: fake_conn)
    monkeypatch.setattr(stress_helpers.openstack_ops, "_conn", lambda auth=None: fake_conn)
    monkeypatch.setattr(stress_helpers, "get_stress_status", lambda auth=None, include_details=False: {"active": True, "details_included": include_details})

    result = stress_helpers.launch_stress_stack(
        auth=None,
        compute_count=2,
        payload={
            "profile": "small-distribution",
            "vm_count": 1,
            "image_id": "img-1",
            "flavor_id": "flavor-1",
            "keypair_mode": "auto",
            "cidr_mode": "manual",
            "cidr": "10.77.71.0/24",
            "external_network_id": "ext-net-1",
        },
    )

    params = fake_conn.orchestration.create_kwargs["parameters"]
    template = fake_conn.orchestration.create_kwargs["template"]
    assert result["active"] is True
    assert params["key_name"] == "vibe-stress-20260409-210101-key"
    assert params["public_key"].startswith("ssh-rsa ")
    assert "\"public_key\": {\"get_param\": \"public_key\"}" in template


def test_launch_lb_nginx_e2e_stack_builds_octavia_resources(monkeypatch):
    monkeypatch.setattr(stress_helpers, "_stress_test_id", lambda: "20260411-200000")

    class FakeOrchestration:
        def __init__(self):
            self.create_kwargs = None

        def stacks(self):
            return []

        def create_stack(self, **kwargs):
            self.create_kwargs = kwargs
            return SimpleNamespace(id="stack-lb-1")

    class FakeConn:
        def __init__(self):
            self.orchestration = FakeOrchestration()
            self.network = SimpleNamespace(
                networks=lambda: [SimpleNamespace(id="ext-net-1", name="public", is_router_external=True, to_dict=lambda: {"router:external": True})],
            )

        def authorize(self):
            return None

        class image:
            @staticmethod
            def images():
                return [SimpleNamespace(id="img-1", name="ubuntu", status="active", min_disk=10, min_ram=1024, disk_format="qcow2", visibility="public", properties={})]

        class compute:
            @staticmethod
            def flavors():
                return [SimpleNamespace(id="flavor-1", name="m1.small", vcpus=2, ram=2048, disk=20, ephemeral=0, swap=0, is_public=True)]

            @staticmethod
            def keypairs():
                return [SimpleNamespace(name="ops-key", fingerprint="fp-1", type="ssh")]

    fake_conn = FakeConn()
    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: fake_conn)
    monkeypatch.setattr(stress_helpers.openstack_ops, "_conn", lambda auth=None: fake_conn)
    monkeypatch.setattr(stress_helpers, "get_stress_status", lambda auth=None, include_details=False: {"active": True, "details_included": include_details})

    result = stress_helpers.launch_stress_stack(
        auth=None,
        compute_count=3,
        payload={
            "profile": "lb-nginx-e2e",
            "vm_count": 3,
            "image_id": "img-1",
            "flavor_id": "flavor-1",
            "keypair_mode": "auto",
            "cidr_mode": "manual",
            "cidr": "10.88.44.0/24",
            "external_network_id": "ext-net-1",
        },
    )

    params = fake_conn.orchestration.create_kwargs["parameters"]
    template = fake_conn.orchestration.create_kwargs["template"]
    assert result["active"] is True
    assert params["profile"] == "lb-nginx-e2e"
    assert params["loadbalancer_name"] == "vibe-stress-20260411-200000-lb"
    assert params["listener_name"] == "vibe-stress-20260411-200000-listener-http"
    assert params["pool_name"] == "vibe-stress-20260411-200000-pool-http"
    assert "\"OS::Octavia::LoadBalancer\"" in template
    assert "\"OS::Octavia::Listener\"" in template
    assert "\"OS::Octavia::Pool\"" in template
    assert "\"OS::Octavia::PoolMember\"" in template
    assert "\"OS::Neutron::FloatingIP\"" in template
    assert "\"load_balancer_floating_ip\"" in template
    assert "\"port_range_min\": 80" in template
    assert "nginx" in template


def test_stress_status_uses_heat_events_for_timing(monkeypatch):
    class FakeOrchestration:
        @staticmethod
        def stacks():
            return [
                SimpleNamespace(
                    id="stack-1",
                    stack_name="vibe-stress-20260409-193659",
                    status="CREATE_COMPLETE",
                    creation_time="2026-04-09T19:37:03Z",
                    updated_time="2026-04-09T19:37:26Z",
                    description="stress",
                    parameters={"vm_count": 1, "profile": "small-distribution", "test_id": "20260409-193659"},
                    outputs=[],
                )
            ]

        @staticmethod
        def resources(stack_ref):
            assert stack_ref == "stack-1"
            return [
                SimpleNamespace(resource_name="stress_vm_01", resource_type="OS::Nova::Server", physical_resource_id="server-1", resource_status="CREATE_COMPLETE", updated_time="2026-04-09T19:37:03Z"),
                SimpleNamespace(resource_name="stress_port_01", resource_type="OS::Neutron::Port", physical_resource_id="port-1", resource_status="CREATE_COMPLETE", updated_time="2026-04-09T19:37:03Z"),
                SimpleNamespace(resource_name="stress_secgroup", resource_type="OS::Neutron::SecurityGroup", physical_resource_id="secgroup-1", resource_status="CREATE_COMPLETE", updated_time="2026-04-09T19:37:03Z"),
                SimpleNamespace(resource_name="stress_router_interface", resource_type="OS::Neutron::RouterInterface", physical_resource_id="iface-1", resource_status="CREATE_COMPLETE", updated_time="2026-04-09T19:37:03Z"),
                SimpleNamespace(resource_name="stress_subnet", resource_type="OS::Neutron::Subnet", physical_resource_id="subnet-1", resource_status="CREATE_COMPLETE", updated_time="2026-04-09T19:37:03Z"),
                SimpleNamespace(resource_name="stress_net", resource_type="OS::Neutron::Net", physical_resource_id="net-1", resource_status="CREATE_COMPLETE", updated_time="2026-04-09T19:37:03Z"),
                SimpleNamespace(resource_name="stress_router", resource_type="OS::Neutron::Router", physical_resource_id="router-1", resource_status="CREATE_COMPLETE", updated_time="2026-04-09T19:37:03Z"),
            ]

        @staticmethod
        def events(stack_ref):
            assert stack_ref == "stack-1"
            return [
                SimpleNamespace(logical_resource_id="stress_secgroup", resource_status="CREATE_IN_PROGRESS", event_time="2026-04-09T19:37:03Z"),
                SimpleNamespace(logical_resource_id="stress_secgroup", resource_status="CREATE_COMPLETE", event_time="2026-04-09T19:37:04Z"),
                SimpleNamespace(logical_resource_id="stress_net", resource_status="CREATE_IN_PROGRESS", event_time="2026-04-09T19:37:04Z"),
                SimpleNamespace(logical_resource_id="stress_net", resource_status="CREATE_COMPLETE", event_time="2026-04-09T19:37:06Z"),
                SimpleNamespace(logical_resource_id="stress_subnet", resource_status="CREATE_IN_PROGRESS", event_time="2026-04-09T19:37:06Z"),
                SimpleNamespace(logical_resource_id="stress_subnet", resource_status="CREATE_COMPLETE", event_time="2026-04-09T19:37:07Z"),
                SimpleNamespace(logical_resource_id="stress_port_01", resource_status="CREATE_IN_PROGRESS", event_time="2026-04-09T19:37:07Z"),
                SimpleNamespace(logical_resource_id="stress_port_01", resource_status="CREATE_COMPLETE", event_time="2026-04-09T19:37:08Z"),
                SimpleNamespace(logical_resource_id="stress_router", resource_status="CREATE_IN_PROGRESS", event_time="2026-04-09T19:37:05Z"),
                SimpleNamespace(logical_resource_id="stress_router", resource_status="CREATE_COMPLETE", event_time="2026-04-09T19:37:08Z"),
                SimpleNamespace(logical_resource_id="stress_router_interface", resource_status="CREATE_IN_PROGRESS", event_time="2026-04-09T19:37:08Z"),
                SimpleNamespace(logical_resource_id="stress_router_interface", resource_status="CREATE_COMPLETE", event_time="2026-04-09T19:37:12Z"),
                SimpleNamespace(logical_resource_id="stress_vm_01", resource_status="CREATE_IN_PROGRESS", event_time="2026-04-09T19:37:08Z"),
                SimpleNamespace(logical_resource_id="stress_vm_01", resource_status="CREATE_COMPLETE", event_time="2026-04-09T19:37:26Z"),
            ]

    class FakeConn:
        orchestration = FakeOrchestration()

        class compute:
            @staticmethod
            def get_server(server_id):
                assert server_id == "server-1"
                return SimpleNamespace(id="server-1", name="stress-vm-01", status="ACTIVE", compute_host="cmp-a.example.com", addresses={}, to_dict=lambda: {"addresses": {}})

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    status = stress_helpers.get_stress_status(auth=None)

    assert status["summary"]["plumbing_elapsed"] == "12s"
    assert status["summary"]["avg_vm_build"] == "18s"
    assert status["summary"]["p95_vm_build"] == "18s"
