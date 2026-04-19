from __future__ import annotations

from types import SimpleNamespace

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


def test_get_networks_includes_router_connection_and_network_type_fallback(monkeypatch):
    class FakeNetwork:
        def __init__(self):
            self.id = "net-1"
            self.name = "tenant-net"
            self.status = "ACTIVE"
            self.is_admin_state_up = True
            self.is_shared = False
            self.project_id = "proj-1"
            self.subnet_ids = ["subnet-1"]
            self.is_router_external = False
            self.provider_network_type = "vxlan"

        def to_dict(self):
            return {"router:external": False}

    class FakePort:
        def __init__(self):
            self.network_id = "net-1"
            self.device_id = "router-1"
            self.device_owner = "network:router_interface"

        def to_dict(self):
            return {
                "network_id": "net-1",
                "device_id": "router-1",
                "device_owner": "network:router_interface",
            }

    class FakeNetworkAPI:
        @staticmethod
        def networks():
            return [FakeNetwork()]

        @staticmethod
        def ports():
            return [FakePort()]

    class FakeConn:
        network = FakeNetworkAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    items = web_server._get_networks(auth=None)

    assert items[0]["network_type"] == "vxlan"
    assert items[0]["router_connected"] is True
    assert items[0]["router_id"] == "router-1"


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
        def ports(device_id=None, network_id=None):
            if device_id == "router-1":
                return [FakePort()]
            if network_id == "net-1":
                return [
                    type("ConsumerPort", (), {"device_owner": "compute:nova", "device_id": "vm-1", "to_dict": lambda self: {}, "id": "consumer-1"})(),
                    type("ConsumerPort", (), {"device_owner": "Octavia", "device_id": "lb-1", "to_dict": lambda self: {}, "id": "consumer-2"})(),
                ]
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

        @staticmethod
        def ips():
            return []

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
    assert item["subnet_consumers"][0]["instance_count"] == 1
    assert item["subnet_consumers"][0]["load_balancer_count"] == 1
    assert item["joins"]["routed_instance_count"] == 1


def test_retype_volume_uses_volume_proxy_when_available(monkeypatch):
    calls = []

    class FakeVolumeProxy:
        @staticmethod
        def retype_volume(volume_id, target_type, migration_policy="on-demand"):
            calls.append((volume_id, target_type, migration_policy))

    class FakeConn:
        volume = FakeVolumeProxy()
        block_storage = None

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    result = web_server._retype_volume("vol-1", "gold-backend-b", auth=None)

    assert result == {
        "volume_id": "vol-1",
        "target_type": "gold-backend-b",
        "migration_policy": "on-demand",
        "status": "requested",
    }
    assert calls == [("vol-1", "gold-backend-b", "on-demand")]


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


def test_repair_subnet_metadata_port_creates_distributed_ovnmeta_port(monkeypatch):
    created = {}

    class FakeSubnet:
        project_id = "proj-1"

    class FakePort:
        id = "port-1"
        fixed_ips = [{"subnet_id": "subnet-1", "ip_address": "10.0.0.2"}]

        def to_dict(self):
            return {}

    class FakeNetworkAPI:
        @staticmethod
        def get_subnet(subnet_id):
            assert subnet_id == "subnet-1"
            return FakeSubnet()

        @staticmethod
        def create_port(**kwargs):
            created.update(kwargs)
            return FakePort()

    class FakeConn:
        network = FakeNetworkAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    item = web_server._repair_subnet_metadata_port("net-1", "subnet-1", auth=None)

    assert created == {
        "name": "metadata-port-repaired-by-vibeview",
        "network_id": "net-1",
        "fixed_ips": [{"subnet_id": "subnet-1"}],
        "device_owner": "network:distributed",
        "device_id": "ovnmeta-net-1",
        "project_id": "proj-1",
    }
    assert item["status"] == "ok"
    assert item["port_id"] == "port-1"
    assert item["ip_address"] == "10.0.0.2"


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
    assert web_server._normalise_image_digest("docker-pullable://ghcr.io/busterswt/vibeview@sha256:abc123") == "sha256:abc123"
    assert web_server._normalise_image_digest("sha256:def456") == "sha256:def456"
    assert web_server._normalise_image_digest("ghcr.io/busterswt/vibeview:main") is None


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

    digest = web_server._resolve_remote_track_digest("ghcr.io/busterswt/vibeview", "main")

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


