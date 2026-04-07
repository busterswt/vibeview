"""HTTPS node-local reboot agent."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

_state_lock = threading.Lock()
_reboot_in_progress = False
_LOGGER = logging.getLogger("draino.node_agent")
_HOST_STATIC_DETAIL_TTL = float(os.getenv("DRAINO_NODE_AGENT_HOST_DETAIL_TTL", "600"))
_HOST_METRICS_TTL = float(os.getenv("DRAINO_NODE_AGENT_HOST_METRICS_TTL", "30"))
_HOST_METRICS_HISTORY_LIMIT = int(os.getenv("DRAINO_NODE_AGENT_HOST_METRICS_HISTORY_LIMIT", "30"))
_host_static_detail_cache: tuple[float, dict] | None = None
_host_static_detail_lock = threading.Lock()
_host_metrics_cache: tuple[float, dict] | None = None
_host_metrics_history: list[dict] = []
_host_metrics_lock = threading.Lock()


class RebootRequest(BaseModel):
    request_id: str
    expected_node: str | None = None
    hypervisor: str | None = None


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _read_token() -> str:
    with open(_env("DRAINO_NODE_AGENT_TOKEN_FILE"), "r", encoding="utf-8") as fh:
        return fh.read().strip()


def _node_name() -> str:
    return _env("DRAINO_NODE_NAME")


def _authorise(authorization: str | None) -> None:
    expected = f"Bearer {_read_token()}"
    if authorization != expected:
        _LOGGER.warning("unauthorised request node=%s", os.getenv("DRAINO_NODE_NAME", "unknown"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorised",
        )


def _reboot_host() -> None:
    global _reboot_in_progress
    try:
        _LOGGER.info("reboot command starting node=%s", _node_name())
        subprocess.run(
            ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "reboot"],
            timeout=15,
            capture_output=True,
            check=False,
        )
    finally:
        _LOGGER.info("reboot command finished node=%s", _node_name())
        with _state_lock:
            _reboot_in_progress = False


def _run_host_shell(script: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "sh", "-lc", script],
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )


def _get_dynamic_host_detail() -> dict:
    result = {
        "hostname": None,
        "architecture": None,
        "kernel_version": None,
        "uptime": None,
        "error": None,
    }

    script = (
        "echo __N__; hostname 2>/dev/null; "
        "echo __A__; uname -m 2>/dev/null; "
        "echo __U__; uptime -p 2>/dev/null | sed 's/^up //'; "
        "echo __R__; uname -r 2>/dev/null; "
        "echo __END__"
    )

    try:
        proc = _run_host_shell(script)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        result["error"] = stderr or f"dynamic host detail command exited {proc.returncode}"
        return result

    section = None
    for line in proc.stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        if s == "__N__":
            section = "hostname"
            continue
        if s == "__A__":
            section = "arch"
            continue
        if s == "__U__":
            section = "uptime"
            continue
        if s == "__R__":
            section = "kernel"
            continue
        if s == "__END__":
            break
        if section == "hostname":
            result["hostname"] = s
        elif section == "arch":
            result["architecture"] = s
        elif section == "uptime":
            result["uptime"] = s
        elif section == "kernel":
            result["kernel_version"] = s

    return result


def _get_static_host_detail() -> dict:
    result: dict = {
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

    script = (
        "echo __V__; cat /sys/class/dmi/id/sys_vendor 2>/dev/null; "
        "echo __P__; cat /sys/class/dmi/id/product_name 2>/dev/null; "
        "echo __B__; cat /sys/class/dmi/id/bios_version 2>/dev/null; "
        "echo __C__; grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//'; "
        "echo __S__; grep 'physical id' /proc/cpuinfo 2>/dev/null | sort -u | wc -l; "
        "echo __K__; grep -m1 'cpu cores' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//'; "
        "echo __H__; grep -m1 'siblings' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//'; "
        "echo __D__; (dmidecode -t 17 2>/dev/null) | "
        r"grep -E '^\s+(Size|Type|Speed|Manufacturer):' | "
        "grep -v 'No Module Installed'; "
        "echo __END__"
    )

    try:
        proc = _run_host_shell(script)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        result["error"] = stderr or f"host detail command exited {proc.returncode}"
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
        if s == "__V__":
            section = "vendor"
            continue
        if s == "__P__":
            section = "product"
            continue
        if s == "__B__":
            section = "bios"
            continue
        if s == "__C__":
            section = "cpu"
            continue
        if s == "__S__":
            section = "sockets"
            continue
        if s == "__K__":
            section = "cores"
            continue
        if s == "__H__":
            section = "siblings"
            continue
        if s == "__D__":
            section = "dmi"
            continue
        if s == "__END__":
            break

        if section == "vendor":
            result["vendor"] = s
        elif section == "product":
            result["product"] = s
        elif section == "bios":
            result["bios_version"] = s
        elif section == "cpu":
            cleaned = re.sub(r"\([RT]M\)", "", s)
            cleaned = re.sub(r"\bCPU\s+@\s+[\d.]+\s*GHz\b", "", cleaned)
            cleaned = re.sub(r"\b\d+-Core\s+Processor\b", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
            result["cpu_model"] = cleaned
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
        elif section == "dmi" and ":" in s:
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
            elif key == "Type" and val not in ("Unknown", "Other", ""):
                dmi_types.append(val)
            elif key == "Speed" and val not in ("Unknown", "0 MT/s", "0 MHz", ""):
                dmi_speeds.append(val)
            elif key == "Manufacturer" and val not in ("Unknown", "Not Specified", ""):
                dmi_mfrs.append(val)

    if dmi_sizes:
        result["ram_total_gb"] = sum(dmi_sizes)
        result["ram_slots_used"] = len(dmi_sizes)
    if dmi_types:
        result["ram_type"] = Counter(dmi_types).most_common(1)[0][0]
    if dmi_speeds:
        result["ram_speed"] = Counter(dmi_speeds).most_common(1)[0][0]
    if dmi_mfrs:
        result["ram_manufacturer"] = Counter(dmi_mfrs).most_common(1)[0][0]
    return result


def _get_cached_static_host_detail(now: float | None = None) -> dict:
    global _host_static_detail_cache
    check_time = time.time() if now is None else now
    with _host_static_detail_lock:
        if _host_static_detail_cache is not None:
            expires_at, payload = _host_static_detail_cache
            if check_time < expires_at:
                return dict(payload)
        payload = _get_static_host_detail()
        _host_static_detail_cache = (check_time + _HOST_STATIC_DETAIL_TTL, dict(payload))
        return payload


def _get_host_detail() -> dict:
    dynamic = _get_dynamic_host_detail()
    static = _get_cached_static_host_detail()
    result = {**dynamic, **static}
    errors = [err for err in (dynamic.get("error"), static.get("error")) if err]
    result["error"] = "; ".join(errors) if errors else None
    return result


def _get_network_interfaces() -> dict:
    script = r"""
