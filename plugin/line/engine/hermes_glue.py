"""Hermes plugin surface: tools, the /line command, the service host and Desktop events.

Nothing here touches disk or the network during ``register``: the store and
service are built on first use, and the call service starts only when the
profile sets ``enabled: true`` (then in a background thread, so ``register``
returns at once). A process that loses the line lease stays in standby and
retries, so a long-lived gateway takes the line over when a short-lived CLI
that held it exits.
"""
from __future__ import annotations

import atexit
import json
import threading
from dataclasses import fields
from pathlib import Path
from typing import Any, Optional

from .config import EngineConfig
from .call_tools import IN_CALL_TOOLS, call_args as _call_args
from .contracts import GateRejected, Origin

PLUGIN_ID = "hermes-line"

TOOLSET = "hermes_line"
SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "hermes-line"
STANDBY_RETRY_S = 15.0


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


CALL_ID = {"type": "string", "description": "Call id from hermes_line_call_status."}
TOOLS: dict[str, dict] = {
    "hermes_line_request_call": {
        "description": ("Ask to phone a business for the owner. Creates a task that waits for the owner's approval "
                        "in chat (/line approve <task_id>); nothing is dialled until the owner approves. Only "
                        "numbers on the owner's allowlist can be called; emergency and service numbers never."),
        "parameters": _obj({
            "destination": {"type": "string", "description": "E.164 number, e.g. +17755550123."},
            "title": {"type": "string", "description": "Short task title, e.g. 'Book a table for 4 at 7pm'."},
            "goal": {"type": "string", "description": "What to achieve on the call and any details to share."},
            "mode": {"type": "string", "enum": ["agent", "owner"],
                     "description": "agent: Hermes completes the task. owner: bridge the owner in when a person answers."},
            "allow_agent_fallback": {"type": "boolean", "description": "In owner mode, continue alone if the owner does not pick up."},
            "leave_voicemail": {"type": "boolean", "description": "Leave a short voicemail if voicemail answers."},
            "owner_ring_timeout_s": {"type": "integer", "minimum": 10, "maximum": 60,
                                     "description": "Owner mode: seconds the owner has to press 1 once rung."},
            "intro_names": {"type": "array", "items": {"type": "string"}, "maxItems": 4, "description": (
                "The business's own name as its phone menu greets callers (for example \"Delta Air Lines\"). A "
                "restricted task's menu greeting may name only these; default none.")},
            "ivr_category": {"type": "string", "enum": ["baggage", "reservations", "general information"],
                             "description": "For an automated phone assistant: the line may say \"<category> "
                                            "question.\" to route the call."},
            "fallback_question": {"type": "string", "description": (
                "Owner mode: if the owner does not press 1 in time, the line asks the person this one general "
                "question (no digits, identity, booking, callback, payment or change), thanks them and hangs up.")},
        }, ["destination", "title", "goal", "mode"]),
    },
    "hermes_line_call_status": {
        "description": "Show live and recent Hermes Line calls (state, masked transcript tail, summary).",
        "parameters": _obj({"call_id": CALL_ID}, []),
    },
    "hermes_line_list_tasks": {
        "description": "List open Hermes Line tasks (awaiting approval, calling, awaiting a callback).",
        "parameters": _obj({}, []),
    },
    "hermes_line_say": {
        "description": "Have Hermes say a sentence on a live call right now (owner's 'Say this').",
        "parameters": _obj({"call_id": CALL_ID, "text": {"type": "string"}}, ["call_id", "text"]),
    },
    "hermes_line_hang_up": {
        "description": "Hang up a live Hermes Line call (all legs).",
        "parameters": _obj({"call_id": CALL_ID}, ["call_id"]),
    },
    "hermes_line_take_over": {
        "description": ("Ring the owner's phone and bridge them into a live call. Uses the bridge approval the owner "
                        "gave with the call; if it was already used, ask the owner to press Take over in the pane."),
        "parameters": _obj({"call_id": CALL_ID}, ["call_id"]),
    },
    **IN_CALL_TOOLS,  # shared with the session MCP server (round 6): one definition, two routes
}


