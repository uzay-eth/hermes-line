"""Sealed delivery to a page and to a live call: the model never sees the code."""
import json
import logging
import unittest

from line.sms import ApprovedTask, FixtureSource, MemoryReleasedStore, SecretText, SmsMessage
from line.sms import scrub
from line.policy.consent import ConsentStore
from line.sms.consent import OTP_ACTION
from line.sms.delivery import (
    BrowserOriginProbe,
    BrowserTypeSink,
    CallSink,
    FillStatus,
    OtpDelivery,
    Sink,
)
from line.sms.destination import Destination
from line.sms.service import OtpService, OtpTaskRegistry, ReleaseStatus, binding_for

from .helpers import CALL_DEST, FILL_DEST, FilterOff, MIN, T0

CODE = "739204"
BODY = "Your ACME code is %s. Do not share it." % CODE


class RecordingSink(Sink):
    """Keeps SecretText objects only; tests reveal them on purpose to assert what was typed."""

    def __init__(self, fail_first=0):
        self.calls = []
        self.fail_first = fail_first

    def fill(self, secret, target, *, task_id=None):
        assert isinstance(secret, SecretText)
        self.calls.append((secret, target, task_id))
        if self.fail_first > 0:
            self.fail_first -= 1
            return False
        return True

    def typed(self):
        return [(s.reveal(), t) for s, t, _ in self.calls]


class Clock:
    def __init__(self):
        self.t = (T0 + 2 * MIN) / 1000.0

    def __call__(self):
        return self.t


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class Base(unittest.TestCase):
    def setUp(self):
        scrub.DEFAULT.clear()
        self.addCleanup(scrub.DEFAULT.clear)
        self.clock = Clock()
        self.consent = ConsentStore(clock=self.clock)
        self.source = FixtureSource([SmsMessage("1", "ACME", T0 + MIN, BODY, source="fixture")], clock=self.clock)
        self.service = OtpService(self.source, OtpTaskRegistry(), released=MemoryReleasedStore(), clock=self.clock, sleep=lambda s: None)
        self.origin = "https://example.com"
        task = ApprovedTask("t1", ("ACME",), T0, T0 + 10 * MIN, destination=FILL_DEST)
        self.service.registry.register(task)
        self.token = self.consent.issue(OTP_ACTION, "t1", binding_for(task), 600)
        self.cap = Capture()
        lg = logging.getLogger("line.sms")
        lg.addHandler(self.cap)
        old = lg.level
        lg.setLevel(logging.DEBUG)
        self.addCleanup(lg.removeHandler, self.cap)
        self.addCleanup(lg.setLevel, old)

    def delivery(self, sink=None, **kw):
        self.sink = sink or RecordingSink()
        kw.setdefault("origin_probe", lambda browser_task_id=None: self.origin)
        kw.setdefault("otp_browser_fill", True)
        return OtpDelivery(self.service, self.sink, clock=self.clock, **kw)


