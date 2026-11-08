"""How a released code reaches its destination without entering Hermes's history.

Why this module exists (docs/lanes/L4.md section 1 has the citations): Hermes core writes a tool's
result string to `state.db` verbatim, right after the tool runs and before the model sees it, and
never rewrites that row (`agent/tool_executor.py:1172-1173`, "Already-written rows must never be
rewritten later" at `:207`). There is no ephemeral or redacted tool-result mechanism for plugin
tools. So a code is never returned to the model. Delivery is SEALED (docs/CONTRACT.md, "OTP delivery"):
the model names a destination, the plugin releases the code (owner consent consumed) and hands it
straight to that destination. The tool result carries a status and nothing else.

Two destinations, each approved by the owner together with sender and window:

* ``fill``  `OtpDelivery.fill` (off unless `otp_browser_fill`): types the code into browser field(s) with Hermes's own `browser_type`
  through `ctx.dispatch_tool` (registry-level: no tool hooks, no `tool.started`, no history). Before
  every attempt the page's current origin must equal the origin the owner approved.
* ``call``  `OtpDelivery.to_call`: hands the code to the live call through
  `line.engine.sealed.send_secret_to_call(call_id, secret, mode)` (L1), as DTMF digits or speech.
  The transcript records `CALL_TRANSCRIPT_MARKER` only.

A released code waits in a sealed in-memory slot (120 s, at most 4 attempts) so a wrong field, a
missing box or a call that is not ready yet can be retried without a second SMS or approval. The
slot only ever feeds the sink; nothing reads it back out.

Unwrapping a `SecretText` happens in exactly one place here: the browser sink (the code goes to the page).
"""
from __future__ import annotations

import enum
import inspect
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence

from .consent import ConsentStoreLike
from .destination import CALL, FILL, normalize_origin
from .engine_stub import resolve_send_secret_to_call
from .log import LOG
from .secret import SecretText
from .service import Approval, OtpService, ReleaseResult, ReleaseStatus

SLOT_TTL_S = 120.0
SLOT_MAX_FILLS = 4
MAX_TARGETS = 12
CALL_TRANSCRIPT_MARKER = "[verification code sent]"
CALL_MODES = ("dtmf", "speak")


class FillStatus(str, enum.Enum):
    FILLED = "filled"
    SENT = "sent"                           # delivered to the live call
    TARGET_MISMATCH = "target_mismatch"     # number of fields does not fit this code; slot kept for a retry
    SINK_FAILED = "sink_failed"             # the page / call refused; slot kept for a retry
    WRONG_DESTINATION = "wrong_destination" # the owner approved a different kind of destination or another call
    ORIGIN_MISMATCH = "origin_mismatch"     # the open page is not the approved site: nothing was released
    ORIGIN_UNKNOWN = "origin_unknown"       # the open page's origin could not be read: nothing was released
    ORIGIN_CHANGED = "origin_changed"       # the page's origin changed (or could not be read) right before/after typing
    SINK_UNSUPPORTED = "sink_unsupported"   # the call sink cannot take an approval generation: refused, nothing released
    MODE_UNSUPPORTED = "mode_unsupported"   # DTMF needs digits; slot kept
    BAD_REQUEST = "bad_request"
    DISABLED = "disabled"                   # no sink configured


@dataclass(frozen=True)
class DeliveryResult:
    """What the tool reports to the model. It never contains the code."""

    status: str                 # a ReleaseStatus or FillStatus value
    task_id: str
    filled: int = 0
    sender: str = ""
    hint: str = ""

    def to_json(self) -> str:
        d: Dict[str, Any] = {"status": self.status, "task_id": self.task_id}
        if self.filled:
            d["filled_fields"] = self.filled
        if self.sender:
            d["from"] = self.sender
        if self.hint:
            d["hint"] = self.hint
        return json.dumps(d, sort_keys=True)

    @property
    def ok(self) -> bool:
        return self.status in (FillStatus.FILLED.value, FillStatus.SENT.value)


