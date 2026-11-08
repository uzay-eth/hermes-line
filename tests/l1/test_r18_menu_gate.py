"""Round 18: R1 delta 13 N16 (one final output gate for restricted tasks' menu actions) and N17 (a menu turn in flight
never acts after the call has moved on). ``probe_hold_r1d13.py`` is the reviewer's probe, copied unchanged; its three
cases are inverted here."""
import threading
import time
import unittest
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import fallback
from test_flows import reach_human
from test_r16c_fallback_question import Q, InfoOnlyMenuTests, owner_legs

GUIDANCE = "Ask about the bag fee. Decline any callback offer. Do not pay anything. Do not change the booking."
MENU = "For baggage, press 2. For reservations, press 3."


def kinds(line, call_id):
    return [e["kind"] for e in line.store.events(call_id)]


class ProbeInvertedTests(unittest.TestCase):
    """The three cases of fixtures/probe_hold.py, inverted."""

    def _menu(self, menu, **kw):
        t = InfoOnlyMenuTests()
        t.setUp()
        self.addCleanup(t.doCleanups)
        return t._menu(menu, **kw)

    def test_N16_probe1_a_callback_offer_never_gets_a_spoken_yes(self):
        line, req, relay, results = self._menu(
            "To get a call back when an agent is available, say yes. To stay on the line, say no.", phrase="yes")
        # Round 25 (L2 round 5 contract): a conversational callback offer is automated_assistant, so no menu turn
        # runs; the engine's fixed-phrase path sends nothing at all for callback wording.
        skipped = lambda: [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "ivr_action_skipped"]
        wait_for(skipped, what="nothing said for the callback offer")
        self.assertEqual(skipped()[0]["why"], "callback, payment or change wording")
        time.sleep(0.2)
        self.assertEqual(relay.texts(), [])  # not "yes", and not anything else
        self.assertNotIn("ivr_action", kinds(line, req["call_id"]))

    def test_N16_probe2_authorize_a_charge_key_is_refused(self):
        line, req, relay, results = self._menu("To authorize a charge, press 1. For an agent, press 2.",
                                               presses=["1", "2"])
        wait_for(lambda: len(results) == 2)
        self.assertFalse(results[0]["ok"])
        self.assertIn("payment", results[0]["error"])
        self.assertFalse(results[1]["ok"], results[1])  # round 20: the whole menu is silenced, the agent key too
        self.assertEqual(relay.of_type("sendDigits"), [])

    def test_N17_probe3_a_late_menu_reply_says_nothing_after_the_person_answers(self):
        release, entered = threading.Event(), threading.Event()

        def slow(driver, text):
            if "[phone menu]" in text:
                entered.set()
                release.wait(8)
                return ["yes"]
            return ["WAIT"]
        line = Line(reply=slow)
        self.addCleanup(line.close)
        self.addCleanup(release.set)
        req = line.request(mode="owner", fallback_question=Q, owner_ring_timeout_s=20)
        line.approve(req["task_id"])
        created = wait_for(lambda: line.adapter.created and line.adapter.created[0])
        relay = line.connect(req, created)
        relay.prompt(MENU)
        self.assertTrue(entered.wait(3))
        reach_human(line, relay)
        line.wait_state(req["call_id"], "ringing_owner")
        release.set()
        wait_for(lambda: "stale_turn_dropped" in kinds(line, req["call_id"]), what="stale turn dropped")
        self.assertNotIn("yes", [t.strip().lower() for t in relay.texts()])


