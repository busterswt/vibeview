"""Node-agent host metrics and network stats probes."""
from __future__ import annotations

import os
import shlex
import threading
import time

from .node_agent_common import _run_host_shell

_HOST_METRICS_TTL = float(os.getenv("DRAINO_NODE_AGENT_HOST_METRICS_TTL", "30"))
_HOST_METRICS_HISTORY_LIMIT = int(os.getenv("DRAINO_NODE_AGENT_HOST_METRICS_HISTORY_LIMIT", "30"))
_host_metrics_cache: tuple[float, dict] | None = None
_host_metrics_history: list[dict] = []
_host_metrics_lock = threading.Lock()
_host_network_prev_samples: dict[str, tuple[float, int, int]] = {}
_host_network_prev_lock = threading.Lock()
_instance_port_prev_samples: dict[str, tuple[float, int, int]] = {}
_instance_port_prev_lock = threading.Lock()
_interface_prev_samples: dict[str, tuple[float, int, int]] = {}
_interface_prev_lock = threading.Lock()
_irq_balance_prev_samples: dict[str, tuple[float, int, int]] = {}
_irq_balance_prev_lock = threading.Lock()


def _get_host_metrics() -> dict:
    now = time.time()
    global _host_metrics_cache
    with _host_metrics_lock:
        if _host_metrics_cache is not None:
            expires_at, payload = _host_metrics_cache
            if now < expires_at:
                return {
                    "current": dict(payload["current"]),
                    "history": [dict(item) for item in _host_metrics_history],
                    "error": payload.get("error"),
                }

    try:
        proc = _run_host_shell(
            "echo __LOAD__; cat /proc/loadavg 2>/dev/null; "
            "echo __MEM__; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo 2>/dev/null; "
            "echo __CPU__; nproc 2>/dev/null; "
            "echo __UPTIME__; cut -d' ' -f1 /proc/uptime 2>/dev/null; "
            "echo __DF__; df -Pk / /var /var/lib 2>/dev/null | awk 'NR>1{printf \"%s|%s|%s|%s|%s\\n\",$6,$2,$3,$4,$5}'; "
            "echo __END__",
            timeout=10,
        )
    except Exception as exc:
        return {"current": None, "history": [], "error": str(exc)}

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        return {
            "current": None,
            "history": [],
            "error": stderr or f"host metrics command exited {proc.returncode}",
        }

    current: dict[str, object] = {
        "timestamp": int(now),
        "load1": None,
        "load5": None,
        "load15": None,
        "cpu_count": None,
        "uptime_seconds": None,
        "memory_total_kb": None,
        "memory_available_kb": None,
        "memory_used_kb": None,
        "memory_used_percent": None,
        "filesystems": [],
    }
    section = None
    filesystems_by_mount: dict[str, dict] = {}

    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "__LOAD__":
            section = "load"
            continue
        if line == "__MEM__":
            section = "mem"
            continue
        if line == "__CPU__":
            section = "cpu"
            continue
        if line == "__UPTIME__":
            section = "uptime"
            continue
        if line == "__DF__":
            section = "df"
            continue
        if line == "__END__":
            break

        if section == "load":
            parts = line.split()
            if len(parts) >= 3:
                try:
                    current["load1"] = float(parts[0])
                    current["load5"] = float(parts[1])
                    current["load15"] = float(parts[2])
                except ValueError:
                    pass
        elif section == "mem" and ":" in line:
            key, _, value = line.partition(":")
            raw_value = value.strip().split()[0] if value.strip() else None
            if raw_value and raw_value.isdigit():
                if key == "MemTotal":
                    current["memory_total_kb"] = int(raw_value)
                elif key == "MemAvailable":
                    current["memory_available_kb"] = int(raw_value)
        elif section == "cpu":
            if line.isdigit():
                current["cpu_count"] = int(line)
        elif section == "uptime":
            try:
                current["uptime_seconds"] = float(line)
            except ValueError:
                pass
        elif section == "df":
            parts = line.split("|")
            if len(parts) != 5:
                continue
            mount, total, used, available, used_pct = parts
            try:
                filesystems_by_mount[mount] = {
                    "mount": mount,
                    "total_kb": int(total),
                    "used_kb": int(used),
                    "available_kb": int(available),
                    "used_percent": int(used_pct.rstrip("%")),
                }
            except ValueError:
                continue

    filesystems = sorted(filesystems_by_mount.values(), key=lambda item: item["mount"])

    mem_total = current.get("memory_total_kb")
    mem_available = current.get("memory_available_kb")
    if isinstance(mem_total, int) and isinstance(mem_available, int) and mem_total > 0:
        mem_used = mem_total - mem_available
        current["memory_used_kb"] = mem_used
        current["memory_used_percent"] = round((mem_used / mem_total) * 100, 1)

    current["filesystems"] = filesystems
    root_fs = next((fs for fs in filesystems if fs.get("mount") == "/"), None)

    sample = {
        "timestamp": current["timestamp"],
        "load1": current.get("load1"),
        "memory_used_percent": current.get("memory_used_percent"),
        "root_used_percent": root_fs.get("used_percent") if root_fs else None,
    }

    payload = {
        "current": current,
        "history": [],
        "error": None,
    }
    with _host_metrics_lock:
        _host_metrics_history.append(sample)
        if len(_host_metrics_history) > _HOST_METRICS_HISTORY_LIMIT:
            del _host_metrics_history[:-_HOST_METRICS_HISTORY_LIMIT]
        payload["history"] = [dict(item) for item in _host_metrics_history]
        _host_metrics_cache = (_HOST_METRICS_TTL + now, {"current": dict(current), "error": None})
    return payload


