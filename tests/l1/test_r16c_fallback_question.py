"""Round 16 addition (c): the approved hold-line trial's owner-timeout fallback, as engine-enforced task options.

When a person answers, the owner is rung. If the owner does not press 1 within ``owner_ring_timeout_s``, the line
itself asks ``fallback_question``, listens, thanks them and ends. Every line after the person answers is fixed text;
Hermes takes no turn, so it cannot invent a booking, a confirmation number or an identity; callbacks, payments and
changes are always forbidden; menu keys are single keys and menu replies a fixed vocabulary."""
import time
import unittest
from unittest import mock

from harness import BUSINESS, OWNER, Line, wait_for
from plugin.line.engine import fallback
from plugin.line.engine.contracts import GateRejected
from test_flows import reach_human

Q = "What is the checked-bag fee for a domestic economy ticket?"


def owner_legs(line):
    return [c for c in line.adapter.created if c["to"] == OWNER]


class FallbackBase(unittest.TestCase):
    def setUp(self):
        for name, value in (("QUIET_AFTER_ANSWER_S", 0.1), ("QUIET_OTHERWISE_S", 0.4), ("ANSWER_MAX_S", 1.5),
                            ("HOLDING_LINE_EVERY_S", 0.0)):
            patcher = mock.patch.object(fallback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make(self, **kw):
        line = Line(**kw)
        self.addCleanup(line.close)
        return line

    def call(self, line, *, timeout=20, question=Q, engine_timeout=0.3):
        req = line.request(mode="owner", title="Checked-bag fee", goal="Ask about the checked-bag fee.",
                           owner_ring_timeout_s=timeout, fallback_question=question)
        out = line.approve(req["task_id"])
        self.assertTrue(out["result"]["ok"], out)
        created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
        session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                           and line.session(req["call_id"]))
        if engine_timeout is not None:  # the engine's deadline, shortened for the test (validation allows 10-60)
            session.options["owner_ring_timeout_s"] = engine_timeout
        relay = line.connect(req, created)
        return req, session, relay

    def hermes_turns_after_human(self, line):
        return [t for d in line.drivers for t in d.turns if "[business]" in t and "[phone menu]" not in t]


