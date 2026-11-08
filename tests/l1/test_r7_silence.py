"""Round 7: the first live call went silent for 39 s. A live person never hears more than ~3 s of silence."""
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from unittest import mock

from harness import BUSINESS, LINE, OWNER, Line, wait_for
from plugin.line.engine import deps
from plugin.line.engine.acp_driver import AcpDriver

# The live call's business lines (masked transcript, evidence/trial/call_68921278eab742c6/trial.json).
TRIAL = ["Hi. How are you?", "Yeah. It's me, Ken. But information you have about me?"]
BUDGET = 3.0
FAKE = str(Path(__file__).with_name("fake_acp_agent.py"))


def unknown(prompt):
    """The model answer that left the live call's detector at unknown/low."""
    if prompt.startswith("The phone call has ended"):
        return "Short call; nothing was arranged."
    return json.dumps({"label": "unknown", "confidence": "low", "evidence": [0]})


def slow(seconds, then=unknown):
    def ask(prompt):
        time.sleep(seconds)
        return then(prompt)
    return ask


class StubDetector:
    """Stands in for L2's HoldTracker so a test forces a verdict (and a delay) regardless of L2's text rules:
    the re-ask and hand-over paths stay tested now that L2 hears the live reply as human."""

    def __init__(self, label="unknown", delay_s=0.0):
        self.label, self.delay_s = label, delay_s

    def __call__(self, ask_model=None):
        return self

    def update(self, utt, context=None):
        if getattr(utt, "speaker", "business") == "business" and self.delay_s:
            time.sleep(self.delay_s)
        return deps.Detection(self.label, "low", ("stub detector",))


def stub_detector(test, **kw):
    patcher = mock.patch.object(deps, "HoldTracker", StubDetector(**kw))
    patcher.start()
    test.addCleanup(patcher.stop)


def reply_latency(relay, line_text, timeout=10.0):
    """Seconds from a business utterance to the first non-empty text Hermes sends back."""
    before = len(relay.frames)
    t0 = time.monotonic()
    relay.prompt(line_text)
    while time.monotonic() - t0 < timeout:
        new = relay.frames[before:]
        if any(f.get("type") == "text" and (f.get("token") or "").strip() for f in new):
            return time.monotonic() - t0
        time.sleep(0.01)
    return float("inf")


def spoken_after(relay, before):
    return "".join(f.get("token", "") for f in relay.frames[before:] if f.get("type") == "text")


