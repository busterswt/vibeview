"""Node-agent-backed inventory helpers."""
from __future__ import annotations

from .. import node_agent_client


def get_etcd_service_status(node_name: str, hostname: str | None = None) -> dict:
    """Return etcd service probe status and any validation error."""
    try:
        result = node_agent_client.get_etcd_status(node_name)
        return {
            "active": result.get("active"),
            "error": None,
        }
    except Exception as exc:
        return {
            "active": None,
            "error": str(exc),
        }


def check_etcd_service(node_name: str, hostname: str | None = None) -> bool | None:
    """Check whether the etcd systemd service is active via the node agent."""
    return get_etcd_service_status(node_name, hostname).get("active")


def get_node_host_signals(node_name: str, hostname: str | None = None) -> dict:
    """Return lightweight reboot/kernel signals for a node via the node agent."""
    try:
        return node_agent_client.get_host_signals(node_name)
    except Exception as exc:
        return {
            "kernel_version": None,
            "latest_kernel_version": None,
            "reboot_required": False,
            "error": str(exc),
        }


def get_node_monitor_metrics(node_name: str, hostname: str | None = None) -> dict:
    """Return lightweight host load, memory, and disk metrics via the node agent."""
    try:
        return node_agent_client.get_host_metrics(node_name)
    except Exception as exc:
        return {
            "current": None,
            "history": [],
            "error": str(exc),
        }


def get_node_network_stats(node_name: str, hostname: str | None = None) -> dict:
    """Return lightweight per-interface rx/tx counters and rates via the node agent."""
    try:
        return node_agent_client.get_host_network_stats(node_name)
    except Exception as exc:
        return {
            "interfaces": [],
            "error": str(exc),
        }


def get_node_hardware_info(node_name: str, hostname: str | None = None) -> dict:
    """Return chassis, CPU, and RAM hardware details via the node agent."""
    result: dict = {
        "hostname": None,
        "architecture": None,
        "kernel_version": None,
        "uptime": None,
        "vendor": None,
        "product": None,
        "bios_version": None,
        "cpu_model": None,
        "cpu_sockets": None,
        "cpu_cores_per_socket": None,
        "cpu_threads_per_core": None,
        "ram_type": None,
        "ram_speed": None,
        "ram_total_gb": None,
        "ram_slots_used": None,
        "ram_manufacturer": None,
        "error": None,
    }

    try:
        result.update(node_agent_client.get_host_detail(node_name))
        result.setdefault("error", None)
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
