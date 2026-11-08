"""Every call path passes check_destination and owner consent before the adapter.

Each rejected case asserts ZERO adapter calls (create, end and redirect).
"""
import unittest
from datetime import datetime, timezone

from harness import BUSINESS, OTHER, OWNER, Line, wait_for
from plugin.line.engine.contracts import CallState, GateRejected, Role
from plugin.line.engine.telephony import DialPermit
from plugin.line.policy import Settings

BAD_DESTINATIONS = [
    "911", "+1911", "1-911", "112", "+44999", "999", "000", "106", "933",   # emergency
    "12345", "#123#", "*67", "611",                                           # short / service codes
    "+17755550172",                                                           # Bora: hard deny
    "+447700900123", "+33142685300", "+18765550101",                          # non-US (incl. NANP Jamaica)
    OTHER,                                                                    # valid US, not allowlisted
    "(775) 555-0101", "775-555-0101", "17755550101", " +17755550101",         # non-canonical forms
    "tel:+17755550101", "sip:+17755550101@example.com", "+17755550101,1234",  # URI / post-dial
    "+17755550101;ext=1", "+1775555010１", "", None,
]


class RequestGateTests(unittest.TestCase):
    def setUp(self):
        self.line = Line()

    def tearDown(self):
        self.line.close()

    def test_every_bad_destination_is_refused_before_approval(self):
        for raw in BAD_DESTINATIONS:
            with self.subTest(raw=raw):
                with self.assertRaises(GateRejected) as ctx:
                    self.line.svc.request_call(destination=raw, title="t", goal="g", mode="agent")
                self.assertEqual(ctx.exception.gate, "destination")
        self.assertEqual(self.line.store.list_tasks(open_only=False), [])
        self.assertEqual(self.line.adapter.total_calls, 0)

    def test_approval_from_a_tool_or_the_model_is_refused(self):
        req = self.line.request()
        for source in ("tool", "phone", "model", "desktop-forged"):
            out = self.line.approve(req["task_id"], source=source)
            self.assertFalse(out["result"]["ok"], source)
        self.assertEqual(self.line.state(req["call_id"]), "awaiting_approval")
        self.assertEqual(self.line.adapter.total_calls, 0)

    def test_destination_rechecked_at_dial_time(self):
        req = self.line.request()
        self.line.svc.gate.settings = Settings.load({"allowed_destinations": [OWNER], "quiet_hours_enabled": False})
        out = self.line.approve(req["task_id"])
        self.assertFalse(out["result"]["ok"])
        self.assertIn("destination", out["result"]["error"])
        self.assertEqual(self.line.state(req["call_id"]), "failed")
        self.assertEqual(self.line.store.get_call(req["call_id"]).end_reason, "rejected")
        self.assertEqual(self.line.adapter.total_calls, 0)

    def test_one_call_per_line(self):
        first, _ = self.line.dial()
        second = self.line.request()
        out = self.line.approve(second["task_id"])
        self.assertFalse(out["result"]["ok"])
        self.assertIn("busy", out["result"]["error"])
        self.assertEqual(len(self.line.adapter.created), 1)
        self.assertEqual(self.line.state(second["call_id"]), "awaiting_approval")

    def test_double_approval_dials_once(self):
        req = self.line.request()
        self.line.approve(req["task_id"])
        again = self.line.approve(req["task_id"])
        self.assertFalse(again["result"]["ok"])
        self.assertEqual(len(self.line.adapter.created), 1)


class QuietHoursTests(unittest.TestCase):
    def setUp(self):
        self.now = [datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)]  # 12:00 in Los Angeles
        self.line = Line(policy={"quiet_hours_enabled": True}, now_utc=lambda: self.now[0])

    def tearDown(self):
        self.line.close()

    def test_request_refused_in_quiet_hours(self):
        self.now[0] = datetime(2026, 11, 6, 7, 0, tzinfo=timezone.utc)  # 23:00 in Los Angeles
        with self.assertRaises(GateRejected) as ctx:
            self.line.request()
        self.assertEqual(ctx.exception.gate, "quiet_hours")
        self.assertEqual(self.line.adapter.total_calls, 0)

    def test_approval_that_lands_in_quiet_hours_does_not_dial(self):
        req = self.line.request()
        self.now[0] = datetime(2026, 11, 6, 7, 0, tzinfo=timezone.utc)
        out = self.line.approve(req["task_id"])
        self.assertFalse(out["result"]["ok"])
        self.assertIn("quiet_hours", out["result"]["error"])
        self.assertEqual(self.line.adapter.total_calls, 0)


