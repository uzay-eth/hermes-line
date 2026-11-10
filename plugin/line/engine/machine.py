"""Per-call state machine. Runs on the call service's event loop.

One ``CallSession`` per live call. ConversationRelay frames arrive through a
bounded inbox and are processed in order; Hermes turns run on the call's own
single worker thread (so the agent is never used concurrently) while the inbox
keeps processing interrupts. Terminal states are final: late or replayed
webhooks never move a call out of ``ended``/``failed``.

Detection runs only before a live person is confirmed. ``line.detect`` needs a
recent Hermes "live person?" question before it reports ``human``, so when the
other side says something the rules cannot place, the session asks that
question (it doubles as the AI disclosure) on a cooldown.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Optional

from . import deps
from .contracts import (CallState, Direction, EndReason, GateRejected, Mode, Role, Speaker, TaskRecord,
                        TaskStatus)
from .redact import mask_digits, mask_text

if TYPE_CHECKING:  # pragma: no cover
    from .service import CallService

S = CallState

ALLOWED: dict[CallState, set[CallState]] = {
    S.AWAITING_APPROVAL: {S.DIALING, S.ENDED, S.FAILED},
    S.DIALING: {S.IVR, S.ON_HOLD, S.HUMAN_DETECTED, S.AGENT_TALKING, S.RINGING_OWNER, S.ENDED, S.FAILED},
    S.IVR: {S.IVR, S.ON_HOLD, S.HUMAN_DETECTED, S.RINGING_OWNER, S.ENDED, S.FAILED},
    S.ON_HOLD: {S.IVR, S.ON_HOLD, S.HUMAN_DETECTED, S.RINGING_OWNER, S.ENDED, S.FAILED},
    S.HUMAN_DETECTED: {S.RINGING_OWNER, S.AGENT_TALKING, S.OWNER_CONNECTED, S.ENDED, S.FAILED},  # round 17: bridge
    S.RINGING_OWNER: {S.OWNER_CONNECTED, S.AGENT_TALKING, S.ON_HOLD, S.IVR, S.DIALING, S.ENDED, S.FAILED},
    S.OWNER_CONNECTED: {S.ENDED, S.FAILED},
    S.AGENT_TALKING: {S.RINGING_OWNER, S.ON_HOLD, S.OWNER_CONNECTED, S.ENDED, S.FAILED},  # ON_HOLD: L5 hold resume;
    # OWNER_CONNECTED: round 17, the owner's own inbound call joins its child's conference
    S.ENDED: set(),
    S.FAILED: set(),
}
PRE_HUMAN = {S.DIALING, S.IVR, S.ON_HOLD}
DIGITS = re.compile(r"^[0-9*#w]{1,32}$")
# Round 7: what Hermes says when a reply would otherwise leave a person in silence (fixed lines, not the model).
FILLER_LINES = ("One moment.", "Just a second.", "Still with you, one moment.")
REASK_LINE = "Sorry, I didn't catch that. Am I speaking with a live person?"
# Round 43 (MIT-018 STEER-3): the person often asks back "Are you a live person too?"; answer it honestly at once.
CONNECTING_LINE = ("Thank you. I'm an AI assistant calling for {owner}. "
                   "Please hold for just a moment while I connect {owner}.")
OWNER_MODE_NOTE = ("[system] The caller is {owner}, verified with the PIN. You are {owner}'s own assistant on this "
                   "call: answer questions and take notes. To phone a business for {owner}, call "
                   "hermes_line_call_new_task (destination, title, goal, bring_owner); the line then reads it back "
                   "and {owner} confirms it on the keypad (the number's last four digits); speech never approves. This call cannot use {owner}'s calendar, files or other tools: say "
                   "so and suggest asking in chat.")
WAIT_WORDS = {"", "wait", "(wait)", "[wait]", "silence", "(silent)", "..."}
MAX_PROMPT = 2000
# Recognition artifacts that never get confirmation speech. Round 16: aligned with L2's known hold artifacts
# (``line.detect`` noise words, repeated "thank you", and music markers), so hold-music junk gets no live-person
# question.
FILLERS = {"uh", "um", "hmm", "mm", "ah", "oh", "music", "inaudible", "instrumental", "silence", "la"}


LEG_ENDED = frozenset({"completed", "busy", "failed", "no-answer", "canceled"})


class CallAlreadyEnded(Exception):
    """relay_action: the leg itself ended; answer Hangup without completing the generation."""


def _words(text: str) -> list:
    """The real words of an utterance; [] for hold artifacts (music markers, fillers, repeated "thank you")."""
    text = re.sub(r"\[[^\]]*\]|[\u2669-\u266f]", " ", text or "")  # [music], [instrumental music], ♪ ♫
    norm = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    if re.fullmatch(r"(?:thank you ){2,}thank you", norm):
        return []
    return [w for w in re.findall(r"[A-Za-z]{2,}", text) if w.lower() not in FILLERS]


_CLEAR_YES = re.compile(r"^(?:yes|yeah|yep|yup|speaking)\b", re.I)
_NOT_A_PERSON = re.compile(r"\b(?:recording|recorded|automated|robot|machine|bot|virtual|digital|artificial|"
                           r"assistant|ai|not (?:a |an )?(?:live|real|actual)|not (?:a )?(?:person|human)|"
                           r"no(?:t)? one)\b|n't (?:a |an )?(?:live|real|person|human)", re.I)  # round 28: "yes, I'm not a live person"


def _clear_affirmative(text: str) -> bool:
    """Round 16: a clear yes to the live-person question (the only non-detector route to a confirmed person)."""
    t = (text or "").strip()
    return bool(_CLEAR_YES.match(t)) and not _NOT_A_PERSON.search(t)


@dataclass(frozen=True)
class SpokenUtterance:
    """Rounds 34-35 (N30-R, N32): one business utterance, created only from a real final and stamped once at creation
    in ``offer``: ``started_at`` is the earliest partial still pending for it, or the final's arrival when none is. It
    travels with the utterance through detection, timeout and late verdicts; only an utterance that started after our
    latest live-person question was sent can confirm a person or ring the owner. Partial text never becomes one.
    Round 41: ``ended_at`` is the arrival of its final (its last frame); it marks the end of outside-hold speech for
    question-segment renewal only. ``started_at`` alone decides confirmation."""
    text: str
    started_at: float
    ended_at: Optional[float] = None


INTERNAL_FRAME_KEYS = ("_started_at", "_ended_at")  # set by the engine only; stripped from relay frames


class TurnCtx:
    """R1 delta 13 N17: one Hermes turn. ``stale`` is set when the call enters a later phase (a person answered,
    the fixed script, the owner joined); a stale turn's speech and tool calls are dropped, however late they come."""
    __slots__ = ("gen", "phase", "stale", "task", "epoch", "timed_out")

    def __init__(self, gen: int, phase: str, task: Optional[asyncio.Task], epoch: int = 0):
        self.gen, self.phase, self.stale, self.task, self.epoch = gen, phase, False, task, epoch
        self.timed_out = False  # round 20 (N17-T): the turn timed out; its worker may still run, it may not act

    def over(self, epoch: int) -> bool:
        return self.stale or self.timed_out or self.epoch != epoch


class RelayLink:
    """One ConversationRelay WebSocket; ``send`` is awaited only by the writer task."""

    def __init__(self, send_json, close):
        self._send_json = send_json
        self._close = close
        self.closed = False
        self.generation: Optional[int] = None  # the relay generation whose token opened this socket
        self.setup_ok = False                   # frames count only after this socket's setup correlates

    async def send(self, frame: dict) -> None:
        await self._send_json(frame)

    async def close(self, code: int = 1000) -> None:
        if not self.closed:
            self.closed = True
            try:
                await self._close(code)
            except Exception:
                pass


