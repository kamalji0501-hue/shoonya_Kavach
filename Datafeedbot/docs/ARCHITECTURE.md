# Architecture

## High level

```
Dhan WS ──────┐
              │
              v
         Datafeedbot  (broker registry: dhan, zerodha, + stubs angel/fyers/upstox/ibkr)
              ^
              │
Zerodha WS ───┘
              |
              v
        Unified Data Stream (sequence)
              |
              v
           UDS IPC
              |
        +-----+-----+
        |           |
     BlitzBot    (GoBot unchanged; optional future client)
     (Telegram UI: WATCH → CE/PE → strike → IPC subscribe)
```

## Failover

```
Dhan tick → healthy? YES → publish
                 NO → Zerodha healthy? YES → publish Zerodha
                                    NO → Dhan REST → else Zerodha REST → else DATA_INVALID
```

## Process

AWS VPS KamaljiAlgo:
1. `datafeedbot.service`
2. `gobot.service` (untouched trading)
3. `blitzbot.service` (consumer + Telegram UI)
