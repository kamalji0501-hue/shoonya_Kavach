from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_WATCH_UNITS = [
    ("Datafeedbot", "datafeedbot.service"),
    ("BlitzBot", "blitzbot.service"),
    ("GoBot", "gobot.service"),
    ("VPS Health", "vps-health-bot.service"),
    ("Place Order", "place-order-bot.service"),
    ("Salon Summary", "salon-summary.service"),
    ("Batman Drishti", "batman-drishti.service"),
    ("Batman Jagran", "batman-jagran.service"),
    ("Batman Kavach2", "batman-kavach2.service"),
    ("Batman Saransh", "batman-saransh.service"),
    ("Batman Monitor", "vps-monitor-batman.service"),
]


def watch_units() -> list[tuple[str, str]]:
    raw = os.getenv("VPS_HEALTH_WATCH", "").strip()
    if not raw:
        return list(DEFAULT_WATCH_UNITS)
    out: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        name, unit = part.split(":", 1)
        unit = unit.strip()
        if not unit.endswith(".service"):
            unit += ".service"
        out.append((name.strip(), unit))
    return out or list(DEFAULT_WATCH_UNITS)

TOKEN_FILES = [
    Path("/home/ubuntu/BlitzBot_Runtime/Data/data/shared/access_token.json"),
    Path("/home/ubuntu/Datafeedbot_Runtime/Data/shared/access_token.json"),
    Path("/home/ubuntu/GoBot_Runtime/Data/data/shared/access_token.json"),
    Path("/home/ubuntu/Datafeedbot_Runtime/Credentials/zerodha/kite_session.json"),
]

FEED_HEALTH_LOG = Path("/home/ubuntu/Datafeedbot_Runtime/Logs/feed_health.log")
FAILOVER_LOG = Path("/home/ubuntu/Datafeedbot_Runtime/Logs/failover.log")
MARKET_DATA_LOG = Path("/home/ubuntu/Datafeedbot_Runtime/Logs/market_data.log")

_TG_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")


def _run(cmd: list[str] | str, timeout: float = 8.0) -> str:
    try:
        if isinstance(cmd, str):
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
        else:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
        return _TG_TOKEN_RE.sub("bot[redacted]", out.strip())
    except Exception as exc:  # noqa: BLE001
        return f"(failed: {exc})"


def _systemctl(unit: str, field: str) -> str:
    out = _run(["systemctl", field, unit], timeout=5)
    return out.splitlines()[0].strip() if out else "unknown"


def _age_seconds(path: Path) -> float | None:
    if not path.exists():
        return None
    return max(0.0, time.time() - path.stat().st_mtime)


def _fmt_age(sec: float | None) -> str:
    if sec is None:
        return "missing"
    if sec < 60:
        return f"{int(sec)}s"
    if sec < 3600:
        return f"{int(sec / 60)}m"
    if sec < 86400:
        return f"{int(sec / 3600)}h {int((sec % 3600) / 60)}m"
    return f"{int(sec / 86400)}d"


