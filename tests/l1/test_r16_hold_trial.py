"""Round 16: blockers for the real hold-line trial (docs/lanes/L2-hold-trial-plan.md preconditions 2 and 5), plus the
R1 delta 12 suffix-collision Medium."""
import time
import unittest
from pathlib import Path
from unittest import mock

from harness import BUSINESS, OWNER, SECRETS, Line, wait_for
from plugin.line.engine import deps
from plugin.line.engine.machine import _words
from test_r7_silence import StubDetector, unknown
from test_r13_owner_line import PIN, OwnerLineBase, agent as owner_agent, key

GUIDANCE = (Path(__file__).resolve().parents[2] / "fixtures" / "hold-sim" / "airline-agent-guidance.txt").read_text()
JUNK = ["[music]", "♪ ♫ ♪", "uh um", "thank you thank you thank you", "la la la", "[instrumental music]",
        "mm hmm", "silence"]


def owners_rung(line):
    return [c for c in line.adapter.created if c["to"] == OWNER]


class HoldNeverRingsOnAnAssumptionTests(unittest.TestCase):
    def _owner_call(self, detector=None):
        if detector is not None:
            patcher = mock.patch.object(deps, "HoldTracker", detector)
            patcher.start()
            self.addCleanup(patcher.stop)
        line = Line(ask=unknown, confirm_cooldown_s=0.0)
        self.addCleanup(line.close)
        req, created = line.dial(mode="owner")
        relay = line.connect(req, created)
        return line, req, relay

    def test_R16_hold_junk_gets_no_confirmation_speech_and_never_rings(self):
        line, req, relay = self._owner_call(StubDetector("unknown"))
        for junk in JUNK * 2:
            relay.prompt(junk)
        time.sleep(1.0)
        said = [t["text"] for t in line.store.transcript(req["call_id"]) if t["speaker"] == "hermes"]
        self.assertEqual(said, [line.cfg.disclosure()])  # nothing but the disclosure
        self.assertEqual(owners_rung(line), [])
        self.assertNotEqual(line.state(req["call_id"]), "ringing_owner")

    def test_R16_unclear_replies_never_ring_in_owner_mode_but_a_clear_yes_does(self):
        line, req, relay = self._owner_call(StubDetector("unknown"))
        for text in ("Your call is important to us.", "Please continue to hold.", "The next available agent.",
                     "Thank you for your patience.", "We value your business."):
            relay.prompt(text)
            time.sleep(0.15)
        time.sleep(0.5)
        kinds = [e["kind"] for e in line.store.events(req["call_id"])]
        self.assertNotIn("human_assumed", kinds)
        self.assertIn("human_not_assumed", kinds)
        self.assertEqual(owners_rung(line), [])
        relay.prompt("Am I speaking with a live person?")  # still unknown: no ring
        time.sleep(0.3)
        self.assertEqual(owners_rung(line), [])
        relay.prompt("Yes, speaking.")  # a clear yes right after our question
        line.wait_state(req["call_id"], "ringing_owner")
        self.assertEqual(len(owners_rung(line)), 1)

    def test_R16_an_l2_human_verdict_rings(self):
        line, req, relay = self._owner_call()  # the real L2 detector
        relay.prompt("Thank you for holding.")
        relay.prompt("Hi, this is Dana with Sample Air, how can I help you today?")
        line.wait_state(req["call_id"], "ringing_owner")
        self.assertEqual(len(owners_rung(line)), 1)

    def test_R16_agent_mode_keeps_talking_but_cannot_ring_without_a_confirmed_person(self):
        patcher = mock.patch.object(deps, "HoldTracker", StubDetector("unknown"))
        patcher.start()
        self.addCleanup(patcher.stop)

        def reply(driver, text):
            if "not certain they are a person" in text:
                driver.tool("request_owner")
            return ["Okay."]
        line = Line(ask=unknown, reply=reply)
        self.addCleanup(line.close)
        req, created = line.dial(mode="agent")
        relay = line.connect(req, created)
        for text in ("Hi. How are you?", "Hmm, who did you want?", "Go on then."):
            relay.prompt(text)
            time.sleep(0.2)
        line.wait_state(req["call_id"], "agent_talking")  # agent mode keeps talking, as before
        wait_for(lambda: "owner_ring_refused" in [e["kind"] for e in line.store.events(req["call_id"])],
                 what="ring refused")
        self.assertEqual(owners_rung(line), [])

    def test_R16_engine_artifacts_match_l2_known_hold_artifacts(self):
        from plugin.line import detect
        for junk in JUNK:
            self.assertEqual(_words(junk), [], junk)
            norm = detect._norm(junk)
            if norm:  # L2 treats the same lines as artifacts
                self.assertTrue(detect._noise(norm), junk)
        self.assertEqual(_words("Your call is important"), ["Your", "call", "is", "important"])


