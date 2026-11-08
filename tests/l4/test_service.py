"""release_otp / OtpService: single-use consent, wrong sender, wrong window, two codes, replay.

Every message here is synthetic: sender `ACME`, invented codes. Assertions are on statuses and
on `SecretText.reveal()` only where the test is about the one released value.
"""
import logging
import threading
import unittest

from line.sms import ApprovedTask, FixtureSource, MemoryReleasedStore, SecretText, SmsMessage
from line.policy.consent import ConsentStore
from line.sms.consent import OTP_ACTION
from line.sms.destination import Destination
from line.sms.released import default_store
from line.sms.service import (
    OtpService,
    OtpTaskRegistry,
    ReleaseStatus,
    approval_prompt,
    binding_for,
    configure,
    release_otp,
)
from line.sms.sources.base import DeviceUnavailable, SmsSource, SourceError, SourceStatus

from .helpers import CALL_DEST, FILL_DEST, MIN, T0

CODE_A = "739204"
CODE_B = "581466"


class Clock:
    def __init__(self, t_ms):
        self.t = t_ms / 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def sms(i, minute, body, sender="ACME"):
    return SmsMessage(str(i), sender, T0 + int(minute * MIN), body, source="fixture")


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(T0 + 2 * MIN)
        self.consent = ConsentStore(clock=self.clock)
        self.audit = []
        self.cap = Capture()
        lg = logging.getLogger("line.sms")
        self.old_level = lg.level
        lg.addHandler(self.cap)
        lg.setLevel(logging.DEBUG)
        self.addCleanup(lg.removeHandler, self.cap)
        self.addCleanup(lg.setLevel, self.old_level)
        self.addCleanup(configure, None)

    def service(self, messages, *, visible_after=None, **kw):
        self.source = FixtureSource(messages, visible_after_ms=visible_after, clock=self.clock)
        kw.setdefault("audit", self.audit.append)
        kw.setdefault("interval_s", 1.0)
        kw.setdefault("released", MemoryReleasedStore())
        return OtpService(self.source, OtpTaskRegistry(), clock=self.clock, sleep=self.clock.advance, **kw)

    def approve(self, svc, task_id="t1", senders=("ACME",), start=0, end=10, ttl_s=600):
        task = ApprovedTask(task_id, tuple(senders), T0 + start * MIN, T0 + end * MIN, destination=FILL_DEST)
        svc.registry.register(task)
        return self.consent.issue(OTP_ACTION, task_id, binding_for(task), ttl_s)

    def attempt(self, svc, token, task_id="t1", **kw):
        return svc.attempt(task_id, token, self.consent, **kw)

    def assertNoLeak(self):
        blob = "\n".join(self.cap.lines + [repr(a) for a in self.audit])
        for needle in (CODE_A, CODE_B, "Your ACME code", "verification"):
            self.assertNotIn(needle, blob)


