"""Round 41: R1b delta 37 B2 (blocking). Segment renewal must not count an utterance's own duration as silence.

Outside-hold business speech is marked by its END (the arrival of its final, its last frame). A new question segment
needs a qualifying 120 s interval of hold or no business speech entirely before the current utterance began: the
current start minus the later of the last question's send and the end of the last outside-hold speech. The immutable
start stamp still decides confirmation only. 20 s spacing, 4 per segment and the cap of 12 stay.

Through actual sends: the real harness and engine, continuous partials (one per engine second) then a real final,
scripted labels, and round 39's 20x engine clock."""
import time
import unittest
from unittest import mock

from harness import wait_for
from plugin.line.engine import deps, machine
from test_r39_hold_queue import K, FastClock, make

GREETING = "Thanks for calling, how can I help?"   # a short recording: possible_new_party (worst case)
ANNOUNCEMENT = "Thank you for calling, we value your business and will be with you shortly."
BOT = "I can help with many things today, for example baggage and seats."
AGENT = "Hello, this is Dana with Delta reservations. How can I help you?"


def label_for(text):
    if text == BOT:
        return deps.Detection("automated_assistant", "high", ("scripted",))
    if text == "Yes.":
        return deps.Detection("human", "high", ("scripted",))
    return deps.Detection("unknown", "low", ("possible_new_party", "scripted worst case"))


class Scripted:
    def __init__(self, ask_model=None):
        pass

    def update(self, utt, context=None):
        if getattr(utt, "speaker", "business") != "business":
            return deps.Detection("unknown", "low", ("hermes",))
        return label_for(utt.text)
    feed = update


def start(test):
    clock = FastClock()
    for p in (mock.patch.object(machine, "time", clock), mock.patch.object(deps, "HoldTracker", Scripted)):
        p.start()
        test.addCleanup(p.stop)
    line, req, session, relay = make()
    test.addCleanup(line.close)
    zero = clock.monotonic()

    def at(t):
        wait = (zero + t - clock.monotonic()) / K
        if wait > 0:
            time.sleep(wait)

    def final(t, text):
        at(t)
        relay.prompt(text)
        time.sleep(0.05)

    def long_utterance(t0, t1, text):
        """Continuous partials, one per engine second from t0 to t1 - 1, then the final at t1."""
        words = text.split()
        for i, t in enumerate(range(t0, t1)):
            at(t)
            relay.send({"type": "prompt", "voicePrompt": " ".join(words[:1 + i % len(words)]), "lang": "en-US",
                        "last": False})
        final(t1, text)
    return line, req, session, relay, final, long_utterance


def owner_calls(line):
    return [c for c in line.adapter.created if c["to"] == line.cfg.owner_number]


def four(final, session):
    for t in (100, 125, 150, 175):
        final(t, GREETING)
    wait_for(lambda: session.questions_sent == 4, timeout=5, what="four questions")


class LongUtteranceTests(unittest.TestCase):
    def test_B2_a_110_s_recording_earns_no_segment_and_the_agent_still_rings(self):
        """Delta 37 B2 case 1 inverted: recordings with 110 s of continuous partials never earn a segment, so the cap
        is not exhausted before the real agent at 1000; the agent gets the fifth question and the yes rings once."""
        line, req, session, relay, final, long_utterance = start(self)
        four(final, session)
        long_utterance(200, 310, ANNOUNCEMENT)
        for t in (335, 360, 385):
            final(t, GREETING)
        long_utterance(410, 520, ANNOUNCEMENT)
        for t in (545, 570, 595):
            final(t, GREETING)
        time.sleep(0.3)
        self.assertEqual(session.questions_sent, 4)  # no fifth question before the agent
        self.assertEqual(len([t for t in relay.texts() if "live person?" in t]), 4)
        final(1000, AGENT)  # 405 s after the last speech ended: a genuine interval before the agent began
        wait_for(lambda: session.questions_sent == 5, timeout=5, what="the question to the agent")
        final(1003, "Yes.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        self.assertEqual(len(owner_calls(line)), 1)

    def test_B2_a_previous_utterance_counts_from_its_end_not_its_start(self):
        """Delta 37 B2 case 2 inverted: a bot utterance 200-310 (continuous partials), then a recording at 335: only 25 s
        since that speech ended, so no fifth question."""
        line, req, session, relay, final, long_utterance = start(self)
        four(final, session)
        long_utterance(200, 310, BOT)
        final(335, GREETING)
        time.sleep(0.4)
        self.assertEqual(session.questions_sent, 4)
        self.assertEqual(owner_calls(line), [])

    def test_B2_control_genuine_silence_gets_the_fifth_question_and_a_yes_rings_once(self):
        line, req, session, relay, final, long_utterance = start(self)
        four(final, session)
        final(400, GREETING)  # 225 s of genuine silence before it began
        wait_for(lambda: session.questions_sent == 5, timeout=5, what="the fifth question")
        final(403, "Yes, enjoy our special offer.")
        time.sleep(0.3)
        self.assertEqual(owner_calls(line), [])  # not a clear yes
        final(406, "Yes.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        self.assertEqual(len(owner_calls(line)), 1)


class MarkTests(unittest.TestCase):
    def test_R41_marks_are_the_end_of_speech_and_relay_frames_cannot_set_it(self):
        line, req, session, relay, final, long_utterance = start(self)
        long_utterance(10, 40, BOT)
        wait_for(lambda: session.unheld_marks, timeout=3)
        self.assertGreater(session.unheld_marks[-1] - machine.time.monotonic(), -15)  # the final's arrival, not 10
        relay.send({"type": "prompt", "voicePrompt": GREETING, "lang": "en-US", "last": True, "_ended_at": -1e12,
                    "_started_at": -1e12})
        wait_for(lambda: len(session.unheld_marks) == 2, timeout=3)
        self.assertGreater(session.unheld_marks[-1], 0)


if __name__ == "__main__":
    unittest.main()
