"""SmsSource interface and the errors every source speaks."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from ..model import SmsMessage, normalize_sender


class SourceError(Exception):
    """Base class. Messages never contain SMS content."""


class DeviceUnavailable(SourceError):
    """No device, unauthorised, offline, or the gateway is unreachable."""


class PermissionDenied(SourceError):
    """The reader lacks READ_SMS (or the gateway rejected the credentials)."""


class ParseError(SourceError):
    """Output did not have the expected shape. Never retried, never released from."""


@dataclass
class SourceStatus:
    ok: bool
    source: str
    detail: str = ""
    info: Dict[str, str] = field(default_factory=dict)


class SmsSource(ABC):
    """Where received SMS come from. Implementations are read-only."""

    name = "source"

    @abstractmethod
    def fetch(
        self,
        *,
        since_ms: Optional[int] = None,
        until_ms: Optional[int] = None,
        addresses: Optional[Sequence[str]] = None,
        limit: int = 50,
    ) -> List[SmsMessage]:
        """Received messages, newest first. Filters are best-effort on the device side and
        always re-applied by `apply_filters`, so a source that ignores one stays correct."""

    @abstractmethod
    def check(self) -> SourceStatus:
        """Cheap reachability/permission check. Must not read message content."""


def apply_filters(
    messages: Iterable[SmsMessage],
    *,
    since_ms: Optional[int] = None,
    until_ms: Optional[int] = None,
    addresses: Optional[Sequence[str]] = None,
    limit: int = 50,
) -> List[SmsMessage]:
    wanted = {normalize_sender(a) for a in addresses} if addresses else None
    out = []
    for m in messages:
        if since_ms is not None and m.date_ms < since_ms:
            continue
        if until_ms is not None and m.date_ms > until_ms:
            continue
        if wanted is not None and normalize_sender(m.address) not in wanted:
            continue
        out.append(m)
    out.sort(key=lambda m: m.date_ms, reverse=True)
    return out[: max(0, limit)]
