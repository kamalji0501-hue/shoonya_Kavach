# VPS Health Bot

> **Status (RK):** Latest VPS_Health code checked on **03 Oct 26** by RK. GitHub repo renamed to **`VPS_Health_Bot`** (former name: `vps-health-bot`). Bot-4 and GitHub `main` match at that check.

Standalone Telegram bot that monitors **one VPS**. Own GitHub repo, own systemd
unit, own secrets. Not part of GoBot, BlitzBot, Datafeedbot, or Place Order.

First production host: **KamaljiAlgo** (`13.126.134.192`). The same codebase is
meant to be copied to other VPS later (`VPS_HEALTH_WATCH` + `VPS_HEALTH_EXPECTED_LIVE`).

| Item | Value |
|------|--------|
| GitHub | https://github.com/rahulkrkhatwani-prog/VPS_Health_Bot (`main`, `develop`) |
| Code | `/home/ubuntu/vps-health-bot` |
| Runtime | `/home/ubuntu/VPSHealth_Runtime` |
| systemd | `vps-health-bot.service` |

Only **one host** may poll a given Telegram bot token. RK Lightsail health unit stays stopped.

## Commands

`/start` `/report` `/bots` `/res` `/net` `/hft` `/logs` `/hist` `/recs` `/id`

Runtime logs (not in git): `VPSHealth_Runtime/Logs/YYYY-MM/YYYY-MM-DD/logs/{all.log,events.jsonl}` and `Data/vps_health/samples.jsonl`.

Posts to **VPS Health Group** every 15 minutes, and immediately if RAM/CPU/steal/load
hits max-utilisation. Privacy “no access to messages” does not block sending.

## Secrets (never commit)

`VPSHealth_Runtime/Credentials/telegram/token.env`
