"""GatewaySource: poll the local API of the capcom6 "SMS Gateway for Android" app.

Verified against the published OpenAPI document (docs.sms-gate.app, android-sms-gateway
v1.78.1): `GET /inbox` (local server mode only) with query `type`, `limit` (1-500),
`offset`, `from`, `to` (ISO 8601), HTTP Basic auth, entries
`{id, sender, recipient, simNumber, createdAt, contentPreview, type}`.

Intended wiring for a USB-attached phone: `adb forward tcp:18080 tcp:8080`, then
`http://127.0.0.1:18080`. Plain HTTP is only accepted for loopback hosts, matching the
app's own rule; anything else must be https. Credentials never appear in repr or logs.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, List, Optional

from ..log import LOG
from ..model import SmsMessage, parse_iso_ms
from .base import (
    DeviceUnavailable,
    ParseError,
    PermissionDenied,
    SmsSource,
    SourceError,
    SourceStatus,
    apply_filters,
)

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(req: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (scheme validated in __init__)


def _iso(ms: int) -> str:
    return _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GatewaySource(SmsSource):
    name = "gateway"

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        opener: Opener = _default_opener,
        timeout_s: float = 10.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        u = urllib.parse.urlparse(base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("gateway base_url must be http(s)://host[:port]")
        if u.scheme == "http" and u.hostname not in _LOOPBACK:
            raise ValueError("plain http is only allowed for loopback; use https or adb forward")
        self._base = base_url.rstrip("/")
        self._auth = "Basic " + base64.b64encode(("%s:%s" % (username, password)).encode("utf-8")).decode("ascii")
        self._open = opener
        self.timeout_s = timeout_s
        self._clock = clock

    def __repr__(self) -> str:  # never show credentials
        return "GatewaySource(%s)" % self._base

    def _get(self, path: str, query: dict):
        url = "%s%s?%s" % (self._base, path, urllib.parse.urlencode(query))
        req = urllib.request.Request(url, headers={"Authorization": self._auth, "Accept": "application/json"}, method="GET")
        try:
            resp = self._open(req, self.timeout_s)
            status = getattr(resp, "status", 200)
            body = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise PermissionDenied("gateway rejected the credentials (HTTP %d)" % exc.code)
            raise SourceError("gateway HTTP %d" % exc.code)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise DeviceUnavailable("gateway unreachable: %s" % type(exc).__name__)
        if status != 200:
            raise SourceError("gateway HTTP %d" % status)
        return body

    def check(self) -> SourceStatus:
        try:
            # A window in the future matches nothing, so this proves auth and /inbox
            # availability without reading any message.
            future = int((self._clock() + 365 * 86400) * 1000)
            self._get("/inbox", {"type": "SMS", "limit": 1, "from": _iso(future)})
        except SourceError as exc:
            return SourceStatus(False, self.name, str(exc))
        return SourceStatus(True, self.name, "inbox endpoint reachable", {"base": self._base})

    def fetch(self, *, since_ms=None, until_ms=None, addresses=None, limit=50) -> List[SmsMessage]:
        q: dict = {"type": "SMS", "limit": max(1, min(500, max(limit, 50))), "offset": 0}
        if since_ms is not None:
            q["from"] = _iso(since_ms)
        if until_ms is not None:
            q["to"] = _iso(until_ms)
        raw = self._get("/inbox", q)
        try:
            data = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
        except (ValueError, UnicodeDecodeError):
            raise ParseError("gateway returned non-JSON")
        if not isinstance(data, list):
            raise ParseError("gateway /inbox did not return a list")
        msgs: List[SmsMessage] = []
        for i, item in enumerate(data):
            try:
                msgs.append(
                    SmsMessage(
                        id=str(item["id"]),
                        address=str(item["sender"]),
                        date_ms=parse_iso_ms(item["createdAt"]),
                        body=str(item.get("contentPreview", "")),
                        source=self.name,
                    )
                )
            except (KeyError, TypeError, ValueError):
                raise ParseError("inbox entry %d malformed" % i)
        LOG.debug("gateway returned %d entries", len(msgs))
        return apply_filters(msgs, since_ms=since_ms, until_ms=until_ms, addresses=addresses, limit=limit)
