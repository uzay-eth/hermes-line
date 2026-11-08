"""Regression tests for the R1 security review findings (H2, M1, M2, M3, signature downgrade)."""
import re
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from websockets.exceptions import InvalidStatus

from harness import ACCOUNT, BUSINESS, LINE, Line, Relay, wait_for
from plugin.line.server.ingress_auth import INBOUND_LIVE, LegExpectation, RestVerifier

NO_TOKEN = {"HERMES_LINE_TWILIO_ACCOUNT_SID": ACCOUNT}
CALLER_B = "+17755550177"


def tokens(line):
    return line.store._conn.execute("SELECT COUNT(*) FROM relay_tokens").fetchone()[0]


def reasons(line):
    return [e["data"].get("reason") for e in line.store.events() if e["kind"] == "ingress_rejected"]


def inbound_calls(line):
    return [c for c in line.store.list_calls() if c.direction.value == "inbound"]


# ----------------------------------------------------------------------------------------- H2
class H2VerifierCacheTests(unittest.TestCase):
    def _verifier(self, records, account=ACCOUNT, **kw):
        acct = {"v": account}
        return RestVerifier(lambda sid: records.get(sid), lambda: acct["v"], **kw), acct

    def test_H2_changed_from_after_cache_is_rejected(self):
        sid = "CA" + "a" * 32
        records = {sid: {"sid": sid, "account_sid": ACCOUNT, "direction": "inbound", "from": BUSINESS, "to": LINE,
                         "status": "ringing"}}
        v, _ = self._verifier(records)
        self.assertEqual(v.verify(LegExpectation(sid, "inbound", LINE, BUSINESS)), (True, "verified"))
        self.assertEqual(v.verify(LegExpectation(sid, "inbound", LINE, BUSINESS)), (True, "cached"))
        self.assertEqual(v.verify(LegExpectation(sid, "inbound", LINE, CALLER_B)),
                         (False, "rest_conflicting_expectation"))
        self.assertEqual(v.verify(LegExpectation(sid, "outbound", LINE, BUSINESS)),
                         (False, "rest_conflicting_expectation"))

    def test_H2_cache_is_keyed_by_account(self):
        sid = "CA" + "b" * 32
        records = {sid: {"sid": sid, "account_sid": ACCOUNT, "direction": "inbound", "from": BUSINESS, "to": LINE}}
        v, acct = self._verifier(records)
        self.assertTrue(v.verify(LegExpectation(sid, "inbound", LINE, BUSINESS))[0])
        acct["v"] = "AC" + "7" * 32
        self.assertEqual(v.verify(LegExpectation(sid, "inbound", LINE, BUSINESS)),
                         (False, "rest_conflicting_expectation"))

    def test_H2_cache_entries_expire_and_are_bounded(self):
        now = [0.0]
        records = {}
        for i in range(5):
            sid = f"CA{i:032d}"
            records[sid] = {"sid": sid, "account_sid": ACCOUNT, "direction": "inbound", "from": BUSINESS, "to": LINE}
        v, _ = self._verifier(records, ttl_s=10, max_entries=3, clock=lambda: now[0])
        for sid in records:
            now[0] += 1
            v.verify(LegExpectation(sid, "inbound", LINE, BUSINESS))
        self.assertEqual(len(v), 3)  # oldest evicted
        now[0] += 60
        v.verify(LegExpectation("CA" + "0" * 31 + "4", "inbound", LINE, BUSINESS))
        self.assertEqual(len(v), 1)  # expired entries dropped

    def test_H2_provider_returned_sid_must_match(self):
        sid = "CA" + "c" * 32
        records = {sid: {"sid": "CA" + "d" * 32, "account_sid": ACCOUNT, "direction": "inbound", "from": BUSINESS,
                         "to": LINE}}
        v, _ = self._verifier(records)
        self.assertEqual(v.verify(LegExpectation(sid, "inbound", LINE, BUSINESS)), (False, "rest_sid_mismatch"))

    def test_H2_live_status_required_for_admission(self):
        sid = "CA" + "e" * 32
        records = {sid: {"sid": sid, "account_sid": ACCOUNT, "direction": "inbound", "from": BUSINESS, "to": LINE,
                         "status": "completed"}}
        v, _ = self._verifier(records)
        self.assertEqual(v.verify(LegExpectation(sid, "inbound", LINE, BUSINESS), require_status=INBOUND_LIVE),
                         (False, "rest_status_not_live"))


