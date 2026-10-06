"""CLI: python -m subscription.refresh [--force]"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Refresh Kavach subscription day-grant")
    p.add_argument("--force", action="store_true", help="Ignore existing today's grant")
    args = p.parse_args(argv)
    from subscription.service import refresh_subscription

    grant = refresh_subscription(force=bool(args.force))
    print(json.dumps(grant.to_dict(), indent=2))
    return 0 if grant.allowed else 2


if __name__ == "__main__":
    sys.exit(main())
