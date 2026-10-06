# shoonya_Kavach — Bot4 (Gitesh) deploy snapshot

Snapshot of the Shoonya Kavach2 + Datafeedbot server layout so a new Ubuntu box can be rebuilt.

**Commit intent:** `SNAPSHOT after directional feature`

## Source server
- Host: `3.111.226.250` (Bot4 / Gitesh)
- Order broker: Shoonya (`MAIN_ORDER_BROKER=shoonya`)
- Kavach UI: port `8789`

## What is included
- `rahul_Changes/` — Kavach2 / Batman code (`run_kavach2.py`, `kavach-2.0/`, …)
- `Datafeedbot/` — market data feeder code
- `Datafeedbot_Runtime/` — feeder runtime layout (no secrets/logs)
- `Trading_Runtime_Rahul/` — runtime layout (no secrets/logs/prod state dumps)
- `systemd/` — `batman-kavach2` + `datafeedbot` unit files
- `CREDENTIALS_LAYOUT.txt` — list of secret files that must be recreated on a new server
- `*.env.example` stubs under Credentials

## Explicitly excluded (do not put secrets in git)
- All real Credentials / tokens / `shoonya_session.json` / JWTs
- Python `.venv` directories (recreate with `pip install -r requirements.txt`)
- Runtime Logs, large instrument caches, backups, nested `.git`

## Rebuild sketch (new Ubuntu server)
1. Copy this repo to `/home/ubuntu/` (or rsync trees into place).
2. Create venvs and install requirements for `rahul_Changes` and `Datafeedbot`.
3. Copy `*.env.example` → real env files under `Credentials/` and fill secrets.
4. Install systemd units from `systemd/`, `daemon-reload`, enable/start:
   - `datafeedbot.service`
   - `batman-kavach2.service`
5. Confirm `MAIN_ORDER_BROKER=shoonya` and Shoonya session before Live trading.

