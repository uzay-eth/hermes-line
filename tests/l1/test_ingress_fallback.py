"""Ingress without the Twilio Auth Token: path secret + one-time stream tokens + REST check.

Every rejection shape asserts zero side effects (no provider call, no call record, no relay
token, no session) and a masked log entry.
"""
import json
import os
import stat
import tempfile
import unittest
import urllib.request
from pathlib import Path

from websockets.exceptions import InvalidStatus

from harness import ACCOUNT, BUSINESS, LINE, OWNER, Line, Relay, wait_for
from plugin.line.server.ingress_auth import load_or_create_path_secret, path_secret_ok

NO_TOKEN = {"HERMES_LINE_TWILIO_ACCOUNT_SID": ACCOUNT}  # API key only; no Auth Token


def effects(line):
    tokens = line.store._conn.execute("SELECT COUNT(*) FROM relay_tokens").fetchone()[0]
    return (line.adapter.total_calls, len(line.store.list_calls()), tokens, len(line.svc.sessions))


def rejections(line):
    return [e for e in line.store.events() if e["kind"] == "ingress_rejected"]


class PathSecretTests(unittest.TestCase):
    def test_secret_is_256_bits_private_and_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ingress-path.secret"
            first = load_or_create_path_secret(path)
            self.assertGreaterEqual(len(first), 43)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(load_or_create_path_secret(path), first)
        self.assertTrue(path_secret_ok("abc", "abc"))
        for bad in ("abd", "", "abc" * 100, None):
            self.assertFalse(path_secret_ok("abc", bad))
        self.assertFalse(path_secret_ok("", ""))


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.line = Line(secrets=NO_TOKEN)
        self.assertEqual(self.line.svc.auth.mode, "path_secret")

    def tearDown(self):
        self.line.close()

    def _answer(self, req, created):
        return self.line.post(f"/twilio/voice/answer/{req['call_id']}", {"CallSid": created["sid"]}, sign=False)

    def test_full_call_without_signatures(self):
        req, created = self.line.dial()
        self.assertIn(self.line.svc.auth.prefix(), created["answer_url"])  # Twilio is given secret URLs
        status, body = self._answer(req, created)
        self.assertEqual(status, 200)
        self.assertIn("<ConversationRelay", body)
        self.assertIn(f"wss://line.test{self.line.svc.auth.prefix()}/twilio/relay/", body)
        relay = self.line.relay(body, sign=False)
        relay.send({"type": "setup", "callSid": created["sid"]})
        relay.prompt("Thanks for calling, this is Mike.")
        relay.wait_text("live person?")
        fetches = len(self.line.adapter.fetches)
        self.line.post(f"/twilio/voice/status/{req['call_id']}", {"CallSid": created["sid"],
                                                                  "CallStatus": "in-progress"}, sign=False)
        self.assertEqual(len(self.line.adapter.fetches), fetches)  # leg verification is cached
        self.line.adapter.records[created["sid"]]["status"] = "completed"
        self.line.post(f"/twilio/voice/status/{req['call_id']}", {"CallSid": created["sid"],
                                                                  "CallStatus": "completed"}, sign=False)
        self.line.wait_state(req["call_id"], "failed")  # hung up during detection, confirmed by REST
        self.assertEqual(rejections(self.line), [])

    def test_wrong_or_missing_path_secret_is_a_bare_404(self):
        req, created = self.line.dial()
        before = effects(self.line)
        for path in (f"/t/wrong/twilio/voice/answer/{req['call_id']}", f"/twilio/voice/answer/{req['call_id']}",
                     f"/t/{'x' * 200}/twilio/voice/inbound", "/t//twilio/voice/inbound"):
            r = urllib.request.Request(self.line.base + path, data=b"CallSid=" + created["sid"].encode(),
                                       headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(r, timeout=5)
            self.assertEqual(ctx.exception.code, 404, path)
        self.assertEqual(effects(self.line), before)
        self.assertNotIn(self.line.svc.auth.path_secret, json.dumps(self.line.store.events()))

    def test_relay_socket_needs_the_secret_and_a_one_time_token(self):
        req, created = self.line.dial()
        status, body = self._answer(req, created)
        good = Relay(self.line, self._ws(body), sign=False)
        with self.assertRaises(InvalidStatus):
            Relay(self.line, self._ws(body), sign=False)  # the token was single-use
        wrong_secret = self._ws(body).replace(self.line.svc.auth.path_secret, "wrong-secret")
        with self.assertRaises(InvalidStatus):
            Relay(self.line, wrong_secret, sign=False)
        good.close()
        kinds = [e["data"]["reason"] for e in rejections(self.line)]
        self.assertIn("relay_token", kinds)
        self.assertIn("path_secret", kinds)

    @staticmethod
    def _ws(body):
        import re
        return re.search(r'url="([^"]+)"', body).group(1).replace("&amp;", "&")

    def _answer_rejected(self, mutate, reason):
        req, created = self.line.dial()
        mutate(self.line.adapter, created["sid"])
        before = effects(self.line)
        status, body = self._answer(req, created)
        self.assertEqual(status, 200)
        self.assertIn("<Hangup/>", body)
        self.assertNotIn("ConversationRelay", body)
        self.assertEqual(effects(self.line), before)  # no relay token issued, nothing dialled
        events = rejections(self.line)
        self.assertEqual(events[-1]["data"]["reason"], reason)
        self.assertNotIn(created["sid"], json.dumps(events))  # SIDs are masked in the log

    def test_answer_rejected_when_rest_does_not_know_the_call(self):
        self._answer_rejected(lambda a, sid: a.records.pop(sid), "rest_unknown_or_unreachable")

    def test_answer_rejected_for_another_account(self):
        self._answer_rejected(lambda a, sid: a.records[sid].update(account_sid="AC" + "9" * 32), "rest_other_account")

    def test_answer_rejected_when_numbers_differ(self):
        self._answer_rejected(lambda a, sid: a.records[sid].update(to="+17755550123"), "rest_numbers_mismatch")

    def test_answer_rejected_when_direction_differs(self):
        self._answer_rejected(lambda a, sid: a.records[sid].update(direction="inbound"), "rest_direction_mismatch")

    def test_answer_rejected_when_twilio_is_unreachable(self):
        self._answer_rejected(lambda a, sid: setattr(a, "rest_down", True), "rest_unknown_or_unreachable")

    def test_forged_terminal_status_is_ignored_until_rest_confirms_it(self):
        req, created = self.line.dial()
        self._answer(req, created)
        before = effects(self.line)
        for forged in ("completed", "busy", "no-answer", "failed", "canceled"):
            self.line.post(f"/twilio/voice/status/{req['call_id']}", {"CallSid": created["sid"],
                                                                      "CallStatus": forged}, sign=False)
        self.assertEqual(self.line.state(req["call_id"]), "dialing")
        self.assertEqual(effects(self.line), before)
        self.assertEqual({e["data"]["reason"] for e in rejections(self.line)}, {"status_unconfirmed"})

    def test_inbound_rejections(self):
        cases = [
            ("CA" + "1" * 32, None, "rest_unknown_or_unreachable"),
            ("CA" + "2" * 32, {"from_": "+17755550177", "to": LINE}, "rest_numbers_mismatch"),
            ("CA" + "3" * 32, {"from_": BUSINESS, "to": "+17755550111"}, "rest_numbers_mismatch"),
            ("CA" + "4" * 32, {"from_": BUSINESS, "to": LINE, "account": "AC" + "8" * 32}, "rest_other_account"),
        ]
        before = effects(self.line)
        for sid, record, reason in cases:
            if record:
                self.line.adapter.add_inbound(sid, **record)
            status, body = self.line.post("/twilio/voice/inbound", {"CallSid": sid, "From": BUSINESS, "To": LINE},
                                          sign=False)
            self.assertIn("<Reject", body, reason)
            self.assertEqual(rejections(self.line)[-1]["data"]["reason"], reason)
        self.assertEqual(effects(self.line), before)  # no call record, no session, no Hermes turn
        self.assertEqual(self.line.drivers, [])

    def test_verified_inbound_is_answered(self):
        sid = "CA" + "5" * 32
        self.line.adapter.add_inbound(sid, from_=BUSINESS, to=LINE)
        status, body = self.line.post("/twilio/voice/inbound", {"CallSid": sid, "From": BUSINESS, "To": LINE},
                                      sign=False)
        self.assertIn("<ConversationRelay", body)

    def _ringing(self):
        req, created = self.line.dial(mode="owner")
        status, body = self._answer(req, created)
        relay = self.line.relay(body, sign=False)
        relay.send({"type": "setup", "callSid": created["sid"]})
        relay.prompt("Hello, this is Mike.")
        relay.wait_text("live person?")
        relay.prompt("Yes, I am a real person")
        self.line.wait_state(req["call_id"], "ringing_owner")
        owner = self.line.adapter.created[-1]
        self.assertEqual(owner["to"], OWNER)
        return req, created, owner

    def test_forged_owner_accept_before_the_owner_answers(self):
        req, created, owner = self._ringing()
        self.line.adapter.records[owner["sid"]]["status"] = "ringing"
        before = effects(self.line)
        status, body = self.line.post(f"/twilio/voice/owner/accept/{req['call_id']}",
                                      {"CallSid": owner["sid"], "Digits": "1"}, sign=False)
        self.assertNotIn("<Conference", body)
        self.assertEqual(self.line.state(req["call_id"]), "ringing_owner")
        self.assertEqual(effects(self.line), before)
        self.assertEqual(rejections(self.line)[-1]["data"]["reason"], "owner_leg_not_answered")

    def test_owner_leg_must_match_rest(self):
        req, created, owner = self._ringing()
        self.line.adapter.records[owner["sid"]]["to"] = "+17755550123"  # not the owner's phone
        status, body = self.line.post(f"/twilio/voice/owner/answer/{req['call_id']}", {"CallSid": owner["sid"]},
                                      sign=False)
        self.assertNotIn("<Gather", body)
        self.assertEqual(rejections(self.line)[-1]["data"]["reason"], "rest_numbers_mismatch")

    def test_real_bridge_then_forged_conference_end_is_ignored(self):
        req, created, owner = self._ringing()
        self.line.post(f"/twilio/voice/owner/answer/{req['call_id']}", {"CallSid": owner["sid"]}, sign=False)
        status, joined = self.line.post(f"/twilio/voice/owner/accept/{req['call_id']}",
                                        {"CallSid": owner["sid"], "Digits": "1"}, sign=False)
        self.assertIn("<Conference", joined)
        self.line.wait_state(req["call_id"], "owner_connected")
        conf = self.line.session(req["call_id"]).conference
        before = effects(self.line)
        self.line.post(f"/twilio/voice/conference/status/{req['call_id']}",
                       {"FriendlyName": conf, "StatusCallbackEvent": "conference-end"}, sign=False)
        self.assertEqual(self.line.state(req["call_id"]), "owner_connected")
        self.assertEqual(effects(self.line), before)
        self.assertEqual(rejections(self.line)[-1]["data"]["reason"], "conference_end_unconfirmed")
        self.line.adapter.records[owner["sid"]]["status"] = "completed"
        self.line.post(f"/twilio/voice/conference/status/{req['call_id']}",
                       {"FriendlyName": conf, "StatusCallbackEvent": "conference-end"}, sign=False)
        self.line.wait_state(req["call_id"], "ended")


class ModeSelectionTests(unittest.TestCase):
    def test_signature_mode_when_the_auth_token_exists(self):
        line = Line()
        try:
            self.assertEqual(line.svc.auth.mode, "signature")
            req, created = line.dial()
            status, _ = line.post(f"/twilio/voice/answer/{req['call_id']}", {"CallSid": created["sid"]}, sign=False)
            self.assertEqual(status, 403)  # with a token, an unsigned request is never enough
            self.assertEqual(line.adapter.fetches, [])  # and no REST lookups are needed
            self.assertIn("ingress auth: signature", line.svc.detail)
        finally:
            line.close()


if __name__ == "__main__":
    unittest.main()
