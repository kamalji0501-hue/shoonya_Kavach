# Agent notes — VPS Health Bot

Standalone Telegram health bot. **Do not** mix with GoBot trading logic.

## Hosts

- **Production:** Kamalji EC2 `ubuntu@13.126.134.192` (`KamaljiAlgo`)
- **Do not run** on RK Lightsail `13.202.206.39` while Kamalji is polling (same bot token)
- Secrets only under `/home/ubuntu/VPSHealth_Runtime`

## Git

- Repo: `rahulkrkhatwani-prog/VPS_Health_Bot`
- `main` = what Kamalji should run
- `develop` = work in progress

Never commit `token.env`, PEMs, or JWT files.

## Deploy (Kamalji)

```bash
cd /home/ubuntu/vps-health-bot
git pull
./.venv/bin/pip install -r requirements.txt
sudo cp deploy/vps-health-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart vps-health-bot
systemctl is-active vps-health-bot
```

## Status check

- Latest VPS_Health code checked on **03 Oct 26** by RK.
- Repo renamed to **`VPS_Health_Bot`** (was `vps-health-bot`).
- Verified Bot-4 running tree matches GitHub `main` at that check.