class SealedFill(Base):
    def test_fill_types_the_code_and_reports_no_code(self):
        d = self.delivery()
        res = d.fill("t1", self.token, self.consent, ["@e7"], browser_task_id="b1")
        self.assertEqual(FillStatus.FILLED.value, res.status)
        self.assertEqual([(CODE, "@e7")], self.sink.typed())
        self.assertEqual("b1", self.sink.calls[0][2])
        blob = res.to_json() + repr(res)
        self.assertNotIn(CODE, blob)
        self.assertNotIn(CODE, "\n".join(self.cap.lines))
        self.assertFalse(d.held("t1"), "the sealed copy is dropped once typed")

    def test_per_character_boxes(self):
        d = self.delivery()
        res = d.fill("t1", self.token, self.consent, ["@a", "@b", "@c", "@d", "@e", "@f"])
        self.assertEqual(6, res.filled)
        self.assertEqual(list(zip(CODE, ["@a", "@b", "@c", "@d", "@e", "@f"])), self.sink.typed())

    def test_wrong_number_of_boxes_keeps_the_slot_and_needs_no_new_consent(self):
        d = self.delivery()
        res = d.fill("t1", self.token, self.consent, ["@a", "@b"])
        self.assertEqual(FillStatus.TARGET_MISMATCH.value, res.status)
        self.assertEqual([], self.sink.calls)
        self.assertTrue(d.held("t1"))
        # same (now spent) token, no phone read: the sealed slot serves the retry
        fetches = self.source.fetch_calls
        res = d.fill("t1", self.token, self.consent, ["@a"])
        self.assertEqual(FillStatus.FILLED.value, res.status)
        self.assertEqual(fetches, self.source.fetch_calls)
        self.assertEqual([(CODE, "@a")], self.sink.typed())

    def test_sink_failure_allows_a_retry_without_a_new_sms_or_approval(self):
        d = self.delivery(RecordingSink(fail_first=1))
        res = d.fill("t1", self.token, self.consent, ["@wrong"])
        self.assertEqual(FillStatus.SINK_FAILED.value, res.status)
        self.assertTrue(d.held("t1"))
        res = d.fill("t1", None, self.consent, ["@right"])   # no token needed: consent was already spent on this code
        self.assertEqual(FillStatus.FILLED.value, res.status)
        self.assertEqual([CODE, CODE], [c for c, _ in self.sink.typed()])

    def test_slot_expires(self):
        d = self.delivery(RecordingSink(fail_first=1), slot_ttl_s=60)
        d.fill("t1", self.token, self.consent, ["@x"])
        self.clock.t += 61
        res = d.fill("t1", self.token, self.consent, ["@y"])
        # the slot is gone; the message is already used and the token spent: nothing more to release
        self.assertFalse(res.ok)
        self.assertEqual([(CODE, "@x")], self.sink.typed())

    def test_slot_has_a_fill_budget(self):
        d = self.delivery(RecordingSink(fail_first=99), slot_max_fills=2)
        statuses = [d.fill("t1", self.token, self.consent, ["@x"]).status for _ in range(4)]
        self.assertEqual(
            [FillStatus.SINK_FAILED.value, FillStatus.SINK_FAILED.value, ReleaseStatus.ALREADY_USED.value,
             ReleaseStatus.ALREADY_USED.value],   # budget spent: that message can never be released again
            statuses,
        )
        self.assertEqual(2, len(self.sink.calls))

    def test_no_consent_no_fill(self):
        d = self.delivery()
        for token in (None, "", "forged"):
            res = d.fill("t1", token, self.consent, ["@e7"])
            self.assertEqual(ReleaseStatus.NO_CONSENT.value, res.status)
        self.assertEqual([], self.sink.calls)

    def test_non_release_statuses_never_touch_the_sink(self):
        src = FixtureSource([SmsMessage("2", "EVIL", T0 + MIN, "code %s" % CODE, source="fixture")], clock=self.clock)
        self.service.source = src
        d = self.delivery()
        res = d.fill("t1", self.token, self.consent, ["@e7"])
        self.assertEqual(ReleaseStatus.NO_MESSAGE.value, res.status)
        self.assertEqual([], self.sink.calls)
        self.assertTrue(res.hint)

    def test_bad_requests(self):
        d = self.delivery()
        for task, targets in (("", ["@a"]), ("t1", []), ("t1", [""]), ("t1", ["@%d" % i for i in range(13)]), ("t1", None)):
            self.assertEqual(FillStatus.BAD_REQUEST.value, d.fill(task, self.token, self.consent, targets).status)
        self.assertEqual([], self.sink.calls)

    def test_no_sink_configured_is_disabled(self):
        d = OtpDelivery(self.service, None, origin_probe=lambda t=None: self.origin, otp_browser_fill=True, clock=self.clock)
        self.assertEqual(FillStatus.DISABLED.value, d.fill("t1", self.token, self.consent, ["@a"]).status)

    def test_released_code_is_scrubbed_from_everything_afterwards(self):
        d = self.delivery()
        d.fill("t1", self.token, self.consent, ["@e7"])
        for text in ("snapshot: textbox value %s" % CODE, "7 3 9 2 0 4", "739-204", "７３９２０４"):
            self.assertNotIn("739", scrub.scrub_text(text).replace("[CODE]", ""), text)


