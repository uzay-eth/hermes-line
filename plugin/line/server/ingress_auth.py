"""How the ingress decides a request really comes from Twilio for one of our calls.

Two modes, chosen at service start:

``signature`` (preferred; ``HERMES_LINE_TWILIO_AUTH_TOKEN`` is set): every webhook and
WebSocket upgrade must carry a valid ``X-Twilio-Signature`` (twilio_signature.py).

``path_secret`` (fallback when the Auth Token is not available): Twilio cannot be asked to
sign with an API key, so the plugin instead
  1. puts an unguessable per-install path secret (256 bits, generated and stored by the
     plugin, compared in constant time) in every webhook and stream URL it gives Twilio;
     the public origin must be https/wss;
  2. gives every relay/stream URL it returns in TwiML a per-call, single-use, short-lived token;
  3. asks the Twilio REST API (API key) that the CallSid exists on this account with the
     direction and from/to numbers the call must have, before any side effect or owner ring
     (cached per call leg), and re-checks terminal or conference-ending events against REST.
Anything that fails is rejected and logged masked. What this does NOT protect against
compared with signatures is listed in docs/lanes/L1.md ("Ingress without the Auth Token").

The path secret is also required in signature mode (defence in depth); only the
signature check differs.
"""
from __future__ import annotations

import hmac
import os
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

SECRET_BYTES = 32  # 256 bits
PREFIX = "/t"


def mask_sid(sid: str) -> str:
    return f"{sid[:2]}…{sid[-4:]}" if isinstance(sid, str) and len(sid) > 8 else "…"


def load_or_create_path_secret(path: Path) -> str:
    """Read the per-install path secret, creating it (mode 0600) on first use."""
    path = Path(path)
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if len(value) >= 43:  # urlsafe base64 of 32 bytes
            return value
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(SECRET_BYTES)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(value + "\n")
    return value


def path_secret_ok(expected: str, presented: str) -> bool:
    if not expected or not isinstance(presented, str) or len(presented) > 128:
        return False
    return hmac.compare_digest(expected.encode(), presented.encode())


@dataclass(frozen=True)
class LegExpectation:
    """What Twilio's record of a leg must say. Numbers are canonical E.164."""

    sid: str
    direction: str   # "outbound" (we created it) or "inbound"
    to: str
    from_: str


INBOUND_LIVE = frozenset({"queued", "ringing", "in-progress"})


class RestVerifier:
    """Cross-checks a CallSid with Twilio's REST API (``adapter.fetch_call``).

    The cache is keyed by SID and remembers the account plus the complete expectation it
    verified (direction, To, From). A later check of the same SID with any different
    expectation is rejected without a lookup (R1 H2: a cached SID must never vouch for a
    changed caller). Entries expire (``ttl_s``), are bounded (``max_entries``, oldest
    evicted) and are dropped when their call ends. The provider-returned SID must equal
    the requested one. Fails closed: an unreachable API, an unknown SID, another account,
    the wrong direction, numbers or (when required) status all mean "not verified".
    """

    def __init__(self, fetch_call: Callable[[str], Optional[dict]], account_sid: Callable[[], Optional[str]],
                 *, ttl_s: float = 4 * 3600.0, max_entries: int = 256, clock: Callable[[], float] = None):
        import time as _time
        self._fetch = fetch_call
        self._account = account_sid
        self._ttl = float(ttl_s)
        self._max = int(max_entries)
        self._clock = clock or _time.monotonic
        self._ok: "dict[str, tuple[tuple, float]]" = {}
        self._lock = threading.Lock()

    def fetch(self, sid: str) -> Optional[dict]:
        try:
            return self._fetch(sid)
        except Exception:
            return None

    @staticmethod
    def _key(account: str, want: LegExpectation) -> tuple:
        return (account, want.sid, want.direction, want.to, want.from_)

    def _expire(self, now: float) -> None:
        for sid in [s for s, (_, t) in self._ok.items() if now - t > self._ttl]:
            self._ok.pop(sid, None)
        while len(self._ok) > self._max:
            self._ok.pop(min(self._ok, key=lambda s: self._ok[s][1]), None)

    def verify(self, want: LegExpectation, *, require_status: Optional[frozenset] = None) -> tuple[bool, str]:
        if not want.sid:
            return False, "no_call_sid"
        account = self._account() or ""
        if not account:
            return False, "rest_other_account"
        key = self._key(account, want)
        now = self._clock()
        with self._lock:
            self._expire(now)
            cached = self._ok.get(want.sid)
            if cached is not None and cached[0] != key:
                return False, "rest_conflicting_expectation"
            if cached is not None and require_status is None:
                return True, "cached"
        record = self.fetch(want.sid)
        if record is None:
            return False, "rest_unknown_or_unreachable"
        if record.get("sid") != want.sid:
            return False, "rest_sid_mismatch"
        if record.get("account_sid") != account:
            return False, "rest_other_account"
        direction = str(record.get("direction") or "")
        if want.direction == "outbound" and not direction.startswith("outbound"):
            return False, "rest_direction_mismatch"
        if want.direction == "inbound" and direction != "inbound":
            return False, "rest_direction_mismatch"
        if record.get("to") != want.to or record.get("from") != want.from_:
            return False, "rest_numbers_mismatch"
        if require_status is not None and str(record.get("status") or "") not in require_status:
            return False, "rest_status_not_live"
        with self._lock:
            self._ok[want.sid] = (key, now)
            self._expire(now)
        return True, "verified"

    def status(self, sid: str) -> Optional[str]:
        record = self.fetch(sid)
        if not record or record.get("sid") != sid:
            return None
        return str(record.get("status"))

    def forget(self, *sids: str) -> None:
        with self._lock:
            for sid in sids:
                self._ok.pop(sid, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._ok)


class IngressAuth:
    def __init__(self, *, mode: str, path_secret: str, verifier: Optional[RestVerifier] = None):
        if mode not in ("signature", "path_secret"):
            raise ValueError("mode must be signature or path_secret")
        if mode == "path_secret" and verifier is None:
            raise ValueError("path_secret mode needs the REST verifier")
        self.mode = mode
        self.path_secret = path_secret
        self.verifier = verifier

    @property
    def signatures(self) -> bool:
        return self.mode == "signature"

    def prefix(self) -> str:
        return f"{PREFIX}/{self.path_secret}"

    def describe(self) -> dict[str, Any]:
        return {"mode": self.mode}