class ReleaseHappyPath(Base):
    def test_release_returns_secret_and_spends_consent(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s. Do not share." % CODE_A)])
        tok = self.approve(svc)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.RELEASED, res.status)
        self.assertIsInstance(res.secret, SecretText)
        self.assertEqual(CODE_A, res.secret.reveal())
        # the same token cannot be used again
        self.assertFalse(self.consent.consume(tok, OTP_ACTION, "t1", binding_for(svc.registry.get("t1"))))
        self.assertNoLeak()
        self.assertNotIn(CODE_A, repr(res) + str(res.secret) + res.log_line())

    def test_contract_entry_point(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        tok = self.approve(svc)
        self.assertIsNone(release_otp("t1", tok, self.consent), "not configured: fail closed")
        configure(svc)
        out = release_otp("t1", tok, self.consent)
        self.assertIsInstance(out, SecretText)
        self.assertEqual(CODE_A, out.reveal())
        self.assertIsNone(release_otp("t1", tok, self.consent), "second call: nothing to release")


class SingleUseConsent(Base):
    def test_token_cannot_release_a_resend(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        tok = self.approve(svc)
        self.assertIs(ReleaseStatus.RELEASED, self.attempt(svc, tok).status)
        self.source.add(sms(2, 3, "Your ACME code is %s." % CODE_B))   # a resend arrives
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.NO_CONSENT, res.status)
        self.assertIsNone(res.secret)
        self.assertNoLeak()

    def test_new_approval_releases_the_resend(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        self.assertIs(ReleaseStatus.RELEASED, self.attempt(svc, self.approve(svc)).status)
        self.source.add(sms(2, 3, "Your ACME code is %s." % CODE_B))
        tok2 = self.consent.issue(OTP_ACTION, "t1", binding_for(svc.registry.get("t1")), 600)
        res = self.attempt(svc, tok2)
        self.assertIs(ReleaseStatus.RELEASED, res.status)
        self.assertEqual(CODE_B, res.secret.reveal())

    def test_missing_or_garbage_token(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        self.approve(svc)
        for bad in (None, "", "nope", "x" * 200, 12345):
            res = self.attempt(svc, bad)
            self.assertIs(ReleaseStatus.NO_CONSENT, res.status, bad)
            self.assertIsNone(res.secret)
        self.assertNoLeak()

    def test_missing_token_does_not_read_the_phone(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        self.approve(svc)
        self.attempt(svc, None)
        self.attempt(svc, "")
        self.assertEqual(0, self.source.fetch_calls)

    def test_expired_consent(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        tok = self.approve(svc, ttl_s=30)
        self.clock.advance(31)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.NO_CONSENT, res.status)
        self.assertIsNone(res.secret)

    def test_token_for_another_task_or_action_is_rejected(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        self.approve(svc, task_id="t1")
        self.approve(svc, task_id="t2")
        task1 = svc.registry.get("t1")
        other_task = self.consent.issue(OTP_ACTION, "t2", binding_for(task1), 600)
        wrong_action = self.consent.issue("outbound_call", "t1", binding_for(task1), 600)
        for tok in (other_task, wrong_action):
            res = self.attempt(svc, tok, task_id="t1")
            self.assertIs(ReleaseStatus.NO_CONSENT, res.status)
            self.assertIsNone(res.secret)

    def test_token_bound_to_a_narrower_approval_is_rejected(self):
        """Owner approved sender OTHER; the registered task also lists ACME: binding mismatch."""
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        self.approve(svc)
        narrow = ApprovedTask("t1", ("OTHER",), T0, T0 + 10 * MIN, destination=FILL_DEST)
        tok = self.consent.issue(OTP_ACTION, "t1", binding_for(narrow), 600)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.NO_CONSENT, res.status)
        self.assertIsNone(res.secret)

    def test_waiting_does_not_burn_consent(self):
        svc = self.service([])
        tok = self.approve(svc)
        res = self.attempt(svc, tok, timeout_s=5)
        self.assertIs(ReleaseStatus.NO_MESSAGE, res.status)
        self.source.add(sms(1, 3, "Your ACME code is %s." % CODE_A))
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.RELEASED, res.status)

    def test_broken_consent_store_fails_closed(self):
        class Boom:
            def consume(self, *a, **k):
                raise RuntimeError("db is down")

        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        tok = self.approve(svc)
        res = svc.attempt("t1", tok, Boom())
        self.assertIs(ReleaseStatus.NO_CONSENT, res.status)
        self.assertIsNone(res.secret)
        self.assertNoLeak()

    def test_concurrent_callers_release_once(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        tok = self.approve(svc)
        results = []
        gate = threading.Barrier(8)

        def worker():
            gate.wait()
            results.append(self.attempt(svc, tok))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        released = [r for r in results if r.status is ReleaseStatus.RELEASED]
        self.assertEqual(1, len(released))
        self.assertEqual(7, len([r for r in results if r.secret is None]))


class WrongSender(Base):
    def test_other_sender_never_releases_and_keeps_consent(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A, sender="EVILCO")])
        tok = self.approve(svc)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.NO_MESSAGE, res.status)
        self.assertIsNone(res.secret)
        self.assertEqual(0, res.considered)
        # consent untouched: the genuine message can still be released with it
        self.source.add(sms(2, 2, "Your ACME code is %s." % CODE_B))
        res = self.attempt(svc, tok)
        self.assertEqual(CODE_B, res.secret.reveal())

    def test_lookalike_sender_and_phone_number_form(self):
        msgs = [sms(1, 1, "code %s" % CODE_A, sender="ACME1"), sms(2, 1, "code %s" % CODE_B, sender="+15550100999")]
        svc = self.service(msgs)
        tok = self.approve(svc, senders=("ACME", "+15550100123"))
        self.assertIs(ReleaseStatus.NO_MESSAGE, self.attempt(svc, tok).status)

    def test_phone_sender_spelling_variants_match(self):
        svc = self.service([sms(1, 1, "Your code is %s." % CODE_A, sender="(555) 010-0123")])
        tok = self.approve(svc, senders=("+1 555 010 0123",))
        self.assertEqual(CODE_A, self.attempt(svc, tok).secret.reveal())

    def test_wrong_sender_with_two_senders_approved_only_in_rules(self):
        msgs = [sms(1, 1, "Your code is %s." % CODE_A, sender="OTHER"), sms(2, 2, "Your code is %s." % CODE_B, sender="32665")]
        svc = self.service(msgs)
        tok = self.approve(svc, senders=("32665",))
        self.assertEqual(CODE_B, self.attempt(svc, tok).secret.reveal())


class WrongWindow(Base):
    def test_message_before_window_is_ignored(self):
        self.clock.t = (T0 + 7 * MIN) / 1000.0
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        tok = self.approve(svc, start=5, end=15)   # message at minute 1, window opens at 5 (skew 30 s)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.NO_MESSAGE, res.status)
        self.assertIsNone(res.secret)

    def test_message_just_inside_clock_skew_is_accepted(self):
        self.clock.t = (T0 + 7 * MIN) / 1000.0
        svc = self.service([sms(1, 4.7, "Your ACME code is %s." % CODE_A)])   # 18 s before the window opens
        tok = self.approve(svc, start=5, end=15)
        self.assertIs(ReleaseStatus.RELEASED, self.attempt(svc, tok).status)

    def test_message_after_window_is_ignored(self):
        svc = self.service([sms(1, 12, "Your ACME code is %s." % CODE_A)])
        tok = self.approve(svc, start=0, end=10)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.NO_MESSAGE, res.status)
        self.assertIsNone(res.secret)

    def test_no_release_once_the_window_closed_even_for_an_in_window_message(self):
        svc = self.service([sms(1, 9, "Your ACME code is %s." % CODE_A)])
        tok = self.approve(svc, start=0, end=10)
        self.clock.t = (T0 + 11 * MIN) / 1000.0
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.EXPIRED, res.status)
        self.assertIsNone(res.secret)
        self.assertEqual(0, self.source.fetch_calls)
        self.assertNoLeak()

    def test_window_closing_while_waiting(self):
        svc = self.service([])
        tok = self.approve(svc, start=0, end=3)
        res = self.attempt(svc, tok, timeout_s=300)   # waits; the clock runs past minute 3
        self.assertIs(ReleaseStatus.EXPIRED, res.status)

    def test_provider_that_withholds_otp_messages_times_out_without_release(self):
        """Android 17 style delay: the code is invisible for hours, so nothing is released."""
        m = sms(1, 1, "Your ACME code is %s." % CODE_A)
        svc = self.service([m], visible_after={"1": T0 + 3 * 60 * MIN})
        tok = self.approve(svc, start=0, end=10)
        res = self.attempt(svc, tok, timeout_s=20)
        self.assertIs(ReleaseStatus.NO_MESSAGE, res.status)
        self.assertIsNone(res.secret)


