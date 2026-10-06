"""Load subscription configuration from env / credentials file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _runtime_root() -> Path:
    for key in ("TRADING_RUNTIME", "BATMAN_RUNTIME_ROOT"):
        raw = os.environ.get(key)
        if raw:
            return Path(raw)
    return Path("/home/ubuntu/Trading_Runtime_Rahul")


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass(frozen=True)
class SubscriptionConfig:
    algo_server_id: str
    source: str  # apps_script | file
    api_url: str
    api_key: str
    file_path: Path
    validate_hour_ist: int
    api_timeout_sec: float
    api_retry_count: int
    grant_dir: Path
    audit_dir: Path
    expiring_soon_days: int

    @property
    def enabled(self) -> bool:
        if self.source == "file":
            return True
        return bool(self.api_url and self.api_key and self.algo_server_id)


def load_config() -> SubscriptionConfig:
    root = _runtime_root()
    file_vals = _load_env_file(root / "Credentials" / "subscription" / "subscription.env")

    def get(name: str, default: str = "") -> str:
        return str(os.environ.get(name) or file_vals.get(name) or default).strip()

    grant_dir = Path(
        get("SUBSCRIPTION_GRANT_DIR") or str(root / "Cache" / "subscription")
    )
    audit_dir = Path(
        get("SUBSCRIPTION_AUDIT_DIR") or str(root / "Logs" / "prod" / "subscription")
    )
    file_path = Path(
        get("SUBSCRIPTION_FILE_PATH")
        or str(root / "Credentials" / "subscription" / "licenses_fixture.json")
    )
    try:
        hour = int(get("SUBSCRIPTION_VALIDATE_HOUR_IST", "8"))
    except ValueError:
        hour = 8
    try:
        timeout = float(get("SUBSCRIPTION_API_TIMEOUT_SEC", "10"))
    except ValueError:
        timeout = 10.0
    try:
        retries = int(get("SUBSCRIPTION_API_RETRY_COUNT", "2"))
    except ValueError:
        retries = 2
    try:
        soon = int(get("SUBSCRIPTION_EXPIRING_SOON_DAYS", "7"))
    except ValueError:
        soon = 7

    return SubscriptionConfig(
        algo_server_id=get("ALGO_SERVER_ID"),
        source=(get("SUBSCRIPTION_SOURCE", "apps_script") or "apps_script").lower(),
        api_url=get("SUBSCRIPTION_API_URL"),
        api_key=get("SUBSCRIPTION_API_KEY"),
        file_path=file_path,
        validate_hour_ist=max(0, min(23, hour)),
        api_timeout_sec=max(1.0, timeout),
        api_retry_count=max(0, min(5, retries)),
        grant_dir=grant_dir,
        audit_dir=audit_dir,
        expiring_soon_days=max(1, soon),
    )
