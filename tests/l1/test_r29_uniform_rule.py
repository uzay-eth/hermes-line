"""Round 29: R1b delta 26 (N24, N23-R, the affirmative mismatch). One uniform rule: once a call had a bot label, the
owner is rung only after our fixed live-person question was asked and the reply is a clear yes by the one shared
contract (line.detect.affirmative.clear_yes, used by L1 and L2). A named greeting after a transfer, music or a hold
message gets the question first; an L2 human label without a pending question means: ask. Calls without a bot label
are unchanged. Run through the real detector and through a scripted overconfident L2 (human for every reply)."""
import math
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine.machine import REASK_LINE
from plugin.line.detect.affirmative import clear_yes
from plugin.line.engine import assistant, deps
from test_r27_new_party import BOT, CONTRACT, D, QUESTION, make

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "l2"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugin"))
from test_clear_confirmation import AFFIRMATIVE, NEGATIVE  # noqa: E402  (L2's tables)

TRANSFER = "Let me connect you to an agent."
OPENER = "Hello, how can I help?"
N23R = ["Yes, no.", "Speaking? Is that correct?", "Yes, would you like to make a payment?"]
FORMER_MISSES = ["This is a real person", "I am a live agent", "I'm a real person."]
NAMED = "Hello, this is Dana. How can I help?"


BOUNDARIES = {TRANSFER: D("hold_message", "high", ("transfer",)), "[music]": D("hold_music", "high", ("music",)),
              "Please hold.": D("hold_message", "high", ("hold",))}
FIXED = set(assistant.PHRASES.values()) | {assistant.phrase_text("category", c) for c in assistant.CATEGORIES}


class OverconfidentL2:
    """Scripted: the round-7 behaviour that the engine must not trust. Every business line after the bot prefix is
    human/high, whatever it says."""

    def __init__(self, ask_model=None):
        self.n = 0

    def update(self, utt, context=None):
        if getattr(utt, "speaker", "business") != "business":
            return D("unknown", "low", ("waiting for business response",))
        self.n += 1
        if utt.text in BOUNDARIES:
            return BOUNDARIES[utt.text]
        if utt.text in CONTRACT and self.n <= 2:
            return CONTRACT[utt.text]
        if utt.text == OPENER:
            return D("unknown", "low", ("possible_new_party",))
        return D("human", "high", ("scripted overconfident",))

    feed = update


class Base(unittest.TestCase):
    scripted = False

    def setUp(self):
        if self.scripted:
            p = mock.patch.object(deps, "HoldTracker", OverconfidentL2)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(assistant, "MIN_SPACING_S", 0.0)
        p.start()
        self.addCleanup(p.stop)

    def run_seq(self, lines, info_only=True):
        line, req, session, relay = make(info_only)
        self.addCleanup(line.close)
        rows = []
        for text in lines:
            seen = len(relay.frames)
            relay.prompt(text)
            time.sleep(0.3)
            rows.append([f.get("token") for f in relay.frames[seen:] if f.get("type") == "text"])
        return line, req, session, relay, rows

    def rung(self, line):
        return [c for c in line.adapter.created if c["to"] == line.cfg.owner_number]

    def answer(self, reply, info_only=True):
        line, req, session, relay, rows = self.run_seq([BOT, TRANSFER, OPENER, reply], info_only)
        self.assertTrue(any(QUESTION in t for t in rows[2]), (reply, rows))
        return line, req, session, relay, rows

    def test_R29_every_l2_affirmative_rings_and_every_negative_does_not(self):
        for mode in (True, False):
            for reply in AFFIRMATIVE + NEGATIVE:
                line, req, session, relay, rows = self.answer(reply, mode)
                expected = reply in AFFIRMATIVE
                if expected:
                    wait_for(lambda: session.human_confirmed, what=f"confirmed by {reply!r}")
                    if mode:
                        line.wait_state(req["call_id"], "ringing_owner")
                else:
                    time.sleep(0.1)
                    self.assertFalse(session.human_confirmed, (reply, mode))
                    self.assertEqual(self.rung(line), [], reply)
                    # no person: at most a fixed assistant phrase when L2 keeps the bot label, or (round 43c) the one
                    # re-ask to a human-like reply
                    self.assertTrue(set(rows[3]) <= FIXED | {REASK_LINE}, (reply, rows))

    def test_R29_the_three_former_misses_now_ring(self):
        for reply in FORMER_MISSES:
            line, req, session, relay, rows = self.answer(reply)
            line.wait_state(req["call_id"], "ringing_owner")

    def test_R29_n23r_inputs_do_not_ring(self):
        for mode in (True, False):
            for reply in N23R:
                line, req, session, relay, rows = self.answer(reply, mode)
                self.assertFalse(session.human_confirmed, reply)
                self.assertEqual(self.rung(line), [], reply)

    def test_R29_n24_a_named_greeting_after_any_boundary_is_asked_first(self):
        for boundary in (TRANSFER, "[music]", "Please hold."):
            line, req, session, relay, rows = self.run_seq([BOT, boundary, NAMED])
            self.assertTrue(any(QUESTION in t for t in rows[2]), (boundary, rows))
            self.assertFalse(session.human_confirmed, boundary)
            self.assertEqual(self.rung(line), [], boundary)
            relay.prompt("Yes, this is Dana.")
            line.wait_state(req["call_id"], "ringing_owner")

    def test_R29_a_yes_without_our_question_is_not_a_person(self):
        line, req, session, relay, rows = self.run_seq([BOT, "Yes."])
        self.assertFalse(session.human_confirmed)
        self.assertEqual(self.rung(line), [])


