# Session handoff — Datafeedbot + BlitzBot — 2026-08-13

**Owner from now: Kamalji.** Rahul development on this pair is **complete**. Further tests, fixes, and feature work = Kamalji.

Do **not** rewrite GoBot trail/OMS. Blitz stays a GoBot copy + Datafeed IPC feed.

**Live status (2026-08-13 ~15:50 IST):** `datafeedbot` + `blitzbot` **active** · `gobot` **inactive** · Blitz cwd `/home/ubuntu/BlitzBot` · **Dhan WS fixed** (IDLE when flat, one socket, live JWT) · MCX Crude `MCX_COMM` ticks OK · no live orders this afternoon.

---

## Kamalji takeover (read this first)

Rahul is done. You own Datafeedbot + BlitzBot from here.

| Item | Truth |
|------|--------|
| Trading logic | **Frozen.** Same as GoBot. Do not change trail / wick / book / punch / OMS unless Rahul/Kamalji explicitly ask |
| Market data | Datafeedbot only (Dhan + Zerodha WS → UDS IPC). Blitz must **not** open its own MarketFeed |
| Dhan on VPS | **Kamalji account** (client ends **3330**). Source of truth: `GoBot_Runtime/Credentials/config/.env` copied into `Datafeedbot_Runtime/Credentials/dhan/dhan.env` |
| Rahul personal Dhan | Client ends **6362**. Cached JWTs deleted from VPS (`place-order-bot` + `Trading_Runtime_Rahul` token files). Desktop hist-data file is Windows-only. **Do not** put 6362 on Datafeed |
| Zerodha | **Do not delete.** `Datafeedbot_Runtime/Credentials/zerodha/zerodha.env` + `kite_session.json` (today’s session, user WAF140). Token dies ~06:00 IST |
| Lightsail / `batman-algo` | Sacred — do not touch |
| Git push | **From VPS** (`/home/ubuntu/Datafeedbot`, `/home/ubuntu/BlitzBot`). Dedicated deploy keys `~/.ssh/datafeedbot_github` + `blitzbot_github`. GoBot key is GoBot-only. **Do not `git pull` over the live tree.** |

### Working live contract (24300 CE this expiry)

- Instrument: **NIFTY 25 Aug 2026 24300 CE**
- Dhan SID `61685` · Zerodha token `15791362` (monthly). Wrong weekly token was `11546114` (~₹165) — reject that mix
- Last compare 10:22 IST: Dhan 62 ticks / Zerodha 48 · gap ≤ ₹0.35 · 5/5 dual
- After Kamalji Dhan confirm 10:36 IST: 2/2 windows dual HEALTHY, LTP ~240

### WS rule of thumb

- **Dhan** denser while JWT is fresh (primary in live hours)
- **Zerodha** more stable overnight / when Dhan 429s (keep as second source; do not delete)
- Always same expiry; drop ≥35% cross-broker LTP clash
- REST is recovery only (`RestRateGuard` ≥3s). Do not hammer quote API (429)
- Dhan WS: **one** `MarketFeed` socket. SID change = subscribe JSON on the same socket. Flat = close + IDLE. HTTP 429 or sub-2s drop → wait **≥60s**. Always `await disconnect()` — never `close_connection()`.

---

## Afternoon closeout (2026-08-13 ~15:50 IST) — Dhan WS fixed

Kamalji traded live on Blitz all day. Dhan WS looked “down” because of **rate-limit + stale JWT + reconnect stampede**, not a bad login.

### Root cause

1. Datafeed opened a **new** Dhan MarketFeed on every SID change / drop (561 connects today before the fix).
2. `close_connection()` was fire-and-forget → `no close frame` → ghost sockets → HTTP 429 on the next handshake.
3. `dhan.env` `DHAN_ACCESS_TOKEN` was **stale**; REST 401 / WS upgrade then RST. Live JWT lives in `BlitzBot_Runtime/Data/data/shared/access_token.json` (and GoBot_Runtime). Prefer newest JWT file over env.
4. Blitz REST `/marketfeed/quote` created a **new** `RestQuoteService` every call (throttle was a no-op) + watchdog every 0.10s.

### What we changed (Dhan only — no trail/OMS)

