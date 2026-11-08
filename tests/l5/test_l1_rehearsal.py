"""Rehearsal of the L1 integration against the REAL call engine (L1), policy and detector (L2).

L1's ``CallService`` runs on loopback with L1's own offline harness (fake Twilio REST
adapter, scripted Hermes driver). Instead of a ConversationRelay socket, the call's
``RelayLink`` is a ``VoiceCall`` fed by a fake Twilio Media Stream, a fake transcriber and
a fake Grok. ``L1Port`` below is test code showing how L1 can implement the ``CallPort``
with what ``CallSession`` already has; the state hook is wired through the service
notifier. No L1 file is modified.
"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

from helpers import ROOT

sys.path.insert(0, str(ROOT / "tests" / "l1"))
from harness import BUSINESS, OWNER, Line, wait_for                                  # noqa: E402

from plugin.line.engine import deps, sealed                                          # noqa: E402
from plugin.line.engine.contracts import CallState, EndReason, Speaker               # noqa: E402
from plugin.line.engine.machine import RelayLink                                     # noqa: E402
from plugin.line.engine.redact import mask_text                                      # noqa: E402
from plugin.line.voice import VoiceCall, VoiceSettings, mulaw                         # noqa: E402
from plugin.line.voice.phrases import PhraseCache                                    # noqa: E402
from plugin.line.voice.tools import TOOL_SPECS                                       # noqa: E402

TOOL_SPECS_BY_NAME = {t.name: t for t in TOOL_SPECS}
from plugin.line.voice.testing import (FakeMediaStreamsClient, FakeProviderHub,      # noqa: E402
                                       FakeTranscriberHub, VirtualClock)

HOLD = "Please hold; all representatives are assisting other callers."
# L2 labels "Hi, thanks for holding, this is Dana..." as a hold message (reported to the coordinator).
GREETING = "Hello, this is Dana speaking. How can I help you today?"
CONFIRM = "Yes, I'm a real person. How can I help?"


class L1Port:
    """How L1 can implement ``CallPort`` over a live ``CallSession`` (reference for L1)."""

    def __init__(self, svc, session) -> None:
        self.svc = svc
        self.s = session
        self.call_id = session.call_id
        self.voice = None
        self.secret = None        # stands in for L4's OtpDelivery.to_call release in this rehearsal
        self.generation_override = None   # test hook: a stale approval generation

    def offer(self, frame):
        return self.s.offer(frame)

    def record(self, speaker, text):
        self.s._transcript(Speaker(speaker), mask_text(text))

    def voice_event(self, kind, **data):
        self.s._event(kind, **data)

    async def ask_hermes(self, question, timeout_s):
        def run():
            driver = self.s._ensure_driver()
            return driver.turn(f"[voice agent asks] {question}", lambda chunk: None) if driver else None
        try:
            result = await self.s._in_worker(run, timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
        return mask_text(result.text) if result else None

    async def bring_owner(self):
        ok = await self.s.ring_owner("agent_request")
        return {"ok": True} if ok else {"ok": False, "error": "the owner cannot be rung on this call"}

    async def press_digits(self, digits):
        # L1 round 5: CallSession.press_digits keeps its checks, then calls voice.press_menu
        # instead of sending a sendDigits frame.
        return await self.voice.press_menu(digits)

    async def redirect_digits(self, nonce):
        """DTMF fallback: point the live business leg at the digits TwiML URL for ``nonce``."""
        url = self.svc.url(f"/twilio/voice/digits/{self.call_id}/{nonce}")
        try:
            await asyncio.to_thread(self.svc.adapter.redirect_call, self.s.provider_sid, url)
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__}
        return {"ok": True}

    async def report_outcome(self, outcome, summary):
        return await self.s.request_finish(outcome, summary)

    async def end_call(self):
        await self.s.hang_up(EndReason.COMPLETED)
        return {"ok": True}

    async def send_verification_code(self, mode):
        if self.secret is None:
            return {"ok": False, "error": "no code was released for this call"}
        # The existing sealed sink: owner grant + live counterpart check, then CallSession.send_secret.
        # Like L1's CallSessionPort, the approval generation comes from the owner's grant, never from the
        # model: the Grok tool only carries ``mode``. The sink refuses a missing generation (L1 N1 residual).
        generation = self.svc.otp_generation(self.s.task.id) if self.generation_override is None \
            else self.generation_override
        ok = await asyncio.to_thread(sealed.send_secret_to_call, self.call_id, self.secret, mode,
                                     approval_generation=generation)
        return {"ok": bool(ok)} if ok else {"ok": False, "error": "no code was approved for this call"}

    async def hold_resumed(self):
        return {"ok": False, "error": "AGENT_TALKING -> ON_HOLD is an L1 integration point"}

    async def voice_failed(self, reason):
        await self.s.hang_up(EndReason.PROVIDER_ERROR, state=CallState.FAILED)


class Wired:
    """One dialled call on the real engine with the voice transport attached."""

    def __init__(self, line: Line, *, mode: str = "agent", phrases: PhraseCache = None, attach: bool = False,
                 provider_factory=None, **voice_settings):
        self.line = line
        self.req, self.created = line.dial(mode=mode)
        self.cid = self.req["call_id"]
        line.answer(self.cid, self.created["sid"])
        line.status(self.cid, self.created["sid"], "in-progress")
        self.session = line.session(self.cid)
        self.clock = VirtualClock()
        self.hub = FakeProviderHub()
        self.stt = FakeTranscriberHub()
        self.client = FakeMediaStreamsClient(call_sid=self.created["sid"])
        self.port = L1Port(line.svc, self.session)
        settings = {"reconnect_backoff_s": 0.01, "idle_hold_s": 600.0}
        settings.update(voice_settings)
        self.voice = VoiceCall(call_id=self.cid, port=self.port, provider_factory=provider_factory or self.hub,
                               transcriber_factory=self.stt, settings=VoiceSettings.load(settings), clock=self.clock,
                               welcome=line.cfg.disclosure() if line.svc.disclosure_on else None,
                               instructions=line.svc.briefing(self.session.task, self.session.direction),
                               mask=mask_text, expected_call_sid=self.created["sid"],
                               **({"phrases": phrases} if phrases is not None else {}))
        self.port.voice = self.voice
        if attach:   # let L1's own routes (digits, connect_twiml) use this VoiceCall, as attach_voice does
            self.session.voice = self.voice
        loop = line.svc.loop
        # L1 integration point: state hook (here through the service notifier).
        original = line.svc.notifier

        def notifier(call_id, revision):
            original(call_id, revision)
            if call_id == self.cid:
                loop.call_soon_threadsafe(self.voice.on_state, self.session.state.value)
        line.svc.notifier = notifier

        async def attach():
            link = RelayLink(self.voice.engine_frame, self.voice.engine_close)
            assert self.session.attach_relay(link)
            self.link = link
            async def serve():   # what L1's media route does around voice.serve (L5.md §2 point 4)
                try:
                    await self.voice.serve(self.client.socket)
                finally:
                    link.closed = True
                    self.session.relay_closed(link)
            self.serve = asyncio.get_running_loop().create_task(serve())
            self.voice.on_state(self.session.state.value)
            self.client.start({"call": self.cid})
            await self.flush()
        self.run(attach())

    def run(self, coro, timeout: float = 120):
        return self.line.svc.run(coro, timeout=timeout)

    async def flush(self, rounds: int = 8):
        for _ in range(rounds):
            await asyncio.sleep(0.003)
            await self.voice.settle()

    async def say(self, text: str, settle_s: float = 0.4):
        await self.stt.utter(text)
        deadline = time.monotonic() + settle_s
        while time.monotonic() < deadline:
            await self.flush(2)

    @property
    def state(self) -> str:
        return self.line.state(self.cid)


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.lines = []

    def tearDown(self):
        for line in self.lines:
            line.close()

    def make_line(self, **kw) -> Line:
        line = Line(**kw)
        self.lines.append(line)
        return line

    def test_forty_minute_hold_on_the_real_engine_costs_zero_voice_seconds(self):
        line = self.make_line()
        w = Wired(line)

        async def scenario():
            await w.say(HOLD)
            assert w.state == "on_hold", w.state
            disclosure_seconds = w.voice.connected_seconds   # first-ever disclosure render (no cache yet)
            hold_start = w.clock()
            frame = mulaw.tone([440, 554], 20, amplitude=0.1)
            for minute in range(40):
                for _ in range(6):                      # 6 x 500 frames x 20 ms = 60 s
                    for _ in range(500):
                        w.client.media(frame)
                    w.clock.advance(10.0)
                    await w.flush(1)
                await w.stt.utter(HOLD)
                await w.flush(1)
            hold_end = w.clock()
            on_hold_after = w.state
            hold_voice = w.voice.meter.seconds_between(hold_start, hold_end)
            opens_during_hold = w.voice.counters["provider_opens"]
            # L2 rounds 6-7: a named greeting after the hold is a high-confidence human verdict, so the
            # engine goes straight to the human phase (no live-person question).
            await w.say(GREETING)
            after_greeting = w.state
            return (disclosure_seconds, hold_start, hold_end, on_hold_after, hold_voice, opens_during_hold,
                    after_greeting)

        (disclosure_seconds, start, end, on_hold_after, hold_voice, opens_during_hold,
         after_greeting) = w.run(scenario())
        self.assertAlmostEqual(end - start, 2400.0, places=3)
        self.assertEqual(on_hold_after, "on_hold")
        self.assertEqual(w.stt.frames, 40 * 60 * 50)
        self.assertEqual(hold_voice, 0.0)                              # the acceptance assertion
        self.assertEqual(opens_during_hold, 1)                         # only the disclosure, before the hold
        self.assertEqual(disclosure_seconds, 0.0)                      # rendered and closed at once (virtual clock)
        self.assertEqual(after_greeting, "agent_talking")              # human verdict straight from the greeting
        wait_for(lambda: w.voice.counters["provider_opens"] == 2, what="voice opened at the human")
        human_open = w.voice.meter.intervals[-1][0] if not w.voice.meter.is_open else w.voice.meter._opened_at
        self.assertGreaterEqual(human_open, end)                       # the voice starts only at the human
        self.assertTrue(w.hub.last.connected)
        said = [t for p in w.hub.instances for t in p.said]
        self.assertNotIn(line.cfg.confirm_question(), said)           # no live-person question any more
        wait_for(lambda: any("book a table" in t for p in w.hub.instances for t in p.said),
                 what="Hermes's turn spoken")                          # hermes_text path (voice not session.voice)
        transcript = [t["text"] for t in line.store.transcript(w.cid, limit=200)]
        self.assertEqual(transcript.count(HOLD), 41)

    def test_disclosure_is_one_transcript_line_with_the_real_grok_adapter(self):
        """Live-call finding 1: L1 records the disclosure at setup; Grok's transcript of the rendered
        line must not add a second one."""
        from test_live_trial_findings import xai_like
        from plugin.line.voice.grok import GrokRealtimeProvider
        from plugin.line.voice.testing import FakeWs, fake_connector
        sockets = []

        def factory():
            ws = FakeWs(xai_like)
            sockets.append(ws)
            return GrokRealtimeProvider(lambda n: "xai-synthetic", connector=fake_connector(ws))
        line = self.make_line()
        w = Wired(line, provider_factory=factory)
        w.run(w.flush(20))
        disclosure = line.cfg.disclosure()
        transcript = [t["text"] for t in line.store.transcript(w.cid, limit=50)]
        self.assertEqual(transcript.count(disclosure), 1, transcript)
        forced = [m for ws in sockets for m in ws.sent_json()
                  if m.get("type") == "conversation.item.create" and m["item"]["type"] == "force_message"]
        self.assertEqual([m["item"]["content"][0]["text"] for m in forced], [disclosure])

    def test_owner_mode_bridges_and_hands_off_the_stream(self):
        line = self.make_line()
        w = Wired(line, mode="owner")

        async def scenario():
            await w.say(HOLD)
            await w.say(GREETING)
            await w.say(CONFIRM)
        w.run(scenario())
        wait_for(lambda: w.state == "ringing_owner", what="ringing owner")
        owner_legs = [c for c in line.adapter.created if c["to"] == OWNER]
        self.assertEqual(len(owner_legs), 1)
        said = [t for p in w.hub.instances for t in p.said]
        self.assertTrue(any("connect Sam" in t for t in said), said)
        self.assertTrue(w.hub.last.connected)                         # keeps the person company while ringing
        status, body = line.post(f"/twilio/voice/owner/accept/{w.cid}",
                                 {"CallSid": owner_legs[0]["sid"], "Digits": "1"})
        self.assertEqual(status, 200)
        wait_for(lambda: w.state == "owner_connected", what="owner connected")
        wait_for(lambda: w.client.closed_code == 1000, what="stream closed for the hand-off")
        self.assertFalse(w.hub.last.connected)
        self.assertIsNone(w.stt.active)
        # Twilio now requests the <Connect action> URL; L1's existing handler returns the conference.
        status, body = line.post(f"/twilio/voice/connect/action/{w.cid}?g={w.session.relay_gen}",
                                 {"CallSid": w.created["sid"]})
        self.assertEqual(status, 200)
        self.assertIn("<Conference", body)

    def _talking(self, line, **kw) -> Wired:
        w = Wired(line, **kw)

        async def scenario():
            await w.say(GREETING)
            await w.say(CONFIRM)
            await w.voice.begin_conversation("A live person answered. " + CONFIRM)   # the L1 hook
            await w.flush()
        w.run(scenario())
        wait_for(lambda: w.state == "agent_talking", what="agent talking")
        self.assertEqual(w.voice.purpose, "conversation")
        return w

    def _tool(self, w: Wired, name: str, args: dict) -> dict:
        async def go():
            n = len(w.hub.last.tool_results)
            w.hub.last.tool_call(name, args)
            for _ in range(100):
                await w.flush(1)
                if len(w.hub.last.tool_results) > n:
                    return w.hub.last.tool_results[-1]["output"]
            raise AssertionError("no tool result")
        return w.run(go())

    def test_bring_owner_obeys_the_destination_gate(self):
        line = self.make_line(policy={"allowed_destinations": [BUSINESS]})
        w = self._talking(line)
        before = list(line.adapter.created)
        out = self._tool(w, "bring_owner", {})
        self.assertFalse(out["ok"])
        self.assertEqual(line.adapter.created, before)                # zero provider calls
        self.assertEqual(w.state, "agent_talking")
        rejected = line.store.events(w.cid, kind="owner_ring_rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["data"]["gate"], "destination")

    def test_bring_owner_uses_the_owner_grant_once(self):
        line = self.make_line()
        w = self._talking(line)
        out = self._tool(w, "bring_owner", {})
        self.assertTrue(out["ok"], out)
        wait_for(lambda: w.state == "ringing_owner", what="ringing owner")
        self.assertEqual([c["to"] for c in line.adapter.created], [BUSINESS, OWNER])
        out = self._tool(w, "bring_owner", {})
        self.assertFalse(out["ok"])
        self.assertEqual(len(line.adapter.created), 2)

    def test_verification_code_needs_the_owner_grant_and_never_reaches_the_model(self):
        from plugin.line.sms import OTP_ACTION, SecretText
        code = "482913"
        line = self.make_line()
        w = self._talking(line)
        w.port.secret = SecretText(code)
        media_before = len(w.client.of("media"))
        out = self._tool(w, "send_verification_code", {"mode": "dtmf"})
        self.assertFalse(out["ok"])                                   # no owner approval for a code
        self.assertEqual(len(w.client.of("media")), media_before)
        # The owner approves one code for exactly this call and counterpart (what /line allow-code does).
        line.svc.grants.put(OTP_ACTION, w.session.task.id, "otp", {"x": 1}, 300, 300, renewable=False,
                            meta={"kind": "call", "call_id": w.cid, "counterpart": BUSINESS,
                                  "approval_generation": 1})   # L1 §11: grants carry their approval generation
        self.assertEqual(set(TOOL_SPECS_BY_NAME["send_verification_code"].parameters["properties"]), {"mode"})
        w.port.generation_override = 0                     # a release under an older approval: refused
        out = self._tool(w, "send_verification_code", {"mode": "dtmf"})
        self.assertFalse(out["ok"])
        self.assertEqual(len(w.client.of("media")), media_before)
        self.assertEqual(len(line.store.events(w.cid, kind="verification_code_refused")), 1)
        w.port.generation_override = None                  # the current approval's generation
        out = self._tool(w, "send_verification_code", {"mode": "dtmf"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["status"], "[verification code sent]")
        wait_for(lambda: len(w.client.of("media")) > media_before, what="tones")
        tones = mulaw.dtmf_tones(code)
        self.assertIn(tones[:1600], w.client.audio_out())
        for text in w.hub.all_text_sent:
            self.assertNotIn(code, text.replace(" ", ""))
        transcript = " ".join(t["text"] for t in line.store.transcript(w.cid, limit=200))
        self.assertIn(deps.CALL_TRANSCRIPT_MARKER, transcript)
        self.assertNotIn(code, transcript)
        out = self._tool(w, "send_verification_code", {"mode": "speak"})  # speak: no digit clips cached
        media = len(w.client.of("media"))
        self.assertEqual(w.voice.counters["sealed_refused"], 1)
        self.assertEqual(len(w.client.of("media")), media)

    def test_tools_are_refused_before_a_human_is_confirmed(self):
        line = self.make_line()
        w = Wired(line, prewarm_s=30.0)

        async def scenario():
            await w.say(HOLD)
            w.voice.prewarm()            # a pre-human (speech-only) session is open now
            await w.flush()
            p = w.hub.last
            assert p.connected and w.voice.purpose == "speech"
            p.tool_call("bring_owner", {})
            p.tool_call("press_keys", {"digits": "0"})
            p.tool_call("send_verification_code", {"mode": "dtmf"})
            for _ in range(50):
                await w.flush(1)
                if len(p.tool_results) == 3:
                    break
            return [r["output"] for r in p.tool_results]
        outs = w.run(scenario())
        self.assertEqual(len(outs), 3)
        self.assertTrue(all(o["ok"] is False for o in outs), outs)
        self.assertEqual([c["to"] for c in line.adapter.created], [BUSINESS])
        self.assertEqual(w.voice.counters["dtmf_inband"], 0)

    def test_dtmf_fallback_redirects_the_real_leg_and_refuses_code_digits(self):
        menu = "For reservations, press 1. For hours and directions, press 2."
        code = "482913"
        line = self.make_line()
        w = Wired(line, attach=True)

        async def scenario():
            await w.say(menu)
            first = await w.port.press_digits("1")
            await w.flush()
            w.client.ack_marks()
            await w.flush()
            await w.say(menu)                                  # the menu ignored the tones
            return first
        first = w.run(scenario())
        self.assertEqual(first, {"ok": True, "via": "tones"})
        self.assertEqual(len(line.adapter.redirected), 1)
        sid, url = line.adapter.redirected[0]
        self.assertEqual(sid, w.created["sid"])
        prefix = line.svc.url(f"/twilio/voice/digits/{w.cid}/")
        self.assertTrue(url.startswith(prefix), url)
        nonce = url[len(prefix):]
        route = f"/twilio/voice/digits/{w.cid}/{nonce}"
        gen_before = w.session.relay_gen
        status, doc = line.post(route, {"CallSid": w.created["sid"]})     # Twilio fetches L1's real route
        self.assertEqual(status, 200)
        self.assertIn('<Play digits="1"/>', doc)
        generation = w.session.relay_gen
        self.assertEqual(generation, gen_before + 1)
        self.assertIn(f"/twilio/voice/connect/action/{w.cid}?g={generation}", doc.replace("&amp;", "&"))
        token = doc.split(f"/twilio/media/{w.cid}/")[1].split('"')[0]
        self.assertFalse(line.svc.claim_stream_token(w.cid, token, "media"))          # old kind: refused
        self.assertFalse(line.svc.claim_stream_token(w.cid, token, f"relay-g{generation}"))
        self.assertTrue(line.svc.claim_stream_token(w.cid, token, f"media-g{generation}"))
        self.assertFalse(line.svc.claim_stream_token(w.cid, token, f"media-g{generation}"))
        # A replayed nonce gets the current generation again: no digits, nothing new minted, call kept.
        status, again = line.post(route, {"CallSid": w.created["sid"]})
        self.assertEqual(status, 200)
        self.assertNotIn("<Play", again)
        self.assertIn("<Stream", again)
        self.assertEqual(w.session.relay_gen, generation)
        self.assertEqual(len(line.store.events(w.cid, kind="ingress_rejected")), 1)

        async def code_digits():
            ok = await w.voice.send_sealed(__import__("plugin.line.sms", fromlist=["SecretText"]).SecretText(code),
                                           "dtmf")
            refused = [await w.port.press_digits(d) for d in (code, "1" + code, "2")]
            return ok, refused
        ok, refused = w.run(code_digits())
        self.assertTrue(ok)
        self.assertFalse(refused[0]["ok"])
        self.assertFalse(refused[1]["ok"])
        self.assertEqual(refused[2], {"ok": True, "via": "redirect"})   # plain menu keys still work
        self.assertEqual(len(line.adapter.redirected), 2)
        status, doc2 = line.post(f"/twilio/voice/digits/{w.cid}/{line.adapter.redirected[1][1].rsplit('/', 1)[1]}",
                                 {"CallSid": w.created["sid"]})
        self.assertIn('<Play digits="2"/>', doc2)
        for _sid, u in line.adapter.redirected:
            self.assertNotIn(code, u)

    def test_provider_lost_mid_call_ends_the_call_cleanly(self):
        line = self.make_line()
        w = self._talking(line, max_reconnects=1)

        async def drop():
            w.hub.fail_connects = 5
            w.hub.last.drop()
            for _ in range(60):
                await w.flush(1)
        w.run(drop())
        wait_for(lambda: w.state == "failed", what="failed")
        call = line.store.get_call(w.cid)
        self.assertEqual(call.end_reason, EndReason.PROVIDER_ERROR.value)
        self.assertIn(w.created["sid"], line.adapter.ended)
        self.assertEqual(w.hub.connected_now, 0)
        wait_for(lambda: w.voice.closed, what="voice closed")
        self.assertIsNone(w.stt.active)


if __name__ == "__main__":
    unittest.main()