for d in /sys/class/net/*/; do
  name=$(basename "$d")
  is_phys=0; is_bond=0
  [ -e "${d}device" ] && is_phys=1
  [ -d "${d}bonding" ] && is_bond=1
  [ "$is_phys" = "0" ] && [ "$is_bond" = "0" ] && continue
  printf '__NIC__ %s\n' "$name"
  printf 'oper=%s\n'      "$(cat ${d}operstate 2>/dev/null)"
  printf 'mac=%s\n'       "$(cat ${d}address 2>/dev/null)"
  printf 'speed_mbps=%s\n' "$(cat ${d}speed 2>/dev/null)"
  printf 'duplex=%s\n'    "$(cat ${d}duplex 2>/dev/null)"
  printf 'ipv4=%s\n'     "$(ip -4 addr show "$name" 2>/dev/null | awk '/inet /{print $2}' | paste -sd, -)"
  printf 'ipv6=%s\n'     "$(ip -6 addr show "$name" 2>/dev/null | awk '/inet6 / && $2 !~ /^fe80/{print $2}' | paste -sd, -)"
  if [ "$is_bond" = "1" ]; then
    printf 'type=bond\n'
    printf 'slaves=%s\n'  "$(cat ${d}bonding/slaves 2>/dev/null)"
    printf 'mode=%s\n'    "$(cat ${d}bonding/mode 2>/dev/null | cut -d' ' -f1)"
  else
    printf 'type=physical\n'
    printf 'driver=%s\n'  "$(ethtool -i "$name" 2>/dev/null | awk '/^driver:/{print $2}')"
    printf 'model=%s\n'   "$(udevadm info "${d}" 2>/dev/null | awk -F= '/^E: ID_MODEL_FROM_DATABASE=/{sub(/^[^=]*=/, ""); print; exit}')"
    printf 'vendor=%s\n'  "$(udevadm info "${d}" 2>/dev/null | awk -F= '/^E: ID_VENDOR_FROM_DATABASE=/{sub(/^[^=]*=/, ""); print; exit}')"
  fi
  printf '__END_NIC__\n'
done
"""

    def fmt_speed(mbps_str: str) -> tuple[str | None, int | None]:
        try:
            value = int(mbps_str)
        except (ValueError, TypeError):
            return None, None
        if value <= 0:
            return None, None
        if value >= 100_000:
            return "100G", value
        if value >= 40_000:
            return "40G", value
        if value >= 25_000:
            return "25G", value
        if value >= 10_000:
            return "10G", value
        if value >= 1_000:
            return "1G", value
        return f"{value}M", value

    try:
        proc = _run_host_shell(script)
    except Exception as exc:
        return {"interfaces": [], "error": str(exc)}

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        return {"interfaces": [], "error": stderr or f"interface command exited {proc.returncode}"}

    interfaces: list[dict] = []
    current: dict | None = None

    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("__NIC__ "):
            current = {
                "name": line[len("__NIC__ "):].strip(),
                "type": "physical",
                "status": "unknown",
                "speed": None,
                "speed_mbps": None,
                "mac": None,
                "duplex": None,
                "driver": None,
                "model": None,
                "vendor": None,
                "members": [],
                "mode": None,
                "ipv4": [],
                "ipv6": [],
            }
        elif line == "__END_NIC__":
            if current is not None:
                interfaces.append(current)
            current = None
        elif current is not None and "=" in line:
            key, _, val = line.partition("=")
            val = val.strip()
            if key == "oper":
                current["status"] = val if val else "unknown"
            elif key == "mac":
                current["mac"] = val or None
            elif key == "speed_mbps":
                speed, raw = fmt_speed(val)
                current["speed"] = speed
                current["speed_mbps"] = raw
            elif key == "duplex":
                current["duplex"] = val or None
            elif key == "type":
                current["type"] = val
            elif key == "driver":
                current["driver"] = val or None
            elif key == "model":
                current["model"] = val or None
            elif key == "vendor":
                current["vendor"] = val or None
            elif key == "slaves":
                current["members"] = [s for s in val.split() if s]
            elif key == "mode":
                current["mode"] = val or None
            elif key == "ipv4":
                current["ipv4"] = [a for a in val.split(",") if a.strip()]
            elif key == "ipv6":
                current["ipv6"] = [a for a in val.split(",") if a.strip()]

    interfaces.sort(key=lambda x: (0 if x["type"] == "bond" else 1, x["name"]))
    return {"interfaces": interfaces, "error": None}


def _get_etcd_status() -> dict:
    try:
        proc = _run_host_shell("systemctl is-active etcd", timeout=10)
        return {
            "active": proc.stdout.strip() == "active",
            "error": None if proc.returncode == 0 or proc.stdout.strip() else proc.stderr.strip() or None,
        }
    except Exception as exc:
        return {"active": None, "error": str(exc)}


def _get_host_signals() -> dict:
    try:
        proc = _run_host_shell(
            "running=$(uname -r 2>/dev/null); "
            "latest=$(ls -1 /lib/modules 2>/dev/null | sort -V | tail -n1); "
            "need=no; "
            "[ -f /var/run/reboot-required ] && need=yes; "
            "if [ -n \"$latest\" ] && [ -n \"$running\" ] && [ \"$latest\" != \"$running\" ]; then need=yes; fi; "
            "printf 'running=%s\nlatest=%s\nreboot_required=%s\n' \"$running\" \"$latest\" \"$need\"",
            timeout=10,
        )
    except Exception as exc:
        return {
            "kernel_version": None,
            "latest_kernel_version": None,
            "reboot_required": False,
            "error": str(exc),
        }

    if proc.returncode != 0 and not proc.stdout.strip():
        stderr = proc.stderr.strip()
        return {
            "kernel_version": None,
            "latest_kernel_version": None,
            "reboot_required": False,
            "error": stderr or f"host signals command exited {proc.returncode}",
        }

    data = {
        "kernel_version": None,
        "latest_kernel_version": None,
        "reboot_required": False,
        "error": None,
    }
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key == "running":
            data["kernel_version"] = val or None
        elif key == "latest":
            data["latest_kernel_version"] = val or None
        elif key == "reboot_required":
            data["reboot_required"] = val.strip().lower() in {"yes", "true", "1"}
    return data


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
    filesystems: list[dict] = []

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
                filesystems.append({
                    "mount": mount,
                    "total_kb": int(total),
                    "used_kb": int(used),
                    "available_kb": int(available),
                    "used_percent": int(used_pct.rstrip("%")),
                })
            except ValueError:
                continue

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


node_agent_app = FastAPI(title="Draino Node Agent")


@node_agent_app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@node_agent_app.get("/readyz")
def readyz() -> dict[str, str]:
    return {"status": "ready"}


@node_agent_app.get("/status")
def agent_status(authorization: str | None = Header(default=None)) -> dict[str, object]:
    _authorise(authorization)
    _LOGGER.info("status requested node=%s", _node_name())
    return {
        "node": _node_name(),
        "reboot_in_progress": _reboot_in_progress,
    }


@node_agent_app.get("/host/detail")
def host_detail(authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    _LOGGER.info("host detail requested node=%s", _node_name())
    return _get_host_detail()


@node_agent_app.get("/host/network-interfaces")
def host_network_interfaces(authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    _LOGGER.info("network interfaces requested node=%s", _node_name())
    return _get_network_interfaces()


@node_agent_app.get("/host/etcd")
def host_etcd_status(authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    _LOGGER.info("etcd status requested node=%s", _node_name())
    return _get_etcd_status()


@node_agent_app.get("/host/signals")
def host_signals(authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    _LOGGER.info("host signals requested node=%s", _node_name())
    return _get_host_signals()


@node_agent_app.get("/host/metrics")
def host_metrics(authorization: str | None = Header(default=None)) -> dict:
    _authorise(authorization)
    _LOGGER.info("host metrics requested node=%s", _node_name())
    return _get_host_metrics()


@node_agent_app.post("/reboot", status_code=status.HTTP_202_ACCEPTED)
def reboot(
    payload: RebootRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    global _reboot_in_progress
    _authorise(authorization)

    node_name = _node_name()
    if payload.expected_node and payload.expected_node != node_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"request targeted '{payload.expected_node}' but this agent serves '{node_name}'",
        )

    with _state_lock:
        if _reboot_in_progress:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="reboot already in progress",
            )
        _reboot_in_progress = True

    _LOGGER.info("reboot accepted node=%s request_id=%s", node_name, payload.request_id)
    threading.Thread(target=_reboot_host, daemon=True).start()
    return {"accepted": True, "node": node_name, "request_id": payload.request_id}


def run(host: str = "0.0.0.0", port: int = 8443) -> None:
    cert_file = _env("DRAINO_NODE_AGENT_TLS_CERT_FILE")
    key_file = _env("DRAINO_NODE_AGENT_TLS_KEY_FILE")
    token_file = _env("DRAINO_NODE_AGENT_TOKEN_FILE")
    for path in (cert_file, key_file, token_file):
        if not Path(path).exists():
            raise RuntimeError(f"required file does not exist: {path}")
    _LOGGER.info("node agent starting node=%s host=%s port=%s", _node_name(), host, port)
    uvicorn.run(
        node_agent_app,
        host=host,
        port=port,
        log_level="warning",
        ssl_certfile=cert_file,
        ssl_keyfile=key_file,
    )