class H2AdmissionTests(unittest.TestCase):
    def _post(self, line, sid, caller=BUSINESS, sign=False):
        return line.post("/twilio/voice/inbound", {"CallSid": sid, "From": caller, "To": LINE}, sign=sign)

    def test_H2_completed_sid_replay_is_refused(self):
        line = Line(secrets=NO_TOKEN)
        try:
            sid = "CA" + "1" * 32
            line.adapter.add_inbound(sid, from_=BUSINESS, to=LINE, status="completed")
            status, body = self._post(line, sid)
            self.assertIn("<Reject", body)
            self.assertEqual(inbound_calls(line), [])
            self.assertEqual(reasons(line)[-1], "rest_status_not_live")
        finally:
            line.close()

    def test_H2_changed_from_after_admission_is_refused(self):
        line = Line(secrets=NO_TOKEN)
        try:
            sid = "CA" + "2" * 32
            line.adapter.add_inbound(sid, from_=BUSINESS, to=LINE)
            self.assertIn("<ConversationRelay", self._post(line, sid)[1])
            before = (len(line.store.list_calls()), tokens(line), len(line.drivers))
            status, body = self._post(line, sid, caller=CALLER_B)
            self.assertIn("<Reject", body)
            self.assertEqual((len(line.store.list_calls()), tokens(line), len(line.drivers)), before)
            self.assertEqual(reasons(line)[-1], "rest_conflicting_expectation")
        finally:
            line.close()

    def test_H2_twilio_retry_returns_the_existing_result(self):
        for secrets in (NO_TOKEN, None):
            line = Line(secrets=secrets)
            try:
                sign = secrets is None
                sid = "CA" + "3" * 32
                line.adapter.add_inbound(sid, from_=BUSINESS, to=LINE)
                first = self._post(line, sid, sign=sign)[1]
                before = (len(line.store.list_calls()), tokens(line), len(line.svc.sessions))
                second = self._post(line, sid, sign=sign)[1]
                self.assertEqual(first, second)
                self.assertEqual((len(line.store.list_calls()), tokens(line), len(line.svc.sessions)), before)
            finally:
                line.close()

    def test_H2_replay_after_session_end_is_refused(self):
        for secrets in (NO_TOKEN, None):
            line = Line(secrets=secrets)
            try:
                sign = secrets is None
                sid = "CA" + "4" * 32
                line.adapter.add_inbound(sid, from_=BUSINESS, to=LINE)  # REST still says in-progress
                self.assertIn("<ConversationRelay", self._post(line, sid, sign=sign)[1])
                call = inbound_calls(line)[0]
                line.svc.submit("hang_up", source="slash", call_id=call.id)
                line.wait_state(call.id, "ended")
                status, body = self._post(line, sid, sign=sign)
                self.assertIn("<Reject", body)
                self.assertEqual(len(inbound_calls(line)), 1)
                self.assertEqual(line.svc.sessions, {})
                self.assertIn("inbound_replay", reasons(line))
            finally:
                line.close()

    def test_H2_replay_after_restart_is_refused(self):
        tmp = Path(tempfile.mkdtemp(prefix="hl-h2-"))
        sid = "CA" + "5" * 32
        first = Line(secrets=NO_TOKEN, store_dir=tmp)
        first.adapter.add_inbound(sid, from_=BUSINESS, to=LINE)
        self.assertIn("<ConversationRelay", self._post(first, sid)[1])
        first.svc.stop(timeout=5)
        first.store.close()
        second = Line(secrets=NO_TOKEN, store_dir=tmp)
        try:
            second.adapter.add_inbound(sid, from_=BUSINESS, to=LINE)
            status, body = self._post(second, sid)
            self.assertIn("<Reject", body)
            self.assertEqual(second.svc.sessions, {})
            self.assertEqual(len(inbound_calls(second)), 1)  # only the original, now failed by the restart
        finally:
            second.close()

    def test_H2_concurrent_admission_creates_one_session(self):
        line = Line(secrets=NO_TOKEN)
        try:
            sid = "CA" + "6" * 32
            line.adapter.add_inbound(sid, from_=BUSINESS, to=LINE)
            bodies = []
            threads = [threading.Thread(target=lambda: bodies.append(self._post(line, sid)[1])) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
            self.assertEqual(len(bodies), 8)
            self.assertEqual(len(set(bodies)), 1)
            self.assertEqual(len(inbound_calls(line)), 1)
            self.assertEqual(len(line.svc.sessions), 1)
            self.assertEqual(tokens(line), 1)
        finally:
            line.close()

    def test_H2_verifier_forgets_a_call_when_it_ends(self):
        line = Line(secrets=NO_TOKEN)
        try:
            req, created = line.dial()
            line.post(f"/twilio/voice/answer/{req['call_id']}", {"CallSid": created["sid"]}, sign=False)
            self.assertEqual(len(line.svc.auth.verifier), 1)
            line.svc.submit("hang_up", source="slash", call_id=req["call_id"])
            line.wait_state(req["call_id"], "ended")
            wait_for(lambda: len(line.svc.auth.verifier) == 0, what="cache evicted")
        finally:
            line.close()


# ----------------------------------------------------------------------------------------- M2
class M2GenerationTests(unittest.TestCase):
    MODES = (("signature", None), ("path_secret", NO_TOKEN))

    def _connected(self, line, sign):
        req, created = line.dial()
        body = line.post(f"/twilio/voice/answer/{req['call_id']}", {"CallSid": created["sid"]}, sign=sign)[1]
        relay = line.relay(body, sign=sign)
        relay.send({"type": "setup", "callSid": created["sid"]})
        relay.prompt("Please hold.")
        line.wait_state(req["call_id"], "on_hold")
        return req, created, body, relay

    def _action(self, line, req, created, g, sign):
        return line.post(f"/twilio/voice/connect/action/{req['call_id']}?g={g}", {"CallSid": created["sid"]},
                         sign=sign)[1]

    def test_M2_duplicate_connect_action_debits_the_retry_budget_once(self):
        for mode, secrets in self.MODES:
            with self.subTest(mode=mode):
                line = Line(secrets=secrets, relay_reconnect_grace_s=10.0, relay_max_reconnects=1)
                try:
                    sign = secrets is None
                    req, created, body, relay = self._connected(line, sign)
                    relay.close()
                    wait_for(lambda: line.session(req["call_id"]).relay is None)
                    before = tokens(line)
                    answers = [self._action(line, req, created, 0, sign) for _ in range(4)]
                    self.assertEqual(len(set(answers)), 1)
                    self.assertIn("<ConversationRelay", answers[0])
                    self.assertEqual(line.session(req["call_id"]).reconnects, 1)
                    self.assertEqual(tokens(line), before + 1)  # one new token set for generation 1, not four
                    self.assertEqual(line.state(req["call_id"]), "on_hold")  # never hung up by replays
                finally:
                    line.close()

    def test_M2_replays_never_hang_up_a_recovered_call(self):
        for mode, secrets in self.MODES:
            with self.subTest(mode=mode):
                line = Line(secrets=secrets, relay_reconnect_grace_s=10.0, relay_max_reconnects=1)
                try:
                    sign = secrets is None
                    req, created, body, relay = self._connected(line, sign)
                    relay.close()
                    wait_for(lambda: line.session(req["call_id"]).relay is None)
                    again = self._action(line, req, created, 0, sign)
                    second = line.relay(again, sign=sign)
                    second.send({"type": "setup", "callSid": created["sid"]})
                    wait_for(lambda: line.session(req["call_id"]).relay is not None)
                    for _ in range(5):  # the budget is spent; replays of g=0 must still change nothing
                        self.assertEqual(self._action(line, req, created, 0, sign), again)
                    self.assertEqual(line.state(req["call_id"]), "on_hold")
                    self.assertEqual(line.adapter.ended, [])
                finally:
                    line.close()

    def test_M2_unknown_or_future_generation_changes_nothing(self):
        for mode, secrets in self.MODES:
            with self.subTest(mode=mode):
                line = Line(secrets=secrets)
                try:
                    sign = secrets is None
                    req, created, body, relay = self._connected(line, sign)
                    before = (tokens(line), line.session(req["call_id"]).reconnects)
                    current = line.session(req["call_id"]).gen_twiml[0]
                    for g in ("7", "", "x", "-1"):
                        out = line.post(f"/twilio/voice/connect/action/{req['call_id']}?g={g}",
                                        {"CallSid": created["sid"]}, sign=sign)[1]
                        self.assertIn(current, out)  # round 5: the current generation again, never empty
                        self.assertNotEqual(out.split("<Response>")[1], "</Response>")
                    self.assertEqual((tokens(line), line.session(req["call_id"]).reconnects), before)
                    self.assertEqual(line.state(req["call_id"]), "on_hold")
                finally:
                    line.close()

    def test_M2_connect_action_while_the_relay_is_live_is_not_believed(self):
        line = Line(secrets=NO_TOKEN)
        try:
            req, created, body, relay = self._connected(line, False)
            before = tokens(line)
            out = self._action(line, req, created, 0, False)
            self.assertIn(line.session(req["call_id"]).gen_twiml[0], out)  # spent token, nothing new
            self.assertEqual((tokens(line), line.session(req["call_id"]).reconnects), (before, 0))
            self.assertIn("connect_unconfirmed", [e["kind"] for e in line.store.events(req["call_id"])])
        finally:
            line.close()

    def test_M2_answer_retransmission_mints_no_new_token(self):
        for mode, secrets in self.MODES:
            with self.subTest(mode=mode):
                line = Line(secrets=secrets)
                try:
                    sign = secrets is None
                    req, created = line.dial()
                    bodies = [line.post(f"/twilio/voice/answer/{req['call_id']}", {"CallSid": created["sid"]},
                                        sign=sign)[1] for _ in range(3)]
                    self.assertEqual(len(set(bodies)), 1)
                    self.assertEqual(tokens(line), 1)
                    Relay(line, re.search(r'url="([^"]+)"', bodies[0]).group(1).replace("&amp;", "&"),
                          sign=sign).close()
                    with self.assertRaises(InvalidStatus):  # the one token set is single-use
                        Relay(line, re.search(r'url="([^"]+)"', bodies[2]).group(1).replace("&amp;", "&"),
                              sign=sign)
                finally:
                    line.close()


class M2DeltaConcurrentCompletionTests(unittest.TestCase):
    """R1 delta M2: overlapping Connect completions for one generation are single-flight."""
    MODES = M2GenerationTests.MODES

    def _concurrent(self, line, req, created, g, sign, n=3, close=None):
        out, errors = [None] * n, []

        def post(i):
            try:
                out[i] = M2GenerationTests._action(None, line, req, created, g, sign)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=post, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        if close is not None:
            wait_for(lambda: len(line.session(req["call_id"])._completing) == 1)  # all are waiting on one claim
            time.sleep(0.5)
            close.close()
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        return out

    def test_M2_delta_concurrent_completions_recover_once_with_identical_answers(self):
        for mode, secrets in self.MODES:
            with self.subTest(mode=mode):
                line = Line(secrets=secrets, relay_reconnect_grace_s=10.0)  # default budget of two
                try:
                    sign = secrets is None
                    self.assertEqual(line.svc.cfg.relay_max_reconnects, 2)
                    req, created, body, relay = M2GenerationTests._connected(None, line, sign)
                    before = tokens(line)
                    answers = self._concurrent(line, req, created, 0, sign, close=relay)
                    self.assertEqual(len(set(answers)), 1, answers)
                    self.assertIn("<ConversationRelay", answers[0])
                    session = line.session(req["call_id"])
                    self.assertEqual(session.reconnects, 1)
                    self.assertEqual(session.relay_gen, 1)
                    self.assertEqual(tokens(line), before + 1)
                    self.assertEqual(line.state(req["call_id"]), "on_hold")
                    self.assertEqual(line.adapter.ended, [])
                    kinds = [e["kind"] for e in line.store.events(req["call_id"])]
                    self.assertEqual(kinds.count("relay_reconnect"), 1)
                    self.assertNotIn("relay_reconnect_exhausted", kinds)
                finally:
                    line.close()

    def test_M2_delta_new_relay_attaching_while_old_completions_wait(self):
        for mode, secrets in self.MODES:
            with self.subTest(mode=mode):
                line = Line(secrets=secrets, relay_reconnect_grace_s=10.0)
                try:
                    sign = secrets is None
                    req, created, body, relay = M2GenerationTests._connected(None, line, sign)
                    first = self._concurrent(line, req, created, 0, sign, close=relay)
                    self.assertEqual(len(set(first)), 1)
                    second = line.relay(first[0], sign=sign)  # generation 1 attaches
                    second.send({"type": "setup", "callSid": created["sid"]})
                    wait_for(lambda: line.session(req["call_id"]).relay is not None)
                    late = self._concurrent(line, req, created, 0, sign)  # old generation, overlapping
                    self.assertEqual(set(late), {first[0]})
                    self.assertEqual(line.session(req["call_id"]).reconnects, 1)
                    again = self._concurrent(line, req, created, 1, sign, close=second)  # gen 1 ends for real
                    self.assertEqual(len(set(again)), 1)
                    self.assertIn("<ConversationRelay", again[0])
                    self.assertEqual(line.session(req["call_id"]).reconnects, 2)
                    self.assertEqual(line.state(req["call_id"]), "on_hold")
                    self.assertEqual(line.adapter.ended, [])
                finally:
                    line.close()


class R5PreservingTwimlTests(unittest.TestCase):
    """Round 5 regression: a Connect action we do not act on must never get an empty <Response/>
    (Twilio would hang up the live business leg). Found by tests/l5 owner-mode rehearsal."""

    def test_R5_action_without_generation_keeps_a_live_call_and_changes_nothing(self):
        line = Line()
        try:
            req, created, body, relay = M2GenerationTests()._connected(line, True)
            before = (tokens(line), line.session(req["call_id"]).reconnects)
            out = line.post(f"/twilio/voice/connect/action/{req['call_id']}", {"CallSid": created["sid"]})[1]
            self.assertIn(line.session(req["call_id"]).gen_twiml[0], out)
            self.assertEqual((tokens(line), line.session(req["call_id"]).reconnects), before)
            self.assertEqual(line.state(req["call_id"]), "on_hold")
        finally:
            line.close()

    def test_R5_action_without_generation_during_handoff_returns_the_conference(self):
        line = Line()
        try:
            req, created = line.dial(mode="owner")
            relay = line.connect(req, created)
            relay.prompt("Hello, this is Mike.")
            relay.wait_text("live person?")
            relay.prompt("Yes, I am a real person")
            line.wait_state(req["call_id"], "ringing_owner")
            owner = line.adapter.created[-1]
            line.post(f"/twilio/voice/owner/accept/{req['call_id']}", {"CallSid": owner["sid"], "Digits": "1"})
            line.wait_state(req["call_id"], "owner_connected")
            out = line.post(f"/twilio/voice/connect/action/{req['call_id']}", {"CallSid": created["sid"]})[1]
            self.assertIn("<Conference", out)
        finally:
            line.close()

    def test_R5_action_after_the_call_ended_hangs_up(self):
        line = Line()
        try:
            req, created, body, relay = M2GenerationTests()._connected(line, True)
            session = line.session(req["call_id"])
            line.svc.run(session.hang_up(__import__("plugin.line.engine.contracts",
                                                    fromlist=["EndReason"]).EndReason.HUNG_UP_BY_OWNER))
            out = line.svc.run(_preserving(session))
            self.assertIn("<Hangup/>", out)
        finally:
            line.close()


async def _preserving(session):
    return session._preserving_twiml()


# ----------------------------------------------------------------------------------------- M3
class M3StaleAfterEnqueueTests(unittest.TestCase):
    def test_M3_command_refused_when_the_call_changes_after_enqueue(self):
        line = Line()
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Please hold, your call is important to us.")
            line.wait_state(req["call_id"], "on_hold")
            gate = threading.Event()
            real_claim = line.store.claim_commands
            line.store.claim_commands = lambda limit=20: real_claim(limit) if gate.is_set() else []
            observed = line.store.get_call(req["call_id"]).control_revision
            out = line.svc.submit("say", source="desktop", call_id=req["call_id"], payload={"text": "Hi Mike!"},
                                  expected_revision=observed, wait_s=0.2)
            self.assertEqual(out["status"], "queued")
            relay.prompt("Thanks for waiting, this is Dana.")
            # L2 round 6: a named greeting after hold is a human verdict (high); no live-person question is needed.
            line.wait_state(req["call_id"], "agent_talking")
            self.assertIn({"label": "human", "confidence": "high"},
                          [e["data"] for e in line.store.events(req["call_id"]) if e["kind"] == "detect"])
            self.assertNotIn("live person?", relay.spoken())
            gate.set()
            line.svc.wake()
            done = wait_for(lambda: (line.store.get_command(out["command_id"]) or {}).get("result"))
            self.assertEqual(done["error"], "stale_revision")
            self.assertTrue(done["stale"])
            time.sleep(0.2)
            self.assertNotIn("Hi Mike!", relay.spoken())
            self.assertIn("command_stale", [e["kind"] for e in line.store.events(req["call_id"])])
        finally:
            line.close()

    def test_M3_unchanged_call_still_executes(self):
        line = Line()
        try:
            req, created = line.dial()
            relay = line.connect(req, created)
            relay.prompt("Please hold.")
            line.wait_state(req["call_id"], "on_hold")
            observed = line.store.get_call(req["call_id"]).control_revision
            relay.prompt("Please hold.")  # transcript only: not a control change
            out = line.svc.submit("hang_up", source="desktop", call_id=req["call_id"], expected_revision=observed)
            self.assertTrue(out["result"]["ok"], out)
        finally:
            line.close()


# ----------------------------------------------------------------------------------------- M1
class M1PendingApprovalTests(unittest.TestCase):
    def setUp(self):
        from plugin.line.sms import FixtureSource, OtpService
        self.line = Line(start=False)
        self.line.svc.otp = OtpService(FixtureSource([]))
        self.line.svc.start(port=0)

    def tearDown(self):
        self.line.close()

    def _human(self, mode="owner"):
        req, created = self.line.dial(mode=mode)
        relay = self.line.connect(req, created)
        relay.prompt("Hello, this is Mike.")
        relay.wait_text("live person?")
        relay.prompt("Yes, I am a real person")
        return req, created, relay

    def _allow(self, task_id, payload):
        return self.line.svc.submit("allow_code", source="slash", task_id=task_id, payload=payload)["result"]

    def test_M1_confirmation_refused_when_the_displayed_call_ended_and_a_callback_took_its_place(self):
        req, created, relay = self._human(mode="agent")
        self.line.wait_state(req["call_id"], "agent_talking")
        prompt, payload = self.line.svc.otp_preview(req["task_id"], ["EXAMPLEBANK"], "call")
        self.assertIn(req["call_id"], prompt)
        self.line.svc.submit("hang_up", source="slash", call_id=req["call_id"])
        self.line.wait_state(req["call_id"], "ended")
        self.line.store.update_task(req["task_id"], status=__import__(
            "plugin.line.engine.contracts", fromlist=["TaskStatus"]).TaskStatus.AWAITING_CALLBACK)
        sid = "CA" + "9" * 32
        body = self.line.post("/twilio/voice/inbound", {"CallSid": sid, "From": BUSINESS, "To": LINE})[1]
        callback = [c for c in self.line.store.list_calls() if c.direction.value == "inbound"][0]
        self.assertEqual(callback.task_id, req["task_id"])
        out = self._allow(req["task_id"], payload)
        self.assertFalse(out["ok"])
        self.assertIn("new prompt", out["error"])
        self.assertIsNone(self.line.svc.otp_token(req["task_id"]))
        self.assertFalse(self.line.svc.otp_call_allowed(callback.id))
        self.assertEqual(self.line.store.get_otp_pending(payload["pending_id"])["status"], "refused")

    def test_M1_counterpart_change_is_refused(self):
        req, created, relay = self._human(mode="agent")
        self.line.wait_state(req["call_id"], "agent_talking")
        prompt, payload = self.line.svc.otp_preview(req["task_id"], ["EXAMPLEBANK"], "call")
        self.line.store._conn.execute("UPDATE calls SET counterpart_e164 = ? WHERE id = ?", (CALLER_B, req["call_id"]))
        out = self._allow(req["task_id"], payload)
        self.assertFalse(out["ok"])
        self.assertIsNone(self.line.svc.otp_token(req["task_id"]))

    def test_M1_pending_approval_is_single_use_and_carries_its_generation(self):
        req, created, relay = self._human(mode="agent")
        self.line.wait_state(req["call_id"], "agent_talking")
        self.assertIsNone(self.line.svc.otp_generation(req["task_id"]))  # no approval: line.sms refuses
        p1, pay1 = self.line.svc.otp_preview(req["task_id"], ["EXAMPLEBANK"], "call")
        self.assertEqual(pay1["prompt_seq"], 1)
        self.assertEqual(set(pay1), {"pending_id", "prompt_seq"})  # no re-resolvable "call" string
        ok = self._allow(req["task_id"], pay1)
        self.assertTrue(ok["ok"], ok)
        g1 = ok["approval_generation"]
        self.assertIsInstance(g1, int)  # the int line.sms's registry.register() returned
        self.assertEqual(self.line.svc.otp_generation(req["task_id"]), g1)
        self.assertEqual(self.line.store.get_otp_pending(pay1["pending_id"])["approval_generation"], g1)
        self.assertEqual(self.line.svc.otp.registry.get(req["task_id"]) is not None, True)
        self.assertFalse(self._allow(req["task_id"], pay1)["ok"])  # single use
        _, pay2 = self.line.svc.otp_preview(req["task_id"], ["EXAMPLEBANK"], "call")
        _, pay3 = self.line.svc.otp_preview(req["task_id"], ["EXAMPLEBANK"], "call")
        ok3 = self._allow(req["task_id"], pay3)
        self.assertTrue(ok3["ok"], ok3)
        self.assertNotEqual(ok3["approval_generation"], g1)  # a replacement approval is a new generation
        self.assertEqual(self.line.svc.otp_generation(req["task_id"]), ok3["approval_generation"])
        stale = self._allow(req["task_id"], pay2)
        self.assertFalse(stale["ok"])
        self.assertIn("newer approval", stale["error"])
        self.assertEqual(self.line.svc.otp_generation(req["task_id"]), ok3["approval_generation"])

    def test_M1_old_generation_cannot_release_after_replacement(self):
        """End to end with line.sms: a release under the first approval's generation is refused once a
        replacement approval exists (L4 binds its slot to the generation L1 hands it)."""
        req, created, relay = self._human(mode="agent")
        self.line.wait_state(req["call_id"], "agent_talking")
        _, pay1 = self.line.svc.otp_preview(req["task_id"], ["EXAMPLEBANK"], "call")
        g1 = self._allow(req["task_id"], pay1)["approval_generation"]
        _, pay2 = self.line.svc.otp_preview(req["task_id"], ["EXAMPLEBANK"], "call")
        g2 = self._allow(req["task_id"], pay2)["approval_generation"]
        token = self.line.svc.otp_token(req["task_id"])
        res = self.line.svc.otp.attempt(req["task_id"], token, self.line.svc.consent, approval_generation=g1)
        self.assertEqual(res.status.value, "approval_changed")
        self.assertIsNotNone(self.line.svc.otp_token(req["task_id"]))  # consent not spent by the refusal
        self.assertNotEqual(g1, g2)


# ------------------------------------------------------------------------------- R1 delta N1
class N1SealedSinkTests(unittest.TestCase):
    """R1 delta N1: the production call sink (engine.sealed) against an approval replacement."""
    CODE = "739204"

    def setUp(self):
        from plugin.line.sms import FixtureSource, OtpService, SmsMessage, SecretText
        from plugin.line.store.released_otps import ReleasedOtps
        self.line = Line(start=False)
        now_ms = int(time.time() * 1000)
        msg = SmsMessage(id="m1", address="EXAMPLEBANK", date_ms=now_ms + 5_000,
                         body=SecretText(f"Your EXAMPLEBANK verification code is {self.CODE}."), source="fixture")
        self.otp = OtpService(FixtureSource([msg]), released=ReleasedOtps(self.line.store))
        self.line.svc.otp = self.otp
        self.line.svc.start(port=0)

    def tearDown(self):
        self.line.close()

    def _human(self):
        req, created = self.line.dial(mode="agent")
        relay = self.line.connect(req, created)
        relay.prompt("Hello, this is Mike.")
        relay.wait_text("live person?")
        relay.prompt("Yes, I am a real person")
        self.line.wait_state(req["call_id"], "agent_talking")
        return req, relay

    def _approve(self, req, sender="EXAMPLEBANK"):
        _, payload = self.line.svc.otp_preview(req["task_id"], [sender], "call")
        out = self.line.svc.submit("allow_code", source="slash", task_id=req["task_id"], payload=payload)
        return out["result"]

    def _loop_alive(self, timeout=1.0):
        import asyncio
        return self.line.svc.run(asyncio.sleep(0, result=True), timeout=timeout)

    def test_N1_replacement_overlapping_delivery_never_blocks_the_loop_and_sends_nothing_late(self):
        from plugin.line.engine import sealed
        from plugin.line.sms import CallSink, OtpDelivery
        req, relay = self._human()
        g1 = self._approve(req)["approval_generation"]
        entered, proceed, forgetting = threading.Event(), threading.Event(), threading.Event()

        def paused_sink(call_id, secret, mode, **kw):  # the production sink, paused at entry inside a delivery
            entered.set()
            proceed.wait(15)
            return sealed.send_secret_to_call(call_id, secret, mode, **kw)

        forget = self.otp.registry.forget

        def watched_forget(task_id):
            forgetting.set()
            return forget(task_id)

        self.otp.registry.forget = watched_forget
        delivery = OtpDelivery(self.otp, call_sink=CallSink(send=paused_sink),
                               counterpart_of=self.line.svc.live_counterpart)
        token = self.line.svc.otp_token(req["task_id"])
        result, replaced = {}, {}
        a = threading.Thread(target=lambda: result.setdefault("r", delivery.to_call(
            req["task_id"], req["call_id"], "dtmf", token, self.line.svc.consent, approval_generation=g1)))
        a.start()
        self.assertTrue(entered.wait(5))
        b = threading.Thread(target=lambda: replaced.setdefault("r", self._approve(req, sender="OTHERBANK")))
        b.start()
        self.assertTrue(forgetting.wait(5))  # the replacement runs while the delivery is inside the sink
        started = time.monotonic()
        for _ in range(5):  # the service loop keeps running meanwhile
            self.assertTrue(self._loop_alive())
        self.assertLess(time.monotonic() - started, 2.0)
        proceed.set()
        a.join(15)
        b.join(15)
        self.assertNotEqual(result["r"].status, "sent")
        self.assertTrue(replaced["r"]["ok"], replaced)
        self.assertNotEqual(replaced["r"]["approval_generation"], g1)
        time.sleep(0.5)
        self.assertEqual(relay.of_type("sendDigits"), [])  # nothing after the reported failure
        self.assertTrue(self.line.svc.otp_call_allowed(req["call_id"]))  # the replacement is in force
        self.assertEqual(self.line.svc.otp_generation(req["task_id"]), replaced["r"]["approval_generation"])
        self.assertNotIn(self.CODE, str(self.line.store.events()))

    def test_N1_residual_missing_generation_is_refused_even_with_a_live_grant(self):
        from plugin.line.engine import sealed
        from plugin.line.sms import SecretText
        req, relay = self._human()
        self._grant(req, 1)
        self.assertTrue(self.line.svc.otp_call_allowed(req["call_id"]))
        for missing in ({}, {"approval_generation": None}, {"approval_generation": True},
                        {"approval_generation": "1"}):
            self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(self.CODE), "dtmf", **missing))
        time.sleep(0.3)
        self.assertEqual(relay.of_type("sendDigits"), [])

    def test_N1_residual_legacy_sink_cannot_send_a_code_under_its_replacement(self):
        """A three-argument sink wrapper pauses after L4 released the code under approval A; the owner
        replaces A with B for the same call; the wrapper resumes without a generation. Nothing is sent."""
        from plugin.line.engine import sealed
        from plugin.line.sms import CallSink, OtpDelivery
        req, relay = self._human()
        g1 = self._approve(req)["approval_generation"]
        entered, proceed = threading.Event(), threading.Event()

        def legacy_sink(call_id, secret, mode):  # cannot carry the approval generation
            entered.set()
            proceed.wait(15)
            return sealed.send_secret_to_call(call_id, secret, mode)

        delivery = OtpDelivery(self.otp, call_sink=CallSink(send=legacy_sink),
                               counterpart_of=self.line.svc.live_counterpart)
        token = self.line.svc.otp_token(req["task_id"])
        result = {}
        a = threading.Thread(target=lambda: result.setdefault("r", delivery.to_call(
            req["task_id"], req["call_id"], "dtmf", token, self.line.svc.consent, approval_generation=g1)))
        a.start()
        entered.wait(5)  # L4 may also refuse such a sink before calling it (its half of N1)
        replaced = self._approve(req, sender="OTHERBANK")
        self.assertTrue(replaced["ok"], replaced)
        self.assertTrue(self.line.svc.otp_call_allowed(req["call_id"]))  # B is in force for the same call
        proceed.set()
        a.join(15)
        self.assertNotEqual(result["r"].status, "sent")
        time.sleep(0.5)
        self.assertEqual(relay.of_type("sendDigits"), [])
        self.assertNotIn(self.CODE, str(self.line.store.events()))

    def _grant(self, req, generation):
        self.line.svc.grants.put("otp_release", req["task_id"], "otp", {"x": 1}, 600, 600, renewable=False,
                                 meta={"kind": "call", "call_id": req["call_id"], "counterpart": BUSINESS,
                                       "approval_generation": generation})

    def test_N1_timed_out_send_can_never_send_later(self):
        from plugin.line.engine import sealed
        from plugin.line.sms import SecretText
        req, relay = self._human()
        self._grant(req, 1)
        self.line.svc.loop.call_soon_threadsafe(time.sleep, 0.8)  # the loop is busy past the caller's timeout
        started = time.monotonic()
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(self.CODE), "dtmf",
                                                    approval_generation=1, timeout=0.2))
        self.assertLess(time.monotonic() - started, 0.7)
        time.sleep(1.2)
        self.assertEqual(relay.of_type("sendDigits"), [])  # abandoned: the loop never sent it
        self.assertTrue(sealed.send_secret_to_call(req["call_id"], SecretText(self.CODE), "dtmf",
                                                   approval_generation=1))
        wait_for(lambda: relay.of_type("sendDigits"))

    def test_N1_ticket_commit_and_abandon_are_exclusive(self):
        from plugin.line.engine.sealed import SendTicket
        t = SendTicket()
        self.assertTrue(t.commit())
        self.assertFalse(t.abandon())
        t = SendTicket()
        self.assertTrue(t.abandon())
        self.assertFalse(t.commit())

    def test_N1_sink_refuses_on_the_service_loop(self):
        from plugin.line.engine import sealed
        from plugin.line.sms import SecretText
        req, relay = self._human()
        self._grant(req, 1)

        async def on_loop():
            return sealed.send_secret_to_call(req["call_id"], SecretText(self.CODE), "dtmf", approval_generation=1)

        started = time.monotonic()
        self.assertFalse(self.line.svc.run(on_loop(), timeout=5))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(relay.of_type("sendDigits"), [])

    def test_N1_generation_is_rechecked_on_the_loop(self):
        from plugin.line.engine import sealed
        from plugin.line.sms import SecretText
        req, relay = self._human()
        self._grant(req, 2)
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(self.CODE), "dtmf",
                                                    approval_generation=1))
        refused = [e["data"].get("why") for e in self.line.store.events(req["call_id"])
                   if e["kind"] == "verification_code_refused"]
        self.assertEqual(refused, ["approval_changed"])  # refused by the final check on the loop
        self.line.svc.grants.drop_task(req["task_id"], scope="otp")
        self.assertFalse(sealed.send_secret_to_call(req["call_id"], SecretText(self.CODE), "dtmf",
                                                    approval_generation=2))
        time.sleep(0.3)
        self.assertEqual(relay.of_type("sendDigits"), [])
        self._grant(req, 2)
        self.assertTrue(sealed.send_secret_to_call(req["call_id"], SecretText(self.CODE), "dtmf",
                                                   approval_generation=2))


