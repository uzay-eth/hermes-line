"""Function tools the voice model may call, and the router that runs them.

Hermes stays the brain. The voice model only gets narrow tools; each one is a request
to the Hermes call session (L1's ``CallSession``) through a ``CallPort``, which applies
exactly the rules the text path applies today:

* ``ask_hermes``: one Hermes turn on the call's own Hermes session, which runs in the call
  profile: no memory, user profile, persona or tools of the owner's; it knows the request the
  owner approved and the call so far. The answer comes back as text.
* ``bring_owner``: ring the owner. Only the owner's earlier approval (L1 ``GrantBook``)
  and the destination gate (L2) can make that happen; the model cannot approve.
* ``press_keys``: DTMF on the live call, validated, one press per prompt (L1).
* ``report_outcome`` / ``end_call``: record the result, then hang up after the goodbye.
* ``send_verification_code``: sealed OTP (L4 + L1). The model never receives the code;
  the result only says whether ``[verification code sent]``.
* ``wait_on_hold``: the person put the call on hold or transferred it; the voice session
  closes and the cheap hold path takes over again.

Arguments are untrusted model output: they are parsed and checked here before the port is
touched, a rejected call has no side effect, and results are masked before the model
hears them.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Callable, Deque, Dict, Optional, Protocol, Tuple

from collections import deque

from .provider import ToolCall, ToolSpec

OUTCOMES = ("done", "callback_expected", "failed")
DIGITS = re.compile(r"^[0-9*#w]{1,32}$")


class CallPort(Protocol):
    """What the voice path needs from L1's call session (see docs/lanes/L5.md, "L1 integration points")."""

    call_id: str

    def offer(self, frame: Dict[str, Any]) -> bool: ...
    def record(self, speaker: str, text: str) -> None: ...
    def voice_event(self, kind: str, **data: Any) -> None: ...
    async def ask_hermes(self, question: str, timeout_s: float) -> Optional[str]: ...
    async def bring_owner(self) -> Dict[str, Any]: ...
    async def press_digits(self, digits: str) -> Dict[str, Any]: ...
    async def report_outcome(self, outcome: str, summary: str) -> Dict[str, Any]: ...
    async def end_call(self) -> Dict[str, Any]: ...
    async def send_verification_code(self, mode: str) -> Dict[str, Any]: ...
    async def hold_resumed(self) -> Dict[str, Any]: ...
    async def voice_failed(self, reason: str) -> None: ...
    # Optional (DTMF fallback): REST-redirect the live leg to the plugin's digits TwiML URL for ``nonce``.
    # async def redirect_digits(self, nonce: str) -> Dict[str, Any]: ...


def _obj(props: Dict[str, Any], required: Tuple[str, ...] = ()) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


TOOL_SPECS = (
    ToolSpec("ask_hermes",
             "Ask Hermes, who holds the request the owner approved for this call, for a detail of the task or a "
             "decision (e.g. 'Which time did the owner ask for?'). Hermes has that request and the call so far, "
             "not the owner's memory, calendar or files. Before calling it, tell the person briefly that you "
             "are checking. Use its answer; never invent the owner's details.",
             _obj({"question": {"type": "string", "description": "The question, with the context Hermes needs."}},
                  ("question",))),
    ToolSpec("bring_owner",
             "Ring the owner to join this call, when the person needs the owner personally. Only works if the "
             "owner allowed it for this call.", _obj({})),
    ToolSpec("press_keys", "Press phone keys (0-9, *, #; w = half-second pause) on an automated menu.",
             _obj({"digits": {"type": "string"}}, ("digits",))),
    ToolSpec("report_outcome",
             "Record the result of the task: done, callback_expected or failed, with a one-sentence summary "
             "including any confirmed details (date, time, reference number).",
             _obj({"outcome": {"type": "string", "enum": list(OUTCOMES)}, "summary": {"type": "string"}},
                  ("outcome", "summary"))),
    ToolSpec("end_call", "End the call. Call report_outcome first, then say a brief goodbye.", _obj({})),
    ToolSpec("send_verification_code",
             "Send the verification code the owner approved for this call, as key presses (dtmf) or read aloud "
             "(speak). You never see the code.",
             _obj({"mode": {"type": "string", "enum": ["dtmf", "speak"]}}, ("mode",))),
    ToolSpec("wait_on_hold", "The person put you on hold or is transferring you. Stop talking and wait.", _obj({})),
)
TOOL_NAMES = frozenset(t.name for t in TOOL_SPECS)


def _err(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}


