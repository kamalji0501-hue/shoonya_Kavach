# Datafeedbot

Standalone **market data** service for NIFTY options.

**Does not** place orders, manage SL, or run Telegram trading wizards.

## Purpose

- Keep Dhan + Zerodha WebSockets connected simultaneously
- Normalize → validate → assign global sequence → publish via Unix domain socket
- Subscribe only the **active** NIFTY option requested by a consumer (BlitzBot / future GoBot client)
- REST is recovery only (`DHAN_REST` / `ZERODHA_REST`, status=`SNAPSHOT`)

## Architecture

```
Dhan WS ──┐
          ├──> Datafeedbot ──> validate/sequence ──> UDS IPC ──> BlitzBot
Zerodha ──┘
```

GoBot trading logic is **not** modified by this project. Reference only.

## Paths (VPS)

| Item | Path |
|------|------|
| Code | `/home/ubuntu/Datafeedbot` |
| Runtime | `/home/ubuntu/Datafeedbot_Runtime` |
| IPC | `$DATAFEEDBOT_RUNTIME/ipc/market.sock` |
| Service | `datafeedbot.service` |

## Market data model

`instrument, ltp, bid, ask, market_timestamp, received_timestamp, source, status, sequence`

## Sequence semantics

`sequence` = order Datafeedbot **accepted** valid updates (not exchange chronological order).

## IPC

Unix domain socket, length-prefixed msgpack (JSON fallback). Event ticks + snapshot on connect. Control actions: `subscribe`, `unsubscribe`, `get_state`, `ping`.

## Configuration

Secrets live only under `Datafeedbot_Runtime/Credentials/` (never git).

```bash
export DATAFEEDBOT_RUNTIME=/home/ubuntu/Datafeedbot_Runtime
./.venv/bin/python run_datafeedbot.py
```

## Zerodha

Official docs: https://kite.trade/docs/connect/v3/websocket/  
Python client: `kiteconnect` (KiteTicker MODE_FULL).

Access token must be minted via Kite login flow and stored in `zerodha.env` as `ZERODHA_ACCESS_TOKEN`.

## Dhan

Patterns adapted from GoBot `GO/option_ws_feed.py` + `GO/option_ws_tick.py` (reference only; GoBot unchanged).

## Failover

Both WS stay connected. If one freezes, the other continues publishing. If both stale → REST recovery hierarchy (Dhan REST then Zerodha REST).

## Tests

```bash
cd /home/ubuntu/Datafeedbot
./.venv/bin/pytest tests -q
```

## Telegram

Bot username: `@Datafeed_bm_bot` — token only in runtime credentials.
