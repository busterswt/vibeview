"""OpenStack operations: Nova compute + Octavia load balancers."""
from __future__ import annotations

import time
from typing import Callable, Optional

import openstack
import openstack.connection

LogFn = Callable[[str], None]

_CLOUD: str | None = None


def configure(cloud: str | None = None) -> None:
    global _CLOUD
    _CLOUD = cloud


def _conn() -> openstack.connection.Connection:
    return openstack.connect(cloud=_CLOUD)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _server_host(server) -> str | None:
    """Extract the compute host from a Nova server object.

    openstacksdk maps OS-EXT-SRV-ATTR:host to the .host attribute.
    Try every known alias so we're robust across SDK versions.
    """
    return (
        getattr(server, "host", None)
        or server.get("OS-EXT-SRV-ATTR:host")
        or server.get("Host")
    )


def _servers_on_host(conn, hypervisor: str) -> list:
    """Return all Nova servers scheduled on *hypervisor*.

    The Nova list-servers `host` query parameter is unreliable across
    deployments, so we fetch all servers (admin, all_projects) and filter
    client-side using the OS-EXT-SRV-ATTR:host extended attribute.
    """
    return [
        s for s in conn.compute.servers(all_projects=True)
        if _server_host(s) == hypervisor
    ]


# ── Node summary (for the node-list panel) ────────────────────────────────────

def get_host_summary(hypervisor: str) -> dict:
    """Return a lightweight summary for the node panel.

    Returns a dict with keys:
      compute_status : str | None   — "up" | "disabled" | "down"
      amphora_count  : int | None
      vm_count       : int | None   (non-amphora instances)
    """
    conn = _conn()

    compute_status: Optional[str] = None
    amphora_count:  Optional[int] = None
    vm_count:       Optional[int] = None

    # ── Compute service status (status = admin enabled/disabled;
    #                            state  = daemon up/down) ──────────────────
    try:
        services = list(conn.compute.services(host=hypervisor, binary="nova-compute"))
        if services:
            svc = services[0]
            svc_state  = (getattr(svc, "state",  "up")       or "up").lower()
            svc_status = (getattr(svc, "status", "enabled")  or "enabled").lower()
            if svc_state == "down":
                compute_status = "down"
            elif svc_status == "disabled":
                compute_status = "disabled"
            else:
                compute_status = "up"
    except Exception:
        pass

    # ── Instance counts ────────────────────────────────────────────────────
    # Initialise outside try so the Octavia block can always reference it.
    servers: list = []
    try:
        servers = _servers_on_host(conn, hypervisor)
        amphora_count = sum(
            1 for s in servers if (s.name or "").startswith("amphora-")
        )
        vm_count = len(servers) - amphora_count
    except Exception:
        pass

    # Refine amphora detection using Octavia if available
    if servers:
        try:
            amp_ids = {
                amp.compute_id
                for amp in conn.load_balancer.amphorae()
                if amp.compute_id
            }
            if amp_ids:
                amphora_count = sum(
                    1 for s in servers
                    if s.id in amp_ids or (s.name or "").startswith("amphora-")
                )
                vm_count = len(servers) - amphora_count
        except Exception:
            pass  # Octavia unavailable — name-based count is already set

    return {
        "compute_status": compute_status,
        "amphora_count":  amphora_count,
        "vm_count":       vm_count,
    }


# ── Compute service ───────────────────────────────────────────────────────────

def disable_compute_service(hypervisor: str, log: LogFn) -> None:
    """Disable the nova-compute service on a hypervisor host."""
    conn = _conn()
    services = list(conn.compute.services(host=hypervisor, binary="nova-compute"))
    if not services:
        raise RuntimeError(f"No nova-compute service found for host '{hypervisor}'")
    for svc in services:
        conn.compute.disable_service(
            svc.id,
            host=hypervisor,
            binary="nova-compute",
            disabled_reason="Node evacuation via draino",
        )
    log(f"Nova compute service disabled on '{hypervisor}'")


# ── Servers ───────────────────────────────────────────────────────────────────

def list_servers_on_host(hypervisor: str, log: LogFn) -> list[dict]:
    """List all server instances currently scheduled on a hypervisor."""
    conn = _conn()
    servers = _servers_on_host(conn, hypervisor)
    result = []
    for s in servers:
        name = s.name or s.id
        result.append(
            {
                "id":         s.id,
                "name":       name,
                "status":     s.status,
                "is_amphora": name.startswith("amphora-"),
            }
        )
    log(
        f"Found {len(result)} instance(s) on '{hypervisor}' "
        f"({sum(1 for s in result if s['is_amphora'])} Amphora)"
    )
    return result


def live_migrate_server(server_id: str, log: LogFn) -> None:
    """Trigger a live migration for a server, letting the scheduler choose the host."""
    conn = _conn()
    conn.compute.live_migrate_server(
        server_id,
        host=None,
        block_migration="auto",
    )
    log(f"Live migration triggered for server {server_id}")


def get_server_migrations(server_id: str) -> list[dict]:
    """Return recent migrations for a server, newest first."""
    conn = _conn()
    try:
        migs = list(conn.compute.migrations(instance_uuid=server_id))
        migs.sort(key=lambda m: getattr(m, "created_at", ""), reverse=True)
        return [{"id": m.id, "status": m.status} for m in migs]
    except Exception:
        return []


def get_server_status(server_id: str) -> Optional[str]:
    """Return the current Nova status string for a server, or None on error."""
    conn = _conn()
    try:
        s = conn.compute.get_server(server_id)
        return s.status if s else None
    except Exception:
        return None


def count_servers_on_host(hypervisor: str) -> int:
    """Return the number of instances currently on a hypervisor."""
    conn = _conn()
    return len(_servers_on_host(conn, hypervisor))


# ── Octavia / Amphora ─────────────────────────────────────────────────────────

def get_amphora_lb_mapping(log: LogFn) -> dict[str, str]:
    """Return {compute_instance_id: lb_id} for all known Octavia amphora."""
    conn = _conn()
    mapping: dict[str, str] = {}
    try:
        for amp in conn.load_balancer.amphorae():
            if amp.compute_id and amp.loadbalancer_id:
                mapping[amp.compute_id] = amp.loadbalancer_id
        log(f"Retrieved {len(mapping)} Amphora→LB mapping(s) from Octavia")
    except Exception as exc:
        log(f"Warning: could not query Octavia amphora list: {exc}")
    return mapping


def failover_loadbalancer(lb_id: str, log: LogFn) -> None:
    """Trigger an Octavia load-balancer failover."""
    conn = _conn()
    conn.load_balancer.failover_load_balancer(lb_id)
    log(f"Failover triggered for load balancer {lb_id}")


def wait_for_lb_active(lb_id: str, log: LogFn, timeout: int = 600) -> bool:
    """Block until the LB returns to ACTIVE provisioning status."""
    conn = _conn()
    deadline = time.time() + timeout
    while time.time() < deadline:
        lb     = conn.load_balancer.get_load_balancer(lb_id)
        status = lb.provisioning_status
        if status == "ACTIVE":
            log(f"Load balancer {lb_id} is ACTIVE")
            return True
        if status == "ERROR":
            log(f"Load balancer {lb_id} entered ERROR state")
            return False
        log(f"Load balancer {lb_id}: {status} — waiting…")
        time.sleep(15)
    log(f"Timeout waiting for load balancer {lb_id} to become ACTIVE")
    return False
