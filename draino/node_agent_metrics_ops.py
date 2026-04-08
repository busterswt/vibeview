"""Node-agent host metrics and network stats probes."""
from __future__ import annotations

import os
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