class BrowserSink(unittest.TestCase):
    def test_dispatch_shape_and_echo_is_dropped(self):
        calls = []

        def dispatch(name, args, **kw):
            calls.append((name, dict(args), kw))
            return json.dumps({"success": True, "typed": args["text"], "element": args["ref"]})   # core echoes the text

        sink = BrowserTypeSink(dispatch)
        self.assertTrue(sink.fill(SecretText(CODE), "@e7", task_id="b1"))
        self.assertEqual([("browser_type", {"ref": "@e7", "text": CODE}, {"task_id": "b1"})], calls)
        self.assertNotIn(CODE, repr(sink))

    def test_failures(self):
        self.assertFalse(BrowserTypeSink(lambda *a, **k: json.dumps({"success": False})).fill(SecretText(CODE), "@e7"))
        self.assertFalse(BrowserTypeSink(lambda *a, **k: "not json").fill(SecretText(CODE), "@e7"))

        def boom(*a, **k):
            raise RuntimeError("failed typing %s" % CODE)   # exception text may carry the argument

        cap = Capture()
        lg = logging.getLogger("line.sms")
        lg.addHandler(cap)
        self.addCleanup(lg.removeHandler, cap)
        with FilterOff():   # prove the code path itself is clean, not just the defence-in-depth filter
            self.assertFalse(BrowserTypeSink(boom).fill(SecretText(CODE), "@e7"))
        self.assertTrue(cap.lines)
        self.assertNotIn(CODE, "\n".join(cap.lines))


class FillDestination(Base):
    """The owner approved a site: the open page must be that site, checked before anything is released."""

    def test_wrong_site_releases_nothing_and_keeps_consent(self):
        d = self.delivery()
        self.origin = "https://evil.example.net"
        fetches = self.source.fetch_calls
        res = d.fill("t1", self.token, self.consent, ["@e7"])
        self.assertEqual(FillStatus.ORIGIN_MISMATCH.value, res.status)
        self.assertEqual([], self.sink.calls)
        self.assertEqual(fetches, self.source.fetch_calls, "the phone is not even read")
        self.assertFalse(d.held("t1"))
        self.origin = "https://example.com"           # back on the approved site: the same token still works
        self.assertEqual(FillStatus.FILLED.value, d.fill("t1", self.token, self.consent, ["@e7"]).status)

    def test_look_alike_origins_do_not_match(self):
        d = self.delivery()
        for origin in ("https://example.com.evil.net", "https://exampIe.com", "http://example.com", "https://example.com:8443",
                       "https://sub.example.com"):
            self.origin = origin
            self.assertEqual(FillStatus.ORIGIN_MISMATCH.value, d.fill("t1", self.token, self.consent, ["@e7"]).status, origin)
        self.assertEqual([], self.sink.calls)

    def test_unreadable_origin_fails_closed(self):
        d = self.delivery(origin_probe=lambda t=None: None)
        self.assertEqual(FillStatus.ORIGIN_UNKNOWN.value, d.fill("t1", self.token, self.consent, ["@e7"]).status)
        d = OtpDelivery(self.service, RecordingSink(), otp_browser_fill=True, clock=self.clock)       # no probe at all
        self.assertEqual(FillStatus.ORIGIN_UNKNOWN.value, d.fill("t1", self.token, self.consent, ["@e7"]).status)

    def test_origin_is_rechecked_on_a_retry_from_the_sealed_slot(self):
        d = self.delivery(RecordingSink(fail_first=1))
        self.assertEqual(FillStatus.SINK_FAILED.value, d.fill("t1", self.token, self.consent, ["@x"]).status)
        self.assertTrue(d.held("t1"))
        self.origin = "https://evil.example.net"            # the page navigated away between attempts
        self.assertEqual(FillStatus.ORIGIN_MISMATCH.value, d.fill("t1", None, self.consent, ["@y"]).status)
        self.assertEqual(1, len(self.sink.calls))

    def test_a_task_approved_for_a_call_cannot_fill(self):
        task = ApprovedTask("tc", ("ACME",), T0, T0 + 10 * MIN, destination=CALL_DEST)
        self.service.registry.register(task)
        tok = self.consent.issue(OTP_ACTION, "tc", binding_for(task), 600)
        d = self.delivery()
        self.assertEqual(FillStatus.WRONG_DESTINATION.value, d.fill("tc", tok, self.consent, ["@e7"]).status)
        self.assertEqual([], self.sink.calls)

    def test_token_issued_for_another_destination_is_rejected(self):
        """Same sender and window, but the owner approved a different site: the binding differs."""
        other = ApprovedTask("t1", ("ACME",), T0, T0 + 10 * MIN, destination=Destination.fill("https://bank.example.org"))
        tok = self.consent.issue(OTP_ACTION, "t1", binding_for(other), 600)
        d = self.delivery()
        res = d.fill("t1", tok, self.consent, ["@e7"])
        self.assertEqual(ReleaseStatus.NO_CONSENT.value, res.status)
        self.assertEqual([], self.sink.calls)

    def test_probe_parses_browser_console_and_normalises(self):
        calls = []

        def dispatch(name, args, **kw):
            calls.append((name, args, kw))
            return json.dumps({"success": True, "result": "HTTPS://Example.COM:443", "result_type": "str"})

        probe = BrowserOriginProbe(dispatch)
        self.assertEqual("https://example.com", probe("b1"))
        self.assertEqual([("browser_console", {"expression": "window.location.origin"}, {"task_id": "b1"})], calls)
        for raw in ('{"success": false}', "nope", json.dumps({"success": True, "result": "about:blank"}),
                    json.dumps({"success": True, "result": "null"}), json.dumps({"success": True, "result": "javascript:alert(1)"})):
            self.assertIsNone(BrowserOriginProbe(lambda *a, **k: raw)(), raw)
        self.assertIsNone(BrowserOriginProbe(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))())


