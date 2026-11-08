"""State-machine paths over the real ingress (loopback), with the fake adapter and a scripted driver."""
import json
import re
import time
import unittest

from harness import BUSINESS, LINE, OWNER, Line, wait_for

CONFIRMED = "Yes, I am a live person"  # round 30: a shared clear_yes() entry (restricted tasks need one)


def finish_on(word, outcome="done", summary="Booked for Friday at 7 pm."):
    def reply(d, text):
        if word in text:
            d.tool("request_finish", outcome, summary)
            return ["Thank you, goodbye!"]
        return ["Hi! I'd like to book a table for four at 7 pm Friday."]
    return reply


def reach_human(line, relay, greeting="Thanks for calling Sample Bistro, this is Mike."):
    relay.prompt(greeting)
    relay.wait_text("live person?")
    relay.prompt(CONFIRMED)


class AgentModeTests(unittest.TestCase):
    def test_outbound_agent_completes_task(self):
        line = Line(reply=finish_on("booked"))
        try:
            req, created = line.dial()
            self.assertEqual(created["to"], BUSINESS)
            self.assertEqual(created["from"], LINE)
            self.assertEqual(created["time_limit_s"], line.cfg.max_call_minutes * 60)
            body = line.answer(req["call_id"], created["sid"])
            self.assertIn("welcomeGreeting=\"Hi, this is Hermes, an AI assistant", body)  # disclosure ON
            self.assertIn('dtmfDetection="true"', body)
            relay = line.relay(body)
            relay.send({"type": "setup", "callSid": created["sid"]})
            line.status(req["call_id"], created["sid"], "in-progress")
            reach_human(line, relay)
            line.wait_state(req["call_id"], "agent_talking")
            relay.wait_text("a table for four")
            relay.prompt("Great, you're booked for Friday at 7.")
            line.wait_state(req["call_id"], "ended")
            call = line.store.get_call(req["call_id"])
            self.assertEqual(call.end_reason, "completed")
            self.assertIn(created["sid"], line.adapter.ended)
            self.assertTrue(relay.of_type("end"))
            task = line.store.get_task(req["task_id"])
            self.assertEqual(task.status.value, "done")
            wait_for(lambda: line.delivered, what="summary delivery")
            task_id, text = line.delivered[0]
            self.assertEqual(task_id, req["task_id"])
            # Round 37: the summary quotes the call, never the model's finish note ("Booked for Friday at 7 pm.")
            self.assertTrue(text.startswith("A person answered; Hermes ended the call after"), text)
            self.assertIn('They: "Great, you\'re booked for Friday at 7."', text)
            self.assertNotIn("Booked for Friday at 7 pm.", text)
            speakers = [t["speaker"] for t in line.store.transcript(req["call_id"])]
            self.assertEqual(speakers[:2], ["hermes", "business"])  # disclosure first
            self.assertTrue(line.drivers[0].closed)
            self.assertIn("Never claim to be human", line.drivers[0].briefing)
            self.assertTrue(any(cid == req["call_id"] for cid, _ in line.events))
            # late webhooks never move a terminal call
            line.status(req["call_id"], created["sid"], "completed")
            self.assertEqual(line.state(req["call_id"]), "ended")
        finally:
            line.close()

    def test_disclosure_can_be_turned_off(self):
        line = Line(policy={"disclosure_enabled": False})
        try:
            req, created = line.dial()
            body = line.answer(req["call_id"], created["sid"])
            self.assertNotIn("welcomeGreeting", body)
        finally:
            line.close()

    def test_ivr_digits_then_hold_then_human(self):
        def reply(d, text):
            if "[phone menu]" in text:
                d.tool("press_digits", "2")
                return ["I pressed two."]  # must not be spoken to the menu after a key press
            return ["Hello, I'd like to change a flight."]
        line = Line(reply=reply)
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Thank you for calling Sample Airline. For reservations press 1. For changes press 2.")
            wait_for(lambda: relay.of_type("sendDigits"), what="digits")
            self.assertEqual(relay.of_type("sendDigits")[0]["digits"], "2")
            self.assertEqual(line.state(req["call_id"]), "ivr")
            time.sleep(0.1)
            self.assertNotIn("I pressed two", relay.spoken())
            self.assertIn("Pressed 2", [t["text"] for t in line.store.transcript(req["call_id"])])
            hold = "Please hold. Your call is important to us and will be answered in the order received."
            turns_before = len(line.drivers[0].turns)
            for _ in range(5):
                relay.prompt(hold)
            line.wait_state(req["call_id"], "on_hold")
            time.sleep(0.2)
            self.assertEqual(len(line.drivers[0].turns), turns_before)  # silent on hold: no Hermes turns
            self.assertEqual(line.drivers[0].asks, [])                   # rules decide; no model calls
            reach_human(line, relay, "Hi, thanks for waiting, this is Dana, how can I help you today?")
            line.wait_state(req["call_id"], "agent_talking")
            relay.wait_text("change a flight")
        finally:
            line.close()

    def test_digits_once_per_prompt_and_validated(self):
        def reply(d, text):
            if "[phone menu]" in text:
                d.results = [d.tool("press_digits", "1"), d.tool("press_digits", "1"),
                             d.tool("press_digits", "+17755550123")]
            return ["WAIT"]
        line = Line(reply=reply)
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("For English press 1.")
            wait_for(lambda: getattr(line.drivers[0], "results", None))
            ok, again, bad = line.drivers[0].results
            self.assertTrue(ok["ok"])
            self.assertFalse(again["ok"])
            self.assertFalse(bad["ok"])
            self.assertEqual(len(relay.of_type("sendDigits")), 1)
            self.assertNotIn("WAIT", relay.spoken())
        finally:
            line.close()


