"""Round 12: findings from live calls 2 (outbound) and 3 (inbound via Fi always-forward)."""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from harness import BUSINESS, LINE, OWNER, Line, wait_for

FI = "+17755550142"
FAKE = str(Path(__file__).with_name("fake_acp_agent.py"))


def inbound(line, sid, caller, **extra):
    status, body = line.post("/twilio/voice/inbound", {"CallSid": sid, "From": caller, "To": LINE, **extra})
    call = wait_for(lambda: [c for c in line.store.list_calls() if c.direction.value == "inbound"
                             and c.provider_sid == sid])[0]
    ev = [e["data"] for e in line.store.events(call.id) if e["kind"] == "inbound"][0]
    return body, call, ev


class OwnerCallerTests(unittest.TestCase):
    def test_R12_owner_number_is_the_owner_not_a_hidden_caller(self):
        line = Line(owner_name="Can", forwarding_number=FI, inbound_screening_hold_s=0.0)
        try:
            body, call, ev = inbound(line, "CA" + "1" * 32, OWNER)  # live call 3: From = the owner's own phone
            self.assertIn("Hi Can, this is Hermes on your line", body)
            self.assertTrue(ev["owner_number"])
            self.assertFalse(ev["caller_hidden"])
            self.assertEqual((ev["forwarded"], ev["forwarding"]), (False, "unknown"))  # Fi sent no ForwardedFrom
            session = line.session(call.id)
            self.assertTrue(session.owner_caller)
            self.assertIn("Caller ID can be faked: it approves nothing", session.inbound_context)
            relay = line.relay(body)
            relay.send({"type": "setup", "callSid": "CA" + "1" * 32})
            relay.prompt("I am Ken.")
            wait_for(lambda: line.drivers and line.drivers[-1].turns, what="turn")
            self.assertIn("most likely Can", line.drivers[-1].briefing)
            self.assertEqual(line.adapter.created, [])
        finally:
            line.close()

    def test_R12_forwarding_recorded_when_the_request_shows_it(self):
        line = Line(owner_name="Can", forwarding_number=FI, inbound_screening_hold_s=0.0)
        try:
            for i, extra in enumerate(({"ForwardedFrom": FI}, {"CalledVia": FI})):
                body, call, ev = inbound(line, "CA" + str(i + 2) * 32, "+17755550177", **extra)
                self.assertEqual((ev["forwarded"], ev["forwarding"], ev["via_forwarding_line"]), (True, "detected", True))
                line.svc.submit("hang_up", source="slash", call_id=call.id)
                wait_for(lambda: call.id not in line.svc.sessions)
        finally:
            line.close()
        plain = Line(owner_name="Can")
        try:
            body, call, ev = inbound(plain, "CA" + "9" * 32, "+17755550177")
            self.assertEqual(ev["forwarding"], "not_configured")
        finally:
            plain.close()

    def test_R12_hidden_caller_still_detected_for_our_own_lines(self):
        line = Line(owner_name="Can", forwarding_number=FI, inbound_screening_hold_s=0.0)
        try:
            body, call, ev = inbound(line, "CA" + "4" * 32, FI)
            self.assertTrue(ev["caller_hidden"])
            self.assertFalse(ev["owner_number"])
        finally:
            line.close()


class HintsInboundTests(unittest.TestCase):
    def test_R12_inbound_relay_hints_carry_owner_phrases_and_open_task_businesses(self):
        line = Line(owner_name="Can")
        try:
            line.request(title="Table at Sample Bistro")
            body, call, ev = inbound(line, "CA" + "5" * 32, "+17755550177")
            hints = re.search(r'hints="([^"]*)"', body).group(1).split(",")
            for want in ("Can", "this is Can", "I am Can", "Sample", "Bistro"):
                self.assertIn(want, hints)
        finally:
            line.close()


class CopyTests(unittest.TestCase):
    def test_R12_disclose_once_and_name_misspelling_note_in_every_briefing(self):
        line = Line(owner_name="Can")
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Hi, how are you?")
            relay.wait_text("live person?")
            relay.prompt("Yes, I am a real person")
            line.wait_state(req["call_id"], "agent_talking")
            briefing = line.drivers[0].briefing
            self.assertIn("Do not introduce yourself again or repeat that you are an AI assistant unless you are asked",
                          briefing)
            self.assertIn("Speech recognition may misspell names", briefing)
        finally:
            line.close()


class DirectToolNamesTests(unittest.TestCase):
    def test_R12_briefing_names_the_exact_deferred_tools_after_the_scope_check(self):
        fd, log = tempfile.mkstemp(prefix="acp-r12-", suffix=".jsonl")
        os.close(fd)
        from plugin.line.engine.acp_driver import AcpDriver
        line = Line(driver_factory=lambda sid: AcpDriver(sid, command=[sys.executable, "-B", FAKE, log, "0",
                                                                       "restricted"],
                                                         cwd=tempfile.gettempdir(), start_timeout_s=10))
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Hello?")
            relay.wait_text("live person?", timeout=10)
            relay.prompt("Yes, I am a real person")
            line.wait_state(req["call_id"], "agent_talking")
            def primary():
                return [json.loads(l)["text"] for l in open(log)
                        if "text" in json.loads(l) and json.loads(l)["session"] == "sess-1"]
            wait_for(lambda: primary(), what="first prompt of the call session")
            first = primary()[0]
            name = line.session(req["call_id"]).mcp_server_name
            self.assertIn(f"mcp__editor_{name}_0123abcd__hermes_line_call_finish", first)
            self.assertIn("no tool_search or tool_describe needed", first)
        finally:
            line.close()
            os.unlink(log)

    def test_R12_no_empty_interruption_note(self):
        from plugin.line.engine.acp_driver import AcpDriver
        d = AcpDriver("c", command=["x"])
        d._active = {"session": "s", "gen": 1, "emit": None, "chunks": [], "first": None, "t0": 0}
        d._gen = 1
        d.interrupt(None)
        self.assertEqual(d._prefix, [])
        d._active = {"session": "s", "gen": 2, "emit": None, "chunks": ["Sure, "], "first": None, "t0": 0}
        d._gen = 2
        d.interrupt(None)
        self.assertEqual(len(d._prefix), 1)
        self.assertIn('heard only: "Sure,"', d._prefix[0])


if __name__ == "__main__":
    unittest.main()
