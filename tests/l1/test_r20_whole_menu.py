"""Round 20: R1b delta 17 (N16-D, N16-E, N17-T). Whole-menu scope, positive option forms, new blocked words, and a
timed-out turn's tools refused.

``r1d17/`` holds the reviewer's fixtures, copied unchanged (``support.py``, ``edges.py``, ``rules.py``).
``ADDED`` below is the added-case list of ``edges.py``; every case runs through the real engine and Relay in both
modes, and the reviewer's own fixtures are run as well."""
import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from harness import Line, wait_for
from plugin.line.engine import menu_gate
from test_flows import reach_human
from test_r16c_fallback_question import Q, owner_legs
from test_r19_small_gate import DELIBERATE, CaseMatrixTests

ROOT = Path(__file__).resolve().parents[2]
SEND, BLOCK = True, False
ADDED = [  # (case, menu, via, value, reviewer expected); all now as the reviewer expects, in both modes
    ("comma charge", "Press 1 for baggage, authorize a charge.", "dtmf", "1", BLOCK),
    ("colon charge", "Press 1 for baggage: authorize a charge.", "dtmf", "1", BLOCK),
    ("semicolon charge", "Press 1 for baggage; authorize a charge.", "dtmf", "1", BLOCK),
    ("period charge", "Press 1 for baggage. This option authorizes a charge.", "dtmf", "1", BLOCK),
    ("newline charge", "Press 1 for baggage\nThis option authorizes a charge.", "dtmf", "1", BLOCK),
    ("speech semicolon", "Say agent for baggage; authorize a charge.", "speech", "agent", BLOCK),
    ("speech period", "Say agent for baggage. This option authorizes a charge.", "speech", "agent", BLOCK),
    ("shared key explicit", "For baggage, press 1. To authorize a charge, press 1.", "dtmf", "1", BLOCK),
    ("shared key shorthand", "For baggage, press 1. Key 1 authorizes a charge.", "dtmf", "1", BLOCK),
    ("shared key referent", "For baggage, press 1. The same key authorizes a charge.", "dtmf", "1", BLOCK),
    ("shared key safe", "For baggage, press 1. For reservations, press 1.", "dtmf", "1", SEND),
    ("shared speech unsafe", "For baggage, say agent. To authorize a charge, say agent.", "speech", "agent", BLOCK),
    ("callback separate context", "We can call you back instead of waiting. To reserve the next available agent, "
     "press 1.", "dtmf", "1", BLOCK),
    ("callback shared speech", "We can call you back instead of waiting. To reserve the next available agent, "
     "say agent.", "speech", "agent", BLOCK),
    ("hang up intact", "For baggage, press 1 to hang up.", "dtmf", "1", BLOCK),
    ("hang up split", "For baggage, press 1 to hang. Up.", "dtmf", "1", BLOCK),
    ("place in line split", "For baggage, press 1 to keep your place in. Line.", "dtmf", "1", BLOCK),
    ("buy baggage", "To buy baggage allowance, press 1.", "dtmf", "1", BLOCK),
    ("deposit reservations", "To put down a deposit for reservations, press 1.", "dtmf", "1", BLOCK),
    ("bill baggage", "To bill baggage to your account, press 1.", "dtmf", "1", BLOCK),
    ("standalone charge key", "To authorize a charge. Press 1.", "dtmf", "1", BLOCK),
    ("standalone charge speech", "To authorize a charge. Say agent.", "speech", "agent", BLOCK),
    ("clean pound", "For an agent, press #.", "dtmf", "#", SEND),
]


class AddedCasesThroughTheEngineTests(CaseMatrixTests):
    """The reviewer's added cases, end to end, both modes (the original matrix runs in the inherited tests)."""

    def test_R20_added_cases_information_only(self):
        self.run_mode(True, [(n, m, v, val, exp, exp, exp) for n, m, v, val, exp in ADDED])

    def test_R20_added_cases_restricted(self):
        self.run_mode(False, [(n, m, v, val, exp, exp, exp) for n, m, v, val, exp in ADDED])


