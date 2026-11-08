"""Hermes wiring for OTP release: the sealed tools and the two scrubbing hooks.

Everything here uses public plugin surfaces only (`ctx.register_tool`, `ctx.register_hook`,
`ctx.dispatch_tool`); nothing patches Hermes. The engine (L1) calls `register(ctx, ...)` once.

Tools (the model never receives the code; see delivery.py)
    line_otp_fill     types the approved SMS code into browser field(s) on the approved site
    line_otp_to_call  hands the approved SMS code to the approved live call (DTMF or speech)

Hooks (fail-open in Hermes, so they only ever add masking)
    transform_tool_result  replaces the exact value of any released code in ANY tool result with
                           `[CODE]` before the result enters context and is persisted. This catches
                           a page snapshot or a terminal echo that contains the typed code.
    transform_llm_output   same for the model's final reply, before the assistant row is written.

The consent token is never a tool argument. `consent_lookup(task_id)` returns the single-use
token the owner's approval produced (the engine keeps it); the model cannot supply or forge one.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from .consent import ConsentStoreLike
from .delivery import CALL_MODES, DeliveryResult, FillStatus, OtpDelivery
from .log import LOG
from .scrub import DEFAULT as DEFAULT_SCRUBBER
from .scrub import ValueScrubber

FILL_TOOL = "line_otp_fill"
CALL_TOOL = "line_otp_to_call"
MAX_WAIT_S = 60.0

FILL_SCHEMA: Dict[str, Any] = {
    "name": FILL_TOOL,
    "description": (
        "Type the verification code from the owner's approved SMS into the code field(s) of the "
        "page open in the browser. Use it only for a task the owner approved for SMS codes, and only "
        "while the approved site is open. You never see the code: this tool reads it and types it for "
        "you. Pass one field reference (the whole code goes there) or one reference per character, in "
        "order, when the page has separate boxes. If the SMS has not arrived yet the status says so: "
        "wait a few seconds and call again. If typing fails you may call again with other references; "
        "no new approval is needed for that."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The Hermes Line task the owner approved SMS codes for."},
            "targets": {
                "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12,
                "description": "Browser field references from the page snapshot (e.g. '@e7'); one, or one per character.",
            },
            "wait_s": {"type": "number", "minimum": 0, "maximum": MAX_WAIT_S,
                       "description": "Seconds to wait for the SMS to arrive (default 0)."},
        },
        "required": ["task_id", "targets"],
    },
}

CALL_SCHEMA: Dict[str, Any] = {
    "name": CALL_TOOL,
    "description": (
        "Give the verification code from the owner's approved SMS to the live call the owner approved "
        "(for example when a representative asks for the code you were sent). Use mode dtmf to key the "
        "digits, or speak to say them. You never see the code: this tool reads it and delivers it. "
        "If the SMS has not arrived yet the status says so: wait and call again."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The Hermes Line task the owner approved SMS codes for."},
            "call_id": {"type": "string", "description": "The live call the owner approved the code for."},
            "mode": {"type": "string", "enum": list(CALL_MODES), "description": "dtmf: key the digits; speak: say the code."},
            "wait_s": {"type": "number", "minimum": 0, "maximum": MAX_WAIT_S,
                       "description": "Seconds to wait for the SMS to arrive (default 0)."},
        },
        "required": ["task_id", "call_id", "mode"],
    },
}

ConsentLookup = Callable[[str], Optional[str]]
GenerationLookup = Callable[[str], Optional[int]]


def _wait(args: Dict[str, Any]) -> float:
    try:
        return max(0.0, min(float(args.get("wait_s") or 0.0), MAX_WAIT_S))
    except (TypeError, ValueError):
        return 0.0


def _error(task_id: str) -> str:
    return DeliveryResult(FillStatus.BAD_REQUEST.value, task_id or "", hint="Give task_id and targets (a list of field references).").to_json()


def _generation(generation_lookup: Optional[GenerationLookup], task_id: str) -> Optional[int]:
    """The approval generation of the engine's pending approval; None when no lookup is wired.

    With a lookup wired, an unknown task yields -1, which matches no generation: the release is refused (M1).
    """
    if generation_lookup is None:
        return None
    g = generation_lookup(task_id)
    return g if isinstance(g, int) and not isinstance(g, bool) else -1


def make_fill_handler(
    delivery: OtpDelivery, consent_store: ConsentStoreLike, consent_lookup: ConsentLookup,
    generation_lookup: Optional[GenerationLookup] = None,
) -> Callable[..., str]:
    def handler(args: Dict[str, Any], **kw: Any) -> str:
        try:
            task_id = args.get("task_id")
            targets = args.get("targets")
            if isinstance(targets, str):
                targets = [targets]
            if not isinstance(task_id, str) or not isinstance(targets, list):
                return _error(str(task_id)[:80] if isinstance(task_id, str) else "")
            res = delivery.fill(
                task_id, consent_lookup(task_id), consent_store, targets,
                timeout_s=_wait(args), browser_task_id=kw.get("task_id"),
                approval_generation=_generation(generation_lookup, task_id),
            )
            return res.to_json()
        except Exception as exc:   # never let an exception message (it may carry arguments) reach the model
            LOG.warning("otp fill tool error: %s", type(exc).__name__)
            return json.dumps({"status": "error", "hint": "SMS code release failed. Tell the owner."})

    return handler


def make_call_handler(
    delivery: OtpDelivery, consent_store: ConsentStoreLike, consent_lookup: ConsentLookup,
    generation_lookup: Optional[GenerationLookup] = None,
) -> Callable[..., str]:
    def handler(args: Dict[str, Any], **kw: Any) -> str:
        try:
            task_id, call_id, mode = args.get("task_id"), args.get("call_id"), args.get("mode")
            if not isinstance(task_id, str) or not isinstance(call_id, str) or mode not in CALL_MODES:
                return _error(task_id[:80] if isinstance(task_id, str) else "")
            res = delivery.to_call(task_id, call_id, mode, consent_lookup(task_id), consent_store, timeout_s=_wait(args),
                                   approval_generation=_generation(generation_lookup, task_id))
            return res.to_json()
        except Exception as exc:
            LOG.warning("otp call tool error: %s", type(exc).__name__)
            return json.dumps({"status": "error", "hint": "SMS code release failed. Tell the owner."})

    return handler


def make_tool_result_hook(scrubber: Optional[ValueScrubber] = None) -> Callable[..., Optional[str]]:
    sc = scrubber or DEFAULT_SCRUBBER

    def transform_tool_result(**kw: Any) -> Optional[str]:
        result = kw.get("result")
        if not isinstance(result, str) or not result:
            return None
        out = sc.scrub(result)
        return out if out != result else None

    return transform_tool_result


def make_llm_output_hook(scrubber: Optional[ValueScrubber] = None) -> Callable[..., Optional[str]]:
    sc = scrubber or DEFAULT_SCRUBBER

    def transform_llm_output(**kw: Any) -> Optional[str]:
        text = kw.get("response_text")
        if not isinstance(text, str) or not text:
            return None
        out = sc.scrub(text)
        return out if out != text else None

    return transform_llm_output


def register(
    ctx: Any,
    delivery: OtpDelivery,
    consent_store: ConsentStoreLike,
    consent_lookup: ConsentLookup,
    *,
    generation_lookup: Optional[GenerationLookup] = None,
    toolset: str = "hermes-line",
    check_fn: Optional[Callable[[], bool]] = None,
    scrubber: Optional[ValueScrubber] = None,
) -> None:
    """Register the sealed tools and both scrubbing hooks on a Hermes `PluginContext`.

    `generation_lookup(task_id)` returns the approval generation of the engine's pending approval (the int
    `registry.register` returned); a release is refused when it differs from the one the code was released under.
    `line_otp_fill` is registered only when the owner switched `otp_browser_fill` on; the call tool is always on.
    """
    if delivery.otp_browser_fill:
        ctx.register_tool(
            name=FILL_TOOL, toolset=toolset, schema=FILL_SCHEMA,
            handler=make_fill_handler(delivery, consent_store, consent_lookup, generation_lookup),
            check_fn=check_fn, description="Type the approved SMS verification code into a page field", emoji="🔐",
        )
    ctx.register_tool(
        name=CALL_TOOL, toolset=toolset, schema=CALL_SCHEMA,
        handler=make_call_handler(delivery, consent_store, consent_lookup, generation_lookup),
        check_fn=check_fn, description="Give the approved SMS verification code to the live call", emoji="🔐",
    )
    ctx.register_hook("transform_tool_result", make_tool_result_hook(scrubber))
    ctx.register_hook("transform_llm_output", make_llm_output_hook(scrubber))
