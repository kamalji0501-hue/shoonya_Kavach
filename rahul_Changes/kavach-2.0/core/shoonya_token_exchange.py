"""Resolve Shoonya OAuth redirect URL / auth code / access token to a session token."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.shoonya_credentials import load_env, load_session

logger = logging.getLogger("batman.shoonya.exchange")

_HOST = "https://api.shoonya.com/NorenWClientAPI/"
_CODE_RE = re.compile(
    r"(?:[?&#]code=|/code/)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    re.I,
)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_TOKEN_RE = re.compile(r"^[A-Za-z0-9]{32,128}$")


def extract_auth_code(text: str) -> str | None:
    """Pull OAuth `code` from a redirect URL or bare UUID."""
    raw = (text or "").strip().strip('"').strip("'")
    if not raw:
        return None
    try:
        vals = parse_qs(urlparse(raw).query).get("code") or []
        if vals:
            cand = str(vals[0]).strip()
            if _UUID_RE.fullmatch(cand):
                return cand
    except Exception:
        pass
    m = _CODE_RE.search(raw)
    if m:
        return m.group(1)
    if _UUID_RE.fullmatch(raw):
        return raw
    return None


def _credentials(root=None) -> tuple[str, str, str]:
    env = load_env(root)
    sess = load_session(root)
    uid = (
        str(env.get("SHOONYA_USER_ID") or "").strip()
        or str(sess.get("uid") or "").strip()
    )
    client_id = str(env.get("SHOONYA_CLIENT_ID") or "").strip()
    secret = str(env.get("SHOONYA_SECRET_CODE") or "").strip()
    return uid, client_id, secret


def _call_gen_acs_tok(*, uid: str, code: str, checksum: str) -> dict[str, Any]:
    payload = {"uid": uid, "code": code, "checksum": checksum}
    body = ("jData=" + json.dumps(payload, separators=(",", ":"))).encode("utf-8")
    req = urllib.request.Request(
        _HOST + "GenAcsTok",
        data=body,
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise ValueError(f"Shoonya GenAcsTok HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Shoonya GenAcsTok network error: {exc}") from exc
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ValueError("Shoonya GenAcsTok returned non-JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Shoonya GenAcsTok returned unexpected payload")
    if str(data.get("stat") or "") != "Ok":
        raise ValueError(data.get("emsg") or f"GenAcsTok failed: {data}")
    return data


def exchange_auth_code(auth_code: str, *, uid: str, client_id: str, secret: str) -> dict[str, Any]:
    code = (auth_code or "").strip()
    if not code:
        raise ValueError("empty Shoonya auth code")
    if not uid:
        raise ValueError("Shoonya user id missing")
    if not client_id or not secret:
        raise ValueError(
            "SHOONYA_CLIENT_ID / SHOONYA_SECRET_CODE missing in Credentials/config/shoonya.env"
        )
    checksum = hashlib.sha256((client_id + secret + code).encode("utf-8")).hexdigest()
    return _call_gen_acs_tok(uid=uid, code=code, checksum=checksum)


def validate_session_token(uid: str, token: str) -> tuple[bool, str]:
    if not uid or not token:
        return False, "missing uid or token"
    payload = {"uid": uid, "actid": uid}
    body = ("jData=" + json.dumps(payload, separators=(",", ":"))).encode("utf-8")
    req = urllib.request.Request(
        _HOST + "Limits",
        data=body,
        headers={
            "Content-Type": "text/plain",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return False, str(exc)
    if isinstance(data, dict) and str(data.get("stat") or "") == "Ok":
        return True, str(data.get("prfname") or uid)
    return False, str((data or {}).get("emsg") or "Limits rejected token")


def resolve_shoonya_session(raw: str, user_id: str = "", *, root=None) -> tuple[str, str, str]:
    """Return (uid, access_token, detail). Raises ValueError on failure."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty Shoonya token / URL input")

    env_uid, client_id, secret = _credentials(root)
    uid = (user_id or "").strip() or env_uid
    if not uid:
        raise ValueError("Shoonya user id required (paste User id, or set SHOONYA_USER_ID)")

    auth_code = extract_auth_code(text)
    if auth_code:
        data = exchange_auth_code(
            auth_code, uid=uid, client_id=client_id, secret=secret
        )
        token = str(
            data.get("access_token")
            or data.get("susertoken")
            or data.get("accesstoken")
            or ""
        ).strip()
        if not token:
            raise ValueError("Shoonya GenAcsTok returned empty access_token")
        act = str(data.get("USERID") or data.get("actid") or data.get("uid") or uid).strip() or uid
        ok, reason = validate_session_token(act, token)
        if not ok:
            raise ValueError(f"Exchanged Shoonya token still rejected: {reason}")
        uname = str(data.get("uname") or reason or act)
        return act, token, uname

    # Already a session / access token
    candidate = text.split()[0].strip()
    if _TOKEN_RE.fullmatch(candidate):
        ok, reason = validate_session_token(uid, candidate)
        if ok:
            return uid, candidate, reason
        raise ValueError(
            f"Not a valid Shoonya session token ({reason}). "
            "Paste the OAuth redirect URL with code=... right after login, or a fresh access token."
        )

    raise ValueError(
        "Paste the Shoonya OAuth redirect URL (http://127.0.0.1/?code=...), "
        "the bare code UUID, or a fresh access/session token."
    )
