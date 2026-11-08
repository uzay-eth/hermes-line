"""The hold gate: when a voice-model session may exist, and how long one did.

The voice model is billed by the minute (docs.x.ai lists $0.08/min for grok-voice), so
it must not be connected while the call sits in a phone menu or on hold. The gate is a
pure decision over the call state (L1's ``CallState`` values) plus three requests:

* the human part of the call: ``human_detected`` / ``agent_talking`` (and
  ``ringing_owner`` when it began from a human conversation) -> open;
* a fixed line that has to be synthesized (an IVR answer, the live-person question),
  while no cached audio exists -> open until it has been rendered;
* an optional short pre-warm after Hermes asked "am I speaking with a live person?".

Everything else -> closed: ``dialing``, ``ivr``, ``on_hold`` without pending speech,
``owner_connected``, ``awaiting_approval``, ``ended``, ``failed``. Returning to
``ivr``/``on_hold`` ends the human part ("hold resumed").
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

PRE_HUMAN = frozenset({"dialing", "ivr", "on_hold"})
HUMAN = frozenset({"human_detected", "agent_talking"})
CLOSED = frozenset({"awaiting_approval", "owner_connected", "ended", "failed"})
KNOWN = PRE_HUMAN | HUMAN | CLOSED | {"ringing_owner"}


class SessionGate:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self.state = "dialing"
        self.from_human = False         # the current stretch began with a human on the line
        self.speech_pending = 0
        self.prewarm_until = 0.0

    def on_state(self, state: str) -> None:
        if state not in KNOWN:
            return
        if state in HUMAN:
            self.from_human = True
        elif state in PRE_HUMAN or state in CLOSED:
            self.from_human = False
            if state in CLOSED:
                self.speech_pending = 0
                self.prewarm_until = 0.0
        self.state = state

    def prewarm(self, ttl_s: float) -> None:
        if self.state in PRE_HUMAN:
            self.prewarm_until = max(self.prewarm_until, self._clock() + max(0.0, ttl_s))

    def decide(self) -> Tuple[bool, str]:
        s = self.state
        if s in CLOSED:
            return False, s
        if s in HUMAN:
            return True, "human"
        if s == "ringing_owner" and self.from_human:
            return True, "ringing_owner"
        if self.speech_pending > 0:
            return True, "speech"
        if s in PRE_HUMAN and self._clock() < self.prewarm_until:
            return True, "prewarm"
        return False, s


class ConnectionMeter:
    """Seconds a provider session was connected, by the injected (virtual) clock."""

    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._opened_at: Optional[float] = None
        self.intervals: List[Tuple[float, float, str]] = []
        self.opens = 0

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None

    def open(self, reason: str = "") -> None:
        if self._opened_at is None:
            self._opened_at = self._clock()
            self._reason = reason
            self.opens += 1

    def close(self) -> None:
        if self._opened_at is not None:
            self.intervals.append((self._opened_at, self._clock(), self._reason))
            self._opened_at = None

    def seconds(self) -> float:
        total = sum(end - start for start, end, _ in self.intervals)
        if self._opened_at is not None:
            total += self._clock() - self._opened_at
        return total

    def seconds_between(self, start: float, end: float) -> float:
        """Connected seconds that overlap the window ``[start, end]``."""
        spans = list(self.intervals)
        if self._opened_at is not None:
            spans.append((self._opened_at, self._clock(), self._reason))
        return sum(max(0.0, min(e, end) - max(s, start)) for s, e, _ in spans)
