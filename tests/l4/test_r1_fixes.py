"""Regression tests for the R1 review findings (REVIEW.md), named after the finding.

H1  a sealed slot is bound to an approval generation + binding; replacement/revocation invalidates it.
M1  (L4 half) a release call carries the engine's pending approval generation and is refused on a mismatch.
BF  browser fill: off by default; the top-level origin is re-probed right before and right after typing.
"""
import json
import random
import threading
import unittest

from line.sms import ApprovedTask, Destination, SecretText, SmsMessage
from line.sms.consent import OTP_ACTION
from line.sms.delivery import CallSink, FillStatus, OtpDelivery, Sink
from line.sms.hooks import CALL_TOOL, FILL_TOOL, make_call_handler, register
from line.sms.service import ReleaseStatus, binding_for, release_otp, configure

from .helpers import CALL_DEST, FILL_DEST, MIN, T0
from .test_delivery import CODE, Base, CallRecorder, RecordingSink
from .test_hooks import FakeCtx

OTHER_DEST = Destination.fill("https://other.example")


class Replacement(Base):
    """H1 helpers: replace the approval for a task the way L1 does (forget, then register new terms)."""

    def replace(self, task_id="t1", senders=("OTHER",), dest=OTHER_DEST, minutes=10):
        reg = self.service.registry
        reg.forget(task_id)
        task = ApprovedTask(task_id, senders, T0, T0 + minutes * MIN, destination=dest)
        gen = reg.register(task)
        return task, gen, self.consent.issue(OTP_ACTION, task_id, binding_for(task), 600)


