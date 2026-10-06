from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _runtime_root() -> Path:
    env = os.environ.get("DATAFEEDBOT_RUNTIME", "").strip()
    if env:
        return Path(env)
    vps = Path("/home/ubuntu/Datafeedbot_Runtime")
    if vps.is_dir():
        return vps
    return Path.home() / "Datafeedbot_Runtime"


def _read_token_json(candidates: list[Path]) -> str:
    best = ""
    best_mtime = -1.0
    for p in candidates:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            tok = str(data.get("access_token") or data.get("token") or "").strip()
            if not tok:
                continue
            mtime = p.stat().st_mtime
            if mtime >= best_mtime:
                best_mtime = mtime
                best = tok
        except Exception:
            continue
    return best


def dhan_jwt_json_candidates(root: Path) -> list[Path]:
    """Kavach + Datafeedbot shared JWT only. Never steal Blitz/GoBot files."""
    out: list[Path] = [
        root / "Data" / "shared" / "access_token.json",
        Path("/home/ubuntu/Trading_Runtime_Rahul/Data/data/shared/access_token.json"),
    ]
    seen: set[str] = set()
    uniq: list[Path] = []
    for path in out:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(path)
    return uniq


def _read_jwt(root: Path) -> str:
    return _read_token_json(dhan_jwt_json_candidates(root))


def load_dhan_jwt(root: Path | None = None) -> str:
    """Kavach + Datafeedbot JSON only. Empty/missing file means no JWT."""
    r = root or _runtime_root()
    return _read_jwt(r)


def zerodha_token_json_candidates(root: Path) -> list[Path]:
    """Kavach + Datafeedbot Kite files only. Never steal Blitz/GoBot tokens."""
    out: list[Path] = [
        root / "Data" / "shared" / "zerodha_access_token.json",
        Path("/home/ubuntu/Trading_Runtime_Rahul/Data/data/shared/zerodha_access_token.json"),
        root / "Credentials" / "zerodha" / "kite_session.json",
        Path("/home/ubuntu/Trading_Runtime_Rahul/Credentials/zerodha/kite_session.json"),
    ]
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _read_zerodha_token(root: Path) -> str:
    return _read_token_json(zerodha_token_json_candidates(root))


def load_zerodha_access_token(root: Path | None = None) -> str:
    """Kavach + Datafeedbot JSON only. Empty/missing file means no Kite token."""
    r = root or _runtime_root()
    return _read_zerodha_token(r)


def shoonya_session_json_candidates(root: Path) -> list[Path]:
    """Kavach Shoonya session only — never invent a second login."""
    out: list[Path] = [
        root / "Credentials" / "config" / "shoonya_session.json",
        root / "Data" / "shared" / "shoonya_session.json",
        Path("/home/ubuntu/Trading_Runtime_Rahul/Credentials/config/shoonya_session.json"),
        Path("/home/ubuntu/Datafeedbot_Runtime/Credentials/config/shoonya_session.json"),
    ]
    seen: set[str] = set()
    uniq: list[Path] = []
    for path in out:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(path)
    return uniq


