"""Twilio request signature validation (``X-Twilio-Signature``), standard library.

Algorithm (https://www.twilio.com/docs/usage/security): HMAC-SHA1 keyed with
the account Auth Token over the full request URL followed by every POST
parameter name and value, sorted by name; base64 encoded. WebSocket upgrades
are signed over the URL alone. The URL is rebuilt from the configured public
origin, never from Host or forwarded headers. As the Twilio SDKs do, the
check also accepts the URL with the default port added or removed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Iterable, Optional
from urllib.parse import urlsplit, urlunsplit


def compute(auth_token: str, url: str, params: Iterable[tuple[str, str]] = ()) -> str:
    data = url + "".join(k + v for k, v in sorted(params, key=lambda kv: (kv[0], kv[1])))
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _variants(url: str) -> list[str]:
    parts = urlsplit(url)
    default = {"https": 443, "wss": 443, "http": 80, "ws": 80}.get(parts.scheme)
    out = [url]
    host = parts.hostname or ""
    if parts.port is None and default:
        out.append(urlunsplit((parts.scheme, f"{host}:{default}", parts.path, parts.query, parts.fragment)))
    elif parts.port == default:
        out.append(urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment)))
    return out


def valid(auth_token: Optional[str], url: str, params: Iterable[tuple[str, str]], signature: Optional[str]) -> bool:
    if not auth_token or not signature or len(signature) > 128:
        return False
    params = list(params)
    for candidate in _variants(url):
        if hmac.compare_digest(compute(auth_token, candidate, params).encode(), signature.encode()):
            return True
    return False
