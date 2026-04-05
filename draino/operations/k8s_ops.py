"""Kubernetes operations: cordon, drain."""
from __future__ import annotations

import subprocess
import time
from typing import Callable, Optional

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

LogFn = Callable[[str], None]

_CONTEXT: str | None = None


def configure(context: str | None = None) -> None:
    global _CONTEXT
    _CONTEXT = context


def _load_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config(context=_CONTEXT)


def get_nodes() -> list[dict]:
    """Return a list of node info dicts."""
    _load_config()
    v1 = client.CoreV1Api()
    raw = v1.list_node()
    result: list[dict] = []
    for node in raw.items:
        name: str = node.metadata.name
        hostname: str = node.metadata.labels.get("kubernetes.io/hostname", name)
        unschedulable: bool = bool(node.spec.unschedulable)
        ready = False
        ready_since = None
        for cond in node.status.conditions or []:
            if cond.type == "Ready":
                ready = cond.status == "True"
                if ready:
                    ready_since = cond.last_transition_time
        node_info = node.status.node_info
        kernel_version: str | None = node_info.kernel_version if node_info else None
        result.append(
            {
                "name": name,
                "hostname": hostname,
                "cordoned": unschedulable,
                "ready": ready,
                "ready_since": ready_since,
                "kernel_version": kernel_version,
            }
        )
    return result


def get_node_k8s_detail(node_name: str) -> dict:
    """Return detailed K8s node info for the summary tab.

    Fetches node_info (kubelet version, container runtime, OS image,
    architecture), capacity/allocatable (cpu, memory, pods), and live
    pod count.  All values default to None on failure.
    """
    _load_config()
    v1 = client.CoreV1Api()

    result: dict = {
        "kubelet_version":     None,
        "container_runtime":   None,
        "os_image":            None,
        "architecture":        None,
        "cpu_capacity":        None,
        "memory_capacity_kb":  None,
        "pods_capacity":       None,
        "cpu_allocatable":     None,
        "memory_allocatable_kb": None,
        "pods_allocatable":    None,
        "pod_count":           None,
        "roles":               [],
    }

    try:
        node = v1.read_node(node_name)
    except Exception:
        return result

    # node_info
    ni = node.status.node_info
    if ni:
        result["kubelet_version"]   = ni.kubelet_version
        result["container_runtime"] = ni.container_runtime_version
        result["os_image"]          = ni.os_image
        result["architecture"]      = ni.architecture

    def _parse_ki(s: str | None) -> int | None:
        """Convert K8s memory string like '263928792Ki' → KiB int."""
        if not s:
            return None
        s = s.strip()
        if s.endswith("Ki"):
            try:
                return int(s[:-2])
            except Exception:
                return None
        if s.endswith("Mi"):
            try:
                return int(s[:-2]) * 1024
            except Exception:
                return None
        if s.endswith("Gi"):
            try:
                return int(s[:-2]) * 1024 * 1024
            except Exception:
                return None
        try:
            return int(s) // 1024  # bytes → KiB
        except Exception:
            return None

    cap  = node.status.capacity    or {}
    alloc = node.status.allocatable or {}
    result["cpu_capacity"]           = cap.get("cpu")
    result["memory_capacity_kb"]     = _parse_ki(cap.get("memory"))
    result["pods_capacity"]          = cap.get("pods")
    result["cpu_allocatable"]        = alloc.get("cpu")
    result["memory_allocatable_kb"]  = _parse_ki(alloc.get("memory"))
    result["pods_allocatable"]       = alloc.get("pods")

    # Roles from labels  (node-role.kubernetes.io/<role>)
    labels = node.metadata.labels or {}
    roles = [
        k.split("/", 1)[1]
        for k in labels
        if k.startswith("node-role.kubernetes.io/")
    ]
    result["roles"] = roles or ["worker"]

    # Live pod count (non-terminated)
    try:
        pods = v1.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={node_name}"
        )
        result["pod_count"] = sum(
            1 for p in pods.items
            if p.status.phase not in ("Succeeded", "Failed")
        )
    except Exception:
        pass

    return result


