"""Round 35: R1b delta 32 (N32). Partial promotion is deleted. Recovery for N26: partials with no final for 2.5 s after
the last one make the engine ask the fixed live-person question itself (quiet gate, global budget, once per pending
utterance). No utterance is made from partial text, it never reaches the detector, and nothing confirms from it. The
eventual final's stamp is the earliest partial still pending for it; a stamp is set once and never moves later (a new
partial after 10 s of partial silence starts a new pending utterance). Real time; quiet 1.2 s unless noted."""
import time
import unittest
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import deps
from plugin.line.engine.machine import CallSession
from test_r16c_fallback_question import Q

GREETING = "Hello, this is Dana. How can I help?"
BOT = "I'm Delta's automated assistant."


def make(quiet=1.2, **over):
    line = Line(reply=lambda d, t: ["UNAUTHORIZED MODEL TEXT"], quiet_before_speak_s=quiet, **over)
    req = line.request(mode="owner", title="Delta reservations", goal="Ask about the bag fee.", fallback_question=Q,
                       owner_ring_timeout_s=20, intro_names=["Delta", "Delta Air Lines"], ivr_category="baggage")
    line.approve(req["task_id"])
    created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
    session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                       and line.session(req["call_id"]))
    relay = line.connect(req, created)
    wait_for(lambda: session.setup_seen)
    return line, req, session, relay


def partial(relay, text):
    relay.send({"type": "prompt", "voicePrompt": text, "lang": "en-US", "last": False})


def questions(relay):
    return [t for t in relay.texts() if "live person?" in t]


def owner_calls(line):
    return [c for c in line.adapter.created if c["to"] == line.cfg.owner_number]


def kinds(line, req):
    return [e["kind"] for e in line.store.events(req["call_id"])]


def spy(session):
    """Every utterance object the engine builds (only finals build one)."""
    seen = []
    orig = session._on_speech

    async def wrapped(raw, rx=None, *, utt=None):
        seen.append(utt)
        await orig(raw, rx, utt=utt)
    session._on_speech = wrapped
    return seen


def delayed_real(delay):
    real = deps.HoldTracker

    class Delayed(real):
        def update(self, utt, context=None):
            if getattr(utt, "speaker", "business") == "business":
                time.sleep(delay)
            return real.update(self, utt, context)
    return mock.patch.object(deps, "HoldTracker", Delayed)