class CrossingPhaseTests(unittest.TestCase):
    """A delayed menu reply (a key press and a spoken phrase) released after the call moved on."""

    def setUp(self):
        for name, value in (("QUIET_AFTER_ANSWER_S", 0.1), ("QUIET_OTHERWISE_S", 0.3), ("ANSWER_MAX_S", 1.5)):
            patcher = mock.patch.object(fallback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.release, self.entered = threading.Event(), threading.Event()
        self.addCleanup(self.release.set)
        self.results = []

    def reply(self, driver, text):
        if "[phone menu]" in text:
            self.entered.set()
            self.release.wait(8)
            self.results.append(driver.tool("press_digits", "2"))  # valid for this menu, were it still current
            return ["agent"]  # a safe menu word, were it still current
        if "[business]" in text:
            return ["Hi, I'm calling about the bag fee."]
        return ["WAIT"]

    def start(self, **request):
        line = Line(reply=self.reply)
        self.addCleanup(line.close)
        req = line.request(**request)
        line.approve(req["task_id"])
        created = wait_for(lambda: line.adapter.created and line.adapter.created[0])
        session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                           and line.session(req["call_id"]))
        for d in line.drivers:
            d.call_id = req["call_id"]
        relay = line.connect(req, created)
        relay.prompt(MENU)
        self.assertTrue(self.entered.wait(3))
        return line, req, session, relay

    def assert_no_stale_effect(self, line, req, relay):
        self.release.set()
        wait_for(lambda: self.results, what="the late tool call")
        self.assertFalse(self.results[0]["ok"])
        wait_for(lambda: "stale_turn_dropped" in kinds(line, req["call_id"]), what="stale turn dropped")
        self.assertEqual(relay.of_type("sendDigits"), [])
        self.assertNotIn("agent", [t.strip().lower() for t in relay.texts()])
        self.assertIn("stale_turn_cancelled", kinds(line, req["call_id"]))

    def test_N17_crossing_human_detection_agent_mode(self):
        line, req, session, relay = self.start(mode="agent", goal=GUIDANCE)
        reach_human(line, relay)
        line.wait_state(req["call_id"], "agent_talking")
        self.assert_no_stale_effect(line, req, relay)
        relay.wait_text("Hi, I'm calling about the bag fee.")  # the human turn runs normally afterwards

    def test_N17_crossing_the_owner_ring(self):
        line, req, session, relay = self.start(mode="owner", fallback_question=Q, owner_ring_timeout_s=20)
        reach_human(line, relay)
        line.wait_state(req["call_id"], "ringing_owner")
        self.assert_no_stale_effect(line, req, relay)

    def test_N17_crossing_the_fallback_question(self):
        line, req, session, relay = self.start(mode="owner", fallback_question=Q, owner_ring_timeout_s=20)
        session.options["owner_ring_timeout_s"] = 0.2
        reach_human(line, relay)
        relay.wait_text(fallback.question_line("Sam", Q))
        self.assert_no_stale_effect(line, req, relay)
        relay.prompt("The first checked bag is 35 dollars.")
        relay.wait_text(fallback.THANKS_LINE)

    def test_N17_crossing_owner_acceptance(self):
        line, req, session, relay = self.start(mode="owner", fallback_question=Q, owner_ring_timeout_s=20)
        reach_human(line, relay)
        line.wait_state(req["call_id"], "ringing_owner")
        leg = owner_legs(line)[0]
        line.post(f"/twilio/voice/owner/accept/{req['call_id']}", {"CallSid": leg["sid"], "Digits": "1"})
        line.wait_state(req["call_id"], "owner_connected")
        self.assert_no_stale_effect(line, req, relay)

    def test_N17_a_late_finish_or_owner_request_does_nothing(self):
        release, entered, results = threading.Event(), threading.Event(), []

        def slow(driver, text):
            if "[phone menu]" in text:
                entered.set()
                release.wait(8)
                results.append(driver.tool("request_finish", "done", "Booked."))
                results.append(driver.tool("request_owner"))
                return ["WAIT"]
            return ["Okay."]
        line = Line(reply=slow)
        self.addCleanup(line.close)
        self.addCleanup(release.set)
        req, created = line.dial(goal=GUIDANCE)
        for d in line.drivers:
            d.call_id = req["call_id"]
        relay = line.connect(req, created)
        relay.prompt(MENU)
        self.assertTrue(entered.wait(3))
        reach_human(line, relay)
        line.wait_state(req["call_id"], "agent_talking")
        release.set()
        wait_for(lambda: len(results) == 2)
        self.assertFalse(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        time.sleep(0.3)
        self.assertEqual(line.state(req["call_id"]), "agent_talking")  # not hung up, owner not rung
        self.assertEqual([c for c in line.adapter.created if c["to"] != created["to"]], [])


# Round 19: the round-18 GateTests (meaning-parsing gate) are superseded by the smaller gate and its case
# matrix in test_r19_small_gate.py.


if __name__ == "__main__":
    unittest.main()
