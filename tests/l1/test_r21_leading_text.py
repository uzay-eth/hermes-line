"""Round 21: R1b delta 19 N16-F. Text before the first option is no longer dropped as a greeting unchecked: it is
ignored only when every leading sentence is a known harmless intro; anything else silences the menu, keypad and
speech alike. Money words and spoken amounts are blocked. ``r1d19/extra.py`` is the reviewer's fixture, unchanged."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from plugin.line.engine import menu_gate
from test_r19_small_gate import CaseMatrixTests

ROOT = Path(__file__).resolve().parents[2]
SEND, BLOCK = True, False
REVIEWER = [  # the delta-19 table: (case, menu, via, value, expected); expected holds in both modes
    ("prelude debit key", "Selecting baggage debits your account. For baggage, press 1.", "dtmf", "1", BLOCK),
    ("prelude debit speech", "Selecting baggage debits your account. For baggage, say agent.", "speech", "agent", BLOCK),
    ("debit trailing control", "For baggage, press 1. Selecting baggage debits your account.", "dtmf", "1", BLOCK),
    ("ordinary greeting", "Welcome to Sample Air. For baggage, press 1.", "dtmf", "1", SEND),
    ("prelude deduction", "Selecting baggage deducts thirty five dollars from your balance. For baggage, press 1.",
     "dtmf", "1", BLOCK),
]
INTROS = ("Welcome to Sample Air.", "Thank you for calling Sample Air.", "Your call is important to us.",
          "Please listen carefully, our menu options have changed.", "Calls may be monitored or recorded.",
          "Para español, oprima 9.")


class ReviewerTableThroughTheEngineTests(CaseMatrixTests):
    def test_R21_reviewer_table_information_only(self):
        self.run_mode(True, [(n, m, v, val, e, e, e) for n, m, v, val, e in REVIEWER], intro_names=["Sample Air"])

    def test_R21_reviewer_table_restricted(self):
        self.run_mode(False, [(n, m, v, val, e, e, e) for n, m, v, val, e in REVIEWER], intro_names=["Sample Air"])

    def test_R21_reviewer_fixture_passes(self):
        out = subprocess.run([sys.executable, "-B", str(ROOT / "tests/l1/r1d19/extra.py")], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        rows = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
        self.assertEqual(len(rows), 10)
        # Round 22 (N16-G): the fixture's session approves no business name, so "Welcome to Sample Air." is refused
        # there; with the name approved it is sent (the engine tables above).
        self.assertEqual({r["case"] for r in rows if not r["pass_case"]}, {"ordinary greeting"})
        self.assertTrue(all(not r["sent"] for r in rows if not r["pass_case"]))


class LeadingTextTests(unittest.TestCase):
    NAMES = ("Sample Air",)  # round 22: the task's approved business name

    def keys(self, digits, menu, info_only=True):
        cb = menu_gate.is_callback_offer(menu, False, menu_gate.normalise_names(self.NAMES))
        return menu_gate.check_keys(digits, menu, cb, info_only=info_only, intro_names=self.NAMES)

    def speech(self, phrase, menu, info_only=True):
        cb = menu_gate.is_callback_offer(menu, False, menu_gate.normalise_names(self.NAMES))
        return menu_gate.check_speech(phrase, menu, cb, info_only=info_only, intro_names=self.NAMES)

    def test_R21_the_sample_air_greeting_still_works(self):
        for mode in (True, False):
            self.assertIsNone(self.keys("1", "Welcome to Sample Air. For baggage, press 1.", mode))
            self.assertIsNone(self.speech("agent", "Welcome to Sample Air. For an agent, say agent.", mode))

    def test_R21_every_allowed_intro_alone_and_all_together(self):
        for intro in INTROS + (" ".join(INTROS),):
            menu = f"{intro} For baggage, press 1."
            for mode in (True, False):
                self.assertIsNone(self.keys("1", menu, mode), menu)
                self.assertIsNone(self.speech("agent", menu, mode), menu)

    def test_R21_an_unknown_intro_silences_keypad_and_speech(self):
        for intro in ("Hello there.", "Selecting baggage grabs your seat.", "Good news.", "Para español, marque 9.",
                      "Welcome to the sample air baggage help line.", "Please hold for my sister."):  # round 23:
            # "Please hold." alone is now an allowed hold line
            menu = f"{intro} For baggage, press 1."
            for mode in (True, False):
                for why in (self.keys("1", menu, mode), self.speech("agent", menu, mode)):
                    self.assertIsNotNone(why, menu)
                    if "9" not in intro:  # "marque 9" is refused earlier, as an amount
                        self.assertIn("known harmless intro", why, menu)

    def test_R21_the_spanish_option_is_ignored_and_never_pressed(self):
        menu = "Para español, oprima 9. For baggage, press 1."
        self.assertIsNone(self.keys("1", menu))
        self.assertIsNotNone(self.keys("9", menu))  # "oprima" is not a key verb
        self.assertIsNotNone(self.keys("1", "For baggage, press 1. Para español, oprima 9."))  # only before options

    def test_R21_an_intro_name_is_still_checked(self):
        for menu in ("Welcome to Sample Air hang. Up. For baggage, press 1.",
                     "Welcome to Account Services. For baggage, press 1.",
                     "Thank you for calling Sample Air ten. For baggage, press 1."):
            for mode in (True, False):
                self.assertIsNotNone(self.keys("1", menu, mode), menu)

    def test_R21_thank_you_for_calling_is_not_a_callback_offer(self):
        menu = "Thank you for calling Sample Air. For reservations, press 2."
        self.assertFalse(menu_gate.is_callback_offer(menu, False, menu_gate.normalise_names(self.NAMES)))
        self.assertTrue(menu_gate.is_callback_offer("Welcome to Sample Air. We can call you back.", False))

    def test_R21_money_words_and_amounts_silence_the_menu(self):
        words = ["debit", "debits", "deduct", "deducts", "dollar", "dollars", "cent", "cents", "balance",
                 "account", "price", "cost"]
        for word in words:
            menu = f"For baggage, press 1. To hear the {word}, press 2."
            for mode in (True, False):
                self.assertIn(word, self.keys("1", menu, mode), word)
                self.assertIn(word, self.speech("agent", menu, mode), word)
        for amount in ("$35", "thirty five", "35", "ten", "two hundred"):
            menu = f"For baggage, press 1. For {amount} extra, press 2."
            for mode in (True, False):
                self.assertIsNotNone(self.keys("1", menu, mode), amount)
        self.assertIsNone(self.keys("1", "For baggage, press one."))  # a key word after a key verb is not an amount
        self.assertIsNone(self.keys("0", "Press zero for general information."))


if __name__ == "__main__":
    unittest.main()
