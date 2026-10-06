# Session handoff — Datafeedbot + BlitzBot — 2026-08-14

**Status at write (16:27 IST):** `datafeedbot` **active** (PID `697380`, cwd `/home/ubuntu/Datafeedbot`, up since **09:27:38 IST**) · `blitzbot` **active** (PID `718290`, cwd `/home/ubuntu/BlitzBot`, up since **11:06:09 IST**) · `gobot` **inactive**.

**Today’s live session worked.** Trail + broker SLL jumps + Zerodha WS quotes carried **59 closed books**. Dhan WS did **not** deliver ticks. Do not `git pull` / `reset --hard` over these live trees.

This file is the **14 Aug** add-on. Keep **13 Aug** as history:

→ `context/SESSION_HANDOFF_2026-08-13.md` (Dhan 429/stampede fix, git deploy keys, Kamalji takeover)

---

## Sacred paths (unchanged)

| Role | Path |
|------|------|
| Datafeed code | `/home/ubuntu/Datafeedbot` |
| Datafeed runtime | `/home/ubuntu/Datafeedbot_Runtime` |
| Datafeed service | `datafeedbot` · WorkingDirectory `/home/ubuntu/Datafeedbot` |
| Blitz code | `/home/ubuntu/BlitzBot` |
| Blitz runtime | `/home/ubuntu/BlitzBot_Runtime` |
| Blitz service | `blitzbot` · WorkingDirectory `/home/ubuntu/BlitzBot` |
| GoBot (standalone, inactive today) | `/home/ubuntu/GoBot` + `/home/ubuntu/GoBot_Runtime` |
| GitHub Datafeed | `rahulkrkhatwani-prog/Datafeedbot` · VPS remote `github.com-datafeedbot` · key `~/.ssh/datafeedbot_github` |
| GitHub Blitz | `rahulkrkhatwani-prog/BlitzBot` · VPS remote `github.com-blitzbot` · key `~/.ssh/blitzbot_github` |
| Dhan account | Kamalji · client last4 **3330** |
| Zerodha | user **WAF140** · runtime `*_Runtime/Credentials/zerodha/` · token dies ~**06:00 IST** |
| Live JWT | `BlitzBot_Runtime/Data/data/shared/access_token.json` (newest file beats `dhan.env`) |
| Tick journal | `/home/ubuntu/Datafeedbot_Runtime/Logs/ticks/YYYY-MM-DD/{sid}.jsonl` |
| GO books | `/home/ubuntu/BlitzBot_Runtime/Data/data/prod/go/books/` |
| GO ticks per trade | `/home/ubuntu/BlitzBot_Runtime/go/trades/YYYY-MM-DD/<book_id>/ticks.jsonl` |

**Do not touch:** Lightsail, `batman-algo`, `batman-go.service`, `/home/ubuntu/Trading_Runtime`. Never commit `*_Runtime/`, `.env`, JWT, PEMs.

**Git rule:** VPS live tree is source of truth. Push from VPS only. Windows copies of Datafeed/Blitz are **stale** — do not push them. Do **not** `git pull` over live files.

---

## Last committed vs live (verified 14 Aug 16:27 IST)

Neither repo was committed on 14 Aug. **Working code is the dirty tree.**

### Datafeedbot

- Branch: `main` tracking `origin/main`
- Last commit: `136ea32` **2026-08-13 16:34 IST** — *Record evening Dhan MCX WS soak and keep the replay script.*
- Dirty:
  - `M datafeedbot/engine.py` (+65 / −71) · mtime **00:12 IST**
  - `?? docs/WS_ONLY_RAM.md` · mtime **00:13 IST**

### BlitzBot

- Branch: `main` tracking `origin/main`
- Last commit: `4f64395` **2026-08-13 16:04 IST** — *VPS live tree is source of truth for BlitzBot.*
- Dirty (+1182 / −239 across 6 files):

