from __future__ import annotations

import json
import subprocess
import urllib.error
from types import SimpleNamespace

from draino import node_agent_client, node_agent_host_ops, node_agent_metrics_ops
from draino.operations import k8s_ops


def test_get_node_hardware_info_uses_node_agent(monkeypatch):
    monkeypatch.setattr(k8s_ops.node_agent_client, "enabled", lambda: True)
    monkeypatch.setattr(
        k8s_ops.node_agent_client,
        "get_host_detail",
        lambda node_name: {
            "hostname": node_name,
            "architecture": "x86_64",
            "kernel_version": "6.8.0",
            "uptime": "3 days, 2 hours",
            "vendor": "Dell Inc.",
            "product": "PowerEdge",
            "bios_version": "1.2.3",
            "cpu_model": "Xeon",
            "cpu_sockets": 2,
            "cpu_cores_per_socket": 16,
            "cpu_threads_per_core": 2,
            "ram_type": "DDR5",
            "ram_speed": "4800 MT/s",
            "ram_total_gb": 512,
            "ram_slots_used": 16,
            "ram_manufacturer": "Samsung",
            "error": None,
        },
    )

    result = k8s_ops.get_node_hardware_info("node-1", "hv-1")

    assert result["hostname"] == "node-1"
    assert result["architecture"] == "x86_64"
    assert result["uptime"] == "3 days, 2 hours"
    assert result["vendor"] == "Dell Inc."


def test_get_network_interfaces_uses_node_agent(monkeypatch):
    monkeypatch.setattr(k8s_ops.node_agent_client, "enabled", lambda: True)
    monkeypatch.setattr(
        k8s_ops.node_agent_client,
        "get_network_interfaces",
        lambda node_name: {
            "interfaces": [{"name": "bond0", "type": "bond", "members": ["eth0", "eth1"]}],
            "error": None,
        },
    )

    result = k8s_ops.get_node_network_interfaces("node-1", "hv-1")

    assert result["error"] is None
    assert result["interfaces"][0]["name"] == "bond0"


def test_check_etcd_service_uses_node_agent(monkeypatch):
    monkeypatch.setattr(k8s_ops.node_agent_client, "enabled", lambda: True)
    monkeypatch.setattr(
        k8s_ops.node_agent_client,
        "get_etcd_status",
        lambda node_name: {"active": True, "error": None},
    )

    assert k8s_ops.check_etcd_service("node-1", "hv-1") is True


def test_get_node_host_signals_uses_node_agent(monkeypatch):
    monkeypatch.setattr(k8s_ops.node_agent_client, "enabled", lambda: True)
    monkeypatch.setattr(
        k8s_ops.node_agent_client,
        "get_host_signals",
        lambda node_name: {
            "kernel_version": "6.8.0",
            "latest_kernel_version": "6.8.12",
            "reboot_required": True,
            "error": None,
        },
    )

    result = k8s_ops.get_node_host_signals("node-1", "hv-1")

    assert result["kernel_version"] == "6.8.0"
    assert result["latest_kernel_version"] == "6.8.12"
    assert result["reboot_required"] is True


def test_get_mariadb_node_names_matches_pod_name_label_and_image(monkeypatch):
    pods = [
        SimpleNamespace(
            metadata=SimpleNamespace(name="mariadb-cluster-0", labels={}, namespace="openstack"),
            spec=SimpleNamespace(node_name="node-a", containers=[]),
            status=SimpleNamespace(phase="Running"),
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(name="db-0", labels={"app.kubernetes.io/name": "mariadb-cluster"}, namespace="openstack"),
            spec=SimpleNamespace(node_name="node-b", containers=[]),
            status=SimpleNamespace(phase="Running"),
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(name="galera-helper", labels={}, namespace="openstack"),
            spec=SimpleNamespace(node_name="node-c", containers=[SimpleNamespace(image="quay.io/example/galera:latest")]),
            status=SimpleNamespace(phase="Running"),
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(name="rabbitmq-0", labels={}, namespace="openstack"),
            spec=SimpleNamespace(node_name="node-d", containers=[SimpleNamespace(image="rabbitmq:3")]),
            status=SimpleNamespace(phase="Running"),
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(name="mariadb-backup-0", labels={"app.kubernetes.io/name": "mariadb-cluster-backup"}, namespace="openstack"),
            spec=SimpleNamespace(node_name="node-e", containers=[SimpleNamespace(image="mariadb:11")]),
            status=SimpleNamespace(phase="Running"),
        ),
        SimpleNamespace(
            metadata=SimpleNamespace(name="mariadb-restore-0", labels={}, namespace="openstack"),
            spec=SimpleNamespace(node_name="node-f", containers=[SimpleNamespace(image="quay.io/example/mariadb-restore:latest")]),
            status=SimpleNamespace(phase="Running"),
        ),
    ]

    class FakeCoreV1Api:
        def __init__(self, api_client):
            pass

        def list_pod_for_all_namespaces(self):
            return SimpleNamespace(items=pods)

    monkeypatch.setattr(k8s_ops, "_api_client", lambda auth=None: object())
    monkeypatch.setattr(k8s_ops.client, "CoreV1Api", FakeCoreV1Api)

    result = k8s_ops.get_mariadb_node_names()

    assert result == {"node-a", "node-b", "node-c"}


