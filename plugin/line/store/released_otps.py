"""Persistent set of released OTP message keys (docs/CONTRACT.md, "OTP delivery").

``line.sms`` marks an SMS as used when its code is released; this set survives
restarts so the same message can never be released twice, even for a new
approval. Only a SHA-256 of the message key is stored, never message text.

Use ``ReleasedOtps(store)`` directly (it is set-like, so it can be passed as
``OtpService(used=...)``), or ``bind(store)`` once and the module functions
``add(message_key)`` / ``contains(message_key)``.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Optional

_TABLE = "CREATE TABLE IF NOT EXISTS released_otps (key_hash TEXT PRIMARY KEY, released_at REAL NOT NULL)"


def _h(message_key: str) -> str:
    if not isinstance(message_key, str) or not message_key:
        raise ValueError("message_key must be a non-empty string")
    return hashlib.sha256(message_key.encode("utf-8")).hexdigest()


class ReleasedOtps:
    def __init__(self, store) -> None:
        self._store = store
        with store._lock:
            store._conn.execute(_TABLE)

    def add(self, message_key: str) -> None:
        with self._store._lock:
            self._store._conn.execute("INSERT OR IGNORE INTO released_otps VALUES (?, ?)",
                                      (_h(message_key), self._store._clock()))

    def contains(self, message_key: str) -> bool:
        try:
            key = _h(message_key)
        except ValueError:
            return False
        with self._store._lock:
            try:
                row = self._store._conn.execute("SELECT 1 FROM released_otps WHERE key_hash = ?", (key,)).fetchone()
            except sqlite3.Error:
                return True  # fail closed: an unreadable set counts as "already released"
        return row is not None

    __contains__ = contains

    def __len__(self) -> int:
        with self._store._lock:
            return int(self._store._conn.execute("SELECT COUNT(*) FROM released_otps").fetchone()[0])


_DEFAULT: Optional[ReleasedOtps] = None


def bind(store) -> ReleasedOtps:
    global _DEFAULT
    _DEFAULT = ReleasedOtps(store)
    return _DEFAULT


def add(message_key: str) -> None:
    if _DEFAULT is None:
        raise RuntimeError("released_otps is not bound to a store")
    _DEFAULT.add(message_key)


def contains(message_key: str) -> bool:
    if _DEFAULT is None:
        return True  # fail closed
    return _DEFAULT.contains(message_key)