| Area | Change |
|------|--------|
| `datafeedbot/brokers/dhan/provider.py` | One socket; `await disconnect()`; 60s 429/instant-drop backoff; 2s idle grace; SID resub on same WS; MCX `MCX_COMM` (code 5) |
| `datafeedbot/config.py` | Newest live JWT (Blitz/GoBot/Datafeed files) beats stale `DHAN_ACCESS_TOKEN` |
| `datafeedbot/engine.py` | Subscribe new SID first; live health in `get_state`; MCX segment on REST recovery |
| `datafeedbot/instruments.py` + `models.py` | SID lookup beyond NIFTY; `dhan_feed_segment` |
| Blitz `RestQuoteService` | Process-wide 1 req/s + 429 cooldown |
| Blitz `single_leg` watchdog | Skip REST if IPC tick age < 2s |

VPS backup: `/home/ubuntu/backups/wsfix_20260813_151441`. Runtime JWT sync is **not** in git.

### Live tests (no orders)

| Test | Result |
|------|--------|
| 5× NIFTY 24300 CE sub/hold/unsub + 3× SID switch 24300↔24400 | **17/17** · 6 connects · 3 same-socket resubs · 0×429 · 0 WS errors |
| CRUDEOIL 17Aug 9150 CE SID `573424` via IPC | **24 Dhan ticks**, LTP 6.0, `seg=MCX_COMM` |
| REST 20 Crude CE/PE | 20/20 HTTP 200 |

### JWT / credentials (no secrets in git)

- Live account = Kamalji Dhan (client last4 **3330**)
- Datafeed must read the **newest** `access_token.json` (Blitz if `blitzbot` is live)
- If WS connects then dies in 0.01s with `no close frame`, first check REST LTP — **401** means stale JWT, not a socket bug
- Never commit `*.env`, JWT files, or `*_Runtime/`

### Still do not

- Change GoBot/Blitz trail / wick / punch / OMS
- Touch Lightsail / `batman-algo`
- Delete Zerodha tokens
- Place test orders to validate feed

### Where to edit

| Change type | Repo / path |
|-------------|-------------|
| Feed, IPC, REST guard, expiry map, dual-WS | `/home/ubuntu/Datafeedbot` → git `Datafeedbot` `main` + `DatafeedDem` |
| IPC adapter logging/reconnect only | `/home/ubuntu/BlitzBot/GO/datafeed_option_feed.py` |
| Trail / OMS / punch | **Do not** (GoBot reference: `/home/ubuntu/GoBot`) |
| Secrets | Runtime only: `*_Runtime/Credentials/` — never commit |

Desktop mirrors:

- `H:\RK Data\Algo Trading Parent\Algo Developed By RK\Datafeedbot`
- `H:\RK Data\Algo Trading Parent\Algo Developed By RK\BlitzBot`

---

## Morning closeout (2026-08-13 ~10:22 IST) — Rahul dev completed

Live NIFTY **25 Aug 2026 24300 CE** (Dhan SID `61685`, Zerodha token `15791362`):

| Batch | Result | Notes |
|-------|--------|-------|
| 4×5 feed cycles | 20/20 | Expiry align + mismatch reject + IPC 15s subscribe retry |
| Dhan vs Zerodha compare (10:22) | 5/5 dual | Dhan 62 ticks / Zerodha 48 · LTP gap ≤ ₹0.35 · both HEALTHY |

**Fix that mattered:** weekly Zerodha token `11546114` (~₹165) was mixing with monthly Dhan `61685` (~₹250). Cache now indexes by expiry; subscribe copies Dhan expiry into Zerodha resolve; ≥35% LTP clash is dropped.

**WS now:** Dhan denser while JWT is fresh; Zerodha more stable overnight. Keep **both**, same expiry.

**Not done / do not do:** Blitz/GoBot trail/OMS edits · live orders · Lightsail / `batman-algo` · desktop `Credentials\Dhan` (hist client **6362**, not live feed **3330**).

---

## Mission (locked)

Split market-data load off GoBot so Kamalji’s trading logic stays intact:

| Role | Path | Runtime | Service |
|------|------|---------|---------|
| **GoBot** (baseline / sacred logic) | `/home/ubuntu/GoBot` | `GoBot_Runtime` | `gobot` — keep **inactive** while Blitz runs |
| **Datafeedbot** | `/home/ubuntu/Datafeedbot` | `Datafeedbot_Runtime` | `datafeedbot` — owns Dhan+Zerodha WS, REST recovery, UDS IPC |
| **BlitzBot** | `/home/ubuntu/BlitzBot` | `BlitzBot_Runtime` | `blitzbot` — GoBot UI/OMS copy; ticks via Datafeed IPC only |