def test_get_load_balancers_includes_floating_ip_and_counts(monkeypatch):
    class FakeLB:
        def __init__(self):
            self.id = "lb-1"
            self.name = "public-lb"
            self.operating_status = "ONLINE"
            self.provisioning_status = "ACTIVE"
            self.vip_address = "10.10.0.5"
            self.vip_port_id = "vip-port-1"
            self.project_id = "proj-1"
            self.listeners = [{"id": "listener-1"}, {"id": "listener-2"}]
            self.pools = [{"id": "pool-1"}]

        def to_dict(self):
            return {}

    class FakeAmphora:
        def __init__(self, lb_id):
            self.loadbalancer_id = lb_id

    class FakeFip:
        floating_ip_address = "198.51.100.25"

    class FakeNetworkAPI:
        @staticmethod
        def ips(port_id=None):
            assert port_id == "vip-port-1"
            return [FakeFip()]

        @staticmethod
        def get_port(port_id):
            assert port_id == "vip-port-1"
            return type("Port", (), {
                "id": "vip-port-1",
                "name": "octavia-lb-vip",
                "status": "ACTIVE",
                "network_id": "net-1",
                "fixed_ips": [{"subnet_id": "subnet-1", "ip_address": "10.10.0.5"}],
                "mac_address": "fa:16:3e:11:22:33",
                "device_owner": "Octavia",
                "device_id": "lb-1",
                "project_id": "proj-1",
                "is_admin_state_up": True,
                "to_dict": lambda self: {},
            })()

        @staticmethod
        def get_port(port_id):
            assert port_id == "vip-port-1"
            return SimpleNamespace(
                id="vip-port-1",
                name="octavia-lb-vip",
                status="ACTIVE",
                network_id="net-1",
                fixed_ips=[{"subnet_id": "subnet-1", "ip_address": "10.10.0.5"}],
                mac_address="fa:16:3e:11:22:33",
                device_owner="Octavia",
                device_id="lb-1",
                project_id="proj-1",
                is_admin_state_up=True,
                to_dict=lambda: {},
            )

        @staticmethod
        def get_port(port_id):
            assert port_id == "vip-port-1"
            return SimpleNamespace(
                id="vip-port-1",
                name="octavia-lb-vip",
                status="ACTIVE",
                network_id="net-1",
                fixed_ips=[{"subnet_id": "subnet-1", "ip_address": "10.10.0.5"}],
                mac_address="fa:16:3e:11:22:33",
                device_owner="Octavia",
                device_id="lb-1",
                project_id="proj-1",
                is_admin_state_up=True,
                to_dict=lambda: {},
            )

    class FakeLoadBalancerAPI:
        @staticmethod
        def load_balancers():
            return [FakeLB()]

        @staticmethod
        def amphorae():
            return [FakeAmphora("lb-1"), FakeAmphora("lb-1"), FakeAmphora("lb-2")]

    class FakeConn:
        network = FakeNetworkAPI()
        load_balancer = FakeLoadBalancerAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    items = web_server._get_load_balancers(auth=None)

    assert items == [{
        "id": "lb-1",
        "name": "public-lb",
        "operating_status": "ONLINE",
        "provisioning_status": "ACTIVE",
        "vip_address": "10.10.0.5",
        "floating_ip": "198.51.100.25",
        "vip_port_id": "vip-port-1",
        "project_id": "proj-1",
        "listener_count": 2,
        "pool_count": 1,
        "amphora_count": 2,
    }]