class _Host:
    """Per-process holder for the store and service."""

    def __init__(self, ctx: Any):
        self.ctx = ctx
        self.lock = threading.RLock()
        self.service = None
        self.retry: Optional[threading.Timer] = None
        self.stopped = False
        self.handles: dict[str, Any] = {}
        self.otp = None
        self.otp_delivery = None
        self.pending_codes: dict[str, tuple[str, dict]] = {}

    # -- construction -------------------------------------------------------
    def config(self) -> EngineConfig:
        values = {}
        for f in fields(EngineConfig):
            value = self.ctx.get_config(f.name, None)
            if value is not None:
                values[f.name] = value
        return EngineConfig.load(values)

    def get_secret(self, name: str) -> Optional[str]:
        """None when the secret is not set. A secret that exists but cannot be read (e.g. no profile
        scope under multiplexing) raises, so callers fail closed instead of treating it as absent."""
        try:
            from agent.secret_scope import get_secret
        except Exception:
            return None  # no Hermes in this process
        try:
            return get_secret(name)
        except Exception:
            raise RuntimeError(f"{name} could not be read") from None

    def get(self):
        with self.lock:
            if self.service is None:
                from plugins.plugin_storage import plugin_data_dir

                from ..store import Store
                from .service import CallService
                from .telephony import TwilioRestAdapter

                cfg = self.config()
                data = plugin_data_dir(PLUGIN_ID)
                from .acp_driver import AcpDriver  # the public `hermes acp` protocol (ADR 0002 gate)
                from .acp_driver import default_command
                profile = cfg.call_profile
                make_driver = lambda sid: AcpDriver(sid, command=default_command(profile))  # noqa: E731
                self.service = CallService(
                    cfg=cfg, store=Store(data / "line.db"), adapter=TwilioRestAdapter(self.get_secret),
                    driver_factory=make_driver, lock_dir=data / "locks",
                    get_secret=self.get_secret, summary_sink=self.deliver, notifier=self.broadcast,
                    otp=self.otp)
                self.service.otp_delivery = self.otp_delivery  # sealed call target for the voice path (L5)
            return self.service

    # -- service lifecycle -------------------------------------------------------
    def start(self) -> str:
        from .lease import LeaseHeld
        with self.lock:
            if self.stopped:
                return "stopped"
            svc = self.get()
            try:
                svc.start()
            except LeaseHeld:
                self._schedule_retry()
            except Exception:
                pass
            return svc.status

    def _schedule_retry(self) -> None:
        if self.retry is None and not self.stopped:
            def again() -> None:
                self.retry = None
                self.start()
            self.retry = threading.Timer(STANDBY_RETRY_S, again)
            self.retry.daemon = True
            self.retry.start()

    def stop(self) -> None:
        with self.lock:
            self.stopped = True
            if self.retry is not None:
                self.retry.cancel()
                self.retry = None
            if self.service is not None:
                try:
                    self.service.stop()
                except Exception:
                    pass

    # -- delivery ---------------------------------------------------------------
    def broadcast(self, call_id: str, revision: int) -> None:
        try:
            from hermes_cli.plugin_events import broadcast_plugin_event
            broadcast_plugin_event(PLUGIN_ID, "call.updated", {"id": call_id, "revision": int(revision)})
        except Exception:
            pass

    def deliver(self, task, row: dict) -> tuple[bool, str]:
        """Post the masked summary to the chat that asked for the call."""
        text = row["text"]
        handle = self.handles.get(task.id) if task is not None else None
        if handle is not None:
            try:
                result = handle.edit(text)
                if (result or {}).get("ok"):
                    return True, ""
            except Exception:
                pass
        if task is not None and task.origin.session_key:
            try:
                if self.ctx.inject_message(f"[Hermes Line call summary] {text}", session_key=task.origin.session_key):
                    return True, ""
                return False, "injection_refused"
            except Exception as exc:
                return False, type(exc).__name__
        # Round 13: a task the owner asked for by phone has no chat origin; its summary goes to the owner's chat.
        try:
            handle = self.ctx.post_message(f"[Hermes Line call summary] {text}")
            if getattr(handle, "ok", False):
                return True, ""
        except Exception as exc:
            return False, type(exc).__name__
        return False, "no_route"