class CallSession:
    # Round 19 (N16-C, N17-R): class-level defaults, so a session built without __init__ (test fixtures) has them.
    phase_epoch = 0      # bumped on entering the human phase, the fixed script, or the owner's acceptance
    menu_seq = 0         # bumped on every new phone-menu prompt
    menu_key_sent = -1   # the menu_seq a key was last sent for: one key per menu
    current_turn: Optional[TurnCtx] = None
    worker_turn: Optional[TurnCtx] = None  # N17-T: the turn whose Hermes worker is running now (one worker thread)
    assistant_declared = False  # round 25/27: L2 said automated_assistant on this call (L2 labels only)
    questions_sent = 0          # round 33 (N31): live-person questions actually sent, every route
    segment_questions = 0       # round 39: questions sent in the current hold segment
    unheld_marks: tuple = ()    # rounds 39-41: END stamps (final arrival) of the last two business finals outside hold
    phrases_paused = False      # round 39: the call entered hold; no phrase until a new automated_assistant label
    last_question_sent = -math.inf
    utt_first_partial_at: Optional[float] = None  # round 36: first business partial since the last final (set once)
    utt_last_partial_at = -math.inf  # the newest partial (the stall watcher's clock only; never the stamp)
    confirmed_utt: Optional[SpokenUtterance] = None  # round 34: the utterance that confirmed the person
    had_bot_label = False       # round 29: L2 said automated_assistant at least once on this call (any task)
    last_business_activity = -math.inf  # rounds 30-31: the last business prompt (partial or final) or barge-in
    business_epoch = 0          # rounds 30-31: bumped on every final business utterance (on arrival)
    assistant_pending: Optional[dict] = None  # round 31 (N27): the phrase waiting in the quiet gate
    question_pending: Optional[dict] = None   # round 32 (N29): the one live-person question waiting or in flight
    human_started = False       # round 32 (N29): the connecting line and owner ring run once
    stall_watch_running = False  # round 35 (N26 recovery): the stalled-partials watcher
    stall_asked_for: Optional[float] = None  # the pending stamp the stall question was asked for (once each)
    STALL_AFTER_S = 2.5         # partials with no final for this long after the last one: the engine asks
    QUIET_CAP_S = 8.0           # round 31 (N26): after this much waiting, the next 0.4 s gap is enough
    QUIET_CAP_GAP_S = 0.4
    QUIET_HARD_S = 10.0         # ... and at this point the line is spoken anyway
    yes_confirmed = False       # round 29: our fixed question was answered with a shared clear yes
    assistant_said: tuple = ()  # round 25: (monotonic time, phrase id) for every fixed phrase said to it

    def __init__(self, svc: "CallService", call, task: Optional[TaskRecord]):
        self.svc = svc
        self.cfg = svc.cfg
        self.store = svc.store
        self.call_id = call.id
        self.task = task
        self.direction = call.direction
        self.mode = call.mode
        self.state: CallState = call.state
        self.provider_sid = call.provider_sid
        self.owner_sid = call.owner_sid
        self.loop = svc.loop
        self.inbox: asyncio.Queue = asyncio.Queue(maxsize=self.cfg.queue_max)
        self.out: Optional[asyncio.Queue] = None
        self.relay: Optional[RelayLink] = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"hl-{call.id[-6:]}")
        # Round 7: questions to the model (detector, summary) run on their own worker, so a slow question
        # never holds up the spoken turn and a turn never holds up a question.
        self.ask_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"hl-ask-{call.id[-6:]}")
        self._driver_lock = threading.Lock()
        self.unclear_replies = 0
        self.party_questions = 0      # round 43c: live-person questions sent to the current party (at most two)
        self.answered_replies = set()  # round 43c: replies already decided (by their start stamp)
        self.assumed_ring_used = False  # round 43c: at most one owner ring without a clear yes per call
        self._wrap_up: Optional[asyncio.Task] = None
        self.inbound_context = ""  # round 7: untrusted notes about an inbound caller, for the briefing
        self.owner_caller = False  # round 12: an inbound call from owner_number (caller ID only)
        # Round 16 (a): the inbound greeting, and the greeting held back while a carrier screening prompt may play.
        self.greeting = ""
        self.pending_greeting: Optional[str] = None
        self.screening_answered = False
        self.note_offered = False  # round 16 (b): Hermes offered to take a note; it is not offered again unasked
        # Round 16: an outbound call rings the owner only once a person is confirmed (L2 HUMAN or a clear yes), and
        # the task's guidance becomes an enforced deny list for the call tools.
        self.human_confirmed = False
        self.last_detection: Any = None
        self.last_menu: Optional[str] = None
        from .task_rules import restrictions_from
        self.restrictions = restrictions_from(task.goal if task is not None else "")
        # Round 16 (c): engine-enforced task options. A fallback question makes this an information-only task.
        self.options = self.store.task_options(task.id) if task is not None else {}
        self.fallback_question: Optional[str] = self.options.get("fallback_question")
        self.info_only = bool(self.fallback_question)
        if self.info_only:
            from .task_rules import CALLBACK, CHANGE, PAYMENT
            self.restrictions = self.restrictions | {CALLBACK, PAYMENT, CHANGE}
        self.script: Optional[dict] = None  # the fallback question in progress
        self.current_turn: Optional[TurnCtx] = None  # N17: the Hermes turn in flight, if any
        self._last_holding_line = -math.inf
        # Round 13, owner line mode: the PIN step, then the verified owner's requests.
        self.pin_phase = False
        self.pin_failures = 0
        self._pin_keys: list = []
        self._pin_timer: Optional[asyncio.Task] = None
        self.owner_verified = False
        # R1 delta 11 N12: a request is confirmed on the keypad, never by speech: the read-back ends with "key the
        # last four digits of the number, then pound", and only those digits, for this task and generation, within
        # 60 s, approve it. Wrong digits or the timeout cancel the confirmation (chat approval still works).
        self.proposal: Optional[dict] = None  # {"task_id", "e164", "gen", "code", "expires"}
        self.confirm_gen = 0
        self._confirm_keys: list = []
        self._confirm_timer: Optional[asyncio.Task] = None
        self._readback: Optional[str] = None
        # N14: on an owner-number call, recent caller frames are scrubbed together and briefly held (see owner_line)
        self._win: list = []
        self._win_lock = asyncio.Lock()
        self._win_hold: Optional[asyncio.Task] = None
        self._last_business_seq = 0
        # Round 17, live-leg owner bridge: the owner's inbound call (parent) and the one task it dials (child).
        self.parent: Optional["CallSession"] = None
        self.child: Optional["CallSession"] = None
        self.children_started = 0
        self.bridged = False
        self.conference_status_id = ""  # the parent joins the child's conference and reports to the child
        self.mcp_server_name = ""  # round 9: this call's session MCP server name (scope check identity)
        self.ask_server_name = ""  # round 9: the detector/summary session's empty server name
        self.driver: Any = None
        self.driver_error: Optional[str] = None
        self.agent_session_id = f"hermes-line-{call.id}"
        self.extra_agent_ids: set[str] = set()
        self.tracker = deps.HoldTracker(ask_model=self._ask_model)
        self.gen = 0
        self.turn_task: Optional[asyncio.Task] = None
        self.started = time.monotonic()
        self.answered = False
        self.setup_seen = False
        self.end_request: Optional[EndReason] = None
        self.finish_outcome: Optional[str] = None
        self.finish_summary: str = ""  # the model's own finish note: checked against the task rules, never summarised
        self.engine_note: str = ""     # round 37: an engine-authored summary note (the fallback script's)
        self.owner_requested = False
        self.handoff_pending = False
        self.conference = ""
        self.state_before_ring: Optional[CallState] = None
        self.reconnects = 0
        # Relay generations (R1 M2): generation N is the Nth <Connect><ConversationRelay> TwiML handed
        # to Twilio. Its TwiML is cached (a retransmitted /answer gets the same one, no new token), and
        # the completion of generation N is processed once; retransmissions get the cached result.
        self.relay_gen = -1
        # Realtime voice path (L5): set by the service when the call's transport is media_streams.
        self.voice: Any = None
        self.voice_conversation = False   # Grok holds the conversation; caller speech is not a Hermes turn
        self.digits_redirect_until = 0.0  # DTMF fallback redirect in progress (no drop watchdog, no debit)
        self.gen_twiml: dict[int, str] = {}
        self.action_results: dict[int, str] = {}
        self._completing: dict[int, asyncio.Task] = {}  # R1 delta M2: one in-flight completion per generation
        self.last_confirm = -math.inf
        self.disclosed = False
        self.digits_gen = -1
        self.left_voicemail = False
        self._bg: set[asyncio.Task] = set()
        self._finalizing = False
        self._drop_task: Optional[asyncio.Task] = None
        self._consumer = self._spawn(self._consume())
        self._spawn(self._max_duration_watchdog())

    # -- helpers ---------------------------------------------------------
    def _spawn(self, coro) -> asyncio.Task:
        task = self.loop.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def _event(self, kind: str, **data: Any) -> None:
        self.store.log_event(self.call_id, self.task.id if self.task else None, kind, data)

    def _transcript(self, speaker: Speaker, text: str) -> int:
        pin = self.svc.owner_pin()
        if pin is not None and text:  # N14: a second line of defence for every speaker
            from .owner_line import scrub_pin
            text = scrub_pin(text, pin)
        if text and text.strip():
            seq, rev = self.store.append_transcript(self.call_id, speaker.value, text.strip())
            self.svc.notify(self.call_id, rev)
            if speaker is Speaker.BUSINESS:
                self._last_business_seq = seq
            return seq
        return 0

    def speech_wait(self, text: str) -> float:
        return min(8.0, len(text or "") / max(self.cfg.speech_chars_per_s, 1.0))

    async def _in_worker(self, fn, *args, timeout: Optional[float] = None):
        fut = self.loop.run_in_executor(self.executor, fn, *args)
        return await asyncio.wait_for(fut, timeout) if timeout else await fut

    async def _in_ask_worker(self, fn, *args, timeout: Optional[float] = None):
        fut = self.loop.run_in_executor(self.ask_executor, fn, *args)
        return await asyncio.wait_for(fut, timeout) if timeout else await fut

    @property
    def owner_self_test(self) -> bool:
        """Round 7: an outbound call to the owner's own phone is the owner testing the line; whoever answers
        is the owner, so the live-person question is not asked."""
        return (self.direction is Direction.OUTBOUND and self.task is not None and bool(self.cfg.owner_number)
                and self.task.destination_e164 == self.cfg.owner_number)

    @property
    def terminal(self) -> bool:
        return self.state.terminal or self._finalizing

    @property
    def title(self) -> str:
        return self.task.title if self.task else "callback"

    async def set_state(self, new: CallState, why: str = "") -> bool:
        if self.terminal and not new.terminal:
            return False
        if new is self.state:
            return True
        if new not in ALLOWED[self.state]:
            self._event("transition_refused", frm=self.state.value, to=new.value, why=why)
            return False
        old, self.state = self.state, new
        if new is S.ON_HOLD and not self.phrases_paused:
            self.phrases_paused = True  # round 39: no phrase into a hold queue until a new automated prompt
            self._event("assistant_phrases_paused", why="hold")
        rev = self.store.update_call(self.call_id, state=new, phase=self._phase_for(new, why))
        self._event("state", frm=old.value, to=new.value, why=why)
        self._voice_state(new)
        self.svc.notify(self.call_id, rev)
        return True

    def _phase_for(self, state: CallState, why: str) -> Optional[str]:
        """Round 37 (B9): the phase a state change opens, when finer than the store's default for that state: an
        automated assistant's IVR state is "assistant"; talking after the owner did not take the call is "fallback"."""
        if state is S.IVR and (why == "automated assistant" or self.had_bot_label):
            return "assistant"
        if state is S.AGENT_TALKING and why.startswith(("owner did not accept", "owner unavailable")):
            return "fallback"
        return None

    def _voice_state(self, state: CallState) -> None:
        """L5 hook: the voice gate follows every stored state change (mandatory)."""
        if self.voice is not None:
            try:
                self.voice.on_state(state.value)
            except Exception as exc:
                self._event("voice_hook_error", error=type(exc).__name__)

    @property
    def grok_talks(self) -> bool:
        return self.voice is not None and getattr(self.voice.settings, "conversation", "") == "grok"

    async def _begin_voice_conversation(self, context: str) -> None:
        self.voice_conversation = True
        await self.voice.begin_conversation(context)

    # -- driver ----------------------------------------------------------
    def _ensure_driver(self) -> Any:
        """Worker thread only. Start the call's Hermes session once."""
        with self._driver_lock:  # the turn worker and the ask worker may both need the driver first
            return self._ensure_driver_locked()

    def _ensure_driver_locked(self) -> Any:
        if self.driver is None and self.driver_error is None:
            try:
                driver = self.svc.driver_factory(self.agent_session_id)
                briefing = self.svc.briefing(self.task, self.direction)
                if self.inbound_context:
                    briefing = briefing + "\n" + self.inbound_context
                if callable(getattr(driver, "set_mcp_servers", None)):
                    # Round 6: the call's own session MCP server, so the in-call tools reach this service
                    # whatever profile the Hermes gateway runs in.
                    servers = self.svc.call_tool_servers(self)
                    if servers:
                        from .call_tools import BRIEFING_NOTE
                        driver.set_mcp_servers(servers)
                        briefing = briefing + "\n" + BRIEFING_NOTE
                if callable(getattr(driver, "restrict_tools", None)):
                    # Rounds 8-9 (catalog rule 12, R1 delta 4 N4/N6): a stranger partly drives these sessions. The
                    # primary may hold only this call's own MCP server, the detector/summary session only its own
                    # empty one; both are checked against the session's complete toolset scope before use.
                    from functools import partial

                    from .call_tools import disallowed_session_tools
                    ask_servers = self.svc.ask_tool_servers(self)
                    driver.on_refusal = lambda kind, why: self.loop.call_soon_threadsafe(
                        lambda: self._event("agent_session_refused", session=kind, why=str(why)[:160]))
                    if callable(getattr(driver, "set_ask_mcp_servers", None)):
                        driver.set_ask_mcp_servers(ask_servers)
                    driver.restrict_tools(
                        partial(disallowed_session_tools, server_names=[self.mcp_server_name]),
                        ask_check=partial(disallowed_session_tools, server_names=[self.ask_server_name],
                                          tools=frozenset()))
                driver.start(briefing)
                note = getattr(driver, "direct_tools_note", lambda: "")()
                if note:  # round 12: the exact deferred names, so a finish needs no tool_search round
                    driver.add_briefing(note)
                self.driver = driver
                hermes_sid = getattr(driver, "hermes_session_id", None)
                if hermes_sid:  # ACP: in-call tools run under the gateway's session id
                    self.svc.register_agent_session(hermes_sid, self)
            except Exception as exc:
                self.driver_error = type(exc).__name__
                detail = str(exc) if ("not restricted" in str(exc) or "not neutral" in str(exc)) else ""
                self.svc.call_tools.revoke(self.call_id)
                data = {"why": detail[:800]} if detail else {"error": self.driver_error}
                self.loop.call_soon_threadsafe(lambda: self._event("agent_start_failed", **data))
        return self.driver

    def warm(self) -> None:
        """Initialize the Hermes session in the background so the first turn is fast."""
        self._spawn(self._in_worker(self._ensure_driver))

    def _ask_model(self, prompt: str) -> str:
        """line.detect's injected model, on the worker thread, bounded in time."""
        driver = self._ensure_driver()
        if driver is None:
            return ""
        raw = driver.ask(prompt + "\nReply with only the JSON object.", self.cfg.ask_timeout_s) or ""
        m = re.search(r"\{.*\}", raw, re.S)
        return m.group(0) if m else raw.strip()

    # -- relay plumbing ----------------------------------------------------
    def attach_relay(self, link: RelayLink) -> bool:
        if self.terminal or (self.relay is not None and not self.relay.closed):
            return False
        self.relay = link
        link.generation = self.relay_gen
        self.out = asyncio.Queue(maxsize=256)
        if self._drop_task:
            self._drop_task.cancel()
            self._drop_task = None
        self._spawn(self._writer(link, self.out))
        self._event("relay_connected", reconnects=self.reconnects)
        return True

    async def _writer(self, link: RelayLink, queue: asyncio.Queue) -> None:
        while not link.closed:
            frame = await queue.get()
            if frame is None:
                break
            try:
                await link.send(frame)
            except Exception:
                break

    def _send(self, frame: dict) -> None:
        if self.out is None or self.relay is None or self.relay.closed:
            return
        try:
            self.out.put_nowait(frame)
        except asyncio.QueueFull:
            self._event("relay_out_overflow")

    def relay_closed(self, link: RelayLink) -> None:
        if link is not self.relay:
            return
        self.relay = None
        if self.out is not None:
            try:
                self.out.put_nowait(None)
            except asyncio.QueueFull:
                pass
        if self.terminal or self.handoff_pending or self.end_request:
            return
        if time.monotonic() < self.digits_redirect_until:
            self._event("relay_redirecting")  # the DTMF fallback replaces the stream; not a drop
            return
        self._event("relay_lost")
        self._drop_task = self._spawn(self._drop_watchdog())

    @property
    def new_party_questions(self) -> int:  # round 33: the one global counter (kept under its round-27 name)
        return self.questions_sent

    def offer(self, frame: dict, link: Optional[RelayLink] = None) -> bool:
        """Bounded ingress: a full inbox drops the frame and records it. ``link`` is the socket the
        frame came from; such frames count only after that socket's setup correlated."""
        if link is not None and any(k in frame for k in INTERNAL_FRAME_KEYS):
            frame = {k: v for k, v in frame.items() if k not in INTERNAL_FRAME_KEYS}  # never from the relay
        kind = frame.get("type")
        text = str(frame.get("voicePrompt") or "").strip() if kind == "prompt" else ""
        final = kind == "prompt" and frame.get("last", True) is True and bool(text)
        if kind == "prompt" and text:
            # Round 34 (N30-R): the utterance's start, stamped once here: its first partial or its final, whichever
            # arrived earlier. The stamp goes with the frame and becomes part of the utterance object.
            # Round 36 (N32-R): the stamp of a final is the arrival of the first business frame (partial or final)
            # since the previous business final. Only a final clears it: no timer, no reset. After an abandoned
            # partial the next final cannot confirm (it fails closed); the one after it can.
            now = time.monotonic()
            if not final:
                if self.utt_first_partial_at is None:
                    self.utt_first_partial_at = now
                self.utt_last_partial_at = now
            else:
                first = self.utt_first_partial_at
                started = now if first is None else min(first, now)
                self.utt_first_partial_at = None
                frame = dict(frame, _started_at=started, _ended_at=now)
        if kind in ("prompt", "interrupt"):
            # Round 31 (N25/N26): business activity is recorded on arrival, so the quiet gate sees speech even while
            # the frame consumer is busy (for example waiting in the gate itself).
            self.last_business_activity = time.monotonic()
            if final:
                self.business_epoch += 1  # a newer utterance: a pending supersedable line is dropped
            elif kind == "prompt" and text and self.direction is Direction.OUTBOUND and self.state in PRE_HUMAN:
                # round 35 (N26 recovery): partials with no final get the fixed question from the engine itself
                if not self.stall_watch_running and self.loop is not None:
                    self.stall_watch_running = True
                    self.loop.call_soon_threadsafe(lambda: self._spawn(self._stall_watch()))
        try:
            self.inbox.put_nowait((link, frame, time.monotonic()))  # N12: when the frame arrived
            return True
        except asyncio.QueueFull:
            self._event("ingress_overflow", type=str(frame.get("type", ""))[:20])
            return False

    async def _stall_watch(self) -> None:
        """Round 35 (N26 recovery, replacing promotion): partials arrived and no final followed within STALL_AFTER_S of
        the last one. The engine asks the fixed live-person question itself, through the normal quiet gate and the
        global budget, once per pending utterance. No utterance is made from partial text, it never reaches the
        detector or the transcript, and nothing confirms from it: the eventual final decides, with the pending stamp."""
        try:
            while not self.terminal:
                pending = self.utt_first_partial_at
                if pending is None:
                    return  # a final came: it decides
                wait = self.utt_last_partial_at + self.STALL_AFTER_S - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(min(wait, 0.5))
                    continue
                if (self.direction is not Direction.OUTBOUND or self.state not in PRE_HUMAN or self.human_confirmed
                        or self.stall_asked_for == pending):
                    return
                self.stall_asked_for = pending
                if self._question_pending():
                    self._event("partials_stalled", action="none: our question is pending")
                    return
                self._event("partials_stalled", action="live-person question")
                await self._ask_live_person(self.cfg.confirm_question() if self.last_confirm == -math.inf else
                                            "Sorry, am I speaking with a live person?")
                return
        finally:
            self.stall_watch_running = False

    async def _consume(self) -> None:
        while True:
            link, frame, rx = await self.inbox.get()
            if self.terminal:
                continue
            if link is not None:
                if link.closed or link is not self.relay:
                    continue
                if frame.get("type") != "setup" and not link.setup_ok:
                    self._event("frame_before_setup", type=str(frame.get("type", ""))[:20])
                    continue
            try:
                await self._handle_frame(frame, rx)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad frame never ends the call
                self._event("frame_error", error=type(exc).__name__)

    async def _handle_frame(self, frame: dict, rx: Optional[float] = None) -> None:
        kind = frame.get("type")
        if kind == "setup":
            await self._on_setup(frame)
        elif kind == "prompt":
            if frame.get("last", True) is not True:
                return  # partial prompts only mark activity (offer)
            text = frame.get("voicePrompt")
            if isinstance(text, str) and text.strip():
                started = frame.get("_started_at")
                if not isinstance(started, (int, float)):
                    started = rx if rx is not None else time.monotonic()
                ended = frame.get("_ended_at")
                if not isinstance(ended, (int, float)):
                    ended = started
                utt = SpokenUtterance(text[:MAX_PROMPT], float(started), float(max(ended, started)))
                await self._on_speech(text[:MAX_PROMPT], rx, utt=utt)
        elif kind == "interrupt":
            spoken = frame.get("utteranceUntilInterrupt")
            self._on_interrupt(spoken if isinstance(spoken, str) else None)
        elif kind == "dtmf":
            if self.pin_phase:  # round 13: a PIN key, never logged or shown to Hermes
                await self._pin_key(str(frame.get("digit", ""))[:1])
                return
            if self.owner_verified and self.proposal is not None:  # N12: the keypad confirmation of a read-back
                await self._confirm_key(str(frame.get("digit", ""))[:1])
                return
            if self.owner_verified and self.child is not None and str(frame.get("digit", ""))[:1] == "*":
                await self.svc.cancel_child(self)  # round 17: star cancels the live child before the bridge
                return
            self._event("remote_dtmf")
        elif kind == "error":
            self._event("relay_error", description=str(frame.get("description", ""))[:200])

    async def _on_setup(self, frame: dict) -> None:
        sid = frame.get("callSid")
        if self.provider_sid and sid != self.provider_sid:
            self._event("relay_correlation_mismatch")
            if self.relay:
                await self.relay.close(1008)
            return
        if self.relay is not None:
            self.relay.setup_ok = True
        self.answered = True
        first = not self.setup_seen
        self.setup_seen = True
        if first and self.direction is Direction.OUTBOUND and self.svc.disclosure_on:
            self.disclosed = True
            self._transcript(Speaker.HERMES, self.cfg.disclosure())
        if first and self.direction is Direction.INBOUND:
            if self.pending_greeting is not None:  # round 16 (a): spoken by the line after the screening wait
                self._spawn(self._greet_after(self.cfg.inbound_screening_hold_s, "no screening prompt"))
            else:
                self._transcript(Speaker.HERMES, self.greeting or self.svc.inbound_greeting(
                    self.task, owner_caller=self.owner_caller))
            self.disclosed = True
            if self.grok_talks:
                await self._begin_voice_conversation(
                    f"This is an incoming call to {self.cfg.owner_name}'s line. You already greeted the caller: "
                    f"{self.svc.inbound_greeting(self.task, owner_caller=self.owner_caller)}")
            if self.mode is Mode.OWNER and self.task is not None:
                if not await self.ring_owner("callback"):
                    self._event("callback_owner_not_rung")

    # -- speech ------------------------------------------------------------
    async def _on_speech(self, raw: str, rx: Optional[float] = None, *, utt: Optional[SpokenUtterance] = None) -> None:
        # R1 delta 10/11 N14: PIN entry is keypad only; spoken digits never verify anyone. As a backstop the
        # configured PIN, spoken or partly transcribed, is scrubbed before the transcript, the detector or Hermes see
        # the text: on an owner-number call across a rolling window of caller frames, elsewhere frame by frame.
        pin = self.svc.owner_pin()
        if await self._screening(raw):  # round 16 (a): the carrier's prompt, answered by the line
            return
        if self.pending_greeting is not None:  # the caller spoke first: greet now
            await self._greet_now("caller spoke")
            if re.fullmatch(r"\W*(?:hello|hi|hey|yes|yeah|hello there)(?:\W+(?:hello|hi|hey|there))*\W*", raw.lower()):
                self._transcript(Speaker.BUSINESS, mask_text(raw))
                return  # a bare hello is answered by the greeting itself
        if pin is not None and self.owner_caller and self.direction is Direction.INBOUND:
            await self._window_speech(raw, rx, pin, utt)
            return
        if pin is not None:
            from .owner_line import scrub_pin
            raw = scrub_pin(raw, pin)
        await self._handle_speech(raw, rx, utt=utt)

    async def _window_speech(self, raw: str, rx: Optional[float], pin: str,
                             utt: Optional[SpokenUtterance] = None) -> None:
        from .owner_line import HOLD_DIGITS_S, pin_may_continue, scrub_window
        async with self._win_lock:
            now = time.monotonic()
            self._win = [w for w in self._win if not w["done"] or now - w["t"] <= 10.0][-4:]
            # Matching always runs on the original fragments (``orig``); ``raw`` is what may be recorded or sent.
            self._win.append({"orig": raw, "raw": raw, "rx": rx, "t": now, "seq": 0, "done": False, "utt": utt})
            for w, clean in zip(self._win, scrub_window([w["orig"] for w in self._win], pin)):
                if clean != w["raw"] and "[PIN]" in clean:
                    if w["done"] and w["seq"]:  # already written: redact it; text already sent to Hermes stays
                        self.svc.notify(self.call_id, self.store.redact_transcript(self.call_id, w["seq"],
                                                                                   mask_text(clean)))
                        self._event("pin_redacted_late")
                    w["raw"] = clean
            if self._win_hold is not None:
                self._win_hold.cancel()
                self._win_hold = None
            waiting = [w for w in self._win if not w["done"]]
            if len(waiting) < 3 and pin_may_continue([w["orig"] for w in self._win], pin):
                self._win_hold = self._spawn(self._flush_window_after(HOLD_DIGITS_S))  # the PIN may continue
                return
            await self._flush_window()

    async def _flush_window_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        async with self._win_lock:
            self._win_hold = None
            await self._flush_window()

    async def _flush_window(self) -> None:
        for w in self._win:
            if not w["done"]:
                w["done"] = True
                await self._handle_speech(w["raw"], w["rx"], utt=w.get("utt"))
                w["seq"] = self._last_business_seq

    # -- round 16 (a): carrier call screening on forwarded inbound calls ------------------------
    async def _screening(self, raw: str) -> bool:
        """True when ``raw`` was the carrier's screening prompt (answered here, never a Hermes turn). Inbound, relay
        transport, the first SCREENING_WINDOW_S of the call, the prompt family only."""
        from .screening import GREET_AFTER_PRESS_S, SCREENING_KEY, SCREENING_WINDOW_S, is_carrier_screening
        if (self.direction is not Direction.INBOUND or self.voice is not None
                or self._elapsed_ms() > SCREENING_WINDOW_S * 1000 or not is_carrier_screening(raw)):
            return False
        pin = self.svc.owner_pin()
        if pin is not None:
            from .owner_line import scrub_pin
            raw = scrub_pin(raw, pin)
        self._transcript(Speaker.BUSINESS, mask_text(raw))
        if self.screening_answered:
            self._event("carrier_screening_repeat")
            return True
        self.screening_answered = True
        self._send({"type": "sendDigits", "digits": SCREENING_KEY})
        self._transcript(Speaker.SYSTEM, f"Pressed {SCREENING_KEY}")
        self._event("carrier_screening_answered", digits=SCREENING_KEY, at_ms=self._elapsed_ms())
        if self.pending_greeting is None:  # the greeting already played to the screening: say it again
            self.pending_greeting = self.greeting or self.svc.inbound_greeting(self.task, owner_caller=self.owner_caller)
        self._spawn(self._greet_after(GREET_AFTER_PRESS_S, "after screening"))
        return True

    async def _greet_after(self, delay: float, why: str) -> None:
        await asyncio.sleep(delay)
        await self._greet_now(why)

    async def _greet_now(self, why: str) -> None:
        line, self.pending_greeting = self.pending_greeting, None
        if line is None or self.terminal:
            return
        self._event("greeting", why=why)
        await self.speak(line, opening=True)  # the inbound opening disclosure

    async def _handle_speech(self, raw: str, rx: Optional[float] = None, *,
                             utt: Optional[SpokenUtterance] = None) -> None:
        # Drivers only ever see masked speech: an ACP session is persisted by Hermes, and the
        # agent never needs a code or card number the other side reads out.
        masked = text = mask_text(raw)
        if utt is None:  # a direct call (fixtures): stamped now
            utt = SpokenUtterance(masked, rx if rx is not None else time.monotonic())
        utt = replace(utt, text=masked)  # the stamp stays; the text is what was recorded
        seq = self._transcript(Speaker.BUSINESS, masked)
        if self.voice_conversation and self.state in (S.AGENT_TALKING, S.HUMAN_DETECTED, S.RINGING_OWNER):
            return  # Grok answers the caller itself and reaches Hermes through its tools (L5)
        if self.info_only and self.state in (S.AGENT_TALKING, S.HUMAN_DETECTED, S.RINGING_OWNER):
            await self._info_only_speech(text)  # round 16 (c): fixed lines only once a person answered
            return
        if self.state in (S.AGENT_TALKING, S.HUMAN_DETECTED):
            self.start_turn(f"[business] {text}")
            return
        if self.state is S.RINGING_OWNER:
            if self.state_before_ring in (S.HUMAN_DETECTED, S.AGENT_TALKING, None):
                self.start_turn(f"[business] {text}\n[system] {self.cfg.owner_name} is being connected; "
                                "if they ask, say so briefly. Do not start new topics.")
            return
        if self.state is S.OWNER_CONNECTED:
            return
        if self.state not in PRE_HUMAN:
            return
        if self.owner_self_test and _words(text):
            self._event("owner_self_test")
            await self._on_human(text, confirmed=True, utt=utt, note=f"[system] This is {self.cfg.owner_name}, the owner, answering their own "
                                            "phone to test the line. Talk to them directly; there is no business.")
            return
        # Round 7: classification never keeps a person waiting. The detector gets reply_budget_s; if it needs
        # longer, Hermes answers now (the live-person question, or a re-ask) and a late verdict still applies.
        detecting = self._spawn(self._detect(masked, seq))
        try:
            detection = await asyncio.wait_for(asyncio.shield(detecting), self.cfg.reply_budget_s)
        except asyncio.TimeoutError:
            self._event("detect_slow")
            self._spawn(self._late_detection(detecting, text, utt))  # the late verdict keeps the utterance's stamp
            if await self._question_reply(text, utt, None):  # round 43c: decided from the words alone
                return
            await self._maybe_confirm(text, utt=utt)  # round 25: skipped for a known automated assistant
            return
        await self._on_detection(detection, text, utt=utt)

    # -- round 25: automated assistants (fixed phrases only) ---------------------------------------
    def _assistant_state(self, label: str) -> Optional[str]:
        """The detection state that allows a fixed phrase to an automated system, or None: only L2's
        ``automated_assistant`` (L2 round 5 contract), before a person, on a restricted task."""
        if not self.restrictions or self.state not in PRE_HUMAN or self.human_confirmed or self.terminal:
            return None
        return label if label == "automated_assistant" else None

    NEW_PARTY_QUESTION_EVERY_S = 20.0
    NEW_PARTY_QUESTIONS_MAX = 4      # round 39: per hold segment (was per call)
    HOLD_SEGMENT_S = 120.0           # round 39: this much hold since the last question starts a new segment
    QUESTIONS_PER_CALL_MAX = 12      # round 39: the hard cap per call

    def _question_pending(self) -> bool:
        """Our fixed live-person question is still awaiting its reply (the reply window, inclusive)."""
        return (self.last_confirm != -math.inf
                and time.monotonic() - self.last_confirm <= self.cfg.confirm_reply_window_s)

    def _after_question(self, utt: Optional[SpokenUtterance]) -> bool:
        """Round 34 (N30-R): the utterance started after our latest live-person question was actually sent (always
        true before any question). The one rule for every confirmation and owner ring; None (a direct call) is now."""
        started = time.monotonic() if utt is None else utt.started_at
        return started > self.last_question_sent

    def _replies_to_question(self, utt: Optional[SpokenUtterance]) -> bool:
        """A reply to our question: it is pending, and the utterance started after it was sent."""
        return self._question_pending() and self._after_question(utt)

    def _strict_confirmation(self) -> bool:
        """Round 30: restricted and information-only tasks in every call, and any call after a bot label: a person
        only after our fixed question and a shared clear yes."""
        return bool(self.restrictions) or self.had_bot_label

    def _is_clear_yes(self, text: str, utt: Optional[SpokenUtterance] = None) -> bool:
        """Rounds 29-30: under strict confirmation, the shared contract (line.detect.affirmative, also used by L2);
        otherwise the round-16 check, unchanged. Round 34: never for an utterance that started before our question."""
        if not self._after_question(utt):
            return False
        if self._strict_confirmation():
            # Round 43 (MIT-018 STEER-3, Hilton call 1): a reply that starts with a clear yes, even with a
            # question after it ("Yes. Are you a live person too?"); callers check that it replies to our question.
            from ..detect.affirmative import clear_yes_reply
            return clear_yes_reply(text)
        return _clear_affirmative(text)

    async def _bot_call_human(self, text: str, utt: Optional[SpokenUtterance] = None) -> None:
        """Round 29 (R1b delta 26 N24, N23-R): once a call had a bot label, a person is accepted (and the owner rung)
        only after our fixed live-person question was asked and the reply is a shared clear yes. An L2 human label
        without a pending question means: ask the question (within the 20 s / 4 budget). With a pending question and
        no clear yes: silence, no re-ask."""
        if self._question_pending():
            if self._replies_to_question(utt) and self._is_clear_yes(text, utt):
                self._event("live_person_confirmed_by_reply")
                self.yes_confirmed = True
                await self._on_human(text, confirmed=True, utt=utt)
                return
            self._event("human_label_not_confirmed", why="no clear yes to the pending live-person question"
                        if self._after_question(utt) else "the utterance started before our question")
            return
        self._event("human_label_needs_question")
        await self._new_party_check(text, utt)

    async def _question_reply(self, text: str, utt: Optional[SpokenUtterance], label: Optional[str]) -> bool:
        """Round 43c (MIT-018 STEER-4; Hilton calls 1-2 reached real people three times and never rang the owner):
        on a strict call, a reply to our pending live-person question is decided here, by what it says.
        - "yes": an explicit affirmation, any words after it, and nothing a bot, menu or recording says: confirmed.
        - "bot": a bot cue, a negative, or the detector's automated/menu/hold/voicemail label: the old paths decide
          (no ring, no re-ask).
        - "unclear" (human-like): the one re-ask now, at most two questions per party; if the reply to the second
          question is still human-like, the owner is rung anyway: in owner mode the owner is the final judge (a wrong
          ring costs the owner seconds; a missed person loses the call).
        True when decided here."""
        if (not hasattr(self, "last_confirm") or not self._strict_confirmation() or self.terminal or self.state not in PRE_HUMAN or self.human_confirmed
                or not _words(text) or not self._replies_to_question(utt)):
            return False
        key = utt.started_at if utt is not None else None
        if key is not None and key in self.answered_replies:
            return True
        from ..detect.affirmative import reply_verdict
        verdict = reply_verdict(text, label)
        if verdict == "bot":
            return False
        if key is not None:
            self.answered_replies.add(key)
        if verdict == "yes":
            self._event("live_person_confirmed_by_reply", label=label)
            self.yes_confirmed = True
            await self._on_human(text, confirmed=True, utt=utt)
            return True
        if self.party_questions >= 2 and (verdict == "generic" or self.assumed_ring_used):
            self._event("live_person_not_assumed", label=label,
                        why="greeting or closing only" if verdict == "generic" else "one assumed ring per call")
            return True  # two questions already: never a third question; a bare "Goodbye." alone never rings
        if self.party_questions >= 2:
            self._event("live_person_assumed_after_two_questions", label=label)
            self.assumed_ring_used = True
            self.yes_confirmed = True  # the owner judges: they hear the person before pressing 1
            await self._on_human(text, confirmed=True, utt=utt)
            return True
        self._event("live_person_reask", label=label)
        await self._ask_live_person(REASK_LINE, utt=utt, reask=True)
        return True

    def _possible_new_party(self, detection: Any) -> bool:
        """Round 27 (L2 round 7 contract): L2 says a new party may have taken over from the bot."""
        evidence = [str(e) for e in getattr(detection, "evidence", ()) or ()]
        return (getattr(detection, "label", "") == "unknown" and "possible_new_party" in evidence
                and bool(self.restrictions) and self.state in PRE_HUMAN and not self.human_confirmed)

    async def _new_party_check(self, text: str, utt: Optional[SpokenUtterance] = None) -> None:
        """Round 27 (N20/N21): a possible new party after a bot gets the fixed live-person question (at most one every
        20 s, 4 per hold segment, 12 per call); a reply to it goes through the existing confirmation path, where a
        clear yes reaches the human path (owner ring as before). Past the budget: silence (round 39)."""
        now = time.monotonic()
        if self._question_pending():  # round 33: one reply-window check
            if self._replies_to_question(utt) and self._is_clear_yes(text, utt):  # a clear yes to our question
                self._event("live_person_confirmed_by_reply")
                self.yes_confirmed = True
                await self._on_human(text, confirmed=True, utt=utt)
                return
            self._event("new_party_unclear_reply")  # no re-ask inside the 20 s budget: listen
            return
        if self._question_budget(utt) == "budget":
            # Round 39: past the budget, silence. Never an assistant phrase on possible_new_party (Delta run 3:
            # "Representative."/"Agent." went into the hold queue five times from here).
            self._event("new_party_question_skipped", why="budget")
            return
        if now - self.last_question_sent >= self.NEW_PARTY_QUESTION_EVERY_S:
            def sent_note() -> None:  # the count itself is the global one in the send path (N31)
                self._event("new_party_question", count=self.questions_sent)
            first = self.last_confirm == -math.inf
            await self._ask_live_person(self.cfg.confirm_question() if first else
                                        "Sorry, am I speaking with a live person?", on_sent=sent_note, utt=utt)
            return
        self._event("new_party_question_skipped", why="spacing")  # at most one question every 20 s: listen

    async def _assistant_reply(self, text: str, state: str) -> None:
        """Round 25: one fixed phrase, chosen by rule (assistant.choose), never model text. Round 39: only while the
        latest label is ``automated_assistant`` and not paused by hold; never on possible_new_party, unknown,
        hold_message or hold_music."""
        from . import assistant
        if self.terminal or self.state not in PRE_HUMAN or self.human_confirmed:
            return
        latest = getattr(getattr(self, "last_detection", None), "label", None)
        if state != "automated_assistant" or latest != "automated_assistant":
            self._event("ivr_action_skipped", state=state, why="the latest label is not automated_assistant")
            return
        if self.phrases_paused:
            self._event("ivr_action_skipped", state=state, why="hold: paused until a new automated prompt")
            return
        said = [pid for _, pid in self.assistant_said]
        category = (getattr(self, "options", None) or {}).get("ivr_category")
        phrase_id = assistant.choose(text, said, category)
        if phrase_id is None:
            self._event("ivr_action_skipped", state=state, why="callback, payment or change wording")
            return
        if len(self.assistant_said) >= assistant.MAX_PHRASES:
            self._event("ivr_action_skipped", state=state, why="phrase limit reached")
            return
        now = time.monotonic()
        # N27: spacing from actual send times. A phrase still pending in the gate is superseded by this newer line
        # (its own task sees the newer utterance and drops it), so nothing is left waiting with nothing to say.
        if self.assistant_said and now - self.assistant_said[-1][0] < assistant.MIN_SPACING_S:
            self._event("ivr_action_skipped", state=state, why="spacing")
            return
        if phrase_id == "category" and category not in assistant.CATEGORIES:
            phrase_id = "speak_agent"
        pending = {"phrase_id": phrase_id, "state": state}
        self.assistant_pending = pending
        if getattr(self.cfg, "quiet_before_speak_s", 0.0) > 0:
            self._spawn(self._phrase_now(pending, category))
            return
        await self._phrase_now(pending, category)

    async def _phrase_now(self, pending: dict, category: Optional[str]) -> None:
        from . import assistant
        phrase_id = pending["phrase_id"]

        def still_ours() -> bool:
            from . import assistant as a
            last = self.assistant_said[-1][0] if self.assistant_said else -math.inf
            return (self.assistant_pending is pending and self.state in PRE_HUMAN and not self.human_confirmed
                    and len(self.assistant_said) < a.MAX_PHRASES and time.monotonic() - last >= a.MIN_SPACING_S)
        sent = await self.speak(assistant.phrase_text(phrase_id, category), supersede=True, guard=still_ours)
        if self.assistant_pending is pending:
            self.assistant_pending = None
        if not sent:
            return  # nothing is spent
        self.assistant_said = tuple(self.assistant_said) + ((time.monotonic(), phrase_id),)  # the actual send time
        self._event("ivr_action", state=pending["state"], phrase_id=phrase_id)
        self.disclosed = True

    async def _late_detection(self, detecting: "asyncio.Task", text: str,
                              utt: Optional[SpokenUtterance] = None) -> None:
        try:
            detection = await detecting
        except Exception:
            return
        if self.terminal or self.state not in PRE_HUMAN:
            return
        if getattr(detection, "label", "unknown") in ("human", "voicemail", "ivr", "hold_message", "hold_music",
                                                      "automated_assistant"):
            await self._on_detection(detection, text, late=True, utt=utt)  # round 34: the original stamp

    async def _on_detection(self, detection: Any, text: str, *, late: bool = False,
                            utt: Optional[SpokenUtterance] = None) -> None:
        label = getattr(detection, "label", "unknown")
        self._event("detect", label=label, confidence=str(getattr(detection, "confidence", "")),
                    **({"late": True} if late else {}))
        self.last_detection = detection
        self.last_menu = text if label == "ivr" else None
        if label == "ivr":
            self.menu_seq += 1  # N16-C: a new menu; one key may be sent for it
        if label not in ("hold_message", "hold_music") and self.state is not S.ON_HOLD and not late:
            # rounds 39-41: outside-hold speech, marked by its END (the final's arrival), never by its start
            if utt is None:
                mark = time.monotonic()
            else:
                mark = utt.ended_at if utt.ended_at is not None else utt.started_at
            self.unheld_marks = (self.unheld_marks + (mark,))[-2:]
        if label == "automated_assistant" and self.phrases_paused:
            self.phrases_paused = False  # round 39: a new automated prompt after hold
            self._event("assistant_phrases_resumed")
        if label == "automated_assistant" and not self.had_bot_label:
            self.had_bot_label = True
            self._event("bot_label_seen")
        if label == "automated_assistant" and self.restrictions and not self.assistant_declared:
            self.assistant_declared = True  # no live-person question to a known bot (L2 round 5 contract)
            self._event("assistant_declared", by="detector")
        if not late and await self._question_reply(text, utt, label):
            return  # round 43c: a reply to our live-person question is decided first
        if label in ("automated_assistant", "ivr", "hold_music") or (
                label == "hold_message" and not self._replies_to_question(utt)):
            self.party_questions = 0  # round 43c: a bot, a menu or the queue: the next voice is a new party
        state = self._assistant_state(label)
        if state is not None:  # round 25: an automated assistant gets a fixed phrase; Hermes is not asked
            await self.set_state(S.IVR, "automated assistant")
            await self._assistant_reply(text, state)
            return
        if label == "automated_assistant":
            if self.restrictions or self.state not in PRE_HUMAN:
                return  # a restricted task speaks to a bot only through the fixed phrases above
            label = "unknown"  # unrestricted tasks: as before L2 round 5 (the live-person question decides)
        if label == "voicemail":
            await self._on_voicemail(text)
        elif label == "ivr":
            await self.set_state(S.IVR, "ivr prompt")
            # Round 16: L2's evidence (e.g. "callback offer requires task authorization") goes with the menu text,
            # and the task's enforced restrictions with it.
            evidence = [str(e) for e in getattr(detection, "evidence", ()) or () if str(e) != text][:3]
            notes = "".join(f"\n[system] Detector: {mask_text(e)[:200]}" for e in evidence)
            from .task_rules import describe
            rules = describe(self.restrictions)
            if self.info_only:
                from .fallback import INFO_ONLY_RULE
                rules = f"{rules} {INFO_ONLY_RULE}".strip()
            self.start_turn(f"[phone menu] {text}{notes}\n[system] This is an automated phone menu. To press keys call "
                            "hermes_line_call_press_digits. If the menu asks you to speak, reply with only the short "
                            "phrase to say. Otherwise reply with only WAIT." + (f"\n[system] {rules}" if rules else ""),
                            spoken=False)
        elif label in ("hold_message", "hold_music") and self._replies_to_question(utt) \
                and self._is_clear_yes(text, utt):
            # Round 39: a clear yes to our pending question (an utterance started after it) is the confirmation,
            # whatever the label: L2 round 10 labels "Yes, I'm a live person." on a hold queue as hold_message.
            self._event("live_person_confirmed_by_reply", label=label)
            self.yes_confirmed = True
            await self._on_human(text, confirmed=True, utt=utt)
        elif label in ("hold_message", "hold_music"):  # round 25: L2's hold_music is a silent hold too
            await self.set_state(S.ON_HOLD, "hold message" if label == "hold_message" else "hold music")
        elif label == "human" and self._strict_confirmation():
            await self._bot_call_human(text, utt)  # rounds 29-30: our question and a shared clear yes, nothing else
        elif label == "human" and not self._after_question(utt):
            self._event("human_label_not_confirmed", why="the utterance started before our question")
        elif label == "human":
            await self._on_human(text, confirmed=True, utt=utt)
        elif self._possible_new_party(detection):
            await self._new_party_check(text, utt)
        elif not late:
            await self._maybe_confirm(text, utt=utt)

    async def _detect(self, masked: str, seq: int):
        utt = deps.Utterance(text=masked[:MAX_PROMPT], at_ms=self._elapsed_ms(), speaker="business", last=True,
                             turn_id=f"{self.call_id}:{seq}")
        context = {"task": self.title, "state": self.state.value, "mode": self.mode.value}
        try:
            return await self._in_ask_worker(self.tracker.update, utt, context,
                                             timeout=self.cfg.ask_timeout_s + 10)
        except (asyncio.TimeoutError, Exception):
            return deps.Detection("unknown", "low", ("detector timeout",))

    async def _maybe_confirm(self, text: str, *, force: bool = False, utt: Optional[SpokenUtterance] = None) -> None:
        """An unclear utterance before a person is confirmed. A reply to our live-person question always gets
        an answer (round 7: the first live call went silent here for 39 s): a re-ask, and after a second
        unclear reply Hermes talks to them. Unprompted speech (recordings) keeps the cooldown."""
        if not _words(text):
            return
        now = time.monotonic()
        first = self.last_confirm == -math.inf
        replying = not first and self._replies_to_question(utt)
        if self.assistant_declared and not force and not replying:
            return  # round 25: no live-person question to a known bot (round 27: unless L2 says possible new party);
            # a reply to a question we did ask still goes through the confirmation below
        if replying:
            if self._is_clear_yes(text, utt):  # round 16: a clear yes to our question is a confirmed person
                self._event("live_person_confirmed_by_reply")
                self.yes_confirmed = True
                await self._on_human(text, confirmed=True, utt=utt)
                return
            self.unclear_replies += 1
            if self.unclear_replies >= 2:
                if self.mode is Mode.OWNER or self._strict_confirmation():  # never assumed (rounds 29-30)
                    # Round 16 (hold-trial precondition 2): never ring the owner on an assumption. Stay quiet and
                    # listening; only an L2 HUMAN verdict or a clear yes rings.
                    if self.unclear_replies == 2:
                        self._event("human_not_assumed", replies=self.unclear_replies)
                    return
                self._event("human_assumed", replies=self.unclear_replies)
                await self._on_human(text, note="[system] They answered the live-person question but it is not "
                                                "certain they are a person. If it sounds like a recording, say so "
                                                "briefly and wait.")
                return
            question = REASK_LINE
        elif not first and not force and now - self.last_confirm < self.cfg.confirm_cooldown_s:
            return
        else:
            question = self.cfg.confirm_question() if first else "Sorry, am I speaking with a live person?"
        await self._ask_live_person(question, utt=utt)

    async def _ask_live_person(self, question: str, on_sent=None, *, utt: Optional[SpokenUtterance] = None,
                               reask: bool = False) -> None:
        """The fixed live-person question (round 7), noted to L2 as Hermes's turn. It never blocks the frame reader
        (round 31: its own task when the quiet gate is on); ``on_sent`` runs only when it was actually sent.
        Round 32 (N29): one slot. While a question is pending or in flight, another request joins it (no second
        send); its budget callback is kept once and runs when that one question is sent."""
        # Round 34: an utterance that started before our latest question was sent already had its question
        why = "asked for this utterance" if utt is not None and not self._after_question(utt) else \
            self._question_budget(utt, reask=reask)
        if why is not None:  # round 33 (N31): one global budget, every route
            self._event("question_budget_skip", why=why)
            return
        slot = self.question_pending
        if slot is not None and slot["epoch"] == self.business_epoch:  # same utterance: join it
            if on_sent is not None and slot.get("on_sent") is None:
                slot["on_sent"] = on_sent
            self._event("question_joined")
            return
        # (a slot from an older utterance is being superseded: its task drops it; this one takes the slot)
        slot = {"on_sent": on_sent, "epoch": self.business_epoch, "utt": utt, "reask": reask}
        self.question_pending = slot
        if getattr(self.cfg, "quiet_before_speak_s", 0.0) > 0:
            self._spawn(self._ask_now(question, slot))
            return
        await self._ask_now(question, slot)

    @property
    def last_unheld_speech_at(self) -> float:
        """Round 39's name: the start of the latest business final outside hold."""
        return self.unheld_marks[-1] if self.unheld_marks else -math.inf

    @last_unheld_speech_at.setter
    def last_unheld_speech_at(self, value: float) -> None:
        self.unheld_marks = (value,)

    def _question_budget(self, utt: Optional[SpokenUtterance] = None, reask: bool = False) -> Optional[str]:
        """Rounds 33 and 39: on strict calls, the live-person question has one global budget, counted in the send path:
        one every NEW_PARTY_QUESTION_EVERY_S (20 s); NEW_PARTY_QUESTIONS_MAX (4) per hold segment; at most
        QUESTIONS_PER_CALL_MAX (12) per call. None when one may go."""
        if not self._strict_confirmation():
            return None
        if self.questions_sent >= self.QUESTIONS_PER_CALL_MAX:
            return "budget"
        now = time.monotonic()
        if now - self.last_question_sent < self.NEW_PARTY_QUESTION_EVERY_S and not reask:
            return "spacing"  # round 43c: the one re-ask to a human-like reply is exempt (the caps still count)
        if self.segment_questions >= self.NEW_PARTY_QUESTIONS_MAX and not self._new_hold_segment(now, utt):
            return "budget"
        return None

    def _new_hold_segment(self, now: float, utt: Optional[SpokenUtterance] = None) -> bool:
        """Rounds 39-41: a new segment, with a fresh per-segment budget, needs a qualifying HOLD_SEGMENT_S interval of
        hold or no business speech that lies entirely BEFORE the current utterance began: its start minus the later of
        the last question's send and the END of the last outside-hold speech (speech heard while on hold, or labelled
        hold_message/hold_music, never marks). Outside-hold speech only delays renewal (R1b delta 36 B1), and neither
        the current utterance's own duration (a 110 s recording) nor a previous utterance's start counts as silence
        (R1b delta 37 B2). The current start is ``utt.started_at``; without an utterance (the stall question) the
        pending partial's start, else ``now``. The same ``utt`` is used at the send guard and reset, so processing
        time never creates an interval."""
        if self.last_question_sent == -math.inf:
            return False
        if utt is not None:
            start = utt.started_at
        else:
            start = self.utt_first_partial_at if self.utt_first_partial_at is not None else now
        before = [m for m in self.unheld_marks if m < start]
        since = max(self.last_question_sent, before[-1] if before else -math.inf)
        return start - since >= self.HOLD_SEGMENT_S

    async def _ask_now(self, question: str, slot: dict) -> None:
        try:
            sent = await self.speak(question, supersede=True,
                                    guard=lambda: (self.state in PRE_HUMAN and not self.human_confirmed
                                                   and self._question_budget(slot.get("utt"), reask=slot.get("reask", False)) is None))
        finally:
            if self.question_pending is slot:
                self.question_pending = None  # the slot frees on send, drop or supersede
        if not sent:
            return  # dropped or superseded: nothing is spent
        now = time.monotonic()
        self.last_confirm = now
        if self._new_hold_segment(now, slot.get("utt")):
            self.segment_questions = 0
            self._event("question_segment_started")
        self.questions_sent += 1  # N31: counted here, when the frame is actually sent
        self.segment_questions += 1
        self.party_questions += 1  # round 43c
        self.last_question_sent = now
        self._event("live_person_question_sent", count=self.questions_sent)
        if slot.get("on_sent") is not None:
            slot["on_sent"]()
        if self.voice is not None:
            self.voice.prewarm()  # acts only when voice.prewarm_s > 0
        self.disclosed = True
        utt = deps.Utterance(text=question, at_ms=self._elapsed_ms(), speaker="hermes", last=True,
                             turn_id=f"{self.call_id}:q{int(now * 1000)}")
        # queued behind any detection still running, never awaited: the next utterance must not wait for it
        self._spawn(self._in_ask_worker(self.tracker.update, utt, None))

    def _enter_phase(self, phase: str) -> None:
        """N17: a person answered, the fixed script starts or the owner joined. Any turn in flight (a menu reply in
        particular) belongs to the earlier phase: it is marked stale, its generation is superseded and Hermes is
        interrupted, so nothing it produces later is said or done."""
        self.gen += 1
        self.phase_epoch += 1  # N17-R: turns queued before this moment are stale too, not only the active one
        ctx = self.current_turn
        if ctx is not None and not ctx.stale:
            ctx.stale = True
            self._event("stale_turn_cancelled", phase=ctx.phase, entering=phase)
            if self.driver is not None:
                self.driver.interrupt(None)

    def stale_turn(self, tool: str) -> Optional[dict]:
        """N17: the refusal for a tool call from a turn of an earlier phase, else None."""
        # N17-T: the originating turn is the one whose worker is running (there is one worker thread, so a tool call
        # in flight comes from it), even after that turn timed out and stopped being the current turn.
        ctx = self.worker_turn or self.current_turn
        if ctx is not None and ctx.over(self.phase_epoch):
            self._event("stale_turn_blocked", tool=tool, phase=ctx.phase, timed_out=ctx.timed_out)
            return {"ok": False, "error": "that turn is over: the call has moved on, so nothing was done"}
        return None

    async def _on_human(self, text: str, *, note: Optional[str] = None, confirmed: bool = False,
                        utt: Optional[SpokenUtterance] = None) -> None:
        if confirmed:
            utt = utt if utt is not None else SpokenUtterance(text, time.monotonic())
            if not self._after_question(utt):  # round 34: the one stamp rule, at the point of confirmation
                self._event("confirmation_refused", why="the utterance started before our live-person question")
                return
            self.human_confirmed = True  # round 16: the only state in which an outbound call may ring the owner
            self.confirmed_utt = utt
        if not await self.set_state(S.HUMAN_DETECTED, "live person confirmed"):
            return
        if self.human_started:
            return  # round 32 (N29): the connecting line and the owner ring run once
        self.human_started = True
        self._enter_phase("human")  # N17: a pending menu reply never reaches the person
        if self.mode is Mode.OWNER:
            hold = CONNECTING_LINE.format(owner=self.cfg.owner_name)
            await self.speak(hold)
            # Round 17: the owner is still on their own call to the line: join that leg, no new dial. Otherwise
            # (the owner hung up, or no live bridge) ring the owner back as before.
            if self.parent is not None and await self.svc.bridge_live_owner(self, delay=self.speech_wait(hold)):
                return
            if not await self.ring_owner("human_detected"):
                await self.owner_unavailable()
            return
        await self.set_state(S.AGENT_TALKING, "agent completes the task")
        if self.grok_talks:
            await self._begin_voice_conversation(f"A live person is on the line now. They said: {text}")
            return
        self.start_turn(f"{note or '[system] A live person is on the line now.'}\n[business] {text}")

    async def _on_voicemail(self, text: str) -> None:
        self._event("voicemail")
        if self.task is not None and self.task.leave_voicemail:
            self.left_voicemail = True
            self.start_turn(f"[voicemail greeting] {text}\n[system] This is voicemail. After the tone, leave one "
                            "short message: who you are, why you called, then call hermes_line_call_finish with "
                            "outcome callback_expected.")
            return
        await self.hang_up(EndReason.VOICEMAIL, state=S.ENDED)

    def _on_interrupt(self, spoken: Optional[str]) -> None:
        self.gen += 1
        self._event("barge_in")
        driver = self.driver
        if driver is not None:
            driver.interrupt(spoken)

    async def _quiet_gate(self, supersede: bool, guard=None) -> bool:
        """Round 31 (N25/N26): the one quiet gate. Wait until ``quiet_before_speak_s`` has passed since the last business
        activity (each partial extends it by at most that much). After QUIET_CAP_S of waiting, the next QUIET_CAP_GAP_S
        gap is enough, and at QUIET_HARD_S the line goes anyway: no line waits forever. ``supersede``: drop the line if
        a newer final business utterance arrived (it gets its own decision). ``guard``: re-checked before sending."""
        quiet = getattr(self.cfg, "quiet_before_speak_s", 0.0)
        if quiet > 0:
            start, epoch = time.monotonic(), self.business_epoch
            while True:
                if self.terminal or (supersede and self.business_epoch != epoch):
                    return False
                now = time.monotonic()
                gap, waited = now - self.last_business_activity, now - start
                if (gap >= quiet if waited < self.QUIET_CAP_S
                        else gap >= self.QUIET_CAP_GAP_S or waited >= self.QUIET_HARD_S):
                    break
                await asyncio.sleep(0.05)
        return not self.terminal and (guard is None or bool(guard()))

    async def speak(self, text: str, *, supersede: bool = False, guard=None, opening: bool = False) -> bool:
        """A fixed line from the plugin (not the model). Round 31 (N25): every line after the opening disclosure goes
        through the one quiet gate. True when the line was sent."""
        if self.terminal:
            return False
        if not opening and not await self._quiet_gate(supersede, guard):
            self._event("speech_superseded", what=text[:40])
            return False
        self._send({"type": "text", "token": text, "last": True})
        self._transcript(Speaker.HERMES, text)
        if self.driver is not None:
            self._spawn(self._in_worker(self.driver.note, f"I said: {text}"))
        return True

    # -- Hermes turns --------------------------------------------------------
    def start_turn(self, user_text: str, *, spoken: bool = True) -> asyncio.Task:
        prev = self.turn_task
        if prev is not None and not prev.done():
            self._on_interrupt(None)  # the other side spoke over us: barge-in
        task = self._spawn(self._run_turn(user_text, prev, spoken, self.phase_epoch))  # N17-R: epoch at scheduling
        self.turn_task = task
        task.add_done_callback(self._turn_done)
        return task

    def _turn_done(self, task: asyncio.Task) -> None:
        ctx = self.current_turn
        if ctx is not None and ctx.task is task:
            self.current_turn = None

    async def _run_turn(self, user_text: str, prev: Optional[asyncio.Task], spoken: bool, epoch: int = 0) -> None:
        if prev is not None:
            try:
                await prev
            except BaseException:
                pass
        if self.terminal or self.handoff_pending:
            return
        if epoch != self.phase_epoch:  # N17-R: queued before the call moved on; it never starts
            self._event("stale_turn_dropped", phase="talk" if spoken else "menu", queued=True)
            return
        self.gen += 1
        gen = self.gen
        ctx = TurnCtx(gen, "talk" if spoken else "menu", asyncio.current_task(), epoch)
        self.current_turn = ctx
        buffered: list[str] = []
        gate = None
        if spoken and self.note_offered:  # round 16 (b): one note offer per call unless the caller asks
            from .note_offers import REMINDER, SentenceGate, caller_asked
            said = "\n".join(line for line in user_text.splitlines() if line.startswith("[business]"))
            if not caller_asked(said):
                gate = SentenceGate()
                user_text = f"{user_text}\n{REMINDER}"

        spoke = {"any": False}

        def emit(chunk: str) -> None:  # worker thread
            if chunk.strip():
                spoke["any"] = True
            self.loop.call_soon_threadsafe(self._emit_chunk, gen, chunk, spoken, buffered, gate, ctx)

        filler = self._spawn(self._filler(gen, spoke)) if spoken else None

        def run() -> Any:  # worker thread
            self.worker_turn = ctx
            try:
                driver = self._ensure_driver()
                if driver is None:
                    return None
                return driver.turn(user_text, emit)
            finally:
                if self.worker_turn is ctx:
                    self.worker_turn = None

        try:
            result = await self._in_worker(run, timeout=self.cfg.turn_timeout_s)
        except asyncio.TimeoutError:
            ctx.timed_out = True  # N17-T: its worker may still deliver a tool call or chunk later; both are refused
            if self.driver is not None:
                self.driver.interrupt(None)
            result = None
            self._event("turn_timeout")
        except Exception as exc:
            result = None
            self._event("turn_error", error=type(exc).__name__)
        finally:
            if filler is not None:
                filler.cancel()
        await asyncio.sleep(0)  # let queued emit callbacks run
        if self.terminal:
            return
        if ctx.stale or ctx.epoch != self.phase_epoch:  # N17: an earlier phase's turn: nothing said, recorded, done
            self._event("stale_turn_dropped", phase=ctx.phase)
            return
        if result is None:
            await self._agent_unavailable()
            return
        text = (result.text or "").strip()
        if gate is not None:
            rest = gate.flush()
            if gen == self.gen and not self.handoff_pending:
                if rest:
                    self._send({"type": "text", "token": rest, "last": False})
                elif not gate.kept and text:
                    self._send({"type": "text", "token": "Okay.", "last": False})  # never silence
            if gate.dropped:
                self._event("note_offer_dropped", sentences=gate.dropped)
                self._spawn(self._in_worker(self._note, "The line did not say your repeated offer to take a note: "
                                                        "the caller has heard it once."))
            text = gate.text() or ("Okay." if text else "")
        if spoken:
            if gen == self.gen:
                self._send({"type": "text", "token": "", "last": True})
            if text:
                self._transcript(Speaker.HERMES, text + (" …" if result.interrupted else ""))
            if not self.note_offered:
                from .note_offers import contains_offer
                if contains_offer(text):
                    self.note_offered = True
                    self._event("note_offered")
        else:
            phrase = "".join(buffered).strip()
            pressed = self.digits_gen == gen
            blocked = False
            if phrase.lower().strip(" .!") not in WAIT_WORDS and gen == self.gen and not pressed:
                why = self._menu_output_refused("speech", phrase)  # N16: the final gate, at the moment of sending
                blocked = why is not None
            if phrase.lower().strip(" .!") not in WAIT_WORDS and gen == self.gen and not pressed and not blocked:
                self._send({"type": "text", "token": phrase, "last": True})
                self._transcript(Speaker.HERMES, phrase)
        if result.error:
            self._event("turn_error", error=str(result.error)[:80])
        self._event("turn", first_text_ms=result.first_text_ms, full_ms=round(result.full_ms, 1),
                    interrupted=result.interrupted)
        await self._after_turn(text)

    async def _filler(self, gen: int, spoke: dict) -> None:
        """Round 7: while a spoken turn has produced nothing yet, a short fixed line every reply_budget_s, so
        the person never hears more than that of silence (at most three, then the turn timeout applies)."""
        for line in FILLER_LINES:
            await asyncio.sleep(self.cfg.reply_budget_s)
            if spoke["any"] or gen != self.gen or self.terminal or self.handoff_pending:
                return
            self._send({"type": "text", "token": line, "last": True})
            self._event("filler")

    def _emit_chunk(self, gen: int, chunk: str, spoken: bool, buffered: list, gate: Any = None,
                    ctx: Optional[TurnCtx] = None) -> None:
        if gen != self.gen or self.terminal or self.handoff_pending:
            return
        ctx = ctx or self.current_turn
        if ctx is not None and ctx.gen == gen and ctx.over(self.phase_epoch):
            return  # N17, N17-T: the originating turn is stale or timed out
        if gate is not None:  # round 16 (b): whole sentences only, a repeated note offer is not said
            chunk = gate.feed(chunk)
            if not chunk:
                return
        if spoken:
            self._send({"type": "text", "token": chunk, "last": False})
        else:
            buffered.append(chunk)

    # -- owner line mode (round 13) --------------------------------------------------------------
    async def _pin_key(self, key: str) -> None:
        if key == "#":
            if self._pin_keys:  # a lone # is not an attempt
                await self._pin_attempt("".join(self._pin_keys))
            return
        if not key.isdigit():
            return
        self._pin_keys.append(key)
        if self._pin_timer is not None:
            self._pin_timer.cancel()
        if len(self._pin_keys) >= 8:
            await self._pin_attempt("".join(self._pin_keys))
            return
        from .owner_line import DTMF_PAUSE_S
        self._pin_timer = self._spawn(self._pin_pause(DTMF_PAUSE_S))

    async def _pin_pause(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if self.pin_phase and self._pin_keys:
            await self._pin_attempt("".join(self._pin_keys))

    async def _pin_attempt(self, entered: str) -> None:
        from .owner_line import MAX_PIN_FAILURES, pin_matches
        self._pin_keys = []
        timer, self._pin_timer = self._pin_timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()  # R1 delta 10 N15: never the pause timer that is running this attempt
        pin = self.svc.owner_pin()
        locked = self.svc.owner_mode_lock()  # N13: while locked, nothing is compared
        ok = locked is None and pin is not None and pin_matches(entered, pin)
        entered = ""
        if ok:
            self.svc.store.owner_auth_success(self.cfg.line_key)
            self.pin_phase = False
            self.owner_verified = True
            self._event("owner_verified")
            self._transcript(Speaker.SYSTEM, f"{self.cfg.owner_name} verified with the PIN")
            await self.speak(f"Thanks, {self.cfg.owner_name}. How can I help?")
            self._spawn(self._in_worker(self._note, OWNER_MODE_NOTE.format(owner=self.cfg.owner_name)))
            return
        self.pin_failures += 1
        self._event("owner_pin_failed", attempt=self.pin_failures)
        self._transcript(Speaker.SYSTEM, "PIN not accepted")
        if locked is None:
            locked = self.svc.owner_pin_failed(self)
        if self.pin_failures < MAX_PIN_FAILURES and locked is None:
            await self.speak("Sorry, that didn't work. Please try again.")  # no hint about which part was wrong
            return
        self.pin_phase = False
        self._event("owner_pin_locked")
        line = "Sorry, I can't verify that. Goodbye."
        await self.speak(line)
        # N15: the hang-up runs in its own task, so cancelling whatever triggered the attempt cannot stop it
        self._spawn(self._goodbye_then_hang_up(line))

    async def _goodbye_then_hang_up(self, line: str) -> None:
        await asyncio.sleep(self.speech_wait(line))
        await self.hang_up(EndReason.COMPLETED, state=S.ENDED)

    async def owner_new_task(self, destination: str, title: str, goal: str, bring_owner: str = "") -> dict:
        """The verified owner asks Hermes to call a business (in-call tool). A normal task through the destination
        gate; the line reads it back after this turn and only the owner's yes to that read-back approves it."""
        stale = self.stale_turn("owner_new_task")  # N17
        if stale is not None:
            return stale
        if not self.owner_verified or self.terminal:
            return {"ok": False, "error": "only for the owner, after their PIN, on this call"}
        if self.child is not None or self.bridged:  # round 17: one child per call
            return {"ok": False, "error": "the line is already on a call for the owner; one call at a time"}
        from .owner_line import spoken_number
        try:
            out = self.svc.request_call(destination=destination, title=title, goal=goal,
                                        mode="owner" if bring_owner else "agent")
        except GateRejected as exc:
            return {"ok": False, "error": f"{exc.gate}: {exc.reason}"}
        task = self.store.get_task(out["task_id"])
        from .owner_line import CONFIRM_WINDOW_S, last_digits
        if self.proposal is not None:  # replaced, never approved
            self._invalidate_confirm("replaced", note=False)
        # R1 delta 12 (suffix collision): four digits only for the owner's own number or a number of an earlier
        # approved task; any other destination needs its last seven digits.
        known = (task.destination_e164 == self.cfg.owner_number
                 or self.store.destination_previously_approved(task.destination_e164, exclude_task=task.id))
        count = 4 if known else 7
        self.confirm_gen += 1
        self.proposal = {"task_id": task.id, "e164": task.destination_e164, "gen": self.confirm_gen,
                         "code": last_digits(task.destination_e164, count),
                         "expires": time.monotonic() + CONFIRM_WINDOW_S}
        self._confirm_keys = []
        self._confirm_timer = self._spawn(self._confirm_timeout(self.confirm_gen, CONFIRM_WINDOW_S))
        live = self.svc.live_bridge_possible(self, task)
        self.proposal["live"] = live
        if live:  # round 17: dial now; the owner stays on this call and is joined when a person answers
            plan = "now, and connect you on this call when a person answers"
        elif task.mode is Mode.OWNER:
            plan = "and ring you when a person answers, after you hang up"
        else:
            plan = "after you hang up"
        self._readback = (f"To confirm: I'll call {spoken_number(task.destination_e164)} about {task.title} {plan}. "
                          f"To confirm, key the last {'four' if count == 4 else 'seven'} digits of the number, "
                          "then pound.")
        self._event("owner_request", task_id=task.id, to=task.destination_masked)
        return {"ok": True, "note": "The line now reads the request back; the owner confirms it on the keypad. Do not "
                                    "repeat it and do not say anything else in this reply."}

    def _invalidate_confirm(self, why: str, *, note: bool = True) -> None:
        """N12: the request is no longer confirmable (wrong digits, timeout, replacement). Its task stays awaiting
        approval (the owner can still approve it in chat); nothing is approved."""
        had = self.proposal
        self.proposal = None
        self._readback = None
        self._confirm_keys = []
        self.confirm_gen += 1
        timer, self._confirm_timer = self._confirm_timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        if had is not None:
            self._event("owner_confirm_invalidated", why=why)
            if note:
                self._spawn(self._in_worker(self._note, f"The keypad confirmation for that call was not given ({why}); "
                                                        f"the call is not approved. {self.cfg.owner_name} can ask "
                                                        "again or approve it in chat."))

    async def _confirm_key(self, key: str) -> None:
        """N12: keypad confirmation. Digits then pound; only the destination's last four digits approve."""
        if key == "#":
            if self._confirm_keys:
                await self._confirm_check("".join(self._confirm_keys))
            return
        if key.isdigit():
            self._confirm_keys.append(key)
            if len(self._confirm_keys) > 8:
                await self._confirm_check("".join(self._confirm_keys))

    async def _confirm_check(self, entered: str) -> None:
        import hmac
        proposal = self.proposal
        self._confirm_keys = []
        if proposal is None:
            return
        if time.monotonic() > proposal["expires"]:
            self._invalidate_confirm("timed out")
            await self.speak("That confirmation timed out, so I won't make the call. You can approve it in chat.")
            return
        if not hmac.compare_digest(entered.encode(), proposal["code"].encode()):
            self._invalidate_confirm("wrong digits")
            await self.speak("Those digits don't match, so I won't make that call. You can approve it in chat.")
            return
        self.proposal = None
        self.confirm_gen += 1
        timer, self._confirm_timer = self._confirm_timer, None
        if timer is not None:
            timer.cancel()
        task = self.store.get_task(proposal["task_id"])
        live = bool(proposal.get("live")) and self.svc.live_bridge_possible(self, task)
        self._event("owner_confirm_keyed", **({"live_bridge": True} if live else {}))
        if live:  # round 17
            await self.speak("Confirmed. I'm calling now. Stay on the line and I'll connect you when a person "
                             "answers; if you hang up, I'll ring you back instead. Press star to cancel the call.")
            self.svc.voice_approve(self, proposal["task_id"], proposal["e164"], live=True)
            self._spawn(self._in_worker(self._note, f"{self.cfg.owner_name} confirmed on the keypad; that call is "
                                                    f"being dialled now while {self.cfg.owner_name} stays on this "
                                                    "call. The line connects them when a person answers. Keep "
                                                    "talking with them meanwhile; you cannot start another "
                                                    "call. To stop it, they press star."))
            return
        self.svc.voice_approve(self, proposal["task_id"], proposal["e164"])
        await self.speak("Confirmed. I'll call as soon as you hang up, and send you the summary.")
        self._spawn(self._in_worker(self._note, f"{self.cfg.owner_name} confirmed on the keypad; that call is approved "
                                                "and will be dialled after this call ends."))

    async def _confirm_timeout(self, gen: int, delay: float) -> None:
        await asyncio.sleep(delay)
        if self.proposal is not None and self.proposal["gen"] == gen and not self.terminal:
            self._invalidate_confirm("timed out")
            await self.speak("That confirmation timed out, so I won't make the call. You can approve it in chat.")

    async def _after_turn(self, text: str) -> None:
        if self._readback is not None and self.proposal is not None:  # round 13: the plugin's own read-back
            line, self._readback = self._readback, None
            await self.speak(line)  # N12: ends with the keypad challenge; no timing is trusted
        if self.finish_outcome is not None and not self.end_request:
            await asyncio.sleep(self.speech_wait(text))
            await self.hang_up(EndReason.COMPLETED, state=S.ENDED)
            return
        if self.owner_requested:
            self.owner_requested = False
            if not await self.ring_owner("agent_request"):
                self._spawn(self._in_worker(self._note, f"{self.cfg.owner_name} could not be rung. Continue yourself."))

    def _note(self, text: str) -> None:
        if self.driver is not None:
            self.driver.note(text)

    async def _agent_unavailable(self) -> None:
        self._event("agent_unavailable", error=self.driver_error or "turn failed")
        if self.state in (S.AGENT_TALKING, S.HUMAN_DETECTED):
            line = "I'm sorry, I'm having trouble on my end. We will call back. Goodbye."
            await self.speak(line)
            await asyncio.sleep(self.speech_wait(line))
            await self.hang_up(EndReason.PROVIDER_ERROR, state=S.FAILED)

    # -- in-call tools (called from the worker thread via the service) -------
    MENU_STATES = frozenset({S.DIALING, S.IVR, S.ON_HOLD, S.AGENT_TALKING})

    def menu_keys_allowed(self) -> bool:
        if self.info_only and self.state not in PRE_HUMAN:
            return False  # round 16 (c) / N17: no key once a person answered an information-only call
        return self._menu_keys_allowed()

    def _menu_keys_allowed(self) -> bool:
        """The menu gate: phone keys may reach the business leg only while Hermes holds it (no owner ringing
        or hand-off, not ending). Used for presses and for the delayed DTMF-fallback webhook (R1 delta 2 N2)."""
        return (not self.terminal and self.state in self.MENU_STATES and not self.handoff_pending
                and self.end_request is None and not self.owner_sid)

    def _callback_offer(self) -> bool:
        evidence = getattr(self.last_detection, "evidence", ()) or ()
        return getattr(self.last_detection, "label", "") == "ivr" and any(
            str(e).startswith("callback offer") for e in evidence)

    def _menu_output_refused(self, via: str, value: str) -> Optional[str]:
        """R1 delta 13 N16: the one final output gate for a restricted task's menu actions, for speech and keys
        alike, run at the moment of sending. Only positively identified safe actions pass (menu_gate.py); a
        refusal sends nothing and logs ``restricted_action_blocked``."""
        if not self.restrictions:
            return None
        from . import menu_gate
        names = tuple((getattr(self, "options", None) or {}).get("intro_names") or ())  # N16-G: the approved names
        callback_offer = menu_gate.is_callback_offer(self.last_menu, self._callback_offer(),
                                                     menu_gate.normalise_names(names))
        info_only = bool(getattr(self, "info_only", False))
        # Round 23 (R1b delta 21 N16-I): whole-menu silence is the first check for every output path (keys, menu
        # speech, generic safe words); while a silenced menu is current, nothing at all is sent.
        why = menu_gate.menu_problem(self.last_menu, callback_offer, menu_gate.normalise_names(names))
        if why is None and via == "dtmf":
            why = menu_gate.check_keys(value, self.last_menu, callback_offer, info_only=info_only, intro_names=names)
            if why is None and self.menu_key_sent == self.menu_seq:  # N16-C: one key per current menu
                why = "a key was already sent for this menu; the next menu prompt decides the next key"
        elif why is None:
            why = menu_gate.check_speech(value, self.last_menu, callback_offer, info_only=info_only,
                                         intro_names=names)
        if why is not None:
            self._event("restricted_action_blocked", via=via, why=why[:160], callback_offer=callback_offer)
        return why

    async def press_digits(self, digits: str) -> dict:
        stale = self.stale_turn("press_digits")  # N17
        if stale is not None:
            return stale
        if not self.menu_keys_allowed():
            return {"ok": False, "error": "no phone menu is active"}
        if not isinstance(digits, str) or not DIGITS.match(digits):
            return {"ok": False, "error": "digits must be 0-9, *, # or w (max 32)"}
        refused = self._menu_output_refused("dtmf", digits)
        if refused is not None:  # N16: nothing reaches the provider
            return {"ok": False, "error": f"restricted_action_blocked: {refused}"}
        if self.digits_gen == self.gen:
            return {"ok": False, "error": "keys were already pressed for this prompt"}
        self.digits_gen = self.gen
        self.menu_key_sent = self.menu_seq  # N16-C: before any await, so a second key for this menu is refused
        if self.voice is not None:
            # L5: in-band tones first, the REST <Play digits> fallback if the menu did not react.
            result = await self.voice.press_menu(digits)
            if not result.get("ok"):
                return result
            self._transcript(Speaker.SYSTEM, f"Pressed {mask_digits(digits)}")
            self._event("dtmf_sent", digits=mask_digits(digits), via=str(result.get("via", "")))
            return result
        self._send({"type": "sendDigits", "digits": digits})
        self._transcript(Speaker.SYSTEM, f"Pressed {mask_digits(digits)}")
        self._event("dtmf_sent", digits=mask_digits(digits))
        return {"ok": True}

    async def voice_hold_resumed(self) -> dict:
        """L5: the voice agent heard the business put the call back on hold."""
        if self.terminal or self.state is not S.AGENT_TALKING:
            return {"ok": False, "error": f"cannot resume hold in state {self.state.value}"}
        self.tracker = deps.HoldTracker(ask_model=self._ask_model)
        self.last_confirm = -math.inf
        self.voice_conversation = False
        ok = await self.set_state(S.ON_HOLD, "hold resumed")
        return {"ok": bool(ok)}

    async def send_secret(self, secret: Any, mode: str, *, approval_generation: Optional[int] = None,
                          ticket: Any = None) -> bool:
        """Sealed delivery (engine.sealed): the code goes to the relay, never to the model or a record.

        R1 delta N1: the last checks run here, on the service loop, with no await between them and the
        frame being queued: the owner's grant for this call and counterpart still exists, its approval
        generation is still ``approval_generation``, and the sender's ``ticket`` commits (a sender
        that already timed out has abandoned it, so a late run can never send)."""
        if self.terminal or self.state not in (S.DIALING, S.IVR, S.ON_HOLD, S.HUMAN_DETECTED, S.AGENT_TALKING):
            return False
        if self.relay is None or self.relay.closed or self.handoff_pending:
            return False
        value = secret.reveal()
        if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Za-z]{3,12}", value):
            return False
        if mode == "dtmf" and not value.isdigit():
            return False
        task_id = self.task.id if self.task is not None else None
        if (approval_generation is None or task_id is None
                or self.svc.otp_generation(task_id) != approval_generation
                or not self.svc.otp_call_allowed(self.call_id)):
            self._event("verification_code_refused", why="approval_changed")
            return False
        if ticket is not None and not ticket.commit():
            self._event("verification_code_refused", why="sender_gave_up")
            return False
        if self.voice is not None:
            del value
            if not await self.voice.send_sealed(secret, mode):  # local tones / cached digit clips (L5)
                return False
        elif mode == "dtmf":
            self._send({"type": "sendDigits", "digits": value})
        else:
            self.gen += 1  # never mixed into a model turn
            self._send({"type": "text", "token": " ".join(value), "last": True})
        value = None
        marker = deps.CALL_TRANSCRIPT_MARKER
        self._transcript(Speaker.SYSTEM, marker)
        self._event("verification_code_sent", mode=mode, marker=marker)
        self._spawn(self._in_worker(self._note, marker))
        return True

    async def request_finish(self, outcome: str, summary: str) -> dict:
        stale = self.stale_turn("request_finish")  # N17
        if stale is not None:
            return stale
        if outcome not in ("done", "callback_expected", "failed"):
            return {"ok": False, "error": "outcome must be done, callback_expected or failed"}
        if self.restrictions and self.state in PRE_HUMAN and not self.terminal and (
                self.info_only or outcome != "done"):
            # Round 25: a restricted hold task is not given up while the call is up: it keeps routing, then holds.
            # Only the business hanging up, the time cap or the owner ends it.
            self._event("finish_refused", outcome=outcome, state=self.state.value)
            return {"ok": False, "error": "this hold task is not finished while the call is up: keep routing, then "
                                          "hold (reply WAIT). The business hanging up, the time cap or the owner "
                                          "ends it."}
        from .task_rules import check_finish
        refused = check_finish(outcome, summary, self.restrictions)
        if refused is not None:  # round 16: a forbidden result cannot be reported as done
            self._event("restriction_blocked", tool="request_finish", why=refused[:120])
            return {"ok": False, "error": refused}
        self.finish_outcome = outcome
        self.finish_summary = mask_text(summary or "")[:1000]
        self._event("finish_requested", outcome=outcome)
        return {"ok": True, "note": "Say a brief goodbye; the call ends after this reply."}

    async def request_owner(self) -> dict:
        stale = self.stale_turn("request_owner")  # N17
        if stale is not None:
            return stale
        if not self.svc.cfg.owner_number:
            return {"ok": False, "error": "no owner number is configured"}
        self.owner_requested = True
        return {"ok": True, "note": "The owner will be rung after this reply."}

    # -- owner bridge ----------------------------------------------------------
    async def ring_owner(self, why: str, *, token: Optional[str] = None) -> bool:
        if self.terminal or self.state in (S.RINGING_OWNER, S.OWNER_CONNECTED) or self.owner_sid:
            return False
        if self._strict_confirmation() and why != "take_over" and not self.yes_confirmed:
            # Rounds 29-30: only our question answered with a clear yes rings the owner (a late or model verdict never).
            self._event("owner_ring_refused", why="no clear yes to our live-person question")
            return False
        if self.direction is Direction.OUTBOUND and why != "take_over" and (
                self.confirmed_utt is None or not self._after_question(self.confirmed_utt)):
            # Round 34 (N30-R): every outbound ring but the owner's own take-over compares the confirming utterance's
            # start stamp with the send time of our latest live-person question.
            self._event("owner_ring_refused", why="no utterance after our live-person question confirmed a person")
            return False
        if self.direction is Direction.OUTBOUND and why != "take_over" and not self.human_confirmed:
            # Round 16 (hold-trial precondition 2): an assumption, a model's request or a recording never rings the
            # owner; only an L2 HUMAN verdict or a clear yes to the live-person question does (the owner's own
            # take-over is their choice).
            self._event("owner_ring_refused", why="no confirmed person")
            return False
        owner = self.cfg.owner_number
        callback = self.direction is Direction.INBOUND
        action = "bridge_owner_callback" if callback else "bridge_owner"
        task_id = self.task.id if self.task else self.call_id
        scope = "callback" if callback else self.call_id
        if callback and deps.is_quiet(self.svc.now_utc(), self.svc.settings):
            self._event("owner_ring_skipped", why="quiet_hours")
            return False
        if token is None:
            token = self.svc.grants.take(action, task_id, scope)
        try:
            permit = self.svc.gate.authorize(owner, role=Role.OWNER, action=action, task_id=task_id,
                                             call_id=self.call_id, line_key=self.cfg.line_key, token=token,
                                             binding_call_id=scope)
        except GateRejected as exc:
            self._event("owner_ring_rejected", gate=exc.gate, reason=exc.reason)
            return False
        self.state_before_ring = self.state
        try:
            sid = await asyncio.to_thread(
                self.svc.adapter.place_call, permit, from_number=self.cfg.line_number or self.cfg.from_number,
                answer_url=self.svc.url(f"/twilio/voice/owner/answer/{self.call_id}"),
                status_url=self.svc.url(f"/twilio/voice/owner/status/{self.call_id}"),
                timeout_s=self.options.get("owner_ring_timeout_s", self.cfg.owner_ring_timeout_s),
                time_limit_s=self.cfg.max_call_minutes * 60)
        except Exception as exc:
            self._event("owner_ring_failed", error=type(exc).__name__)
            return False
        self.owner_sid = sid
        self.store.update_call(self.call_id, owner_sid=sid)
        await self.set_state(S.RINGING_OWNER, why)
        if "owner_ring_timeout_s" in self.options:  # round 16 (c): the engine, not Twilio, ends the wait
            self._spawn(self._owner_accept_deadline(sid, float(self.options["owner_ring_timeout_s"])))
        if not callback:
            self._spawn(self._ring_filler(sid))  # round 43c: the person never waits in silence while the owner rings
        self._transcript(Speaker.SYSTEM, f"Ringing {self.cfg.owner_name}")
        return True

    RING_FILLER_QUIET_S = 5.0  # round 43c: at most this much silence for the person while the owner is rung

    async def _ring_filler(self, sid: str) -> None:
        """Round 43c (STEER-4 2d): while the owner's phone rings, a fixed holding line whenever the person has heard
        nothing for RING_FILLER_QUIET_S (neither their own speech nor ours). Stops when the ring ends."""
        from . import fallback
        lines = (fallback.holding_line(self.cfg.owner_name), "Still connecting, one moment please.")
        said = 0
        # The connecting line was just sent; the quiet clock starts when it has been spoken.
        last = time.monotonic() + self.speech_wait(CONNECTING_LINE.format(owner=self.cfg.owner_name))
        while not self.terminal and self.state is S.RINGING_OWNER and self.owner_sid == sid:
            await asyncio.sleep(0.25)
            if self.terminal or self.state is not S.RINGING_OWNER or self.owner_sid != sid:
                return
            now = time.monotonic()
            quiet_since = max(last, self.last_business_activity, self._last_holding_line)
            if now - quiet_since >= self.RING_FILLER_QUIET_S:
                self._last_holding_line = now
                last = now
                await self.speak(lines[said % len(lines)])
                said += 1
                self._event("ring_filler", count=said)

    async def _owner_accept_deadline(self, sid: str, timeout_s: float) -> None:
        await asyncio.sleep(timeout_s)
        if not self.terminal and self.state is S.RINGING_OWNER and self.owner_sid == sid:
            self._event("owner_ring_timeout", after_s=timeout_s)
            await self.owner_unavailable()

    # -- round 16 (c): the owner-timeout fallback question (fixed lines, no Hermes turn) -----------
    async def _info_only_speech(self, text: str) -> None:
        if not _words(text):
            return
        from . import fallback
        if self.state is S.RINGING_OWNER:  # the person talks while the owner is rung: a fixed holding line
            now = time.monotonic()
            if now - self._last_holding_line >= fallback.HOLDING_LINE_EVERY_S:
                self._last_holding_line = now
                await self.speak(fallback.holding_line(self.cfg.owner_name))
            return
        script = self.script
        if script is None or script["phase"] != "asking":
            return  # nothing is said for the owner outside the script
        script["answer"].append(text)
        script["heard"].append(text)  # round 31 (N28): everything said after the question, quoted in the summary
        if "?" in text and not fallback.answerish(text) and not script["clarified"]:
            script["clarified"] = True  # e.g. "can I have your confirmation number?": once, a fixed reply
            script["answer"].pop()
            await self.speak(fallback.clarify_line(self.fallback_question))
            return
        script["token"] += 1
        quiet = fallback.QUIET_AFTER_ANSWER_S if fallback.answerish(text) else fallback.QUIET_OTHERWISE_S
        self._spawn(self._script_quiet(script["token"], quiet))

    async def _ask_fallback_question(self) -> None:
        from . import fallback
        if self.script is not None or self.terminal:
            return  # asked once, whatever ends the owner's ring (timeout, decline, no answer)
        if not await self.set_state(S.AGENT_TALKING, "owner did not accept; the task's fallback question"):
            return
        self._enter_phase("script")
        self.script = {"phase": "asking", "answer": [], "heard": [], "clarified": False, "token": 0}
        await self.speak(fallback.question_line(self.cfg.owner_name, self.fallback_question))
        self._event("fallback_question_asked")
        self._spawn(self._script_deadline())

    async def _script_quiet(self, token: int, delay: float) -> None:
        await asyncio.sleep(delay)
        if self.script is not None and self.script["token"] == token:
            await self._script_finish()

    async def _script_deadline(self) -> None:
        from . import fallback
        await asyncio.sleep(fallback.ANSWER_MAX_S)
        await self._script_finish()

    async def _script_finish(self) -> None:
        from . import fallback
        script = self.script
        if script is None or script["phase"] != "asking" or self.terminal:
            return
        script["phase"] = "done"
        answer = mask_text(" ".join(script["heard"]).strip())[:600]
        line = fallback.THANKS_LINE  # round 30: neutral, never "that's very helpful"
        await self.speak(line)
        wait = self.options.get("owner_ring_timeout_s", self.cfg.owner_ring_timeout_s)
        head = f"{self.cfg.owner_name} did not pick up within {wait} s, so Hermes asked: \"{self.fallback_question}\""
        reply = f" The reply was: \"{answer}\"" if answer else " No reply was given."
        # Round 31 (N28): no guessing whether the reply answers the question. Anything said is "unverified".
        if answer:
            self.fallback_result = "unverified"
            self.finish_outcome = "done"
            self._event("fallback_question_answered", verified=False)
        else:
            self.fallback_result = "unanswered"
            self.finish_outcome = "failed"
            self._event("fallback_question_unanswered")
        self.engine_note = f"{head}{reply} Outcome: {self.fallback_result}; answer not verified."
        await asyncio.sleep(self.speech_wait(line))
        await self.hang_up(EndReason.COMPLETED, state=S.ENDED)

    def owner_whisper_text(self) -> str:
        return (f"Hermes Line. {self.title}. Someone is on the line now. "
                "Press 1 to take over the call.")

    async def owner_accept(self, digits: str) -> bool:
        """Owner pressed a key on the private whisper. True means: join the conference."""
        if self.terminal or self.state is not S.RINGING_OWNER:
            return False
        if digits != "1":
            # Round 7: the owner declined (any other key). Hermes carries on at once instead of waiting for the
            # owner leg's status callback.
            self._event("owner_declined")
            self._spawn(self.owner_unavailable())
            return False
        self.conference = f"hl-{self.call_id}"
        self.store.update_call(self.call_id, conference=self.conference)
        self.handoff_pending = True
        self._enter_phase("owner")  # Hermes stops speaking now; a turn in flight is stale (N17)
        if self.driver is not None:
            self.driver.interrupt(None)
        await self.set_state(S.OWNER_CONNECTED, "owner accepted")
        self.mode = Mode.OWNER
        self.store.update_call(self.call_id, mode=Mode.OWNER)
        self._transcript(Speaker.SYSTEM, f"{self.cfg.owner_name} took over the call")
        self._spawn(self._move_business_to_conference())
        return True

    async def join_conference_after(self, delay: float) -> None:
        """Round 17: after the fixed line was spoken, this leg (the owner's inbound leg or the business leg) joins
        the shared conference."""
        await asyncio.sleep(delay)
        if not self.terminal:
            await self._move_business_to_conference()

    async def child_update(self, title: str, reason: Optional[EndReason], *, detail: str = "") -> None:
        """Round 17: tell the owner, on their own call, that the child call ended before they were connected."""
        if self.terminal or self.bridged:
            return
        words = detail or {EndReason.NO_ANSWER: "nobody answered", EndReason.BUSY: "the line was busy",
                           EndReason.HUNG_UP_BY_OWNER: "you cancelled it", EndReason.CANCELED: "you cancelled it",
                           }.get(reason, f"it ended ({reason.value.replace('_', ' ')})" if reason else "it ended")
        line = f"An update on the call about {title}: {words}. I'll send you the summary."
        await self.speak(line)
        owner = self.cfg.owner_name
        self._spawn(self._in_worker(self._note, f"[system] The call about {title} ended before {owner} was "
                                                f"connected: {words}. {owner} may ask for another call."))

    def conference_twiml(self) -> str:
        """The conference this leg joins; the owner's inbound leg reports to its child (round 17)."""
        from ..server import twiml
        return twiml.conference(name=self.conference, status_url=self.svc.url(
            f"/twilio/voice/conference/status/{self.conference_status_id or self.call_id}"))

    async def _move_business_to_conference(self) -> None:
        if self.relay is not None and not self.relay.closed:
            self._send({"type": "end", "handoffData": json.dumps({"reason": "takeover"})})
            return
        try:  # no relay socket: redirect the live leg instead
            await asyncio.to_thread(self.svc.adapter.redirect_call, self.provider_sid,
                                    self.svc.url(f"/twilio/voice/conference/join/{self.call_id}"))
        except Exception as exc:
            self._event("bridge_failed", error=type(exc).__name__)
            await self.hang_up(EndReason.PROVIDER_ERROR, state=S.FAILED)

    async def owner_leg_status(self, status: str) -> None:
        if self.terminal:
            return
        if status in ("no-answer", "busy", "failed", "canceled", "completed"):
            if self.state is S.RINGING_OWNER:
                await self.owner_unavailable()
            elif self.state is S.OWNER_CONNECTED and status == "completed":
                await self.hang_up(EndReason.OWNER_FINISHED, state=S.ENDED)

    async def owner_unavailable(self) -> None:
        if self.owner_sid:
            self._spawn(asyncio.to_thread(self._end_leg, self.owner_sid))
        self.owner_sid = ""
        self.store.update_call(self.call_id, owner_sid="")
        self._transcript(Speaker.SYSTEM, f"{self.cfg.owner_name} did not take the call")
        before = self.state_before_ring
        if before in (S.DIALING, S.IVR, S.ON_HOLD):
            await self.set_state(before, "owner unavailable; still waiting")
            return
        if self.fallback_question:  # round 16 (c): the task's one question, never Hermes on its own
            await self._ask_fallback_question()
            return
        fallback = self.direction is Direction.INBOUND or (self.task is not None and self.task.allow_agent_fallback)
        if fallback and self.driver_error is None and await self.set_state(S.AGENT_TALKING, "owner unavailable"):
            if self.grok_talks:
                await self._begin_voice_conversation(f"{self.cfg.owner_name} could not be reached. Tell the person "
                                                     "briefly and continue with the task yourself.")
                return
            self.start_turn(f"[system] {self.cfg.owner_name} could not be reached. Tell the person briefly and "
                            "continue with the task yourself.")
            return
        line = f"I'm sorry, {self.cfg.owner_name} isn't available right now. We'll call back. Goodbye."
        await self.speak(line)
        await asyncio.sleep(self.speech_wait(line))
        await self.hang_up(EndReason.OWNER_UNAVAILABLE, state=S.ENDED)

    # -- owner actions --------------------------------------------------------
    async def say(self, text: str) -> dict:
        if self.terminal or self.state in (S.OWNER_CONNECTED, S.AWAITING_APPROVAL) or self.relay is None:
            return {"ok": False, "error": "Hermes is not speaking on this call"}
        text = (text or "").strip()[:500]
        if not text:
            return {"ok": False, "error": "nothing to say"}
        self.gen += 1  # supersede anything Hermes was saying
        if self.driver is not None:
            self.driver.interrupt(None)
        self._send({"type": "text", "token": text, "last": True})
        self._transcript(Speaker.HERMES, text)
        self._spawn(self._in_worker(self._note, f"The owner asked me to say this, and I said it: {text}"))
        return {"ok": True}

    async def take_over(self, token: Optional[str] = None) -> dict:
        if self.terminal or self.state not in (S.DIALING, S.IVR, S.ON_HOLD, S.HUMAN_DETECTED, S.AGENT_TALKING):
            return {"ok": False, "error": f"cannot take over in state {self.state.value}"}
        if self.state in (S.HUMAN_DETECTED, S.AGENT_TALKING):
            await self.speak(f"One moment please, I'm connecting {self.cfg.owner_name}.")
        ok = await self.ring_owner("take_over", token=token)
        return {"ok": ok} if ok else {"ok": False, "error": "the owner leg could not be rung"}

    # -- endings --------------------------------------------------------------
    def _end_leg(self, sid: str) -> None:
        if not sid:
            return
        try:
            self.svc.adapter.end_call(sid)
        except Exception as exc:
            self._event("end_leg_failed", error=type(exc).__name__)

    async def hang_up(self, reason: EndReason, *, state: CallState = S.ENDED) -> None:
        if self.terminal:
            return
        self.end_request = reason
        if self.relay is not None and not self.relay.closed:
            self._send({"type": "end"})
        await asyncio.to_thread(self._end_leg, self.provider_sid)
        await asyncio.to_thread(self._end_leg, self.owner_sid)
        await self.finalize(state, reason)

    async def business_status(self, status: str) -> None:
        """Twilio status callback for the business leg."""
        if status in ("in-progress", "answered"):
            if not self.answered:
                self.answered = True
                self.store.update_call(self.call_id, started_at=self.svc.clock())
            return
        if self.terminal or status in ("queued", "initiated", "ringing"):
            return
        if status == "busy":
            await self.finalize(S.FAILED, EndReason.BUSY)
        elif status == "no-answer":
            await self.finalize(S.FAILED, EndReason.NO_ANSWER)
        elif status == "failed":
            await self.finalize(S.FAILED, EndReason.PROVIDER_ERROR)
        elif status == "canceled":
            await self.finalize(S.FAILED, self.end_request or EndReason.CANCELED)
        elif status == "completed":
            if self.owner_sid:
                await asyncio.to_thread(self._end_leg, self.owner_sid)
            if self.end_request:
                await self.finalize(S.ENDED, self.end_request)
            elif self.state is S.OWNER_CONNECTED:
                await self.finalize(S.ENDED, EndReason.OWNER_FINISHED)
            elif self.state is S.AGENT_TALKING:
                done = self.finish_outcome is not None
                await self.finalize(S.ENDED, EndReason.COMPLETED if done else EndReason.BUSINESS_HANGUP)
            elif not self.answered and not self.setup_seen:
                await self.finalize(S.FAILED, EndReason.NO_ANSWER)
            else:
                await self.finalize(S.FAILED, EndReason.BUSINESS_HANGUP)

    async def connect_completed(self, generation: Optional[int], params: dict) -> str:
        """Twilio reports that relay generation ``generation`` ended (<Connect action>).

        Processed once per generation (R1 M2), single-flight (R1 delta M2): the first request for a
        generation claims it synchronously, before any await, by starting one completion task; every
        overlapping or later request for that generation awaits the same task and returns the same
        TwiML. The retry budget is debited at most once per generation and only when the completion
        becomes a real recovery. An unknown or superseded generation changes nothing. If the socket
        of that generation is still connected, the completion is not believed yet.
        """
        if generation is None or generation not in self.gen_twiml:
            self._event("connect_unknown_generation")
            return self._preserving_twiml()
        if generation in self.action_results:
            self._event("connect_retransmit", generation=generation)
            return self.action_results[generation]
        task = self._completing.get(generation)
        if task is not None:
            self._event("connect_retransmit", generation=generation, in_flight=True)
        else:
            if generation != self.relay_gen:
                self._event("connect_stale_generation", generation=generation)
                return self._preserving_twiml()
            # Claimed here, with no await between the checks above and this line. The task belongs to the
            # session, so a caller that goes away (HTTP disconnect) never cancels the completion for others.
            task = self._spawn(self._complete_generation(generation, params))
            self._completing[generation] = task
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._preserving_twiml()

    async def _complete_generation(self, generation: int, params: dict) -> str:
        from ..server import twiml
        try:
            for _ in range(30):
                live = self.relay is not None and not self.relay.closed and self.relay.generation == generation
                if not live:
                    break
                await asyncio.sleep(0.1)
                # re-check after every await: a newer generation, the end of the call or a completed result
                if generation in self.action_results:
                    return self.action_results[generation]
                if generation != self.relay_gen:
                    self._event("connect_stale_generation", generation=generation)
                    return self._preserving_twiml()
                if self.terminal:
                    break
            else:
                self._event("connect_unconfirmed", generation=generation)
                return twiml.response(self.gen_twiml[generation])  # token already spent; nothing new is minted
            if generation in self.action_results:
                return self.action_results[generation]
            if generation != self.relay_gen:
                self._event("connect_stale_generation", generation=generation)
                return self._preserving_twiml()
            try:
                result = await self.relay_action(params)  # no await inside: debit, token and cache are one step
            except CallAlreadyEnded:
                return twiml.response(twiml.hangup())  # not cached: a forged "completed" cannot end the real call
            self.action_results[generation] = result
            return result
        finally:
            if self._completing.get(generation) is asyncio.current_task():
                del self._completing[generation]

    def _preserving_twiml(self) -> str:
        """TwiML for a Connect action we will not act on (missing, unknown or superseded generation).

        Never an empty ``<Response/>``: if Twilio itself sent it, an empty document would hang up a
        live business leg (round 5 regression fix). Nothing changes, nothing is debited, no new
        token is minted: the conference during a hand-off, a hang-up only if the call is already
        over, otherwise the current generation's document again (its token is single-use)."""
        from ..server import twiml
        if self.terminal or self.end_request:
            return twiml.response(twiml.hangup())
        if self.handoff_pending:
            return twiml.response(self.conference_twiml())
        if self.relay_gen >= 0:
            return twiml.response(self.gen_twiml[self.relay_gen])
        return twiml.response(twiml.hangup())

    async def relay_action(self, params: dict) -> str:
        """The business leg's <Connect> ended (once per generation). Returns the next TwiML."""
        from ..server import twiml
        if str(params.get("CallStatus", "")) in LEG_ENDED:
            # Round 7: the caller hung up; the Connect ended because the call did. No recovery, no token, no
            # debit (the first live call minted a reconnect for a completed call). The status callback ends it.
            self._event("connect_after_hangup")
            raise CallAlreadyEnded()
        if self.handoff_pending and not self.terminal:
            return twiml.response(self.conference_twiml())
        if self.terminal or self.end_request:
            return twiml.response(twiml.hangup())
        if time.monotonic() < self.digits_redirect_until:
            self._event("relay_redirect_stream")  # DTMF fallback in progress: a fresh stream, not a reconnect
            return twiml.response(self.svc.relay_twiml(self, welcome=None))
        if self.reconnects < self.cfg.relay_max_reconnects:
            self.reconnects += 1
            self._event("relay_reconnect", attempt=self.reconnects)
            return twiml.response(self.svc.relay_twiml(self, welcome=None))
        self._event("relay_reconnect_exhausted")
        self._spawn(self.hang_up(EndReason.DROPPED, state=S.FAILED))
        return twiml.response(twiml.hangup())

    async def conference_event(self, event: str, params: dict) -> None:
        self._event("conference", what=event[:40])
        if event == "conference-end" and not self.terminal:
            await self.hang_up(EndReason.OWNER_FINISHED, state=S.ENDED)

    async def _drop_watchdog(self) -> None:
        await asyncio.sleep(self.cfg.relay_reconnect_grace_s)
        if self.relay is None and not self.terminal and not self.handoff_pending:
            self._event("dropped")
            await self.hang_up(EndReason.DROPPED, state=S.FAILED)

    async def _max_duration_watchdog(self) -> None:
        limit = self.cfg.max_call_minutes * 60
        while not self.terminal:
            await asyncio.sleep(min(5.0, max(limit / 20, 0.05)))
            if time.monotonic() - self.started > limit and not self.terminal:
                self._event("max_duration")
                await self.hang_up(EndReason.MAX_DURATION, state=S.FAILED)
                return

    async def finalize(self, state: CallState, reason: EndReason) -> None:
        if self.state.terminal or self._finalizing:
            return
        self._finalizing = True
        self.gen += 1
        if self.turn_task is not None and not self.turn_task.done() and self.driver is not None:
            self.driver.interrupt(None)
        rev = self.store.update_call(self.call_id, state=state, end_reason=reason.value, ended_at=self.svc.clock())
        self.state = state
        self._event("ended", state=state.value, reason=reason.value)
        self._voice_state(state)
        self._update_task(state, reason)
        self.svc.notify(self.call_id, rev)
        if self.parent is not None or self.child is not None:
            self.svc.linked_call_ended(self, reason)  # round 17: the other leg of a parent/child pair
        if self.relay is not None:
            self._send({"type": "end"})
            relay = self.relay
            self._spawn(self._close_relay_soon(relay))
        # Round 7: everything slow happens after the caller of finalize (often a Twilio webhook) has its
        # answer. On the first live call the summary's model question ran inside the status callback, so
        # Twilio waited on it (notification 15003) and the service's stop had to cancel it.
        self._wrap_up = self.loop.create_task(self._wrap_up_call(state, reason))
        self.svc.wrap_ups.add(self._wrap_up)
        self._wrap_up.add_done_callback(self.svc.wrap_ups.discard)

    async def _wrap_up_call(self, state: CallState, reason: EndReason) -> None:
        summary = self._summary_base(reason)
        try:
            summary = await self._summarize(state, reason)
        except asyncio.CancelledError:
            pass  # stopping: the plain summary, no model
        except Exception:
            pass
        finally:
            try:
                rev = self.store.update_call(self.call_id, summary=summary)
                if self.task is not None:
                    self.store.update_task(self.task.id, summary=summary)
                self.store.outbox_put(task_id=self.task.id if self.task else None, call_id=self.call_id,
                                      kind="summary", text=summary)
                self.svc.notify(self.call_id, rev)
            finally:
                driver = self.driver
                if driver is not None:
                    threading.Thread(target=self._close_driver, args=(driver,), daemon=True).start()
                self.executor.shutdown(wait=False)
                self.ask_executor.shutdown(wait=False)
                self.svc.session_finished(self)
                self._consumer.cancel()

    @staticmethod
    def _close_driver(driver: Any) -> None:
        try:
            driver.close()
        except Exception:
            pass

    async def _close_relay_soon(self, relay: RelayLink) -> None:
        await asyncio.sleep(0.5)
        await relay.close(1000)

    def _update_task(self, state: CallState, reason: EndReason) -> None:
        if self.task is None:
            return
        if self.direction is Direction.INBOUND:
            if self.finish_outcome == "done" or reason is EndReason.OWNER_FINISHED:
                self.store.update_task(self.task.id, status=TaskStatus.DONE, outcome="done on callback")
            return
        if getattr(self, "fallback_result", None):  # round 30: the fallback question's honest outcome
            status = TaskStatus.DONE if self.fallback_result == "unverified" else TaskStatus.FAILED
            outcome = self.fallback_result
        elif self.finish_outcome == "done" or reason is EndReason.OWNER_FINISHED:
            status, outcome = TaskStatus.DONE, "done"
        elif self.finish_outcome == "callback_expected" or self.left_voicemail:
            status, outcome = TaskStatus.AWAITING_CALLBACK, "callback expected"
        elif reason is EndReason.HUNG_UP_BY_OWNER:
            status, outcome = TaskStatus.CANCELED, "hung up by owner"
        else:
            status, outcome = TaskStatus.FAILED, reason.value.replace("_", " ")
        self.store.update_task(self.task.id, status=status, outcome=outcome)

    SUMMARY_LINES = 12   # round 37: the last lines of the call quoted in the summary
    SUMMARY_LINE_CHARS = 160

    def _summary_base(self, reason: EndReason) -> str:
        return self._summary_text(reason)

    async def _summarize(self, state: CallState, reason: EndReason) -> str:
        return self._summary_text(reason)

    def _summary_text(self, reason: EndReason) -> str:
        """Round 37 (B8 and L7's note): the summary states only the engine's own record. It has the outcome sentence,
        the log line, the engine's fallback note, and the lines actually exchanged, quoted from the transcript (what
        Hermes sent and what the other side said). The model's finish note and any model summary are never used:
        Delta run 1's model summary claimed lines the gate had blocked."""
        parts = [self._summary_head(reason), self.engine_note, self._said_lines()]
        return mask_text(" ".join(p.strip() for p in parts if p and p.strip()))[:1500]

    def _said_lines(self) -> str:
        said = []
        for t in self.store.transcript(self.call_id, limit=200):
            who = {"hermes": "Hermes", "business": "They"}.get(t["speaker"])
            text = " ".join(str(t["text"]).split())
            if who is None or not text:
                continue
            line = f'{who}: "{text[:self.SUMMARY_LINE_CHARS]}{"…" if len(text) > self.SUMMARY_LINE_CHARS else ""}"'
            if not said or said[-1] != line:
                said.append(line)
        if not said:
            return ""
        return "On the call: " + " / ".join(said[-self.SUMMARY_LINES:]) + "."

    def _summary_head(self, reason: EndReason) -> str:
        call = self.store.get_call(self.call_id)
        duration = 0
        if call and call.ended_at:
            duration = int(call.ended_at - (call.started_at or call.created_at))
        who = call.counterpart_masked if call else ""
        head = {
            EndReason.COMPLETED: "Call completed",
            EndReason.OWNER_FINISHED: f"{self.cfg.owner_name} took over and finished the call",
            EndReason.HUNG_UP_BY_OWNER: "Call hung up by the owner",
            EndReason.VOICEMAIL: "Reached voicemail; no message left" if not self.left_voicemail else "Left a voicemail",
            EndReason.NO_ANSWER: "No answer",
            EndReason.BUSY: "Line busy",
            EndReason.BUSINESS_HANGUP: "The other side hung up",
            EndReason.DROPPED: "The call dropped and could not be recovered",
            EndReason.OWNER_UNAVAILABLE: f"A person answered but {self.cfg.owner_name} could not be reached",
            EndReason.PROVIDER_ERROR: "The call failed (provider or agent error)",
            EndReason.MAX_DURATION: "Call ended at the maximum call length",
            EndReason.SERVICE_RESTART: "Call ended because the line service stopped",
        }.get(reason, reason.value.replace("_", " ").capitalize())
        log = f"Hermes Line: {head} ({who}, \"{self.title}\", {duration // 60}m{duration % 60:02d}s)."
        return f"{self._outcome_sentence(reason, duration, call)} {log}"  # round 37 (B8): the outcome first

    def _outcome_sentence(self, reason: EndReason, duration: int, call) -> str:
        """Round 37 (B8): one plain sentence on what happened, from the engine's own record (phases, confirmation,
        fallback result, end reason); names only the owner and the task's approved intro name."""
        took = f"{duration} s" if duration < 60 else f"{duration // 60} min {duration % 60} s"
        owner = self.cfg.owner_name
        phases = {p.get("phase") for p in (getattr(call, "phases", None) or [])}
        names = (getattr(self, "options", None) or {}).get("intro_names") or []
        bot = (f"{names[0]}'s automated assistant" if names else "the automated assistant") if self.had_bot_label \
            else None
        ender = {
            EndReason.BUSINESS_HANGUP: f"{bot or 'the other side'} ended the call",
            EndReason.COMPLETED: "Hermes ended the call",
            EndReason.HUNG_UP_BY_OWNER: f"{owner} hung up the call",
            EndReason.DROPPED: "the call dropped",
            EndReason.MAX_DURATION: "the call reached the maximum length",
            EndReason.PROVIDER_ERROR: "the call failed",
            EndReason.SERVICE_RESTART: "the line service stopped",
        }.get(reason, "the call ended")
        if self.direction is Direction.INBOUND:
            if reason is EndReason.OWNER_FINISHED or "bridged" in phases:
                return f"{owner} took the incoming call; it ended after {took}."
            return f"Hermes answered an incoming call; {ender} after {took}."
        if reason is EndReason.OWNER_FINISHED or "bridged" in phases:
            return f"A person answered and {owner} was connected; the call ended after {took}."
        result = getattr(self, "fallback_result", None)
        if result:
            got = "got a reply (not verified)" if result == "unverified" else "got no reply"
            return (f"{owner} did not pick up, so Hermes asked the fallback question and {got}; "
                    f"the call ended after {took}.")
        if self.human_confirmed or phases & {"person", "fallback"}:
            if reason is EndReason.OWNER_UNAVAILABLE:
                return f"A person answered but {owner} could not be reached; the call ended after {took}."
            return f"A person answered; {ender} after {took}."  # the model's reported outcome is not restated
        if reason is EndReason.NO_ANSWER:
            return f"No answer; the call ended after {took}."
        if reason is EndReason.BUSY:
            return "The line was busy."
        if reason is EndReason.VOICEMAIL:
            return (f"Reached voicemail and left a message; the call ended after {took}." if self.left_voicemail
                    else f"Reached voicemail; no message left. The call ended after {took}.")
        return f"No person reached; {ender} after {took}."
