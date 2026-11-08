import unittest

from smsline import ApprovedTask, Decision, FixtureSource, SmsMessage, TaskMatcher, normalize_sender, wait_for_code
from smsline.sources.base import DeviceUnavailable, SmsSource, SourceError, SourceStatus

from tests.helpers import MIN, T0, fixture_messages


def task(rules=("ACME",), start=0, end=60, **kw):
    return ApprovedTask("t1", tuple(rules), T0 + start * MIN, T0 + end * MIN, **kw)


class SenderNormalisation(unittest.TestCase):
    def test_forms(self):
        n = normalize_sender
        self.assertEqual("+15550100123", n("+1 (555) 010-0123"))
        self.assertEqual("+15550100123", n("5550100123"))
        self.assertEqual("+15550100123", n("15550100123"))
        self.assertEqual("32665", n("32665"))
        self.assertEqual("examplebank", n("Example Bank"))
        self.assertEqual("", n(""))
        self.assertEqual("+905321234567", n("+90 532 123 45 67"))

    def test_rule_matching(self):
        t = task(rules=("+15550100123", "32665", "EXAMPLE*"))
        self.assertTrue(t.sender_ok("(555) 010-0123"))
        self.assertTrue(t.sender_ok("32665"))
        self.assertTrue(t.sender_ok("ExampleBank"))
        self.assertFalse(t.sender_ok("32666"))
        self.assertFalse(t.sender_ok("EXAMPL"))
        self.assertFalse(t.sender_ok("ACME"))
        self.assertFalse(t.sender_ok(""))

    def test_lookalike_senders_do_not_match(self):
        t = task(rules=("ACME",))
        for bad in ("ACME1", "ACMEX", "xACME", "AC ME ", "ACME-", "+15550100123"):
            if bad.strip().replace(" ", "").casefold() == "acme":
                continue
            self.assertFalse(t.sender_ok(bad), bad)


class MatcherDecisions(unittest.TestCase):
    def setUp(self):
        self.msgs = fixture_messages()

    def evaluate(self, t, msgs=None, now=None, used=None):
        m = TaskMatcher(t, used)
        return m, m.evaluate(self.msgs if msgs is None else msgs, now_ms=min(t.window_end_ms, t.window_start_ms + MIN) if now is None else now)

    def test_release_newest_matching_code(self):
        # ACME messages in the 0..20 window: 101 (482915) and 104 (650318): newest wins
        _, out = self.evaluate(task(start=0, end=20))
        self.assertEqual(Decision.RELEASE, out.decision)
        self.assertEqual("650318", out.release())
        self.assertEqual("104", out.message.id)
        self.assertEqual(2, out.considered)

    def test_release_is_the_only_unmasked_path(self):
        _, out = self.evaluate(task(start=0, end=20))
        for text in (repr(out), str(out), out.log_line(), repr(out.code), str(out.code)):
            self.assertNotIn("650318", text)

    def test_window_start_end(self):
        _, out = self.evaluate(task(start=2, end=5))     # nothing from ACME between minute 2 and 5
        self.assertEqual(Decision.NO_MESSAGE, out.decision)
        with self.assertRaises(PermissionError):
            out.release()

    def test_clock_skew_widens_the_start_only(self):
        t = task(start=1, end=2, clock_skew_ms=45_000)    # message at minute 1 exactly
        _, out = self.evaluate(t)
        self.assertEqual("482915", out.release())
        t2 = task(start=2, end=3, clock_skew_ms=45_000)   # minute 1 is 60s before start > 45s skew
        _, out2 = self.evaluate(t2)
        self.assertEqual(Decision.NO_MESSAGE, out2.decision)

    def test_window_end_is_hard(self):
        t = task(start=0, end=5, clock_skew_ms=0)         # 104 arrives at minute 6
        _, out = self.evaluate(t)
        self.assertEqual("482915", out.release())

    def test_wrong_sender_never_releases(self):
        _, out = self.evaluate(task(rules=("NOBODY",), start=0, end=60))
        self.assertEqual(Decision.NO_MESSAGE, out.decision)

    def test_short_code_sender(self):
        _, out = self.evaluate(task(rules=("32665",), start=0, end=10))
        self.assertEqual("739104", out.release())

    def test_other_senders_codes_are_not_considered(self):
        _, out = self.evaluate(task(rules=("OTHERCO",), start=0, end=60))
        self.assertEqual("905512", out.release())
        self.assertEqual(1, out.considered)

    def test_ambiguous_never_releases(self):
        _, out = self.evaluate(task(rules=("EXAMPLEBANK",), start=0, end=60))
        self.assertEqual(Decision.AMBIGUOUS, out.decision)
        self.assertIsNone(out.code)

    def test_ambiguous_newer_message_blocks_older_code(self):
        msgs = [
            SmsMessage("1", "ACME", T0 + 1 * MIN, "Your code is 482915."),
            SmsMessage("2", "ACME", T0 + 2 * MIN, "Your code is 111222. Backup code: 333444"),
        ]
        _, out = self.evaluate(task(start=0, end=10), msgs)
        self.assertEqual(Decision.AMBIGUOUS, out.decision)

    def test_in_window_message_without_code(self):
        _, out = self.evaluate(task(start=31, end=40))
        self.assertEqual(Decision.NO_CODE, out.decision)

    def test_single_use(self):
        t = task(start=0, end=2)
        matcher, out = self.evaluate(t)
        self.assertEqual("482915", out.release())
        again = matcher.evaluate(self.msgs, now_ms=T0 + 3 * MIN)
        self.assertEqual(Decision.ALREADY_USED, again.decision)
        with self.assertRaises(PermissionError):
            again.release()

    def test_used_set_can_be_shared_across_tasks(self):
        used = set()
        _, a = self.evaluate(task(start=0, end=2), used=used)
        b_matcher = TaskMatcher(ApprovedTask("t2", ("ACME",), T0, T0 + 2 * MIN), used)
        b = b_matcher.evaluate(self.msgs, now_ms=T0 + 3 * MIN)
        self.assertEqual(Decision.RELEASE, a.decision)
        self.assertEqual(Decision.ALREADY_USED, b.decision)

    def test_a_resend_wins_over_the_earlier_code(self):
        # first code consumed, then the service resends: the new message releases
        t = task(start=0, end=20)
        matcher = TaskMatcher(t)
        first = matcher.evaluate(self.msgs[:1], now_ms=T0 + 2 * MIN)
        self.assertEqual("482915", first.release())
        second = matcher.evaluate(self.msgs[:4], now_ms=T0 + 7 * MIN)
        self.assertEqual("650318", second.release())

    def test_expired_when_window_closed_and_nothing_found(self):
        _, out = self.evaluate(task(rules=("NOBODY",), start=0, end=5), now=T0 + 10 * MIN)
        self.assertEqual(Decision.EXPIRED, out.decision)

    def test_log_line_has_no_content(self):
        _, out = self.evaluate(task(start=0, end=20))
        line = out.log_line()
        self.assertIn("decision=release", line)
        self.assertNotIn("650318", line)
        self.assertNotIn("ACME code", line)