def _json(data: Any) -> str:
    return json.dumps(data, default=str)


def _call_brief(view: dict, tail: int = 6) -> dict:
    return {k: view[k] for k in ("id", "task_id", "task_title", "direction", "counterpart_masked", "state", "mode",
                                 "duration_s", "summary", "end_reason")} | {"transcript_tail": view["transcript"][-tail:]}


def register(ctx: Any) -> None:
    host = _Host(ctx)

    def svc():
        return host.get()

    def request_call(args: dict, **kw) -> str:
        try:
            origin = Origin(profile=str(getattr(ctx, "profile_name", "") or ""),
                            session_id=str(kw.get("session_id") or ""), session_key=str(kw.get("session_key") or ""),
                            platform=_surface(ctx))
            out = svc().request_call(destination=str(args.get("destination", "")), title=str(args.get("title", "")),
                                     goal=str(args.get("goal", "")), mode=str(args.get("mode", "agent")),
                                     allow_agent_fallback=bool(args.get("allow_agent_fallback", True)),
                                     leave_voicemail=bool(args.get("leave_voicemail", False)), origin=origin,
                                     owner_ring_timeout_s=args.get("owner_ring_timeout_s"),
                                     fallback_question=args.get("fallback_question"),
                                     intro_names=args.get("intro_names"),
                                     ivr_category=args.get("ivr_category"))
        except GateRejected as exc:
            return _json({"ok": False, "blocked_by": exc.gate, "reason": exc.reason})
        try:
            extra = "".join(f" {out[k]}" for k in ("fallback", "intro_note") if out.get(k))
            handle = ctx.post_message(f"Hermes Line: call to {out['counterpart']} for \"{args.get('title', '')}\" is "
                                      f"waiting for your approval.{extra} Approve it with {out['approval']}.")
            if getattr(handle, "ok", False):
                host.handles[out["task_id"]] = handle
        except Exception:
            pass
        return _json({"ok": True, **out, "note": "Waiting for the owner's approval; you cannot approve it yourself."})

    def call_status(args: dict, **kw) -> str:
        from ..server.desktop_api import call_view, snapshot
        store = svc().store
        if args.get("call_id"):
            call = store.get_call(str(args["call_id"]))
            return _json({"ok": bool(call), "call": _call_brief(call_view(store, call)) if call else None})
        snap = snapshot(store, limit=10)
        return _json({"ok": True, "service": svc().describe(), "calls": [_call_brief(v, 3) for v in snap["calls"]]})

    def list_tasks(args: dict, **kw) -> str:
        tasks = svc().store.list_tasks(open_only=True)
        return _json({"ok": True, "tasks": [{"id": t.id, "title": t.title, "status": t.status.value,
                                             "mode": t.mode.value, "counterpart": t.destination_masked,
                                             "outcome": t.outcome} for t in tasks]})

    def owner_action(action: str):
        def handler(args: dict, **kw) -> str:
            payload = {"text": str(args.get("text", ""))} if action == "say" else None
            call = svc().store.get_call(str(args.get("call_id", "")))
            if call is None:
                return _json({"ok": False, "error": "unknown call"})
            return _json(svc().submit(action, source="tool", call_id=call.id, task_id=call.task_id, payload=payload))
        return handler

    def in_call(method: str):
        def handler(args: dict, **kw) -> str:
            sid = str(kw.get("session_id") or getattr(kw.get("parent_agent"), "session_id", "") or "")
            return _json(svc().in_call(sid, method, *_call_args(method, args)))
        return handler

    handlers = {
        "hermes_line_request_call": request_call,
        "hermes_line_call_status": call_status,
        "hermes_line_list_tasks": list_tasks,
        "hermes_line_say": owner_action("say"),
        "hermes_line_hang_up": owner_action("hang_up"),
        "hermes_line_take_over": owner_action("take_over"),
        "hermes_line_call_press_digits": in_call("press_digits"),
        "hermes_line_call_finish": in_call("request_finish"),
        "hermes_line_call_bring_owner": in_call("request_owner"),
        "hermes_line_call_new_task": in_call("owner_new_task"),  # round 13: refused unless the owner passed the PIN
    }
    for name, spec in TOOLS.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema={"name": name, **spec}, handler=handlers[name],
                          description=spec["description"])

    _register_otp(ctx, host)

    ctx.register_skill("hermes-line", SKILL_DIR / "SKILL.md",
                       description="When and how to use Hermes Line to phone businesses for the owner.")

    def line_command(raw_args: str, *, session_key: str = "", session_id: str = "", platform: str = "",
                     source: Any = None) -> str:
        return _line(host, raw_args or "")

    ctx.register_command("line", handler=line_command, description="Hermes Line: approve, status, hang up, take over",
                         args_hint="[approve|deny|takeover|hangup|say|allow-code|unlock-owner|service] ...")
    ctx.on_unload(host.stop)
    atexit.register(host.stop)

    try:
        enabled = ctx.get_config("enabled", False) is True
    except Exception:
        enabled = False
    if enabled:
        threading.Thread(target=host.start, name="hermes-line-start", daemon=True).start()