class ScriptedOverconfidentTests(Base):
    """The engine decides alone: L2 says human for every reply."""
    scripted = True

    def test_R29_n23_and_bot_yes_with_an_overconfident_detector(self):
        for reply, expected in (("Hello, how can I help?", False), ("Is that correct?", False),
                                ("Would you like to make a payment?", False), ("Yes, I'm not a live person.", False),
                                ("Yes, I'm a live person.", True)):
            line, req, session, relay, rows = self.answer(reply)
            if expected:
                line.wait_state(req["call_id"], "ringing_owner")
            else:
                time.sleep(0.1)
                self.assertFalse(session.human_confirmed, reply)
                self.assertEqual(self.rung(line), [], reply)


class NoBotLabelTests(unittest.TestCase):
    """Unchanged without a bot label: the legacy question-back confirmation and a direct named greeting."""

    def run_seq(self, lines):
        # Round 30: restricted tasks are strict in every call, so the unchanged controls are an unrestricted
        # owner-mode task without a bot label.
        line = Line(reply=lambda d, t: ["Okay."])
        self.addCleanup(line.close)
        req, created = line.dial(mode="owner", goal="Book a table for four.")
        relay = line.connect(req, created)
        session = line.session(req["call_id"])
        for text in lines:
            relay.prompt(text)
            time.sleep(0.3)
        return line, req, session

    def test_R29_no_bot_label_controls(self):
        line, req, session = self.run_seq(["Thanks for calling.", "Who is this?"])
        line.wait_state(req["call_id"], "ringing_owner")
        line, req, session = self.run_seq([NAMED])
        line.wait_state(req["call_id"], "ringing_owner")
        self.assertFalse(session.had_bot_label)
        self.assertFalse(session.restrictions)


class SharedContractTests(unittest.TestCase):
    def test_R29_the_shared_list(self):
        for ok in ("yes", "Yes I am", "Yeah.", "Yep", "Speaking", "Yes, speaking.", "This is a real person",
                   "I am a real person", "I'm a real person.", "Yes, I'm a live person.", "Yes, I am a live person",
                   "I am a live agent", "Yes, you are speaking to a live agent.", "Yeah, it's me.", "Yes, this is Dana."):
            self.assertTrue(clear_yes(ok), ok)
        for bad in N23R + ["Yes, this is Dana Smith.", "Yes, I am a real person and I can help", "yes?", "Sure.",
                           "Yes, I'm not a live person.", "Yes, I am virtual.", ""]:
            self.assertFalse(clear_yes(bad), bad)

    def test_R29_l1_uses_the_shared_function_after_a_bot_label(self):
        from plugin.line.engine.machine import CallSession
        s = CallSession.__new__(CallSession)
        s.had_bot_label, s.restrictions = True, frozenset()
        with mock.patch("plugin.line.detect.affirmative.clear_yes", return_value=True) as shared:
            self.assertTrue(s._is_clear_yes("anything"))
        shared.assert_called_once_with("anything")

    def test_R29_confirmation_window_is_inclusive(self):
        line, req, session, relay = make(True)
        self.addCleanup(line.close)
        session.last_confirm = 100.0
        w = session.cfg.confirm_reply_window_s
        for now, expected in ((100 + w - 0.001, True), (100 + w, True), (100 + w + 0.001, False), (100 + 2 * w, False)):
            with mock.patch("time.monotonic", return_value=now):
                self.assertEqual(session._question_pending(), expected, now)

    def test_R29_ring_owner_refuses_a_bot_call_without_a_clear_yes(self):
        line, req, session, relay = make(True)
        self.addCleanup(line.close)
        session.had_bot_label, session.human_confirmed, session.yes_confirmed = True, True, False
        self.assertFalse(line.svc.run(session.ring_owner("human_detected")))
        self.assertIn("owner_ring_refused", [e["kind"] for e in line.store.events(req["call_id"])])
        self.assertEqual([c for c in line.adapter.created if c["to"] == line.cfg.owner_number], [])


if __name__ == "__main__":
    unittest.main()
