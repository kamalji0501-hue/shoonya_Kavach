# Datafeedbot / BlitzBot implementation report

**Date:** 2026-08-12 (IST)  
**Host:** KamaljiAlgo (`13.126.134.192`)

## Summary

Market data was separated into a new **Datafeedbot** service (Dhan + Zerodha adapters, global sequence, Unix-socket IPC). **GoBot trading code was not modified.** **BlitzBot** is an isolated consumer scaffold. A frozen **GoBot_Baseline** exists on the VPS.

## Repository / path changes

### Created (VPS)

| Path | Role |
|------|------|
| `/home/ubuntu/GoBot_Baseline/` | Exact pre-change GoBot copy + `BASELINE_FROZEN.txt` |
| `/home/ubuntu/Datafeedbot/` | Market data service code |
| `/home/ubuntu/Datafeedbot_Runtime/` | Secrets, IPC socket, logs |
| `/home/ubuntu/BlitzBot/` | Consumer scaffold |
| `/home/ubuntu/BlitzBot_Runtime/` | Blitz Telegram credentials |
| `datafeedbot.service` | systemd |
| `blitzbot.service` | systemd |

### Local mirrors (Windows)

- `H:\RK Data\Algo Trading Parent\Algo Developed By RK\Datafeedbot\`
- `H:\RK Data\Algo Trading Parent\Algo Developed By RK\BlitzBot\`

### Modified

- **None** under `/home/ubuntu/GoBot` trading logic.
- Git tag on GoBot: `pre-databot-20260812` (annotation only).

### Deleted

- None.

## GoBot analysis (reference only)

Existing market data path (unchanged):

- `GO/option_ws_feed.py` — Dhan MarketFeed v2 Full packet, one `security_id`
- `GO/option_ws_tick.py` — LTP/bid/ask parse
- `GO/strategy/single_leg.py` — every tick → wick/trail

Datafeedbot **reuses patterns** from those files inside `datafeedbot/brokers/dhan/provider.py` without editing GoBot.

## Datafeedbot architecture

Components: `models`, `validation`, `normalization`, `sequence`, `state`, `ipc`, `engine`, `instruments`, brokers (`dhan`, `zerodha`), `app/main.py`.

Hot path: tick → validate → normalize → sequence → RAM state → UDS publish.

## Zerodha

Integrated via official `kiteconnect` (`KiteTicker`, `MODE_FULL`) per https://kite.trade/docs/connect/v3/websocket/.  
API key/secret stored in `Datafeedbot_Runtime/Credentials/zerodha/zerodha.env`.  
**`ZERODHA_ACCESS_TOKEN` is empty** until a Kite login mints a daily access token — WS will stay idle/NO_CREDENTIALS until set.

## Dhan

JWT/client copied from GoBot_Runtime into Datafeedbot_Runtime (read-only reuse of shared token file). Adapter mirrors GoBot WS Full mode + REST LTP recovery.

## IPC

Unix domain socket at `/home/ubuntu/Datafeedbot_Runtime/ipc/market.sock` — length-prefixed msgpack (JSON fallback). Chosen for low latency, Python compatibility, and debuggability vs shared memory complexity.

## Sequence model

- `sequence`: monotonic accept order inside Datafeedbot  
- `market_timestamp`: broker/exchange when available  
- `received_timestamp`: local receive time  

Delayed packets cannot overwrite newer unified state.

## Failover / recovery

Both WS intended always-on. If one freezes, the other continues. If both stale → Dhan REST then Zerodha REST snapshots (`status=SNAPSHOT`).

## Tests

| Suite | Result |
|-------|--------|
| Datafeedbot `tests/` | **66 passed** |
| BlitzBot `tests/` | run with Datafeedbot on `PYTHONPATH` |

Warnings: transient EBADF on IPC thread shutdown (hardened with `OSError` catch).

## Performance

Unit-path `process_ms` stats helper present (`engine.latency_stats`). Live market P50/P95/P99 require market hours + access tokens — **not yet measured live**.

## Services

```
gobot          active  cwd=/home/ubuntu/GoBot
datafeedbot    active
blitzbot       active
```

## Remaining live-validation risks

1. **Zerodha access token** must be minted and written to runtime before dual-WS live proof.  
2. Live Dhan WS subscribe needs an active instrument control message (BlitzBot/manual client).  
3. Shadow comparison vs GoBot in-process feed was **not** wired into GoBot (explicitly left unchanged).  
4. REST recovery shapes may need live tuning against Dhan/Kite payload variants.  
5. Telegram chat IDs empty — bots do not DM until chat IDs set.

## Credentials policy

Secrets only under `*_Runtime/Credentials/`. Not committed. Do not paste tokens into README/logs.
