# Kavach_Kamalji — repository identity

**Canonical GitHub name:** `rahulkrkhatwani-prog/Kavach_Kamalji`  
**Former names:** `batman-algo`, temporary typo marker `Kavach_Kamlaji` (corrected).

## Branch

- **`main` is FINAL** (source of truth). Other branches are historical / behind.

## Empty rename marker commits (history noise)

These commits on `main` are **message-only** (no file tree changes). They record the rename story but do not alter code:

- `20f9392` — copy-bot merge note (02 Oct wording)
- `355c928` — batman-algo → Kavach_Kamlaji (typo)
- `cb029f5` — spelling fix Kavach_Kamlaji → Kavach_Kamalji

Do **not** rewrite `main` history to delete them (would require force-push). This file is the durable clarification.

## Product UI

Primary operator UI is the **Kavach web desk** (port 8789). Telegram for KAVACH2 is disabled in systemd (`KAVACH2_TELEGRAM_DISABLED=1`). Keep `telegram/bots/*/token.env` untracked leftovers; do not commit.

## WebSocket `/ws/state`

Requires a valid `kavach_web` session cookie after password login (`KAVACH_WEB_PASSWORD` in `Trading_Runtime_Rahul/Credentials/web/kavach_web.env`). Clients without that cookie are rejected (seen as HTTP 403 / close 4401).