class CallRecorder:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, call_id, secret, mode, approval_generation=None):
        assert isinstance(secret, SecretText), "the sink must get a SecretText, not a str"
        self.calls.append((call_id, secret, mode))
        return self.ok


class ToCall(Base):
    def setUp(self):
        super().setUp()
        self.ctask = ApprovedTask("tc", ("ACME",), T0, T0 + 10 * MIN, destination=CALL_DEST)
        self.service.registry.register(self.ctask)
        self.ctoken = self.consent.issue(OTP_ACTION, "tc", binding_for(self.ctask), 600)
        self.rec = CallRecorder()

    def call_delivery(self, **kw):
        return self.delivery(call_sink=CallSink(self.rec), **kw)

    def test_dtmf_and_speak_hand_a_secret_to_the_call(self):
        for mode in ("dtmf", "speak"):
            self.setUp()
            d = self.call_delivery()
            res = d.to_call("tc", "call-1", mode, self.ctoken, self.consent)
            self.assertEqual(FillStatus.SENT.value, res.status)
            (call_id, secret, m), = self.rec.calls
            self.assertEqual(("call-1", mode, CODE), (call_id, m, secret.reveal()))
            self.assertNotIn(CODE, res.to_json() + repr(res) + "\n".join(self.cap.lines))
            self.assertFalse(d.held("tc"))

    def test_other_call_id_or_task_kind_is_refused_before_release(self):
        d = self.call_delivery()
        fetches = self.source.fetch_calls
        self.assertEqual(FillStatus.WRONG_DESTINATION.value, d.to_call("tc", "call-2", "dtmf", self.ctoken, self.consent).status)
        self.assertEqual(FillStatus.WRONG_DESTINATION.value, d.to_call("t1", "call-1", "dtmf", self.token, self.consent).status)
        self.assertEqual(fetches, self.source.fetch_calls)
        self.assertEqual([], self.rec.calls)
        self.assertEqual(FillStatus.SENT.value, d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent).status, "consent untouched")

    def test_live_counterpart_must_be_the_approved_party(self):
        live = {"call-1": "+15550100111"}
        d = self.call_delivery(counterpart_of=lambda cid: live.get(cid))
        res = d.to_call("tc", "call-1", "speak", self.ctoken, self.consent)
        self.assertEqual(FillStatus.WRONG_DESTINATION.value, res.status)
        self.assertEqual([], self.rec.calls)
        live["call-1"] = "+15550100999"
        self.assertEqual(FillStatus.SENT.value, d.to_call("tc", "call-1", "speak", self.ctoken, self.consent).status)
        d2 = self.call_delivery(counterpart_of=lambda cid: (_ for _ in ()).throw(RuntimeError("engine down")))
        self.assertEqual(FillStatus.WRONG_DESTINATION.value, d2.to_call("tc", "call-1", "speak", self.ctoken, self.consent).status)

    def test_token_for_another_call_is_rejected(self):
        other = ApprovedTask("tc", ("ACME",), T0, T0 + 10 * MIN, destination=Destination.call("call-9", "+15550100999"))
        tok = self.consent.issue(OTP_ACTION, "tc", binding_for(other), 600)
        res = self.call_delivery().to_call("tc", "call-1", "dtmf", tok, self.consent)
        self.assertEqual(ReleaseStatus.NO_CONSENT.value, res.status)
        self.assertEqual([], self.rec.calls)

    def test_call_not_ready_keeps_the_sealed_code_for_a_retry(self):
        self.rec.ok = False
        d = self.call_delivery()
        self.assertEqual(FillStatus.SINK_FAILED.value, d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent).status)
        self.assertTrue(d.held("tc"))
        self.rec.ok = True
        self.assertEqual(FillStatus.SENT.value, d.to_call("tc", "call-1", "dtmf", None, self.consent).status)
        self.assertEqual(2, len(self.rec.calls))

    def test_dtmf_needs_digits_and_keeps_the_slot(self):
        self.source.add(SmsMessage("9", "ACME", T0 + 2 * MIN, "Your ACME code is AB12CD.", source="fixture"))
        d = self.call_delivery()
        res = d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent)
        self.assertEqual(FillStatus.MODE_UNSUPPORTED.value, res.status)
        self.assertEqual([], self.rec.calls)
        self.assertEqual(FillStatus.SENT.value, d.to_call("tc", "call-1", "speak", None, self.consent).status)
        self.assertEqual("AB12CD", self.rec.calls[0][1].reveal())

    def test_bad_requests(self):
        d = self.call_delivery()
        for args in (("tc", "call-1", "email"), ("tc", 5, "dtmf"), ("", "call-1", "dtmf"), (None, "call-1", "speak")):
            self.assertEqual(FillStatus.BAD_REQUEST.value, d.to_call(*args, self.ctoken, self.consent).status, args)
        self.assertEqual([], self.rec.calls)

    def test_a_call_sink_that_raises_is_a_failed_delivery_and_logs_no_code(self):
        def boom(call_id, secret, mode, approval_generation=None):
            raise RuntimeError("engine failed sending %s" % secret.reveal())

        d = self.delivery(call_sink=CallSink(boom))
        with FilterOff():
            res = d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent)
        self.assertEqual(FillStatus.SINK_FAILED.value, res.status)
        self.assertNotIn(CODE, "\n".join(self.cap.lines) + res.to_json())

    def test_engine_stub_matches_the_contract_and_sends_nothing(self):
        import inspect

        from line.sms import engine_stub

        sig = inspect.signature(engine_stub.send_secret_to_call)
        self.assertEqual(["call_id", "secret", "mode", "approval_generation"], list(sig.parameters))
        self.assertIsNone(sig.parameters["approval_generation"].default)
        self.assertFalse(engine_stub.send_secret_to_call("call-1", SecretText(CODE), "dtmf"))
        d = self.delivery()                    # default CallSink resolves the stub until L1 merges line.engine.sealed
        res = d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent)
        self.assertEqual(FillStatus.SINK_FAILED.value, res.status)
        self.assertTrue(d.held("tc"))


