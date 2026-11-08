"""Round 7: callbacks over the owner's forwarded line (Fi -> the plugin's Twilio line)."""
import unittest

from harness import ACCOUNT, BUSINESS, LINE, OWNER, Line, wait_for

FI = "+17755550142"  # sample forwarding line (the owner's Fi number in the trial setup)
NO_TOKEN = {"HERMES_LINE_TWILIO_ACCOUNT_SID": ACCOUNT}
MODES = (("signature", None), ("path_secret", NO_TOKEN))


def agent(driver, text):
    """Scripted Hermes for callbacks: rings the owner when asked, otherwise takes a message."""
    if "speak to Sam" in text:
        driver.tool("request_owner")
        return ["Of course, ", "let me try to reach Sam."]
    if "could not be reached" in text:
        return ["Sam isn't available. ", "Can I take a message?"]
    if "call me back" in text:
        driver.tool("request_finish", "done", "Dana from Sample Bistro: please call back about Friday.")
        return ["Thanks, ", "I'll pass that on. Goodbye."]
    return ["Thanks for calling back."]


class ForwardedCallbackTests(unittest.TestCase):
    def _line(self, secrets, **kw):
        line = Line(secrets=secrets, reply=agent, forwarding_number=FI, inbound_screening_hold_s=0.0, **kw)
        self.addCleanup(line.close)
        return line

    def _inbound(self, line, sign, sid, caller=BUSINESS, **extra):
        if not sign:
            line.adapter.add_inbound(sid, caller, LINE)
        params = {"CallSid": sid, "From": caller, "To": LINE, **extra}
        return line.post("/twilio/voice/inbound", params, sign=sign)

    def _missed(self, line, mode="agent", sign=True):
        req, created = line.dial(mode=mode)
        if not sign:
            line.adapter.records[created["sid"]]["status"] = "no-answer"
        line.post(f"/twilio/voice/status/{req['call_id']}", {"CallSid": created["sid"], "CallStatus": "no-answer"},
                  sign=sign)
        line.wait_state(req["call_id"], "failed")
        return req

    def _inbound_call(self, line):
        return wait_for(lambda: [c for c in line.store.list_calls() if c.direction.value == "inbound"])[0]

    def test_R7_forwarded_callback_is_matched_to_its_task(self):
        for mode, secrets in MODES:
            with self.subTest(mode=mode):
                line = self._line(secrets)
                sign = secrets is None
                req = self._missed(line, sign=sign)
                sid = "CA" + "1" * 31 + ("a" if sign else "b")
                status, body = self._inbound(line, sign, sid, ForwardedFrom=FI, CallerName="SAMPLE BISTRO<script>")
                self.assertEqual(status, 200)
                self.assertIn("Are you calling back about Book a table for 4 at 7?", body)
                call = self._inbound_call(line)
                self.assertEqual(call.task_id, req["task_id"])
                ev = [e["data"] for e in line.store.events(call.id) if e["kind"] == "inbound"][0]
                self.assertTrue(ev["matched"] and ev["forwarded"] and ev["via_forwarding_line"])
                self.assertEqual(ev["caller_name"], "SAMPLE BISTROscript")  # sanitized, untrusted
                self.assertNotIn(FI, str(ev))  # masked
                relay = line.relay(body, sign=sign)
                relay.send({"type": "setup", "callSid": sid})
                relay.prompt("Hi, this is Dana from Sample Bistro returning your call.")
                relay.wait_text("Thanks for calling back.")
                self.assertIn("Caller ID name (from the phone network, unverified): SAMPLE BISTROscript",
                              line.drivers[-1].briefing)
                # H2 stays: a replay of the admitted SID after the call is refused
                line.svc.submit("hang_up", source="slash", call_id=call.id)
                line.wait_state(call.id, "ended")
                wait_for(lambda: call.id not in line.svc.sessions)
                self.assertIn("<Reject", self._inbound(line, sign, sid, ForwardedFrom=FI)[1])

    def test_R7_forwarding_that_hides_the_caller_matches_nothing(self):
        line = self._line(None)
        self._missed(line)
        status, body = self._inbound(line, True, "CA" + "2" * 32, caller=FI)
        self.assertIn("How can I help?", body)
        call = self._inbound_call(line)
        self.assertIsNone(call.task_id)
        ev = [e["data"] for e in line.store.events(call.id) if e["kind"] == "inbound"][0]
        self.assertTrue(ev["caller_hidden"])

    def test_R7_unknown_caller_gets_disclosure_and_can_reach_the_owner_who_presses_1(self):
        for mode, secrets in MODES:
            with self.subTest(mode=mode):
                line = self._line(secrets)
                sign = secrets is None
                sid = "CA" + "3" * 31 + ("a" if sign else "b")
                status, body = self._inbound(line, sign, sid, caller="+17755550177", ForwardedFrom=FI)
                self.assertIn("This is Hermes, an AI assistant.", body)  # disclosure in the greeting
                self.assertIn("How can I help?", body)
                call = self._inbound_call(line)
                self.assertIsNone(call.task_id)
                relay = line.relay(body, sign=sign)
                relay.send({"type": "setup", "callSid": sid})
                relay.prompt("Hi, can I speak to Sam please?")
                line.wait_state(call.id, "ringing_owner")
                owner = line.adapter.created[-1]
                self.assertEqual(owner["to"], OWNER)
                self.assertIn("presses 1 to join", line.drivers[-1].briefing)
                line.post(f"/twilio/voice/owner/answer/{call.id}", {"CallSid": owner["sid"]}, sign=sign)
                status, joined = line.post(f"/twilio/voice/owner/accept/{call.id}",
                                           {"CallSid": owner["sid"], "Digits": "1"}, sign=sign)
                self.assertIn("<Conference", joined)
                line.wait_state(call.id, "owner_connected")
                self.assertEqual([c["to"] for c in line.adapter.created], [OWNER])  # never anyone else

    def test_R7_owner_declines_and_hermes_takes_a_message(self):
        line = self._line(None)
        sid = "CA" + "4" * 32
        status, body = self._inbound(line, True, sid, caller="+17755550177", ForwardedFrom=FI)
        call = self._inbound_call(line)
        relay = line.relay(body)
        relay.send({"type": "setup", "callSid": sid})
        relay.prompt("Hi, can I speak to Sam please?")
        line.wait_state(call.id, "ringing_owner")
        owner = line.adapter.created[-1]
        line.post(f"/twilio/voice/owner/answer/{call.id}", {"CallSid": owner["sid"]})
        status, out = line.post(f"/twilio/voice/owner/accept/{call.id}", {"CallSid": owner["sid"], "Digits": "2"})
        self.assertNotIn("<Conference", out)
        line.wait_state(call.id, "agent_talking")
        relay.wait_text("Can I take a message?")
        self.assertIn("owner_declined", [e["kind"] for e in line.store.events(call.id)])
        self.assertIn(owner["sid"], line.adapter.ended)
        relay.prompt("Sure, it's Dana, please call me back about Friday.")
        line.wait_state(call.id, "ended", timeout=10)
        wait_for(lambda: line.delivered, timeout=10, what="owner summary")
        # Round 37: the summary quotes what the caller said, never the model's own note of it
        self.assertIn('They: "Sure, it\'s Dana, please call me back about Friday."', line.delivered[-1][1])
        # a second ask for the owner on the same call cannot ring again (one grant per call)
        self.assertEqual(len([c for c in line.adapter.created if c["to"] == OWNER]), 1)

    def test_R7_owner_who_does_not_answer_is_like_a_decline(self):
        line = self._line(None)
        sid = "CA" + "5" * 32
        status, body = self._inbound(line, True, sid, caller="+17755550177")
        call = self._inbound_call(line)
        relay = line.relay(body)
        relay.send({"type": "setup", "callSid": sid})
        relay.prompt("Hi, can I speak to Sam please?")
        line.wait_state(call.id, "ringing_owner")
        owner = line.adapter.created[-1]
        line.status(call.id, owner["sid"], "no-answer", owner=True)
        line.wait_state(call.id, "agent_talking")
        relay.wait_text("Can I take a message?")

    def test_R7_caller_hangs_up_early(self):
        for mode, secrets in MODES:
            with self.subTest(mode=mode):
                line = self._line(secrets)
                sign = secrets is None
                sid = "CA" + "6" * 31 + ("a" if sign else "b")
                status, body = self._inbound(line, sign, sid, caller="+17755550177", ForwardedFrom=FI)
                call = self._inbound_call(line)
                if not sign:
                    line.adapter.records[sid]["status"] = "completed"
                status = line.post(f"/twilio/voice/status/{call.id}", {"CallSid": sid, "CallStatus": "completed"},
                                   sign=sign)[0]
                self.assertEqual(status, 204)
                wait_for(lambda: line.store.get_call(call.id).state.terminal, what="ended")
                self.assertEqual(line.store.get_call(call.id).end_reason, "business_hangup")
                self.assertEqual(line.adapter.created, [])  # nobody rung
                wait_for(lambda: call.id not in line.svc.sessions)
                self.assertIn("<Reject", self._inbound(line, sign, sid)[1])  # H2: no second admission

    def test_R7_unknown_caller_cannot_ring_when_disabled(self):
        line = self._line(None, inbound_ring_owner=False)
        sid = "CA" + "7" * 32
        status, body = self._inbound(line, True, sid, caller="+17755550177")
        call = self._inbound_call(line)
        relay = line.relay(body)
        relay.send({"type": "setup", "callSid": sid})
        relay.prompt("Hi, can I speak to Sam please?")
        relay.wait_text("let me try to reach Sam.")
        wait_for(lambda: any(e["kind"] == "owner_ring_rejected" for e in line.store.events(call.id)),
                 what="ring refused")
        self.assertEqual(line.adapter.created, [])
        self.assertNotIn("presses 1", line.drivers[-1].briefing)


if __name__ == "__main__":
    unittest.main()
