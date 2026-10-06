from __future__ import annotations

from html import escape

from vps_health_bot.collectors import Snapshot


def _status_emoji(active: str) -> str:
    if active == "active":
        return "🟢"
    if active == "inactive":
        return "⚪"
    if active == "failed":
        return "🔴"
    return "🟡"


def overview(snap: Snapshot) -> str:
    session = "MARKET OPEN" if snap.market_open else "market closed"
    lines = [
        "<b>VPS Health — Overview</b>",
        f"<code>{escape(snap.hostname)}</code> · {escape(snap.ts.strftime('%Y-%m-%d %H:%M:%S %Z'))}",
        f"Session: <b>{session}</b> · Uptime: {escape(snap.uptime)}",
        f"Load: <code>{escape(' '.join(snap.load.split()[:3]))}</code> · {snap.nproc} CPUs",
        (
            f"CPU since boot: idle {snap.cpu_idle_pct:.0f}% · "
            f"iowait {snap.cpu_iowait_pct:.1f}% · steal {snap.cpu_steal_pct:.1f}%"
        ),
        (
            f"RAM: {snap.mem_used_pct:.0f}% used · {snap.mem_available_mb:.0f}MB free / "
            f"{snap.mem_total_mb:.0f}MB · swap {snap.swap_used_mb:.0f}MB"
        ),
        f"Disk: {snap.disk_used_pct:.0f}% · {escape(snap.disk_avail)} · inodes {snap.inode_pct:.0f}%",
        f"Public IP: <code>{escape(str(snap.network.get('public_ip') or '?'))}</code>",
        (
            f"CPU now: steal {snap.cpu_steal_instant_pct:.1f}% · "
            f"iowait {snap.cpu_iowait_instant_pct:.1f}%"
        ),
        "",
        "<b>Live processes</b>",
    ]
    for b in snap.bots:
        if b.active != "active":
            continue
        lines.append(
            f"• {escape(b.name)} CPU {b.cpu_pct:.1f}% · RSS {b.rss_mb:.0f}MB · "
            f"thr {b.threads} · up {escape(b.uptime or '?')}"
        )
    lines.append("")
    lines.append("<b>What went well</b>")
    if snap.went_well:
        lines.extend(f"• {escape(x)}" for x in snap.went_well)
    else:
        lines.append("• (nothing flagged)")
    lines.append("")
    lines.append("<b>Concerns</b>")
    if snap.concerns:
        lines.extend(f"• {escape(x)}" for x in snap.concerns)
    else:
        lines.append("• None right now")
    peaks = snap.peaks or {}
    if peaks:
        lines.extend(
            [
                "",
                f"<b>Peaks today</b> ({peaks.get('samples', 0)} samples)",
                f"• Load {peaks.get('load1', {}).get('value')} at {escape(str(peaks.get('load1', {}).get('at') or ''))}",
                f"• RAM {peaks.get('mem_pct', {}).get('value')}% at {escape(str(peaks.get('mem_pct', {}).get('at') or ''))}",
                f"• Steal {peaks.get('steal', {}).get('value')}% at {escape(str(peaks.get('steal', {}).get('at') or ''))}",
                f"• Max RSS {peaks.get('max_rss_mb', {}).get('value')}MB at {escape(str(peaks.get('max_rss_mb', {}).get('at') or ''))}",
            ]
        )
    return "\n".join(lines)


def bots_section(snap: Snapshot) -> str:
    lines = ["<b>Trading bots / services</b>", ""]
    for b in snap.bots:
        extra = ""
        if b.active == "active":
            extra = (
                f"\n   pid {escape(b.pid)} · CPU {b.cpu_pct:.1f}% · RSS {b.rss_mb:.0f}MB · "
                f"threads {b.threads} · restarts {escape(b.nrestarts or '0')}"
            )
            if b.cwd:
                extra += f"\n   cwd <code>{escape(b.cwd)}</code>"
        lines.append(
            f"{_status_emoji(b.active)} <b>{escape(b.name)}</b>\n"
            f"   {escape(b.unit)} · {escape(b.active)} · enabled={escape(b.enabled)}"
            + extra
        )
    lines.append("")
    lines.append(
        "<i>Kamalji live stack: Datafeedbot + BlitzBot + this health bot. "
        "GoBot / Batman / Place Order should stay parked.</i>"
    )
    return "\n".join(lines)


