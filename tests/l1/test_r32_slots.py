"""Round 32: R1b delta 29 (N26-R, N29). Partials with no final were promoted after 2.5 s (round 35: promotion is gone;
the engine asks the fixed question itself, see test_r35_no_promotion);
a later real final for the same utterance
was dropped as a duplicate (round 33: it is processed; see test_r33_provisional). One slot for the live-person question (and the
connecting line): a second request while one is pending joins it, and the budget counts the one question sent."""
import threading
import time
import unittest
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import deps
from plugin.line.engine.machine import CallSession
from test_r16c_fallback_question import Q

QUESTION = "Am I speaking with a live person?"
CONNECTING = "Thank you. I'm an AI assistant calling for Sam. Please hold for just a moment while I connect Sam."


def make(quiet=1.2, **over):
    line = Line(reply=lambda d, t: ["UNAUTHORIZED MODEL TEXT"], quiet_before_speak_s=quiet, **over)
    req = line.request(mode="owner", title="Delta reservations", goal="Ask about the bag fee.", fallback_question=Q,
                       owner_ring_timeout_s=20, intro_names=["Delta"])
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


class PartialPromotionTests(unittest.TestCase):
    def test_N26R_partials_only_get_one_question_within_stall_plus_quiet(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        for text in ("Hello", "Hello, how can", "Hello, how can I help"):  # partials, never a final
            partial(relay, text)
            time.sleep(0.3)
        last = time.monotonic()
        wait_for(lambda: questions(relay), timeout=CallSession.STALL_AFTER_S + 4, what="the question")
        asked = time.monotonic()
        self.assertLessEqual(asked - last, CallSession.STALL_AFTER_S + 1.2 + 1.0)
        kinds = [e["kind"] for e in line.store.events(req["call_id"])]
        self.assertIn("partials_stalled", kinds)
        self.assertNotIn("detect", kinds)  # round 35: partial text never reaches the detector or the transcript
        self.assertNotIn("Hello, how can I help", [t["text"] for t in line.store.transcript(req["call_id"])])
        time.sleep(0.5)
        self.assertEqual(len(questions(relay)), 1)

    def test_N26R_a_late_real_final_for_the_same_utterance_asks_no_second_question(self):
        # Round 33 (N30): the final is processed (and recorded). Round 35: it carries the stamp of its first partial,
        # from before the question, so it is no reply to it and asks no second question.
        line, req, session, relay = make(quiet=0.0)
        self.addCleanup(line.close)
        partial(relay, "Hello, how can I help")
        wait_for(lambda: questions(relay), timeout=5)
        relay.prompt("Hello, how can I help you today?")  # the real final, late
        time.sleep(0.5)
        kinds = [e["kind"] for e in line.store.events(req["call_id"])]
        self.assertNotIn("duplicate_final_dropped", kinds)
        self.assertEqual(len(questions(relay)), 1)
        self.assertEqual(session.unclear_replies, 0)
        self.assertIn("Hello, how can I help you today?", [t["text"] for t in line.store.transcript(req["call_id"])])

    def test_N26R_a_final_in_time_means_no_stall_question(self):
        line, req, session, relay = make(quiet=0.0)
        self.addCleanup(line.close)
        partial(relay, "Thanks for")
        time.sleep(0.3)
        relay.prompt("Thanks for calling.")
        time.sleep(CallSession.STALL_AFTER_S + 0.5)
        self.assertNotIn("partials_stalled", [e["kind"] for e in line.store.events(req["call_id"])])
        self.assertLessEqual(len(questions(relay)), 1)  # at most the final's own question


class SlowHuman:
    """A detector that answers HUMAN/high late (the reviewer's delayed verdict)."""

    def __init__(self, ask_model=None):
        pass

    def update(self, utt, context=None):
        if getattr(utt, "speaker", "business") == "business":
            time.sleep(1.0)
            return deps.Detection("human", "high", ("late model verdict",))
        return deps.Detection("unknown", "low", ("waiting",))
    feed = update


class QuestionSlotTests(unittest.TestCase):
    def test_N29_a_late_verdict_joins_the_pending_question_exactly_one_frame_budget_one(self):
        with mock.patch.object(deps, "HoldTracker", SlowHuman):
            line, req, session, relay = make(reply_budget_s=0.5)
            self.addCleanup(line.close)
            relay.prompt("We appreciate your call.")  # detection is slow: the timeout path schedules the question
            stop = threading.Event()

            def keep_talking():  # partials keep the quiet gate closed until the late verdict is in
                for _ in range(5):
                    if stop.is_set():
                        return
                    partial(relay, "and we")
                    time.sleep(0.3)
            threading.Thread(target=keep_talking, daemon=True).start()
            wait_for(lambda: questions(relay), timeout=10, what="the question")
            stop.set()
            time.sleep(1.0)
        self.assertEqual(len(questions(relay)), 1)  # one frame, not two
        kinds = [e["kind"] for e in line.store.events(req["call_id"])]
        self.assertIn("question_joined", kinds)
        self.assertEqual(session.new_party_questions, 1)  # the budget counts the one question sent
        self.assertFalse(session.human_confirmed)
        self.assertEqual([c for c in line.adapter.created if c["to"] == line.cfg.owner_number], [])

    def test_N29_a_superseded_question_is_still_asked_for_the_newer_line(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt("Thanks for calling.")
        time.sleep(0.4)
        relay.prompt("Hello?")  # newer utterance before the first question could be spoken
        wait_for(lambda: questions(relay), timeout=6)
        time.sleep(1.5)
        self.assertEqual(len(questions(relay)), 1)

    def test_N29_the_connecting_line_runs_once(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)

        async def twice():
            import asyncio
            session.human_confirmed = session.yes_confirmed = True
            await asyncio.gather(session._on_human("Yes.", confirmed=True), session._on_human("Yes.", confirmed=True))
        line.svc.run(twice(), timeout=20)
        wait_for(lambda: CONNECTING in relay.texts(), timeout=6)
        time.sleep(0.5)
        self.assertEqual(relay.texts().count(CONNECTING), 1)
        self.assertLessEqual(len([c for c in line.adapter.created if c["to"] == line.cfg.owner_number]), 1)


if __name__ == "__main__":
    unittest.main()