_HINTS = {
    ReleaseStatus.NO_TASK.value: "No SMS code was approved for this task. Ask the owner to approve it first.",
    ReleaseStatus.NO_CONSENT.value: "The owner has not approved releasing a code for this task (or the approval was already used).",
    ReleaseStatus.NO_MESSAGE.value: "No message from the approved sender has arrived in the approved window yet. Wait a few seconds and call again.",
    ReleaseStatus.NO_CODE.value: "A message arrived from the approved sender but no code could be found in it.",
    ReleaseStatus.AMBIGUOUS.value: "The message holds more than one possible code. Do not guess: ask the owner.",
    ReleaseStatus.ALREADY_USED.value: "That message's code was already released once. Ask the service to send a new code.",
    ReleaseStatus.EXPIRED.value: "The approved window has closed. Ask the owner for a new approval.",
    ReleaseStatus.SOURCE_ERROR.value: "The phone could not be read. Check the USB cable and that the phone is unlocked.",
    ReleaseStatus.STORE_ERROR.value: "The record of released codes could not be written, so nothing was released. Tell the owner.",
    ReleaseStatus.APPROVAL_CHANGED.value: "The owner's approval for this task was replaced or is no longer the pending one. Nothing was delivered; ask the owner.",
    FillStatus.TARGET_MISMATCH.value: "Pass either one field (the whole code) or one field per character, in order.",
    FillStatus.SINK_FAILED.value: "The code could not be delivered. Check the destination and retry; no new approval is needed yet.",
    FillStatus.WRONG_DESTINATION.value: "The owner approved the code for a different destination than this tool call. Ask the owner.",
    FillStatus.ORIGIN_MISMATCH.value: "The open page is not the site the owner approved. Nothing was released. Go to the approved site or ask the owner.",
    FillStatus.ORIGIN_UNKNOWN.value: "The open page's site could not be determined. Nothing was released. Open the approved site and retry.",
    FillStatus.ORIGIN_CHANGED.value: "The page's site changed while the code was being typed. Stop and tell the owner; the code may have reached another page.",
    FillStatus.DISABLED.value: "This way of delivering a code is not set up or is switched off (typing into the browser is off by default). Ask the owner, or use the live call target.",
    FillStatus.SINK_UNSUPPORTED.value: "The call sink is out of date and cannot bind the code to the owner's approval. Nothing was released. Tell the owner.",
    FillStatus.MODE_UNSUPPORTED.value: "This code has letters, which DTMF cannot send. Use mode speak.",
    FillStatus.BAD_REQUEST.value: "Check the arguments: task_id plus targets (fields) or call_id and mode.",
}


# ---------------------------------------------------------------------------- sinks and probes

class Sink:
    """Where a sealed code is typed. Implementations must not keep, log or return the value."""

    def fill(self, secret: SecretText, target: str, *, task_id: Optional[str] = None) -> bool:
        raise NotImplementedError


class BrowserTypeSink(Sink):
    """Types into a page field with Hermes's own `browser_type` tool.

    `dispatch` is `ctx.dispatch_tool` (registry-level dispatch: no pre/post tool hooks, no
    tool.started event, nothing written to history). The tool's JSON answer echoes the typed
    text, so it is parsed for `success` and then dropped.
    """

    def __init__(self, dispatch: Callable[..., str], tool_name: str = "browser_type") -> None:
        self._dispatch = dispatch
        self._tool = tool_name

    def __repr__(self) -> str:
        return "BrowserTypeSink(%s)" % self._tool

    def fill(self, secret: SecretText, target: str, *, task_id: Optional[str] = None) -> bool:
        kwargs: Dict[str, Any] = {}
        if task_id:
            kwargs["task_id"] = task_id
        try:
            raw = self._dispatch(self._tool, {"ref": target, "text": secret.reveal()}, **kwargs)   # the code goes to the page
        except Exception as exc:   # exception text can carry the arguments: log the type only
            LOG.warning("otp sink %s failed: %s", self._tool, type(exc).__name__)
            return False
        try:
            return bool(json.loads(raw).get("success"))
        except Exception:
            return False
        finally:
            del raw


class BrowserOriginProbe:
    """Reads the open page's origin through `browser_console` (`window.location.origin`).

    Returns a normalised origin or None when it cannot be read; the caller then releases nothing.
    """

    EXPRESSION = "window.location.origin"

    def __init__(self, dispatch: Callable[..., str], tool_name: str = "browser_console") -> None:
        self._dispatch = dispatch
        self._tool = tool_name

    def __call__(self, browser_task_id: Optional[str] = None) -> Optional[str]:
        kwargs: Dict[str, Any] = {}
        if browser_task_id:
            kwargs["task_id"] = browser_task_id
        try:
            data = json.loads(self._dispatch(self._tool, {"expression": self.EXPRESSION}, **kwargs))
            if not data.get("success"):
                return None
            return normalize_origin(str(data.get("result") or "").strip().strip('"').strip("'"))
        except Exception:
            return None