| File | mtime IST | Role |
|------|-----------|------|
| `GO/broker_protect_sl.py` | 02:56 | Broker SLL place/modify + downside **rescue → LIMIT crash** |
| `GO/protect_trail_sync.py` | 02:56 | Trail sync debounce **50ms**, min trigger move **0.20** |
| `GO/telegram/bots/go/params.json` | 02:56 | Tunables (see below) |
| `GO/order_manager.py` | 11:04 | `SeedableQuoteProvider` for live punches |
| `GO/place_order_bot/market_data.py` | 11:04 | `SeedableQuoteProvider` + REST reserved for **entry quote fallback / orders** |
| `GO/strategy/single_leg.py` | 11:04 | WS-only monitor + **feed_ready gate** |
| `?? GO/tests/test_go_feed_ready_gate.py` | 11:06 | Unit test: stale/cross-SID quote must not fire soft SL |

### Blitz backups (do not delete)

Under `/home/ubuntu/BlitzBot/backups/`:

- `checkpoint_ws_only_20260814_000922`
- `pre_ws_only_impl_20260814_001251`
- `checkpoint_sll_rescue_20260814_025326`
- `snapshot_14aug_REST_optimized_20260814_031309`
- `checkpoint_entry_quote_rest_20260814_100954`
- `checkpoint_feed_ready_gate_20260814_110434`
- plus 13 Aug `checkpoint_working_20260813_143605`

---

## What Kamalji changed overnight / morning (timeline IST)

| Time | What |
|------|------|
| **00:12–00:13** | Datafeed `engine.py` **WS-only** (REST quote recovery **disabled**). Tick JSONL journal + RAM ring max **2000**. `docs/WS_ONLY_RAM.md`. Blitz quote REST ripped from `_quote_premium`. Both bots restarted. |
| **02:56–03:07** | SLL rescue / sparse REST: `broker_protect_sl.py`, `protect_trail_sync.py`, `params.json`, `single_leg.py`. Blitz restarts. |
| **09:27** | Daily **Zerodha token refresh** (Rahul). `datafeedbot` + `blitzbot` restarted. PID `697380` still this Datafeed process. |
| **10:09** | `order_manager.py` + `market_data.py` entry quote via `SeedableQuoteProvider`. Backup `checkpoint_entry_quote_rest_100954`. Blitz restart. SSH from **103.206.178.210** (Kamalji). |
| **11:04–11:06** | Feed-ready gate in `single_leg.py` + test. Backup `checkpoint_feed_ready_gate_110434`. **Blitz last restart 11:06** — this is the process that traded the rest of the day. |

---

## Datafeed — WS-only contract (live `engine.py`, uncommitted)

**Quote path = websocket only.** Function `_maybe_rest_recovery` was replaced by `_maybe_ws_gap_log`. It logs `quote_source=ws_only (REST recovery disabled)` and **never** calls Dhan/Zerodha quote REST.

### Tick journal + RAM ring

| Item | Value |
|------|--------|
| Disk | `$DATAFEEDBOT_RUNTIME/Logs/ticks/YYYY-MM-DD/{security_id}.jsonl` |
| Also written | `NIFTY_{strike}_{CE\|PE}.jsonl` (sparse / SID-string files exist today) |
| Fields | `sequence, instrument, ltp, bid, ask, source, status, market_timestamp, received_timestamp, sequenced_timestamp, security_id, quote_source=ws_only` |
| RAM | `DatafeedEngine._tick_ring` max **2000** |
| VPS RAM note | 3.7Gi box is OK for 1 active option (`docs/WS_ONLY_RAM.md`) |

### 13 Aug Dhan WS rules **still in committed code** (`provider.py` — not dirty today)

- One Dhan `MarketFeed` socket
- `await disconnect()` — never fire-and-forget `close_connection()`
- SID change = subscribe JSON on **same** socket
- Flat = close + **IDLE**
- HTTP 429 or sub-2s drop → backoff **≥60s**
- MCX segment `MCX_COMM` (code 5)
- Newest JWT file beats stale `DHAN_ACCESS_TOKEN` in env