def test_get_load_balancer_detail_includes_listeners_pools_and_amphorae(monkeypatch):
    class FakeLB:
        id = "lb-1"
        name = "public-lb"
        operating_status = "ONLINE"
        provisioning_status = "ACTIVE"
        vip_address = "10.10.0.5"
        vip_port_id = "vip-port-1"
        vip_subnet_id = "subnet-1"
        project_id = "proj-1"
        flavor_id = "flavor-1"
        listeners = [{"id": "listener-1"}]
        pools = [{"id": "pool-1"}]

        def to_dict(self):
            return {}

    class FakeListener:
        id = "listener-1"
        name = "https"
        protocol = "TERMINATED_HTTPS"
        protocol_port = 443
        default_pool_id = "pool-1"

        def to_dict(self):
            return {}

    class FakePool:
        id = "pool-1"
        name = "web-pool"
        protocol = "HTTP"
        lb_algorithm = "ROUND_ROBIN"
        is_admin_state_up = True
        operating_status = "ONLINE"
        healthmonitor_id = "hm-1"
        session_persistence = {"type": "SOURCE_IP"}
        tls_enabled = True

        def to_dict(self):
            return {}

    class FakeHealthMonitor:
        type = "TCP"
        delay = 5
        timeout = 3
        max_retries = 3

        def to_dict(self):
            return {}

    class FakeMember:
        def __init__(self, member_id, address, status):
            self.id = member_id
            self.address = address
            self.protocol_port = 443
            self.operating_status = status

        def to_dict(self):
            return {}

    class FakeAmphora:
        def __init__(self, amp_id, role, compute_id, status, lb_ip):
            self.id = amp_id
            self.role = role
            self.compute_id = compute_id
            self.status = status
            self.loadbalancer_id = "lb-1"
            self.lb_network_ip = lb_ip
            self.ha_ip = "192.0.2.11"
            self.vrrp_ip = "192.0.2.12"

        def to_dict(self):
            return {}

    class FakeServer:
        def __init__(self, name, host, image_id):
            self.name = name
            self.compute_host = host
            self.image = {"id": image_id}

    class FakeFip:
        floating_ip_address = "198.51.100.25"

    class FakeNetworkAPI:
        @staticmethod
        def ips(port_id=None):
            assert port_id == "vip-port-1"
            return [FakeFip()]

        @staticmethod
        def get_port(port_id):
            assert port_id == "vip-port-1"
            return type("Port", (), {
                "id": "vip-port-1",
                "name": "octavia-lb-vip",
                "status": "ACTIVE",
                "network_id": "net-1",
                "fixed_ips": [{"subnet_id": "subnet-1", "ip_address": "10.10.0.5"}],
                "mac_address": "fa:16:3e:11:22:33",
                "device_owner": "Octavia",
                "device_id": "lb-1",
                "project_id": "proj-1",
                "is_admin_state_up": True,
                "to_dict": lambda self: {},
            })()

        @staticmethod
        def ports(device_id=None, network_id=None, fixed_ips=None):
            if fixed_ips == "ip_address=10.10.0.21":
                return [type("Port", (), {
                    "id": "member-port-1",
                    "device_owner": "compute:nova",
                    "device_id": "server-1",
                    "to_dict": lambda self: {"fixed_ips": [{"ip_address": "10.10.0.21"}]},
                })()]
            if fixed_ips == "ip_address=10.10.0.22":
                return [type("Port", (), {
                    "id": "member-port-2",
                    "device_owner": "compute:nova",
                    "device_id": "server-2",
                    "to_dict": lambda self: {"fixed_ips": [{"ip_address": "10.10.0.22"}]},
                })()]
            if network_id == "net-1":
                return [type("RouterPort", (), {"device_owner": "network:router_interface", "device_id": "router-1", "network_id": "net-1", "to_dict": lambda self: {}})()]
            if device_id is None and network_id is None and fixed_ips is None:
                return [type("RouterPort", (), {"device_owner": "network:router_interface", "device_id": "router-1", "network_id": "net-1", "to_dict": lambda self: {}})()]
            return []

        @staticmethod
        def get_network(network_id):
            assert network_id == "net-1"
            return type("Network", (), {"name": "tenant-net"})()

        @staticmethod
        def get_subnet(subnet_id):
            assert subnet_id == "subnet-1"
            return type("Subnet", (), {"name": "tenant-subnet", "cidr": "10.10.0.0/24", "gateway_ip": "10.10.0.1", "is_dhcp_enabled": True})()

        @staticmethod
        def get_router(router_id):
            assert router_id == "router-1"
            return type("Router", (), {"name": "tenant-router", "to_dict": lambda self: {}})()

    class FakeComputeAPI:
        @staticmethod
        def get_server(server_id):
            hosts = {
                "server-1": FakeServer("api-01", "compute-a.example", "image-1"),
                "server-2": FakeServer("api-02", "compute-b.example", "image-2"),
            }
            return hosts[server_id]

    class FakeLoadBalancerAPI:
        @staticmethod
        def get_load_balancer(lb_id):
            assert lb_id == "lb-1"
            return FakeLB()

        @staticmethod
        def listeners():
            return [FakeListener()]

        @staticmethod
        def pools():
            return [FakePool()]

        @staticmethod
        def members(pool_id):
            assert pool_id == "pool-1"
            return [FakeMember("member-1", "10.10.0.21", "ONLINE"), FakeMember("member-2", "10.10.0.22", "ERROR")]

        @staticmethod
        def get_health_monitor(hm_id):
            assert hm_id == "hm-1"
            return FakeHealthMonitor()

        @staticmethod
        def amphorae():
            return [
                FakeAmphora("amp-1", "MASTER", "server-1", "ALLOCATED", "192.0.2.10"),
                FakeAmphora("amp-2", "BACKUP", "server-2", "ALLOCATED", "192.0.2.20"),
            ]

    class FakeConn:
        network = FakeNetworkAPI()
        compute = FakeComputeAPI()
        load_balancer = FakeLoadBalancerAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "_server_host", lambda server: getattr(server, "compute_host", ""))

    item = web_server._get_load_balancer_detail("lb-1", auth=None)

    assert item["id"] == "lb-1"
    assert item["floating_ip"] == "198.51.100.25"
    assert item["vip_subnet_id"] == "subnet-1"
    assert item["vip_port"]["id"] == "vip-port-1"
    assert item["vip_port"]["name"] == "octavia-lb-vip"
    assert item["vip_port"]["network_id"] == "net-1"
    assert item["vip_port"]["network_name"] == "tenant-net"
    assert item["vip_port"]["router_name"] == "tenant-router"
    assert item["vip_port"]["subnet_id"] == "subnet-1"
    assert item["vip_port"]["ip_address"] == "10.10.0.5"
    assert item["vip_port"]["mac_address"] == "fa:16:3e:11:22:33"
    assert item["vip_port"]["device_id"] == "lb-1"
    assert item["vip_port"]["admin_state_up"] is True
    assert item["listeners"][0]["id"] == "listener-1"
    assert item["listeners"][0]["protocol_port"] == 443
    assert item["pools"][0]["member_count"] == 2
    assert item["pools"][0]["members"][0]["instance_id"] == "server-1"
    assert item["pools"][0]["members"][0]["instance_name"] == "api-01"
    assert item["pools"][0]["members"][1]["compute_host"] == "compute-b.example"
    assert item["pools"][0]["healthmonitor"] == "TCP\ndelay 5\ntimeout 3\nmax retries 3"
    assert item["pools"][0]["session_persistence"] == "SOURCE_IP"
    assert item["pools"][0]["tls_enabled"] is True
    assert len(item["amphorae"]) == 2
    assert item["amphorae"][0]["compute_host"] == "compute-a.example"
    assert item["distinct_host_count"] == 2
    assert item["ha_summary"] == "HA spread OK"


