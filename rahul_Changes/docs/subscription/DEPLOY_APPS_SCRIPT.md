# Deploy subscription Google Sheet + Apps Script

## 1. Create the Sheet

1. Open Google Sheets with your admin Google account.
2. Create a workbook (any name), rename first tab to **Licenses**.
3. Paste headers from `templates/Licenses_headers.csv` (row 1).
4. Add the BOT4 sample row (or edit limits as needed).
5. `Status` must be `ACTIVE` or `BLOCKED`.

## 2. Attach Apps Script

1. Extensions → Apps Script.
2. Paste contents of `apps_script/Code.gs`.
3. Project Settings → Script properties → Add:
   - `SUBSCRIPTION_API_KEY` = value from `API_KEY.txt` (same file copied to BOT4).
4. Deploy → New deployment → Web app:
   - Execute as: **Me**
   - Who has access: **Anyone**
5. Copy the `/exec` URL.

## 3. Put secrets on BOT4 only

File: `/home/ubuntu/Trading_Runtime_Rahul/Credentials/subscription/subscription.env`

```
ALGO_SERVER_ID=http://3.111.226.250:8789/
SUBSCRIPTION_SOURCE=apps_script
SUBSCRIPTION_API_URL=<your /exec URL>
SUBSCRIPTION_API_KEY=<same as Script property>
SUBSCRIPTION_VALIDATE_HOUR_IST=8
```

Never commit this file. Never put Gmail password on the server.

## 4. Refresh grant

```bash
cd /home/ubuntu/rahul_Changes/kavach-2.0
sudo systemctl restart batman-kavach2
# or force once:
/home/ubuntu/rahul_Changes/.venv/bin/python -m subscription.refresh
```

Grant file: `/home/ubuntu/Trading_Runtime_Rahul/Cache/subscription/grant_YYYY-MM-DD.json`