class _LazyReleased:
    """The persistent released-OTP set (line.store.released_otps), opened on first use."""

    def __init__(self, host: _Host):
        self._host = host
        self._set = None

    def _get(self):
        if self._set is None:
            from ..store.released_otps import ReleasedOtps, bind
            self._set = ReleasedOtps(self._host.get().store)
            bind(self._host.get().store)
        return self._set

    def contains(self, key) -> bool:
        return self._get().contains(key)

    def add(self, key) -> None:
        self._get().add(key)


class _LazyConsent:
    """The call service's ConsentStore (process-local, L2), resolved on first use."""

    def __init__(self, host: _Host):
        self._host = host

    def issue(self, *a, **kw):
        return self._host.get().consent.issue(*a, **kw)

    def consume(self, *a, **kw):
        return self._host.get().consent.consume(*a, **kw)


def _register_otp(ctx: Any, host: _Host) -> None:
    """Wire line.sms (L4) as docs/lanes/L4.md describes: sealed delivery only (docs/CONTRACT.md)."""
    from . import deps
    if deps.sms.is_stub:
        return
    from ..sms import AdbSource, CallSink, OtpDelivery, OtpService, configure
    from ..sms.hooks import register as register_otp_hooks
    from . import sealed

    try:
        cfg = host.config()
        kind, serial = cfg.sms_source, cfg.sms_adb_serial or None
    except Exception:
        kind, serial = "none", None
    if kind == "none":
        return

    def audit(payload) -> None:
        try:
            host.get().store.log_event(None, str(payload.get("task_id", "")) or None, "otp", dict(payload))
        except Exception:
            pass

    otp = OtpService(AdbSource(serial=serial), released=_LazyReleased(host), audit=audit)
    host.otp = otp
    configure(otp)
    # Passed explicitly: line.sms's own fallbacks import ``line.*`` absolutely, which does not resolve
    # under Hermes's plugin package name.
    # Round 8 (catalog G3): no browser target in this build. No page sink, no origin probe, the fill flag off:
    # line.sms registers no ``line_otp_fill`` and its browser path has nothing to type into.
    delivery = OtpDelivery(otp, None, origin_probe=None,
                           call_sink=CallSink(send=sealed.send_secret_to_call),
                           counterpart_of=lambda call_id: host.get().live_counterpart(call_id),
                           otp_browser_fill=False)
    host.otp_delivery = delivery
    register_otp_hooks(ctx, delivery, _LazyConsent(host), consent_lookup=lambda task_id: host.get().otp_token(task_id),
                       generation_lookup=lambda task_id: host.get().otp_generation(task_id),  # R1 M1
                       toolset=TOOLSET)


def _surface(ctx: Any) -> str:
    try:
        return str(ctx.session_surface() or "")
    except Exception:
        return ""


