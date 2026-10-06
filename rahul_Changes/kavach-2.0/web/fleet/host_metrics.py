"""Local host CPU / RAM / disk (no broker, no Telegram)."""

from __future__ import annotations

import os
import shutil
import time
from typing import Any

_LAST_CPU: tuple[float, float] | None = None  # (idle, total)


def _read_proc_stat() -> tuple[float, float] | None:
    try:
        with open("/proc/stat", encoding="utf-8") as fh:
            line = fh.readline()
        parts = line.split()
        if parts[0] != "cpu":
            return None
        nums = [float(x) for x in parts[1:]]
        idle = nums[3] + (nums[4] if len(nums) > 4 else 0.0)
        total = sum(nums)
        return idle, total
    except Exception:
        return None


def _loadavg_cpu() -> float | None:
    try:
        load1 = os.getloadavg()[0]
        ncpu = os.cpu_count() or 1
        return round(min(100.0, (load1 / max(ncpu, 1)) * 100.0), 1)
    except Exception:
        return None


def cpu_percent() -> float | None:
    global _LAST_CPU
    cur = _read_proc_stat()
    if cur is None:
        return _loadavg_cpu()
    if _LAST_CPU is None:
        _LAST_CPU = cur
        time.sleep(0.12)
        cur2 = _read_proc_stat()
        if cur2 is None:
            return _loadavg_cpu()
        prev, cur = _LAST_CPU, cur2
    else:
        prev, cur = _LAST_CPU, cur
    _LAST_CPU = cur
    di = cur[0] - prev[0]
    dt = cur[1] - prev[1]
    if dt < 5:  # too short a window — prefer loadavg
        alt = _loadavg_cpu()
        if alt is not None:
            return alt
    if dt <= 0:
        return 0.0
    used = max(0.0, min(100.0, (1.0 - di / dt) * 100.0))
    return round(used, 1)


def mem_percent() -> tuple[float | None, float | None, float | None]:
    """Return (percent_used, used_gb, total_gb)."""
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                info[k.strip()] = int(v.strip().split()[0])
        total_kb = float(info.get("MemTotal") or 0)
        avail_kb = float(info.get("MemAvailable") or info.get("MemFree") or 0)
        if total_kb <= 0:
            return None, None, None
        used_kb = total_kb - avail_kb
        pct = round((used_kb / total_kb) * 100.0, 1)
        return pct, round(used_kb / (1024 * 1024), 2), round(total_kb / (1024 * 1024), 2)
    except Exception:
        return None, None, None


def disk_percent(path: str = "/") -> tuple[float | None, float | None, float | None]:
    try:
        u = shutil.disk_usage(path)
        pct = round((u.used / u.total) * 100.0, 1) if u.total else None
        return pct, round(u.used / (1024**3), 2), round(u.total / (1024**3), 2)
    except Exception:
        return None, None, None


def _unit_active(unit: str) -> str:
    try:
        import subprocess

        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return (r.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


def _process_cpu_by_name(needles: tuple[str, ...]) -> tuple[bool, float | None, int | None]:
    """Return (found, cpu_pct_of_one_core_scaled_to_100_host, pid) for first matching process."""
    try:
        import subprocess

        r = subprocess.run(
            ["ps", "-eo", "pid,pcpu,comm,args", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode != 0:
            return False, None, None
        ncpu = max(os.cpu_count() or 1, 1)
        best = None
        for line in (r.stdout or "").splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) < 4:
                continue
            pid_s, pcpu_s, _comm, args = parts
            blob = f"{_comm} {args}".lower()
            if not any(n in blob for n in needles):
                continue
            try:
                pid = int(pid_s)
                pcpu = float(pcpu_s)
            except Exception:
                continue
            # ps %CPU can exceed 100 on multi-core; normalize to 0–100 host share
            host_pct = round(min(100.0, (pcpu / ncpu)), 1)
            row = (pid, host_pct, pcpu)
            if best is None or row[2] > best[2]:
                best = row
        if not best:
            return False, None, None
        return True, best[1], best[0]
    except Exception:
        return False, None, None


def collect_host_health() -> dict[str, Any]:
    cpu = cpu_percent()
    ram_pct, ram_used, ram_total = mem_percent()
    disk_pct, disk_used, disk_total = disk_percent("/")
    load = None
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except Exception:
        pass
    units: dict[str, str] = {
        "batman-kavach2": _unit_active("batman-kavach2.service"),
        "datafeedbot": _unit_active("datafeedbot.service"),
    }
    feeder_found, feeder_cpu, feeder_pid = _process_cpu_by_name(
        ("datafeedbot", "run_datafeed", "datafeed_bot", "dhan_feed")
    )
    feeder_unit_ok = units.get("datafeedbot") == "active"
    feeder_ok = bool(feeder_unit_ok or feeder_found)
    return {
        "ok": True,
        "cpu_pct": cpu,
        "ram_pct": ram_pct,
        "ram_used_gb": ram_used,
        "ram_total_gb": ram_total,
        "disk_pct": disk_pct,
        "disk_used_gb": disk_used,
        "disk_total_gb": disk_total,
        "load": load,
        "units": units,
        "feeder_ok": feeder_ok,
        "feeder_cpu_pct": feeder_cpu,
        "feeder_pid": feeder_pid,
        "feeder_unit": units.get("datafeedbot"),
        "ts": time.time(),
    }