class TrialReplayTests(unittest.TestCase):
    def _call(self, line, **kw):
        req, created = line.dial(**kw)
        relay = line.connect(req, created)
        return req, relay

    def test_R7_trial_transcript_every_human_turn_gets_an_answer_within_budget(self):
        for name, delay in (("unknown verdict", 0.0), ("detector slower than the budget", 6.0)):
            with self.subTest(case=name):
                patcher = mock.patch.object(deps, "HoldTracker", StubDetector("unknown", delay))
                patcher.start()
                self.addCleanup(patcher.stop)
                line = Line(ask=unknown, confirm_cooldown_s=45.0, reply_budget_s=2.5)
                try:
                    req, relay = self._call(line)
                    latencies = []
                    for text in TRIAL:
                        before = len(relay.frames)
                        latencies.append(reply_latency(relay, text))
                        wait_for(lambda: spoken_after(relay, before).strip(), what="hermes reply")
                    self.assertTrue(all(lat <= BUDGET for lat in latencies), latencies)
                    said = [t["text"] for t in line.store.transcript(req["call_id"]) if t["speaker"] == "hermes"]
                    self.assertIn(line.cfg.confirm_question(), said)
                    # round 16: "Yeah. It's me…" is a clear yes to the question: Hermes answers, nobody re-asks
                    self.assertNotIn("Sorry, I didn't catch that. Am I speaking with a live person?", said)
                    self.assertIn("live_person_confirmed_by_reply",
                                  [e["kind"] for e in line.store.events(req["call_id"])])
                finally:
                    line.close()
                    patcher.stop()

    def test_R7_live_transcript_with_the_real_detector_gets_a_direct_answer(self):
        """L2 (round 2/3) hears the live reply "Yeah. It's me, Ken..." as a person: no re-ask, Hermes answers."""
        line = Line(ask=unknown, confirm_cooldown_s=45.0)
        try:
            req, relay = self._call(line)
            latencies = [reply_latency(relay, TRIAL[0])]
            relay.wait_text("live person?")
            latencies.append(reply_latency(relay, TRIAL[1]))
            self.assertTrue(all(lat <= BUDGET for lat in latencies), latencies)
            line.wait_state(req["call_id"], "agent_talking")
            said = [t["text"] for t in line.store.transcript(req["call_id"]) if t["speaker"] == "hermes"]
            self.assertNotIn("Sorry, I didn't catch that. Am I speaking with a live person?", said)
            self.assertIn({"label": "human", "confidence": "high"},
                          [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "detect"])
        finally:
            line.close()

    def test_R7_second_unclear_reply_hands_over_to_hermes(self):
        stub_detector(self)
        line = Line(ask=unknown)
        try:
            req, relay = self._call(line)
            for text in ["Hi. How are you?", "Hmm, who did you want?", "Go on then."]:  # unclear, never a yes
                lat = reply_latency(relay, text)
                self.assertLessEqual(lat, BUDGET, text)
            line.wait_state(req["call_id"], "agent_talking")
            self.assertIn("human_assumed", [e["kind"] for e in line.store.events(req["call_id"])])
            self.assertIn("not certain they are a person", line.drivers[0].turns[-1])
        finally:
            line.close()

    def test_R7_recordings_still_respect_the_cooldown(self):
        stub_detector(self)
        line = Line(ask=unknown, confirm_cooldown_s=45.0, confirm_reply_window_s=0.5)
        try:
            req, relay = self._call(line)
            relay.prompt("Thank you for calling. Our office hours are nine to five.")
            relay.wait_text("live person?")
            time.sleep(0.7)  # past the reply window: unprompted speech, not an answer
            before = len(relay.frames)
            relay.prompt("We are open Monday through Friday from nine to five.")
            time.sleep(1.5)
            self.assertEqual(spoken_after(relay, before).strip(), "")
        finally:
            line.close()

    def test_R7_owner_self_test_skips_the_live_person_question(self):
        line = Line(ask=unknown, owner_number=BUSINESS, owner_name="Can",
                    policy={"allowed_destinations": [BUSINESS, OWNER]})
        try:
            req, relay = self._call(line)
            lat = reply_latency(relay, TRIAL[0])
            self.assertLessEqual(lat, BUDGET)
            line.wait_state(req["call_id"], "agent_talking")
            said = [t["text"] for t in line.store.transcript(req["call_id"]) if t["speaker"] == "hermes"]
            self.assertNotIn(line.cfg.confirm_question(), said)
            self.assertIn("owner_self_test", [e["kind"] for e in line.store.events(req["call_id"])])
            self.assertIn("answering their own phone", line.drivers[0].turns[0])
        finally:
            line.close()

    def test_R7_a_slow_hermes_turn_gets_fillers(self):
        def slow_reply(driver, text):
            time.sleep(4.0)
            return ["Sure, ", "Friday works."]
        line = Line(ask=unknown, reply=slow_reply, owner_number=BUSINESS,
                    policy={"allowed_destinations": [BUSINESS, OWNER]}, reply_budget_s=1.0, turn_timeout_s=10.0)
        try:
            req, relay = self._call(line)
            before = len(relay.frames)
            t0 = time.monotonic()
            relay.prompt("Hello?")
            wait_for(lambda: "One moment." in spoken_after(relay, before), timeout=3, what="filler")
            self.assertLess(time.monotonic() - t0, 1.0 + BUDGET)
            relay.wait_text("Friday works.", timeout=10)
            self.assertGreaterEqual([e["kind"] for e in line.store.events(req["call_id"])].count("filler"), 2)
            said = [t["text"] for t in line.store.transcript(req["call_id"]) if t["speaker"] == "hermes"]
            self.assertIn("Sure, Friday works.", said)
        finally:
            line.close()


class HintsTests(unittest.TestCase):
    def test_R7_relay_hints_carry_owner_and_business_names(self):
        line = Line(owner_name="Can")
        try:
            req, created = line.dial(title="Table for 4 at Sample Bistro")
            body = line.answer(req["call_id"], created["sid"])
            hints = re.search(r'hints="([^"]*)"', body).group(1).split(",")
            self.assertEqual(hints[0], "Can")
            self.assertIn("this is Can", hints)  # round 12: phrase hints
            self.assertEqual(hints[-2:], ["Sample", "Bistro"])
            self.assertNotRegex(re.search(r'hints="([^"]*)"', body).group(1), r"[0-9]")
        finally:
            line.close()