class ReviewerFixtureTests(unittest.TestCase):
    """The reviewer's own socket-free fixtures, unchanged, against this code."""

    def run_fixture(self, path):
        out = subprocess.run([sys.executable, "-B", str(path)], cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]

    def test_R20_reviewer_added_cases_and_timeout_cases_all_pass(self):
        rows = self.run_fixture(ROOT / "tests/l1/r1d17/edges.py")
        self.assertEqual(len(rows), 52)
        self.assertEqual([r["case"] for r in rows if not r["pass_case"]], [])

    def test_R20_reviewer_original_matrix_differs_only_by_decided_refusals(self):
        rows = self.run_fixture(ROOT / "tests/l1/conformance_r1d15.py")
        self.assertEqual(len(rows), 66)
        for r in rows:
            if not r["pass_case"]:
                self.assertTrue(r["expected"] and not r["sent"], r["case"])  # only ever a refusal
                self.assertIn((r["case"], r["mode"] == "information-only"), DELIBERATE, r["case"])


class WholeMenuRuleTests(unittest.TestCase):
    NAMES = ("Sample Air",)  # round 22: an intro name must be one of the task's approved names

    def keys(self, digits, menu, info_only=True, detector=False):
        return menu_gate.check_keys(digits, menu, menu_gate.is_callback_offer(menu, detector), info_only=info_only,
                                    intro_names=self.NAMES)

    def speech(self, phrase, menu, info_only=True, detector=False):
        return menu_gate.check_speech(phrase, menu, menu_gate.is_callback_offer(menu, detector),
                                      info_only=info_only)

    def test_R20_whole_words_reserve_blocks_reservations_does_not(self):
        for mode in (True, False):
            self.assertIsNone(self.keys("1", "For reservations, press 1.", mode))
            self.assertIn("reserve", self.keys("1", "To reserve a seat. For reservations, press 1.", mode))
            self.assertIn("reserved", self.keys("1", "Seats are reserved. For baggage, press 1.", mode))
        self.assertIsNone(menu_gate.BLOCKED.search(menu_gate.joined("For reservations, press 1.")))
        self.assertIsNotNone(menu_gate.BLOCKED.search("reserve"))

    def test_R20_new_blocked_words_silence_the_whole_menu(self):
        for word in ("buy", "bill", "deposit", "sell", "order", "renew", "subscribe", "transfer", "reserve"):
            menu = f"We can {word} it for you. For baggage, press 1."
            for mode in (True, False):
                self.assertIn(word, self.keys("1", menu, mode), word)
                self.assertIn(word, self.speech("agent", menu, mode), word)  # not even "agent"

    def test_R20_a_blocked_word_anywhere_silences_even_agent(self):
        menu = "For baggage, press 1. To make a payment, press 9."
        self.assertIsNotNone(self.keys("1", menu))
        self.assertIsNotNone(self.speech("agent", menu))
        self.assertIsNotNone(self.speech("agent", "For baggage, press 1.", detector=True))  # an L2 callback offer

    def test_R20_positive_forms_only_even_without_a_blocked_word(self):
        for menu in ("To grab baggage allowance, press 1.", "To put down a hold for reservations, press 1.",
                     "Press 1 for baggage; hold your seat.", "For baggage, press 1. It holds your seat.",
                     "For baggage and lost items, press 1.", "For flight status, press 1.",
                     "For general information about seats, press 1.", "Press 1 for baggage claims."):
            for mode in (True, False):
                self.assertIsNotNone(self.keys("1", menu, mode), menu)
        for menu in ("For baggage, press 1.", "Press 1 for baggage.", "Reservations, press 1.",
                     "For general information, press 1.", "To speak with a representative, press 1.",
                     "To talk to an agent, press 1.", "For an agent, press 1.", "Press 1 to speak to an agent.",
                     "Welcome to Sample Air. For baggage, press 1.", "For baggage, press 1. For reservations, press 2."):
            for mode in (True, False):
                self.assertIsNone(self.keys("1", menu, mode), menu)

    def test_R20_agent_speech_follows_the_same_rules(self):
        self.assertIsNone(self.speech("agent", "For an agent, say agent."))
        self.assertIsNone(self.speech("agent", "For baggage, press 1."))  # a generic request on a clean menu
        self.assertIsNotNone(self.speech("agent", "To grab a seat, say agent."))
        self.assertIsNotNone(self.speech("agent", "Say agent. It holds your seat."))