class OwnerModeTests(unittest.TestCase):
    def _to_ringing(self, line, **kw):
        req, created = line.dial(mode="owner", **kw)
        relay = line.connect(req, created)
        reach_human(line, relay)
        relay.wait_text("connect Sam")
        line.wait_state(req["call_id"], "ringing_owner")
        owner = line.adapter.created[-1]
        self.assertEqual(owner["to"], OWNER)
        self.assertEqual(owner["from"], LINE)
        return req, created, relay, owner

    def test_bridge_owner_into_conference(self):
        line = Line()
        try:
            req, created, relay, owner = self._to_ringing(line)
            status, whisper = line.post(f"/twilio/voice/owner/answer/{req['call_id']}", {"CallSid": owner["sid"]})
            self.assertEqual(status, 200)
            self.assertIn("<Gather", whisper)
            self.assertIn("Press 1 to take over", whisper)
            self.assertNotIn("<Conference", whisper)  # no join before acceptance
            status, joined = line.post(f"/twilio/voice/owner/accept/{req['call_id']}",
                                       {"CallSid": owner["sid"], "Digits": "1"})
            self.assertIn("<Conference", joined)
            conf = re.search(r">(hl-[^<]+)</Conference>", joined).group(1)
            line.wait_state(req["call_id"], "owner_connected")
            wait_for(lambda: relay.of_type("end"), what="relay end with handoff")
            self.assertEqual(json.loads(relay.of_type("end")[0]["handoffData"])["reason"], "takeover")
            relay.close()  # Twilio ends the <Connect> and closes the relay socket
            wait_for(lambda: line.session(req["call_id"]).relay is None, what="relay closed")
            status, moved = line.post(f"/twilio/voice/connect/action/{req['call_id']}?g=0",
                                      {"CallSid": created["sid"], "HandoffData": '{"reason":"takeover"}'})
            self.assertIn(f">{conf}</Conference>", moved)
            self.assertIn('endConferenceOnExit="true"', moved)
            self.assertEqual(line.store.get_call(req["call_id"]).mode.value, "owner")
            line.status(req["call_id"], owner["sid"], "completed", owner=True)
            line.wait_state(req["call_id"], "ended")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "owner_finished")
            line.wait_task(req["task_id"], "done")
            self.assertIn(created["sid"], line.adapter.ended)
        finally:
            line.close()

    def test_owner_declines_then_agent_continues(self):
        line = Line()
        try:
            req, created, relay, owner = self._to_ringing(line)
            status, bye = line.post(f"/twilio/voice/owner/accept/{req['call_id']}",
                                    {"CallSid": owner["sid"], "Digits": "9"})
            self.assertIn("<Hangup/>", bye)
            line.status(req["call_id"], owner["sid"], "completed", owner=True)
            line.wait_state(req["call_id"], "agent_talking")
            wait_for(lambda: any("could not be reached" in t for t in line.drivers[0].turns))
        finally:
            line.close()

    def test_owner_no_answer_without_fallback(self):
        line = Line()
        try:
            req, created, relay, owner = self._to_ringing(line, allow_agent_fallback=False)
            line.status(req["call_id"], owner["sid"], "no-answer", owner=True)
            relay.wait_text("isn't available")
            line.wait_state(req["call_id"], "ended")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "owner_unavailable")
        finally:
            line.close()

    def test_business_hangs_up_while_owner_rings(self):
        line = Line()
        try:
            req, created, relay, owner = self._to_ringing(line)
            line.status(req["call_id"], created["sid"], "completed")
            line.wait_state(req["call_id"], "failed")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "business_hangup")
            self.assertIn(owner["sid"], line.adapter.ended)  # the ringing owner leg is cancelled
        finally:
            line.close()

    def test_take_over_while_on_hold_rings_owner_from_the_pane(self):
        line = Line()
        try:
            req, created = line.dial(mode="agent")
            relay = line.connect(req, created)
            relay.prompt("Please hold, your call is important to us.")
            line.wait_state(req["call_id"], "on_hold")
            out = line.svc.submit("take_over", source="desktop", call_id=req["call_id"])
            self.assertTrue(out["result"]["ok"], out)
            line.wait_state(req["call_id"], "ringing_owner")
            owner = line.adapter.created[-1]
            line.status(req["call_id"], owner["sid"], "no-answer", owner=True)
            line.wait_state(req["call_id"], "on_hold")  # owner missed it: keep holding
        finally:
            line.close()