class EndOfCallTests(unittest.TestCase):
    def _talked(self, line):
        req, created = line.dial()
        relay = line.connect(req, created)
        relay.prompt(TRIAL[0])
        relay.wait_text("live person?")
        return req, created, relay

    def test_R7_status_callback_answers_at_once_while_the_summary_waits_on_the_model(self):
        line = Line(ask=slow(30.0), summary_timeout_s=1.0)
        try:
            req, created, relay = self._talked(line)
            t0 = time.monotonic()
            status = line.status(req["call_id"], created["sid"], "completed")
            self.assertLess(time.monotonic() - t0, 1.0)  # notification 15003: Twilio no longer waits on us
            self.assertEqual(status, 204)
            line.wait_state(req["call_id"], "failed")
            wait_for(lambda: line.store.get_call(req["call_id"]).summary, timeout=5, what="summary")
            summary = line.store.get_call(req["call_id"]).summary  # round 37: the outcome sentence first
            self.assertTrue(summary.startswith("No person reached; the other side ended the call after"), summary)
            self.assertIn("Hermes Line: The other side hung up", summary)
            wait_for(lambda: req["call_id"] not in line.svc.sessions, timeout=5, what="session finished")
            self.assertTrue(relay.closed.wait(3))  # the relay is told to end before the summary, not after
        finally:
            line.close()

    def test_R7_stop_during_a_hanging_summary_is_bounded(self):
        line = Line(ask=slow(60.0), summary_timeout_s=30.0)
        req, created, relay = self._talked(line)
        line.status(req["call_id"], created["sid"], "completed")
        line.wait_state(req["call_id"], "failed")
        t0 = time.monotonic()
        line.svc.stop(timeout=10)
        self.assertLess(time.monotonic() - t0, 6.0)
        self.assertIn("Hermes Line:", line.store.get_call(req["call_id"]).summary)  # round 37: no model to wait on
        self.assertTrue(line.store.outbox_pending() or line.delivered)
        line.store.close()

    def test_R7_connect_action_after_hangup_is_no_reconnect(self):
        line = Line(relay_reconnect_grace_s=10.0)
        try:
            req, created, relay = self._talked(line)
            relay.close()
            wait_for(lambda: line.session(req["call_id"]).relay is None)
            path = f"/twilio/voice/connect/action/{req['call_id']}?g=0"
            status, body = line.post(path, {"CallSid": created["sid"], "CallStatus": "completed"})
            self.assertIn("<Hangup/>", body)
            session = line.session(req["call_id"])
            self.assertEqual((session.reconnects, session.relay_gen), (0, 0))
            self.assertIn("connect_after_hangup", [e["kind"] for e in line.store.events(req["call_id"])])
            # not cached: a genuine completion of the same generation still recovers (a forged "completed" in
            # fallback mode cannot end the real call)
            status, again = line.post(path, {"CallSid": created["sid"], "CallStatus": "in-progress"})
            self.assertIn("<ConversationRelay", again)
            self.assertEqual(session.reconnects, 1)
        finally:
            line.close()


class AcpAskOverlapTests(unittest.TestCase):
    def test_R7_ask_runs_while_a_turn_streams_and_neither_loses_text(self):
        fd_log = tempfile.mkstemp(prefix="acp-r7-", suffix=".jsonl")[1]
        d = AcpDriver("call-1", command=[sys.executable, "-B", FAKE, fd_log, "0.15"], cwd=tempfile.gettempdir(),
                      start_timeout_s=10, turn_timeout_s=10)
        d.start("b")
        try:
            got, out = [], {}
            t = threading.Thread(target=lambda: out.setdefault("turn", d.turn("[business] hi", got.append)))
            t.start()
            time.sleep(0.2)  # the turn is streaming
            answer = d.ask("is this a person?", 10)
            t.join(10)
            self.assertEqual(answer, "Hello from the fake agent.")
            self.assertEqual(out["turn"].text, "Hello from the fake agent.")
            self.assertFalse(out["turn"].interrupted)
            self.assertEqual("".join(got), "Hello from the fake agent.")
        finally:
            d.close()
            Path(fd_log).unlink()


if __name__ == "__main__":
    unittest.main()