Those rules **did not save Dhan today** — see “Dhan WS still broken”.

---

## Blitz — what actually traded today (uncommitted)

### Feed

- Blitz **must not** open its own Dhan MarketFeed.
- Monitor consumes Datafeed IPC ticks (`GO/datafeed_option_feed.py`).
- `single_leg._rest_poll_if_stale` is **fail-closed**: no `/marketfeed/quote` REST for trail/SL.
- **`_feed_ready`**: True only after a **live WS tick for the open book’s SID**. Cross-SID / leftover LTP must **not** fire soft SL. Test: `GO/tests/test_go_feed_ready_gate.py`.
- Live punches: `SeedableQuoteProvider` seeded from Datafeed WS. `RestQuoteService` still exists, throttled **1 req/s**, reserved for **orders** (and optional entry quote if WS seed missing).

### Protect / trail (worked)

- Soft peak/trail on WS (Zerodha today).
- Broker **STOP_LOSS LIMIT** on Dhan: place at entry − SL; modify on trail (min move **0.20**, debounce **50ms**).
- Downside rescue at **SL − ₹1**: modify protect → **LIMIT crash** at Bid/LTP × **0.70** (floor 0.05). Pause upside trail REST while rescue owns the order.
- Telegram: entry/exit only (`telegram_trail_updates: false`).

### Params file vs live books (do not confuse)

`GO/telegram/bots/go/params.json` **defaults** (file):

- `default_sl_rupees`: **10.0**
- `trail_activate_rupees`: **5.0**
- `broker_sl_enabled`: true
- `broker_sl_min_trigger_move`: **0.2**
- `broker_sl_rescue_offset_rs`: **1.0**
- `broker_sl_rescue_pct`: **0.70**
- `target_enabled`: false
- `lot_size`: 65

**Every live book today** used **`sl_rupees=3.0`** and **`trail_activate_rupees=2.0`** (UI / ADD TRADE override, not the file defaults). `target_enabled=false` on all 59 books.

---

## Zerodha today (verified from journal)

| Window | What |
|--------|------|
| 00:13 | Token valid `user_id=WAF140`, WS connected (overnight process) |
| **06:59–07:46** | Daily token death: TCP drop → **502** → **403 Forbidden** storm (~96× 403) |
| **07:46–09:27** | WS silent/dead (~1h 41m). Pre-open, no trades. |
| **09:27:38** | Fresh session after pasted `request_token`. `Zerodha WS connected`. **0 errors after this.** |
| Market hours | **1** connect, **0** `Zerodha WS error`, carried **all** live quotes |

Last pulse at 16:27 IST: `last_source=ZERODHA_WS` · `ticks={'ZERODHA_WS': 10851}` · `rejects={}` · REST attempts **0** · `http_429=0` · `dual_stale_ms=0` · latency p50 **~0.35 ms**.

**Monday:** paste `request_token` **before 09:15 IST**. Flow is in Browser Automation rule `zerodha-daily-token.mdc`. Never paste access_token / api_secret in chat.

---

## Dhan WS today — still broken (not 429)

After 09:27:

| Metric | Count |
|--------|-------|
| `Dhan WS connected` | **80** |
| `Dhan WS error` | **80** (every connect) |
| Typical error | `uptime=0.01s 429=False … no close frame received or sent` |
| Dhan ticks accepted | **0** (`ticks=` pulse never shows `DHAN_WS`) |

This is **JWT/handshake**, not the 13 Aug reconnect stampede. Pattern: subscribe → connect → die in ~10 ms → failover `ZERODHA_WS`.

**If both WS fail** under current WS-only engine: **no quote REST recovery**. Trail/SL go blind. Zerodha saved 14 Aug.

**Fix before relying on Dhan as primary:** renew Dhan JWT (`access_token.json`), prove one SID subscribe stays up, then consider re-enabling guarded REST recovery.

---

## Live trading results 14 Aug (books, verified 16:27)