def check_etcd_service(hostname: str) -> Optional[bool]:
    """SSH to *hostname* and check whether the etcd systemd service is active.

    Returns True if active, False if inactive/failed, None if the check
    could not be completed (SSH unreachable, timeout, etc.).
    """
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "ConnectTimeout=5",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                hostname,
                "systemctl", "is-active", "etcd",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return None


def get_etcd_node_names() -> set[str]:
    """Return the set of node names in the etcd role.

    Detects nodes labelled by kubespray with node-role.kubernetes.io/etcd.
    """
    _load_config()
    v1 = client.CoreV1Api()
    result: set[str] = set()
    try:
        nodes = v1.list_node(
            label_selector="node-role.kubernetes.io/etcd"
        )
        for node in nodes.items:
            result.add(node.metadata.name)
    except Exception:
        pass
    return result


def cordon_node(name: str, log: LogFn) -> None:
    """Mark a node unschedulable."""
    _load_config()
    v1 = client.CoreV1Api()
    v1.patch_node(name, {"spec": {"unschedulable": True}})
    log(f"Node '{name}' cordoned successfully")


def get_pods_on_node(node_name: str) -> list[dict]:
    """Return a list of pod info dicts for all pods scheduled on *node_name*."""
    _load_config()
    v1 = client.CoreV1Api()
    raw = v1.list_pod_for_all_namespaces(
        field_selector=f"spec.nodeName={node_name}"
    )
    result: list[dict] = []
    for pod in raw.items:
        ready_count = 0
        total_count = 0
        restarts = 0
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                total_count += 1
                if cs.ready:
                    ready_count += 1
                restarts += cs.restart_count or 0
        elif pod.spec.containers:
            total_count = len(pod.spec.containers)
        result.append({
            "namespace":   pod.metadata.namespace,
            "name":        pod.metadata.name,
            "phase":       pod.status.phase or "Unknown",
            "ready_count": ready_count,
            "total_count": total_count,
            "restarts":    restarts,
            "created_at":  pod.metadata.creation_timestamp,
        })
    return result


def uncordon_node(name: str, log: LogFn) -> None:
    """Mark a node schedulable."""
    _load_config()
    v1 = client.CoreV1Api()
    v1.patch_node(name, {"spec": {"unschedulable": False}})
    log(f"Node '{name}' uncordoned successfully")