def _read_shoonya_session_file(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "", ""
    try:
        data = __import__("json").loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return "", ""
        uid = str(data.get("uid") or data.get("user_id") or "").strip()
        tok = str(
            data.get("susertoken")
            or data.get("access_token")
            or data.get("token")
            or ""
        ).strip()
        return uid, tok
    except Exception:
        return "", ""


def load_shoonya_session(root: Path | None = None) -> tuple[str, str]:
    """Return (uid, susertoken) from newest readable Kavach session file."""
    r = root or _runtime_root()
    best_uid, best_tok = "", ""
    best_mtime = -1.0
    for path in shoonya_session_json_candidates(r):
        uid, tok = _read_shoonya_session_file(path)
        if not tok:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime >= best_mtime:
            best_mtime = mtime
            best_uid, best_tok = uid, tok
    if not best_uid:
        best_uid = (
            __import__("os").environ.get("SHOONYA_USER_ID", "")
            or __import__("os").environ.get("SHOONYA_UID", "")
        ).strip()
    if not best_tok:
        best_tok = (
            __import__("os").environ.get("SHOONYA_SUSERTKEN", "")
            or __import__("os").environ.get("SHOONYA_ACCESS_TOKEN", "")
        ).strip()
    return best_uid, best_tok


@dataclass(slots=True)
class Settings:
    runtime_root: Path
    ipc_socket: Path
    stale_ms: int = 3000
    rest_recovery_min_interval_ms: int = 3000
    dhan_client_code: str = ""
    dhan_access_token: str = ""
    zerodha_api_key: str = ""
    zerodha_api_secret: str = ""
    zerodha_access_token: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    instruments_csv: Path | None = None
    log_dir: Path | None = None
    enable_zerodha: bool = True
    enable_dhan: bool = True
    enable_shoonya: bool = False
    shoonya_uid: str = ""
    shoonya_access_token: str = ""

    @classmethod
    def load(cls) -> Settings:
        root = _runtime_root()
        for name in (
            root / "Credentials" / "config" / ".env",
            root / "Credentials" / "dhan" / "dhan.env",
            root / "Credentials" / "zerodha" / "zerodha.env",
            root / "Credentials" / "config" / "shoonya.env",
            Path("/home/ubuntu/Trading_Runtime_Rahul/Credentials/config/shoonya.env"),
            root / "Credentials" / "telegram" / "datafeed.env",
        ):
            if name.is_file():
                load_dotenv(name, override=False)
        sock = Path(
            os.environ.get(
                "DATAFEEDBOT_IPC_SOCKET",
                str(root / "ipc" / "market.sock"),
            )
        )
        csv_candidates = [
            Path(os.environ["DATAFEEDBOT_INSTRUMENTS_CSV"])
            if os.environ.get("DATAFEEDBOT_INSTRUMENTS_CSV")
            else None,
            root / "MarketData" / "security_id_list.csv",
            Path("/home/ubuntu/GoBot/marketdata/security_id_list.csv"),
            Path("/home/ubuntu/GoBot/Dependencies/all_instrument 2026-08-12.csv"),
            # Windows-style filename accidentally present on VPS
            Path(r"/home/ubuntu/GoBot/Dependencies\all_instrument 2026-08-12.csv"),
            Path(r"/home/ubuntu/Dependencies\all_instrument 2026-08-12.csv"),
        ]
        csv_path = next((p for p in csv_candidates if p and p.is_file()), None)
        s_uid, s_tok = load_shoonya_session(root)
        return cls(
            runtime_root=root,
            ipc_socket=sock,
            stale_ms=int(os.environ.get("DATAFEEDBOT_STALE_MS", "3000")),
            rest_recovery_min_interval_ms=int(
                os.environ.get("DATAFEEDBOT_REST_MIN_MS", "3000")
            ),
            dhan_client_code=os.environ.get("DHAN_CLIENT_CODE", "").strip(),
            # Live JWT files beat a stale DHAN_ACCESS_TOKEN baked into dhan.env
            dhan_access_token=load_dhan_jwt(root),
            zerodha_api_key=os.environ.get("ZERODHA_API_KEY", "").strip(),
            zerodha_api_secret=os.environ.get("ZERODHA_API_SECRET", "").strip(),
            zerodha_access_token=load_zerodha_access_token(root),
            telegram_token=os.environ.get("DATAFEED_BOT_TOKEN", "").strip()
            or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.environ.get("DATAFEED_CHAT_ID", "").strip()
            or os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
            instruments_csv=csv_path,
            log_dir=root / "Logs",
            enable_zerodha=os.environ.get("DATAFEEDBOT_ENABLE_ZERODHA", "1") != "0",
            enable_dhan=os.environ.get("DATAFEEDBOT_ENABLE_DHAN", "1") != "0",
            enable_shoonya=os.environ.get("DATAFEEDBOT_ENABLE_SHOONYA", "0") == "1",
            shoonya_uid=s_uid,
            shoonya_access_token=s_tok,
        )