def _get_host_network_stats() -> dict:
    try:
        proc = _run_host_shell(
            r"""
for d in /sys/class/net/*/; do
  name=$(basename "$d")
  is_phys=0; is_bond=0
  [ -e "${d}device" ] && is_phys=1
  [ -d "${d}bonding" ] && is_bond=1
  [ "$is_phys" = "0" ] && [ "$is_bond" = "0" ] && continue
  rx=$(cat "${d}statistics/rx_bytes" 2>/dev/null)
  tx=$(cat "${d}statistics/tx_bytes" 2>/dev/null)
  printf '%s|%s|%s\n' "$name" "${rx:-0}" "${tx:-0}"
done
""",
            timeout=10,
        )
    except Exception as exc:
        return {"interfaces": [], "error": str(exc)}

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        return {
            "interfaces": [],
            "error": stderr or f"network stats command exited {proc.returncode}",
        }

    now = time.time()
    results: list[dict] = []
    with _host_network_prev_lock:
        for raw_line in proc.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) != 3:
                continue
            name, rx_raw, tx_raw = parts
            try:
                rx_bytes = int(rx_raw)
                tx_bytes = int(tx_raw)
            except ValueError:
                continue
            rx_rate_bps = None
            tx_rate_bps = None
            previous = _host_network_prev_samples.get(name)
            if previous is not None:
                prev_time, prev_rx, prev_tx = previous
                elapsed = now - prev_time
                if elapsed > 0:
                    rx_rate_bps = max(0.0, (rx_bytes - prev_rx) / elapsed)
                    tx_rate_bps = max(0.0, (tx_bytes - prev_tx) / elapsed)
            _host_network_prev_samples[name] = (now, rx_bytes, tx_bytes)
            results.append({
                "name": name,
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_bytes_per_second": rx_rate_bps,
                "tx_bytes_per_second": tx_rate_bps,
            })

    results.sort(key=lambda item: item["name"])
    return {"interfaces": results, "error": None}


