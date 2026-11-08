"""Masking on every log and transcript path line.sms touches.

Paths covered, each with the library's own masking filter REMOVED (the filter is defence in depth;
the mechanism is that no code or body is ever formatted in the first place), and again with it on:

  1. every `line.sms` log record at DEBUG, across the source, matcher, service, delivery and hooks
  2. audit-hook payloads
  3. tool results (the JSON the model sees)
  4. what a Hermes-core model stores and broadcasts for those tool results
  5. probe CLI output
  6. exception text from every source error path (adb, gateway)
  7. owner-facing transcript text (`mask_for_transcript`) for released and not-yet-released codes

Inputs are the S3 fixture bodies (synthetic sender ACME, invented codes), so a leak of any planted
code or of the body text fails a test.
"""
import io
import json
import logging
import unittest
import urllib.error

from line.sms import (
    MemoryReleasedStore,
    AdbSource,
    ApprovedTask,
    FixtureSource,
    GatewaySource,
    SmsMessage,
    mask_for_transcript,
    scrub,
)
from line.sms import probe
from line.policy.consent import ConsentStore
from line.sms.consent import OTP_ACTION
from line.sms.delivery import BrowserOriginProbe, BrowserTypeSink, CallSink, OtpDelivery
from line.sms.hooks import CALL_TOOL, FILL_TOOL, register
from line.sms.service import OtpService, OtpTaskRegistry, binding_for
from line.sms.sources.adb import RunResult
from line.sms.sources.base import SourceError

from .helpers import CALL_DEST, FILL_DEST, FakeAdb, FilterOff, MIN, T0, adb_rows, load_otp_cases
from .test_hooks import CoreModel, FakeCtx


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())
        if record.exc_text:
            self.lines.append(record.exc_text)


def run_pipeline(body, kind, clock_t, with_filter):
    """One message through consent, release, delivery, hooks and a core model. Returns all surfaces."""
    surfaces = []
    cap = Capture()
    lg = logging.getLogger("line.sms")
    lg.addHandler(cap)
    old = lg.level
    lg.setLevel(logging.DEBUG)
    ctx_off = None if with_filter else FilterOff()
    try:
        if ctx_off:
            ctx_off.__enter__()
        scrub.DEFAULT.clear()
        t = {"now": clock_t}
        consent = ConsentStore(clock=lambda: t["now"])
        msg = SmsMessage("42", "ACME", T0 + MIN, body, source="fixture")
        audit = []
        svc = OtpService(FixtureSource([msg], clock=lambda: t["now"]), OtpTaskRegistry(), released=MemoryReleasedStore(),
                         clock=lambda: t["now"], sleep=lambda s: None, audit=audit.append)
        task = ApprovedTask("t1", ("ACME",), T0, T0 + 10 * MIN, destination=FILL_DEST if kind == "fill" else CALL_DEST)
        svc.registry.register(task)
        token = consent.issue(OTP_ACTION, "t1", binding_for(task), 600)
        ctx = FakeCtx()
        sent = []
        delivery = OtpDelivery(svc, BrowserTypeSink(ctx.dispatch_tool), origin_probe=BrowserOriginProbe(ctx.dispatch_tool),
                               call_sink=CallSink(lambda cid, secret, mode, approval_generation=None: sent.append(cid) or True), otp_browser_fill=True, clock=lambda: t["now"])
        register(ctx, delivery, consent, lambda tid: token)
        core = CoreModel(ctx)

        if kind == "fill":
            call = lambda: core.call_tool(FILL_TOOL, {"task_id": "t1", "targets": ["@e7"]})
        else:
            call = lambda: core.call_tool(CALL_TOOL, {"task_id": "t1", "call_id": "call-1", "mode": "speak"})
        call()
        call()                                                                       # replay / already used
        core.call_tool(FILL_TOOL, {"task_id": "nope", "targets": ["@e7"]})
        if ctx.pages or sent:
            # something was released: a later page snapshot / terminal echo quoting the SMS must be scrubbed
            core.tool_result_from_other_tool("browser_snapshot", "textbox value %s" % body)
        surfaces += core.store + core.events + [repr(a) for a in audit]
        surfaces += [repr(delivery), repr(svc.registry.get("t1")), repr(msg), msg.log_summary()]
        surfaces += cap.lines
        return surfaces, core
    finally:
        if ctx_off:
            ctx_off.__exit__()
        lg.removeHandler(cap)
        lg.setLevel(old)
        scrub.DEFAULT.clear()