class EndingTests(unittest.TestCase):
    def test_voicemail_hangs_up(self):
        line = Line()
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("You've reached Sample Bistro. Please leave a message after the tone.")
            line.wait_state(req["call_id"], "ended")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "voicemail")
            self.assertIn(created["sid"], line.adapter.ended)
            line.wait_task(req["task_id"], "failed")
        finally:
            line.close()

    def test_voicemail_message_when_allowed(self):
        line = Line(reply=finish_on("[voicemail greeting]", "callback_expected", "Left a message."))
        try:
            req, created = line.dial(leave_voicemail=True)
            relay = line.connect(req, created)
            relay.prompt("You've reached Sample Bistro. Please leave a message after the tone.")
            line.wait_state(req["call_id"], "ended")
            line.wait_task(req["task_id"], "awaiting_callback")
        finally:
            line.close()

    def test_no_answer_and_busy(self):
        for status, reason in (("no-answer", "no_answer"), ("busy", "busy"), ("failed", "provider_error")):
            line = Line()
            try:
                req, created = line.dial()
                line.status(req["call_id"], created["sid"], status)
                line.wait_state(req["call_id"], "failed")
                self.assertEqual(line.store.get_call(req["call_id"]).end_reason, reason)
                line.wait_task(req["task_id"], "failed")
            finally:
                line.close()

    def test_business_hangs_up_during_hold_and_during_talk(self):
        line = Line()
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            line.status(req["call_id"], created["sid"], "in-progress")
            relay.prompt("Please hold, all of our agents are busy.")
            line.wait_state(req["call_id"], "on_hold")
            line.status(req["call_id"], created["sid"], "completed")
            line.wait_state(req["call_id"], "failed")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "business_hangup")
        finally:
            line.close()
        line = Line()
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            reach_human(line, relay)
            line.wait_state(req["call_id"], "agent_talking")
            line.status(req["call_id"], created["sid"], "completed")
            line.wait_state(req["call_id"], "ended")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "business_hangup")
        finally:
            line.close()

    def test_dropped_relay_hangs_up_after_grace(self):
        line = Line()
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Please hold.")
            line.wait_state(req["call_id"], "on_hold")
            relay.close()
            line.wait_state(req["call_id"], "failed", timeout=5)
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "dropped")
            self.assertIn(created["sid"], line.adapter.ended)
        finally:
            line.close()

    def test_relay_reconnects_then_gives_up(self):
        line = Line(relay_reconnect_grace_s=5.0, relay_max_reconnects=1)
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Please hold.")
            line.wait_state(req["call_id"], "on_hold")
            relay.close()
            status, again = line.post(f"/twilio/voice/connect/action/{req['call_id']}?g=0", {"CallSid": created["sid"]})
            self.assertIn("<ConversationRelay", again)
            self.assertNotIn("welcomeGreeting", again)  # no second greeting on reconnect
            second = line.relay(again)
            second.send({"type": "setup", "callSid": created["sid"]})
            second.prompt("Please hold.")
            time.sleep(0.2)
            self.assertEqual(line.state(req["call_id"]), "on_hold")
            second.close()
            status, final = line.post(f"/twilio/voice/connect/action/{req['call_id']}?g=1", {"CallSid": created["sid"]})
            self.assertIn("<Hangup/>", final)
            line.wait_state(req["call_id"], "failed")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "dropped")
        finally:
            line.close()

    def test_max_duration_watchdog(self):
        line = Line()
        try:
            line.svc.cfg.max_call_minutes = 0.01  # 0.6 s
            req, created = line.dial()
            line.connect(req, created)
            line.wait_state(req["call_id"], "failed", timeout=5)
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "max_duration")
            self.assertIn(created["sid"], line.adapter.ended)
        finally:
            line.close()

    def test_owner_hang_up_and_cancel(self):
        line = Line()
        try:
            waiting = line.request()
            out = line.svc.submit("hang_up", source="desktop", call_id=waiting["call_id"])
            self.assertTrue(out["result"]["ok"])
            self.assertEqual(line.state(waiting["call_id"]), "failed")
            self.assertEqual(line.store.get_task(waiting["task_id"]).status.value, "canceled")
            req, created = line.dial()
            line.connect(req, created)
            out = line.svc.submit("hang_up", source="tool", call_id=req["call_id"])
            self.assertTrue(out["result"]["ok"])
            line.wait_state(req["call_id"], "ended")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "hung_up_by_owner")
            self.assertIn(created["sid"], line.adapter.ended)
            self.assertEqual(line.adapter.created.__len__(), 1)
        finally:
            line.close()

    def test_provider_refuses_dial(self):
        line = Line()
        try:
            line.adapter.fail_create = True
            req = line.request()
            out = line.approve(req["task_id"])
            self.assertFalse(out["result"]["ok"])
            line.wait_state(req["call_id"], "failed")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "provider_error")
        finally:
            line.close()

    def test_agent_unavailable_apologises_and_ends(self):
        def broken(d, text):
            raise RuntimeError("model route down")
        line = Line(reply=broken)
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            reach_human(line, relay)
            relay.wait_text("trouble on my end")
            line.wait_state(req["call_id"], "failed")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "provider_error")
        finally:
            line.close()