def get_node_hardware_info(hostname: str) -> dict:
    """SSH to *hostname* and return chassis, CPU, and RAM hardware details.

    Reads /sys/class/dmi/id/ (no sudo) for vendor/product info,
    /proc/cpuinfo (no sudo) for CPU model and topology, and tries
    dmidecode -t 17 (sudo -n, non-interactive) for RAM type/speed.
    Returns a dict with all keys present; unknown values are None.
    """
    import re as _re
    from collections import Counter

    result: dict = {
        "vendor":              None,
        "product":             None,
        "bios_version":        None,
        "cpu_model":           None,
        "cpu_sockets":         None,
        "cpu_cores_per_socket": None,
        "cpu_threads_per_core": None,
        "ram_type":            None,
        "ram_speed":           None,
        "ram_total_gb":        None,
        "ram_slots_used":      None,
        "ram_manufacturer":    None,
    }

    # Single SSH session — all reads in one round-trip
    script = (
        "echo __V__; cat /sys/class/dmi/id/sys_vendor 2>/dev/null; "
        "echo __P__; cat /sys/class/dmi/id/product_name 2>/dev/null; "
        "echo __B__; cat /sys/class/dmi/id/bios_version 2>/dev/null; "
        "echo __C__; grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//'; "
        "echo __S__; grep 'physical id' /proc/cpuinfo 2>/dev/null | sort -u | wc -l; "
        "echo __K__; grep -m1 'cpu cores' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//'; "
        "echo __H__; grep -m1 'siblings' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//'; "
        "echo __D__; (sudo -n dmidecode -t 17 2>/dev/null || dmidecode -t 17 2>/dev/null) | "
        r"grep -E '^\s+(Size|Type|Speed|Manufacturer):' | "
        "grep -v 'No Module Installed'; "
        "echo __END__"
    )

    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o", "ConnectTimeout=5",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                hostname,
                script,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return result

    section = None
    dmi_sizes: list[int] = []
    dmi_types: list[str] = []
    dmi_speeds: list[str] = []
    dmi_mfrs: list[str] = []

    for line in proc.stdout.splitlines():
        s = line.strip()
        if not s:
            continue

        # Section markers
        if s == "__V__":   section = "vendor";   continue
        if s == "__P__":   section = "product";  continue
        if s == "__B__":   section = "bios";     continue
        if s == "__C__":   section = "cpu";      continue
        if s == "__S__":   section = "sockets";  continue
        if s == "__K__":   section = "cores";    continue
        if s == "__H__":   section = "siblings"; continue
        if s == "__D__":   section = "dmi";      continue
        if s == "__END__": break

        if section == "vendor":
            result["vendor"] = s
        elif section == "product":
            result["product"] = s
        elif section == "bios":
            result["bios_version"] = s
        elif section == "cpu":
            # Clean up: remove (R)/(TM), " CPU @ X.XXGHz", "XX-Core Processor"
            m = _re.sub(r'\([RT]M\)', '', s)
            m = _re.sub(r'\bCPU\s+@\s+[\d.]+\s*GHz\b', '', m)
            m = _re.sub(r'\b\d+-Core\s+Processor\b', '', m, flags=_re.IGNORECASE)
            m = _re.sub(r'\s{2,}', ' ', m).strip()
            result["cpu_model"] = m
        elif section == "sockets":
            try:
                n = int(s)
                result["cpu_sockets"] = n if n > 0 else 1
            except Exception:
                pass
        elif section == "cores":
            try:
                result["cpu_cores_per_socket"] = int(s)
            except Exception:
                pass
        elif section == "siblings":
            try:
                siblings = int(s)
                cps = result["cpu_cores_per_socket"]
                if cps and cps > 0:
                    result["cpu_threads_per_core"] = siblings // cps
            except Exception:
                pass
        elif section == "dmi":
            if ":" not in s:
                continue
            key, _, val = s.partition(":")
            key = key.strip()
            val = val.strip()
            if key == "Size":
                parts = val.split()
                if len(parts) >= 2:
                    try:
                        num = int(parts[0])
                        unit = parts[1].upper()
                        gb = num if unit == "GB" else num // 1024 if unit == "MB" else None
                        if gb is not None and gb > 0:
                            dmi_sizes.append(gb)
                    except Exception:
                        pass
            elif key == "Type":
                if val and val not in ("Unknown", "Other", ""):
                    dmi_types.append(val)
            elif key == "Speed":
                if val and val not in ("Unknown", "0 MT/s", "0 MHz", ""):
                    dmi_speeds.append(val)
            elif key == "Manufacturer":
                if val and val not in ("Unknown", "Not Specified", ""):
                    dmi_mfrs.append(val)

    # Aggregate DMI memory info
    if dmi_sizes:
        result["ram_total_gb"]  = sum(dmi_sizes)
        result["ram_slots_used"] = len(dmi_sizes)
    if dmi_types:
        result["ram_type"] = Counter(dmi_types).most_common(1)[0][0]
    if dmi_speeds:
        result["ram_speed"] = Counter(dmi_speeds).most_common(1)[0][0]
    if dmi_mfrs:
        result["ram_manufacturer"] = Counter(dmi_mfrs).most_common(1)[0][0]

    return result


