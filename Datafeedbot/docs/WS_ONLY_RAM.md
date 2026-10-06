# Datafeedbot WS-only RAM notes

Captured post-restart on VPS `ubuntu@13.126.134.192`.

## Current VPS memory (`free -h`)

```
               total        used        free      shared  buff/cache   available
Mem:           3.7Gi       928Mi       1.3Gi        10Mi       1.8Gi       2.8Gi
Swap:          2.0Gi       144Mi       1.9Gi
```

## Guidance

- **4GB is OK** for 1 active option + tick journals (current VPS is ~3.7Gi total with ~2.8Gi available in this snapshot).
- Prefer **8GB** if you later run multi-strike / multi-instrument subscriptions with larger journal fan-out.
- Tick journal + RAM ring are the main new memory consumers beyond dual WS sockets.

## Tick journal

| Item | Value |
|------|--------|
| Path | `$DATAFEEDBOT_RUNTIME/Logs/ticks/YYYY-MM-DD/{security_id}.jsonl` |
| Runtime (systemd) | `/home/ubuntu/Datafeedbot_Runtime` |
| Example | `/home/ubuntu/Datafeedbot_Runtime/Logs/ticks/2026-08-14/12345.jsonl` |
| RAM ring | `DatafeedEngine._tick_ring` max **2000** ticks |
| Quote path | **WS-only** — Dhan/Zerodha quote REST recovery disabled in engine |

## Related BlitzBot change

Live punches use `SeedableQuoteProvider` (Datafeed WS seeds). Dhan REST remains for **orders only**, not `/marketfeed/quote`.
