"""Round 31: R1b delta 28 (N25-N28). One central quiet gate in ``speak()`` for every line after the opening disclosure;
a hard cap on the wait (after 8 s the next 0.4 s gap, at 10 s anyway); the question budget is spent only on questions
actually sent; phrase spacing from actual send times; the fallback outcome is "unverified" when anything was said,
"unanswered" only on silence. Real time, real 1.2 s quiet, real detector."""
import threading
import time
import unittest
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import assistant, fallback
from plugin.line.engine.machine import CallSession
from test_r16c_fallback_question import Q

QUESTION = "live person?"
BOT = "I'm Delta's automated assistant."
CONNECTING = "Thank you. I'm an AI assistant calling for Sam. Please hold for just a moment while I connect Sam."


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


class Stamped:
    """Records when each text frame arrives and when the test sent each business frame."""

    def __init__(self, relay):
        self.relay, self.sent = relay, []
        self.arrived = {}
        self._stop = threading.Event()
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        seen = 0
        while not self._stop.is_set():
            frames = list(self.relay.frames)
            for f in frames[seen:]:
                if f.get("type") == "text" and f.get("token"):
                    self.arrived.setdefault(f["token"], time.monotonic())
            seen = len(frames)
            time.sleep(0.005)

    def prompt(self, text):
        self.relay.prompt(text)
        self.sent.append(time.monotonic())

    def partial(self, text="and also"):
        self.relay.send({"type": "prompt", "voicePrompt": text, "lang": "en-US", "last": False})
        self.sent.append(time.monotonic())

    def wait(self, token, timeout=15):
        return wait_for(lambda: self.arrived.get(token), timeout=timeout, what=token)

    def gap_before(self, token):
        at = self.arrived[token]
        return at - max(t for t in self.sent if t <= at)

    def close(self):
        self._stop.set()