def test_get_node_monitor_metrics_uses_node_agent(monkeypatch):
    monkeypatch.setattr(
        k8s_ops.node_agent_client,
        "get_host_metrics",
        lambda node_name: {
            "current": {
                "load1": 1.2,
                "memory_used_percent": 67.5,
                "filesystems": [{"mount": "/", "available_kb": 1000, "used_percent": 82}],
            },
            "history": [{"timestamp": 1, "load1": 1.2, "memory_used_percent": 67.5, "root_used_percent": 82}],
            "error": None,
        },
    )

    result = k8s_ops.get_node_monitor_metrics("node-1", "hv-1")

    assert result["current"]["load1"] == 1.2
    assert result["current"]["filesystems"][0]["mount"] == "/"
    assert result["history"][0]["root_used_percent"] == 82


def test_node_agent_host_metrics_parses_load_memory_and_disk(monkeypatch):
    stdout = "\n".join([
        "__LOAD__",
        "1.23 0.98 0.75 2/100 12345",
        "__MEM__",
        "MemTotal:       16000000 kB",
        "MemAvailable:    6000000 kB",
        "__CPU__",
        "16",
        "__UPTIME__",
        "7200.00",
        "__DF__",
        "/|1000000|700000|300000|70%",
        "/var|500000|200000|300000|40%",
        "__END__",
    ])

    monkeypatch.setattr(
        node_agent_metrics_ops,
        "_run_host_shell",
        lambda script, timeout=10: subprocess.CompletedProcess(args=["sh"], returncode=0, stdout=stdout, stderr=""),
    )
    monkeypatch.setattr(node_agent_metrics_ops.time, "time", lambda: 1000.0)
    node_agent_metrics_ops._host_metrics_cache = None
    node_agent_metrics_ops._host_metrics_history.clear()

    result = node_agent_metrics_ops._get_host_metrics()

    assert result["error"] is None
    assert result["current"]["load1"] == 1.23
    assert result["current"]["cpu_count"] == 16
    assert result["current"]["memory_used_kb"] == 10000000
    assert result["current"]["memory_used_percent"] == 62.5
    assert result["current"]["filesystems"][0]["mount"] == "/"
    assert result["history"][0]["root_used_percent"] == 70


def test_node_agent_host_metrics_dedupes_duplicate_mounts(monkeypatch):
    stdout = "\n".join([
        "__LOAD__",
        "1.00 0.50 0.25 1/100 12345",
        "__MEM__",
        "MemTotal:       16000000 kB",
        "MemAvailable:    8000000 kB",
        "__CPU__",
        "16",
        "__UPTIME__",
        "7200.00",
        "__DF__",
        "/|1000000|700000|300000|70%",
        "/|1000000|700000|300000|70%",
        "/|1000000|700000|300000|70%",
        "__END__",
    ])

    monkeypatch.setattr(
        node_agent_metrics_ops,
        "_run_host_shell",
        lambda script, timeout=10: subprocess.CompletedProcess(args=["sh"], returncode=0, stdout=stdout, stderr=""),
    )
    monkeypatch.setattr(node_agent_metrics_ops.time, "time", lambda: 1000.0)
    node_agent_metrics_ops._host_metrics_cache = None
    node_agent_metrics_ops._host_metrics_history.clear()

    result = node_agent_metrics_ops._get_host_metrics()

    assert result["error"] is None
    assert len(result["current"]["filesystems"]) == 1
    assert result["current"]["filesystems"][0]["mount"] == "/"


def test_get_node_network_stats_uses_node_agent(monkeypatch):
    monkeypatch.setattr(
        k8s_ops.node_agent_client,
        "get_host_network_stats",
        lambda node_name: {
            "interfaces": [{"name": "bond0", "rx_bytes_per_second": 125000000.0, "tx_bytes_per_second": 62500000.0}],
            "error": None,
        },
    )

    result = k8s_ops.get_node_network_stats("node-1", "hv-1")

    assert result["interfaces"][0]["name"] == "bond0"
    assert result["interfaces"][0]["rx_bytes_per_second"] == 125000000.0