Frozen baseline: `/home/ubuntu/GoBot_Baseline` (tag `pre-databot-20260812`).

**Hard rule:** Do not casually change trail / wick / book / punch / order_manager / protect_trail_sync. Blitz differs from GoBot only by branding + `DatafeedOptionFeed`.

---

## Architecture

```
Dhan WS ──┐
          ├──> Datafeedbot (validate → sequence → state)
Zerodha ──┘              │
                         v
              UDS  Datafeedbot_Runtime/ipc/market.sock
                         │
                      BlitzBot  (GO/* copy + datafeed_option_feed.py)
```

REST is **recovery only**, guarded by `RestRateGuard` (min interval ≥3s, exponential cooldown on HTTP 429). Lesson from GoBot 12-Aug logs: WS drop → REST flood → `marketfeed 429`.

---

## GitHub (Rahul closeout tips — pull these before you edit)

| Repo | Branches | Tip | Message |
|------|----------|-----|---------|
| https://github.com/rahulkrkhatwani-prog/Datafeedbot | `main`, `DatafeedDem` | afternoon Dhan WS fix + this handoff | One-socket Dhan WS, live JWT, MCX_COMM |
| https://github.com/rahulkrkhatwani-prog/BlitzBot | `main`, `BlitzDem` | Dhan REST throttle (no trail) | Shared 1 req/s quote + watchdog skip 2s |

Prior: expiry align `ae92663` · live validation `4484336` · REST/429 harden `1def980` · Blitz parity `e6850c0` / IPC logs `0d86b6e`.

**Push from the VPS.** SSH remotes:

- Datafeedbot: `git@github.com-datafeedbot:rahulkrkhatwani-prog/Datafeedbot.git`
- BlitzBot: `git@github.com-blitzbot:rahulkrkhatwani-prog/BlitzBot.git`

```bash
cd /home/ubuntu/Datafeedbot && git push origin main && git push origin main:DatafeedDem
cd /home/ubuntu/BlitzBot && git push origin main && git push origin main:BlitzDem
```

Do **not** `git pull` / `reset --hard` on the live trees. VPS files are the running bots. Windows copies are optional mirrors, not the operator path.

---

## VPS ops snapshot (2026-08-13 ~15:50 IST)

- `datafeedbot` = **active** (cwd `/home/ubuntu/Datafeedbot`)
- `blitzbot` = **active**, cwd **`/home/ubuntu/BlitzBot`**
- `gobot` = **inactive**
- Dhan WS when flat = **IDLE**, 0 sockets to `api-feed.dhan.co`
- Last Dhan prove: NIFTY 17/17 WS cycles + Crude SID 573424 24 ticks · REST NIFTY LTP ok
- Idle IPC clients=0 is normal until Blitz ADD TRADE / a subscribe test
- Do **not** touch Lightsail / `batman-algo` unless asked

SSH:

```bash
ssh -i "H:\RK Data\Algo Trading Parent\Credentials\AWS\Kamalji\Kamalji_EC_2.pem" ubuntu@13.126.134.192
```

Restart (Datafeed first, then Blitz):

```bash
sudo systemctl restart datafeedbot && sleep 2 && sudo systemctl restart blitzbot
systemctl is-active datafeedbot blitzbot gobot
```

---

## Parity reality (file layout on VPS)

GoBot/Blitz Kamalji tree is **flat under `GO/`**, not `GO/strategy/` for book/OMS:

Identical (byte-same) examples: `GO/book.py`, `GO/protect_trail_sync.py`, `GO/order_manager.py`, `GO/punch.py`, `GO/broker_protect_sl.py`, `GO/option_ws_tick.py`, `GO/telegram/bots/go/params.json`, …

**Only intentional diffs:**

1. `GO/bot.py` — BLITZ branding (+ short help blurb mentioning Datafeedbot)
2. `GO/strategy/single_leg.py` — imports `DatafeedOptionFeed as OptionWsFeed` + strike/opt getters + IPC-down notify text
3. `GO/datafeed_option_feed.py` — **Blitz only**

