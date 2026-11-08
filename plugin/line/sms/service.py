"""OTP release: the one function that turns an SMS code into a value a caller can use.

Order of checks in `OtpService.attempt` (every failure returns a fixed status, never text
from a message):

1. the task must be registered (the owner approved sender rules, a time window and a destination);
2. a consent token must be supplied;
3. read the inbox and DRY-RUN the matcher: sender rule AND window AND exactly one
   unambiguous code AND the message was not released before;
4. only if step 3 would release: under one lock, re-check "not used", spend the owner's
   single-use consent token (`ConsentStore.consume`, bound to the task, sender rules, window and
   destination), mark the message used, and return the code as `SecretText`.

Consent is spent only when a code is actually handed over, so polling for a code that has
not arrived yet does not burn the approval. A consumed token or a used message can never
release again (replay). The consent binding is built from the *registered* task, never
from caller input, so a caller cannot widen the senders or the window.

The code is never unwrapped here: the caller receives a `SecretText` and decides how to
deliver it (see `delivery.py`).
"""
from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional

from .consent import OTP_ACTION, ConsentStoreLike
from .log import LOG
from .masking import mask_sender
from .matching import ApprovedTask, Decision, MatchOutcome, OtpMatcher, message_key
from .model import iso_utc, normalize_sender
from .released import ReleasedSet, default_store
from .scrub import ValueScrubber, register_released
from .secret import SecretText
from .sources.base import SmsSource, SourceError

MAX_WAIT_S = 120.0
MAX_TASK_ID = 80


class ReleaseStatus(str, enum.Enum):
    RELEASED = "released"
    NO_TASK = "no_task"              # task id not registered / not approved
    NO_CONSENT = "no_consent"        # token missing, expired, reused or bound to something else
    NO_MESSAGE = "no_message"        # nothing from an approved sender in the window yet
    NO_CODE = "no_code"              # approved sender in the window, but no code found
    AMBIGUOUS = "ambiguous"          # two competing codes: ask the owner, never guess
    ALREADY_USED = "already_used"    # that message's code was already released once
    EXPIRED = "expired"              # the approved window closed
    SOURCE_ERROR = "source_error"    # the phone/gateway could not be read
    STORE_ERROR = "store_error"      # the released-message store failed: nothing is released
    APPROVAL_CHANGED = "approval_changed"   # the owner's approval was replaced or revoked, or is not the pending one
    DISABLED = "disabled"            # OTP release is not configured


@dataclass(frozen=True)
class ReleaseResult:
    """Outcome of one release attempt. `secret` is set only for RELEASED."""

    status: ReleaseStatus
    task_id: str
    secret: Optional[SecretText] = None
    sender: str = ""          # masked sender of the message the decision is about, or ""
    considered: int = 0
    approval: Optional["Approval"] = None   # the approval (generation + binding) this release was made under

    def __repr__(self) -> str:
        return "ReleaseResult(%s)" % self.log_line()

    def log_line(self) -> str:
        return "otp task=%s status=%s considered=%d from=%s" % (
            self.task_id, self.status.value, self.considered, self.sender or "-"
        )


def binding_for(task: ApprovedTask) -> Dict[str, Any]:
    """The consent binding for a task: what the owner approved, in canonical form.

    The approval flow issues the token with exactly this dict; `OtpService.attempt` rebuilds
    it from the registered task. Sender rules are normalised and sorted so spelling and
    order do not matter. The destination is part of it: `{"kind":"fill","origin":…}` or
    `{"kind":"call","call_id":…,"counterpart":…}`.
    """
    if task.destination is None:
        raise ValueError("an OTP task needs a destination")
    senders = sorted({normalize_sender(r, task.default_cc) for r in task.sender_rules if normalize_sender(r, task.default_cc)})
    return {
        "senders": senders,
        "window_start_ms": int(task.window_start_ms),
        "window_end_ms": int(task.window_end_ms),
        "destination": task.destination.binding(),
    }


def approval_prompt(task: ApprovedTask) -> str:
    """The text the owner approval prompt must show: what may be read, when, and where it goes."""
    if task.destination is None:
        raise ValueError("an OTP task needs a destination")
    return "Allow Hermes to use the verification code from %s received between %s and %s. %s." % (
        ", ".join(sorted(task.sender_rules)), iso_utc(task.window_start_ms), iso_utc(task.window_end_ms),
        task.destination.prompt_text(),
    )


