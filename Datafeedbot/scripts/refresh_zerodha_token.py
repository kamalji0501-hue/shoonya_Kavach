#!/usr/bin/env python3
"""Refresh Zerodha access_token into Runtime credentials (outside git).

Usage:
  python scripts/refresh_zerodha_token.py
  python scripts/refresh_zerodha_token.py --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from datafeedbot.auth.zerodha_token import (  # noqa: E402
    ensure_zerodha_access_token,
    load_zerodha_credentials,
    validate_access_token,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--runtime",
        default="",
        help="Override DATAFEEDBOT_RUNTIME root",
    )
    args = ap.parse_args()
    runtime = Path(args.runtime) if args.runtime else None
    try:
        token = ensure_zerodha_access_token(runtime, force_refresh=args.force)
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    creds = load_zerodha_credentials(runtime)
    profile = validate_access_token(creds.api_key, token)
    print("OK access_token refreshed/validated")
    print(f"  user_id={ (profile or {}).get('user_id') }")
    print(f"  token_len={len(token)}")
    print(f"  env={creds.env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