| Item | Value |
|------|--------|
| Books | **59** · all **closed** · 0 open |
| Exit `fixed_sl` | **22** (price never +₹2 to arm trail → no SLL jump) |
| Exit `trailing_sl` | **21** (100% of these had SLL jumps) |
| Exit `manual_external_exit` | **16** (10+ had already jumped SL) |
| SL jumps | Broker `SL-L modified` events; min step **₹0.20** |
| Expiry traded | **18 Aug 2026** NIFTY options |
| SIDs in tick journal | `45098` 24200 CE · `45102` 24300 CE · `45107` 24400 PE · `45111` 24500 PE · `45117` 24600 PE |

Blitz `ticks.jsonl` mix used for the tick-feed workbook: **ws=10786, rest=52**.

Dhan PnL moved through the day (broker truth, not books). Snapshots taken during the session included realized **₹+1888.25** (flat, ~13:06) and later **₹+1820** (~14:58). Re-run `/tmp/tardes_now.py` for current broker PnL — do not treat a stale snapshot as EOD.

---

## Tick-feed status columns (template from 13 Aug Telegram sheet)

Workbook: Browser Automation `data/reports/GO_tick_feed_status_2026-08-14.xlsx`  
Generator (desktop): `Browser Automation/scripts/tick_feed_status_day.py` (run on VPS).

| Column | How derived |
|--------|-------------|
| Ticks | Blitz `ticks.jsonl` per book |
| WS dhan | Datafeed journal connect/error/idle, carried forward |
| WS zerodha | Zerodha WS connected + `last_source=ZERODHA_WS` |
| Rest dhan | UP only if Blitz tick `source=rest` |
| Rest zerodha | DOWN (not used) |
| Bid/Ask dhan | YES only on REST ticks with BBO |
| Bid/Ask zerodha | YES on WS ticks with bid & ask |

Other reports (desktop `Browser Automation/data/reports/`):

- `GO_trades_report_2026-08-14_v2.xlsx` — timeline, FIFO legs, **SL_Jump_*** sheets
- `GO_zerodha_ws_bidask_2026-08-14.xlsx` — Datafeed JSONL bid/ask tape during books

---

## What “finalise this code” means (do not skip)

The **working** 14 Aug stack is the **uncommitted** VPS tree, not `origin/main`.

When someone is told to commit (explicit ask only):

1. Commit **from VPS**, never Windows.
2. Datafeed: `engine.py` + `docs/WS_ONLY_RAM.md` on `main` **and** `DatafeedDem`.
3. Blitz: the 6 dirty files + `GO/tests/test_go_feed_ready_gate.py` on `main` **and** `BlitzDem`.
4. Do not commit Runtime, JWT, books, tick journals, backups, PEMs.
5. After push, **do not** `git pull` on the live box.

Until then: treat dirty files as production.

---

## Monday / next-session checklist

1. Zerodha `request_token` **before 09:15 IST** → full refresh (desktop + VPS Datafeed/Blitz/GoBot runtime) → restart datafeed then blitz. Confirm journal `Zerodha access_token valid` / `Zerodha WS connected`. Print **user_id only**.
2. Confirm Blitz cwd `/home/ubuntu/BlitzBot`, Datafeed cwd `/home/ubuntu/Datafeedbot`.
3. Dhan JWT: if WS still dies in 0.01s, renew token **before** open. Optional: re-enable guarded REST recovery (currently off).
4. Do not change trail/OMS math unless explicitly asked.
5. `tardes` / `trades` = Dhan broker fills + books for SL column. Wrap table in `<small>`.

---

## Cross-link

- Blitz copy of this day: `/home/ubuntu/BlitzBot/context/SESSION_HANDOFF_2026-08-14.md`
- 13 Aug (429 fix, git keys): `context/SESSION_HANDOFF_2026-08-13.md`
- WS-only RAM: `/home/ubuntu/Datafeedbot/docs/WS_ONLY_RAM.md`