class CallSink:
    """Hands a sealed code to a live call via `line.engine.sealed.send_secret_to_call` (L1).

    The approval generation is MANDATORY at this boundary (R1-delta2 N1 residual): the sink is always called as
    `send(call_id, secret, mode, approval_generation=<int>)` and L1 re-checks it on its loop right before the
    frame is queued. A sink that cannot accept `approval_generation` (a legacy three-argument wrapper) is
    refused: it is never called and `OtpDelivery.to_call` reports `sink_unsupported` before any code is released.
    """

    def __init__(self, send: Optional[Callable[..., bool]] = None) -> None:
        self._send = send

    def __repr__(self) -> str:
        return "CallSink()"

    def accepts_generation(self) -> bool:
        return _accepts_generation(self._send or resolve_send_secret_to_call())

    def send(self, call_id: str, secret: SecretText, mode: str, approval_generation: Optional[int] = None) -> bool:
        if not isinstance(approval_generation, int) or isinstance(approval_generation, bool):
            LOG.warning("otp call sink refused: no approval generation")
            return False
        send = self._send or resolve_send_secret_to_call()
        if not _accepts_generation(send):
            LOG.warning("otp call sink refused: it cannot take an approval generation")
            return False
        try:
            return bool(send(call_id, secret, mode, approval_generation=approval_generation))
        except Exception as exc:
            LOG.warning("otp call sink failed: %s", type(exc).__name__)
            return False


def _accepts_generation(fn: Callable[..., Any]) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "approval_generation" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


# ---------------------------------------------------------------------------- delivery

@dataclass
class _Slot:
    secret: SecretText
    expires_at: float
    fills_left: int
    sender: str
    generation: int      # the approval generation the code was released under
    binding: str         # canonical sender/window/destination binding of that approval