def test_get_security_groups_audits_open_world_and_unused_groups(monkeypatch):
    class FakeRule:
        def __init__(self, rule_id, direction, protocol, min_port, max_port, cidr="", ethertype="IPv4"):
            self.id = rule_id
            self.direction = direction
            self.protocol = protocol
            self.port_range_min = min_port
            self.port_range_max = max_port
            self.remote_ip_prefix = cidr
            self.ethertype = ethertype

        def to_dict(self):
            return {}

    class FakeSecurityGroup:
        def __init__(self, group_id, name, project_id, rules):
            self.id = group_id
            self.name = name
            self.project_id = project_id
            self.description = ""
            self.security_group_rules = rules
            self.stateful = True

        def to_dict(self):
            return {"revision_number": 3, "stateful": True}

    class FakeProject:
        def __init__(self, project_id, name):
            self.id = project_id
            self.name = name

    class FakePort:
        def __init__(self, port_id, group_ids, device_owner, device_id):
            self.id = port_id
            self.security_group_ids = group_ids
            self.device_owner = device_owner
            self.device_id = device_id
            self.project_id = "proj-1"
            self.network_id = "net-1"
            self.fixed_ips = [{"subnet_id": "subnet-1", "ip_address": "10.0.0.5"}]

        def to_dict(self):
            return {}

    critical = FakeSecurityGroup(
        "sg-1",
        "allow-all",
        "proj-1",
        [FakeRule("rule-1", "ingress", None, None, None, "0.0.0.0/0")],
    )
    unused = FakeSecurityGroup(
        "sg-2",
        "unused-sg",
        "proj-2",
        [FakeRule("rule-2", "egress", "tcp", 443, 443, "0.0.0.0/0")],
    )

    class FakeIdentityAPI:
        @staticmethod
        def projects():
            return [FakeProject("proj-1", "production"), FakeProject("proj-2", "staging")]

    class FakeNetworkAPI:
        @staticmethod
        def security_groups():
            return [critical, unused]

        @staticmethod
        def ports():
            return [FakePort("port-1", ["sg-1"], "compute:nova", "server-1")]

    class FakeConn:
        identity = FakeIdentityAPI()
        network = FakeNetworkAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    items = web_server._get_security_groups(auth=None)

    assert [item["id"] for item in items] == ["sg-1", "sg-2"]
    assert items[0]["audit"]["severity"] == "critical"
    assert items[0]["audit"]["has_any_any_open_world"] is True
    assert items[0]["attachment_instance_count"] == 1
    assert items[0]["project_name"] == "production"
    assert items[1]["audit"]["severity"] == "medium"
    assert items[1]["audit"]["has_unused"] is True
    assert items[1]["attachment_port_count"] == 0