def resources_section(snap: Snapshot) -> str:
    lines = [
        "<b>Resources (HFT host)</b>",
        f"CPU load 1/5/15: <code>{escape(' '.join(snap.load.split()[:3]))}</code> · nproc={snap.nproc}",
        (
            f"Idle {snap.cpu_idle_pct:.1f}% · iowait now {snap.cpu_iowait_instant_pct:.1f}% · "
            f"<b>AWS steal now {snap.cpu_steal_instant_pct:.1f}%</b> (boot avg {snap.cpu_steal_pct:.1f}%)"
        ),
        f"Memory used: <b>{snap.mem_used_pct:.1f}%</b> · {snap.mem_available_mb:.0f} / {snap.mem_total_mb:.0f} MB",
        f"Swap: {snap.swap_used_mb:.0f} / {snap.swap_total_mb:.0f} MB",
        f"Root disk: {snap.disk_used_pct:.1f}% · {escape(snap.disk_avail)} · inodes {snap.inode_pct:.0f}%",
        "",
        "<b>Top CPU</b>",
    ]
    for p in snap.top_cpu[:5]:
        lines.append(
            f"• {escape(p.cpu)}% cpu {escape(p.mem)}% mem pid {escape(p.pid)} "
            f"<code>{escape(p.command[:70])}</code>"
        )
    lines.append("")
    lines.append("<b>Top RAM</b>")
    for p in snap.top_mem[:5]:
        try:
            rss_mb = float(p.rss_kb) / 1024.0
            rss_s = f"{rss_mb:.0f}MB"
        except ValueError:
            rss_s = p.rss_kb
        lines.append(
            f"• {escape(rss_s)} {escape(p.mem)}% pid {escape(p.pid)} "
            f"<code>{escape(p.command[:70])}</code>"
        )
    lines.append("")
    lines.append(
        "<i>Steal &gt;8% or load &gt; CPUs means AWS is throttling the box — "
        "that is the HFT contention signal.</i>"
    )
    return "\n".join(lines)


def network_section(snap: Snapshot) -> str:
    vn = snap.network.get("vnstat") or "(vnstat unavailable)"
    return "\n".join(
        [
            "<b>Network</b>",
            f"Public IP: <code>{escape(str(snap.network.get('public_ip') or '?'))}</code>",
            f"Expected: <code>{escape(str(snap.network.get('expected_ip') or '?'))}</code>",
            f"TCP sockets: {escape(str(snap.network.get('tcp_count') or '?'))}",
            "",
            "<b>Traffic (vnstat)</b>",
            f"<pre>{escape(str(vn)[:900])}</pre>",
        ]
    )


def hft_section(snap: Snapshot) -> str:
    h = snap.hft
    ticks = h.get("ticks") or {}
    tick_s = ", ".join(f"{k}={v}" for k, v in ticks.items()) if ticks else "none"
    lines = [
        "<b>HFT / market data</b>",
        f"Instrument: <code>{escape(str(h.get('active_instrument') or 'idle'))}</code>",
        f"Last source: <code>{escape(str(h.get('last_source') or '?'))}</code>",
        f"IPC clients: {escape(str(h.get('ipc_clients')))}",
        f"Ticks (last pulse): {escape(tick_s)}",
        f"feed_health age: {escape(str(h.get('feed_age')))} · market_data age: {escape(str(h.get('market_data_age')))}",
        f"Dhan WS 429 (15m): <b>{h.get('dhan_429', 0)}</b> · 24h: {h.get('dhan_429_24h', '?')} · WS errors: {h.get('dhan_ws_err', 0)}",
        "",
        "<b>Broker tokens (age only, no secrets)</b>",
    ]
    for t in snap.tokens:
        lines.append(f"• {escape(t['short'])} · {escape(t['present'])} · age {escape(t['age'])}")
    if h.get("last_sub"):
        lines.extend(["", "<b>Last subscribe</b>", f"<code>{escape(str(h['last_sub'])[-200:])}</code>"])
    if h.get("last_pulse"):
        lines.extend(["", "<b>Last pulse</b>", f"<code>{escape(str(h['last_pulse'])[-280:])}</code>"])
    return "\n".join(lines)