def _get_host_instance_port_stats() -> dict:
    try:
        proc = _run_host_shell(
            r"""
if ! command -v ovs-vsctl >/dev/null 2>&1; then
  echo "__ERROR__ ovs-vsctl not found"
  exit 0
fi
ovs-vsctl --data=bare --no-heading --columns=name list Interface 2>/dev/null | while read -r name; do
  [ -n "$name" ] || continue
  iface_id=$(ovs-vsctl --if-exists get Interface "$name" external_ids:iface-id 2>/dev/null | tr -d '"')
  [ -n "$iface_id" ] || continue
  [ -r "/sys/class/net/$name/statistics/rx_bytes" ] || continue
  rx=$(cat "/sys/class/net/$name/statistics/rx_bytes" 2>/dev/null)
  tx=$(cat "/sys/class/net/$name/statistics/tx_bytes" 2>/dev/null)
  oper=$(cat "/sys/class/net/$name/operstate" 2>/dev/null)
  printf '%s|%s|%s|%s|%s\n' "$iface_id" "$name" "${rx:-0}" "${tx:-0}" "${oper:-unknown}"
done
""",
            timeout=10,
        )
    except Exception as exc:
        return {"ports": [], "error": str(exc)}

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        return {"ports": [], "error": stderr or f"instance port stats command exited {proc.returncode}"}

    if "__ERROR__" in proc.stdout:
        return {"ports": [], "error": "ovs-vsctl not found"}

    now = time.time()
    results: list[dict] = []
    with _instance_port_prev_lock:
        for raw_line in proc.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("__ERROR__"):
                continue
            parts = line.split("|")
            if len(parts) != 5:
                continue
            port_id, iface_name, rx_raw, tx_raw, operstate = parts
            try:
                rx_bytes = int(rx_raw)
                tx_bytes = int(tx_raw)
            except ValueError:
                continue
            rx_rate_bps = None
            tx_rate_bps = None
            previous = _instance_port_prev_samples.get(port_id)
            if previous is not None:
                prev_time, prev_rx, prev_tx = previous
                elapsed = now - prev_time
                if elapsed > 0:
                    rx_rate_bps = max(0.0, (rx_bytes - prev_rx) / elapsed)
                    tx_rate_bps = max(0.0, (tx_bytes - prev_tx) / elapsed)
            _instance_port_prev_samples[port_id] = (now, rx_bytes, tx_bytes)
            results.append({
                "port_id": port_id,
                "interface_name": iface_name,
                "operstate": operstate or "unknown",
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_bytes_per_second": rx_rate_bps,
                "tx_bytes_per_second": tx_rate_bps,
            })

    results.sort(key=lambda item: (item["port_id"], item["interface_name"]))
    return {"ports": results, "error": None}


def _get_named_interface_stats(interface_names: list[str]) -> dict:
    requested = [name for name in interface_names if name]
    if not requested:
        return {"interfaces": [], "error": None}

    try:
        proc = _run_host_shell(
            "\n".join(
                [
                    "for name in " + " ".join(shlex.quote(name) for name in requested) + "; do",
                    '  [ -r "/sys/class/net/$name/statistics/rx_bytes" ] || continue',
                    '  rx=$(cat "/sys/class/net/$name/statistics/rx_bytes" 2>/dev/null)',
                    '  tx=$(cat "/sys/class/net/$name/statistics/tx_bytes" 2>/dev/null)',
                    '  oper=$(cat "/sys/class/net/$name/operstate" 2>/dev/null)',
                    '  printf \'%s|%s|%s|%s\\n\' "$name" "${rx:-0}" "${tx:-0}" "${oper:-unknown}"',
                    "done",
                ]
            ),
            timeout=10,
        )
    except Exception as exc:
        return {"interfaces": [], "error": str(exc)}

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        return {"interfaces": [], "error": stderr or f"interface stats command exited {proc.returncode}"}

    now = time.time()
    results: list[dict] = []
    with _interface_prev_lock:
        for raw_line in proc.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) != 4:
                continue
            iface_name, rx_raw, tx_raw, operstate = parts
            try:
                rx_bytes = int(rx_raw)
                tx_bytes = int(tx_raw)
            except ValueError:
                continue
            rx_rate_bps = None
            tx_rate_bps = None
            previous = _interface_prev_samples.get(iface_name)
            if previous is not None:
                prev_time, prev_rx, prev_tx = previous
                elapsed = now - prev_time
                if elapsed > 0:
                    rx_rate_bps = max(0.0, (rx_bytes - prev_rx) / elapsed)
                    tx_rate_bps = max(0.0, (tx_bytes - prev_tx) / elapsed)
            _interface_prev_samples[iface_name] = (now, rx_bytes, tx_bytes)
            results.append({
                "name": iface_name,
                "operstate": operstate or "unknown",
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_bytes_per_second": rx_rate_bps,
                "tx_bytes_per_second": tx_rate_bps,
            })

    results.sort(key=lambda item: item["name"])
    return {"interfaces": results, "error": None}