class OwnerTimeoutTests(FallbackBase):
    def test_R16c_owner_timeout_asks_the_one_question_thanks_and_ends(self):
        line = self.make()
        req, session, relay = self.call(line)
        reach_human(line, relay)
        line.wait_state(req["call_id"], "ringing_owner")
        owner_sid = owner_legs(line)[0]["sid"]
        wait_for(lambda: "owner_ring_timeout" in [e["kind"] for e in line.store.events(req["call_id"])])
        wait_for(lambda: owner_sid in line.adapter.ended, what="owner leg ended")  # the engine ended it
        relay.wait_text(fallback.question_line("Sam", Q))
        relay.prompt("Let me check.")  # not an answer yet: the line keeps listening
        time.sleep(0.2)
        self.assertNotIn(fallback.THANKS_LINE, relay.texts())
        relay.prompt("For a domestic economy ticket the first checked bag is 35 dollars.")
        relay.wait_text(fallback.THANKS_LINE)
        line.wait_state(req["call_id"], "ended")
        line.wait_task(req["task_id"], "done")
        summary = wait_for(lambda: line.store.get_task(req["task_id"]).summary, what="summary")  # written at wrap-up
        self.assertIn(Q, summary)
        self.assertIn("first checked bag is 35 dollars", summary)
        self.assertEqual(self.hermes_turns_after_human(line), [])  # Hermes never spoke to the person
        kinds = [e["kind"] for e in line.store.events(req["call_id"])]
        self.assertIn("fallback_question_asked", kinds)
        self.assertIn("fallback_question_answered", kinds)

    def test_R16c_a_question_back_gets_one_fixed_reply_and_nothing_invented(self):
        line = self.make()
        req, session, relay = self.call(line)
        reach_human(line, relay)
        relay.wait_text(fallback.question_line("Sam", Q), timeout=5)
        relay.prompt("Sure, can I have your confirmation number?")
        relay.wait_text(fallback.clarify_line(Q))
        relay.prompt("Can I get your name?")  # a second question back gets no new line: nothing to give
        time.sleep(0.6)
        self.assertEqual(relay.texts().count(fallback.clarify_line(Q)), 1)
        relay.prompt("It's free for the first bag on some fares, otherwise 35 dollars.")
        relay.wait_text(fallback.THANKS_LINE)
        self.assertEqual(self.hermes_turns_after_human(line), [])

    def test_R16c_no_answer_thanks_them_and_ends_as_failed(self):
        line = self.make()
        req, session, relay = self.call(line)
        reach_human(line, relay)
        relay.wait_text(fallback.question_line("Sam", Q), timeout=5)
        relay.wait_text(fallback.NO_ANSWER_LINE, timeout=5)
        line.wait_task(req["task_id"], "failed")
        summary = wait_for(lambda: line.store.get_task(req["task_id"]).summary, what="summary")  # written at wrap-up
        self.assertIn("No reply was given", summary)  # round 30 wording
        self.assertIn("Outcome: unanswered; answer not verified.", summary)
        self.assertEqual(line.store.get_task(req["task_id"]).outcome, "unanswered")

    def test_R16c_owner_presses_1_in_time_and_no_question_is_asked(self):
        line = self.make()
        req, session, relay = self.call(line, engine_timeout=None)
        reach_human(line, relay)
        line.wait_state(req["call_id"], "ringing_owner")
        leg = owner_legs(line)[0]
        self.assertEqual(leg["timeout_s"], 20)  # Twilio's ring timeout is the task's
        line.post(f"/twilio/voice/owner/accept/{req['call_id']}", {"CallSid": leg["sid"], "Digits": "1"})
        line.wait_state(req["call_id"], "owner_connected")
        self.assertNotIn(fallback.question_line("Sam", Q), relay.texts())

    def test_R16c_owner_declines_and_the_question_is_asked(self):
        line = self.make()
        req, session, relay = self.call(line, engine_timeout=None)
        reach_human(line, relay)
        line.wait_state(req["call_id"], "ringing_owner")
        leg = owner_legs(line)[0]
        line.post(f"/twilio/voice/owner/accept/{req['call_id']}", {"CallSid": leg["sid"], "Digits": "2"})
        relay.wait_text(fallback.question_line("Sam", Q))
        line.status(req["call_id"], leg["sid"], "completed", owner=True)  # the owner leg's own end: asked once
        time.sleep(0.3)
        self.assertEqual(relay.texts().count(fallback.question_line("Sam", Q)), 1)

    def test_R16c_speech_while_the_owner_is_rung_gets_a_fixed_line_never_a_hermes_turn(self):
        line = self.make()
        req, session, relay = self.call(line, engine_timeout=None)
        reach_human(line, relay)
        line.wait_state(req["call_id"], "ringing_owner")
        relay.prompt("Hello? Are you still there? What's your booking reference?")
        relay.wait_text(fallback.holding_line("Sam"))
        self.assertEqual(self.hermes_turns_after_human(line), [])


