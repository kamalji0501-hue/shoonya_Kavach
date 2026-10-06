"""Fleet admin session (independent cookie; password may fall back to desk web pw)."""

from __future__ import annotations

import hmac
import os
import secrets
import time
from hashlib import sha256
from pathlib import Path

from dotenv import load_dotenv

COOKIE = "kavach_fleet"
TTL_S = 12 * 3600


def _runtime_root() -> Path:
    env = (os.environ.get("TRADING_RUNTIME") or "").strip()
    if env:
        return Path(env)
    return Path("/home/ubuntu/Trading_Runtime_Rahul")


def fleet_env_path() -> Path:
    return _runtime_root() / "Credentials" / "web" / "kavach_fleet.env"


def web_env_path() -> Path:
    return _runtime_root() / "Credentials" / "web" / "kavach_web.env"


def ensure_fleet_secrets() -> tuple[str, str]:
    path = fleet_env_path()
    if path.is_file():
        load_dotenv(path, override=True)
    password = (os.environ.get("KAVACH_FLEET_PASSWORD") or "").strip()
    secret = (os.environ.get("KAVACH_FLEET_SESSION_SECRET") or "").strip()

    if not password:
        # Fall back to desk web password so ops can log in immediately.
        if web_env_path().is_file():
            load_dotenv(web_env_path(), override=False)
        password = (os.environ.get("KAVACH_WEB_PASSWORD") or "").strip()
    if not password:
        password = secrets.token_urlsafe(16)
        os.environ["KAVACH_FLEET_PASSWORD"] = password
    if not secret:
        secret = secrets.token_hex(32)
        os.environ["KAVACH_FLEET_SESSION_SECRET"] = secret

    # Persist fleet env (never overwrite an existing password file blindly if present)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"KAVACH_FLEET_PASSWORD={password}\n"
            f"KAVACH_FLEET_SESSION_SECRET={secret}\n"
            f"# Optional per-child keys:\n"
            f"# FLEET_COPY_KEY_AMAN=\n",
            encoding="utf-8",
        )
        os.chmod(path, 0o600)
    else:
        # Ensure secret env vars are loaded for process
        os.environ["KAVACH_FLEET_PASSWORD"] = password
        os.environ["KAVACH_FLEET_SESSION_SECRET"] = secret
    return secret, password


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), sha256).hexdigest()


def make_session(secret: str) -> str:
    payload = str(int(time.time()))
    return f"{payload}.{_sign(secret, payload)}"


def valid_session(secret: str, cookie: str | None) -> bool:
    if not cookie or "." not in cookie:
        return False
    payload, sig = cookie.split(".", 1)
    if not hmac.compare_digest(sig, _sign(secret, payload)):
        return False
    try:
        ts = int(payload)
    except ValueError:
        return False
    return 0 <= time.time() - ts < TTL_S


def password_ok(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode(), (given or "").encode())