class FakeClock:
    def __init__(self, start):
        self.t = start
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class FlakySource(SmsSource):
    name = "flaky"

    def __init__(self, errors, then):
        self.errors, self.then, self.calls = errors, then, 0

    def fetch(self, **kw):
        self.calls += 1
        if self.calls <= self.errors:
            raise DeviceUnavailable("unplugged")
        return self.then

    def check(self):
        return SourceStatus(True, self.name)


class PollingTests(unittest.TestCase):
    def test_code_arrives_while_polling(self):
        clk = FakeClock(T0 / 1000.0 + 10)
        late = SmsMessage("9", "ACME", T0 + 40_000, "Your code is 482915")
        src = FixtureSource([late], visible_after_ms={"9": T0 + 25_000}, clock=clk.now)
        t = ApprovedTask("t", ("ACME",), T0, T0 + 5 * MIN)
        out = wait_for_code(src, TaskMatcher(t), timeout_s=120, interval_s=5, clock=clk.now, sleep=clk.sleep)
        # message dated +40s is only fetched once it is both visible (+25s) and... always in window
        self.assertEqual(Decision.RELEASE, out.decision)
        self.assertEqual("482915", out.release())
        self.assertGreaterEqual(len(clk.sleeps), 1)

    def test_android17_style_delay_times_out_without_release(self):
        """A provider that withholds OTP messages for 3h looks like 'nothing arrived'."""
        clk = FakeClock(T0 / 1000.0 + 10)
        msg = SmsMessage("9", "ACME", T0 + 5_000, "Your code is 482915")
        src = FixtureSource([msg], visible_after_ms={"9": T0 + 3 * 3600_000}, clock=clk.now)
        t = ApprovedTask("t", ("ACME",), T0, T0 + 5 * MIN)
        out = wait_for_code(src, TaskMatcher(t), timeout_s=60, interval_s=10, clock=clk.now, sleep=clk.sleep)
        self.assertEqual(Decision.EXPIRED, out.decision)
        self.assertIsNone(out.code)
        self.assertGreaterEqual(src.fetch_calls, 3)

    def test_transient_source_errors_are_retried(self):
        clk = FakeClock(T0 / 1000.0 + 10)
        good = SmsMessage("9", "ACME", T0 + 5_000, "Your code is 482915")
        src = FlakySource(errors=2, then=[good])
        t = ApprovedTask("t", ("ACME",), T0, T0 + 5 * MIN)
        out = wait_for_code(src, TaskMatcher(t), timeout_s=60, interval_s=2, clock=clk.now, sleep=clk.sleep)
        self.assertEqual("482915", out.release())

    def test_persistent_source_error_is_raised_for_fallback(self):
        clk = FakeClock(T0 / 1000.0 + 10)
        src = FlakySource(errors=99, then=[])
        t = ApprovedTask("t", ("ACME",), T0, T0 + 5 * MIN)
        with self.assertRaises(SourceError):
            wait_for_code(src, TaskMatcher(t), timeout_s=60, interval_s=2, clock=clk.now, sleep=clk.sleep, max_source_errors=3)
        self.assertEqual(3, src.calls)

    def test_window_close_stops_polling_before_timeout(self):
        clk = FakeClock(T0 / 1000.0 + 200)           # already past a 100s window
        src = FixtureSource([], clock=clk.now)
        t = ApprovedTask("t", ("ACME",), T0, T0 + 100_000)
        out = wait_for_code(src, TaskMatcher(t), timeout_s=600, interval_s=5, clock=clk.now, sleep=clk.sleep)
        self.assertEqual(Decision.EXPIRED, out.decision)
        self.assertEqual([], clk.sleeps)


if __name__ == "__main__":
    unittest.main()