def _mask_enabled(value: str | None) -> bool:
    if not value:
        return False
    cleaned = value.strip().replace(",", "").replace("0x", "").lower()
    return any(ch not in {"0"} for ch in cleaned)


def _get_host_irq_balance() -> dict:
    try:
        proc = _run_host_shell(
            r"""
echo __IFACES__
for d in /sys/class/net/*/; do
  name=$(basename "$d")
  is_phys=0; is_bond=0
  [ -e "${d}device" ] && is_phys=1
  [ -d "${d}bonding" ] && is_bond=1
  [ "$is_phys" = "0" ] && [ "$is_bond" = "0" ] && continue
  rx=$(cat "${d}statistics/rx_bytes" 2>/dev/null)
  tx=$(cat "${d}statistics/tx_bytes" 2>/dev/null)
  rxq=$(find "${d}queues" -maxdepth 1 -type d -name 'rx-*' 2>/dev/null | wc -l | tr -d ' ')
  txq=$(find "${d}queues" -maxdepth 1 -type d -name 'tx-*' 2>/dev/null | wc -l | tr -d ' ')
  rps=0
  for f in "${d}"queues/rx-*/rps_cpus; do
    [ -r "$f" ] || continue
    v=$(cat "$f" 2>/dev/null | tr -d '\n')
    case "${v}" in
      ""|0|00|000|0,0|0,00|00,00) ;;
      *) rps=1; break ;;
    esac
  done
  xps=0
  for f in "${d}"queues/tx-*/xps_cpus; do
    [ -r "$f" ] || continue
    v=$(cat "$f" 2>/dev/null | tr -d '\n')
    case "${v}" in
      ""|0|00|000|0,0|0,00|00,00) ;;
      *) xps=1; break ;;
    esac
  done
  printf '%s|%s|%s|%s|%s|%s|%s\n' "$name" "${rx:-0}" "${tx:-0}" "${rxq:-0}" "${txq:-0}" "$rps" "$xps"
done
echo __INTERRUPTS__
cat /proc/interrupts 2>/dev/null
echo __END__
""",
            timeout=10,
        )
    except Exception as exc:
        return {"interfaces": [], "error": str(exc)}

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        return {"interfaces": [], "error": stderr or f"irq balance command exited {proc.returncode}"}

    section = None
    iface_rows: dict[str, dict] = {}
    interrupt_lines: list[str] = []
    for raw_line in proc.stdout.splitlines():
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        if stripped == "__IFACES__":
            section = "ifaces"
            continue
        if stripped == "__INTERRUPTS__":
            section = "interrupts"
            continue
        if stripped == "__END__":
            break
        if not stripped:
            continue
        if section == "ifaces":
            parts = stripped.split("|")
            if len(parts) != 7:
                continue
            name, rx_raw, tx_raw, rxq_raw, txq_raw, rps_raw, xps_raw = parts
            try:
                iface_rows[name] = {
                    "name": name,
                    "rx_bytes": int(rx_raw),
                    "tx_bytes": int(tx_raw),
                    "rx_queues": int(rxq_raw),
                    "tx_queues": int(txq_raw),
                    "rps_enabled": rps_raw == "1",
                    "xps_enabled": xps_raw == "1",
                }
            except ValueError:
                continue
        elif section == "interrupts":
            interrupt_lines.append(line)

    if not interrupt_lines:
        return {"interfaces": [], "error": "no /proc/interrupts data available"}

    header = interrupt_lines[0].split()
    cpu_count = len([item for item in header if item.startswith("CPU")])
    irq_by_iface: dict[str, list[int]] = {name: [0] * cpu_count for name in iface_rows}
    for line in interrupt_lines[1:]:
        if ":" not in line:
            continue
        _, _, rest = line.partition(":")
        parts = rest.split()
        if len(parts) < cpu_count + 1:
            continue
        counts_raw = parts[:cpu_count]
        detail = " ".join(parts[cpu_count:])
        counts: list[int] = []
        valid = True
        for item in counts_raw:
            try:
                counts.append(int(item))
            except ValueError:
                valid = False
                break
        if not valid:
            continue
        for iface_name in iface_rows:
            if iface_name and iface_name in detail:
                current = irq_by_iface[iface_name]
                irq_by_iface[iface_name] = [current[idx] + counts[idx] for idx in range(cpu_count)]

    now = time.time()
    results: list[dict] = []
    with _irq_balance_prev_lock:
        for iface_name, base in iface_rows.items():
            rx_bytes = base["rx_bytes"]
            tx_bytes = base["tx_bytes"]
            rx_rate_bps = None
            tx_rate_bps = None
            previous = _irq_balance_prev_samples.get(iface_name)
            if previous is not None:
                prev_time, prev_rx, prev_tx = previous
                elapsed = now - prev_time
                if elapsed > 0:
                    rx_rate_bps = max(0.0, (rx_bytes - prev_rx) / elapsed)
                    tx_rate_bps = max(0.0, (tx_bytes - prev_tx) / elapsed)
            _irq_balance_prev_samples[iface_name] = (now, rx_bytes, tx_bytes)

            cpu_counts = irq_by_iface.get(iface_name, [])
            irq_total = sum(cpu_counts)
            active_cpus = sum(1 for value in cpu_counts if value > 0)
            top_idx = max(range(len(cpu_counts)), key=lambda idx: cpu_counts[idx]) if cpu_counts else None
            top_cpu_count = cpu_counts[top_idx] if top_idx is not None else 0
            top_cpu_share_pct = round((top_cpu_count / irq_total) * 100, 1) if irq_total > 0 else None
            top_cpu = f"CPU{top_idx}" if top_idx is not None and top_cpu_count > 0 else None
            traffic_bps = max(value or 0.0 for value in (rx_rate_bps, tx_rate_bps))
            reason = "Balanced or idle"
            risk = "low"
            if irq_total <= 0:
                reason = "No interface IRQ activity observed"
            elif traffic_bps >= 100_000_000 and top_cpu_share_pct is not None and top_cpu_share_pct >= 70:
                risk = "high"
                reason = f"IRQ concentration on {top_cpu} under active traffic"
            elif traffic_bps >= 100_000_000 and active_cpus <= 1 and max(base["rx_queues"], base["tx_queues"]) > 1:
                risk = "high"
                reason = "Single active CPU handling multi-queue traffic"
            elif traffic_bps >= 25_000_000 and top_cpu_share_pct is not None and top_cpu_share_pct >= 50:
                risk = "medium"
                reason = f"{top_cpu} handling most interrupts"
            elif traffic_bps >= 25_000_000 and base["rx_queues"] > 1 and active_cpus < min(4, base["rx_queues"]):
                risk = "medium"
                reason = "Interrupts spread across too few CPUs for queue count"

            results.append({
                "name": iface_name,
                "rx_bytes_per_second": rx_rate_bps,
                "tx_bytes_per_second": tx_rate_bps,
                "irq_total": irq_total,
                "active_cpus": active_cpus,
                "cpu_count": cpu_count,
                "top_cpu": top_cpu,
                "top_cpu_share_pct": top_cpu_share_pct,
                "rx_queues": base["rx_queues"],
                "tx_queues": base["tx_queues"],
                "rps_enabled": base["rps_enabled"],
                "xps_enabled": base["xps_enabled"],
                "risk": risk,
                "reason": reason,
            })

    results.sort(key=lambda item: item["name"])
    return {"interfaces": results, "error": None}
