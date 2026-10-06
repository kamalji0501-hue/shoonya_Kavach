"""Zerodha / Kite Connect daily access-token manager (GoBot-style).

Official flow (kite.trade docs):
  login URL → request_token → generate_session(api_secret) → access_token
  access_token expires ~06:00 IST next day.

Unattended automation (community standard, same HTTP as Kite web login):
  POST kite.zerodha.com/api/login (user_id + password)
  POST kite.zerodha.com/api/twofa (TOTP via pyotp)
  follow connect/login redirect → request_token → kite.generate_session

Secrets MUST live outside the git tree:
  Datafeedbot_Runtime/Credentials/zerodha/zerodha.env
  and/or desktop: Credentials/Zerodha/zerodha.env
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
from dotenv import load_dotenv

logger = logging.getLogger("datafeedbot.auth.zerodha")

_LOGIN_URL = "https://kite.zerodha.com/api/login"
_TWOFA_URL = "https://kite.zerodha.com/api/twofa"
_CONNECT_LOGIN = "https://kite.trade/connect/login?v=3&api_key={api_key}"
_PROFILE_URL = "https://api.kite.trade/user/profile"


@dataclass(frozen=True)
class ZerodhaCredentials:
    api_key: str
    api_secret: str
    user_id: str = ""
    password: str = ""
    totp_secret: str = ""
    access_token: str = ""
    env_path: Path | None = None

    @property
    def can_autologin(self) -> bool:
        return bool(
            self.api_key
            and self.api_secret
            and self.user_id
            and self.password
            and self.totp_secret
        )


def _candidate_env_paths(runtime_root: Path | None = None) -> list[Path]:
    out: list[Path] = []
    if runtime_root is not None:
        out.append(runtime_root / "Credentials" / "zerodha" / "zerodha.env")
    env = os.environ.get("DATAFEEDBOT_RUNTIME", "").strip()
    if env:
        out.append(Path(env) / "Credentials" / "zerodha" / "zerodha.env")
    out.extend(
        [
            Path("/home/ubuntu/Datafeedbot_Runtime/Credentials/zerodha/zerodha.env"),
            Path(
                r"H:\RK Data\Algo Trading Parent\Credentials\Zerodha\zerodha.env"
            ),
            Path(
                r"H:\RK Data\Algo Trading Parent\Credentials\Datafeedbot\zerodha\zerodha.env"
            ),
        ]
    )
    # de-dupe
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        k = str(p)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq


def _session_json_paths(runtime_root: Path | None = None) -> list[Path]:
    out: list[Path] = []
    if runtime_root is not None:
        out.append(runtime_root / "Credentials" / "zerodha" / "kite_session.json")
    out.extend(
        [
            Path("/home/ubuntu/Datafeedbot_Runtime/Credentials/zerodha/kite_session.json"),
            Path(
                r"H:\RK Data\Algo Trading Parent\Credentials\Zerodha\kite_session_datafeedbot.json"
            ),
        ]
    )
    return out


def load_zerodha_credentials(runtime_root: Path | None = None) -> ZerodhaCredentials:
    env_path: Path | None = None
    for p in _candidate_env_paths(runtime_root):
        if p.is_file():
            load_dotenv(p, override=True)
            env_path = p
            break
    # Optional session JSON fallback for access_token
    access = os.environ.get("ZERODHA_ACCESS_TOKEN", "").strip()
    if not access:
        for sp in _session_json_paths(runtime_root):
            if not sp.is_file():
                continue
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
                access = str(data.get("access_token") or "").strip()
                if access and not os.environ.get("ZERODHA_API_KEY"):
                    os.environ["ZERODHA_API_KEY"] = str(data.get("api_key") or "")
                if access:
                    break
            except Exception:
                continue
    return ZerodhaCredentials(
        api_key=os.environ.get("ZERODHA_API_KEY", "").strip()
        or os.environ.get("KITE_API_KEY", "").strip(),
        api_secret=os.environ.get("ZERODHA_API_SECRET", "").strip()
        or os.environ.get("KITE_API_SECRET", "").strip(),
        user_id=os.environ.get("ZERODHA_USER_ID", "").strip()
        or os.environ.get("KITE_USER_ID", "").strip(),
        password=os.environ.get("ZERODHA_PASSWORD", "").strip()
        or os.environ.get("KITE_PASSWORD", "").strip(),
        totp_secret=os.environ.get("ZERODHA_TOTP_SECRET", "").strip()
        or os.environ.get("KITE_TOTP_SECRET", "").strip(),
        access_token=access,
        env_path=env_path,
    )


def validate_access_token(api_key: str, access_token: str, *, timeout: float = 15.0) -> dict[str, Any] | None:
    if not api_key or not access_token:
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(
                _PROFILE_URL,
                headers={
                    "X-Kite-Version": "3",
                    "Authorization": f"token {api_key}:{access_token}",
                },
            )
            if r.status_code != 200:
                return None
            body = r.json()
            if body.get("status") != "success":
                return None
            data = body.get("data")
            return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("validate_access_token failed: %s", exc)
        return None


def _upsert_env_token(env_path: Path, access_token: str, user_id: str = "") -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    kv: dict[str, str] = {}
    order: list[str] = []
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k not in kv:
            order.append(k)
        kv[k] = v.strip()
    kv["ZERODHA_ACCESS_TOKEN"] = access_token
    if user_id:
        kv.setdefault("ZERODHA_USER_ID", user_id)
    for required in (
        "ZERODHA_API_KEY",
        "ZERODHA_API_SECRET",
        "ZERODHA_USER_ID",
        "ZERODHA_PASSWORD",
        "ZERODHA_TOTP_SECRET",
        "ZERODHA_ACCESS_TOKEN",
    ):
        if required not in order:
            order.append(required)
        kv.setdefault(required, "")
    kv["ZERODHA_ACCESS_TOKEN"] = access_token
    out = [f"{k}={kv.get(k, '')}" for k in order]
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    # also write session json beside env
    sess = env_path.parent / "kite_session.json"
    sess.write_text(
        json.dumps(
            {
                "api_key": kv.get("ZERODHA_API_KEY", ""),
                "access_token": access_token,
                "user_id": kv.get("ZERODHA_USER_ID", user_id),
                "login_time": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _extract_request_token(url: str) -> str | None:
    try:
        q = parse_qs(urlparse(url).query)
        vals = q.get("request_token") or []
        if vals:
            return str(vals[0])
    except Exception:
        pass
    m = re.search(r"request_token=([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def autologin_access_token(creds: ZerodhaCredentials, *, timeout: float = 20.0) -> str:
    """Headless Kite login using password + TOTP (community HTTP flow)."""
    if not creds.can_autologin:
        raise RuntimeError(
            "Zerodha autologin needs ZERODHA_USER_ID, ZERODHA_PASSWORD, "
            "ZERODHA_TOTP_SECRET (+ API key/secret) in Runtime credentials"
        )

    with httpx.Client(timeout=timeout, follow_redirects=False) as session:
        # Warm connect login (sets cookies / flow)
        login_page = session.get(_CONNECT_LOGIN.format(api_key=creds.api_key))
        # Some responses redirect; keep cookies either way
        _ = login_page

        r1 = session.post(
            _LOGIN_URL,
            data={"user_id": creds.user_id, "password": creds.password},
        )
        r1.raise_for_status()
        body1 = r1.json()
        if body1.get("status") != "success":
            raise RuntimeError(f"Kite login failed: {body1.get('message')}")
        request_id = (body1.get("data") or {}).get("request_id")
        if not request_id:
            raise RuntimeError("Kite login missing request_id")

        otp = pyotp.TOTP(creds.totp_secret).now()
        r2 = session.post(
            _TWOFA_URL,
            data={
                "user_id": creds.user_id,
                "request_id": request_id,
                "twofa_value": otp,
                "twofa_type": "totp",
                "skip_session": "true",
            },
        )
        r2.raise_for_status()
        body2 = r2.json()
        if body2.get("status") != "success":
            raise RuntimeError(f"Kite TOTP failed: {body2.get('message')}")

        # Finish Connect OAuth-like redirect to capture request_token
        url = _CONNECT_LOGIN.format(api_key=creds.api_key) + "&skip_session=true"
        request_token: str | None = None
        for _ in range(6):
            resp = session.get(url, follow_redirects=False)
            loc = resp.headers.get("Location") or resp.headers.get("location") or ""
            final = loc or str(resp.url)
            request_token = _extract_request_token(final)
            if request_token:
                break
            # Sometimes body / history holds it
            request_token = _extract_request_token(resp.text[:2000])
            if request_token:
                break
            if loc:
                url = loc if loc.startswith("http") else f"https://kite.zerodha.com{loc}"
                continue
            break

        if not request_token:
            # Last resort: allow_redirects and parse final URL
            try:
                resp = session.get(
                    _CONNECT_LOGIN.format(api_key=creds.api_key) + "&skip_session=true",
                    follow_redirects=True,
                )
                request_token = _extract_request_token(str(resp.url))
            except httpx.HTTPError as exc:
                # Redirect to 127.0.0.1 often raises / fails connect — parse from error URL
                msg = str(exc)
                request_token = _extract_request_token(msg)

        if not request_token:
            raise RuntimeError("Could not extract request_token after Kite 2FA")

    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=creds.api_key)
    data = kite.generate_session(request_token, api_secret=creds.api_secret)
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("generate_session returned empty access_token")
    return token


def persist_access_token(
    token: str,
    creds: ZerodhaCredentials,
    *,
    runtime_root: Path | None = None,
    user_id: str = "",
) -> None:
    targets: list[Path] = []
    if creds.env_path is not None:
        targets.append(creds.env_path)
    for p in _candidate_env_paths(runtime_root):
        if p not in targets:
            # Prefer writing into existing runtime path or first VPS/desktop path
            if "Datafeedbot_Runtime" in str(p) or p.parent.exists() or p == targets[0] if targets else False:
                targets.append(p)
    if not targets:
        targets = _candidate_env_paths(runtime_root)[:2]
    # Always write primary runtime + desktop Zerodha folder when present
    preferred = [
        Path("/home/ubuntu/Datafeedbot_Runtime/Credentials/zerodha/zerodha.env"),
        Path(r"H:\RK Data\Algo Trading Parent\Credentials\Zerodha\zerodha.env"),
    ]
    for p in preferred:
        if p not in targets:
            targets.insert(0, p)
    written = 0
    for p in targets[:3]:
        try:
            _upsert_env_token(p, token, user_id=user_id or creds.user_id)
            # also mirror session json used historically
            if "Credentials\\Zerodha" in str(p) or str(p).endswith("Zerodha/zerodha.env"):
                hist = p.parent / "kite_session_datafeedbot.json"
                hist.write_text(
                    json.dumps(
                        {
                            "api_key": creds.api_key,
                            "access_token": token,
                            "user_id": user_id or creds.user_id,
                            "login_time": datetime.now().isoformat(timespec="seconds"),
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            written += 1
            logger.info("Zerodha access_token saved to %s", p)
        except Exception as exc:
            logger.warning("Could not write %s: %s", p, exc)
    if written == 0:
        raise RuntimeError("Failed to persist Zerodha access_token to any credentials path")


def ensure_zerodha_access_token(
    runtime_root: Path | None = None,
    *,
    force_refresh: bool = False,
) -> str:
    """Return a valid access_token; autologin + persist when needed."""
    creds = load_zerodha_credentials(runtime_root)
    if not creds.api_key or not creds.api_secret:
        raise RuntimeError("ZERODHA_API_KEY / ZERODHA_API_SECRET missing in credentials")

    if not force_refresh and creds.access_token:
        profile = validate_access_token(creds.api_key, creds.access_token)
        if profile:
            logger.info(
                "Zerodha access_token valid user_id=%s",
                profile.get("user_id"),
            )
            return creds.access_token
        logger.warning("Zerodha access_token invalid/expired — refreshing")

    if not creds.can_autologin:
        raise RuntimeError(
            "Stored Zerodha access_token invalid and autologin credentials incomplete. "
            "Add ZERODHA_USER_ID, ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET to "
            "Datafeedbot_Runtime/Credentials/zerodha/zerodha.env "
            "(TOTP key from Kite → enable authenticator → 'Can't scan? Copy key')."
        )

    # Retry a few times around TOTP window boundaries
    last_err: Exception | None = None
    token = ""
    for attempt in range(1, 4):
        try:
            token = autologin_access_token(creds)
            break
        except Exception as exc:
            last_err = exc
            logger.warning("Zerodha autologin attempt %s failed: %s", attempt, exc)
            time.sleep(1.2)
    if not token:
        assert last_err is not None
        raise last_err

    profile = validate_access_token(creds.api_key, token)
    uid = str((profile or {}).get("user_id") or creds.user_id)
    persist_access_token(token, creds, runtime_root=runtime_root, user_id=uid)
    os.environ["ZERODHA_ACCESS_TOKEN"] = token
    return token
