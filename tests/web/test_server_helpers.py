from __future__ import annotations

from draino.models import NodeState
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


def test_get_routers_includes_gateway_and_interface_counts(monkeypatch):
    class FakeRouter:
        def __init__(self):
            self.id = "router-1"
            self.name = "tenant-router"
            self.status = "ACTIVE"
            self.is_admin_state_up = True
            self.project_id = "proj-1"

        def to_dict(self):
            return {
                "ha": True,
                "distributed": False,
                "routes": [{"destination": "10.1.0.0/24", "nexthop": "192.0.2.1"}],
                "external_gateway_info": {
                    "network_id": "ext-net-1",
                    "external_fixed_ips": [{"ip_address": "203.0.113.2", "subnet_id": "ext-subnet-1"}],
                },
            }

    class FakePort:
        def __init__(self, owner):
            self.device_owner = owner

    class FakeNetworkAPI:
        @staticmethod
        def routers():
            return [FakeRouter()]

        @staticmethod
        def ports(device_id=None):
            assert device_id == "router-1"
            return [FakePort("network:router_interface"), FakePort("network:router_gateway")]

        @staticmethod
        def get_network(network_id):
            assert network_id == "ext-net-1"
            return type("Network", (), {"name": "public"})()

    class FakeConn:
        network = FakeNetworkAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    items = web_server._get_routers(auth=None)

    assert items == [{
        "id": "router-1",
        "name": "tenant-router",
        "status": "ACTIVE",
        "admin_state": "up",
        "ha": True,
        "distributed": False,
        "project_id": "proj-1",
        "external_network_id": "ext-net-1",
        "external_network_name": "public",
        "external_gateway_ips": ["203.0.113.2"],
        "interface_count": 1,
        "route_count": 1,
    }]


def test_get_router_detail_includes_connected_subnets_and_gateway(monkeypatch):
    class FakeRouter:
        id = "router-1"
        name = "tenant-router"
        status = "ACTIVE"
        is_admin_state_up = True
        project_id = "proj-1"

        def to_dict(self):
            return {
                "ha": True,
                "distributed": True,
                "routes": [{"destination": "10.2.0.0/24", "nexthop": "192.0.2.1"}],
                "external_gateway_info": {
                    "network_id": "ext-net-1",
                    "enable_snat": True,
                    "external_fixed_ips": [{"ip_address": "203.0.113.2", "subnet_id": "ext-subnet-1"}],
                },
            }

    class FakePort:
        id = "port-1"
        device_owner = "network:router_interface"
        network_id = "net-1"
        fixed_ips = [{"subnet_id": "subnet-1", "ip_address": "10.0.0.1"}]

        def to_dict(self):
            return {}

    class FakeNetworkAPI:
        @staticmethod
        def get_router(router_id):
            assert router_id == "router-1"
            return FakeRouter()

        @staticmethod
        def ports(device_id=None):
            assert device_id == "router-1"
            return [FakePort()]

        @staticmethod
        def get_network(network_id):
            names = {"net-1": "tenant-net", "ext-net-1": "public"}
            return type("Network", (), {"name": names[network_id]})()

        @staticmethod
        def get_subnet(subnet_id):
            details = {
                "subnet-1": {"name": "tenant-subnet", "cidr": "10.0.0.0/24", "gateway_ip": "10.0.0.1", "is_dhcp_enabled": True},
                "ext-subnet-1": {"name": "public-subnet", "cidr": "203.0.113.0/24", "gateway_ip": "203.0.113.1", "is_dhcp_enabled": False},
            }[subnet_id]
            return type("Subnet", (), details)()

    class FakeConn:
        network = FakeNetworkAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    item = web_server._get_router_detail("router-1", auth=None)

    assert item["name"] == "tenant-router"
    assert item["ha"] is True
    assert item["distributed"] is True
    assert item["external_gateway"]["network_name"] == "public"
    assert item["external_gateway"]["external_fixed_ips"][0]["subnet_name"] == "public-subnet"
    assert item["connected_subnets"][0]["network_name"] == "tenant-net"
    assert item["connected_subnets"][0]["cidr"] == "10.0.0.0/24"
    assert item["routes"][0]["destination"] == "10.2.0.0/24"


def test_get_network_detail_includes_metadata_port_for_matching_subnet(monkeypatch):
    class FakeSubnet:
        def __init__(self, subnet_id, name, cidr):
            self.id = subnet_id
            self.name = name
            self.cidr = cidr
            self.ip_version = 4
            self.gateway_ip = "10.0.0.1"
            self.is_dhcp_enabled = True
            self.allocation_pools = []
            self.dns_nameservers = []
            self.host_routes = []

    class FakePort:
        id = "12345678-aaaa-bbbb-cccc-1234567890ab"
        device_owner = "network:distributed"
        device_id = "ovnmeta-net-1"
        fixed_ips = [{"subnet_id": "subnet-1", "ip_address": "10.0.0.2"}]

        def to_dict(self):
            return {}

    class FakeNetwork:
        id = "net-1"
        subnet_ids = ["subnet-1", "subnet-2"]

        def to_dict(self):
            return {}

    class FakeSegmentAPI:
        @staticmethod
        def __call__(*args, **kwargs):
            return []

    class FakeNetworkAPI:
        @staticmethod
        def get_network(network_id):
            assert network_id == "net-1"
            return FakeNetwork()

        @staticmethod
        def get_subnet(subnet_id):
            if subnet_id == "subnet-1":
                return FakeSubnet("subnet-1", "tenant-a", "10.0.0.0/24")
            return FakeSubnet("subnet-2", "tenant-b", "10.0.1.0/24")

        @staticmethod
        def ports(network_id=None):
            assert network_id == "net-1"
            return [FakePort()]

        @staticmethod
        def segments(network_id=None):
            return []

    class FakeConn:
        network = FakeNetworkAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    item = web_server._get_network_detail("net-1", auth=None)

    assert item["subnets"][0]["metadata_port"]["status"] == "ok"
    assert item["subnets"][0]["metadata_port"]["port_id"] == "12345678-aaaa-bbbb-cccc-1234567890ab"
    assert item["subnets"][0]["metadata_port"]["ip_address"] == "10.0.0.2"
    assert item["subnets"][1]["metadata_port"]["status"] == "missing"


def test_serialise_includes_k8s_taints():
    state = NodeState(k8s_name="node-1", hypervisor="hv-1")
    state.k8s_taints = [{"key": "key", "value": "value", "effect": "NoSchedule"}]
    state.is_edge = True
    state.node_agent_ready = False

    data = web_server._serialise(state)

    assert data["k8s_taints"] == [{"key": "key", "value": "value", "effect": "NoSchedule"}]
    assert data["is_edge"] is True
    assert data["node_agent_ready"] is False


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
