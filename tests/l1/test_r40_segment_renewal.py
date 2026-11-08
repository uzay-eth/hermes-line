"""Round 40: R1b delta 36 B1 (blocking) and B17.

B1: a new question segment starts once the call has been continuously in hold (on_hold, hold_message/hold_music, or no
business speech) for 120 s, measured from the later of the last question and the last outside-hold speech. Outside-hold
speech only delays renewal; in round 39 one outside-hold utterance after the fourth question blocked it for good, so a
real agent after a long hold was never asked and the owner never rung. 20 s spacing and the cap of 12 stay. The
utterance being decided for is not counted against itself.

B17: switching channel ("Would you like to switch channels?", to chat/text/the app, continue by text) is an unsafe offer:
silence.

Through actual sends: the real harness and engine, scripted labels, the machine clock 20x faster (round 39's FastClock)."""
import time
import unittest
from unittest import mock

from harness import wait_for
from plugin.line.engine import assistant, deps, machine
from test_r39_hold_queue import K, FastClock, make
from plugin.line.engine.machine import CallSession


async def _round43c_reply_rule_off(self, *args, **kwargs):
    """Round 43c (MIT-018 STEER-4): the reply rule (ask twice per party, then the owner judges) replaces the 20 s
    no-re-ask rule for replies to our question. This budget test checks the global question budget for everything
    the reply rule does not decide, so it runs with that rule switched off."""
    return False

AGENT = "Hello, this is Dana. How can I help?"
QUESTION = "live person?"


def label_for(text):
    if text == "Please hold.":
        return deps.Detection("hold_message", "high", ("scripted",))
    if text in ("Bot prompt.", "Would you like to switch channels?"):
        return deps.Detection("automated_assistant", "high", ("scripted",))
    if text == "Yes.":
        return deps.Detection("human", "high", ("scripted",))
    return deps.Detection("unknown", "low", ("possible_new_party", "scripted"))


class Scripted:
    def __init__(self, ask_model=None):
        pass

    def update(self, utt, context=None):
        if getattr(utt, "speaker", "business") != "business":
            return deps.Detection("unknown", "low", ("hermes",))
        return label_for(utt.text)
    feed = update


def questions(relay):
    return [t for t in relay.texts() if QUESTION in t]


def start(test):
    """The real harness on the fast clock. Returns the pieces and send(at, text) on the call's engine clock."""
    clock = FastClock()
    for p in (mock.patch.object(machine, "time", clock), mock.patch.object(deps, "HoldTracker", Scripted)):
        p.start()
        test.addCleanup(p.stop)
    line, req, session, relay = make()
    test.addCleanup(line.close)
    zero = clock.monotonic()

    def send(at, text):
        wait = (zero + at - clock.monotonic()) / K
        if wait > 0:
            time.sleep(wait)
        relay.prompt(text)
        time.sleep(0.05)
    return line, req, session, relay, send


def owner_calls(line):
    return [c for c in line.adapter.created if c["to"] == line.cfg.owner_number]


def four_questions(test, send, session, relay):
    for at in (100, 125, 150, 175):
        send(at, AGENT)
    wait_for(lambda: session.questions_sent == 4, timeout=5, what="four questions")
    test.assertEqual(len(questions(relay)), 4)  # four actual sends