class H1_RetainedSlot(Replacement):
    def retain(self, sink=None):
        d = self.delivery(sink or RecordingSink(fail_first=1))
        self.assertEqual(FillStatus.SINK_FAILED.value, d.fill("t1", self.token, self.consent, ["@wrong"]).status)
        self.assertTrue(d.held("t1"))
        return d

    def test_h1_fill_to_fill_replacement_does_not_move_the_old_code(self):
        d = self.retain()
        self.replace()                                   # sender B / origin B
        self.origin = "https://other.example"            # the page is now approval B's site
        self.assertFalse(d.held("t1"), "replacement drops the sealed code at once")
        res = d.fill("t1", None, self.consent, ["@e8"])  # retry with no token
        self.assertFalse(res.ok, res.status)
        self.assertEqual(1, len(self.sink.calls), "nothing typed after the replacement")

    def test_h1_fill_to_fill_replacement_even_without_the_eager_listener(self):
        d = self.retain()
        self.service.registry._listeners.clear()         # only the generation/binding check can stop it now
        self.replace()
        self.origin = "https://other.example"
        self.assertTrue(d.held("t1"))
        res = d.fill("t1", None, self.consent, ["@e8"])
        self.assertFalse(res.ok, res.status)
        self.assertEqual(1, len(self.sink.calls))
        self.assertFalse(d.held("t1"))

    def test_h1_identical_terms_after_forget_are_a_new_generation_and_drop_the_slot(self):
        d = self.retain()
        old = self.service.registry.generation("t1")
        self.service.registry.forget("t1")
        task = ApprovedTask("t1", ("ACME",), T0, T0 + 10 * MIN, destination=FILL_DEST)
        self.assertGreater(self.service.registry.register(task), old)
        self.assertFalse(d.held("t1"))
        self.assertFalse(d.fill("t1", None, self.consent, ["@e8"]).ok)

    def test_h1_idempotent_registration_keeps_generation_and_slot(self):
        d = self.retain()
        gen = self.service.registry.generation("t1")
        self.assertEqual(gen, self.service.registry.register(self.service.registry.get("t1")))
        self.assertTrue(d.held("t1"))
        self.assertEqual(FillStatus.FILLED.value, d.fill("t1", None, self.consent, ["@e8"]).status)

    def test_h1_fill_to_call_replacement_does_not_send_the_old_code(self):
        d = self.retain()
        rec = CallRecorder()
        d.call_sink = CallSink(rec)
        _, _, tok = self.replace(dest=CALL_DEST)
        res = d.to_call("t1", "call-1", "dtmf", None, self.consent)
        self.assertFalse(res.ok, res.status)
        self.assertEqual([], rec.calls)

    def test_h1_call_to_call_replacement_does_not_send_the_old_code(self):
        rec = CallRecorder(ok=False)
        task = ApprovedTask("tc", ("ACME",), T0, T0 + 10 * MIN, destination=CALL_DEST)
        self.service.registry.register(task)
        tok = self.consent.issue(OTP_ACTION, "tc", binding_for(task), 600)
        d = self.delivery(call_sink=CallSink(rec))
        self.assertEqual(FillStatus.SINK_FAILED.value, d.to_call("tc", "call-1", "dtmf", tok, self.consent).status)
        self.assertTrue(d.held("tc"))
        self.replace("tc", senders=("ACME",), dest=Destination.call("call-2", "+15550100222"))
        rec.ok = True
        for call_id in ("call-1", "call-2"):
            self.assertFalse(d.to_call("tc", call_id, "dtmf", None, self.consent).ok, call_id)
        self.assertEqual(1, len(rec.calls), "only the first, failed attempt ever reached the sink")

    def test_h1_revocation_drops_the_slot_and_blocks_retry(self):
        d = self.retain()
        self.service.registry.forget("t1")
        self.assertFalse(d.held("t1"))
        self.assertEqual(ReleaseStatus.NO_TASK.value, d.fill("t1", None, self.consent, ["@e8"]).status)
        self.assertEqual(1, len(self.sink.calls))

    def test_h1_slot_binding_covers_sender_and_window_not_only_destination(self):
        """Same destination, different sender/window: still a different approval."""
        d = self.retain()
        self.service.registry._listeners.clear()
        self.replace(senders=("EVIL",), dest=FILL_DEST)
        self.assertFalse(d.fill("t1", None, self.consent, ["@e8"]).ok)
        self.assertEqual(1, len(self.sink.calls))

    def test_h1_concurrent_replacement_waits_for_an_in_flight_delivery(self):
        """Validation and delivery are one step against replacement: the replacement cannot land between them."""
        entered, release = threading.Event(), threading.Event()

        class Slow(RecordingSink):
            def fill(s, secret, target, *, task_id=None):
                entered.set()
                assert release.wait(5)
                return super().fill(secret, target, task_id=task_id)

        d = self.delivery(Slow())
        out = {}
        t = threading.Thread(target=lambda: out.update(res=d.fill("t1", self.token, self.consent, ["@e7"])))
        t.start()
        self.assertTrue(entered.wait(5))
        replaced = threading.Event()
        r = threading.Thread(target=lambda: (self.replace(), replaced.set()))
        r.start()
        self.assertFalse(replaced.wait(0.3), "replacement must wait for the delivery in flight")
        release.set()
        t.join(5)
        r.join(5)
        self.assertEqual(FillStatus.FILLED.value, out["res"].status)
        self.assertTrue(replaced.is_set())
        self.assertEqual(self.service.registry.get("t1").sender_rules, ("OTHER",))
        self.assertFalse(d.held("t1"))

    def test_h1_replacement_during_the_pre_typing_probe_cannot_redirect_the_code(self):
        """The retry is blocked in its pre-typing origin probe; a replacement started now must queue behind it."""
        d = self.retain()
        in_probe, go = threading.Event(), threading.Event()
        calls = {"n": 0}

        def probe(browser_task_id=None):
            calls["n"] += 1
            if calls["n"] == 2:          # the pre-typing probe (1 = the early check)
                in_probe.set()
                assert go.wait(5)
            return "https://example.com"

        d.origin_probe = probe
        out = {}
        t = threading.Thread(target=lambda: out.update(res=d.fill("t1", None, self.consent, ["@e8"])))
        t.start()
        self.assertTrue(in_probe.wait(5))
        done = threading.Event()
        r = threading.Thread(target=lambda: (self.replace(), done.set()))
        r.start()
        self.assertFalse(done.wait(0.3))
        go.set()
        t.join(5)
        r.join(5)
        self.assertEqual(FillStatus.FILLED.value, out["res"].status)
        # the typing happened while approval A was still the registered one: A's page, A's code, A's consent
        self.assertEqual(2, len(self.sink.calls))

    def test_h1_concurrent_replacement_stress_never_types_into_an_unapproved_site(self):
        sites = ["https://a.example", "https://b.example", "https://c.example"]
        d = self.delivery(RecordingSink(fail_first=0))
        page = {"origin": sites[0]}
        violations = []

        class Checking(Sink):
            def fill(s, secret, target, *, task_id=None):
                cur = d.service.registry.get("t1")
                if cur is None or cur.destination.origin != page["origin"]:
                    violations.append((cur and cur.destination.origin, page["origin"]))
                return True

        d.sink = Checking()
        d.origin_probe = lambda bt=None: page["origin"]
        stop = threading.Event()
        rng = random.Random(7)

        def replacer():
            n = 0
            while not stop.is_set():
                site = sites[n % 3]
                n += 1
                task = ApprovedTask("t1", ("ACME",), T0, T0 + 10 * MIN, destination=Destination.fill(site))
                with d.service.registry.gate:
                    d.service.registry.forget("t1")
                    d.service.registry.register(task)
                    page["origin"] = site       # the owner moves the browser to the newly approved site
                self.tokens_for = self.consent.issue(OTP_ACTION, "t1", binding_for(task), 600)

        def filler():
            for _ in range(200):
                tok = getattr(self, "tokens_for", None)
                d.fill("t1", tok, self.consent, ["@e7"])

        th = [threading.Thread(target=replacer)] + [threading.Thread(target=filler) for _ in range(4)]
        for t in th:
            t.start()
        for t in th[1:]:
            t.join(30)
        stop.set()
        th[0].join(5)
        self.assertEqual([], violations)


    def test_h1_replacement_while_the_phone_is_being_read_is_refused_at_commit(self):
        """The approval is replaced after the dry-run match but before consent is spent: nothing is released."""
        from line.sms import FixtureSource

        reg = self.service.registry
        replaced = []
        src = FixtureSource([SmsMessage("1", "ACME", T0 + MIN, "Your ACME code is %s." % CODE, source="fixture")], clock=self.clock)
        orig = src.fetch

        def fetch(**kw):
            msgs = orig(**kw)
            if not replaced:
                replaced.append(self.replace(senders=("ACME",), dest=FILL_DEST))   # same terms, new generation
            return msgs

        src.fetch = fetch
        self.service.source = src
        d = self.delivery()
        res = d.fill("t1", self.token, self.consent, ["@e7"])
        self.assertEqual(ReleaseStatus.APPROVAL_CHANGED.value, res.status)
        self.assertEqual([], self.sink.calls)
        self.assertFalse(d.held("t1"))
        _, gen, new_token = replaced[0]
        self.assertEqual(FillStatus.FILLED.value, d.fill("t1", new_token, self.consent, ["@e7"], approval_generation=gen).status,
                         "the replacement approval has its own unspent consent")


