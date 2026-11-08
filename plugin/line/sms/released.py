"""The set of SMS messages whose code was already released (single use survives a restart).

The contract (docs/CONTRACT.md, "OTP delivery") puts persistence in L1's store:
`line.store.released_otps` with `add(message_key)` and `contains(message_key)`. `default_store()`
uses it when it is importable and falls back to `MemoryReleasedStore`, a process-local stub with
the same two methods, until L1 merges. Keys are `source:id:date_ms`; they hold no SMS text.
"""
from __future__ import annotations

import threading
from typing import Any, Set

try:
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[assignment,misc]

from .log import LOG


class ReleasedStore(Protocol):
    def add(self, message_key: str) -> None: ...

    def contains(self, message_key: str) -> bool: ...


class MemoryReleasedStore:
    """Process-local stand-in: loses its memory on restart (R7 in docs/lanes/L4.md)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: Set[str] = set()

    def add(self, message_key: str) -> None:
        with self._lock:
            self._keys.add(message_key)

    def contains(self, message_key: str) -> bool:
        with self._lock:
            return message_key in self._keys


class ReleasedSet:
    """Set-like view (`in`, `add`) over a ReleasedStore, as `OtpMatcher` expects."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def __contains__(self, key: str) -> bool:
        return bool(self.store.contains(key))

    def add(self, key: str) -> None:
        self.store.add(key)


def default_store() -> Any:
    """L1's `line.store.released_otps` when present, else an in-memory stub."""
    try:
        # package-relative: the plugin loads under Hermes's own package name, not as top-level `line`
        from ..store import released_otps   # type: ignore[import-not-found]

        if callable(getattr(released_otps, "add", None)) and callable(getattr(released_otps, "contains", None)):
            return released_otps
    except ImportError:
        pass
    LOG.warning("line.store.released_otps not available: released-message set is in memory only")
    return MemoryReleasedStore()