class TwoCodes(Base):
    def test_two_competing_codes_in_one_message_never_release(self):
        svc = self.service([sms(1, 1, "Your ACME codes: %s and %s." % (CODE_A, CODE_B))])
        tok = self.approve(svc)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.AMBIGUOUS, res.status)
        self.assertIsNone(res.secret)
        self.assertNoLeak()
        # the consent is intact for a later, unambiguous message
        self.source.add(sms(2, 3, "Your ACME code is %s." % CODE_B))
        self.assertIs(ReleaseStatus.RELEASED, self.attempt(svc, tok).status)

    def test_newer_ambiguous_message_blocks_an_older_clean_code(self):
        msgs = [sms(1, 1, "Your ACME code is %s." % CODE_A), sms(2, 3, "Your ACME codes: %s and %s." % (CODE_A, CODE_B))]
        svc = self.service(msgs)
        tok = self.approve(svc)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.AMBIGUOUS, res.status)
        self.assertIsNone(res.secret)

    def test_two_messages_newest_wins_and_older_stays_unreleased_for_replay(self):
        msgs = [sms(1, 1, "Your ACME code is %s." % CODE_A), sms(2, 3, "Your ACME code is %s." % CODE_B)]
        svc = self.service(msgs)
        res = self.attempt(svc, self.approve(svc))
        self.assertEqual(CODE_B, res.secret.reveal())