def test_get_security_group_detail_includes_rules_and_attachments(monkeypatch):
    class FakeRule:
        def __init__(self, rule_id, direction, protocol, min_port, max_port, cidr="", ethertype="IPv4"):
            self.id = rule_id
            self.direction = direction
            self.protocol = protocol
            self.port_range_min = min_port
            self.port_range_max = max_port
            self.remote_ip_prefix = cidr
            self.ethertype = ethertype

        def to_dict(self):
            return {}

    group = SimpleNamespace(
        id="sg-1",
        name="web-frontend",
        description="web tier",
        project_id="proj-1",
        security_group_rules=[
            FakeRule("rule-1", "ingress", "tcp", 22, 22, "0.0.0.0/0"),
            SimpleNamespace(
                id="rule-remote-1",
                direction="ingress",
                protocol="tcp",
                port_range_min=443,
                port_range_max=443,
                remote_ip_prefix="",
                remote_group_id="sg-2",
                ethertype="IPv4",
                to_dict=lambda: {},
            ),
            FakeRule("rule-2", "egress", "tcp", 443, 443, "0.0.0.0/0"),
        ],
        stateful=True,
        to_dict=lambda: {"revision_number": 7, "stateful": True},
    )
    group_two = SimpleNamespace(
        id="sg-2",
        name="shared-backend",
        description="backend tier",
        project_id="proj-1",
        security_group_rules=[
            SimpleNamespace(
                id="rule-remote-2",
                direction="ingress",
                protocol="tcp",
                port_range_min=8443,
                port_range_max=8443,
                remote_ip_prefix="",
                remote_group_id="sg-3",
                ethertype="IPv4",
                to_dict=lambda: {},
            ),
        ],
        stateful=True,
        to_dict=lambda: {"revision_number": 8, "stateful": True},
    )
    group_three = SimpleNamespace(
        id="sg-3",
        name="database",
        description="db tier",
        project_id="proj-1",
        security_group_rules=[],
        stateful=True,
        to_dict=lambda: {"revision_number": 9, "stateful": True},
    )
    referrer = SimpleNamespace(
        id="sg-4",
        name="ingress-proxy",
        description="proxy tier",
        project_id="proj-1",
        security_group_rules=[
            SimpleNamespace(
                id="rule-remote-4",
                direction="ingress",
                protocol="tcp",
                port_range_min=443,
                port_range_max=443,
                remote_ip_prefix="",
                remote_group_id="sg-1",
                ethertype="IPv4",
                to_dict=lambda: {},
            ),
        ],
        stateful=True,
        to_dict=lambda: {"revision_number": 10, "stateful": True},
    )

    class FakeProject:
        id = "proj-1"
        name = "production"

    class FakePort:
        id = "port-1"
        security_group_ids = ["sg-1"]
        device_owner = "compute:nova"
        device_id = "server-1"
        project_id = "proj-1"
        network_id = "net-1"
        fixed_ips = [{"subnet_id": "subnet-1", "ip_address": "10.0.0.5"}]

        def to_dict(self):
            return {}

    class FakeIdentityAPI:
        @staticmethod
        def projects():
            return [FakeProject()]

    class FakeServer:
        id = "server-1"
        name = "api-01"
        compute_host = "compute-a.example"

        def to_dict(self):
            return {}

    class FakeNetworkAPI:
        @staticmethod
        def get_security_group(group_id):
            assert group_id == "sg-1"
            return group

        @staticmethod
        def security_groups():
            return [group, group_two, group_three, referrer]

        @staticmethod
        def ports():
            return [FakePort()]

        @staticmethod
        def get_network(network_id):
            assert network_id == "net-1"
            return SimpleNamespace(name="tenant-net")

    class FakeComputeAPI:
        @staticmethod
        def get_server(server_id):
            assert server_id == "server-1"
            return FakeServer()

    class FakeConn:
        identity = FakeIdentityAPI()
        network = FakeNetworkAPI()
        compute = FakeComputeAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())
    monkeypatch.setattr(web_server.openstack_ops, "_server_host", lambda server: getattr(server, "compute_host", ""))

    item = web_server._get_security_group_detail("sg-1", auth=None)

    assert item["audit"]["severity"] == "high"
    assert item["flagged_rule_count"] == 1
    assert item["attachments"][0]["port_id"] == "port-1"
    assert item["attachments"][0]["device_id"] == "server-1"
    assert item["attachments"][0]["network_name"] == "tenant-net"
    assert item["attachments"][0]["instance_name"] == "api-01"
    assert item["attachments"][0]["compute_host"] == "compute-a.example"
    assert item["rules"][0]["audit"]["flagged"] is True
    assert item["rules"][0]["audit"]["summary"] == "tcp:22 0.0.0.0/0"
    assert item["rules"][1]["audit"]["flagged"] is False
    assert item["remote_group_fanout"]["direct_group_count"] == 1
    assert item["reference_graph_depth"] == 2
    assert item["referenced_by"][0]["id"] == "sg-4"