def in_market_hours(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    t = now.timetz() if False else now.time()
    return dtime(9, 0) <= t <= dtime(15, 40)


@dataclass
class BotStatus:
    name: str
    unit: str
    active: str
    enabled: str
    pid: str = "0"
    cwd: str = ""
    uptime: str = ""
    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    rss_mb: float = 0.0
    threads: int = 0
    nrestarts: str = ""
    notes: str = ""


@dataclass
class ProcRow:
    pid: str
    user: str
    cpu: str
    mem: str
    rss_kb: str
    command: str


@dataclass
class Snapshot:
    ts: datetime
    hostname: str
    uptime: str
    load: str
    nproc: int
    load1: float
    cpu_idle_pct: float
    cpu_iowait_pct: float
    cpu_steal_pct: float
    cpu_iowait_instant_pct: float
    cpu_steal_instant_pct: float
    mem_total_mb: float
    mem_available_mb: float
    mem_used_pct: float
    swap_used_mb: float
    swap_total_mb: float
    disk_used_pct: float
    disk_avail: str
    inode_pct: float
    market_open: bool
    bots: list[BotStatus] = field(default_factory=list)
    top_cpu: list[ProcRow] = field(default_factory=list)
    top_mem: list[ProcRow] = field(default_factory=list)
    place_order: dict[str, Any] = field(default_factory=dict)
    hft: dict[str, Any] = field(default_factory=dict)
    log_summary: dict[str, Any] = field(default_factory=dict)
    network: dict[str, Any] = field(default_factory=dict)
    tokens: list[dict[str, str]] = field(default_factory=list)
    journals: dict[str, str] = field(default_factory=dict)
    chrony_ok: bool = True
    fail2ban: str = ""
    concerns: list[str] = field(default_factory=list)
    went_well: list[str] = field(default_factory=list)
    peaks: dict[str, Any] = field(default_factory=dict)


def collect_memory() -> tuple[float, float, float, float, float]:
    meminfo = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
    vals: dict[str, float] = {}
    for line in meminfo.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            vals[parts[0][:-1]] = float(parts[1])
    total = vals.get("MemTotal", 1.0) / 1024
    available = vals.get("MemAvailable", 0.0) / 1024
    used_pct = max(0.0, min(100.0, (1.0 - available / total) * 100.0))
    swap_total = vals.get("SwapTotal", 0.0) / 1024
    swap_free = vals.get("SwapFree", 0.0) / 1024
    swap_used = max(0.0, swap_total - swap_free)
    return total, available, used_pct, swap_used, swap_total


def collect_disk() -> tuple[float, str, float]:
    usage = shutil.disk_usage("/")
    pct = (usage.used / usage.total) * 100.0 if usage.total else 0.0
    avail_g = usage.free / (1024**3)
    inode_pct = 0.0
    df = _run("df -i / | tail -1", timeout=3)
    parts = df.split()
    if len(parts) >= 5 and parts[4].endswith("%"):
        try:
            inode_pct = float(parts[4].rstrip("%"))
        except ValueError:
            inode_pct = 0.0
    return pct, f"{avail_g:.1f}G free", inode_pct


def _cpu_parts() -> list[float]:
    line = Path("/proc/stat").read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    parts = [float(x) for x in line.split()[1:]]
    while len(parts) < 8:
        parts.append(0.0)
    return parts


def collect_cpu_split() -> tuple[float, float, float]:
    """Idle / iowait / steal percentages from /proc/stat (since boot)."""
    parts = _cpu_parts()
    total = sum(parts) or 1.0
    return (parts[3] / total) * 100.0, (parts[4] / total) * 100.0, (parts[7] / total) * 100.0


def collect_cpu_instant(sleep_s: float = 0.35) -> tuple[float, float, float]:
    """Short-window idle / iowait / steal (what AWS feels right now)."""
    a = _cpu_parts()
    time.sleep(sleep_s)
    b = _cpu_parts()
    delta = [max(0.0, b[i] - a[i]) for i in range(8)]
    total = sum(delta) or 1.0
    return (delta[3] / total) * 100.0, (delta[4] / total) * 100.0, (delta[7] / total) * 100.0


def _proc_stats(pid: str) -> dict[str, Any]:
    if not pid or pid == "0":
        return {}
    out = _run(
        f"ps -o pid=,pcpu=,pmem=,rss=,nlwp=,etime= -p {pid}",
        timeout=3,
    )
    parts = out.split()
    if len(parts) < 6:
        return {}
    try:
        rss_kb = float(parts[3])
    except ValueError:
        rss_kb = 0.0
    return {
        "cpu_pct": float(parts[1]),
        "mem_pct": float(parts[2]),
        "rss_mb": rss_kb / 1024.0,
        "threads": int(parts[4]),
        "uptime": parts[5],
    }


def collect_bots() -> list[BotStatus]:
    rows: list[BotStatus] = []
    for name, unit in watch_units():
        active = _systemctl(unit, "is-active")
        enabled = _systemctl(unit, "is-enabled")
        if "not-found" in enabled and active in {"inactive", "unknown", ""}:
            continue
        pid = _run(f"systemctl show -p MainPID --value {unit}", timeout=3) or "0"
        cwd = ""
        stats: dict[str, Any] = {}
        if pid and pid != "0":
            cwd = _run(f"readlink /proc/{pid}/cwd", timeout=2)
            stats = _proc_stats(pid)
        nrestarts = _run(f"systemctl show -p NRestarts --value {unit}", timeout=3)
        rows.append(
            BotStatus(
                name=name,
                unit=unit,
                active=active,
                enabled=enabled,
                pid=pid,
                cwd=cwd,
                uptime=str(stats.get("uptime") or ""),
                cpu_pct=float(stats.get("cpu_pct") or 0.0),
                mem_pct=float(stats.get("mem_pct") or 0.0),
                rss_mb=float(stats.get("rss_mb") or 0.0),
                threads=int(stats.get("threads") or 0),
                nrestarts=nrestarts,
            )
        )
    return rows


def _parse_ps(cmd: str) -> list[ProcRow]:
    raw = _run(cmd, timeout=5)
    rows: list[ProcRow] = []
    for line in raw.splitlines()[1:8]:
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        rows.append(
            ProcRow(
                pid=parts[1],
                user=parts[0],
                cpu=parts[2],
                mem=parts[3],
                rss_kb=parts[5],
                command=parts[10][:80],
            )
        )
    return rows


def collect_top() -> tuple[list[ProcRow], list[ProcRow]]:
    cpu = _parse_ps("ps aux --sort=-%cpu")
    mem = _parse_ps("ps aux --sort=-%mem")
    return cpu, mem


def collect_tokens() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for path in TOKEN_FILES:
        age = _age_seconds(path)
        length = 0
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                tok = data.get("access_token") or data.get("token") or ""
                if isinstance(tok, dict):
                    tok = tok.get("access_token") or ""
                length = len(str(tok).strip())
            except Exception:
                length = -1
        out.append(
            {
                "path": str(path),
                "short": path.parent.parent.name + "/" + path.name
                if path.name
                else str(path),
                "age": _fmt_age(age),
                "present": "yes" if length > 0 else ("missing" if age is None else "empty"),
                "len": str(length),
            }
        )
    return out


def _tail(path: Path, n: int = 12) -> str:
    if not path.is_file():
        return f"(missing {path})"
    raw = _run(["tail", "-n", str(n), str(path)], timeout=3)
    return "\n".join(_TG_TOKEN_RE.sub("bot[redacted]", ln)[-220:] for ln in raw.splitlines())


def collect_hft() -> dict[str, Any]:
    info: dict[str, Any] = {
        "feed_age": _fmt_age(_age_seconds(FEED_HEALTH_LOG)),
        "failover_age": _fmt_age(_age_seconds(FAILOVER_LOG)),
        "market_data_age": _fmt_age(_age_seconds(MARKET_DATA_LOG)),
        "last_pulse": "",
        "last_sub": "",
        "dhan_429": 0,
        "dhan_ws_err": 0,
        "zerodha_ok": False,
        "active_instrument": None,
        "ticks": {},
        "last_source": None,
        "ipc_clients": None,
    }
    pulse = _tail(FEED_HEALTH_LOG, 1)
    info["last_pulse"] = pulse
    m = re.search(r"active=([^\s]+)", pulse)
    if m and m.group(1) not in {"None", "none"}:
        info["active_instrument"] = m.group(1)
    src = re.search(r"last_source=([^\s]+)", pulse)
    if src:
        info["last_source"] = src.group(1)
    ticks = re.search(r"ticks=(\{.*?\})", pulse)
    if ticks:
        try:
            info["ticks"] = ast.literal_eval(ticks.group(1))
        except Exception:
            info["ticks"] = {}
    ipc = re.search(r"ipc_clients=(\d+)", pulse)
    if ipc:
        info["ipc_clients"] = int(ipc.group(1))
    if "ZERODHA_WS" in pulse:
        info["zerodha_ok"] = True

    fail = _tail(FAILOVER_LOG, 8)
    info["failover_tail"] = fail
    md = _tail(MARKET_DATA_LOG, 40)
    info["market_data_tail"] = "\n".join(md.splitlines()[-6:])
    sub_lines = [ln for ln in md.splitlines() if "subscribe" in ln.lower()]
    info["last_sub"] = sub_lines[-1][-220:] if sub_lines else ""

    info["dhan_429"] = _count_journal("datafeedbot", "15 min ago", "HTTP 429")
    info["dhan_ws_err"] = _count_journal("datafeedbot", "15 min ago", "Dhan WS error|no close frame")
    info["dhan_429_24h"] = _count_journal("datafeedbot", "24 hours ago", "HTTP 429")
    return info


def _count_journal(unit: str, since: str, pattern: str) -> int:
    raw = _run(
        f"journalctl -u {unit} --since '{since}' --no-pager -o cat 2>/dev/null | grep -cE '{pattern}' || true",
        timeout=8,
    )
    try:
        return int(raw.splitlines()[-1].strip() or 0)
    except ValueError:
        return 0


def collect_network(expected_ip: str) -> dict[str, Any]:
    vn = _run("vnstat -d 1 2>/dev/null | tail -n 8", timeout=6)
    ip = _run(
        "curl -4 -s --max-time 4 https://checkip.amazonaws.com "
        "|| curl -4 -s --max-time 4 https://api.ipify.org",
        timeout=6,
    ).strip()
    ss = _run("ss -tnp 2>/dev/null | wc -l", timeout=4)
    dhan = _run("ss -tnp 2>/dev/null | grep -c dhan || true", timeout=4)
    return {
        "public_ip": ip,
        "expected_ip": expected_ip,
        "vnstat": vn,
        "tcp_count": ss,
        "dhan_conns": dhan,
    }


def collect_journals() -> dict[str, str]:
    return {
        "datafeedbot": _run("journalctl -u datafeedbot -n 10 --no-pager -o short-iso", timeout=6)[-1400:],
        "blitzbot": _run("journalctl -u blitzbot -n 8 --no-pager -o short-iso", timeout=6)[-1200:],
        "vps-health-bot": _run(
            "journalctl -u vps-health-bot -n 6 --no-pager -o short-iso", timeout=6
        )[-800:],
    }


def collect_place_order_runtime(place_root: Path | None = None) -> dict[str, Any]:
    root = place_root or Path("/home/ubuntu/place-order-bot")
    env_path = root / ".env"
    info: dict[str, Any] = {"root": str(root), "live_orders": None, "present": root.exists()}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("LIVE_ORDERS="):
                info["live_orders"] = line.split("=", 1)[1].strip().strip('"').lower() in {
                    "1",
                    "true",
                    "yes",
                }
    info["service"] = _systemctl("place-order-bot.service", "is-active")
    info["journal_tail"] = _run(
        "journalctl -u place-order-bot -n 6 --no-pager -o short-iso",
        timeout=6,
    )[-800:]
    return info


def collect_snapshot(
    tz_name: str = "Asia/Kolkata",
    expected_public_ip: str = "",
    expected_live: tuple[str, ...] = ("datafeedbot", "blitzbot", "vps-health-bot"),
) -> Snapshot:
    from vps_health_bot.peaks import peaks_today, record_sample

    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    total, available, used_pct, swap_used, swap_total = collect_memory()
    disk_pct, disk_avail, inode_pct = collect_disk()
    idle, iowait, steal = collect_cpu_split()
    idle_i, iowait_i, steal_i = collect_cpu_instant()
    bots = collect_bots()
    top_cpu, top_mem = collect_top()
    hft = collect_hft()
    network = collect_network(expected_public_ip)
    tokens = collect_tokens()
    journals = collect_journals()
    place = collect_place_order_runtime()
    load = Path("/proc/loadavg").read_text(encoding="utf-8").strip()
    try:
        load1 = float(load.split()[0])
    except (IndexError, ValueError):
        load1 = 0.0
    nproc = int(_run("nproc", timeout=2) or "1")
    chrony = _run("chronyc tracking 2>/dev/null | head -n 2", timeout=4)
    fail2ban = _run("fail2ban-client status sshd 2>/dev/null | head -8", timeout=3)

    snap = Snapshot(
        ts=now,
        hostname=_run("hostname", timeout=2) or os.uname().nodename,
        uptime=_run("uptime -p", timeout=3),
        load=load,
        nproc=nproc,
        load1=load1,
        cpu_idle_pct=idle,
        cpu_iowait_pct=iowait,
        cpu_steal_pct=steal,
        cpu_iowait_instant_pct=iowait_i,
        cpu_steal_instant_pct=steal_i,
        mem_total_mb=total,
        mem_available_mb=available,
        mem_used_pct=used_pct,
        swap_used_mb=swap_used,
        swap_total_mb=swap_total,
        disk_used_pct=disk_pct,
        disk_avail=disk_avail,
        inode_pct=inode_pct,
        market_open=in_market_hours(now),
        bots=bots,
        top_cpu=top_cpu,
        top_mem=top_mem,
        place_order=place,
        hft=hft,
        log_summary={},
        network=network,
        tokens=tokens,
        journals=journals,
        chrony_ok="failed" not in chrony.lower(),
        fail2ban=fail2ban,
    )

    by_unit = {b.unit.replace(".service", ""): b for b in bots}
    market = snap.market_open

    for unit in expected_live:
        b = by_unit.get(unit)
        if b and b.active == "active":
            snap.went_well.append(f"{b.name} is active")
        elif market:
            snap.concerns.append(f"{unit} is NOT active during market hours")
        elif b:
            snap.concerns.append(f"{unit} is {b.active}")

    gobot = by_unit.get("gobot")
    if gobot and gobot.active == "active" and by_unit.get("blitzbot") and by_unit.get("blitzbot").active == "active":
        snap.concerns.append("GoBot + BlitzBot both active — Telegram token conflict risk")
    elif gobot and gobot.active != "active":
        snap.went_well.append("GoBot parked (Blitz is live UI)")

    batman_on = [b for b in bots if b.name.startswith("Batman") and b.active == "active"]
    if batman_on:
        snap.concerns.append("Batman services running: " + ", ".join(b.name for b in batman_on))
    else:
        snap.went_well.append("Legacy Batman units stopped")

    if used_pct >= 85:
        snap.concerns.append(f"Memory pressure {used_pct:.0f}% used")
    elif used_pct < 75:
        snap.went_well.append(f"Memory OK ({used_pct:.0f}% used, {available:.0f}MB free)")

    if disk_pct >= 80:
        snap.concerns.append(f"Disk {disk_pct:.0f}% used")
    else:
        snap.went_well.append(f"Disk OK ({disk_pct:.0f}% used, {disk_avail})")

    if swap_used >= 400:
        snap.concerns.append(f"Swap in use {swap_used:.0f}MB — possible RAM pressure")

    if load1 >= nproc * 1.5:
        snap.concerns.append(f"Load {load1:.2f} vs {nproc} CPUs")
    elif load1 < nproc:
        snap.went_well.append(f"Load {load1:.2f} on {nproc} CPUs")

    if steal_i >= 8:
        snap.concerns.append(f"AWS CPU steal now {steal_i:.1f}% — noisy neighbor / credit")
    elif steal >= 8:
        snap.concerns.append(f"AWS CPU steal since boot {steal:.1f}%")
    if iowait_i >= 10:
        snap.concerns.append(f"CPU iowait now {iowait_i:.1f}%")

    for b in bots:
        if b.active == "active" and b.rss_mb >= 1200:
            snap.concerns.append(f"{b.name} RSS {b.rss_mb:.0f}MB")
        if b.active == "active" and b.cpu_pct >= 80:
            snap.concerns.append(f"{b.name} CPU {b.cpu_pct:.0f}%")

    if hft.get("dhan_429", 0) >= 10:
        snap.concerns.append(f"Dhan WS HTTP 429 storm x{hft['dhan_429']} in last 15m")
    elif hft.get("dhan_429", 0) >= 3:
        snap.concerns.append(f"Dhan WS HTTP 429 x{hft['dhan_429']} in last 15m")
    if hft.get("dhan_ws_err", 0) >= 40:
        snap.concerns.append(f"Dhan WS reconnect noise x{hft['dhan_ws_err']} in last 15m")
    if hft.get("zerodha_ok"):
        snap.went_well.append("Zerodha WS seen on last feed pulse")
    ticks = hft.get("ticks") or {}
    tick_n = 0
    if isinstance(ticks, dict):
        tick_n = sum(int(v or 0) for v in ticks.values() if str(v).isdigit() or isinstance(v, (int, float)))
    active = hft.get("active_instrument")
    if market and active in {None, "None"} and tick_n == 0:
        snap.went_well.append("No HFT subscribe right now (idle between trades)")
    elif market and active not in {None, "None"}:
        snap.went_well.append(f"HFT subscribed {active}")

    try:
        from vps_health_bot.history import rss_trend

        trend = rss_trend("blitz_rss_mb", window=8)
        if trend and trend.get("delta", 0) >= 150:
            snap.concerns.append(
                f"Blitz RSS +{trend['delta']:.0f}MB ({trend['from']:.0f}→{trend['to']:.0f}) since {trend.get('from_ts')}"
            )
    except Exception:
        pass
    blitz = next((b for b in bots if b.name.lower().startswith("blitz") and b.active == "active"), None)
    if blitz and blitz.rss_mb >= 900:
        snap.concerns.append(f"BlitzBot RSS {blitz.rss_mb:.0f}MB (watch leak)")

    ip = str(network.get("public_ip") or "")
    if expected_public_ip and ip and ip != expected_public_ip:
        snap.concerns.append(f"Public IP {ip} ≠ expected {expected_public_ip}")
    elif ip:
        snap.went_well.append(f"Public IP {ip}")

    stale = [t for t in tokens if t["present"] == "yes" and t["age"].endswith("d")]
    if market and stale:
        snap.concerns.append("Broker token file older than 1 day: " + ", ".join(t["short"] for t in stale))

    if not snap.chrony_ok:
        snap.concerns.append("chrony time sync failed")

    try:
        record_sample(snap)
        snap.peaks = peaks_today(now)
    except Exception:
        snap.peaks = {}

    return snap