Tree audit historically: **78 same / 2 intentional diffs / 0 missing**.

---

## What we shipped tonight (hardening)

### Datafeedbot

- `datafeedbot/rest_guard.py` — REST acquire/block + 429 cooldown stats
- `datafeedbot/alerts.py` — deduped Telegram alerts (`DATAFEED_BOT_TOKEN` / chat)
- `engine.py` — dual-WS stale → guarded REST; source_switch logs; `ops_snapshot()` / `health_pulse()`
- `ipc.py` — `client_count()`, accept/drop logging, drop dead clients
- `config.py` — default `DATAFEEDBOT_REST_MIN_MS=3000`
- Dhan/Zerodha REST return `RestQuoteOutcome` (tick / is_429 / error)
- `app/main.py` — pulse every 30s → `Logs/feed_health.log`, `performance.log`
- Tests: `tests/test_rest_guard.py`
- Gate: `scripts/hardening_gate_10x.py` (**run with Datafeed `.venv` python**)

### BlitzBot (adapter only)

- `GO/datafeed_option_feed.py` — faster reconnect base, sequence-gap warnings, 30s feed pulse, connect/subscribe timing logs  
- **Did not edit** `update_trailing` / wick / book / OMS

---

## Test results (night of 2026-08-13)

| Gate | Result | Log |
|------|--------|-----|
| Datafeed pytest | **78 passed** | — |
| Hardening 10×12 | **10/10 clean** | `/home/ubuntu/Datafeedbot_Runtime/Logs/hardening_gate_10x.json` |
| Earlier parity 20×22 | **20/20 clean** | `/home/ubuntu/BlitzBot_Runtime/Logs/quality_gate_20x.json` |
| Earlier 12 parity | **12/12** | `/home/ubuntu/BlitzBot_Runtime/Logs/gobot_parity_tests.json` |
| Five-cycle dual broker | **5/5** | `/home/ubuntu/Datafeedbot_Runtime/Logs/five_cycle_dual_broker.json` |
| Live integration suite | present | `/home/ubuntu/Datafeedbot_Runtime/Logs/live_integration_results.json` |

Re-run hardening:

```bash
cd /home/ubuntu/Datafeedbot
./.venv/bin/python scripts/hardening_gate_10x.py 10
./.venv/bin/pytest -q
```

IPC ops check:

```bash
./.venv/bin/python -c "
import sys; sys.path.insert(0,'/home/ubuntu/Datafeedbot')
from pathlib import Path
from datafeedbot.ipc import IpcClient
c=IpcClient(Path('/home/ubuntu/Datafeedbot_Runtime/ipc/market.sock')); c.connect()
print(c.send_control('get_state')); c.close()
"
```

Expect `data.ops.rest_guard`, `ipc_clients`, `tick_counts`.

---

## GoBot pain we designed against (12-Aug `all.log`)

1. Option WS fail (`no close frame…`)
2. REST stale fallback stampede
3. **`marketfeed 429`** bursts
4. Many Telegram `deleteWebhook` = process restarts under stress  

“Webhook failing” in ops chat was mostly **Telegram deleteWebhook on restarts**, not Dhan fill webhooks. Real reliability issue = **WS + REST 429 cascade**. Datafeed centralizes that path.

---

## Secrets / Zerodha (do not paste in chat)

