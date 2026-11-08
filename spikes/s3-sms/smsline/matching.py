"""Match a received SMS to an owner-approved task, and decide whether a code may be released.

This is the deterministic gate from ADR 0001 ("SMS code belongs to task", yes/no):
sender rule AND time window AND an unambiguous code AND not used before. Anything else
is "do not release". The decision never contains the code; `MatchOutcome.code` is a
SecretText and `release()` is the single approved way to read it.
"""
from __future__ import annotations

import enum
import fnmatch
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Set, Tuple

from .log import LOG
from .model import SmsMessage, normalize_sender
from .otp import OtpResult, extract_otp
from .secret import SecretText
from .sources.base import SmsSource, SourceError


class Decision(str, enum.Enum):
    RELEASE = "release"              # one unambiguous code from an approved sender in the window
    NO_MESSAGE = "no_message"        # nothing from an approved sender in the window (yet)
    NO_CODE = "no_code"              # approved sender, in window, but no code found
    AMBIGUOUS = "ambiguous"          # two competing codes: do not guess, ask the owner
    ALREADY_USED = "already_used"    # this message's code was already released
    EXPIRED = "expired"              # the approval window closed with nothing to release


@dataclass(frozen=True)
class ApprovedTask:
    """What the owner approved. The window is wall-clock ms since epoch (UTC)."""

    task_id: str
    sender_rules: Tuple[str, ...]    # "+15550100123", "32665", "EXAMPLEBANK", "EXAMPLE*"
    window_start_ms: int
    window_end_ms: int
    clock_skew_ms: int = 30_000      # phone clock vs Mac clock; widens the start only
    default_cc: str = "1"

    def sender_ok(self, address: str) -> bool:
        a = normalize_sender(address, self.default_cc)
        if not a:
            return False
        for rule in self.sender_rules:
            r = normalize_sender(rule, self.default_cc)
            if not r:
                continue
            if "*" in r or "?" in r:
                if fnmatch.fnmatchcase(a, r):
                    return True
            elif a == r:
                return True
        return False

    def in_window(self, date_ms: int) -> bool:
        return (self.window_start_ms - self.clock_skew_ms) <= date_ms <= self.window_end_ms


@dataclass(frozen=True)
class MatchOutcome:
    decision: Decision
    task_id: str
    message: Optional[SmsMessage] = None
    code: Optional[SecretText] = None      # present only for Decision.RELEASE
    considered: int = 0                    # messages that passed sender+window
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def release(self) -> str:
        """The single approved read of the code. Raises unless the decision is RELEASE."""
        if self.decision is not Decision.RELEASE or self.code is None:
            raise PermissionError("no code approved for release (%s)" % self.decision.value)
        return self.code.reveal()

    def log_line(self) -> str:
        who = self.message.log_summary() if self.message else "no message"
        return "otp task=%s decision=%s considered=%d %s" % (
            self.task_id, self.decision.value, self.considered, who
        )


def message_key(m: SmsMessage) -> str:
    return "%s:%s:%d" % (m.source, m.id, m.date_ms)


class TaskMatcher:
    """Evaluates fetched messages against one approved task. Single-use per message."""

    def __init__(self, task: ApprovedTask, used: Optional[Set[str]] = None) -> None:
        self.task = task
        self.used: Set[str] = used if used is not None else set()

    def evaluate(self, messages: Iterable[SmsMessage], now_ms: Optional[int] = None) -> MatchOutcome:
        t = self.task
        now = int(time.time() * 1000) if now_ms is None else now_ms
        eligible: List[SmsMessage] = [
            m for m in messages if t.sender_ok(m.address) and t.in_window(m.date_ms)
        ]
        eligible.sort(key=lambda m: m.date_ms, reverse=True)   # newest first: a resend wins
        if not eligible:
            return MatchOutcome(
                Decision.EXPIRED if now > t.window_end_ms else Decision.NO_MESSAGE, t.task_id
            )
        saw_used = False
        newest: Optional[SmsMessage] = eligible[0]
        for m in eligible:
            res: OtpResult = extract_otp(m.body)
            if res.ambiguous:
                # a newer ambiguous message must not be silently skipped for an older code
                return MatchOutcome(Decision.AMBIGUOUS, t.task_id, m, None, len(eligible), ("two-codes",))
            if not res.primary:
                continue
            if message_key(m) in self.used:
                saw_used = True
                continue
            self.used.add(message_key(m))
            return MatchOutcome(
                Decision.RELEASE, t.task_id, m, res.primary.value, len(eligible), res.primary.reasons
            )
        if saw_used:
            return MatchOutcome(Decision.ALREADY_USED, t.task_id, newest, None, len(eligible))
        return MatchOutcome(Decision.NO_CODE, t.task_id, newest, None, len(eligible))


def wait_for_code(
    source: SmsSource,
    matcher: TaskMatcher,
    *,
    timeout_s: float = 120.0,
    interval_s: float = 3.0,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    max_source_errors: int = 3,
) -> MatchOutcome:
    """Poll `source` until the matcher releases a code, the task window closes, or timeout.

    Terminal non-release outcomes (AMBIGUOUS, ALREADY_USED) are returned at once: more
    polling cannot fix them. Transient source errors are tolerated up to a limit, then
    re-raised so the caller can fall back to another source.
    """
    t = matcher.task
    deadline = clock() + timeout_s
    errors = 0
    last: Optional[MatchOutcome] = None
    while True:
        now_ms = int(clock() * 1000)
        try:
            msgs = source.fetch(since_ms=t.window_start_ms - t.clock_skew_ms, until_ms=None)
            errors = 0
        except SourceError as exc:
            errors += 1
            LOG.warning("source %s error %d/%d: %s", source.name, errors, max_source_errors, type(exc).__name__)
            if errors >= max_source_errors:
                raise
            msgs = []
        last = matcher.evaluate(msgs, now_ms=now_ms)
        LOG.info(last.log_line())
        if last.decision in (Decision.RELEASE, Decision.AMBIGUOUS, Decision.ALREADY_USED, Decision.EXPIRED):
            return last
        if clock() + interval_s > deadline or now_ms > t.window_end_ms:
            return MatchOutcome(Decision.EXPIRED, t.task_id, last.message, None, last.considered)
        sleep(interval_s)
