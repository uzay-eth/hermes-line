"""Round 28: R1b delta 25 N23. On a call that had a bot label, while our possible-new-party question is pending, an L2
human label rings the owner only if the reply is a clear yes by L1's own check (the round-16/27 word set); a question
back, a generic question or an offer is treated as unknown (silence, no re-ask inside 20 s). Calls without a bot label
keep the question-back confirmation. Run with a scripted L2 that returns human for the N23 replies (the worst case,
as the round-7 detector did) and with the real detector on main."""
import time
import unittest
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine.machine import REASK_LINE
from plugin.line.engine import assistant, deps
from test_r16c_fallback_question import Q
from test_r27_new_party import BOT, CONTRACT, D, QUESTION, ContractL2, make

TRANSFER = "Let me connect you to an agent."
OPENER = "Hello, how can I help?"
N23_REPLIES = ["Hello, how can I help?", "Is that correct?", "Would you like to make a payment?"]
CONTROLS = ["May I have your confirmation number?", "No.", "Maybe.", "Would you like us to call you back?",
            "Yes, I'm not a live person."]


class WorstCaseL2(ContractL2):
    """The round-7 behaviour the reviewer found: after our question, a question back is human/high."""

    def __init__(self, ask_model=None):
        self.asked = False

    def update(self, utt, context=None):
        if getattr(utt, "speaker", "business") == "hermes":
            self.asked = True
            return D("unknown", "low", ("waiting for business response",))
        if self.asked and utt.text in N23_REPLIES + ["Yes, I'm a live person."]:
            self.asked = False
            return D("human", "high", ("contextual confirmation",))
        self.asked = False
        return CONTRACT.get(utt.text, D("unknown", "low", ("rules inconclusive",)))


class Base(unittest.TestCase):
    scripted = True

    def setUp(self):
        if self.scripted:
            p = mock.patch.object(deps, "HoldTracker", WorstCaseL2)
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
            time.sleep(1.0 if not self.scripted else 0.35)  # the reviewer's one-second gaps
            rows.append([f.get("token") for f in relay.frames[seen:] if f.get("type") == "text"])
        return line, req, session, relay, rows

    def rung(self, line):
        return [c for c in line.adapter.created if c["to"] == line.cfg.owner_number]

    def test_N23_a_question_back_or_an_offer_does_not_ring(self):
        for reply in N23_REPLIES:
            for mode in (True, False):
                line, req, session, relay, rows = self.run_seq([BOT, TRANSFER, OPENER, reply], mode)
                self.assertTrue(any(QUESTION in t for t in rows[2]), (reply, rows))
                # round 43c: at most the one re-ask to a human-like reply (never "please hold while I connect")
                self.assertTrue(set(rows[3]) <= {REASK_LINE}, (reply, mode, rows[3]))
                self.assertFalse(session.human_confirmed, reply)
                self.assertEqual(self.rung(line), [], reply)
                self.assertNotIn("UNAUTHORIZED MODEL TEXT", relay.texts())

    def test_N23_a_clear_yes_still_rings(self):
        line, req, session, relay, rows = self.run_seq([BOT, TRANSFER, OPENER, "Yes, I'm a live person."])
        line.wait_state(req["call_id"], "ringing_owner")
        self.assertTrue(session.human_confirmed)

    def test_N23_controls_stay_unconfirmed(self):
        for reply in CONTROLS:
            line, req, session, relay, rows = self.run_seq([BOT, TRANSFER, OPENER, reply])
            self.assertFalse(session.human_confirmed, reply)
            self.assertEqual(self.rung(line), [], reply)
            self.assertFalse(any("connect" in t for t in rows[3]), (reply, rows))

    def test_N23_a_named_agent_without_a_pending_question_still_rings(self):
        # Round 29 (N24): the named agent is asked first; the ring follows a clear yes only.
        line, req, session, relay, rows = self.run_seq([BOT, TRANSFER, "Hello, this is Dana. How can I help?"])
        self.assertTrue(any(QUESTION in t for t in rows[2]), rows)
        self.assertEqual(self.rung(line), [])
        relay.prompt("Yes, this is Dana.")
        line.wait_state(req["call_id"], "ringing_owner")


class RealDetectorTests(Base):
    scripted = False

    def test_N23_without_a_bot_label_a_question_back_still_confirms(self):
        # Round 30: restricted and information-only tasks are strict in every call, so here a question back is not
        # a person (and nothing rings); only a shared clear yes is. Unrestricted calls keep the legacy rule.
        line, req, session, relay, rows = self.run_seq(["Thanks for calling.", "Who is this?"])
        self.assertTrue(any(QUESTION in t for t in rows[0]), rows)
        self.assertFalse(session.human_confirmed)
        self.assertEqual(self.rung(line), [])
        relay.prompt("Yes, speaking.")
        line.wait_state(req["call_id"], "ringing_owner")


if __name__ == "__main__":
    unittest.main()
