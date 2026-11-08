"""Round 23: R1b delta 21 N16-I. Whole-menu silence is the first check for every output path; a menu with no parsed
option is never safe for generic speech unless every sentence is a known intro or hold line; callback paraphrases
("ring you back", "we will call you", "keep your place") silence the menu. ``r1d21/`` holds the reviewer's fixtures,
copied unchanged."""
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

from harness import Line, wait_for
from plugin.line.detect import Detection
from plugin.line.engine import menu_gate
from test_r16c_fallback_question import Q

ROOT = Path(__file__).resolve().parents[2]
NAMES = ["Delta", "Delta Air Lines"]
GUIDANCE = "Ask about the bag fee. Decline any callback offer. Do not pay anything. Do not change the booking."
REVIEWER = [  # menu, expected for generic agent/representative (through real L2 detection and dispatch)
    ("We can ring you back. Select an option.", False),  # N16-I, inverted
    ("We can call you back. Select an option.", False),
    ("We can ring you back. For baggage, press 1.", False),
    ("Please select an option.", False),  # reviewer: spoken; round 23 decision: not an intro/option/hold line
]
SILENCED = [  # (reason, menu, L2 callback evidence)
    ("callback offer by L2", "For baggage, press 1.", True),
    ("callback paraphrase", "We can ring you back. For baggage, press 1. For an agent, say agent.", False),
    ("keep your place", "To keep your place, press 1. For baggage, press 2.", False),
    ("we will call you", "We will call you when an agent is free. For baggage, press 1.", False),
    ("blocked word", "For baggage, press 1. To make a payment, press 9.", False),
    ("amount", "For baggage, press 1. For 35USD, press 2.", False),
    ("unknown sentence", "Please select an option. For baggage, press 1.", False),
    ("trailing text", "For baggage, press 1. Please hold.", False),
]


def make(info_only, reply=None):
    line = Line(reply=reply or (lambda driver, text: ["WAIT"]))
    if info_only:
        req = line.request(mode="owner", title="Bag fee", goal="Ask about the bag fee.", fallback_question=Q,
                           intro_names=NAMES)
    else:
        req = line.request(goal=GUIDANCE, intro_names=NAMES)
    line.approve(req["task_id"])
    created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
    session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                       and line.session(req["call_id"]))
    return line, req, created, session


class ReviewerCasesThroughDetectionTests(unittest.TestCase):
    """The reviewer's N16-I menus spoken by the business: real L2 detection, real menu turn, real gate, Relay."""

    def run_word(self, info_only, word):
        holder = {}
        line, req, created, session = make(info_only, lambda driver, text: [word] if "[phone menu]" in text
                                           else ["WAIT"])
        self.addCleanup(line.close)
        relay = line.connect(req, created)
        wait_for(lambda: session.setup_seen)
        cases = REVIEWER + [("All agents are busy. For baggage, press 1.", True)]  # an allowed control
        handled = ("restricted_action_blocked", "ivr_action", "ivr_action_skipped")
        from plugin.line.engine import assistant
        fixed = set(assistant.PHRASES.values())
        for menu, expected in cases:
            seen = len(relay.frames)
            before = len([e for e in line.store.events(req["call_id"]) if e["kind"] in handled])
            relay.prompt(menu)
            if expected:
                wait_for(lambda: any(f.get("token") == word for f in relay.frames[seen:]), what=f"{menu} spoken")
            else:
                # Round 25: a menu with no announcement goes to the engine's automated-assistant path (fixed
                # phrases only; callback wording gets nothing); with announcements, to the round-24 gate. Either
                # way the model's word is never sent.
                wait_for(lambda: len([e for e in line.store.events(req["call_id"])
                                      if e["kind"] in handled]) > before, what=f"{menu} handled")
                time.sleep(0.05)
                said = [f.get("token") for f in relay.frames[seen:] if f.get("type") == "text"]
                self.assertNotIn(word, said, (menu, info_only, word))
                self.assertTrue(set(said) <= fixed, (menu, said))
                if "ring you back" in menu or "call you back" in menu:
                    self.assertEqual(said, [], menu)  # a callback offer gets nothing at all
            holder[menu] = expected
        return holder

    def test_R23_information_only_agent(self):
        self.run_word(True, "agent")

    def test_R23_information_only_representative(self):
        self.run_word(True, "representative")

    def test_R23_restricted_agent(self):
        self.run_word(False, "agent")

    def test_R23_restricted_representative(self):
        self.run_word(False, "representative")