def _line(host: _Host, raw: str) -> str:
    parts = raw.strip().split(maxsplit=2)
    if not parts:
        parts = ["status"]
    verb = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    try:
        svc = host.get()
    except Exception as exc:
        return f"Hermes Line is not configured: {exc}"
    if verb == "status":
        d = svc.describe()
        tasks = svc.store.list_tasks(open_only=True)
        lines = [f"Hermes Line service: {d['status']} {d['detail']}".strip()]
        lines += [f"- {t.id} [{t.status.value}] {t.title} -> {t.destination_masked}" for t in tasks] or ["No open tasks."]
        return "\n".join(lines)
    if verb == "service":
        sub = arg.lower() or "status"
        if sub == "start":
            host.stopped = False
            return f"Hermes Line service: {host.start()} {svc.detail}".strip()
        if sub == "stop":
            host.stop()
            return "Hermes Line service stopped."
        return f"Hermes Line service: {svc.status} {svc.detail}".strip()
    if verb == "unlock-owner":  # R1 delta 10 N13: allow owner PIN entry again after a lockout
        return _done(svc.submit("unlock_owner", source="slash"))
    if verb == "allow-code":
        return _allow_code(host, svc, raw)
    if verb == "confirm-code":
        pending = host.pending_codes.pop(arg, None)
        if pending is None:
            return "Nothing to confirm (the request expired or was already used)."
        task_id, payload = pending
        return _done(svc.submit("allow_code", source="slash", task_id=task_id, payload=payload))
    mapping = {"approve": ("approve", "task"), "deny": ("deny", "task"),
               "hangup": ("hang_up", "call"), "takeover": ("take_over", "call"), "say": ("say", "call")}
    if verb not in mapping or not arg:
        return ("Usage: /line [status] | approve <task> | deny <task> | takeover <call> | hangup <call> | "
                "say <call> <text> | allow-code <task> <sender> call | unlock-owner | service start|stop|status")
    action, kind = mapping[verb]
    payload = {"text": parts[2]} if verb == "say" and len(parts) > 2 else None
    if kind == "task":
        task = svc.store.get_task(arg)
        if task is None:
            return "Unknown task."
        out = svc.submit(action, source="slash", task_id=task.id, payload=payload)
    else:
        call = svc.store.get_call(arg)
        if call is None:
            return "Unknown call."
        out = svc.submit(action, source="slash", call_id=call.id, task_id=call.task_id, payload=payload)
    return _done(out)


def _done(out: dict) -> str:
    result = out.get("result")
    if result is None:
        return f"Queued for the line service ({out['status']})."
    if result.get("stopped_before_dialling"):
        return result.get("note") or result.get("error") or "Stopped before dialling."
    if not result.get("ok"):
        return f"Not done: {result.get('error', 'unknown error')}"
    if result.get("prompt"):
        return f"Allowed. {result['prompt']}"
    return "Done."


def _allow_code(host: _Host, svc, raw: str) -> str:
    """/line allow-code <task> <sender> call: show the destination (the task's live call), then confirm."""
    import secrets as _secrets
    parts = raw.strip().split()
    if len(parts) != 4:
        return ("Usage: /line allow-code <task_id> <sender> call  (sender: the number, "
                "short code or name the SMS comes from)")
    _, task_id, sender, destination = parts
    task = svc.store.get_task(task_id)
    if task is None:
        return "Unknown task."
    try:
        prompt, payload = svc.otp_preview(task_id, [sender], destination, minutes=10)
    except GateRejected as exc:
        return f"Not allowed: {exc.reason}"
    except ValueError as exc:
        return f"Not allowed: {exc}"
    question = f"{prompt} One code, single use; Hermes never sees it."
    choice = None
    try:
        choice = host.ctx.ask_choice(question, [{"value": "allow", "label": "Allow once"},
                                                {"value": "cancel", "label": "Cancel"}])
    except Exception:
        choice = None
    if choice == "allow":
        return _done(svc.submit("allow_code", source="slash", task_id=task_id, payload=payload))
    if choice == "cancel":
        return "Cancelled."
    nonce = _secrets.token_hex(3)
    host.pending_codes[nonce] = (task_id, payload)
    return f"{question}\nTo allow it, run: /line confirm-code {nonce}"