class Replay(Base):
    def test_same_message_cannot_release_twice_with_fresh_consent(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        tok1 = self.approve(svc)
        self.assertIs(ReleaseStatus.RELEASED, self.attempt(svc, tok1).status)
        tok2 = self.consent.issue(OTP_ACTION, "t1", binding_for(svc.registry.get("t1")), 600)
        res = self.attempt(svc, tok2)
        self.assertIs(ReleaseStatus.ALREADY_USED, res.status)
        self.assertIsNone(res.secret)
        # and the second token was not spent: it is still good for a genuinely new message
        self.source.add(sms(2, 4, "Your ACME code is %s." % CODE_B))
        self.assertEqual(CODE_B, self.attempt(svc, tok2).secret.reveal())

    def test_released_message_stays_used_across_tasks(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        t1 = self.approve(svc, task_id="t1")
        t2 = self.approve(svc, task_id="t2")
        self.assertIs(ReleaseStatus.RELEASED, self.attempt(svc, t1, task_id="t1").status)
        res = self.attempt(svc, t2, task_id="t2")
        self.assertIs(ReleaseStatus.ALREADY_USED, res.status)
        self.assertIsNone(res.secret)

    def test_replaying_a_used_token_for_another_task_fails(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        t1 = self.approve(svc, task_id="t1")
        self.approve(svc, task_id="t2")
        self.attempt(svc, t1, task_id="t1")
        self.source.add(sms(2, 4, "Your ACME code is %s." % CODE_B))
        res = self.attempt(svc, t1, task_id="t2")
        self.assertIs(ReleaseStatus.NO_CONSENT, res.status)

    def test_shared_released_store_survives_a_service_restart(self):
        msgs = [sms(1, 1, "Your ACME code is %s." % CODE_A)]
        store = MemoryReleasedStore()
        svc = self.service(msgs, released=store)
        self.assertIs(ReleaseStatus.RELEASED, self.attempt(svc, self.approve(svc)).status)
        svc2 = self.service(msgs, released=store)
        res = self.attempt(svc2, self.approve(svc2))
        self.assertIs(ReleaseStatus.ALREADY_USED, res.status)


class PollingInterval(Base):
    """Wi-Fi latency: a read takes seconds; the poll interval counts from the start of the read, not after it."""

    def test_a_slow_read_is_not_followed_by_another_full_wait(self):
        sleeps = []
        self.source = FixtureSource([], clock=self.clock)
        orig = self.source.fetch

        def slow_fetch(**kw):
            self.clock.advance(4.0)                    # a 4 s `content query`
            return orig(**kw)

        self.source.fetch = slow_fetch
        svc = OtpService(self.source, OtpTaskRegistry(), released=MemoryReleasedStore(), clock=self.clock,
                         sleep=lambda x: (sleeps.append(x), self.clock.advance(x)), interval_s=3.0)
        tok = self.approve(svc)
        res = self.attempt(svc, tok, timeout_s=20)
        self.assertIs(ReleaseStatus.NO_MESSAGE, res.status)
        self.assertTrue(sleeps)
        self.assertTrue(all(x == 0.0 for x in sleeps), sleeps)

    def test_a_fast_read_still_waits_out_the_interval(self):
        sleeps = []
        svc = OtpService(FixtureSource([], clock=self.clock), OtpTaskRegistry(), released=MemoryReleasedStore(),
                         clock=self.clock, sleep=lambda x: (sleeps.append(x), self.clock.advance(x)), interval_s=3.0)
        tok = self.approve(svc)
        self.attempt(svc, tok, timeout_s=10)
        self.assertEqual([3.0, 3.0, 3.0], sleeps)


class RaceAtCommit(Base):
    def test_a_message_used_between_peek_and_commit_is_not_released_and_consent_is_kept(self):
        """Two callers with different tokens both see RELEASE in the dry run; the lock decides."""
        from line.sms.matching import OtpMatcher, message_key

        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        tok = self.approve(svc)
        task = svc.registry.get("t1")
        outcome = OtpMatcher(task, used=set()).evaluate(self.source.fetch(), now_ms=T0 + 2 * MIN, commit=False)
        self.assertEqual("release", outcome.decision.value)
        svc.used.add(message_key(outcome.message))          # the other caller won the race
        res = svc._commit(svc.registry.get_entry('t1'), outcome, tok, self.consent)
        self.assertIs(ReleaseStatus.ALREADY_USED, res.status)
        self.assertIsNone(res.secret)
        self.assertTrue(self.consent.consume(tok, OTP_ACTION, "t1", binding_for(task)), "token must still be unspent")


class DestinationBinding(Base):
    def test_a_task_without_a_destination_cannot_be_registered_or_bound(self):
        svc = self.service([])
        bare = ApprovedTask("t1", ("ACME",), T0, T0 + MIN)
        with self.assertRaises(ValueError):
            svc.registry.register(bare)
        with self.assertRaises(ValueError):
            binding_for(bare)
        with self.assertRaises(ValueError):
            approval_prompt(bare)

    def test_binding_carries_the_destination(self):
        fill = ApprovedTask("t", ("ACME",), 1, 2, destination=FILL_DEST)
        call = ApprovedTask("t", ("ACME",), 1, 2, destination=CALL_DEST)
        self.assertEqual({"kind": "fill", "origin": "https://example.com"}, binding_for(fill)["destination"])
        self.assertEqual({"kind": "call", "call_id": "call-1", "counterpart": "+15550100999"}, binding_for(call)["destination"])
        self.assertNotEqual(binding_for(fill), binding_for(call))

    def test_a_token_for_another_destination_never_releases(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        self.approve(svc)
        task = svc.registry.get("t1")
        for dest in (Destination.fill("https://other.example.org"), CALL_DEST, Destination.fill("https://example.com:8443")):
            tok = self.consent.issue(OTP_ACTION, "t1", binding_for(ApprovedTask("t1", task.sender_rules, task.window_start_ms,
                                                                                  task.window_end_ms, destination=dest)), 600)
            res = self.attempt(svc, tok)
            self.assertIs(ReleaseStatus.NO_CONSENT, res.status, dest)
            self.assertIsNone(res.secret)

    def test_task_cannot_be_reregistered_with_another_destination(self):
        svc = self.service([])
        self.approve(svc)
        with self.assertRaises(ValueError):
            svc.registry.register(ApprovedTask("t1", ("ACME",), T0, T0 + 10 * MIN, destination=CALL_DEST))

    def test_approval_prompt_shows_sender_window_and_destination(self):
        fill = approval_prompt(ApprovedTask("t", ("ACME", "32665"), T0, T0 + 10 * MIN, destination=FILL_DEST))
        for needle in ("ACME", "32665", "2026-11-05T00:00:00Z", "2026-11-05T00:10:00Z", "https://example.com"):
            self.assertIn(needle, fill)
        call = approval_prompt(ApprovedTask("t", ("ACME",), T0, T0 + 10 * MIN, destination=CALL_DEST))
        for needle in ("call-1", "+15550100999"):
            self.assertIn(needle, call)


class FailingStore:
    def __init__(self, fail_contains=False, fail_add=False):
        self.keys, self.fail_contains, self.fail_add = set(), fail_contains, fail_add

    def contains(self, key):
        if self.fail_contains:
            raise OSError("disk gone")
        return key in self.keys

    def add(self, key):
        if self.fail_add:
            raise OSError("disk full")
        self.keys.add(key)


class ReleasedStoreInterface(Base):
    """`line.store.released_otps` (L1) is any object with add(key) / contains(key)."""

    def test_a_plain_add_contains_object_is_enough_and_gets_message_keys_only(self):
        store = FailingStore()
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)], released=store)
        self.assertIs(ReleaseStatus.RELEASED, self.attempt(svc, self.approve(svc)).status)
        self.assertEqual({"fixture:1:%d" % (T0 + MIN)}, store.keys)
        self.assertNotIn(CODE_A, repr(store.keys))

    def test_store_that_cannot_record_means_nothing_is_released(self):
        store = FailingStore(fail_add=True)
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)], released=store)
        res = self.attempt(svc, self.approve(svc))
        self.assertIs(ReleaseStatus.STORE_ERROR, res.status)
        self.assertIsNone(res.secret)
        self.assertNoLeak()

    def test_store_that_cannot_be_read_means_nothing_is_released_and_consent_is_kept(self):
        store = FailingStore(fail_contains=True)
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)], released=store)
        tok = self.approve(svc)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.STORE_ERROR, res.status)
        self.assertIsNone(res.secret)
        store.fail_contains = False
        self.assertIs(ReleaseStatus.RELEASED, self.attempt(svc, tok).status)

    def test_default_store_is_l1s_module_now_that_l1_is_merged(self):
        """L1's `line.store.released_otps` exists in the tree since the L1 merge, so `default_store()` returns it
        (it fails closed until the engine binds a store). The memory stand-in only serves a tree without L1."""
        import sys

        from line.store import released_otps

        self.assertIs(released_otps, default_store())
        saved = sys.modules.get("line.store", "absent")
        sys.modules["line.store"] = None        # makes the import raise ImportError: L1 not installed
        try:
            self.assertEqual("MemoryReleasedStore", type(default_store()).__name__)
        finally:
            if saved == "absent":
                sys.modules.pop("line.store", None)
            else:
                sys.modules["line.store"] = saved

    def test_imports_are_package_relative(self):
        """Under Hermes the plugin is not importable as top-level `line`: load it under another package name."""
        import importlib
        import os
        import sys

        root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "plugin", "line")
        import types

        pkg = types.ModuleType("hl_alias")
        pkg.__path__ = [root]
        sys.modules["hl_alias"] = pkg
        try:
            released = importlib.import_module("hl_alias.sms.released")
            stub = importlib.import_module("hl_alias.sms.engine_stub")
            self.assertEqual("hl_alias.store.released_otps", released.default_store().__name__)
            self.assertIs(importlib.import_module("hl_alias.engine.sealed").send_secret_to_call, stub.resolve_send_secret_to_call())
        finally:
            for k in [k for k in sys.modules if k == "hl_alias" or k.startswith("hl_alias.")]:
                del sys.modules[k]