class ReviewerSequenceTests(unittest.TestCase):
    """Delta 36 B1 inverted: four questions, an outside-hold bot prompt at 200, hold from 210; the greeting at 600 gets a
    question and the yes at 603 rings once. Control: the same without the prompt at 200."""

    def sequence(self, intervening):
        line, req, session, relay, send = start(self)
        four_questions(self, send, session, relay)
        if intervening:
            send(200, "Bot prompt.")
        send(210, "Please hold.")
        line.wait_state(req["call_id"], "on_hold")
        send(600, AGENT)
        wait_for(lambda: session.questions_sent == 5, timeout=5, what="the fifth question at 600")
        self.assertEqual(len(questions(relay)), 5)
        send(603, "Yes.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        self.assertEqual(len(owner_calls(line)), 1)

    def test_B1_outside_hold_speech_only_delays_renewal(self):
        self.sequence(intervening=True)

    def test_B1_control_without_the_outside_hold_utterance(self):
        self.sequence(intervening=False)

    def test_B1_renewal_counts_120_s_from_the_last_outside_hold_speech(self):
        line, req, session, relay, send = start(self)
        four_questions(self, send, session, relay)
        send(500, "Bot prompt.")  # outside hold: the reference moves to 500
        send(560, AGENT)  # 60 s later: same segment, no question (and itself outside-hold speech: reference 560)
        time.sleep(0.3)
        self.assertEqual(session.questions_sent, 4)
        send(640, AGENT)  # 80 s after the last outside-hold speech: still no question
        time.sleep(0.3)
        self.assertEqual(session.questions_sent, 4)
        send(770, AGENT)  # 130 s after it: a new segment
        wait_for(lambda: session.questions_sent == 5, timeout=5, what="the question 130 s after the last speech")

    def test_B1_a_greeting_outside_hold_after_silence_is_not_counted_against_itself(self):
        line, req, session, relay, send = start(self)
        four_questions(self, send, session, relay)
        send(400, AGENT)  # 225 s of silence (no business speech), never on_hold
        wait_for(lambda: session.questions_sent == 5, timeout=5, what="the question to the greeting at 400")
        send(403, "Yes.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)

    @mock.patch.object(CallSession, "_question_reply", _round43c_reply_rule_off)
    def test_B1_spacing_and_the_cap_of_twelve_stay(self):
        line, req, session, relay, send = start(self)
        send(50, "Please hold.")
        for at in (100, 110, 125, 150, 175, 300, 325, 350, 375, 500, 525, 550, 575, 700, 900):
            # Round 43c: "Hello, this is Dana." in reply to our question is now a clear yes and would ring; the cap
            # and spacing are checked with a possible-new-party line that is not an affirmation.
            send(at, "Hello, how can I help?")
        time.sleep(0.3)
        self.assertEqual(session.questions_sent, 12)
        self.assertEqual(len(questions(relay)), 12)
        sent = [e["ts"] for e in line.store.events(req["call_id"]) if e["kind"] == "live_person_question_sent"]
        self.assertEqual(len(sent), 12)  # the attempt at 900 is past the cap
        gaps = [(b - a) * K for a, b in zip(sent, sent[1:])]  # wall-clock event times, back to engine seconds
        self.assertGreaterEqual(min(gaps), 19.0, gaps)  # 20 s spacing (scheduling jitter allowed)
        self.assertIn("budget", [e["data"].get("why") for e in line.store.events(req["call_id"])
                                 if e["kind"] in ("question_budget_skip", "new_party_question_skipped")])


class SwitchChannelTests(unittest.TestCase):
    VARIANTS = ("Would you like to switch channels?", "Would you like to switch to chat?", "You can switch to text.",
                "Switch to the Fly Delta app.", "We can continue by text.", "Would you like to continue this over text?",
                "Can we move this conversation to chat?")

    def test_B17_switch_channel_offers_are_unsafe(self):
        for text in self.VARIANTS:
            self.assertTrue(assistant.unsafe_offer(text), text)
            self.assertIsNone(assistant.choose(text, ["representative"], "baggage"), text)
        for text in ("Please switch off your phone during takeoff.", "How can I help you today?"):
            self.assertFalse(assistant.unsafe_offer(text), text)

    def test_B17_through_the_output_path_silence(self):
        line, req, session, relay, send = start(self)
        send(10, "Would you like to switch channels?")
        time.sleep(0.5)
        self.assertEqual(session.assistant_said, ())
        self.assertFalse([t for t in relay.texts() if t in set(assistant.PHRASES.values())])
        self.assertIn("callback, payment or change wording",
                      [e["data"].get("why") for e in line.store.events(req["call_id"])
                       if e["kind"] == "ivr_action_skipped"])


if __name__ == "__main__":
    unittest.main()