def test_node_agent_host_network_stats_computes_rates(monkeypatch):
    outputs = iter([
        "eth0|1000|2000\nbond0|5000|7000\n",
        "eth0|3000|5000\nbond0|9000|11000\n",
    ])
    times = iter([1000.0, 1002.0])

    monkeypatch.setattr(
        node_agent_metrics_ops,
        "_run_host_shell",
        lambda script, timeout=10: subprocess.CompletedProcess(args=["sh"], returncode=0, stdout=next(outputs), stderr=""),
    )
    monkeypatch.setattr(node_agent_metrics_ops.time, "time", lambda: next(times))
    node_agent_metrics_ops._host_network_prev_samples.clear()

    first = node_agent_metrics_ops._get_host_network_stats()
    second = node_agent_metrics_ops._get_host_network_stats()

    assert first["error"] is None
    assert first["interfaces"][0]["rx_bytes_per_second"] is None
    eth0 = next(item for item in second["interfaces"] if item["name"] == "eth0")
    assert eth0["rx_bytes_per_second"] == 1000.0
    assert eth0["tx_bytes_per_second"] == 1500.0


def test_get_ovn_edge_nodes_reads_other_config_from_json(monkeypatch):
    payload = {
        "headings": [
            "_uuid",
            "encaps",
            "external_ids",
            "hostname",
            "name",
            "nb_cfg",
            "other_config",
            "transport_zones",
            "vtep_logical_switches",
        ],
        "data": [
            [
                ["uuid", "a"],
                ["uuid", "b"],
                ["map", [["vendor", "kube-ovn"]]],
                "node-1.example.com",
                "chassis-a",
                0,
                ["map", [["ovn-cms-options", "enable-chassis-as-gw,availability-zones=az1"]]],
                ["set", []],
                ["set", []],
            ],
            [
                ["uuid", "c"],
                ["uuid", "d"],
                ["map", [["vendor", "kube-ovn"]]],
                "node-2.example.com",
                "chassis-b",
                0,
                ["map", [["ovn-cms-options", ""]]],
                ["set", []],
                ["set", []],
            ],
        ],
    }

    monkeypatch.setattr(
        k8s_ops.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    result = k8s_ops.get_ovn_edge_nodes()

    assert result == {"node-1.example.com"}


def test_node_agent_client_uses_pod_ip_and_disables_hostname_check(monkeypatch, tmp_path):
    token_file = tmp_path / "token"
    ca_file = tmp_path / "ca.crt"
    token_file.write_text("secret-token", encoding="utf-8")
    ca_file.write_text("ca", encoding="utf-8")

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        node_agent_client,
        "_discover_agent_pod_host",
        lambda node_name, cfg: "10.0.0.42",
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps({"active": True, "error": None}).encode("utf-8")

    class FakeSSLContext:
        def __init__(self):
            self.check_hostname = True

    ssl_ctx = FakeSSLContext()

    def fake_urlopen(request, timeout=None, context=None):
        captured["url"] = request.full_url
        captured["auth"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        captured["context"] = context
        return FakeResponse()

    monkeypatch.setattr(node_agent_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(node_agent_client.ssl, "create_default_context", lambda cafile=None: ssl_ctx)

    result = node_agent_client._request_json(
        "node-1",
        "GET",
        "/host/etcd",
        agent_config=node_agent_client.NodeAgentConfig(
            namespace="draino",
            service_name="trustmebro-agent",
            label_selector="app=node-agent",
            port=8443,
            ca_file=str(ca_file),
            token_file=str(token_file),
            request_timeout=5.0,
        ),
    )

    assert result["active"] is True
    assert captured["url"] == "https://10.0.0.42:8443/host/etcd"
    assert captured["auth"] == "Bearer secret-token"
    assert captured["timeout"] == 5.0
    assert captured["context"] is ssl_ctx
    assert ssl_ctx.check_hostname is False


def test_discover_agent_pod_host_uses_cache_until_ttl(monkeypatch):
    node_agent_client._endpoint_cache.clear()

    calls: list[str] = []
    now_values = iter([1000.0, 1000.0, 1005.0])

    class FakePodStatus:
        phase = "Running"
        pod_ip = "10.0.0.42"
        conditions = [type("Cond", (), {"type": "Ready", "status": "True"})()]

    class FakePod:
        status = FakePodStatus()

    class FakeCore:
        def list_namespaced_pod(self, namespace=None, label_selector=None, field_selector=None):
            calls.append(field_selector)
            return type("Pods", (), {"items": [FakePod()]})()

    monkeypatch.setattr(node_agent_client.time, "time", lambda: next(now_values))
    monkeypatch.setattr(node_agent_client.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(node_agent_client.client, "CoreV1Api", lambda: FakeCore())

    cfg = node_agent_client.NodeAgentConfig(namespace="draino", service_name="trustmebro-agent", label_selector="app=node-agent", endpoint_ttl=30.0)

    first = node_agent_client._discover_agent_pod_host("node-1", cfg)
    second = node_agent_client._discover_agent_pod_host("node-1", cfg)

    assert first == "10.0.0.42"
    assert second == "10.0.0.42"
    assert calls == ["spec.nodeName=node-1"]


def test_discover_agent_pod_host_refreshes_after_ttl(monkeypatch):
    node_agent_client._endpoint_cache.clear()

    calls: list[str] = []
    now_values = iter([1000.0, 1000.0, 1035.0, 1035.0])
    pod_ips = iter(["10.0.0.42", "10.0.0.77"])

    class FakeCore:
        def list_namespaced_pod(self, namespace=None, label_selector=None, field_selector=None):
            calls.append(field_selector)
            pod_ip = next(pod_ips)
            status = type(
                "FakePodStatus",
                (),
                {
                    "phase": "Running",
                    "pod_ip": pod_ip,
                    "conditions": [type("Cond", (), {"type": "Ready", "status": "True"})()],
                },
            )()
            return type("Pods", (), {"items": [type("FakePod", (), {"status": status})()]})()

    monkeypatch.setattr(node_agent_client.time, "time", lambda: next(now_values))
    monkeypatch.setattr(node_agent_client.config, "load_incluster_config", lambda: None)
    monkeypatch.setattr(node_agent_client.client, "CoreV1Api", lambda: FakeCore())

    cfg = node_agent_client.NodeAgentConfig(namespace="draino", service_name="trustmebro-agent", label_selector="app=node-agent", endpoint_ttl=30.0)

    first = node_agent_client._discover_agent_pod_host("node-1", cfg)
    second = node_agent_client._discover_agent_pod_host("node-1", cfg)

    assert first == "10.0.0.42"
    assert second == "10.0.0.77"
    assert calls == ["spec.nodeName=node-1", "spec.nodeName=node-1"]


def test_request_json_invalidates_cached_host_on_url_error(monkeypatch, tmp_path):
    node_agent_client._endpoint_cache.clear()

    token_file = tmp_path / "token"
    ca_file = tmp_path / "ca.crt"
    token_file.write_text("secret-token", encoding="utf-8")
    ca_file.write_text("ca", encoding="utf-8")

    monkeypatch.setattr(node_agent_client, "_discover_agent_pod_host", lambda node_name, cfg: "10.0.0.42")
    monkeypatch.setattr(node_agent_client.ssl, "create_default_context", lambda cafile=None: type("Ctx", (), {"check_hostname": True})())
    monkeypatch.setattr(
        node_agent_client.urllib.request,
        "urlopen",
        lambda request, timeout=None, context=None: (_ for _ in ()).throw(
            urllib.error.URLError("connection refused")
        ),
    )

    node_agent_client._endpoint_cache["node-1"] = ("10.0.0.42", 9999999999.0)

    try:
        node_agent_client._request_json(
            "node-1",
            "GET",
            "/host/etcd",
            agent_config=node_agent_client.NodeAgentConfig(
                namespace="draino",
                service_name="trustmebro-agent",
                label_selector="app=node-agent",
                port=8443,
                ca_file=str(ca_file),
                token_file=str(token_file),
                request_timeout=5.0,
            ),
        )
    except RuntimeError as exc:
        assert "connection refused" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert "node-1" not in node_agent_client._endpoint_cache


def test_node_agent_static_host_detail_cache_reuses_expensive_probe(monkeypatch):
    node_agent_host_ops._host_static_detail_cache = None
    calls = {"static": 0}

    monkeypatch.setattr(node_agent_host_ops.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        node_agent_host_ops,
        "_get_static_host_detail",
        lambda: calls.__setitem__("static", calls["static"] + 1) or {"vendor": "Dell", "error": None},
    )

    first = node_agent_host_ops._get_cached_static_host_detail()
    second = node_agent_host_ops._get_cached_static_host_detail()

    assert first["vendor"] == "Dell"
    assert second["vendor"] == "Dell"
    assert calls["static"] == 1


def test_node_agent_host_detail_combines_dynamic_with_cached_static(monkeypatch):
    node_agent_host_ops._host_static_detail_cache = None

    monkeypatch.setattr(node_agent_host_ops, "_get_cached_static_host_detail", lambda now=None: {"vendor": "Dell", "error": None})
    monkeypatch.setattr(
        node_agent_host_ops,
        "_get_dynamic_host_detail",
        lambda: {
            "hostname": "node-1",
            "architecture": "x86_64",
            "kernel_version": "6.8.0",
            "uptime": "3 days",
            "error": None,
        },
    )

    result = node_agent_host_ops._get_host_detail()

    assert result["hostname"] == "node-1"
    assert result["kernel_version"] == "6.8.0"
    assert result["vendor"] == "Dell"
    assert result["error"] is None