class EveryPathOnASilencedMenuTests(unittest.TestCase):
    """The engine's single output hook: whatever the path, a silenced menu returns the silence reason first."""

    PATHS = [("dtmf", "1"), ("dtmf", "2"), ("dtmf", "12"), ("dtmf", "*"), ("speech", "agent"),
             ("speech", "representative"), ("speech", "yes"), ("speech", "no"), ("speech", "one"),
             ("speech", "operator")]

    def check_mode(self, info_only):
        line, req, created, session = make(info_only)
        self.addCleanup(line.close)
        for reason, menu, l2_callback in SILENCED:
            session.last_menu = menu
            session.last_detection = (Detection("ivr", "high", ("callback offer requires task authorization; "
                                                                "otherwise stay in queue", menu))
                                      if l2_callback else None)
            for via, value in self.PATHS:
                session.menu_seq += 1
                why = session._menu_output_refused(via, value)
                self.assertIsNotNone(why, (reason, via, value))
                self.assertIn("nothing is sent", why, (reason, via, value))  # the whole-menu reason, first
        # and the same through the real tool: nothing reaches the provider
        session.last_menu, session.last_detection = SILENCED[1][1], None
        session.digits_gen = -1
        seen = len(line.adapter.created)
        out = line.svc.run(session.press_digits("1"))
        self.assertFalse(out["ok"])
        self.assertEqual(len(line.adapter.created), seen)

    def test_R23_every_path_silenced_information_only(self):
        self.check_mode(True)

    def test_R23_every_path_silenced_restricted(self):
        self.check_mode(False)


class GenericSpeechRuleTests(unittest.TestCase):
    def speech(self, menu, word="agent", info_only=True):
        names = menu_gate.normalise_names(NAMES)
        return menu_gate.check_speech(word, menu, menu_gate.is_callback_offer(menu, False, names),
                                      info_only=info_only, intro_names=NAMES)

    def test_R23_generic_speech_only_on_harmless_sentences(self):
        for ok in ("Please hold.", "Please stay on the line.", "All agents are busy.", "All agents are currently busy.",
                   "Your call will be answered in the order received.", "Thank you for holding.",
                   "The estimated wait time is 12 minutes.", "Estimated wait time is about twenty five minutes.",
                   "Thank you for calling Delta Air Lines. Please continue to hold.",
                   "All agents are busy. For baggage, press 1.", "For an agent, say agent."):
            for mode in (True, False):
                self.assertIsNone(self.speech(ok, info_only=mode), ok)
        for bad in ("Please select an option.", "Please hold. Your seat is ready.", "The estimated wait time is 12.",
                    "The estimated wait time is 12 dollars.", "The estimated wait time is long.",
                    "For baggage, press 1. Please hold.", "For baggage, press 1. To repeat, press 9.",
                    "Please hold for a moment while we check.", "Hello."):
            for mode in (True, False):
                self.assertIsNotNone(self.speech(bad, info_only=mode), bad)

    def test_R23_callback_paraphrases_are_callback_offers(self):
        names = menu_gate.normalise_names(NAMES)
        for menu in ("We can ring you back.", "Would you like a ring back?", "We will call you.", "We'll call you.",
                     "Keep your place in line.", "To hold your place, press 1.", "We can save your place.",
                     "Keep your spot.", "We will get back to you.", "We can contact you.", "We will reach you.",
                     "We will return your call.", "We can call you back later."):
            self.assertTrue(menu_gate.is_callback_offer(menu, False, names), menu)
            for mode in (True, False):
                self.assertIsNotNone(self.speech(menu, info_only=mode), menu)
        self.assertFalse(menu_gate.is_callback_offer("Thank you for calling Delta. For baggage, press 1.", False,
                                                     names))


class ReviewerFixtureTests(unittest.TestCase):
    def run_fixture(self, name):
        out = subprocess.run([sys.executable, "-B", str(ROOT / "tests/l1/r1d21" / name)], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]

    def assert_only_decided(self, rows):
        failing = [r for r in rows if not r["pass_case"]]
        self.assertTrue(failing)
        for r in failing:  # only the decided refusal of "Please select an option." (not intro/option/hold line)
            self.assertEqual(r["menu"], "Please select an option.", r)
            self.assertTrue(r["expected"] and not r["sent"])

    def test_R23_reviewer_no_option_callback_fixture(self):
        # Round 25: the fixture asserts its precondition that L2 labels these menus ivr. Under the L2 round 5
        # contract they are automated_assistant, so it stops there. The same menus are covered end to end, through
        # real detection and the new contract, by ReviewerCasesThroughDetectionTests above.
        out = subprocess.run([sys.executable, "-B", str(ROOT / "tests/l1/r1d21/callback_no_options.py")], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("automated_assistant", out.stderr)  # the precondition, not a send, is what fails
        self.assertNotIn('"sent": true', out.stdout)

    def test_R23_reviewer_approved_edges_fixture(self):
        rows = self.run_fixture("approved_edges.py")
        self.assertEqual(len(rows), 124)
        self.assert_only_decided(rows)

    def test_R23_reviewer_delta_name_fixtures_all_pass(self):
        for name, count in (("intro_edges_delta.py", 132), ("extra_delta.py", 10)):
            rows = self.run_fixture(name)
            self.assertEqual(len(rows), count, name)
            self.assertEqual([r["case"] for r in rows if not r["pass_case"]], [], name)


if __name__ == "__main__":
    unittest.main()
