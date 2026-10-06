from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable

import msgpack

from datafeedbot.models import MarketData
from datafeedbot.sequence import now_ms

logger = logging.getLogger("datafeedbot.ipc")

_HEADER = struct.Struct("!I")
_INDEX_SIDS = frozenset({"13", "256265"})


def is_index_tick(md: MarketData | dict[str, Any]) -> bool:
    """True for NIFTY index (sid 13 / 256265), not option tape."""
    if isinstance(md, MarketData):
        inst = str(md.instrument or "")
        sid = str(
            getattr(md, "broker_id", None)
            or getattr(md, "dhan_security_id", None)
            or getattr(md, "security_id", None)
            or ""
        )
    else:
        inst = str((md or {}).get("instrument") or "")
        sid = str(
            (md or {}).get("broker_id")
            or (md or {}).get("security_id")
            or (md or {}).get("dhan_security_id")
            or ""
        )
    if inst == "NIFTY_INDEX":
        return True
    return sid in _INDEX_SIDS


@dataclass
class _ClientFilter:
    want: str = "index"  # index | all
    allow: set[str] = field(default_factory=set)

    def allows(self, md: MarketData) -> bool:
        if self.want == "all":
            return True
        if is_index_tick(md):
            return True
        inst = str(md.instrument or "")
        return bool(inst) and inst in self.allow


def encode_frame(obj: dict[str, Any]) -> bytes:
    try:
        body = msgpack.packb(obj, use_bin_type=True)
    except Exception:
        body = json.dumps(obj).encode("utf-8")
        return _HEADER.pack(len(body) | 0x80000000) + body
    return _HEADER.pack(len(body)) + body


def decode_frame(data: bytes) -> dict[str, Any]:
    if len(data) < 4:
        raise ValueError("short frame")
    (n,) = _HEADER.unpack(data[:4])
    use_json = bool(n & 0x80000000)
    n = n & 0x7FFFFFFF
    body = data[4 : 4 + n]
    if use_json:
        return json.loads(body.decode("utf-8"))
    return msgpack.unpackb(body, raw=False)


class IpcServer:
    """Unix domain socket server: push market ticks + request/response control."""

    def __init__(self, path: Path, state_getter: Callable[[], dict[str, Any]]) -> None:
        self.path = Path(path)
        self._state_getter = state_getter
        self._clients: set[socket.socket] = set()
        self._filters: dict[socket.socket, _ClientFilter] = {}
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._on_control: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self._accept_count = 0
        self._drop_count = 0

    def set_control_handler(
        self, handler: Callable[[dict[str, Any]], dict[str, Any]]
    ) -> None:
        self._on_control = handler

    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "clients": len(self._clients),
                "accepts": self._accept_count,
                "drops": self._drop_count,
            }

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.path.unlink()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(str(self.path))
        self._sock.listen(16)
        self._sock.settimeout(1.0)
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="dfb-ipc", daemon=True)
        self._thread.start()
        logger.info("IPC listening on %s", self.path)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for c in list(self._clients):
                try:
                    c.close()
                except Exception:
                    pass
            self._clients.clear()
            self._filters.clear()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if self.path.exists():
            try:
                self.path.unlink()
            except Exception:
                pass

    def _filter_for(self, conn: socket.socket) -> _ClientFilter:
        filt = self._filters.get(conn)
        if filt is None:
            filt = _ClientFilter()
            self._filters[conn] = filt
        return filt

    def set_client_want(self, conn: socket.socket, want: str) -> str:
        mode = str(want or "index").strip().lower()
        if mode not in {"index", "all"}:
            mode = "index"
        with self._lock:
            self._filter_for(conn).want = mode
        return mode

    def allow_instrument(self, conn: socket.socket, instrument: str) -> None:
        key = str(instrument or "").strip()
        if not key:
            return
        with self._lock:
            self._filter_for(conn).allow.add(key)

    def publish(self, md: MarketData) -> None:
        md.ipc_publish_timestamp = now_ms()
        payload = {"type": "tick", "data": md.to_dict()}
        frame = encode_frame(payload)
        dead: list[socket.socket] = []
        with self._lock:
            clients = list(self._clients)
            filters = {c: self._filters.get(c) or _ClientFilter() for c in clients}
        for c in clients:
            filt = filters.get(c) or _ClientFilter()
            if not filt.allows(md):
                continue
            try:
                c.sendall(frame)
            except Exception:
                dead.append(c)
        if dead:
            with self._lock:
                for c in dead:
                    self._clients.discard(c)
                    self._drop_count += 1
                    try:
                        c.close()
                    except Exception:
                        pass
            logger.warning(
                "IPC dropped slow/dead clients n=%s remaining=%s",
                len(dead),
                self.client_count(),
            )
    def _handle_control(self, conn: socket.socket, msg: dict[str, Any]) -> dict[str, Any]:
        action = str(msg.get("action") or "")
        if action in {"want", "ipc_filter"}:
            mode = str(msg.get("filter") or msg.get("want") or msg.get("mode") or "index")
            mode = self.set_client_want(conn, mode)
            return {"type": "wanted", "filter": mode}
        if self._on_control is None:
            return {"type": "error", "error": f"unknown_action:{action}"}
        resp = self._on_control(msg)
        if action == "subscribe" and isinstance(resp, dict) and resp.get("type") == "subscribed":
            self.allow_instrument(conn, str(resp.get("instrument") or ""))
        return resp

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(1.0)
            with self._lock:
                self._clients.add(conn)
                self._filters[conn] = _ClientFilter()
                self._accept_count += 1
            logger.info("IPC client connected clients=%s", self.client_count())
            threading.Thread(
                target=self._client_loop, args=(conn,), daemon=True
            ).start()
            # send latest snapshot immediately
            try:
                snap = {"type": "snapshot", "data": self._state_getter()}
                conn.sendall(encode_frame(snap))
            except Exception:
                pass

    def _client_loop(self, conn: socket.socket) -> None:
        buf = b""
        try:
            while not self._stop.is_set():
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 4:
                    (n,) = _HEADER.unpack(buf[:4])
                    n = n & 0x7FFFFFFF
                    need = 4 + n
                    if len(buf) < need:
                        break
                    frame = buf[:need]
                    buf = buf[need:]
                    try:
                        msg = decode_frame(frame)
                    except Exception as exc:
                        logger.warning("bad IPC frame: %s", exc)
                        continue
                    if msg.get("type") == "control":
                        try:
                            resp = self._handle_control(conn, msg)
                        except Exception as exc:
                            resp = {"type": "error", "error": str(exc)}
                        try:
                            conn.sendall(encode_frame(resp))
                        except Exception:
                            break
        finally:
            with self._lock:
                self._clients.discard(conn)
                self._filters.pop(conn, None)
                self._drop_count += 1
            logger.info("IPC client disconnected clients=%s", self.client_count())
            try:
                conn.close()
            except Exception:
                pass


