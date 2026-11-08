"""Fixture source: offline, deterministic, can model the Android 17 OTP delay."""
from __future__ import annotations

import json
import time
from typing import Callable, List, Optional, Sequence

from ..model import SmsMessage
from .base import SmsSource, SourceStatus, apply_filters


class FixtureSource(SmsSource):
    """Serves a fixed list of messages.

    `visible_after_ms` maps a message id to the epoch-ms moment it becomes visible,
    which models a provider that withholds OTP messages for a while (Android 17's
    3-hour delay). Messages not in the map are visible immediately.
    """

    name = "fixture"

    def __init__(
        self,
        messages: Sequence[SmsMessage],
        visible_after_ms: Optional[dict] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._messages = list(messages)
        self._visible_after = dict(visible_after_ms or {})
        self._clock = clock
        self.fetch_calls = 0

    @classmethod
    def from_json(cls, path: str, **kw) -> "FixtureSource":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        msgs = [
            SmsMessage(id=r["id"], address=r["address"], date_ms=r["date_ms"], body=r["body"], source="fixture")
            for r in data["messages"]
        ]
        return cls(msgs, **kw)

    def add(self, message: SmsMessage) -> None:
        self._messages.append(message)

    def fetch(self, *, since_ms=None, until_ms=None, addresses=None, limit=50) -> List[SmsMessage]:
        self.fetch_calls += 1
        now_ms = int(self._clock() * 1000)
        visible = [m for m in self._messages if self._visible_after.get(m.id, 0) <= now_ms]
        return apply_filters(visible, since_ms=since_ms, until_ms=until_ms, addresses=addresses, limit=limit)

    def check(self) -> SourceStatus:
        return SourceStatus(True, self.name, "fixture", {"messages": str(len(self._messages))})
