# Add a fleet child (no code change)

On **Master** EC2 (`Trading_Runtime_Rahul`):

1. Edit `Data/data/shared/fleet/child_registry.json`.
2. Append under `children`:

```json
{
  "id": "child-NAME",
  "label": "Name",
  "base_url": "http://CHILD_PUBLIC_IP:8789",
  "copy_key_env": "FLEET_COPY_KEY_NAME",
  "aws_account": "aws-NAME",
  "link_type": "public",
  "enabled": true
}
```

3. Optional: set the key in `Credentials/web/kavach_fleet.env`:
   `FLEET_COPY_KEY_NAME=<same shared copy key as that desk>`
   If omitted, master uses `copy_trading/copy_key.txt`.

4. On the **child** desk, deploy the same Kavach build so these exist:
   - `GET /api/fleet/desk-snapshot` (X-Copy-Key)
   - `POST /api/fleet/remote` (X-Copy-Key)
   - `GET /api/host-health`, `GET /api/margin`

5. Restart or wait for the next fleet poll (~30s). Open `http://MASTER:8789/fleet`.

Login password: `KAVACH_FLEET_PASSWORD` in `kavach_fleet.env` (falls back to desk web password if unset).