class ConversationTests(unittest.TestCase):
    def test_barge_in_cuts_stale_chunks(self):
        def reply(d, text):
            return ["One. ", "Two. ", "Three. ", "Four. ", "Five."]
        line = Line(reply=reply, chunk_delay=0.15)
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            reach_human(line, relay)
            relay.wait_text("One.")
            relay.send({"type": "interrupt", "utteranceUntilInterrupt": "One.", "durationUntilInterruptMs": 400})
            wait_for(lambda: "One." in line.drivers[0].interrupts, what="interrupt reached the driver")
            time.sleep(0.4)
            self.assertNotIn("Four.", relay.spoken())
            relay.prompt("Sorry, what time did you say?")
            wait_for(lambda: len(line.drivers[0].turns) >= 2)
        finally:
            line.close()

    def test_new_prompt_while_generating_interrupts(self):
        line = Line(reply=lambda d, t: ["a ", "b ", "c ", "d ", "e "], chunk_delay=0.2)
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            reach_human(line, relay)
            relay.wait_text("a ")
            relay.prompt("Hold on, one second.")
            wait_for(lambda: len(line.drivers[0].turns) >= 2, timeout=5)
            self.assertTrue(line.drivers[0].interrupts)
        finally:
            line.close()

    def test_say_this_from_the_owner(self):
        line = Line()
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            reach_human(line, relay)
            line.wait_state(req["call_id"], "agent_talking")
            out = line.svc.submit("say", source="desktop", call_id=req["call_id"],
                                  payload={"text": "Please use the name Sam."})
            self.assertTrue(out["result"]["ok"])
            relay.wait_text("Please use the name Sam.")
            wait_for(lambda: any("Please use the name Sam" in n for n in line.drivers[0].notes))
        finally:
            line.close()

    def test_bounded_inbox(self):
        line = Line(queue_max=2)
        try:
            req, created = line.dial()
            session = line.session(req["call_id"])
            results = line.svc.run(_flood(session, 10))
            self.assertIn(False, results)
            wait_for(lambda: any(e["kind"] == "ingress_overflow" for e in line.store.events(req["call_id"])))
        finally:
            line.close()


