"""Round 39: Delta run 3 (evidence/trial/call_05899c72ff3649bf, 14 min 3 s on Delta's real hold queue).

Seen live: the four live-person questions were spent by 256 s on the bot's yes/no offer and on hold announcements;
afterwards announcements got "Representative."/"Agent." five times from the assistant path in state
``possible_new_party``, so Hermes talked into the hold queue, and an agent at minute 23 could not have been asked.

The engine's second layer (it must hold even when L2 mislabels hold announcements):
1. Assistant phrases go out only while the latest label is ``automated_assistant``; never on possible_new_party,
   unknown, hold_message or hold_music. After the call entered hold, phrases stop until a new prompt is labelled
   ``automated_assistant``.
2. Question budget: 20 s spacing; 4 per hold segment (a new segment starts after 120 s of hold since the last question:
   hold_message/hold_music, or no business speech outside hold); hard cap 12 per call.
3. Unsafe offers stay silent ("say yes, and we'll send a message to your phone").

The replay sends the call's business lines with their recorded relative times through the real engine. The engine's
clock (``time.monotonic`` inside the machine module) runs ``K`` times faster than real time, so 23 engine minutes take
about 70 s; every engine constant (20 s spacing, 8 s phrase spacing, reply windows) applies unchanged."""
import json
import time
import unittest
from pathlib import Path
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import assistant, deps
from plugin.line.engine import machine
from test_r16c_fallback_question import Q

ROOT = Path(__file__).resolve().parents[2]
TRIAL = json.loads((ROOT / "evidence/trial/call_05899c72ff3649bf/trial.json").read_text())
T0 = TRIAL["transcript"][0]["ts"]
BUSINESS = [(t["ts"] - T0, t["text"]) for t in TRIAL["transcript"] if t["speaker"] == "business"]
HOLD_AT = 60.6          # "I'll transfer your call now." (hold_message live)
AGENT_AT = 23 * 60.0    # minute 23: a real agent
AGENT = "Hi, this is Dana with Delta reservations. How can I help you?"
PHRASES = set(assistant.PHRASES.values()) | {"Baggage question."}
K = 20.0


class FastClock:
    """The machine module's ``time``: monotonic runs K times faster; everything else is the real module."""

    def __init__(self):
        self.base = time.monotonic()

    def monotonic(self):
        return self.base + (time.monotonic() - self.base) * K

    def __getattr__(self, name):
        return getattr(time, name)


def worst_case_label(text):
    """The live labels up to hold, then possible_new_party for every hold announcement and for the agent."""
    live = {"You're speaking with Delta's virtual assistant.": "automated_assistant",
            "Not a live person.": "automated_assistant", "I'll transfer your call now.": "hold_message"}
    if text in live:
        return deps.Detection(live[text], "high", ("scripted live label",))
    if text.startswith("Are you trying to add or update"):
        return deps.Detection("automated_assistant", "high", ("scripted live label",))
    if text in ("Thanks for calling. I'm", "Whenever you're set, go ahead and ask your question.", "Got it. 1 moment."):
        return deps.Detection("unknown", "low", ("scripted live label",))
    if text == "Yes, speaking.":
        return deps.Detection("human", "high", ("scripted",))
    return deps.Detection("unknown", "low", ("possible_new_party", "scripted worst case"))


class Scripted:
    def __init__(self, ask_model=None):
        pass

    def update(self, utt, context=None):
        if getattr(utt, "speaker", "business") != "business":
            return deps.Detection("unknown", "low", ("hermes",))
        return worst_case_label(utt.text)
    feed = update


def make(**over):
    line = Line(reply=lambda d, t: ["UNAUTHORIZED MODEL TEXT"], quiet_before_speak_s=0.0, **over)
    req = line.request(mode="owner", title="Delta reservations (hold-line test)", goal="Ask about the bag fee.",
                       fallback_question=Q, owner_ring_timeout_s=20, intro_names=["Delta", "Delta Air Lines"],
                       ivr_category="baggage")
    line.approve(req["task_id"])
    created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
    session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                       and line.session(req["call_id"]))
    relay = line.connect(req, created)
    wait_for(lambda: session.setup_seen)
    return line, req, session, relay


def replay(test, scripted=True):
    """Returns (line, req, session, relay, frames sent before the agent greeting, kinds before the agent)."""
    clock = FastClock()
    patches = [mock.patch.object(machine, "time", clock)]
    if scripted:
        patches.append(mock.patch.object(deps, "HoldTracker", Scripted))
    for p in patches:
        p.start()
        test.addCleanup(p.stop)
    line, req, session, relay = make()
    test.addCleanup(line.close)
    start = clock.monotonic() - BUSINESS[0][0]  # engine time 0 = the recorded start
    for at, text in BUSINESS + [(AGENT_AT, AGENT)]:
        wait = (start + at - clock.monotonic()) / K
        if wait > 0:
            time.sleep(wait)
        if text == AGENT:
            before = list(relay.texts())
        relay.prompt(text)
    return line, req, session, relay, before


def phrases_after_hold(line, req):
    events = line.store.events(req["call_id"])
    held = next((i for i, e in enumerate(events) if e["kind"] == "state" and e["data"].get("to") == "on_hold"), None)
    if held is None:
        return []
    return [e["data"] for e in events[held:] if e["kind"] == "ivr_action"]