- Runtime only: `*_Runtime/Credentials/`
- Desktop: `H:\RK Data\Algo Trading Parent\Credentials\{Zerodha,Dhan,Datafeedbot,BlitzBot}\`
- VPS `zerodha.env` today: API_KEY, API_SECRET, ACCESS_TOKEN — **no password/TOTP yet**
- Auto-login module exists: `datafeedbot/auth/zerodha_token.py` + `scripts/refresh_zerodha_token.py`
- **Tomorrow:** add `ZERODHA_PASSWORD` + `ZERODHA_TOTP_SECRET` (+ user id) for unattended renew after ~06:00 IST

Telegram (names only): `@Datafeed_bm_bot`, `@BlitzBot_bm_bot`.

---

## Kamalji next checklist

1. Edit on VPS. Commit + push from VPS. **Never `git pull` over live Datafeed/Blitz.**  
2. Confirm services + Blitz cwd `/home/ubuntu/BlitzBot`  
3. Subscribe 24300 CE (or ATM) via IPC; expect `DHAN_WS` + `ZERODHA_WS`, REST only if both stale  
4. Watch for **no 429 storm** if one WS dies  
5. Optional: one Blitz **ADD TRADE → trail/exit** smoke (paper/live as you decide) — do not change trail code to “make it work”  
6. If Zerodha token dies after ~06:00 IST: refresh via Kite request_token / `scripts/refresh_zerodha_token.py` (**do not delete** zerodha files)  
7. Re-run `hardening_gate_10x.py 10` + pytest after feed changes  
8. Keep Batman Phase-1 / `batman-algo` stopped/untouched  
9. Never commit `.env`, JWTs, PIN/TOTP, `profiles/`  

---

## Improvement backlog (safe; no trail rewrite)

1. Market-hours dual-WS SLA dashboard line in Telegram  
2. Persist sequence-gap / REST-429 counters daily  
3. Blitz: ensure it never opens own Dhan MarketFeed (adapter-only — already intended)  
4. Reduce unnecessary `blitzbot`/`datafeedbot` restarts (deleteWebhook spam)  
5. Sync VPS tree → git if hotfixing only on VPS  
6. Optional: GoBot later as IPC consumer (not required for Blitz path)

---

## Agent rules reminder

- Lightsail sacred — never touch unless asked  
- Never commit `.env`, tokens, `profiles/`, Runtime credentials  
- Edit Blitz/Datafeed only for feed/ops; GoBot trading logic is reference + baseline  
- Prefer VPS as source of truth for running **and** git; push from VPS after a live commit  

---

## Evening git closeout (2026-08-13 ~16:05 IST)

VPS live trees are now the git remotes. Transplanted GitHub history into `/home/ubuntu/Datafeedbot/.git` and `/home/ubuntu/BlitzBot/.git` **without replacing running files**. Then committed VPS diffs (LF + live extras) and pushed `main` + Dem.

VPS-only extras now in git (already running; no restart required):

- Datafeed `ipc.py` — `clear_pending()`, `connected`, peer-close → `ConnectionError` (stops idle CPU spin)
- Blitz `datafeed_option_feed.py` — idle drain + sleep
- Blitz `GO/bot.py` — non-blocking Datafeed `get_state` LTP peek
- Blitz `GO/instruments.py` + `multileg_entry.py` — CSV SID fallback
- Blitz exit-rescue knobs on `punch` / `order_manager` / `single_leg` (trail math unchanged)
- Dropped Windows-only helper scripts that were never on the VPS

Git hosts: `github.com-datafeedbot` / `github.com-blitzbot` in `~/.ssh/config`. Do not use `github.com-gobot` for these repos.

Never commit `Dependencies/`, `*.csv`, `backups/`, `*_Runtime/`, `token*.txt`, `.env`.

---

## Evening Dhan WS soak (2026-08-13 ~16:15 IST)

81 IPC scenarios, **no orders**, Zerodha socket left alone. Kamalji JWT last4 **3330**.

| Score | Result |
|-------|--------|
| Raw | **67 / 81** |
| Data path (sub / BBO / SID switch / repeat) | **66 / 67** — only SILVER 236000 PE silent in 5s (illiquid) |
| REST token quotes on 6 futures | **6 / 6**, 0×401, 0×429 |
| Dhan WS 429 / connect_failed / ws_error | **0 / 0 / 0** |
| Same-socket resubs | 17 · new connects 13 · idle closes 12 |
| Zerodha ticks during soak | **0** (status stayed HEALTHY) |
| After test | Dhan **IDLE**, Zerodha **HEALTHY**, both services active |

14 “fails” were almost all **idle-close timing** (2.6s wait vs 2s grace + leftover ticks). Socket was still HEALTHY, not 429. Extra 4s unsub → IDLE.

LTP + BID + ASK + volume arrived on GOLD / GOLDM / SILVER / SILVERM / CRUDEOIL / CRUDEOILM futures and nearby CE/PE. Log: `/tmp/dhan_ws_mcx_50.json`.

---

## Continuity one-liner

> Blitz = GoBot brain + Datafeed nerves. VPS is the only source of truth for Blitz + Datafeed. Kamalji owns the next changes — do not touch the brain.