@dataclass(frozen=True)
class Approval:
    """One owner approval: the registered terms, an immutable generation number and the exact binding.

    Every registration gets a new generation; a sealed code is tied to the generation (and binding) it
    was released under, so replacing or revoking the approval can never redirect a retained code.
    """

    task: ApprovedTask
    generation: int
    binding: str      # canonical JSON of `binding_for(task)`


def _canon(binding: Mapping[str, Any]) -> str:
    import json

    return json.dumps(binding, sort_keys=True, separators=(",", ":"))


class OtpTaskRegistry:
    """Owner-approved OTP tasks by id, each with a generation. Replacement goes through `forget` + `register`.

    `gate` is a re-entrant lock held by `register`/`forget` and by delivery while it validates an approval and
    hands a code to a sink, so an approval cannot be replaced between the check and the delivery.
    Listeners (`on_change`) are called under the gate when a task id is registered, replaced or forgotten.
    """

    def __init__(self) -> None:
        self.gate = threading.RLock()
        self._lock = threading.Lock()
        self._entries: Dict[str, Approval] = {}
        self._counter = 0
        self._listeners: List[Callable[[str], None]] = []

    def on_change(self, listener: Callable[[str], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def register(self, task: ApprovedTask) -> int:
        """Register terms; returns the approval generation. Identical terms keep their generation."""
        if not task.task_id or len(task.task_id) > MAX_TASK_ID or not task.sender_rules:
            raise ValueError("an OTP task needs an id (up to %d characters) and at least one sender rule" % MAX_TASK_ID)
        if task.window_end_ms <= task.window_start_ms:
            raise ValueError("the OTP window must end after it starts")
        if task.destination is None:
            raise ValueError("an OTP task needs a destination (page origin or call)")
        binding = _canon(binding_for(task))
        with self.gate:
            with self._lock:
                old = self._entries.get(task.task_id)
                if old is not None:
                    if old.task != task:
                        raise ValueError("task %r is already registered with different terms" % task.task_id)
                    return old.generation
                self._counter += 1
                self._entries[task.task_id] = Approval(task, self._counter, binding)
                gen = self._counter
                listeners = list(self._listeners)
            self._notify(listeners, task.task_id)
            return gen

    def get(self, task_id: str) -> Optional[ApprovedTask]:
        entry = self.get_entry(task_id)
        return entry.task if entry is not None else None

    def get_entry(self, task_id: str) -> Optional[Approval]:
        with self._lock:
            return self._entries.get(task_id)

    def generation(self, task_id: str) -> Optional[int]:
        entry = self.get_entry(task_id)
        return entry.generation if entry is not None else None

    def forget(self, task_id: str) -> None:
        with self.gate:
            with self._lock:
                removed = self._entries.pop(task_id, None)
                listeners = list(self._listeners)
            if removed is not None:
                self._notify(listeners, task_id)

    @staticmethod
    def _notify(listeners: List[Callable[[str], None]], task_id: str) -> None:
        for fn in listeners:
            try:
                fn(task_id)
            except Exception as exc:   # a listener must never block a replacement
                LOG.warning("otp registry listener error: %s", type(exc).__name__)


AuditHook = Callable[[Mapping[str, Any]], None]


class OtpService:
    def __init__(
        self,
        source: SmsSource,
        registry: Optional[OtpTaskRegistry] = None,
        *,
        released: Optional[Any] = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        interval_s: float = 3.0,
        max_source_errors: int = 3,
        audit: Optional[AuditHook] = None,
        scrubber: Optional[ValueScrubber] = None,
    ) -> None:
        self.source = source
        self.registry = registry if registry is not None else OtpTaskRegistry()
        # `line.store.released_otps` (L1, persisted) or its in-memory stand-in: add/contains on message keys
        self.released = released if released is not None else default_store()
        self.used = ReleasedSet(self.released)
        self._clock = clock
        self._sleep = sleep
        self._interval_s = interval_s
        self._max_errors = max_source_errors
        self._audit = audit
        self._scrubber = scrubber
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public

    def attempt(
        self,
        task_id: str,
        consent_token: Optional[str],
        consent_store: ConsentStoreLike,
        *,
        timeout_s: float = 0.0,
        approval_generation: Optional[int] = None,
    ) -> ReleaseResult:
        if not isinstance(task_id, str) or len(task_id) > MAX_TASK_ID:
            return self._done(ReleaseResult(ReleaseStatus.NO_TASK, ""))   # model-supplied text is not echoed into logs
        approval = self.registry.get_entry(task_id)
        if approval is None:
            return self._done(ReleaseResult(ReleaseStatus.NO_TASK, task_id))
        if approval_generation is not None and approval_generation != approval.generation:
            # the engine's pending approval is not the one registered here (M1): refuse before reading the phone
            return self._done(ReleaseResult(ReleaseStatus.APPROVAL_CHANGED, task_id))
        task = approval.task
        if not consent_token or not isinstance(consent_token, str):
            return self._done(ReleaseResult(ReleaseStatus.NO_CONSENT, task_id))

        if int(self._clock() * 1000) > task.window_end_ms:
            # The approval covers the window only: no release afterwards, even for a message
            # that arrived inside it. Checked before the phone is read.
            return self._done(ReleaseResult(ReleaseStatus.EXPIRED, task_id))

        matcher = OtpMatcher(task, used=self.used)
        timeout_s = max(0.0, min(float(timeout_s), MAX_WAIT_S))
        deadline = self._clock() + timeout_s
        errors = 0
        while True:
            loop_start = self._clock()
            now_ms = int(self._clock() * 1000)
            fetch_failed = False
            try:
                msgs = self.source.fetch(since_ms=task.window_start_ms - task.clock_skew_ms, until_ms=None)
                errors = 0
            except SourceError as exc:
                fetch_failed = True
                errors += 1
                LOG.warning("otp task=%s source %s error %d/%d: %s",
                            task_id, self.source.name, errors, self._max_errors, type(exc).__name__)
                if errors >= self._max_errors:
                    return self._done(ReleaseResult(ReleaseStatus.SOURCE_ERROR, task_id))
                msgs = []
            try:
                outcome = matcher.evaluate(msgs, now_ms=now_ms, commit=False)
            except Exception as exc:   # the released-message store is read during matching
                LOG.warning("otp task=%s matcher error: %s", task_id, type(exc).__name__)
                return self._done(ReleaseResult(ReleaseStatus.STORE_ERROR, task_id))

            if outcome.decision is Decision.RELEASE:
                return self._done(self._commit(approval, outcome, consent_token, consent_store))
            if outcome.decision in (Decision.AMBIGUOUS, Decision.ALREADY_USED, Decision.EXPIRED):
                return self._done(self._plain(outcome))
            if now_ms > task.window_end_ms:
                return self._done(ReleaseResult(ReleaseStatus.EXPIRED, task_id, considered=outcome.considered))
            if self._clock() + self._interval_s > deadline:
                if fetch_failed:   # "nothing arrived" would hide that the phone could not be read
                    return self._done(ReleaseResult(ReleaseStatus.SOURCE_ERROR, task_id))
                return self._done(self._plain(outcome))
            # the interval counts from the START of the poll: a slow read (seconds over Wi-Fi) must not be followed by
            # another full wait, or a code that arrived meanwhile would be read an interval late
            self._sleep(max(0.0, self._interval_s - (self._clock() - loop_start)))

    # ------------------------------------------------------------------ internals

    def _commit(
        self, approval: Approval, outcome: MatchOutcome, token: str, store: ConsentStoreLike
    ) -> ReleaseResult:
        task = approval.task
        msg = outcome.message
        assert msg is not None and outcome.code is not None
        key = message_key(msg)
        # gate first: no replacement of the approval can slip in between the check and the consume
        with self.registry.gate, self._lock:
            cur = self.registry.get_entry(task.task_id)
            if cur is None or cur.generation != approval.generation:
                return ReleaseResult(ReleaseStatus.APPROVAL_CHANGED, task.task_id, None, _who(outcome), outcome.considered)
            try:
                already = key in self.used
            except Exception as exc:
                LOG.warning("otp task=%s released-store error: %s", task.task_id, type(exc).__name__)
                return ReleaseResult(ReleaseStatus.STORE_ERROR, task.task_id, None, _who(outcome), outcome.considered)
            if already:
                return ReleaseResult(ReleaseStatus.ALREADY_USED, task.task_id, None, _who(outcome), outcome.considered)
            try:
                ok = bool(store.consume(token, OTP_ACTION, task.task_id, json_binding(approval)))
            except Exception as exc:   # a broken consent store must fail closed
                LOG.warning("otp task=%s consent store error: %s", task.task_id, type(exc).__name__)
                ok = False
            if not ok:
                return ReleaseResult(ReleaseStatus.NO_CONSENT, task.task_id, None, _who(outcome), outcome.considered)
            try:
                self.used.add(key)
            except Exception as exc:   # cannot record the release: do not release (consent is spent; fail closed)
                LOG.warning("otp task=%s released-store error: %s", task.task_id, type(exc).__name__)
                return ReleaseResult(ReleaseStatus.STORE_ERROR, task.task_id, None, _who(outcome), outcome.considered)
            # From this moment the exact value is scrubbed from logs, transcripts and tool results.
            if self._scrubber is not None:
                self._scrubber.register(outcome.code)
            else:
                register_released(outcome.code)
        return ReleaseResult(ReleaseStatus.RELEASED, task.task_id, outcome.code, _who(outcome), outcome.considered, approval)

    @staticmethod
    def _plain(outcome: MatchOutcome) -> ReleaseResult:
        status = {
            Decision.NO_MESSAGE: ReleaseStatus.NO_MESSAGE,
            Decision.NO_CODE: ReleaseStatus.NO_CODE,
            Decision.AMBIGUOUS: ReleaseStatus.AMBIGUOUS,
            Decision.ALREADY_USED: ReleaseStatus.ALREADY_USED,
            Decision.EXPIRED: ReleaseStatus.EXPIRED,
        }[outcome.decision]
        return ReleaseResult(status, outcome.task_id, None, _who(outcome), outcome.considered)

    def _done(self, res: ReleaseResult) -> ReleaseResult:
        LOG.info(res.log_line())
        if self._audit is not None:
            try:
                # status and masked facts only: no body, no code
                task = self.registry.get(res.task_id)
                dest = task.destination.log_text() if task is not None and task.destination is not None else ""
                self._audit({"event": "otp_release_attempt", "task_id": res.task_id,
                             "status": res.status.value, "sender": res.sender, "considered": res.considered,
                             "destination": dest})
            except Exception as exc:   # an audit hook must never change the outcome
                LOG.warning("otp audit hook error: %s", type(exc).__name__)
        return res


def json_binding(approval: Approval) -> Dict[str, Any]:
    """The binding dict of an approval, rebuilt from its frozen task (what the consent token was issued for)."""
    return binding_for(approval.task)


def _who(outcome: MatchOutcome) -> str:
    return mask_sender(outcome.message.address) if outcome.message else ""


# ---------------------------------------------------------------------- contract entry point

_default: Optional[OtpService] = None
_default_lock = threading.Lock()


def configure(service: Optional[OtpService]) -> None:
    """Install (or, with None, remove) the service `release_otp` uses. Called by the engine."""
    global _default
    with _default_lock:
        _default = service


def get_service() -> Optional[OtpService]:
    with _default_lock:
        return _default


def release_otp(
    task_id: str, consent_token: str, consent_store: ConsentStoreLike, *, timeout_s: float = 0.0,
    approval_generation: Optional[int] = None,
) -> Optional[SecretText]:
    """Contract entry point (docs/CONTRACT.md): the code as a SecretText, or None.

    None covers every non-release (not configured, no consent, wrong sender or window, no
    message yet, two codes, replay, expired, phone unreadable). Use `get_service().attempt`
    when the reason matters; it returns a `ReleaseResult` with a fixed status vocabulary.
    """
    svc = get_service()
    if svc is None:
        LOG.info("otp task=%s status=%s", task_id, ReleaseStatus.DISABLED.value)
        return None
    return svc.attempt(task_id, consent_token, consent_store, timeout_s=timeout_s,
                       approval_generation=approval_generation).secret