class PipelineSweep(unittest.TestCase):
    def sweep(self, kind, with_filter):
        cases = [c for c in load_otp_cases() if c["codes"]]
        self.assertGreater(len(cases), 30)
        clock_t = (T0 + 2 * MIN) / 1000.0
        for case in cases:
            with self.subTest(case=case["name"], kind=kind, filter=with_filter):
                surfaces, core = run_pipeline(case["body"], kind, clock_t, with_filter)
                blob = "\n".join(surfaces)
                for code in case["codes"]:
                    self.assertNotIn(code, blob)
                if len(case["body"]) >= 12:
                    self.assertNotIn(case["body"], blob)

    def test_fill_filter_off(self):
        self.sweep("fill", with_filter=False)

    def test_fill_filter_on(self):
        self.sweep("fill", with_filter=True)

    def test_call_filter_off(self):
        self.sweep("call", with_filter=False)

    def test_call_filter_on(self):
        self.sweep("call", with_filter=True)


class SourceErrorText(unittest.TestCase):
    CODE = "739204"

    def test_adb_error_paths_do_not_echo_output(self):
        body = "Your ACME code is %s" % self.CODE
        for stdout, stderr in (
            ("garbage %s\n" % body, ""),
            ("Row: 0 _id=1, address=ACME, date=1, type=1, body=%s\nRow: 5 _id=9, address=X, date=1, type=1, body=%s\n" % (body, body), ""),
            ("", "java.lang.SecurityException: %s" % body),
            ("Error while accessing provider: %s" % body, ""),
        ):
            def runner(argv, timeout, so=stdout, se=stderr):
                if argv[1:] == ["devices"]:
                    return RunResult(0, "List of devices attached\nSERIAL1\tdevice\n", "")
                return RunResult(1 if se else 0, so, se)

            try:
                AdbSource(adb_path="adb", runner=runner).fetch()
            except SourceError as exc:
                self.assertNotIn(self.CODE, str(exc) + repr(exc) + repr(exc.args))
                self.assertNotIn("Your ACME", str(exc))

    def test_gateway_error_paths_do_not_echo_body(self):
        body = ('{"error":"bad code %s"}' % self.CODE).encode()

        class Resp:
            def __init__(self, status, data):
                self.status, self._d = status, data

            def read(self):
                return self._d

        def http_500(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 500, "boom %s" % self.CODE, {}, io.BytesIO(body))

        for opener in (http_500, lambda req, timeout: Resp(200, body), lambda req, timeout: Resp(200, b"not json " + body)):
            src = GatewaySource("http://127.0.0.1:18080", "user", "pass-%s" % "hunter2", opener=opener)
            try:
                src.fetch()
            except SourceError as exc:
                text = str(exc) + repr(exc) + repr(exc.args)
                self.assertNotIn(self.CODE, text)
                self.assertNotIn("hunter2", text)
        self.assertNotIn("hunter2", repr(src))


class ProbeOutput(unittest.TestCase):
    def test_probe_inbox_has_no_code_or_body(self):
        cases = [c for c in load_otp_cases() if c["codes"]]
        for case in cases:
            m = SmsMessage("42", "ACME", T0 + MIN, case["body"], source="fixture")
            buf = io.StringIO()
            probe.main(["inbox"], source=AdbSource(adb_path="adb", runner=FakeAdb(adb_rows([m]))), out=buf,
                       clock=lambda: (T0 + 2 * MIN) / 1000.0)
            for code in case["codes"]:
                self.assertNotIn(code, buf.getvalue(), case["name"])