class PermitTests(unittest.TestCase):
    def setUp(self):
        self.line = Line(start=False)

    def tearDown(self):
        self.line.close()

    def _token(self, call_id="c1", e164=BUSINESS, role=Role.BUSINESS, action="dial"):
        gate = self.line.svc.gate
        return gate.consent.issue(action, "t1", gate.binding(e164=e164, role=role, call_id=call_id,
                                                              line_key="default"), 60)

    def _authorize(self, token, raw=BUSINESS, call_id="c1", role=Role.BUSINESS, action="dial"):
        return self.line.svc.gate.authorize(raw, role=role, action=action, task_id="t1", call_id=call_id,
                                            line_key="default", token=token)

    def test_forged_permit_and_non_permit_are_refused(self):
        with self.assertRaises(GateRejected):
            DialPermit(BUSINESS, Role.BUSINESS, "t1", "c1", "default")
        for fake in (None, BUSINESS, {"e164": BUSINESS}, object()):
            with self.assertRaises(GateRejected):
                self.line.adapter.place_call(fake, from_number="+1", answer_url="u", status_url="s",
                                             timeout_s=1, time_limit_s=1)
        self.assertEqual(self.line.adapter.total_calls, 0)

    def test_permit_is_single_use(self):
        permit = self._authorize(self._token())
        kw = dict(from_number="+17755550100", answer_url="u", status_url="s", timeout_s=1, time_limit_s=1)
        self.line.adapter.place_call(permit, **kw)
        with self.assertRaises(GateRejected):
            self.line.adapter.place_call(permit, **kw)
        self.assertEqual(len(self.line.adapter.created), 1)

    def test_consent_is_single_use_and_bound(self):
        token = self._token()
        self._authorize(token)
        with self.assertRaises(GateRejected):
            self._authorize(token)  # reuse
        with self.assertRaises(GateRejected):
            self._authorize(self._token(call_id="c1"), call_id="c2")  # other call
        with self.assertRaises(GateRejected):
            self._authorize(self._token(), raw=OWNER)  # other destination
        with self.assertRaises(GateRejected):
            self._authorize(self._token(action="bridge_owner"))  # other action
        with self.assertRaises(GateRejected):
            self._authorize(None)
        self.assertEqual(self.line.adapter.total_calls, 0)

    def test_hard_denies_hold_even_with_a_valid_token(self):
        for raw in ("911", "+17755550172", "+447700900123"):
            token = self._token(e164=raw)
            with self.assertRaises(GateRejected):
                self._authorize(token, raw=raw)
        self.assertEqual(self.line.adapter.total_calls, 0)


class OwnerLegGateTests(unittest.TestCase):
    def test_owner_number_off_the_allowlist_is_never_rung(self):
        line = Line(policy={"allowed_destinations": [BUSINESS]})
        try:
            req, created = line.dial(mode="owner")
            relay = line.connect(req, created)
            relay.prompt("Hello, this is Mike at Sample Bistro.")
            relay.wait_text("live person?")
            relay.prompt("Yes, I am a real person")
            line.wait_state(req["call_id"], "agent_talking")  # allow_agent_fallback defaults to True
            self.assertEqual([c["to"] for c in line.adapter.created], [BUSINESS])
            kinds = [e["kind"] for e in line.store.events(req["call_id"])]
            self.assertIn("owner_ring_rejected", kinds)
        finally:
            line.close()

    def test_owner_unreachable_without_fallback_ends_politely(self):
        line = Line(policy={"allowed_destinations": [BUSINESS]})
        try:
            req, created = line.dial(mode="owner", allow_agent_fallback=False)
            relay = line.connect(req, created)
            relay.prompt("Hello, this is Mike at Sample Bistro.")
            relay.wait_text("live person?")
            relay.prompt("Yes, I am a real person")
            relay.wait_text("isn't available")
            line.wait_state(req["call_id"], "ended")
            self.assertEqual(line.store.get_call(req["call_id"]).end_reason, "owner_unavailable")
            self.assertEqual([c["to"] for c in line.adapter.created], [BUSINESS])
            self.assertIn(created["sid"], line.adapter.ended)
        finally:
            line.close()

    def test_tool_take_over_cannot_reuse_a_spent_bridge_grant(self):
        line = Line()
        try:
            req, created = line.dial(mode="agent")
            relay = line.connect(req, created)
            relay.prompt("Hello, this is Mike.")
            relay.wait_text("live person?")
            relay.prompt("Yes, I am a real person")
            line.wait_state(req["call_id"], "agent_talking")
            first = line.svc.submit("take_over", source="tool", call_id=req["call_id"])
            self.assertTrue(first["result"]["ok"], first)
            owner_sid = line.adapter.created[-1]["sid"]
            line.status(req["call_id"], owner_sid, "no-answer", owner=True)
            line.wait_state(req["call_id"], "agent_talking")
            second = line.svc.submit("take_over", source="tool", call_id=req["call_id"])
            self.assertFalse(second["result"]["ok"])
            self.assertEqual([c["to"] for c in line.adapter.created], [BUSINESS, OWNER])
            third = line.svc.submit("take_over", source="desktop", call_id=req["call_id"])
            self.assertTrue(third["result"]["ok"], third)  # a fresh owner action is fresh consent
            self.assertEqual([c["to"] for c in line.adapter.created], [BUSINESS, OWNER, OWNER])
        finally:
            line.close()

    def test_callback_in_quiet_hours_does_not_ring_owner(self):
        now = [datetime(2026, 11, 5, 20, 0, tzinfo=timezone.utc)]
        line = Line(policy={"quiet_hours_enabled": True}, now_utc=lambda: now[0])
        try:
            req, created = line.dial(mode="owner")
            line.status(req["call_id"], created["sid"], "no-answer")
            line.wait_state(req["call_id"], "failed")
            now[0] = datetime(2026, 11, 6, 7, 0, tzinfo=timezone.utc)
            status, body = line.post("/twilio/voice/inbound", {"CallSid": "CA" + "b" * 32, "From": BUSINESS,
                                                               "To": line.cfg.line_number})
            self.assertEqual(status, 200)
            call = wait_for(lambda: [c for c in line.store.list_calls() if c.direction.value == "inbound"])[0]
            relay = line.relay(body)
            relay.send({"type": "setup", "callSid": "CA" + "b" * 32})
            wait_for(lambda: any(e["kind"] == "owner_ring_skipped" for e in line.store.events(call.id)))
            self.assertEqual(len(line.adapter.created), 1)
        finally:
            line.close()


if __name__ == "__main__":
    unittest.main()
