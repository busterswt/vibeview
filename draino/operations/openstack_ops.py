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

    openstacksdk maps OS-EXT-SRV-ATTR:host to .compute_host in newer
    releases and .host in older ones. Try all known forms.
    """
    return (
        getattr(server, "compute_host", None)
        or getattr(server, "host", None)
        or server.to_dict().get("OS-EXT-SRV-ATTR:host")
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

def get_all_host_summaries(log_cb: Optional[LogFn] = None) -> dict[str, dict]:
    """Fetch summaries for every hypervisor in exactly three API calls.

    Returns {hypervisor_hostname: {compute_status, amphora_count, vm_count}}
    log_cb, if provided, receives diagnostic strings useful for debugging.
    """
    conn = _conn()

    def _log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    # 1. Nova compute service statuses — one call for all hosts
    service_map: dict[str, str] = {}
    try:
        for svc in conn.compute.services(binary="nova-compute"):
            svc_host   = getattr(svc, "host", None) or ""
            svc_state  = (getattr(svc, "state",  "up")      or "up").lower()
            svc_status = (getattr(svc, "status", "enabled") or "enabled").lower()
            if svc_state == "down":
                service_map[svc_host] = "down"
            elif svc_status == "disabled":
                service_map[svc_host] = "disabled"
            else:
                service_map[svc_host] = "up"
        _log(f"Nova services found on: {sorted(service_map)}")
    except Exception as exc:
        _log(f"Nova services query failed: {exc}")

    # 2. All servers, grouped by host — one call for all hosts
    servers_by_host: dict[str, list] = {}
    total_servers = 0
    null_host_count = 0
    try:
        for s in conn.compute.servers(all_projects=True):
            total_servers += 1
            # On the first server, log all available keys so mismatches are visible
            if total_servers == 1:
                try:
                    d = s.to_dict()
                    host_keys = [k for k in d if "host" in k.lower()]
                    _log(f"Sample server host-related keys: {host_keys}")
                    _log(f"Sample server host values: { {k: d[k] for k in host_keys} }")
                except Exception as exc:
                    _log(f"Could not inspect server dict: {exc}")
            h = _server_host(s)
            if h:
                servers_by_host.setdefault(h, []).append(s)
            else:
                null_host_count += 1
        _log(
            f"Server list: {total_servers} total, "
            f"{null_host_count} with no host attr, "
            f"hosts seen: {sorted(servers_by_host)}"
        )
    except Exception as exc:
        _log(f"Server list query failed: {exc}")

    # 3. Octavia amphora instance IDs — one call
    amp_ids: set[str] = set()
    try:
        amp_ids = {
            amp.compute_id
            for amp in conn.load_balancer.amphorae()
            if amp.compute_id
        }
        _log(f"Octavia: {len(amp_ids)} amphora instance(s)")
    except Exception as exc:
        _log(f"Octavia query failed (non-fatal): {exc}")

    # 4. Host aggregates — derive AZ and aggregate membership per host
    # Each host maps to {az: str|None, aggregates: [agg_name, ...]}
    agg_map: dict[str, dict] = {}
    try:
        for agg in conn.compute.aggregates():
            agg_az   = (agg.metadata or {}).get("availability_zone") or None
            agg_name = getattr(agg, "name", None) or ""
            for host in (agg.hosts or []):
                if host not in agg_map:
                    agg_map[host] = {"az": None, "aggregates": []}
                # First AZ-bearing aggregate wins
                if agg_az and not agg_map[host]["az"]:
                    agg_map[host]["az"] = agg_az
                if agg_name:
                    agg_map[host]["aggregates"].append(agg_name)
        _log(f"Aggregates: {len(agg_map)} host(s) mapped")
    except Exception as exc:
        _log(f"Aggregates query failed (non-fatal): {exc}")

    # Build per-host summary from the four collected datasets
    result: dict[str, dict] = {}
    for host in set(service_map) | set(servers_by_host):
        servers = servers_by_host.get(host, [])
        amphora_count = sum(
            1 for s in servers
            if s.id in amp_ids or (s.name or "").startswith("amphora-")
        )
        host_agg = agg_map.get(host, {})
        result[host] = {
            "is_compute":        True,
            "compute_status":    service_map.get(host),
            "amphora_count":     amphora_count,
            "vm_count":          len(servers) - amphora_count,
            "availability_zone": host_agg.get("az"),
            "aggregates":        host_agg.get("aggregates", []),
        }

    return result


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


# ── Hypervisor detail (for summary tab) ──────────────────────────────────────

def get_hypervisor_detail(hypervisor: str) -> dict:
    """Return Nova hypervisor resource stats for the summary tab.

    Returns a dict with vcpus, vcpus_used, memory_mb, memory_mb_used,
    local_disk_gb, local_disk_gb_used, running_vms, cpu_info (dict).
    All values are None on failure.
    """
    import json as _json

    conn = _conn()
    result: dict = {
        "vcpus":             None,
        "vcpus_used":        None,
        "memory_mb":         None,
        "memory_mb_used":    None,
        "local_disk_gb":     None,
        "local_disk_gb_used": None,
        "running_vms":       None,
        "cpu_info":          {},
    }
    try:
        hvs = list(conn.compute.hypervisors(hypervisor_hostname_pattern=hypervisor))
        if not hvs:
            return result
        hv = hvs[0]
        # Fetch full detail (some fields only available via get)
        try:
            hv = conn.compute.get_hypervisor(hv.id)
        except Exception:
            pass
        d = hv.to_dict() if hasattr(hv, "to_dict") else {}

        result["vcpus"]              = d.get("vcpus")              or getattr(hv, "vcpus",              None)
        result["vcpus_used"]         = d.get("vcpus_used")         or getattr(hv, "vcpus_used",         None)
        result["memory_mb"]          = d.get("memory_size")        or getattr(hv, "memory_size",        None) \
                                    or d.get("memory_mb")          or getattr(hv, "memory_mb",          None)
        result["memory_mb_used"]     = d.get("memory_used")        or getattr(hv, "memory_used",        None) \
                                    or d.get("memory_mb_used")     or getattr(hv, "memory_mb_used",     None)
        result["local_disk_gb"]      = d.get("local_disk_size")    or getattr(hv, "local_disk_size",    None) \
                                    or d.get("disk_available_least") and None  # don't use available
        result["local_disk_gb_used"] = d.get("local_disk_used")    or getattr(hv, "local_disk_used",    None)
        result["running_vms"]        = d.get("running_vms")        or getattr(hv, "running_vms",        None)

        raw_cpu = d.get("cpu_info") or getattr(hv, "cpu_info", None)
        if isinstance(raw_cpu, str):
            try:
                raw_cpu = _json.loads(raw_cpu)
            except Exception:
                raw_cpu = {}
        if isinstance(raw_cpu, dict):
            result["cpu_info"] = raw_cpu
    except Exception:
        pass
    return result


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


def enable_compute_service(hypervisor: str, log: LogFn) -> None:
    """Enable the nova-compute service on a hypervisor host."""
    conn = _conn()
    services = list(conn.compute.services(host=hypervisor, binary="nova-compute"))
    if not services:
        raise RuntimeError(f"No nova-compute service found for host '{hypervisor}'")
    for svc in services:
        conn.compute.enable_service(
            svc.id,
            host=hypervisor,
            binary="nova-compute",
        )
    log(f"Nova compute service enabled on '{hypervisor}'")


# ── Servers ───────────────────────────────────────────────────────────────────

def get_instances_preflight(hypervisor: str) -> list[dict]:
    """Return all instances on *hypervisor* with storage-type metadata.

    Used to preview what will be migrated before the workflow starts.
    is_volume_backed is True when the server has no image (booted from volume).
    """
    conn = _conn()
    servers = _servers_on_host(conn, hypervisor)
    result = []
    for s in servers:
        name = s.name or s.id
        result.append({
            "id":               s.id,
            "name":             name,
            "status":           s.status,
            "is_amphora":       name.startswith("amphora-"),
            "is_volume_backed": not s.image,  # {} or None → booted from volume
        })
    return result


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


def get_server_task_state(server_id: str) -> Optional[str]:
    """Return the current OS-EXT-STS:task_state for a server, or None on error."""
    conn = _conn()
    try:
        s = conn.compute.get_server(server_id)
        return (
            getattr(s, "task_state", None)
            or s.to_dict().get("OS-EXT-STS:task_state")
        )
    except Exception:
        return None


def cold_migrate_server(server_id: str, log: LogFn) -> None:
    """Trigger a cold migration (server will be stopped and moved)."""
    conn = _conn()
    conn.compute.migrate_server(server_id)
    log(f"Cold migration triggered for server {server_id}")


def confirm_resize_server(server_id: str, log: LogFn) -> None:
    """Confirm a completed cold migration, moving it from VERIFY_RESIZE to ACTIVE."""
    conn = _conn()
    conn.compute.confirm_resize_server(server_id)
    log(f"Cold migration confirmed for server {server_id}")


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