class WorstCaseReplayTests(unittest.TestCase):
    def test_R39_no_phrase_into_the_hold_queue_and_the_agent_at_minute_23_rings(self):
        line, req, session, relay, before = replay(self)
        said_before = [t for t in before if t in PHRASES]
        self.assertEqual(phrases_after_hold(line, req), [], "assistant phrases were sent during hold")
        self.assertLessEqual(len(said_before), 1, said_before)  # at most the one before hold
        wait_for(lambda: len([t for t in relay.texts() if "live person?" in t]) >
                 len([t for t in before if "live person?" in t]), timeout=6, what="the question to the agent")
        self.assertLessEqual(session.questions_sent, session.QUESTIONS_PER_CALL_MAX)
        relay.prompt("Yes, speaking.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        self.assertEqual(len([c for c in line.adapter.created if c["to"] == line.cfg.owner_number]), 1)
        self.assertNotIn("UNAUTHORIZED MODEL TEXT", relay.texts())


class RealDetectorReplayTests(unittest.TestCase):
    def test_R39_real_detector_replay(self):
        """The same replay through the real detector (L2 on main at the time of the run)."""
        line, req, session, relay, before = replay(self, scripted=False)
        self.assertEqual(phrases_after_hold(line, req), [], "assistant phrases were sent during hold")
        asked = wait_for(lambda: len([t for t in relay.texts() if "live person?" in t]) >
                         len([t for t in before if "live person?" in t]), timeout=6, what="the question to the agent")
        self.assertTrue(asked)
        relay.prompt("Yes, speaking.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)


class BudgetUnitTests(unittest.TestCase):
    def session(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        self.assertTrue(session._strict_confirmation())
        return session

    def test_R39_four_per_segment_a_new_segment_after_120_s_of_hold_twelve_per_call(self):
        s = self.session()
        s.questions_sent, s.segment_questions, s.last_question_sent, s.last_unheld_speech_at = 4, 4, 1000.0, 990.0
        with mock.patch.object(machine.time, "monotonic", return_value=1010.0):
            self.assertEqual(s._question_budget(), "spacing")  # 20 s spacing first
        with mock.patch.object(machine.time, "monotonic", return_value=1100.0):
            self.assertEqual(s._question_budget(), "budget")  # 4 in this segment, under 120 s of hold
        with mock.patch.object(machine.time, "monotonic", return_value=1120.0):
            self.assertIsNone(s._question_budget())  # 120 s of hold since the question: a new segment
        s.last_unheld_speech_at = 1050.0  # speech outside hold since then: renewal counts 120 s from it (round 40)
        with mock.patch.object(machine.time, "monotonic", return_value=1160.0):
            self.assertEqual(s._question_budget(), "budget")
        with mock.patch.object(machine.time, "monotonic", return_value=1170.0):
            self.assertIsNone(s._question_budget())
        s.last_unheld_speech_at, s.questions_sent, s.segment_questions = 990.0, 12, 0
        with mock.patch.object(machine.time, "monotonic", return_value=5000.0):
            self.assertEqual(s._question_budget(), "budget")  # the hard cap per call

    def test_R39_phrases_only_on_a_fresh_automated_assistant_label_and_not_in_hold(self):
        s = self.session()

        async def reply(label, paused=False):
            s.last_detection = deps.Detection(label, "high", ("unit",))
            s.phrases_paused = paused
            before = len(s.assistant_said)
            await s._assistant_reply("How can I help you today?", label)
            return len(s.assistant_said) - before
        for label in ("possible_new_party", "unknown", "hold_message", "hold_music"):
            self.assertEqual(s.svc.run(reply(label)), 0, label)
        self.assertEqual(s.svc.run(reply("automated_assistant", paused=True)), 0)
        self.assertEqual(s.svc.run(reply("automated_assistant")), 1)


class HoldLabelledYesTests(unittest.TestCase):
    """L2 round 10 labels "Yes, I'm a live person." on a hold queue as hold_message. A clear yes to our pending
    question still confirms (the label is never the evidence on strict calls); a hold line with no pending question
    never does."""

    class AllHold:
        def __init__(self, ask_model=None):
            pass

        def update(self, utt, context=None):
            if getattr(utt, "speaker", "business") != "business":
                return deps.Detection("unknown", "low", ("hermes",))
            if utt.text.startswith("Hi, this is Dana"):
                return deps.Detection("unknown", "low", ("possible_new_party",))
            return deps.Detection("hold_message", "medium", ("queue speech without person cues",))
        feed = update

    def test_R39_a_clear_yes_labelled_hold_message_rings_only_after_our_question(self):
        with mock.patch.object(deps, "HoldTracker", self.AllHold):
            line, req, session, relay = make()
            self.addCleanup(line.close)
            relay.prompt("Yes, I'm a live person.")  # no question pending: a hold line
            line.wait_state(req["call_id"], "on_hold")
            time.sleep(0.5)
            self.assertFalse(session.human_confirmed)
            relay.prompt(AGENT)
            relay.wait_text("live person?")
            relay.prompt("Yes, I'm a live person.")  # the reply, labelled hold_message
            line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        self.assertEqual(len([c for c in line.adapter.created if c["to"] == line.cfg.owner_number]), 1)


class UnsafeOfferTests(unittest.TestCase):
    def test_R39_the_message_offer_is_unsafe(self):
        for text in ("Please say yes, and we'll send a message to your phone.",
                     "Instead of waiting on hold, you can message with a representative for fast service where the "
                     "wait time is 0 minutes.", "Or you can say, no. Thanks."):
            self.assertTrue(assistant.unsafe_offer(text), text)
            self.assertIsNone(assistant.choose(text, ["representative"], "baggage"), text)
        self.assertFalse(assistant.unsafe_offer("How can I help you today?"))


if __name__ == "__main__":
    unittest.main()