# ----------------------------------------------------------------------- signature downgrade
class SignatureDowngradeTests(unittest.TestCase):
    def test_unreadable_auth_token_fails_closed(self):
        def secrets(name):
            if name == "HERMES_LINE_TWILIO_AUTH_TOKEN":
                raise RuntimeError("no profile scope")
            return {"HERMES_LINE_TWILIO_ACCOUNT_SID": ACCOUNT}.get(name)
        line = Line(start=False)
        try:
            line.svc.get_secret = secrets
            with self.assertRaises(RuntimeError):
                line.svc.start(port=0)
            self.assertEqual(line.svc.status, "misconfigured")
            self.assertIn("cannot be read", line.svc.detail)
            self.assertIsNone(line.svc.server)
        finally:
            line.store.close()

    def test_ingress_auth_signature_requires_the_token(self):
        line = Line(start=False, secrets=NO_TOKEN, ingress_auth="signature")
        try:
            with self.assertRaises(RuntimeError):
                line.svc.start(port=0)
            self.assertEqual(line.svc.status, "misconfigured")
        finally:
            line.store.close()

    def test_glue_reports_an_unreadable_secret_instead_of_none(self):
        from plugin.line.engine.hermes_glue import _Host
        host = _Host(ctx=None)
        with mock.patch("agent.secret_scope.get_secret", side_effect=RuntimeError("unscoped")):
            with self.assertRaises(RuntimeError):
                host.get_secret("HERMES_LINE_TWILIO_AUTH_TOKEN")
        with mock.patch("agent.secret_scope.get_secret", return_value=None):
            self.assertIsNone(host.get_secret("HERMES_LINE_TWILIO_AUTH_TOKEN"))


class FramesBeforeSetupTests(unittest.TestCase):
    def test_frames_before_setup_are_ignored(self):
        line = Line()
        try:
            req, created = line.dial()
            relay = line.connect(req, created, setup=False)
            relay.prompt("Please hold.")
            wait_for(lambda: "frame_before_setup" in [e["kind"] for e in line.store.events(req["call_id"])])
            self.assertEqual([t for t in line.store.transcript(req["call_id"]) if t["speaker"] == "business"], [])
            relay.send({"type": "setup", "callSid": created["sid"]})
            relay.prompt("Please hold.")
            line.wait_state(req["call_id"], "on_hold")
        finally:
            line.close()


if __name__ == "__main__":
    unittest.main()