class ReviewerProbeTests(unittest.TestCase):
    """N32 inverted: the partial starts before our question; whatever partial updates, revisions or delays follow, its
    final keeps that start and never confirms. Genuine new replies after the question still ring."""

    def stalled_then_question(self, first="Yes", **over):
        line, req, session, relay = make(**over)
        self.addCleanup(line.close)
        seen = spy(session)
        t0 = time.monotonic()
        partial(relay, first)  # before any question
        wait_for(lambda: questions(relay), timeout=CallSession.STALL_AFTER_S + 4, what="the stall question")
        self.assertGreater(session.last_question_sent, t0)
        return line, req, session, relay, seen, t0

    def assert_no_ring(self, line, session, wait=2.0):
        time.sleep(wait)
        self.assertEqual(owner_calls(line), [])
        self.assertFalse(session.human_confirmed or session.yes_confirmed)

    def test_N32_repeated_partial_updates_keep_the_first_start(self):
        line, req, session, relay, seen, t0 = self.stalled_then_question()
        time.sleep(0.2)
        partial(relay, "Yes")  # another update of the same unfinished utterance, after the question
        time.sleep(CallSession.STALL_AFTER_S + 0.5)  # the old second promotion point: nothing happens
        self.assertEqual(seen, [])  # no utterance from partial text
        relay.prompt("Yes.")
        wait_for(lambda: seen, timeout=3)
        self.assertLess(seen[0].started_at - t0, 0.2)  # the first partial's start, not the later update's
        self.assert_no_ring(line, session)
        self.assertEqual(len(questions(relay)), 1)

    def test_N32_revised_finals_keep_the_start(self):
        for first, final in (("Yeah", "Yes."), ("I am", "I'm a real person.")):
            with self.subTest(final=final):
                line, req, session, relay, seen, t0 = self.stalled_then_question(first)
                relay.prompt(final)  # a revision, not a new answer
                wait_for(lambda: seen, timeout=3)
                self.assertLess(seen[0].started_at - t0, 0.2)
                self.assert_no_ring(line, session)

    def test_N32_revised_final_with_a_delayed_real_detector(self):
        with delayed_real(3.0):
            line, req, session, relay, seen, t0 = self.stalled_then_question("Yeah")
            relay.prompt("Yes.")
            time.sleep(5.5)
        self.assertIn("detect_slow", kinds(line, req))
        self.assert_no_ring(line, session, wait=0.0)

    def test_N32_a_delayed_final_without_new_partials_fails_closed(self):
        line, req, session, relay, seen, t0 = self.stalled_then_question("Yes.")
        wait_for(lambda: time.monotonic() - t0 > 4.0, timeout=6)
        relay.prompt("Yes.")  # late, but nothing shows it is a new utterance
        wait_for(lambda: seen, timeout=3)
        self.assertLess(seen[0].started_at - t0, 0.2)
        self.assert_no_ring(line, session)

    def test_N32_control_a_revision_before_the_stall_asks_and_does_not_ring(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        partial(relay, "I am")
        time.sleep(0.5)
        relay.prompt("I'm a real person.")
        wait_for(lambda: questions(relay), timeout=5)
        self.assertNotIn("partials_stalled", kinds(line, req))
        self.assert_no_ring(line, session, wait=1.0)

    def test_N32_control_a_genuine_new_reply_after_the_question_rings(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        wait_for(lambda: questions(relay), timeout=5)
        time.sleep(0.2)
        partial(relay, "Yes")  # its own partials, after the question
        time.sleep(0.3)
        relay.prompt("Yes.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        self.assertEqual(len(owner_calls(line)), 1)

    def test_N32_control_after_an_abandoned_partial_the_second_final_confirms(self):
        # Round 36: a later partial never starts anew; only a final clears the pending stamp (see test_r36_stamp)
        line, req, session, relay, seen, t0 = self.stalled_then_question("Hello")
        wait_for(lambda: time.monotonic() - t0 > 4.0, timeout=6)
        partial(relay, "Yes")
        time.sleep(0.3)
        relay.prompt("Yes.")  # stamped with "Hello": fails closed
        self.assert_no_ring(line, session, wait=1.5)
        relay.prompt("Yes.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)


class PartialsOnlyTests(unittest.TestCase):
    def test_N26_partials_only_get_one_question_and_nothing_else(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        seen = spy(session)
        for text in ("Hello", "Hello, how can", "Yes, hello, how can I help"):
            partial(relay, text)
            time.sleep(0.3)
        wait_for(lambda: questions(relay), timeout=CallSession.STALL_AFTER_S + 4, what="the question")
        for text in ("Yes, hello", "Yes"):  # the same unfinished utterance goes on: no second question
            time.sleep(0.4)
            partial(relay, text)
        time.sleep(CallSession.STALL_AFTER_S + 1.5)
        self.assertEqual(relay.texts(), questions(relay))  # the one fixed question is all that was said
        self.assertEqual(len(questions(relay)), 1)
        self.assertEqual(session.questions_sent, 1)
        self.assertEqual(seen, [])  # no utterance object
        k = kinds(line, req)
        self.assertNotIn("detect", k)  # partial text never reaches the detector
        self.assertEqual([t for t in line.store.transcript(req["call_id"]) if t["speaker"] == "business"], [])
        self.assertEqual(owner_calls(line), [])
        self.assertFalse(session.human_confirmed or session.yes_confirmed)
        self.assertEqual(session.assistant_said, ())

    def test_N26_the_stall_question_waits_for_quiet_and_respects_the_budget(self):
        with mock.patch.object(CallSession, "NEW_PARTY_QUESTIONS_MAX", 1):
            line, req, session, relay = make()
            self.addCleanup(line.close)
            relay.prompt(BOT)
            relay.wait_text("Representative.", timeout=6)
            relay.prompt(GREETING)  # a strict call: spends the one allowed question
            wait_for(lambda: questions(relay), timeout=6)
            time.sleep(18)  # past the reply window and (round 39: spacing is checked first) the 20 s spacing
            partial(relay, "Hello?")
            time.sleep(CallSession.STALL_AFTER_S + 2.0)
        self.assertEqual(len(questions(relay)), 1)
        self.assertIn("budget", [e["data"].get("why") for e in line.store.events(req["call_id"])
                                 if e["kind"] == "question_budget_skip"])

    def test_N26_no_stall_question_on_inbound_or_after_a_person(self):
        line, req, session, relay = make(quiet=0.0)
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        relay.wait_text("live person?")
        relay.prompt("Yes.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        partial(relay, "Hello? Are you still")
        time.sleep(CallSession.STALL_AFTER_S + 0.8)
        self.assertNotIn("partials_stalled", kinds(line, req))
        self.assertEqual(len(questions(relay)), 1)


class FinalsDecideTests(unittest.TestCase):
    def test_N32_the_final_after_stalled_partials_decides_the_phrase(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        partial(relay, "I'm Delta's")
        wait_for(lambda: questions(relay), timeout=CallSession.STALL_AFTER_S + 4)
        relay.prompt(BOT)  # the final: the detector and the phrase rules see it
        relay.wait_text("Representative.", timeout=6)
        self.assertIn("automated_assistant", [e["data"].get("label") for e in line.store.events(req["call_id"])
                                              if e["kind"] == "detect"])

    def test_N32_the_final_after_stalled_partials_keeps_unsafe_offer_silence(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt(BOT)
        relay.wait_text("Representative.", timeout=6)
        partial(relay, "Would you like")
        wait_for(lambda: "partials_stalled" in kinds(line, req), timeout=CallSession.STALL_AFTER_S + 4)
        relay.prompt("Would you like us to call you back?")
        time.sleep(2.5)
        self.assertEqual(len(session.assistant_said), 1)
        self.assertIn("callback, payment or change wording",
                      [e["data"].get("why") for e in line.store.events(req["call_id"])
                       if e["kind"] == "ivr_action_skipped"])
        self.assertEqual(owner_calls(line), [])

    def test_N32_no_promotion_code_left(self):
        import inspect
        src = inspect.getsource(CallSession)
        for gone in ("promoted", "_partial_watch", "_provisional_speech", "PROMOTE_AFTER_S", "last_partial_text"):
            self.assertNotIn(gone, src)


if __name__ == "__main__":
    unittest.main()
