"""Telephony adapter interface, the dial gate and the Twilio REST adapter.

Every leg the plugin creates (business call, owner ring, any retry) goes through
``DialGate.authorize``: destination gate, quiet hours, then single-use owner
consent. Only that method can mint a ``DialPermit``, and ``place_call`` accepts
nothing else; each permit works once. A rejected case therefore reaches no
adapter method at all.
"""
from __future__ import annotations

import abc
import base64
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from . import deps
from .contracts import GateRejected, Role

_MINT = object()
_SID = re.compile(r"^CA[0-9a-f]{32}$")


@dataclass
class DialPermit:
    e164: str
    role: Role
    task_id: str
    call_id: str
    line_key: str
    _mint: Any = field(default=None, repr=False)
    _used: bool = field(default=False, repr=False)

    def __post_init__(self):
        if self._mint is not _MINT:
            raise GateRejected("permit", "permits are only issued by DialGate.authorize")


class DialGate:
    """The one gate in front of every provider call creation."""

    def __init__(self, settings: Any, consent: Any, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.settings = settings
        self.consent = consent
        self._now = now

    def preview(self, raw: str):
        """Destination check before an approval request is shown."""
        return deps.check_destination(raw, self.settings)

    def binding(self, *, e164: str, role: Role, call_id: str, line_key: str, extra: Optional[dict] = None) -> dict:
        data = {"destination": e164, "role": role.value, "call": call_id, "line": line_key}
        if extra:
            data.update(extra)
        return data

    def authorize(self, raw: str, *, role: Role, action: str, task_id: str, call_id: str, line_key: str,
                  token: Optional[str], binding_extra: Optional[dict] = None,
                  binding_call_id: Optional[str] = None) -> DialPermit:
        decision = deps.check_destination(raw, self.settings)
        if not decision.allowed or not decision.e164:
            raise GateRejected("destination", decision.reason)
        if role is Role.BUSINESS and deps.is_quiet(self._now(), self.settings):
            raise GateRejected("quiet_hours", "outbound business calls are blocked during quiet hours")
        binding = self.binding(e164=decision.e164, role=role, call_id=binding_call_id or call_id,
                               line_key=line_key, extra=binding_extra)
        if not token or not self.consent.consume(token, action, task_id, binding):
            raise GateRejected("consent", "no valid owner approval for this exact call")
        return DialPermit(decision.e164, role, task_id, call_id, line_key, _mint=_MINT)


class ProviderError(Exception):
    """A provider request failed. The message never contains credentials."""


class TelephonyAdapter(abc.ABC):
    """Provider boundary. Subclasses implement the ``_``-prefixed primitives."""

    live = False  # True for an adapter that reaches a real phone network

    def __init__(self) -> None:
        self._permit_lock = threading.Lock()

    def place_call(self, permit: DialPermit, *, from_number: str, answer_url: str, status_url: str,
                   timeout_s: int, time_limit_s: int) -> str:
        if not isinstance(permit, DialPermit) or permit._mint is not _MINT:
            raise GateRejected("permit", "place_call requires a DialPermit")
        with self._permit_lock:
            if permit._used:
                raise GateRejected("permit", "permit already used")
            permit._used = True
        return self._create_call(to=permit.e164, from_number=from_number, answer_url=answer_url,
                                 status_url=status_url, timeout_s=int(timeout_s), time_limit_s=int(time_limit_s))

    @abc.abstractmethod
    def _create_call(self, *, to: str, from_number: str, answer_url: str, status_url: str,
                     timeout_s: int, time_limit_s: int) -> str: ...

    @abc.abstractmethod
    def end_call(self, sid: str) -> None:
        """Hang up a leg. Idempotent: ending an ended call is not an error."""

    @abc.abstractmethod
    def redirect_call(self, sid: str, url: str) -> None:
        """Point a live leg at new TwiML (fallback path for takeover)."""

    def fetch_call(self, sid: str) -> Optional[dict]:
        """The provider's record of a leg: ``{sid, account_sid, direction, from, to, status}``, or None
        if it does not exist. Used to verify webhooks when no request signature is available."""
        raise NotImplementedError


class TwilioRestAdapter(TelephonyAdapter):
    """Twilio Voice REST over the standard library.

    Credentials come from ``get_secret`` (``agent.secret_scope.get_secret`` in the
    plugin) at request time and are never stored on the instance or logged.
    """

    live = True
    API = "https://api.twilio.com/2010-04-01"

    def __init__(self, get_secret: Callable[[str], Optional[str]], *, opener: Optional[Callable] = None,
                 timeout_s: float = 10.0) -> None:
        super().__init__()
        self._get_secret = get_secret
        self._open = opener or urllib.request.urlopen
        self._timeout = timeout_s

    def _auth(self) -> tuple[str, str]:
        account = self._get_secret("HERMES_LINE_TWILIO_ACCOUNT_SID") or ""
        key = self._get_secret("HERMES_LINE_TWILIO_API_KEY_SID") or ""
        secret = self._get_secret("HERMES_LINE_TWILIO_API_KEY_SECRET") or ""
        if not re.fullmatch(r"AC[0-9a-f]{32}", account) or not re.fullmatch(r"SK[0-9a-f]{32}", key) or not secret:
            raise ProviderError("Twilio credentials are missing or malformed")
        token = base64.b64encode(f"{key}:{secret}".encode()).decode()
        return account, token

    def _post(self, path: str, params: Optional[list[tuple[str, str]]]) -> dict:
        account, token = self._auth()
        url = f"{self.API}/Accounts/{account}/{path}"
        if params is None:  # GET
            req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Basic {token}"})
        else:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), method="POST",
                                         headers={"Authorization": f"Basic {token}",
                                                  "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self._open(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            code = ""
            try:
                code = str(json.loads(exc.read().decode() or "{}").get("code", ""))
            except Exception:
                pass
            raise ProviderError(f"Twilio HTTP {exc.code} {code}".strip()) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"Twilio unreachable: {type(exc).__name__}") from None

    def _create_call(self, *, to, from_number, answer_url, status_url, timeout_s, time_limit_s) -> str:
        params = [("To", to), ("From", from_number), ("Url", answer_url), ("Method", "POST"),
                  ("StatusCallback", status_url), ("StatusCallbackMethod", "POST"),
                  ("Timeout", str(timeout_s)), ("TimeLimit", str(time_limit_s)), ("Record", "false")]
        params += [("StatusCallbackEvent", e) for e in ("initiated", "ringing", "answered", "completed")]
        sid = str(self._post("Calls.json", params).get("sid", ""))
        if not _SID.match(sid):
            raise ProviderError("Twilio returned no call SID")
        return sid

    def end_call(self, sid: str) -> None:
        if not _SID.match(sid or ""):
            return
        try:
            self._post(f"Calls/{sid}.json", [("Status", "completed")])
        except ProviderError as exc:
            if "HTTP 404" not in str(exc):
                raise

    def redirect_call(self, sid: str, url: str) -> None:
        if not _SID.match(sid or ""):
            raise ProviderError("bad call SID")
        self._post(f"Calls/{sid}.json", [("Url", url), ("Method", "POST")])

    def fetch_call(self, sid: str) -> Optional[dict]:
        if not _SID.match(sid or ""):
            return None
        try:
            data = self._post(f"Calls/{sid}.json", None)
        except ProviderError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise
        return {k: data.get(k) for k in ("sid", "account_sid", "direction", "from", "to", "status")}