def test_get_volume_detail_includes_retype_targets_and_recovery_lists(monkeypatch):
    class FakeVolume:
        id = "vol-1"
        name = "db-prod-01-root"
        status = "in-use"
        size = 120
        description = "database root disk"
        volume_type = "gold-rbd-a"
        project_id = "proj-1"
        is_bootable = True
        encrypted = False
        is_multiattach = False
        availability_zone = "storage-a"
        created_at = "2026-04-18T00:00:00Z"
        updated_at = "2026-04-18T01:00:00Z"
        attachments = [{
            "server_id": "server-1",
            "host_name": "compute-a.example",
            "device": "/dev/vda",
            "attached_at": "2026-04-18T00:05:00Z",
        }]

        def to_dict(self):
            return {
                "os-vol-host-attr:host": "cinder-a@rbd-a#pool1",
                "metadata": {"owner": "db-team"},
            }

    class FakeType:
        def __init__(self, type_id, name, backend):
            self.id = type_id
            self.name = name
            self.description = f"{name} desc"
            self.is_public = True
            self.extra_specs = {"volume_backend_name": backend}

        def to_dict(self):
            return {
                "id": self.id,
                "name": self.name,
                "description": self.description,
                "is_public": True,
                "extra_specs": self.extra_specs,
            }

    class FakeSnapshot:
        id = "snap-1"
        name = "snap-db"
        status = "available"
        size = 120
        volume_id = "vol-1"
        created_at = "2026-04-18T02:00:00Z"

        def to_dict(self):
            return {"project_id": "proj-1"}

    class FakeBackup:
        id = "backup-1"
        name = "backup-db"
        status = "available"
        size = 120
        volume_id = "vol-1"
        project_id = "proj-1"
        created_at = "2026-04-18T03:00:00Z"
        is_incremental = False

        def to_dict(self):
            return {}

    class FakeServer:
        name = "vm-db-01"

        def to_dict(self):
            return {}

    class FakeVolumeAPI:
        @staticmethod
        def get_volume(volume_id):
            assert volume_id == "vol-1"
            return FakeVolume()

        @staticmethod
        def get_type(ref):
            mapping = {
                "gold-rbd-a": FakeType("type-a", "gold-rbd-a", "rbd-a"),
                "type-a": FakeType("type-a", "gold-rbd-a", "rbd-a"),
                "type-b": FakeType("type-b", "gold-rbd-b", "rbd-b"),
            }
            return mapping.get(ref)

        @staticmethod
        def types():
            return [FakeType("type-a", "gold-rbd-a", "rbd-a"), FakeType("type-b", "gold-rbd-b", "rbd-b")]

        @staticmethod
        def snapshots(details=False):
            assert details is False
            return [FakeSnapshot()]

        @staticmethod
        def backups(details=False):
            assert details is False
            return [FakeBackup()]

    class FakeComputeAPI:
        @staticmethod
        def get_server(server_id):
            assert server_id == "server-1"
            return FakeServer()

    class FakeConn:
        volume = FakeVolumeAPI()
        compute = FakeComputeAPI()

    monkeypatch.setattr(web_server.openstack_ops, "_conn", lambda auth=None: FakeConn())

    item = web_server._get_volume_detail("vol-1", auth=None)

    assert item["backend_name"] == "rbd-a"
    assert item["backend_pool"] == "pool1"
    assert item["volume_type_detail"]["backend_name"] == "rbd-a"
    assert item["available_volume_types"][1]["backend_name"] == "rbd-b"
    assert item["attachments"][0]["server_name"] == "vm-db-01"
    assert item["snapshot_count"] == 1
    assert item["backup_count"] == 1
    assert item["snapshots"][0]["id"] == "snap-1"
    assert item["backups"][0]["id"] == "backup-1"
    assert item["control_plane_complexity"]["level"] == "elevated"