class Destinations(unittest.TestCase):
    def test_origin_normalisation_and_rules(self):
        self.assertEqual("https://example.com", Destination.fill("HTTPS://Example.com:443/login?x=1").origin)
        self.assertEqual("https://example.com:8443", Destination.fill("https://example.com:8443/").origin)
        self.assertEqual("http://localhost:3000", Destination.fill("http://localhost:3000/a").origin)
        for bad in ("example.com", "http://example.com", "ftp://example.com", "https://", "https://u:p@example.com",
                    "javascript:alert(1)", "", None, "https://example.com:99999"):
            with self.assertRaises(ValueError, msg=bad):
                Destination.fill(bad)

    def test_call_destination_rules(self):
        d = Destination.call("call-1", "(555) 010-0999")
        self.assertEqual("+15550100999", d.counterpart)
        for cid, num in (("", "+15550100999"), ("a b", "+15550100999"), ("x" * 65, "+15550100999"), ("c1", "32665"), ("c1", ""), ("c1", "ACME")):
            with self.assertRaises(ValueError, msg=(cid, num)):
                Destination.call(cid, num)

    def test_prompt_and_log_text(self):
        self.assertEqual("Type the code into the page at https://example.com", FILL_DEST.prompt_text())
        self.assertIn("call-1", CALL_DEST.prompt_text())
        self.assertIn("+15550100999", CALL_DEST.prompt_text())
        self.assertNotIn("+15550100999", CALL_DEST.log_text())     # logs carry the masked number

    def test_direct_construction_cannot_bypass_normalisation(self):
        with self.assertRaises(ValueError):
            Destination("fill", origin="https://EXAMPLE.com/")
        with self.assertRaises(ValueError):
            Destination("call", call_id="c1", counterpart="555")
        with self.assertRaises(ValueError):
            Destination("email")


if __name__ == "__main__":
    unittest.main()