class Registry(Base):
    def test_unknown_task(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        self.assertIs(ReleaseStatus.NO_TASK, self.attempt(svc, "tok", task_id="ghost").status)

    def test_hostile_task_ids_are_not_echoed(self):
        svc = self.service([])
        for bad in ("x" * 500, None, 5, ["t1"]):
            res = svc.attempt(bad, "tok", self.consent)
            self.assertIs(ReleaseStatus.NO_TASK, res.status)
            self.assertEqual("", res.task_id)
        with self.assertRaises(ValueError):
            svc.registry.register(ApprovedTask("y" * 81, ("ACME",), T0, T0 + MIN, destination=FILL_DEST))

    def test_a_registered_task_cannot_be_widened(self):
        svc = self.service([])
        self.approve(svc, start=0, end=10)
        with self.assertRaises(ValueError):
            svc.registry.register(ApprovedTask("t1", ("ACME", "EVIL*"), T0, T0 + 10 * MIN, destination=FILL_DEST))
        with self.assertRaises(ValueError):
            svc.registry.register(ApprovedTask("t1", ("ACME",), T0, T0 + 99 * MIN, destination=FILL_DEST))
        svc.registry.register(svc.registry.get("t1"))   # re-registering identical terms is fine

    def test_degenerate_tasks_are_refused(self):
        reg = OtpTaskRegistry()
        with self.assertRaises(ValueError):
            reg.register(ApprovedTask("x", (), T0, T0 + MIN, destination=FILL_DEST))
        with self.assertRaises(ValueError):
            reg.register(ApprovedTask("x", ("ACME",), T0 + MIN, T0, destination=FILL_DEST))
        with self.assertRaises(ValueError):
            reg.register(ApprovedTask("", ("ACME",), T0, T0 + MIN, destination=FILL_DEST))

    def test_binding_is_canonical(self):
        a = ApprovedTask("t", ("ACME", "+1 (555) 010-0123"), 1, 2, destination=FILL_DEST)
        b = ApprovedTask("t", ("5550100123", "acme"), 1, 2, destination=FILL_DEST)
        self.assertEqual(binding_for(a), binding_for(b))


class SourceFailures(Base):
    def test_unreadable_phone_is_a_status_not_a_leak(self):
        class Dead(SmsSource):
            name = "dead"

            def fetch(self, **kw):
                raise DeviceUnavailable("no device attached")

            def check(self):
                return SourceStatus(False, self.name, "no device")

        svc = OtpService(Dead(), OtpTaskRegistry(), released=MemoryReleasedStore(), clock=self.clock, sleep=self.clock.advance, audit=self.audit.append)
        tok = self.approve(svc)
        res = self.attempt(svc, tok)
        self.assertIs(ReleaseStatus.SOURCE_ERROR, res.status)
        self.assertIsNone(res.secret)

    def test_audit_hook_failure_does_not_change_the_outcome(self):
        def bad(_):
            raise RuntimeError("audit sink down")

        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)], audit=bad)
        res = self.attempt(svc, self.approve(svc))
        self.assertIs(ReleaseStatus.RELEASED, res.status)


class AuditAndLogs(Base):
    def test_audit_events_carry_status_only(self):
        svc = self.service([sms(1, 1, "Your ACME code is %s." % CODE_A)])
        self.attempt(svc, self.approve(svc))
        self.assertEqual(1, len(self.audit))
        ev = self.audit[0]
        self.assertEqual({"event", "task_id", "status", "sender", "considered", "destination"}, set(ev))
        self.assertEqual("fill:https://example.com", ev["destination"])
        self.assertEqual("released", ev["status"])
        self.assertEqual("A***ME", ev["sender"])   # masked form only
        self.assertNoLeak()


if __name__ == "__main__":
    unittest.main()
