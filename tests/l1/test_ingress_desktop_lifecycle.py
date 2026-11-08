"""Ingress authentication, Desktop REST contract, service lifecycle and plugin registration."""
import json
import re
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from websockets.exceptions import InvalidStatus

from harness import ACCOUNT, AUTH_TOKEN, BUSINESS, LINE, OWNER, PUBLIC, Line, Relay, wait_for
from plugin.line.engine.contracts import CallState, Direction, Mode
from plugin.line.engine.lease import LeaseHeld
from plugin.line.server import twilio_signature
from plugin.line.server.desktop_api import DesktopApi

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00$")


class IngressSecurityTests(unittest.TestCase):
    def setUp(self):
        self.line = Line()
        self.req, self.created = self.line.dial()
        self.path = f"/twilio/voice/status/{self.req['call_id']}"

    def tearDown(self):
        self.line.close()

    def test_signature_required(self):
        ok = {"CallSid": self.created["sid"], "CallStatus": "completed"}
        self.assertEqual(self.line.post(self.path, ok, sign=False)[0], 403)
        self.assertEqual(self.line.post(self.path, ok, token="wrong-token")[0], 403)
        self.assertEqual(self.line.post(self.path, ok, signature="AAAA")[0], 403)
        self.assertEqual(self.line.post(self.path, ok, account="AC" + "1" * 32)[0], 403)
        self.assertEqual(self.line.post(self.path, ok, account=None)[0], 403)
        self.assertEqual(self.line.state(self.req["call_id"]), "dialing")

    def test_signature_bound_to_public_url_not_host_header(self):
        params = {"CallSid": self.created["sid"], "CallStatus": "completed", "AccountSid": ACCOUNT}
        sig = twilio_signature.compute(AUTH_TOKEN, "http://127.0.0.1" + self.path, list(params.items()))
        self.assertEqual(self.line.post(self.path, params, signature=sig)[0], 403)

    def test_tampered_body_refused(self):
        params = {"CallSid": self.created["sid"], "CallStatus": "in-progress", "AccountSid": ACCOUNT}
        sig = twilio_signature.compute(AUTH_TOKEN, PUBLIC + self.path, list(params.items()))
        params["CallStatus"] = "completed"
        self.assertEqual(self.line.post(self.path, params, signature=sig)[0], 403)
        self.assertEqual(self.line.state(self.req["call_id"]), "dialing")

    def test_call_sid_correlation(self):
        status, body = self.line.post(f"/twilio/voice/answer/{self.req['call_id']}", {"CallSid": "CA" + "9" * 32})
        self.assertEqual(status, 200)
        self.assertIn("<Hangup/>", body)
        self.assertNotIn("ConversationRelay", body)
        self.line.post(self.path, {"CallSid": "CA" + "9" * 32, "CallStatus": "completed"})
        self.assertEqual(self.line.state(self.req["call_id"]), "dialing")
        kinds = [e["kind"] for e in self.line.store.events(self.req["call_id"])]
        self.assertIn("correlation_rejected", kinds)

    def test_owner_routes_need_the_owner_leg_sid(self):
        status, body = self.line.post(f"/twilio/voice/owner/accept/{self.req['call_id']}",
                                      {"CallSid": self.created["sid"], "Digits": "1"})
        self.assertNotIn("<Conference", body)

    def test_oversized_and_wrong_content_type(self):
        big = {"CallSid": self.created["sid"], "Pad": "x" * 70000}
        self.assertEqual(self.line.post(self.path, big)[0], 403)
        req = urllib.request.Request(self.line.base + self.line.svc.auth.prefix() + self.path, data=b'{"a":1}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 403)

    def test_relay_socket_authentication(self):
        body = self.line.answer(self.req["call_id"], self.created["sid"])
        with self.assertRaises(InvalidStatus):
            self.line.relay(body, sign=False)
        with self.assertRaises(InvalidStatus):
            self.line.relay(body, signature="bad")
        # failed attempts above were refused before the token was spent
        relay = self.line.relay(body)
        url = relay.public_url
        with self.assertRaises(InvalidStatus):
            Relay(self.line, url)  # single-use token
        forged = re.sub(r"t=[^&]+", "t=forged", url)
        with self.assertRaises(InvalidStatus):
            Relay(self.line, forged)
        relay.send({"type": "setup", "callSid": "CA" + "8" * 32})  # setup for another call
        relay.closed.wait(5)
        self.assertTrue(relay.closed.is_set())
        kinds = [e["kind"] for e in self.line.store.events(self.req["call_id"])]
        self.assertIn("relay_correlation_mismatch", kinds)

    def test_one_relay_socket_per_call(self):
        first = self.line.relay(self.line.answer(self.req["call_id"], self.created["sid"]))
        first.send({"type": "setup", "callSid": self.created["sid"]})
        second_body = self.line.answer(self.req["call_id"], self.created["sid"])
        with self.assertRaises(InvalidStatus):  # the retransmitted TwiML carries the spent token (R1 M2)
            self.line.relay(second_body)
        self.assertFalse(first.closed.is_set())

    def test_health_leaks_nothing(self):
        with urllib.request.urlopen(self.line.base + "/health", timeout=5) as resp:
            self.assertEqual(json.loads(resp.read()), {"ok": True})


class DesktopApiTests(unittest.TestCase):
    def setUp(self):
        self.line = Line()
        self.api = DesktopApi(lambda: self.line.store, wait_s=3.0)

    def tearDown(self):
        self.line.close()

    def test_snapshot_shape(self):
        req, created = self.line.dial()
        relay = self.line.connect(req, created)
        relay.prompt("Please hold, your verification code is 123456.")
        self.line.wait_state(req["call_id"], "on_hold")
        status, snap = self.api.get_calls()
        self.assertEqual(status, 200)
        self.assertIsInstance(snap["revision"], int)
        call = snap["calls"][0]
        for key in ("id", "task_title", "direction", "counterpart_masked", "state", "started_at", "state_since",
                    "duration_s", "mode", "transcript", "summary"):
            self.assertIn(key, call)
        self.assertEqual(call["direction"], "outbound")
        self.assertEqual(call["counterpart_masked"], "+1 775-•••-0101")
        self.assertEqual(call["state"], "on_hold")
        self.assertEqual(call["mode"], "agent")
        self.assertRegex(call["state_since"], ISO)
        self.assertRegex(call["started_at"], ISO)
        for line in call["transcript"]:
            self.assertEqual(set(line), {"seq", "speaker", "text", "ts"})
            self.assertIn(line["speaker"], ("hermes", "business", "owner", "system"))
            self.assertRegex(line["ts"], ISO)
        self.assertNotIn("123456", json.dumps(snap))
        self.assertIn("[CODE]", json.dumps(snap))
        self.assertNotIn(BUSINESS, json.dumps(snap))

    def test_actions_validation_staleness_and_idempotency(self):
        req, created = self.line.dial()
        relay = self.line.connect(req, created)
        relay.prompt("Thanks for calling, this is Mike.")
        relay.wait_text("live person?")
        _, snap = self.api.get_calls()
        rev = snap["revision"]
        self.assertEqual(self.api.post_call_action(req["call_id"], "say", {"revision": rev})[0], 400)
        self.assertEqual(self.api.post_call_action(req["call_id"], "say", {"idempotency_key": "k" * 10,
                                                                           "revision": rev})[0], 400)
        self.assertEqual(self.api.post_call_action(req["call_id"], "explode", {})[0], 404)
        self.assertEqual(self.api.post_call_action("call_nope", "hang_up", {"idempotency_key": "k" * 10,
                                                                            "revision": rev})[0], 404)
        relay.prompt("Yes, I am a real person")
        self.line.wait_state(req["call_id"], "agent_talking")
        status, body = self.api.post_call_action(req["call_id"], "hang_up", {"idempotency_key": "stale-key-1",
                                                                             "revision": rev})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "stale_revision")
        self.assertEqual(self.line.state(req["call_id"]), "agent_talking")
        _, snap = self.api.get_calls()
        key = "7f1c2a9e-3b6d-4c1e-9a7b-1d2e3f4a5b6c"
        status, body = self.api.post_call_action(req["call_id"], "say", {"idempotency_key": key,
                                                                         "revision": snap["revision"],
                                                                         "text": "One moment please."})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["result"]["ok"])
        status, again = self.api.post_call_action(req["call_id"], "say", {"idempotency_key": key,
                                                                          "revision": snap["revision"],
                                                                          "text": "One moment please."})
        self.assertTrue(again["duplicate"])
        self.assertEqual(relay.spoken().count("One moment please."), 1)
        _, snap = self.api.get_calls()
        status, body = self.api.post_call_action(req["call_id"], "hang_up", {"idempotency_key": "hang-up-key-1",
                                                                             "revision": snap["revision"]})
        self.assertEqual(status, 200)
        self.line.wait_state(req["call_id"], "ended")
        status, body = self.api.post_call_action(req["call_id"], "hang_up", {"idempotency_key": "hang-up-key-2",
                                                                             "revision": self.line.store.revision()})
        self.assertEqual(status, 409)

    def test_take_over_from_pane(self):
        req, created = self.line.dial()
        relay = self.line.connect(req, created)
        relay.prompt("Please hold.")
        self.line.wait_state(req["call_id"], "on_hold")
        _, snap = self.api.get_calls()
        status, body = self.api.post_call_action(req["call_id"], "take_over", {"idempotency_key": "take-over-1",
                                                                               "revision": snap["revision"]})
        self.assertEqual(status, 200, body)
        self.line.wait_state(req["call_id"], "ringing_owner")
        self.assertEqual(self.line.adapter.created[-1]["to"], OWNER)

    def test_approve_and_deny_from_pane(self):
        req = self.line.request()
        _, snap = self.api.get_calls()
        self.assertEqual(snap["calls"][0]["state"], "awaiting_approval")
        status, body = self.api.post_task_action(req["task_id"], "approve", {"idempotency_key": "approve-001",
                                                                             "revision": snap["revision"]})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["result"]["ok"])
        wait_for(lambda: self.line.adapter.created)
        other = self.line.request(title="Second")
        self.assertEqual(self.api.post_task_action(other["task_id"], "deny", {"idempotency_key": "deny-0001",
                                                                              "revision": 0})[0], 409)
        status, body = self.api.post_task_action(other["task_id"], "deny", {"idempotency_key": "deny-0002",
                                                                            "revision": self.line.store.revision()})
        self.assertEqual(status, 200)
        self.assertEqual(self.line.state(other["call_id"]), "failed")

    def test_events_carry_snapshot_revision(self):
        req, created = self.line.dial()
        self.line.connect(req, created).prompt("Please hold.")
        self.line.wait_state(req["call_id"], "on_hold")
        cid, rev = self.line.events[-1]
        self.assertLessEqual(rev, self.line.store.revision())
        self.assertTrue(all(isinstance(r, int) for _, r in self.line.events))

    def test_fastapi_router(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from plugin.line.server.desktop_api import create_router
        app = FastAPI()
        app.include_router(create_router(lambda: self.line.store), prefix="/api/plugins/hermes-line")
        client = TestClient(app)
        req = self.line.request()
        snap = client.get("/api/plugins/hermes-line/calls").json()
        self.assertEqual(snap["calls"][0]["id"], req["call_id"])
        r = client.post(f"/api/plugins/hermes-line/calls/{req['call_id']}/hang_up",
                        json={"idempotency_key": "router-key-1", "revision": snap["revision"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(client.post(f"/api/plugins/hermes-line/calls/{req['call_id']}/say",
                                     content=b"not json").status_code, 400)


class LifecycleTests(unittest.TestCase):
    def test_crash_recovery_hangs_up_orphans(self):
        tmp = Path(tempfile.mkdtemp(prefix="hl-rec-"))
        first = Line(store_dir=tmp, start=False)
        task = first.store.create_task(title="Hold for me", goal="g", destination_e164=BUSINESS,
                                       destination_masked="+1 775-•••-0101", mode=Mode.AGENT,
                                       origin=__import__("plugin.line.engine.contracts", fromlist=["Origin"]).Origin())
        call = first.store.create_call(task_id=task.id, direction=Direction.OUTBOUND, counterpart_e164=BUSINESS,
                                       counterpart_masked="+1 775-•••-0101", state=CallState.ON_HOLD,
                                       mode=Mode.AGENT, provider_sid="CA" + "4" * 32)
        first.store.write_lease("default", "dead-owner", 999999, "ready")  # crashed owner left this behind
        first.store.close()
        second = Line(store_dir=tmp)
        try:
            self.assertTrue(second.svc.lease.previous_crashed())
            self.assertIn(call.id, second.svc.recovered)
            self.assertIn("CA" + "4" * 32, second.adapter.ended)
            got = second.store.get_call(call.id)
            self.assertEqual(got.state, CallState.FAILED)
            self.assertEqual(got.end_reason, "service_restart")
            self.assertEqual(second.store.get_task(task.id).status.value, "failed")
            wait_for(lambda: second.delivered, what="recovery summary")
        finally:
            second.close()

    def test_clean_stop_ends_live_calls_and_releases(self):
        line = Line()
        req, created = line.dial()
        line.connect(req, created)
        line.svc.stop(timeout=5)
        try:
            call = line.store.get_call(req["call_id"])
            self.assertEqual(call.state, CallState.FAILED)
            self.assertEqual(call.end_reason, "service_restart")
            self.assertIn(created["sid"], line.adapter.ended)
            self.assertEqual(line.store.read_lease("default")["status"], "stopped")
            self.assertEqual(line.svc.status, "stopped")
        finally:
            line.store.close()

    def test_second_process_waits_in_standby(self):
        tmp = Path(tempfile.mkdtemp(prefix="hl-sb-"))
        owner = Line(store_dir=tmp)
        standby = Line(store_dir=tmp, start=False)
        try:
            with self.assertRaises(LeaseHeld):
                standby.svc.start(port=0)
            self.assertEqual(standby.svc.status, "standby")
            self.assertIn("pid", standby.svc.detail)
            req = standby.request()  # any process can create a request
            out = standby.svc.submit("approve", source="slash", task_id=req["task_id"])
            self.assertEqual(out["status"], "queued")  # executed by the owner, not here
            wait_for(lambda: owner.adapter.created, what="owner process dialled")
            self.assertEqual(standby.adapter.total_calls, 0)
            owner.svc.stop(timeout=5)
            standby.svc.start(port=0)
            self.assertEqual(standby.svc.status, "ready")
        finally:
            standby.close()
            owner.store.close()

    def test_live_adapter_refuses_to_start_misconfigured(self):
        from plugin.line.engine.telephony import TwilioRestAdapter
        line = Line(start=False, public_base_url="")
        try:
            line.svc.adapter = TwilioRestAdapter(lambda name: None)
            with self.assertRaises(RuntimeError):
                line.svc.start(port=0)
            self.assertEqual(line.svc.status, "misconfigured")
        finally:
            line.store.close()


class RestAdapterTests(unittest.TestCase):
    def test_requests_are_built_without_leaking_credentials(self):
        from plugin.line.engine.telephony import ProviderError, TwilioRestAdapter
        sent = []

        class Resp:
            def __init__(self, body):
                self.body = body
            def read(self):
                return self.body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            sent.append(req)
            return Resp(json.dumps({"sid": "CA" + "5" * 32}).encode())

        secrets = {"HERMES_LINE_TWILIO_ACCOUNT_SID": ACCOUNT, "HERMES_LINE_TWILIO_API_KEY_SID": "SK" + "6" * 32,
                   "HERMES_LINE_TWILIO_API_KEY_SECRET": "s3cr3t-value"}
        adapter = TwilioRestAdapter(secrets.get, opener=opener)
        sid = adapter._create_call(to=BUSINESS, from_number=LINE, answer_url="https://h/a", status_url="https://h/s",
                                   timeout_s=30, time_limit_s=5400)
        self.assertEqual(sid, "CA" + "5" * 32)
        body = sent[0].data.decode()
        self.assertIn("Record=false", body)
        self.assertIn("TimeLimit=5400", body)
        self.assertNotIn("s3cr3t", body)
        self.assertTrue(sent[0].full_url.endswith(f"/Accounts/{ACCOUNT}/Calls.json"))
        broken = TwilioRestAdapter({}.get, opener=opener)
        with self.assertRaises(ProviderError) as ctx:
            broken._create_call(to=BUSINESS, from_number=LINE, answer_url="a", status_url="s", timeout_s=1,
                                time_limit_s=1)
        self.assertNotIn("s3cr3t", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