class ToolRouter:
    """Validates and runs one tool call at a time per tool, with a per-minute budget."""

    def __init__(self, port: CallPort, *, mask: Callable[[str], str] = lambda s: s,
                 ask_timeout_s: float = 10.0, max_calls_per_minute: int = 20,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.port = port
        self.mask = mask
        self.ask_timeout_s = ask_timeout_s
        self.max_calls_per_minute = max_calls_per_minute
        self._clock = clock
        self._recent: Deque[float] = deque()
        self._busy: set = set()
        self.calls = 0
        self.rejected = 0
        self.end_requested = False
        self.hold_requested = False

    def _budget_ok(self) -> bool:
        now = self._clock()
        while self._recent and now - self._recent[0] > 60.0:
            self._recent.popleft()
        if len(self._recent) >= self.max_calls_per_minute:
            return False
        self._recent.append(now)
        return True

    async def run(self, call: ToolCall, *, allowed: bool) -> str:
        """Run ``call``; returns the JSON text that goes back to the model."""
        self.calls += 1
        result = await self._run(call, allowed)
        if not result.get("ok"):
            self.rejected += 1
        self.port.voice_event("voice_tool", name=call.name if call.name in TOOL_NAMES else "unknown",
                              ok=bool(result.get("ok")))
        return json.dumps({k: (self.mask(v) if isinstance(v, str) else v) for k, v in result.items()},
                          separators=(",", ":"))

    async def _run(self, call: ToolCall, allowed: bool) -> Dict[str, Any]:
        if call.name not in TOOL_NAMES:
            return _err("unknown tool")
        if not allowed:
            return _err("tools are only available while you are talking with a person")
        try:
            args = json.loads(call.arguments or "{}")
        except ValueError:
            return _err("arguments must be a JSON object")
        if not isinstance(args, dict):
            return _err("arguments must be a JSON object")
        if not self._budget_ok():
            return _err("too many tool calls; continue the conversation")
        if call.name in self._busy:
            return _err(f"{call.name} is already running")
        self._busy.add(call.name)
        try:
            return await getattr(self, "_t_" + call.name)(args)
        except Exception as exc:  # a port failure is a failed tool, never a crashed call
            return _err(f"{call.name} failed ({type(exc).__name__})")
        finally:
            self._busy.discard(call.name)

    @staticmethod
    def _only(args: Dict[str, Any], *names: str) -> Optional[Dict[str, Any]]:
        if set(args) - set(names):
            return _err("unexpected arguments")
        return None

    async def _t_ask_hermes(self, args):
        bad = self._only(args, "question")
        q = args.get("question")
        if bad or not isinstance(q, str) or not q.strip():
            return bad or _err("question must be a non-empty string")
        q = q.strip()[:1000]
        try:
            answer = await asyncio.wait_for(self.port.ask_hermes(q, self.ask_timeout_s), self.ask_timeout_s + 1.0)
        except asyncio.TimeoutError:
            return _err("Hermes did not answer in time. Tell the person you will confirm later.")
        if not answer or not str(answer).strip():
            return _err("Hermes has no answer right now. Tell the person you will confirm later.")
        return {"ok": True, "answer": str(answer).strip()[:1500]}

    async def _t_bring_owner(self, args):
        bad = self._only(args)
        if bad:
            return bad
        out = await self.port.bring_owner()
        if out.get("ok"):
            return {"ok": True, "note": "The owner is being rung. Ask the person to hold for a moment."}
        return _err(str(out.get("error") or "the owner cannot be rung on this call"))

    async def _t_press_keys(self, args):
        bad = self._only(args, "digits")
        digits = args.get("digits")
        if bad or not isinstance(digits, str) or not DIGITS.match(digits):
            return bad or _err("digits must be 0-9, *, # or w (max 32)")
        return await self.port.press_digits(digits)

    async def _t_report_outcome(self, args):
        bad = self._only(args, "outcome", "summary")
        outcome, summary = args.get("outcome"), args.get("summary", "")
        if bad or outcome not in OUTCOMES or not isinstance(summary, str):
            return bad or _err("outcome must be done, callback_expected or failed; summary a string")
        return await self.port.report_outcome(outcome, summary.strip()[:1000])

    async def _t_end_call(self, args):
        bad = self._only(args)
        if bad:
            return bad
        self.end_requested = True
        return {"ok": True, "note": "Say a brief goodbye now; the call ends after it."}

    async def _t_send_verification_code(self, args):
        bad = self._only(args, "mode")
        mode = args.get("mode")
        if bad or mode not in ("dtmf", "speak"):
            return bad or _err("mode must be dtmf or speak")
        out = await self.port.send_verification_code(mode)
        if out.get("ok"):
            return {"ok": True, "status": "[verification code sent]"}
        return _err(str(out.get("error") or "no approved code for this call"))

    async def _t_wait_on_hold(self, args):
        bad = self._only(args)
        if bad:
            return bad
        self.hold_requested = True
        return {"ok": True}
