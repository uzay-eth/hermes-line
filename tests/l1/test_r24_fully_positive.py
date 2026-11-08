"""Round 24: R1b delta 22 N16-J. A fully positive menu rule: an output (key or speech) is allowed only when EVERY
sentence of the current menu is an allowed intro, an allowed option or a known hold line. Any other sentence, even an
option the call would never choose, silences the whole menu. ``r1d22/`` holds the reviewer's fixtures, unchanged."""
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import menu_gate
from test_r16c_fallback_question import Q

ROOT = Path(__file__).resolve().parents[2]
NAMES = ["Delta", "Delta Air Lines"]
GUIDANCE = "Ask about the bag fee. Decline any callback offer. Do not pay anything. Do not change the booking."
MIXED = [  # the reviewer's N16-J table (inverted) plus the requested one-allowed-one-unknown menu
    ("For reservations, press 1. To have an agent phone you instead, press 2.", "dtmf", "1"),
    ("For reservations, say agent. To have an agent phone you instead, press 2.", "speech", "agent"),
    ("For reservations, press 1. To be contacted when an agent is free, press 2.", "dtmf", "1"),
    ("For reservations, say agent. To be contacted when an agent is free, press 2.", "speech", "agent"),
    ("For baggage, press 1. For seat maps, press 2.", "dtmf", "1"),
    ("For baggage, say agent. For seat maps, press 2.", "speech", "agent"),
    ("For baggage, press 1. To repeat, press 9.", "dtmf", "1"),  # was usable; the cost accepted in round 24
    ("For baggage, press 1. To repeat, press 9.", "speech", "agent"),
]


def make(info_only, reply):
    line = Line(reply=reply)
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


class MixedMenusThroughDetectionTests(unittest.TestCase):
    """Each menu spoken by the business: real L2 detection and dispatch; speech through the real menu turn, keys
    through the real press tool afterwards. Nothing reaches the Relay."""

    def run_mode(self, info_only):
        holder = {"word": "WAIT"}
        line, req, created, session = make(info_only, lambda d, text: [holder["word"]] if "[phone menu]" in text
                                           else ["WAIT"])
        self.addCleanup(line.close)
        relay = line.connect(req, created)
        wait_for(lambda: session.setup_seen)
        allowed = ("Thank you for calling Delta Air Lines. All agents are busy. For baggage, press 1. "
                   "For reservations, press 2.")
        for menu, via, value in MIXED + [(allowed, "dtmf", "1"), (allowed.replace("press 2", "say agent"),
                                                                  "speech", "agent")]:
            expected = menu.startswith("Thank you")
            holder["word"] = value if via == "speech" else "WAIT"
            seq, seen = session.menu_seq, len(relay.frames)
            relay.prompt(menu)
            wait_for(lambda: session.menu_seq > seq and session.last_menu == menu, what=f"menu dispatched: {menu}")
            if via == "dtmf":
                wait_for(lambda: session.current_turn is None, what="menu turn done")
                session.digits_gen = -1
                out = line.svc.run(session.press_digits(value))
                self.assertEqual(out["ok"], expected, (menu, info_only, out))
            if expected:
                wait_for(lambda: any((f.get("digits") if via == "dtmf" else f.get("token")) == value
                                     for f in relay.frames[seen:]), what=f"{menu} sent")
            else:
                if via == "speech":
                    wait_for(lambda: session.current_turn is None, what="menu turn done")
                time.sleep(0.05)
                sent = [f for f in relay.frames[seen:] if f.get("type") in ("sendDigits", "text")]
                self.assertEqual(sent, [], (menu, info_only))

    def test_R24_mixed_menus_silent_information_only(self):
        self.run_mode(True)

    def test_R24_mixed_menus_silent_restricted(self):
        self.run_mode(False)


class FullyPositiveRuleTests(unittest.TestCase):
    def check(self, menu, via, value, info_only):
        names = menu_gate.normalise_names(NAMES)
        cb = menu_gate.is_callback_offer(menu, False, names)
        f = menu_gate.check_keys if via == "dtmf" else menu_gate.check_speech
        return f(value, menu, cb, info_only=info_only, intro_names=NAMES)

    def test_R24_one_allowed_and_one_unknown_option_is_silent_for_keys_and_speech(self):
        menu = "For baggage, press 1. For seat maps, press 2."
        for mode in (True, False):
            for via, value in (("dtmf", "1"), ("speech", "agent"), ("speech", "representative")):
                why = self.check(menu, via, value, mode)
                self.assertIn("not an allowed one", why, (via, value))

    def test_R24_the_decision_does_not_depend_on_the_denylist(self):
        never = mock.patch.object(menu_gate, "BLOCKED", menu_gate.re.compile(r"(?!x)x"))  # matches nothing
        with never, mock.patch.object(menu_gate, "CALLBACK_WORDING", menu_gate.re.compile(r"(?!x)x")):
            for menu, via, value in MIXED:
                for mode in (True, False):
                    self.assertIsNotNone(self.check(menu, via, value, mode), menu)

    def test_R24_all_allowed_menus_still_work(self):
        for menu, via, value in (
                ("For baggage, press 1. For reservations, press 2.", "dtmf", "1"),
                ("Thank you for calling Delta Air Lines. All agents are busy. For baggage, press 1.", "dtmf", "1"),
                ("For general information, press 0. To speak to an agent, press 1.", "dtmf", "1"),
                ("For an agent, say agent. For baggage, press 2.", "speech", "agent"),
                ("Please hold. The estimated wait time is 5 minutes.", "speech", "representative")):
            for mode in (True, False):
                self.assertIsNone(self.check(menu, via, value, mode), menu)


class ReviewerFixtureTests(unittest.TestCase):
    def test_R24_no_option_callback_fixture_premise_changed_by_l2_round_5(self):
        # Round 25: callback_no_options_r23.py asserts that L2 labels its menus ivr; they are now automated_assistant.
        out = subprocess.run([sys.executable, "-B", str(ROOT / "tests/l1/r1d22/callback_no_options_r23.py")],
                             cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("automated_assistant", out.stderr)
        self.assertNotIn('"sent": true', out.stdout)

    def run_fixture(self, name):
        out = subprocess.run([sys.executable, "-B", str(ROOT / "tests/l1/r1d22" / name)], cwd=ROOT,
                             capture_output=True, text=True, timeout=150)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]

    def test_R24_mixed_callback_differs_only_by_the_accepted_repeat_option_cost(self):
        rows = self.run_fixture("mixed_callback.py")
        self.assertEqual(len(rows), 24)
        failing = [r for r in rows if not r["pass_case"]]
        self.assertEqual(len(failing), 4)
        for r in failing:
            self.assertIn("To repeat, press 9.", r["menu"])
            self.assertTrue(r["expected"] and not r["sent"])  # a refusal, never a send

    def test_R24_other_reviewer_fixtures_all_pass(self):
        for name, count in (("mixed_callback_controls.py", 12), ("silence_paths.py", 160), ("hold_edges.py", 60),
                            ("approved_edges_r23.py", 124)):
            rows = self.run_fixture(name)
            self.assertEqual(len(rows), count, name)
            self.assertEqual([r.get("case") for r in rows if not r["pass_case"]], [], name)


if __name__ == "__main__":
    unittest.main()