class M1_ApprovalGeneration(Replacement):
    def test_m1_release_refuses_a_generation_that_is_not_the_registered_one(self):
        d = self.delivery()
        gen = self.service.registry.generation("t1")
        fetches = self.source.fetch_calls
        for bad in (gen + 1, gen - 1, -1):
            res = d.fill("t1", self.token, self.consent, ["@e7"], approval_generation=bad)
            self.assertEqual(ReleaseStatus.APPROVAL_CHANGED.value, res.status, bad)
        self.assertEqual(fetches, self.source.fetch_calls, "refused before the phone is read")
        self.assertEqual([], self.sink.calls)
        self.assertEqual(FillStatus.FILLED.value, d.fill("t1", self.token, self.consent, ["@e7"], approval_generation=gen).status,
                         "the refusals did not spend the consent")

    def test_m1_slot_generation_must_match_the_pending_one(self):
        d = self.delivery(RecordingSink(fail_first=1))
        gen = self.service.registry.generation("t1")
        d.fill("t1", self.token, self.consent, ["@e7"], approval_generation=gen)
        self.assertTrue(d.held("t1"))
        res = d.fill("t1", None, self.consent, ["@e7"], approval_generation=gen + 5)
        self.assertEqual(ReleaseStatus.APPROVAL_CHANGED.value, res.status)
        self.assertEqual(1, len(self.sink.calls))

    def test_m1_call_target_checks_the_generation_too(self):
        rec = CallRecorder()
        task = ApprovedTask("tc", ("ACME",), T0, T0 + 10 * MIN, destination=CALL_DEST)
        gen = self.service.registry.register(task)
        tok = self.consent.issue(OTP_ACTION, "tc", binding_for(task), 600)
        d = self.delivery(call_sink=CallSink(rec))
        self.assertEqual(ReleaseStatus.APPROVAL_CHANGED.value,
                         d.to_call("tc", "call-1", "dtmf", tok, self.consent, approval_generation=gen + 1).status)
        self.assertEqual([], rec.calls)
        self.assertEqual(FillStatus.SENT.value, d.to_call("tc", "call-1", "dtmf", tok, self.consent, approval_generation=gen).status)

    def test_m1_release_otp_contract_entry_takes_the_generation(self):
        gen = self.service.registry.generation("t1")
        configure(self.service)
        self.addCleanup(configure, None)
        self.assertIsNone(release_otp("t1", self.token, self.consent, approval_generation=gen + 1))
        self.assertEqual(CODE, release_otp("t1", self.token, self.consent, approval_generation=gen).reveal())

    def test_m1_tool_handlers_pass_the_engines_pending_generation(self):
        rec = CallRecorder()
        task = ApprovedTask("tc", ("ACME",), T0, T0 + 10 * MIN, destination=CALL_DEST)
        gen = self.service.registry.register(task)
        tok = self.consent.issue(OTP_ACTION, "tc", binding_for(task), 600)
        d = self.delivery(call_sink=CallSink(rec))
        pending = {"tc": gen + 1}                                # the engine's pending approval is a different one
        h = make_call_handler(d, self.consent, lambda t: tok, lambda t: pending.get(t))
        args = {"task_id": "tc", "call_id": "call-1", "mode": "dtmf"}
        self.assertEqual("approval_changed", json.loads(h(args))["status"])
        pending.clear()                                           # unknown pending approval: refuse as well
        self.assertEqual("approval_changed", json.loads(h(args))["status"])
        pending["tc"] = gen
        self.assertEqual("sent", json.loads(h(args))["status"])
        self.assertEqual(1, len(rec.calls))