class InfoOnlyMenuTests(FallbackBase):
    def _menu(self, menu, *, presses=(), phrase=None):
        results = []

        def reply(driver, text):
            if "[phone menu]" in text:
                driver.prompts = getattr(driver, "prompts", []) + [text]
                for digits in presses:
                    results.append(driver.tool("press_digits", digits))
                    driver.svc_ref["svc"].sessions[driver.call_id].digits_gen = -1
                return [phrase] if phrase else ["WAIT"]
            return ["Okay."]
        line = self.make(reply=reply)
        req = line.request(mode="owner", title="Checked-bag fee", goal="Ask about the checked-bag fee.",
                           fallback_question=Q)
        line.approve(req["task_id"])
        created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
        wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid)
        for d in line.drivers:
            d.call_id = req["call_id"]
        relay = line.connect(req, created)
        relay.prompt(menu)
        return line, req, relay, results

    def test_R16c_menu_keys_are_single_keys_only(self):
        line, req, relay, results = self._menu("For baggage, press 2. For reservations, press 3.",
                                               presses=["2345", "2"])
        wait_for(lambda: len(results) == 2)
        self.assertFalse(results[0]["ok"])
        self.assertIn("single key", results[0]["error"])
        self.assertTrue(results[1]["ok"])
        self.assertEqual([f["digits"] for f in relay.of_type("sendDigits")], ["2"])
        self.assertIn("information-only task", line.drivers[0].prompts[0])

    def test_R16c_callbacks_are_refused_even_when_the_goal_never_mentions_them(self):
        menu = "To get a call back when an agent is available, press 1. To stay on the line, press 2."
        line, req, relay, results = self._menu(menu, presses=["1", "2"])
        wait_for(lambda: len(results) == 2)
        self.assertFalse(results[0]["ok"])
        self.assertIn("callback", results[0]["error"])
        # Round 19: an information-only task presses only reservations, baggage, general information or an agent;
        # "stay on the line" is not on that list, so it stays silent and waits.
        self.assertFalse(results[1]["ok"])
        self.assertEqual(relay.of_type("sendDigits"), [])

    def test_R16c_a_spoken_menu_reply_cannot_carry_a_name_or_number(self):
        line, req, relay, results = self._menu("Please say your confirmation number or say agent.",
                                               phrase="ABC 123")
        # Round 25 (L2 round 5 contract): this line is now automated_assistant, so no menu turn runs and the
        # model is not asked. The engine answers a data request with its fixed "Agent." and nothing else.
        acted = lambda: [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "ivr_action"]
        wait_for(acted, what="fixed phrase")
        self.assertEqual(acted()[0], {"state": "automated_assistant", "phrase_id": "agent"})
        time.sleep(0.1)
        self.assertEqual(relay.texts(), ["Agent."])
        self.assertNotIn("ABC 123", relay.texts())
        self.assertTrue(fallback.menu_phrase_ok("agent"))
        self.assertTrue(fallback.menu_phrase_ok("Representative."))
        for bad in ("my name is Dana", "ABC 123", "one two three", "speak to an agent about my booking now please"):
            self.assertFalse(fallback.menu_phrase_ok(bad), bad)


class OptionValidationTests(unittest.TestCase):
    def test_R16c_options_are_validated_and_shown_with_the_approval(self):
        line = Line()
        self.addCleanup(line.close)
        out = line.request(mode="owner", owner_ring_timeout_s=20, fallback_question=Q)
        self.assertEqual(out["options"], {"owner_ring_timeout_s": 20, "fallback_question": Q})
        self.assertIn(Q, out["fallback"])
        self.assertIn("within 20 s", out["fallback"])
        self.assertEqual(line.store.task_options(out["task_id"])["fallback_question"], Q)
        bad = [dict(fallback_question="What is the fee for booking ABC123?"),
               dict(fallback_question="Hi, I'm Can. What is the bag fee?"),
               dict(fallback_question="What is the fee? And the size limit?"),
               dict(fallback_question="Can you call me back about the bag fee?"),
               dict(fallback_question="What would it cost to change my flight?"),
               dict(fallback_question="The bag fee."),
               dict(owner_ring_timeout_s=5), dict(owner_ring_timeout_s=61), dict(owner_ring_timeout_s=20.5)]
        for kw in bad:
            with self.assertRaises(GateRejected, msg=kw):
                line.request(mode="owner", **kw)
        with self.assertRaises(GateRejected):
            line.request(mode="agent", fallback_question=Q)  # it is about the owner's ring
        plain = line.request(mode="owner")
        self.assertNotIn("options", plain)
        self.assertEqual(line.store.task_options(plain["task_id"]), {})

    def test_R16c_the_briefing_marks_the_task_information_only(self):
        line = Line()
        self.addCleanup(line.close)
        out = line.request(mode="owner", fallback_question=Q)
        from plugin.line.engine.contracts import Direction
        brief = line.svc.briefing(line.store.get_task(out["task_id"]), Direction.OUTBOUND)
        self.assertIn("information-only task", brief)
        self.assertIn("accepting a callback", brief)


if __name__ == "__main__":
    unittest.main()