class OtpDelivery:
    """Sealed delivery. `otp_browser_fill` (default OFF) switches the browser target on; the call target is always on."""

    def __init__(
        self,
        service: OtpService,
        sink: Optional[Sink] = None,
        *,
        origin_probe: Optional[Callable[[Optional[str]], Optional[str]]] = None,
        call_sink: Optional[CallSink] = None,
        counterpart_of: Optional[Callable[[str], Optional[str]]] = None,
        otp_browser_fill: bool = False,
        slot_ttl_s: float = SLOT_TTL_S,
        slot_max_fills: int = SLOT_MAX_FILLS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.service = service
        self.sink = sink
        self.origin_probe = origin_probe
        self.call_sink = call_sink if call_sink is not None else CallSink()
        self.counterpart_of = counterpart_of   # optional: live counterpart of a call_id, supplied by L1
        self.otp_browser_fill = bool(otp_browser_fill)
        self._ttl = slot_ttl_s
        self._max_fills = slot_max_fills
        self._clock = clock
        self._lock = threading.Lock()
        self._slots: Dict[str, _Slot] = {}
        # a replaced or revoked approval drops its sealed code at once (H1)
        service.registry.on_change(self._drop)

    # -------------------------------------------------------------- fill (browser)

    def fill(
        self,
        task_id: str,
        consent_token: Optional[str],
        consent_store: ConsentStoreLike,
        targets: Sequence[str],
        *,
        timeout_s: float = 0.0,
        browser_task_id: Optional[str] = None,
        approval_generation: Optional[int] = None,
    ) -> DeliveryResult:
        if not self.otp_browser_fill or self.sink is None:
            return self._r(FillStatus.DISABLED.value, task_id if isinstance(task_id, str) else "")
        targets = [t for t in (targets or []) if isinstance(t, str) and t.strip()] if isinstance(targets, (list, tuple)) else []
        if not isinstance(task_id, str) or not task_id or not targets or len(targets) > MAX_TARGETS:
            return self._r(FillStatus.BAD_REQUEST.value, task_id if isinstance(task_id, str) else "")

        approval = self.service.registry.get_entry(task_id)
        if approval is None:
            return self._r(ReleaseStatus.NO_TASK.value, task_id)
        bad = self._check_fill(approval, browser_task_id, first=True)
        if bad is not None:
            return self._r(bad, task_id)

        slot, res = self._slot_or_release(approval, consent_token, consent_store, timeout_s, approval_generation)
        if slot is None:
            return res
        n = len(slot.secret)
        if len(targets) not in (1, n):
            return self._r(FillStatus.TARGET_MISMATCH.value, task_id, sender=slot.sender)

        # Validation and delivery are one step against replacement of the approval (registry gate).
        with self.service.registry.gate:
            status = self._revalidate(task_id, slot, approval_generation)
            if status is not None:
                return self._r(status, task_id)
            cur = self.service.registry.get_entry(task_id)
            bad = self._check_fill(cur, browser_task_id, first=False)   # re-probe immediately before typing
            if bad is not None:
                return self._r(bad, task_id, sender=slot.sender)
            if not self._spend_attempt(task_id, slot):
                return self._r(FillStatus.SINK_FAILED.value, task_id, sender=slot.sender)
            outcome = self._type(slot.secret, targets, browser_task_id, cur.task.destination.origin)
            if outcome == "origin_changed":
                self._drop(task_id)   # the code may have reached another page: never retry from this slot
                return self._r(FillStatus.ORIGIN_CHANGED.value, task_id, sender=slot.sender)
            if outcome != "ok":
                return self._r(FillStatus.SINK_FAILED.value, task_id, sender=slot.sender)
            self._drop(task_id)   # delivered: the sealed copy is dropped at once
            return self._r(FillStatus.FILLED.value, task_id, filled=len(targets), sender=slot.sender)

    def _check_fill(self, approval: Approval, browser_task_id: Optional[str], *, first: bool) -> Optional[str]:
        """Destination kind and open-page origin against the approval. `first=False` is the pre-typing re-probe."""
        dest = approval.task.destination
        if dest is None or dest.kind != FILL:
            return FillStatus.WRONG_DESTINATION.value
        current = self.origin_probe(browser_task_id) if self.origin_probe is not None else None
        if current is None:
            return FillStatus.ORIGIN_UNKNOWN.value if first else FillStatus.ORIGIN_CHANGED.value
        if current != dest.origin:
            LOG.warning("otp task=%s origin %s: open page is %s, approved %s", approval.task.task_id,
                        "mismatch" if first else "changed", current, dest.origin)
            return FillStatus.ORIGIN_MISMATCH.value if first else FillStatus.ORIGIN_CHANGED.value
        return None

    def _type(self, secret: SecretText, targets: Sequence[str], browser_task_id: Optional[str], origin: str) -> str:
        """Type the code; the top-level origin is re-read right after (and between boxes). 'ok' | 'failed' | 'origin_changed'."""
        assert self.sink is not None
        if len(targets) == 1:
            pieces = [(secret, targets[0])]
        else:
            # one field per character, sliced through SecretText so no plain str of the whole code is built
            pieces = [(secret[i], t) for i, t in enumerate(targets)]
        for i, (part, target) in enumerate(pieces):
            if i > 0 and self.origin_probe(browser_task_id) != origin:
                return "origin_changed"
            if not self.sink.fill(part, target, task_id=browser_task_id):
                return "failed"
        return "ok" if self.origin_probe(browser_task_id) == origin else "origin_changed"

    # -------------------------------------------------------------- to_call

    def to_call(
        self,
        task_id: str,
        call_id: str,
        mode: str,
        consent_token: Optional[str],
        consent_store: ConsentStoreLike,
        *,
        timeout_s: float = 0.0,
        approval_generation: Optional[int] = None,
    ) -> DeliveryResult:
        if not isinstance(task_id, str) or not task_id or not isinstance(call_id, str) or mode not in CALL_MODES:
            return self._r(FillStatus.BAD_REQUEST.value, task_id if isinstance(task_id, str) else "")
        approval = self.service.registry.get_entry(task_id)
        if approval is None:
            return self._r(ReleaseStatus.NO_TASK.value, task_id)
        bad = self._check_call(approval, call_id)
        if bad is not None:
            return self._r(bad, task_id)
        if not self.call_sink.accepts_generation():
            return self._r(FillStatus.SINK_UNSUPPORTED.value, task_id)   # before the phone is read or consent spent

        slot, res = self._slot_or_release(approval, consent_token, consent_store, timeout_s, approval_generation)
        if slot is None:
            return res
        if mode == "dtmf" and not slot.secret.fullmatch(r"[0-9]{1,16}"):
            return self._r(FillStatus.MODE_UNSUPPORTED.value, task_id, sender=slot.sender)

        # The registry gate is a blocking threading lock: it must not be held across a call sink, which
        # waits for the service event loop, and that loop can itself be waiting for the gate (R1-delta N1).
        # Validate under the gate, release it, and hand the sink the approval generation: L1 re-checks it on
        # the loop right before the frame is queued, so a replacement in between cannot be followed by a send.
        with self.service.registry.gate:
            status = self._revalidate(task_id, slot, approval_generation)
            if status is not None:
                return self._r(status, task_id)
            bad = self._check_call(self.service.registry.get_entry(task_id), call_id)
            if bad is not None:
                return self._r(bad, task_id, sender=slot.sender)
            if not self._spend_attempt(task_id, slot):
                return self._r(FillStatus.SINK_FAILED.value, task_id, sender=slot.sender)
            generation = slot.generation
        if not self.call_sink.send(call_id, slot.secret, mode, approval_generation=generation):
            return self._r(FillStatus.SINK_FAILED.value, task_id, sender=slot.sender)
        self._drop(task_id, slot)
        return self._r(FillStatus.SENT.value, task_id, sender=slot.sender)

    def _check_call(self, approval: Approval, call_id: str) -> Optional[str]:
        dest = approval.task.destination
        if dest is None or dest.kind != CALL or dest.call_id != call_id:
            return FillStatus.WRONG_DESTINATION.value
        if self.counterpart_of is not None:
            # L1 can tell us who is really on the line; it must be the party the owner saw in the prompt
            try:
                live = self.counterpart_of(call_id)
            except Exception:
                live = None
            if live != dest.counterpart:
                return FillStatus.WRONG_DESTINATION.value
        return None

    # -------------------------------------------------------------- shared

    def _slot_or_release(self, approval, consent_token, consent_store, timeout_s, approval_generation):
        """The sealed slot for this approval, releasing a code (and spending consent) if there is none.

        A slot only serves the approval generation and binding it was released under; anything else is dropped.
        """
        task_id = approval.task.task_id
        with self._lock:
            self._expire()
            slot = self._slots.get(task_id)
            if slot is not None and (slot.generation != approval.generation or slot.binding != approval.binding):
                del self._slots[task_id]
                slot = None
        if slot is not None:
            if approval_generation is not None and approval_generation != slot.generation:
                return None, self._r(ReleaseStatus.APPROVAL_CHANGED.value, task_id)
            return slot, None
        res: ReleaseResult = self.service.attempt(task_id, consent_token, consent_store, timeout_s=timeout_s,
                                                  approval_generation=approval_generation)
        if res.status is not ReleaseStatus.RELEASED or res.secret is None or res.approval is None:
            return None, self._r(res.status.value, task_id, sender=res.sender)
        with self._lock:
            slot = _Slot(res.secret, self._clock() + self._ttl, self._max_fills, res.sender,
                         res.approval.generation, res.approval.binding)
            self._slots[task_id] = slot
        return slot, None

    def _revalidate(self, task_id: str, slot: _Slot, approval_generation: Optional[int]) -> Optional[str]:
        """Under the registry gate: the slot must still belong to the registered approval. Drops it otherwise."""
        cur = self.service.registry.get_entry(task_id)
        if (cur is None or cur.generation != slot.generation or cur.binding != slot.binding
                or (approval_generation is not None and approval_generation != slot.generation)):
            self._drop(task_id)
            return ReleaseStatus.APPROVAL_CHANGED.value if cur is not None else ReleaseStatus.NO_TASK.value
        return None

    def _spend_attempt(self, task_id: str, slot: _Slot) -> bool:
        with self._lock:
            if slot.fills_left <= 0 or slot.expires_at < self._clock():
                self._slots.pop(task_id, None)
                return False
            slot.fills_left -= 1
            return True

    def _drop(self, task_id: str, only: Optional[_Slot] = None) -> None:
        """Drop the task's slot (only that very slot when `only` is given: a newer approval's slot stays)."""
        with self._lock:
            if only is None or self._slots.get(task_id) is only:
                self._slots.pop(task_id, None)

    def _expire(self) -> None:
        now = self._clock()
        for k in [k for k, s in self._slots.items() if s.expires_at < now or s.fills_left <= 0]:
            del self._slots[k]

    def held(self, task_id: str) -> bool:
        with self._lock:
            self._expire()
            return task_id in self._slots

    @staticmethod
    def _r(status: str, task_id: str, *, filled: int = 0, sender: str = "") -> DeliveryResult:
        res = DeliveryResult(status, task_id, filled, sender, _HINTS.get(status, ""))
        LOG.info("otp delivery task=%s status=%s fields=%d", task_id, status, filled)
        return res