class N25AllLinesTests(unittest.TestCase):
    def test_N25_the_connecting_line_waits_for_quiet(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        s = Stamped(relay)
        self.addCleanup(s.close)
        s.prompt("Hi, this is Dana with Delta reservations, how can I help you?")
        q = next(t for t in [session.cfg.confirm_question()] if t)
        s.wait(q)
        s.prompt("Yes, speaking.")
        time.sleep(0.4)
        s.partial("I can help")  # the business keeps talking after the yes
        s.wait(CONNECTING)
        self.assertGreaterEqual(s.gap_before(CONNECTING), 1.1)
        self.assertGreaterEqual(s.gap_before(q), 1.1)

    def test_N25_the_fallback_question_and_closing_wait_for_quiet(self):
        with mock.patch.object(fallback, "QUIET_AFTER_ANSWER_S", 0.1), mock.patch.object(fallback, "QUIET_OTHERWISE_S",
                                                                                          0.2):
            line, req, session, relay = make()
            self.addCleanup(line.close)
            s = Stamped(relay)
            self.addCleanup(s.close)
            session.options["owner_ring_timeout_s"] = 0.3
            s.prompt("Hi, this is Dana with Delta reservations, how can I help you?")
            s.wait(session.cfg.confirm_question())
            s.prompt("Yes, speaking.")
            fq = fallback.question_line("Sam", Q)
            s.wait(fq)
            self.assertGreaterEqual(s.gap_before(fq), 1.1)
            s.prompt("It depends on your fare.")
            for _ in range(3):
                time.sleep(0.3)
                s.partial("and the route")
            s.wait(fallback.THANKS_LINE)
            self.assertGreaterEqual(s.gap_before(fallback.THANKS_LINE), 1.1)


class N26CapAndBudgetTests(unittest.TestCase):
    def test_N26_a_never_finalising_partial_stream_still_gets_the_question_once(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        s = Stamped(relay)
        self.addCleanup(s.close)
        s.prompt(BOT)
        time.sleep(1.8)  # "Representative." goes out after quiet
        s.prompt("My name is Alex. How can I help you?")  # possible new party: the question is pending
        start = time.monotonic()
        stop = threading.Event()

        def stream():
            while not stop.is_set():
                s.partial("and so on")
                time.sleep(0.3)  # gaps of 0.3 s: never 1.2 s, never 0.4 s
        threading.Thread(target=stream, daemon=True).start()
        try:
            asked = s.wait(session.cfg.confirm_question(), timeout=14)
        finally:
            stop.set()
        self.assertLessEqual(asked - start, CallSession.QUIET_HARD_S + 2.0)  # within cap + 2 s
        self.assertGreaterEqual(asked - start, CallSession.QUIET_CAP_S)  # it did wait while they talked
        time.sleep(0.5)
        self.assertEqual(session.new_party_questions, 1)  # the budget is spent once, on the question sent
        self.assertEqual(relay.texts().count(session.cfg.confirm_question()), 1)

    def test_N26_a_superseded_question_spends_nothing_and_the_newer_line_is_asked(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        s = Stamped(relay)
        self.addCleanup(s.close)
        s.prompt(BOT)
        time.sleep(1.8)
        s.prompt("My name is Alex. How can I help you?")
        time.sleep(0.5)
        s.prompt("Hello? Are you there?")  # newer, before the first question could go
        s.wait(session.cfg.confirm_question())
        time.sleep(0.4)
        self.assertEqual(session.new_party_questions, 1)
        self.assertEqual(relay.texts().count(session.cfg.confirm_question()), 1)
        self.assertIn("speech_superseded", [e["kind"] for e in line.store.events(req["call_id"])])


class N27SpacingTests(unittest.TestCase):
    def test_N27_phrases_are_never_less_than_eight_seconds_apart(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        s = Stamped(relay)
        self.addCleanup(s.close)
        s.prompt(BOT)
        for _ in range(8):  # the first phrase is held back for about 3 s
            time.sleep(0.3)
            s.partial("please continue")
        s.wait("Representative.")
        time.sleep(0.2)
        s.prompt("I can give you a moment if you need it.")  # 10 s after the first decision, not after its send
        time.sleep(2.0)
        sends = [t for t, _ in session.assistant_said]
        self.assertEqual(len(sends), 1, session.assistant_said)  # spacing from the actual send: skipped
        self.assertIn("spacing", [e["data"].get("why") for e in line.store.events(req["call_id"])
                                  if e["kind"] == "ivr_action_skipped"])
        for a, b in zip(sends, sends[1:]):
            self.assertGreaterEqual(b - a, assistant.MIN_SPACING_S)


class N28OutcomeTests(unittest.TestCase):
    def outcome(self, reply):
        with mock.patch.object(fallback, "QUIET_AFTER_ANSWER_S", 0.1), mock.patch.object(fallback, "QUIET_OTHERWISE_S",
                                                                                          0.2), \
                mock.patch.object(fallback, "ANSWER_MAX_S", 1.0):
            line, req, session, relay = make(quiet=0.0)
            self.addCleanup(line.close)
            session.options["owner_ring_timeout_s"] = 0.2
            relay.prompt("Hi, this is Dana with Delta reservations, how can I help you?")
            relay.wait_text(QUESTION)
            relay.prompt("Yes, speaking.")
            relay.wait_text(fallback.question_line("Sam", Q), timeout=6)
            if reply:
                relay.prompt(reply)
            relay.wait_text(fallback.THANKS_LINE, timeout=6)
            line.wait_state(req["call_id"], "ended")
            summary = wait_for(lambda: line.store.get_task(req["task_id"]).summary)
            return line.store.get_task(req["task_id"]).outcome, summary

    def test_N28_reviewer_table(self):
        for reply, expected in (("You're welcome. What can no problem.", "unverified"), ("", "unanswered"),
                                ("It depends on your fare.", "unverified"),
                                ("The first checked bag is 35 dollars.", "unverified"),
                                ("I cannot quote the fee in dollars.", "unverified"),
                                ("Please wait 2 minutes.", "unverified")):
            outcome, summary = self.outcome(reply)
            self.assertEqual(outcome, expected, reply)
            self.assertIn("answer not verified", summary)
            if reply:
                self.assertIn(reply, summary)


class GateUnitTests(unittest.TestCase):
    def test_R31_the_opening_greeting_is_the_only_exemption(self):
        import inspect
        src = inspect.getsource(CallSession)
        self.assertEqual(src.count("opening=True"), 1)  # the inbound opening disclosure only
        self.assertNotIn("_after_quiet", src)  # the per-path waits are gone: one gate in speak()


if __name__ == "__main__":
    unittest.main()