def get_ovn_port_detail(port_id: str) -> dict:
    """Run `kubectl ko nbctl lsp-show <port_id>` and return parsed data.

    Returns a dict with keys: id, type, addresses, port_security,
    up, enabled, tag, external_ids, options, dynamic_addresses.
    Raises RuntimeError if kubectl is unavailable or the command fails.
    """
    import json as _json
    import re as _re

    cmd = ["kubectl"]
    if _CONTEXT:
        cmd += ["--context", _CONTEXT]
    # ovn-nbctl has no lsp-show; use --format=list list TABLE <name> which
    # looks up by name column directly — avoids the find condition parser
    # mis-treating hyphenated UUIDs as multi-value expressions.
    cmd += ["ko", "nbctl", "--format=list", "list", "Logical_Switch_Port",
            port_id]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        raise RuntimeError("kubectl not found in PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("kubectl ko nbctl list timed out")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"nbctl list exited with code {result.returncode}")

    if not result.stdout.strip():
        raise RuntimeError(f"No logical switch port found with name {port_id!r}")

    def _parse_ovn_map(s: str) -> dict:
        """Parse OVN map format: {key="value", key2=value2}"""
        s = s.strip().strip("{}")
        out: dict = {}
        for m in _re.finditer(r'([\w:.\-]+)\s*=\s*"([^"]*)"', s):
            out[m.group(1)] = m.group(2)
        for m in _re.finditer(r'([\w:.\-]+)\s*=\s*([^",}\s]+)', s):
            if m.group(1) not in out:
                out[m.group(1)] = m.group(2)
        return out

    data: dict = {
        "id":                port_id,
        "type":              "",
        "addresses":         [],
        "port_security":     [],
        "up":                None,
        "enabled":           None,
        "tag":               None,
        "external_ids":      {},
        "options":           {},
        "dynamic_addresses": "",
    }

    for line in result.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue

        if key == "type":
            data["type"] = val.strip('"')
        elif key == "addresses":
            try:
                data["addresses"] = _json.loads(val)
            except Exception:
                data["addresses"] = [val.strip('"')] if val and val != "[]" else []
        elif key == "port_security":
            try:
                data["port_security"] = _json.loads(val)
            except Exception:
                data["port_security"] = [val.strip('"')] if val and val != "[]" else []
        elif key == "up":
            if val in ("true", "false"):
                data["up"] = (val == "true")
        elif key == "enabled":
            if val in ("true", "false"):
                data["enabled"] = (val == "true")
        elif key == "tag":
            try:
                data["tag"] = int(val)
            except Exception:
                pass
        elif key == "dynamic_addresses":
            v = val.strip('"')
            if v:
                data["dynamic_addresses"] = v
        elif key in ("external_ids", "options"):
            data[key] = _parse_ovn_map(val)

    return data


def get_ovn_logical_switch(network_id: str) -> dict:
    """Run `kubectl ko nbctl show neutron-<network_id>` and return parsed data.

    Returns:
        {
            "ls_name": "neutron-<uuid>",
            "ls_uuid": "<ovn-internal-uuid>",
            "ports": [
                {
                    "id":          "<port-name>",   # Neutron port UUID for VM/router ports
                    "type":        "",              # "" | "router" | "localnet" | ...
                    "addresses":   ["mac ip", ...],
                    "router_port": "",
                }
            ]
        }
    Raises RuntimeError if kubectl is not available or the command fails.
    """
    import json as _json

    ls_name = f"neutron-{network_id}"
    cmd = ["kubectl"]
    if _CONTEXT:
        cmd += ["--context", _CONTEXT]
    cmd += ["ko", "nbctl", "show", ls_name]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        raise RuntimeError("kubectl not found in PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("kubectl ko nbctl show timed out")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(stderr or f"kubectl ko exited with code {result.returncode}")

    # ── Parse the nbctl show output ──────────────────────────────────────────
    # Format:
    #   switch <ovn-uuid> (neutron-<network-id>)
    #       port <port-name>
    #           type: router
    #           addresses: ["fa:16:3e:... 10.0.0.1"]
    lines = result.stdout.splitlines()
    ls_uuid = ""
    ports: list[dict] = []
    current: dict | None = None

    for line in lines:
        content = line.rstrip()
        stripped = content.lstrip()
        if not stripped:
            continue
        indent = len(content) - len(stripped)

        if indent == 0 and stripped.startswith("switch "):
            parts = stripped.split(None, 2)
            ls_uuid = parts[1] if len(parts) > 1 else ""
            current = None

        elif indent == 4 and stripped.startswith("port "):
            if current is not None:
                ports.append(current)
            current = {
                "id":          stripped[len("port "):].split()[0],
                "type":        "",
                "addresses":   [],
                "router_port": "",
            }

        elif indent == 8 and current is not None and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if key == "type":
                current["type"] = val
            elif key == "router-port":
                current["router_port"] = val.strip('"')
            elif key == "addresses":
                try:
                    current["addresses"] = _json.loads(val)
                except Exception:
                    current["addresses"] = [val.strip('"')]

    if current is not None:
        ports.append(current)

    return {"ls_name": ls_name, "ls_uuid": ls_uuid, "ports": ports}


# ── Cluster-wide resource listings ───────────────────────────────────────────

def _ts(obj) -> str | None:
    ts = obj.metadata.creation_timestamp if obj and obj.metadata else None
    return ts.isoformat() if ts else None


def list_k8s_namespaces() -> list[dict]:
    _load_config()
    v1 = client.CoreV1Api()
    return [
        {"name": ns.metadata.name, "status": ns.status.phase or "Active", "created": _ts(ns),
         "labels": dict(ns.metadata.labels or {})}
        for ns in v1.list_namespace().items
    ]


def list_k8s_pods(namespace: str | None = None) -> list[dict]:
    _load_config()
    v1 = client.CoreV1Api()
    raw = v1.list_pod_for_all_namespaces() if not namespace else v1.list_namespaced_pod(namespace)
    result = []
    for pod in raw.items:
        total    = len(pod.spec.containers or [])
        ready    = 0
        restarts = 0
        if pod.status.container_statuses:
            for cs in pod.status.container_statuses:
                if cs.ready: ready += 1
                restarts += cs.restart_count or 0
        result.append({
            "namespace": pod.metadata.namespace,
            "name":      pod.metadata.name,
            "phase":     pod.status.phase or "Unknown",
            "ready":     f"{ready}/{total}",
            "restarts":  restarts,
            "node":      pod.spec.node_name or "",
            "created":   _ts(pod),
        })
    return result


def list_k8s_services(namespace: str | None = None) -> list[dict]:
    _load_config()
    v1 = client.CoreV1Api()
    raw = v1.list_service_for_all_namespaces() if not namespace else v1.list_namespaced_service(namespace)
    result = []
    for svc in raw.items:
        ports = ", ".join(
            f"{p.port}{'/' + p.protocol if p.protocol != 'TCP' else ''}"
            + (f":{p.node_port}" if p.node_port else "")
            for p in (svc.spec.ports or [])
        )
        ext_ips: list[str] = []
        if svc.status.load_balancer and svc.status.load_balancer.ingress:
            ext_ips = [i.ip or i.hostname or "" for i in svc.status.load_balancer.ingress]
        result.append({
            "namespace":    svc.metadata.namespace,
            "name":         svc.metadata.name,
            "type":         svc.spec.type or "ClusterIP",
            "cluster_ip":   svc.spec.cluster_ip or "",
            "external_ips": [x for x in ext_ips if x],
            "ports":        ports,
            "created":      _ts(svc),
        })
    return result


def list_k8s_pvs() -> list[dict]:
    _load_config()
    v1 = client.CoreV1Api()
    result = []
    for pv in v1.list_persistent_volume().items:
        claim = ""
        if pv.spec.claim_ref:
            claim = f"{pv.spec.claim_ref.namespace}/{pv.spec.claim_ref.name}"
        result.append({
            "name":           pv.metadata.name,
            "capacity":       (pv.spec.capacity or {}).get("storage", ""),
            "access_modes":   ",".join(pv.spec.access_modes or []),
            "reclaim_policy": pv.spec.persistent_volume_reclaim_policy or "",
            "status":         pv.status.phase or "",
            "claim":          claim,
            "storageclass":   pv.spec.storage_class_name or "",
            "created":        _ts(pv),
        })
    return result


def list_k8s_pvcs(namespace: str | None = None) -> list[dict]:
    _load_config()
    v1 = client.CoreV1Api()
    raw = (v1.list_persistent_volume_claim_for_all_namespaces() if not namespace
           else v1.list_namespaced_persistent_volume_claim(namespace))
    result = []
    for pvc in raw.items:
        result.append({
            "namespace":    pvc.metadata.namespace,
            "name":         pvc.metadata.name,
            "status":       pvc.status.phase or "",
            "volume":       pvc.spec.volume_name or "",
            "capacity":     (pvc.status.capacity or {}).get("storage", ""),
            "access_modes": ",".join(pvc.spec.access_modes or []),
            "storageclass": pvc.spec.storage_class_name or "",
            "created":      _ts(pvc),
        })
    return result


def list_k8s_crds() -> list[dict]:
    _load_config()
    api = client.ApiextensionsV1Api()
    result = []
    for crd in api.list_custom_resource_definition().items:
        spec = crd.spec
        versions = [v.name for v in (spec.versions or []) if v.served]
        result.append({
            "name":     crd.metadata.name,
            "group":    spec.group,
            "kind":     spec.names.kind,
            "scope":    spec.scope,
            "versions": versions,
            "created":  _ts(crd),
        })
    return result


def drain_node(name: str, log: LogFn, timeout: int = 300) -> None:
    """Evict all non-DaemonSet pods from a node and wait for termination."""
    _load_config()
    v1 = client.CoreV1Api()

    log(f"Listing pods on node '{name}'…")
    pods = v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={name}")

    to_evict = []
    skipped_ds = 0

    for pod in pods.items:
        if pod.status.phase in ("Succeeded", "Failed"):
            continue
        is_ds = any(
            ref.kind == "DaemonSet"
            for ref in (pod.metadata.owner_references or [])
        )
        if is_ds:
            skipped_ds += 1
        else:
            to_evict.append(pod)

    log(
        f"Evicting {len(to_evict)} pod(s), "
        f"skipping {skipped_ds} DaemonSet pod(s)"
    )

    for pod in to_evict:
        ns    = pod.metadata.namespace
        pname = pod.metadata.name
        eviction = client.V1Eviction(
            metadata=client.V1ObjectMeta(name=pname, namespace=ns)
        )
        for attempt in range(2):
            try:
                v1.create_namespaced_pod_eviction(
                    name=pname, namespace=ns, body=eviction
                )
                log(f"Evicted {ns}/{pname}")
                break
            except ApiException as exc:
                if exc.status == 429 and attempt == 0:
                    log(f"PodDisruptionBudget delay for {ns}/{pname}, retrying…")
                    time.sleep(5)
                elif exc.status == 404:
                    break  # already gone
                else:
                    log(f"Warning: could not evict {ns}/{pname}: {exc.reason}")
                    break

    log("Waiting for pods to terminate…")
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = v1.list_pod_for_all_namespaces(
            field_selector=f"spec.nodeName={name}"
        )
        non_ds_alive = [
            p
            for p in remaining.items
            if p.status.phase not in ("Succeeded", "Failed")
            and not any(
                r.kind == "DaemonSet"
                for r in (p.metadata.owner_references or [])
            )
        ]
        if not non_ds_alive:
            log(f"All pods drained from '{name}'")
            return
        log(f"{len(non_ds_alive)} pod(s) still terminating on '{name}'…")
        time.sleep(10)

    log(f"WARNING: drain timeout reached for '{name}' — some pods may remain")