async def _flood(session, n):
    return [session.offer({"type": "prompt", "voicePrompt": f"hold {i}", "last": True}) for i in range(n)]


class CallbackTests(unittest.TestCase):
    def _missed(self, line, mode):
        req, created = line.dial(mode=mode)
        line.status(req["call_id"], created["sid"], "no-answer")
        line.wait_state(req["call_id"], "failed")
        return req

    def test_callback_matched_to_task_rings_owner(self):
        line = Line()
        try:
            req = self._missed(line, "owner")
            sid = "CA" + "c" * 32
            status, body = line.post("/twilio/voice/inbound", {"CallSid": sid, "From": BUSINESS, "To": LINE})
            self.assertEqual(status, 200)
            self.assertIn("Are you calling back about Book a table for 4 at 7?", body)
            inbound = wait_for(lambda: [c for c in line.store.list_calls() if c.direction.value == "inbound"])[0]
            self.assertEqual(inbound.task_id, req["task_id"])
            relay = line.relay(body)
            relay.send({"type": "setup", "callSid": sid})
            line.wait_state(inbound.id, "ringing_owner")
            self.assertEqual(line.adapter.created[-1]["to"], OWNER)
        finally:
            line.close()

    def test_unmatched_callback_takes_a_message(self):
        line = Line()
        try:
            sid = "CA" + "d" * 32
            status, body = line.post("/twilio/voice/inbound", {"CallSid": sid, "From": "+17755550177", "To": LINE})
            self.assertIn("How can I help?", body)
            inbound = wait_for(lambda: line.store.list_calls())[0]
            self.assertIsNone(inbound.task_id)
            relay = line.relay(body)
            relay.send({"type": "setup", "callSid": sid})
            relay.prompt("Hi, this is the pharmacy, the prescription is ready.")
            relay.wait_text("book a table")  # the scripted agent answers; nobody is rung
            self.assertEqual(line.adapter.created, [])
            self.assertIn("take a short message", line.drivers[0].briefing)
        finally:
            line.close()

    def test_callback_ambiguous_match_is_no_match(self):
        line = Line()
        try:
            self._missed(line, "agent")
            self._missed(line, "agent")
            status, body = line.post("/twilio/voice/inbound", {"CallSid": "CA" + "e" * 32, "From": BUSINESS,
                                                               "To": LINE})
            self.assertIn("How can I help?", body)
        finally:
            line.close()

    def test_callback_rejected_when_busy_or_wrong_number(self):
        line = Line()
        try:
            status, body = line.post("/twilio/voice/inbound", {"CallSid": "CA" + "f" * 32, "From": BUSINESS,
                                                               "To": "+17755550111"})
            self.assertIn("<Reject", body)
            req, created = line.dial()
            status, body = line.post("/twilio/voice/inbound", {"CallSid": "CA" + "a" * 32, "From": BUSINESS,
                                                               "To": LINE})
            self.assertIn('<Reject reason="busy"/>', body)
        finally:
            line.close()


if __name__ == "__main__":
    unittest.main()
