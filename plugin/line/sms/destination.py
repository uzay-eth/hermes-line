"""Where an approved code may go. The owner approves the destination together with sender and window.

Two kinds (docs/CONTRACT.md, "OTP delivery"):

* ``fill``  - the browser page whose origin (``scheme://host[:port]``) the owner saw in the approval
  prompt. Only https is accepted, plus http on loopback for local development.
* ``call``  - one live call: its ``call_id`` and the counterpart's number.

The destination is part of the consent binding (`service.binding_for`), so a token issued for one
destination can never release a code to another, and `prompt_text()` is the text the approval prompt
must show next to the sender and window. `log_text()` is the masked form for logs and audit events.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict
from urllib.parse import urlsplit

from .masking import mask_sender
from .model import normalize_sender

FILL = "fill"
CALL = "call"
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_DEFAULT_PORT = {"http": 80, "https": 443}
_CALL_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")


def normalize_origin(value: str) -> str:
    """`scheme://host[:port]`, lower-case, default port dropped. Raises ValueError otherwise."""
    if not isinstance(value, str) or "://" not in value:
        raise ValueError("origin must include a scheme")
    parts = urlsplit(value.strip())
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        raise ValueError("origin must be an http(s) URL with a host")
    if scheme == "http" and host not in _LOOPBACK:
        raise ValueError("plain http is not accepted for a code destination")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("invalid port in origin") from None
    if parts.username or parts.password:
        raise ValueError("origin must not carry credentials")
    host_part = "[%s]" % host if ":" in host else host
    return "%s://%s" % (scheme, host_part) if port in (None, _DEFAULT_PORT[scheme]) else "%s://%s:%d" % (scheme, host_part, port)


@dataclass(frozen=True)
class Destination:
    kind: str
    origin: str = ""        # fill
    call_id: str = ""       # call
    counterpart: str = ""   # call: E.164 of the other party

    @classmethod
    def fill(cls, origin: str) -> "Destination":
        return cls(FILL, origin=normalize_origin(origin))

    @classmethod
    def call(cls, call_id: str, counterpart: str) -> "Destination":
        if not isinstance(call_id, str) or not _CALL_ID.match(call_id):
            raise ValueError("call_id must be 1-64 characters of [A-Za-z0-9_.:-]")
        number = normalize_sender(counterpart or "")
        if not _E164.match(number):
            raise ValueError("counterpart must be a phone number")
        return cls(CALL, call_id=call_id, counterpart=number)

    def __post_init__(self) -> None:
        if self.kind == FILL:
            if normalize_origin(self.origin) != self.origin:
                raise ValueError("origin is not normalised; use Destination.fill()")
        elif self.kind == CALL:
            if not _CALL_ID.match(self.call_id) or not _E164.match(self.counterpart):
                raise ValueError("invalid call destination; use Destination.call()")
        else:
            raise ValueError("unknown destination kind")

    def binding(self) -> Dict[str, Any]:
        if self.kind == FILL:
            return {"kind": FILL, "origin": self.origin}
        return {"kind": CALL, "call_id": self.call_id, "counterpart": self.counterpart}

    def prompt_text(self) -> str:
        """What the owner approval prompt must show for this destination."""
        if self.kind == FILL:
            return "Type the code into the page at %s" % self.origin
        return "Send the code into the live call %s with %s" % (self.call_id, self.counterpart)

    def log_text(self) -> str:
        if self.kind == FILL:
            return "fill:%s" % self.origin
        return "call:%s:%s" % (self.call_id, mask_sender(self.counterpart))