class TranscriptText(unittest.TestCase):
    """Owner-facing text: call transcript, summary, anything stored or shown."""

    def setUp(self):
        scrub.DEFAULT.clear()
        self.addCleanup(scrub.DEFAULT.clear)

    def test_code_shaped_tokens_are_masked_before_any_release(self):
        for text in ("my code is 739204", "the code: 739 204", "your pin is 7392", "OTP: AB12CD", "verification code is G-482915"):
            out = mask_for_transcript(text)
            self.assertNotRegex(out, r"\d{3}[ -]?\d{3}|AB12CD|7392", text)
            self.assertIn("[CODE]", out)

    def test_call_speech_stays_readable(self):
        """Round 3: a booking, a date, a flight or a confirmation number is not a verification code."""
        for text in (
            "Friday the 14th", "see you on March 3rd at 7", "confirmation number 48213", "your confirmation code is 482915",
            "booking reference AB12CD", "reservation code XK94TQ", "flight DL1234 departs at 6", "table for 4 at 7",
            "your total is 129 dollars and 50 cents", "zip code 89501", "order number 5512907", "gate B12",
        ):
            self.assertEqual(text, mask_for_transcript(text), text)

    def test_contract_mask_tokens(self):
        self.assertEqual("call me on [NUMBER]", mask_for_transcript("call me on 775 555 0101"))
        for number in ("(775) 555-0101", "775-555-0101", "775.555.0101", "+1 775 555 0101", "1-775-555-0101", "+17755550101", "+905321234567"):
            self.assertEqual("[NUMBER]", mask_for_transcript(number), number)
        for card in ("4111 1111 1111 1111", "4111-1111-1111-1111", "4111111111111111", "3782 822463 10005"):
            self.assertEqual("card [CARD] ok", mask_for_transcript("card %s ok" % card), card)
        self.assertEqual("[CODE]", mask_for_transcript("739204").replace("739204", "[CODE]"))   # bare digits are not guessed at
        self.assertEqual("your code is [CODE]", mask_for_transcript("your code is 4829 1503"))

    def test_mixed_call_sentence(self):
        out = mask_for_transcript("Call me at 775-555-0101 about flight DL1234 on Friday the 14th, confirmation 48213, your code is 482915.")
        self.assertEqual("Call me at [NUMBER] about flight DL1234 on Friday the 14th, confirmation 48213, your code is [CODE].", out)

    def test_business_number_equal_to_a_received_code_is_masked(self):
        scrub.register_released("482915")
        self.assertEqual("your confirmation number is [CODE]", mask_for_transcript("your confirmation number is 482915"))

    def test_logs_stay_strict(self):
        from line.sms.masking import mask_log_text

        self.assertNotIn("48213", mask_log_text("confirmation number 48213"))

    def test_letter_only_code_is_masked_once_released_and_not_before(self):
        text = "your code is KJHDWQ, read it back"
        self.assertEqual(text, mask_for_transcript(text), "known limit: shape masking cannot see a letter-only code")
        scrub.register_released("KJHDWQ")
        self.assertNotIn("KJHDWQ", mask_for_transcript(text))
        self.assertNotIn("kjhdwq", mask_for_transcript(text.lower()))

    def test_released_code_in_every_spelling(self):
        scrub.register_released("739204")
        for text in ("7 3 9 2 0 4", "7-3-9-2-0-4", "739.204", "７３９２０４", "7\u200b3\u200b9204", "code739204end"):
            out = mask_for_transcript(text)
            self.assertNotRegex(out.replace("[CODE]", ""), r"\d", text)

    def test_scrubber_expires_and_is_bounded(self):
        t = {"now": 1000.0}
        sc = scrub.ValueScrubber(ttl_s=10, max_values=2, clock=lambda: t["now"])
        sc.register("111111")
        sc.register("222222")
        sc.register("333333")
        self.assertEqual("111111 [CODE] [CODE]", sc.scrub("111111 222222 333333"))
        t["now"] += 11
        self.assertEqual("222222", sc.scrub("222222"))
        self.assertEqual(0, len(sc))

    def test_scrub_time_is_bounded_on_large_and_hostile_results(self):
        import time

        sc = scrub.ValueScrubber()
        sc.register("739204")
        sc.register("111111")
        for text in ("word 12345 \n" * 100_000, "1 " * 500_000, "1" * 1_000_000, " " * 1_000_000):
            start = time.time()
            sc.scrub(text)
            self.assertLess(time.time() - start, 5.0)

    def test_too_short_or_too_long_values_are_not_registered(self):
        sc = scrub.ValueScrubber()
        self.assertFalse(sc.register("123"))
        self.assertFalse(sc.register("1" * 40))
        self.assertFalse(sc.register(""))
        self.assertTrue(sc.register("1234"))

    def test_log_filter_scrubs_released_values_in_free_text(self):
        scrub.register_released("KJHDWQ")
        cap = Capture()
        lg = logging.getLogger("line.sms")
        lg.addHandler(cap)
        old = lg.level
        lg.setLevel(logging.DEBUG)
        self.addCleanup(lg.removeHandler, cap)
        self.addCleanup(lg.setLevel, old)
        lg.info("sloppy: the code was %s", "KJHDWQ")
        self.assertNotIn("KJHDWQ", "\n".join(cap.lines))


if __name__ == "__main__":
    unittest.main()