class IpcClient:
    """Consumer client used by BlitzBot (and optionally GoBot later)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._sock: socket.socket | None = None
        self.last_processed_sequence = 0
        self._buf = b""
        self._pending: list[dict[str, Any]] = []

    def connect(self, timeout: float = 5.0) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(self.path))
        self._sock = s
        self._buf = b""
        self._pending = []
        # Server pushes a snapshot on accept — drain only that greeting frame.
        first = self._recv_raw(timeout=1.0)
        if first is not None and first.get("type") != "snapshot":
            self._pending.append(first)

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._pending = []

    def clear_pending(self) -> int:
        n = len(self._pending)
        self._pending = []
        return n

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def send_control(self, action: str, **kwargs: Any) -> dict[str, Any]:
        if not self._sock:
            raise RuntimeError("not connected")
        msg = {"type": "control", "action": action, **kwargs}
        self._sock.sendall(encode_frame(msg))
        deadline = time.time() + 15.0
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            resp = self.recv(timeout=remaining)
            if not resp:
                continue
            # Skip async push frames interleaved on the same socket.
            if resp.get("type") in {"tick", "snapshot"}:
                continue
            return resp
        return {"type": "error", "error": "no_response"}

    def recv(self, timeout: float | None = 1.0) -> dict[str, Any] | None:
        if self._pending:
            return self._pending.pop(0)
        return self._recv_raw(timeout=timeout)

    def _recv_raw(self, timeout: float | None = 1.0) -> dict[str, Any] | None:
        if not self._sock:
            return None
        self._sock.settimeout(timeout)
        while True:
            if len(self._buf) >= 4:
                (n,) = _HEADER.unpack(self._buf[:4])
                n = n & 0x7FFFFFFF
                need = 4 + n
                if len(self._buf) >= need:
                    frame = self._buf[:need]
                    self._buf = self._buf[need:]
                    return decode_frame(frame)
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                # Peer closed — surface as disconnect so callers reconnect
                # instead of spinning at 100% CPU on repeated empty reads.
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
                raise ConnectionError("IPC peer closed")
            self._buf += chunk

    def note_sequence(self, seq: int) -> str | None:
        """Return anomaly note if gap/duplicate/old."""
        if seq == self.last_processed_sequence:
            return "duplicate"
        if self.last_processed_sequence and seq < self.last_processed_sequence:
            return "old"
        if self.last_processed_sequence and seq > self.last_processed_sequence + 1:
            missing = seq - self.last_processed_sequence - 1
            self.last_processed_sequence = seq
            return f"gap_missing_{missing}"
        self.last_processed_sequence = seq
        return None
