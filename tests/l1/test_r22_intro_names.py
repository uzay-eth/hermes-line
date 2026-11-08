"""Round 22: R1b delta 20 (N16-G, N16-H). Intro names are the task's approved business names, matched as the whole
rest of their sentence; any token with a digit (other than an announced key) and currency/surcharge words silence the
menu. ``r1d20/intro_edges.py`` is the reviewer's fixture, unchanged."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from harness import Line
from plugin.line.engine import menu_gate
from plugin.line.engine.contracts import GateRejected
from test_r16c_fallback_question import Q
from test_r19_small_gate import CaseMatrixTests

ROOT = Path(__file__).resolve().parents[2]
SEND, BLOCK = True, False
INTROS = [  # the reviewer's intro table: (case, leading text, expected with "Sample Air" approved)
    ("ordinary welcome", "Welcome to Sample Air.", SEND),
    ("ordinary thanks", "Thank you for calling Sample Air.", SEND),
    ("all fixed intros", "Your call is important to us. Please listen carefully. Our menu options have changed. "
     "Calls may be monitored or recorded. Para español, oprima nine.", SEND),
    ("name callback blocked", "Welcome to Callback Air.", BLOCK),
    ("name payment blocked", "Welcome to Payment Air.", BLOCK),
    ("name split blocked", "Welcome to Sample Air hang. Up.", BLOCK),
    ("no-name callback promise", "Thank you for calling. We'll ring you back.", BLOCK),  # N16-G, inverted
    ("no-name callback promise punctuated", "Thank you for calling; we'll ring you back!", BLOCK),  # inverted
    ("normal-name callback promise", "Welcome to Sample. We'll ring back.", BLOCK),  # inverted
    ("no-name payment statement", "Thank you for calling. Baggage incurs surcharges.", BLOCK),  # inverted
    ("normal-name payment statement", "Welcome to Sample Air. Surcharges apply.", BLOCK),  # inverted
    ("payment non-intro control", "Baggage incurs surcharges.", BLOCK),
    ("callback non-intro control", "We'll ring you back.", BLOCK),
    ("callback blocked verb control", "Thank you for calling. We'll call you back.", BLOCK),
    ("amount name ten", "Thank you for calling Sample ten.", BLOCK),
    ("amount intro dollar", "Welcome to Sample Air $35.", BLOCK),
    ("unknown greeting", "Hello there.", BLOCK),
    ("oversized name", "Welcome to One Very Long Sample Airline Name.", BLOCK),
    # punctuation-loss variants (round 22)
    ("callback promise no punctuation", "Thank you for calling we'll ring you back", BLOCK),
    ("payment statement no punctuation", "Welcome to Sample Air surcharges apply", BLOCK),
]
AMOUNTS = ["$35", "£35", "€35", "35", "thirty five", "half", "quarter", "1.50", "35USD", "USD35", "35p", "35c",
           "ten dollars", "20 EUR", "GBP 20", "twenty euros", "ten pounds sterling"]


def rows(intro_rows, approved):
    out = []
    for name, lead, expected in intro_rows:
        exp = expected and (approved or "Sample Air" not in lead)
        out.append((name + " key", f"{lead} For baggage, press 1.", "dtmf", "1", exp, exp, exp))
        out.append((name + " speech", f"{lead} For baggage, say agent.", "speech", "agent", exp, exp, exp))
    return out


class ReviewerTablesThroughTheEngineTests(CaseMatrixTests):
    def test_R22_intro_table_with_the_name_approved(self):
        for mode in (True, False):
            self.run_mode(mode, rows(INTROS, True), intro_names=["Sample Air"])

    def test_R22_intro_table_with_no_name_approved(self):
        for mode in (True, False):
            self.run_mode(mode, rows(INTROS, False))

    def test_R22_amount_table(self):
        matrix = []
        for amount in AMOUNTS:
            matrix.append((amount + " key", f"For baggage, press 1. For {amount}, press 2.", "dtmf", "1",
                           BLOCK, BLOCK, BLOCK))
            matrix.append((amount + " speech", f"For baggage, say agent. For {amount}, press 2.", "speech",
                           "agent", BLOCK, BLOCK, BLOCK))
        for mode in (True, False):
            self.run_mode(mode, matrix)

    def test_R22_delta_air_lines_works_only_when_approved(self):
        menu = "Thank you for calling Delta Air Lines. For baggage, press 1."
        for mode in (True, False):
            self.run_mode(mode, [("delta approved", menu, "dtmf", "1", SEND, SEND, SEND)],
                          intro_names=["Delta Air Lines"])
            self.run_mode(mode, [("delta unapproved", menu, "dtmf", "1", BLOCK, BLOCK, BLOCK)],
                          intro_names=["Sample Air"])
            self.run_mode(mode, [("no name", "Thank you for calling. For baggage, press 1.", "dtmf", "1",
                                  SEND, SEND, SEND)])

    def test_R22_reviewer_fixture_differs_only_by_refusing_unapproved_names(self):
        out = subprocess.run([sys.executable, "-B", str(ROOT / "tests/l1/r1d20/intro_edges.py")], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        found = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
        self.assertEqual(len(found), 132)
        failing = [r for r in found if not r["pass_case"]]
        self.assertTrue(all(r["expected"] and not r["sent"] and "Sample Air" in r["menu"] for r in failing))
        self.assertEqual({r["case"] for r in failing}, {"ordinary welcome", "ordinary thanks", "generic agent"})


class IntroNameRuleTests(unittest.TestCase):
    def keys(self, menu, names=(), digits="1"):
        cb = menu_gate.is_callback_offer(menu, False, menu_gate.normalise_names(names))
        return menu_gate.check_keys(digits, menu, cb, info_only=True, intro_names=names)

    def test_R22_the_name_is_the_whole_remainder_of_its_sentence(self):
        names = ("Delta", "Delta Air Lines")
        for ok in ("Welcome to Delta.", "Welcome to Delta Air Lines.", "Thank you for calling Delta.",
                   "Thank you for calling.", "Welcome to Delta, your call is important to us."):
            self.assertIsNone(self.keys(f"{ok} For baggage, press 1.", names), ok)
        for bad in ("Welcome to Delta Air.", "Welcome to Delta Air Lines today.", "Welcome to.",
                    "Welcome to Delta. We'll ring you back.", "Thank you for calling Delta we'll ring back.",
                    "Thank you for calling Sample Air."):
            self.assertIsNotNone(self.keys(f"{bad} For baggage, press 1.", names), bad)

    def test_R22_digits_only_as_an_announced_key(self):
        for ok, digits in (("For baggage, press 1.", "1"), ("For an agent, press pound.", "#"),
                           ("For baggage, press one.", "1"), ("For reservations, dial 123.", "123")):
            self.assertIsNone(menu_gate.check_keys(digits, ok, False, info_only=False), ok)
        for bad in ("For baggage, press 1. Gate 35.", "For baggage, press 1st.", "For baggage, press 1. Bags weigh "
                    "twenty pounds.", "For baggage, press 1. A surcharge may apply."):
            self.assertIsNotNone(menu_gate.check_keys("1", bad, False, info_only=False), bad)

    def test_R22_intro_names_are_validated_and_shown_with_the_approval(self):
        line = Line()
        self.addCleanup(line.close)
        out = line.request(mode="owner", fallback_question=Q, intro_names=["Delta Air Lines"])
        self.assertEqual(out["options"]["intro_names"], ["Delta Air Lines"])
        self.assertIn("Delta Air Lines", out["intro_note"])
        self.assertEqual(line.store.task_options(out["task_id"])["intro_names"], ["Delta Air Lines"])
        for bad in (["Callback Air"], ["Air 35"], ["Payment Services"], ["Delta"] * 5, "Delta",
                    ["One Two Three Four Five Six"], ["Sample $ Air"]):
            with self.assertRaises(GateRejected, msg=bad):
                line.request(mode="owner", intro_names=bad)
        self.assertNotIn("intro_names", line.request().get("options", {}))  # default: none


if __name__ == "__main__":
    unittest.main()