class N1_CallSinkDoesNotHoldTheGate(Replacement):
    """R1-delta N1: `registry.gate` is a blocking lock; the call sink waits for the service loop, which may itself
    be replacing an approval. The gate is released before the sink runs and the generation travels to the sink."""

    def setUp(self):
        super().setUp()
        self.ctask = ApprovedTask("tc", ("ACME",), T0, T0 + 10 * MIN, destination=CALL_DEST)
        self.gen = self.service.registry.register(self.ctask)
        self.ctoken = self.consent.issue(OTP_ACTION, "tc", binding_for(self.ctask), 600)

    def test_n1_a_sink_that_needs_a_second_thread_to_take_the_gate_completes(self):
        got = {}

        def send(call_id, secret, mode, approval_generation=None):
            done = threading.Event()
            # stands in for the service loop: it replaces the approval (needs the gate) while the sink waits for it
            t = threading.Thread(target=lambda: (self.service.registry.forget("tc"), done.set()))
            t.start()
            got["completed"] = done.wait(3)
            t.join(3)
            return True

        d = self.delivery(call_sink=CallSink(send))
        res = d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent)
        self.assertTrue(got["completed"], "the gate was held across the sink: the replacement could not run")
        self.assertEqual(FillStatus.SENT.value, res.status)
        self.assertFalse(d.held("tc"))

    def test_n1_the_gate_is_free_while_the_sink_runs(self):
        seen = {}

        def send(call_id, secret, mode, approval_generation=None):
            out = []
            t = threading.Thread(target=lambda: out.append(self.service.registry.gate.acquire(timeout=1) and
                                                           (self.service.registry.gate.release() or True)))
            t.start()
            t.join(3)
            seen["gate_free"] = bool(out and out[0])
            return True

        self.delivery(call_sink=CallSink(send)).to_call("tc", "call-1", "speak", self.ctoken, self.consent)
        self.assertTrue(seen["gate_free"])

    def test_n1_the_approval_generation_is_passed_to_the_sink(self):
        calls = []

        def send(call_id, secret, mode, approval_generation=None):
            calls.append((call_id, mode, approval_generation))
            return True

        self.delivery(call_sink=CallSink(send)).to_call("tc", "call-1", "dtmf", self.ctoken, self.consent)
        self.assertEqual([("call-1", "dtmf", self.gen)], calls)

    def test_n1_a_retry_passes_the_slots_generation_and_never_a_newer_one(self):
        calls = []
        ok = {"v": False}

        def send(call_id, secret, mode, approval_generation=None):
            calls.append(approval_generation)
            return ok["v"]

        d = self.delivery(call_sink=CallSink(send))
        self.assertEqual(FillStatus.SINK_FAILED.value, d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent).status)
        ok["v"] = True
        self.assertEqual(FillStatus.SENT.value, d.to_call("tc", "call-1", "dtmf", None, self.consent).status)
        self.assertEqual([self.gen, self.gen], calls)

    def test_n1_a_sink_that_cannot_take_the_generation_is_refused_and_nothing_is_released(self):
        calls = []

        def legacy(call_id, secret, mode):          # the old three-argument contract
            calls.append((call_id, mode))
            return True

        fetches = self.source.fetch_calls
        d = self.delivery(call_sink=CallSink(legacy))
        res = d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent)
        self.assertEqual(FillStatus.SINK_UNSUPPORTED.value, res.status)
        self.assertEqual([], calls)
        self.assertEqual(fetches, self.source.fetch_calls, "refused before the phone is read")
        self.assertFalse(d.held("tc"))
        self.assertFalse(CallSink(legacy).send("call-1", SecretText(CODE), "dtmf", approval_generation=self.gen),
                         "CallSink itself refuses a legacy sink")

    def test_n1_legacy_wrapper_replacement_interleaving_sends_nothing(self):
        """The R1-delta2 reproduction: a legacy wrapper pauses after validation, the approval is replaced, the wrapper
        resumes. It is never called, so the old code cannot be adopted by the new approval."""
        calls, paused = [], threading.Event()

        def legacy(call_id, secret, mode):
            calls.append(call_id)
            return True

        d = self.delivery(call_sink=CallSink(legacy))
        orig = d.call_sink.accepts_generation

        def accepts():
            paused.set()
            self.replace("tc", senders=("EVIL",), dest=CALL_DEST)    # replaced exactly where the wrapper would have paused
            return orig()

        d.call_sink.accepts_generation = accepts
        res = d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent)
        self.assertTrue(paused.is_set())
        self.assertFalse(res.ok, res.status)
        self.assertEqual([], calls)

    def test_n1_a_missing_generation_is_refused_by_the_call_sink(self):
        calls = []
        sink = CallSink(lambda c, s, m, approval_generation=None: calls.append(approval_generation) or True)
        for gen in (None, True, "3"):
            self.assertFalse(sink.send("call-1", SecretText(CODE), "dtmf", approval_generation=gen), gen)
        self.assertEqual([], calls)
        self.assertTrue(sink.send("call-1", SecretText(CODE), "dtmf", approval_generation=4))
        self.assertEqual([4], calls)

    def test_n1_replacement_during_the_send_keeps_the_newer_approvals_slot(self):
        """A send in flight finishes after the approval was replaced and a newer slot exists: only its own slot goes."""
        from line.sms.delivery import _Slot

        holder = {}

        def send(call_id, secret, mode, approval_generation=None):
            _, gen, _ = self.replace("tc", senders=("ACME",), dest=CALL_DEST)      # replacement drops the old slot
            holder["new"] = _Slot(SecretText("111111"), 10 ** 12, 4, "A***ME", gen,
                                  self.service.registry.get_entry("tc").binding)   # the newer approval's slot now exists
            with d._lock:
                d._slots["tc"] = holder["new"]
            return True

        d = self.delivery(call_sink=CallSink(send))
        res = d.to_call("tc", "call-1", "dtmf", self.ctoken, self.consent)
        self.assertEqual(FillStatus.SENT.value, res.status)
        self.assertIs(holder["new"], d._slots.get("tc"), "the finished send must not drop the newer approval's slot")

    def test_n1_browser_fill_still_serializes_against_replacement(self):
        """Browser locking is separate and unchanged: the gate is held across the (worker-thread) browser sink."""
        entered, release = threading.Event(), threading.Event()

        class Slow(RecordingSink):
            def fill(s, secret, target, *, task_id=None):
                entered.set()
                assert release.wait(5)
                return super().fill(secret, target, task_id=task_id)

        d = self.delivery(Slow())
        t = threading.Thread(target=lambda: d.fill("t1", self.token, self.consent, ["@e7"]))
        t.start()
        self.assertTrue(entered.wait(5))
        replaced = threading.Event()
        r = threading.Thread(target=lambda: (self.replace(), replaced.set()))
        r.start()
        self.assertFalse(replaced.wait(0.3))
        release.set()
        t.join(5)
        r.join(5)
        self.assertTrue(replaced.is_set())