def logs_section(snap: Snapshot) -> str:
    lines = ["<b>Recent journals</b>"]
    for name, body in snap.journals.items():
        lines.append("")
        lines.append(f"<b>{escape(name)}</b>")
        lines.append(f"<pre>{escape((body or '')[-900:])}</pre>")
    return "\n".join(lines)


def history_section(snap: Snapshot) -> str:
    from vps_health_bot.history import load_samples, rss_trend

    day = snap.ts.strftime("%Y-%m-%d")
    rows = load_samples(day=day)
    peaks = snap.peaks or {}
    trend = rss_trend("blitz_rss_mb", window=8)
    lines = [
        "<b>History (this VPS, today)</b>",
        f"Samples: {len(rows)} · stored under <code>VPSHealth_Runtime</code>",
    ]
    if peaks:
        lines.extend(
            [
                f"• Peak load {peaks.get('load1', {}).get('value')} at {escape(str(peaks.get('load1', {}).get('at') or ''))}",
                f"• Peak RAM {peaks.get('mem_pct', {}).get('value')}% at {escape(str(peaks.get('mem_pct', {}).get('at') or ''))}",
                f"• Peak steal {peaks.get('steal', {}).get('value')}% at {escape(str(peaks.get('steal', {}).get('at') or ''))}",
                f"• Peak RSS {peaks.get('max_rss_mb', {}).get('value')}MB at {escape(str(peaks.get('max_rss_mb', {}).get('at') or ''))}",
                f"• Peak Dhan 429/15m {peaks.get('dhan_429', {}).get('value')} at {escape(str(peaks.get('dhan_429', {}).get('at') or ''))}",
            ]
        )
    if trend:
        lines.append(
            f"• Blitz RSS trend {trend['from']:.0f}→{trend['to']:.0f}MB (Δ {trend['delta']:+.0f}) "
            f"{escape(str(trend.get('from_ts') or ''))}→{escape(str(trend.get('to_ts') or ''))}"
        )
    h24 = (snap.hft or {}).get("dhan_429_24h")
    if h24 is not None:
        lines.append(f"• Dhan HTTP 429 last 24h: {h24}")
    vn = str((snap.network or {}).get("vnstat") or "")
    if vn and "vnstat" not in vn.lower()[:20]:
        lines.extend(["", "<b>Traffic (vnstat)</b>", f"<pre>{escape(vn[:700])}</pre>"])
    if not rows:
        lines.append("• No sample file yet — collector writes on each snapshot")
    return "\n".join(lines)


def recommendations() -> str:
    return "\n".join(
        [
            "<b>VPS Health — how this bot is meant to be used</b>",
            "",
            "• Standalone project: own GitHub repo, own systemd unit, own secrets",
            "• One instance per VPS; never share a Telegram token across two hosts",
            "• Watch expected-live units (Kamalji: datafeedbot + blitzbot + this bot)",
            "• Peak load is usually 9:15–15:30 IST while option WS is subscribed",
            "• Steal &gt;8% or load &gt; vCPU = AWS contention — size up or stop extra bots",
            "• Dhan WS 429 is chronic on this host — Zerodha WS is the live tick path",
            "• Watch Blitz RSS growth (seen 699→817MB in minutes) — restart if &gt;900MB",
            "• Group gets a card every 15 min, and immediately at max utilisation",
            "• Dedicated logs: VPSHealth_Runtime/Logs/.../events.jsonl + samples.jsonl + history.json",
        ]
    )


def day_report(snap: Snapshot) -> str:
    return "\n".join(
        [
            overview(snap),
            "",
            "────────",
            hft_section(snap),
            "",
            "────────",
            bots_section(snap),
            "",
            "────────",
            resources_section(snap),
            "",
            "────────",
            history_section(snap),
        ]
    )