class TaskRestrictionTests(unittest.TestCase):
    MENU = "To get a call back when an agent is available, press 1. To stay on the line, press 2."

    def _ivr_call(self, goal, menu, presses):
        results = []

        def reply(driver, text):
            if "[phone menu]" in text:
                driver.prompts = getattr(driver, "prompts", []) + [text]
                for digits in presses:
                    results.append(driver.tool("press_digits", digits))
                    driver.svc_ref["svc"].sessions[driver.call_id].digits_gen = -1  # allow the next press
                return ["WAIT"]
            return ["Okay."]
        line = Line(reply=reply)
        self.addCleanup(line.close)
        req, created = line.dial(goal=goal)
        for d in line.drivers:
            d.call_id = req["call_id"]
        relay = line.connect(req, created)
        relay.prompt(menu)
        wait_for(lambda: len(results) == len(presses), what="presses tried")
        return line, req, relay, results

    def test_R16_callback_offer_menu_sends_no_key_at_all(self):
        # Round 20 (R1b delta 17): a menu that offers a callback anywhere sends nothing, not even the stay key.
        line, req, relay, results = self._ivr_call(GUIDANCE, self.MENU, ["1", "2"])
        self.assertFalse(results[0]["ok"])
        self.assertIn("callback", results[0]["error"])
        self.assertFalse(results[1]["ok"], results[1])
        self.assertEqual(relay.of_type("sendDigits"), [])  # the callback key never reached the provider
        prompt = line.drivers[0].prompts[0]
        self.assertIn("Detector: callback offer requires task authorization", prompt)
        self.assertIn("This task forbids: accepting a callback", prompt)
        self.assertIn("restricted_action_blocked", [e["kind"] for e in line.store.events(req["call_id"])])

    def test_R16_a_callback_offer_without_a_stay_option_refuses_every_key(self):
        line, req, relay, results = self._ivr_call(GUIDANCE, "We can call you back. Press 1 to accept.", ["1", "3"])
        self.assertFalse(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertEqual(relay.of_type("sendDigits"), [])

    def test_R16_payment_key_refused_and_a_change_department_blocked_too(self):
        # Round 19 (R1 delta 15): the gate no longer reads "reservations or to change a flight" as navigation; a
        # sentence with a conjunction or the word "change" backs no key. A clean department sentence still does.
        # Round 20: a blocked word anywhere in the menu silences the whole menu, so the clean "For baggage, press 4."
        # in the same menu is refused too.
        menu = "For reservations or to change a flight, press 2. To make a payment, press 3. For baggage, press 4."
        line, req, relay, results = self._ivr_call(GUIDANCE, menu, ["3", "2", "4"])
        self.assertFalse(results[0]["ok"])
        self.assertIn("payment", results[0]["error"])
        self.assertFalse(results[1]["ok"])
        self.assertFalse(results[2]["ok"], results[2])
        self.assertEqual(relay.of_type("sendDigits"), [])

    def test_R16_a_task_without_restrictions_presses_freely(self):
        line, req, relay, results = self._ivr_call("Book a table for four.", self.MENU, ["1"])
        self.assertTrue(results[0]["ok"])

    def test_R16_report_outcome_cannot_report_a_change_as_done(self):
        line = Line()
        self.addCleanup(line.close)
        req, created = line.dial(goal=GUIDANCE)
        session = line.session(req["call_id"])
        bad = line.svc.run(session.request_finish("done", "I changed your flight to Friday at 7."))
        self.assertFalse(bad["ok"])
        self.assertIn("cannot be reported as done", bad["error"])
        # Round 25: before a person answers, a restricted hold task cannot be finished as callback_expected or
        # failed at all; after a person answered it can, and the claim check above still applies.
        early = line.svc.run(session.request_finish("callback_expected", "The agent offered a change."))
        self.assertFalse(early["ok"])
        from plugin.line.engine.contracts import CallState
        self.assertTrue(line.svc.run(session.set_state(CallState.AGENT_TALKING, "test: a person answered")))
        ok = line.svc.run(session.request_finish("callback_expected", "The agent offered a change for 75 dollars."))
        self.assertTrue(ok["ok"])


class SevenDigitRuleTests(OwnerLineBase):
    """R1 delta 12 Medium: suffix collisions need more digits unless the number is already known."""

    def _verified(self, line):
        body, call, relay, session = self.call_in(line, sid="CA" + "8" * 32)
        self.verify(line, relay, session)
        return call, relay, session

    def _propose(self, line, session, destination):
        out = line.svc.in_call(session.agent_session_id, "owner_new_task", destination, "Check", "Check", "")
        self.assertTrue(out["ok"], out)
        return session.proposal, session._readback

    def test_R16_a_new_destination_needs_seven_digits(self):
        line = self.make()
        call, relay, session = self._verified(line)
        proposal, readback = self._propose(line, session, BUSINESS)
        self.assertEqual(proposal["code"], BUSINESS[-7:])
        self.assertIn("key the last seven digits", readback)
        key(relay, BUSINESS[-4:] + "#")  # four digits are not enough
        relay.wait_text("Those digits don't match")
        self.assertEqual(line.svc._voice_tickets, [])

    def test_R16_the_owners_own_number_keeps_four_digits(self):
        line = self.make()
        call, relay, session = self._verified(line)
        proposal, readback = self._propose(line, session, OWNER)
        self.assertEqual(proposal["code"], OWNER[-4:])
        self.assertIn("key the last four digits", readback)

    def test_R16_a_number_from_an_earlier_approved_task_keeps_four_digits(self):
        line = self.make()
        req, created = line.dial()  # an earlier, approved (dialled) task to BUSINESS
        line.svc.submit("hang_up", source="slash", call_id=req["call_id"])
        wait_for(lambda: req["call_id"] not in line.svc.sessions)
        call, relay, session = self._verified(line)
        proposal, readback = self._propose(line, session, BUSINESS)
        self.assertEqual(proposal["code"], BUSINESS[-4:])
        key(relay, BUSINESS[-4:] + "#")
        relay.wait_text("Confirmed.")


if __name__ == "__main__":
    unittest.main()