class TimedOutTurnTests(unittest.TestCase):
    """N17-T: the menu turn times out while its tool call is still on its way; after the person answers (and after
    owner acceptance) the late tool call is refused and changes nothing."""

    def run_case(self, accept):
        release, entered, results = threading.Event(), threading.Event(), []

        def reply(driver, text):
            if "[phone menu]" in text:
                entered.set()
                release.wait(8)
                results.append(driver.tool("request_finish", "done", "Information received."))
                return ["agent"]
            return ["WAIT"]
        line = Line(reply=reply, turn_timeout_s=0.3)
        self.addCleanup(line.close)
        self.addCleanup(release.set)
        req = line.request(mode="owner", fallback_question=Q, owner_ring_timeout_s=20)
        line.approve(req["task_id"])
        created = wait_for(lambda: line.adapter.created and line.adapter.created[0])
        session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                           and line.session(req["call_id"]))
        for d in line.drivers:
            d.call_id = req["call_id"]
        relay = line.connect(req, created)
        relay.prompt("For baggage, press 2.")
        self.assertTrue(entered.wait(3))
        wait_for(lambda: "turn_timeout" in [e["kind"] for e in line.store.events(req["call_id"])], what="timeout")
        wait_for(lambda: session.current_turn is None, what="the timed-out turn is no longer current")
        self.assertIsNotNone(session.worker_turn)  # but its worker still runs
        reach_human(line, relay)
        line.wait_state(req["call_id"], "ringing_owner")
        if accept:
            leg = owner_legs(line)[0]
            line.post(f"/twilio/voice/owner/accept/{req['call_id']}", {"CallSid": leg["sid"], "Digits": "1"})
            line.wait_state(req["call_id"], "owner_connected")
        release.set()
        wait_for(lambda: results, what="the late tool call")
        self.assertFalse(results[0]["ok"])
        self.assertIsNone(session.finish_outcome)
        self.assertNotIn("finish_requested", [e["kind"] for e in line.store.events(req["call_id"])])
        blocked = [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "stale_turn_blocked"]
        self.assertTrue(blocked and blocked[0]["timed_out"])
        time.sleep(0.2)
        self.assertNotIn("agent", [t.strip().lower() for t in relay.texts()])

    def test_R20_timed_out_turn_tool_refused_with_no_phase_change(self):
        """Still in the menu: a key the timed-out turn presses late is refused (it may belong to an older menu)."""
        release, entered, results = threading.Event(), threading.Event(), []

        def reply(driver, text):
            if "[phone menu]" in text:
                entered.set()
                release.wait(8)
                results.append(driver.tool("press_digits", "2"))
                return ["WAIT"]
            return ["WAIT"]
        line = Line(reply=reply, turn_timeout_s=0.3)
        self.addCleanup(line.close)
        self.addCleanup(release.set)
        req = line.request(mode="owner", fallback_question=Q)
        line.approve(req["task_id"])
        created = wait_for(lambda: line.adapter.created and line.adapter.created[0])
        session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                           and line.session(req["call_id"]))
        for d in line.drivers:
            d.call_id = req["call_id"]
        relay = line.connect(req, created)
        relay.prompt("For baggage, press 2.")
        self.assertTrue(entered.wait(3))
        wait_for(lambda: "turn_timeout" in [e["kind"] for e in line.store.events(req["call_id"])], what="timeout")
        epoch = session.phase_epoch
        release.set()
        wait_for(lambda: results, what="the late key")
        self.assertEqual(session.phase_epoch, epoch)  # no phase change: only the timeout makes it stale
        self.assertFalse(results[0]["ok"])
        self.assertEqual(relay.of_type("sendDigits"), [])

    def test_R20_timed_out_turn_tool_refused_after_the_person_answers(self):
        self.run_case(accept=False)

    def test_R20_timed_out_turn_tool_refused_after_owner_acceptance(self):
        self.run_case(accept=True)


if __name__ == "__main__":
    unittest.main()
