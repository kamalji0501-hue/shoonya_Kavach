#!/bin/bash
# Restart the one datafeedbot only after SAVE ZERODHA TOKEN (file has a token).
# DEACTIVATE deletes the file — do not restart; in-process watch idles Kite.
set -euo pipefail
LOCK=/home/ubuntu/Datafeedbot_Runtime/Data/shared/self_restart.lock
ZERA=/home/ubuntu/Datafeedbot_Runtime/Data/shared/zerodha_access_token.json

has_token() {
  python3 - "$1" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.is_file():
    raise SystemExit(1)
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
tok = str((data or {}).get("access_token") or (data or {}).get("token") or "").strip()
raise SystemExit(0 if tok else 1)
PY
}

if ! has_token "$ZERA"; then
  echo "skip datafeedbot token-reload (Kite deactivated or no Zerodha JSON)"
  exit 0
fi

mkdir -p "$(dirname "$LOCK")"
now=$(date +%s)
if [ -f "$LOCK" ]; then
  age=$((now - $(stat -c %Y "$LOCK")))
  if [ "$age" -lt 45 ]; then
    echo "skip datafeedbot token-reload (cooldown ${age}s)"
    exit 0
  fi
fi
echo "kite-token-saved $(date -Is)" > "$LOCK"
sleep 2
echo "systemctl restart datafeedbot.service (Kite token saved)"
exec systemctl restart datafeedbot.service
