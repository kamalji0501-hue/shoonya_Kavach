"""Max-utilisation gates for immediate Telegram alerts."""

from __future__ import annotations

from typing import Any


def max_util_reasons(snap: Any) -> list[str]:
    reasons: list[str] = []
    nproc = max(int(getattr(snap, "nproc", 1) or 1), 1)
    if float(getattr(snap, "mem_used_pct", 0) or 0) >= 85:
        reasons.append(f"RAM {snap.mem_used_pct:.0f}%")
    steal = float(
        getattr(snap, "cpu_steal_instant_pct", None)
        or getattr(snap, "cpu_steal_pct", 0)
        or 0
    )
    if steal >= 8:
        reasons.append(f"AWS steal {steal:.1f}%")
    if float(getattr(snap, "load1", 0) or 0) >= nproc:
        reasons.append(f"load {snap.load1:.2f} ≥ {nproc} CPUs")
    if float(getattr(snap, "swap_used_mb", 0) or 0) >= 400:
        reasons.append(f"swap {snap.swap_used_mb:.0f}MB")
    if float(getattr(snap, "disk_used_pct", 0) or 0) >= 90:
        reasons.append(f"disk {snap.disk_used_pct:.0f}%")
    for bot in getattr(snap, "bots", []) or []:
        if getattr(bot, "active", "") != "active":
            continue
        if float(getattr(bot, "cpu_pct", 0) or 0) >= 80:
            reasons.append(f"{bot.name} CPU {bot.cpu_pct:.0f}%")
        if float(getattr(bot, "rss_mb", 0) or 0) >= 1200:
            reasons.append(f"{bot.name} RSS {bot.rss_mb:.0f}MB")
    return reasons