class ProbeSequence:
    def __init__(self, *origins):
        self.origins = list(origins)
        self.calls = 0

    def __call__(self, browser_task_id=None):
        self.calls += 1
        return self.origins.pop(0) if len(self.origins) > 1 else self.origins[0]


class BF_BrowserFill(Base):
    A, B = "https://example.com", "https://evil.example.net"

    def test_bf_browser_fill_is_off_by_default_and_the_tool_is_not_registered(self):
        d = OtpDelivery(self.service, RecordingSink(), origin_probe=lambda t=None: self.A, clock=self.clock)
        self.assertFalse(d.otp_browser_fill)
        self.assertEqual(FillStatus.DISABLED.value, d.fill("t1", self.token, self.consent, ["@e7"]).status)
        ctx = FakeCtx()
        register(ctx, d, self.consent, lambda t: self.token)
        self.assertEqual({CALL_TOOL}, set(ctx.tools), "the call target stays on")
        d.otp_browser_fill = True
        ctx2 = FakeCtx()
        register(ctx2, d, self.consent, lambda t: self.token)
        self.assertEqual({CALL_TOOL, FILL_TOOL}, set(ctx2.tools))

    def test_bf_origin_changed_right_before_typing_types_nothing_and_keeps_the_sealed_code(self):
        probe = ProbeSequence(self.A, self.B, self.A)           # early check, pre-typing re-probe, later retry
        d = self.delivery(origin_probe=probe)
        res = d.fill("t1", self.token, self.consent, ["@e7"])
        self.assertEqual(FillStatus.ORIGIN_CHANGED.value, res.status)
        self.assertEqual([], self.sink.calls)
        self.assertTrue(d.held("t1"))

    def test_bf_origin_changed_right_after_typing_reports_it_and_burns_the_slot(self):
        probe = ProbeSequence(self.A, self.A, self.B)            # early, pre-typing, post-typing
        d = self.delivery(origin_probe=probe)
        res = d.fill("t1", self.token, self.consent, ["@e7"])
        self.assertEqual(FillStatus.ORIGIN_CHANGED.value, res.status)
        self.assertEqual(1, len(self.sink.calls))
        self.assertFalse(d.held("t1"), "the code may have reached another page: no retry from this slot")
        self.assertNotIn(CODE, res.to_json())

    def test_bf_origin_unreadable_after_typing_counts_as_changed(self):
        probe = ProbeSequence(self.A, self.A, None)
        d = self.delivery(origin_probe=probe)
        self.assertEqual(FillStatus.ORIGIN_CHANGED.value, d.fill("t1", self.token, self.consent, ["@e7"]).status)

    def test_bf_origin_changes_between_boxes_stops_typing(self):
        probe = ProbeSequence(self.A, self.A, self.A, self.A, self.B)     # early, pre, box 2, box 3, box 4 -> B
        d = self.delivery(origin_probe=probe)
        res = d.fill("t1", self.token, self.consent, ["@a", "@b", "@c", "@d", "@e", "@f"])
        self.assertEqual(FillStatus.ORIGIN_CHANGED.value, res.status)
        self.assertEqual(3, len(self.sink.calls), "typing stopped at the box where the origin changed")
        self.assertFalse(d.held("t1"))

    def test_bf_stable_origin_fills_and_probes_around_the_typing(self):
        probe = ProbeSequence(self.A)
        d = self.delivery(origin_probe=probe)
        self.assertEqual(FillStatus.FILLED.value, d.fill("t1", self.token, self.consent, ["@e7"]).status)
        self.assertEqual(3, probe.calls, "early check, immediately before typing, immediately after")


if __name__ == "__main__":
    unittest.main()
