"""Round 34: R1b delta 31 (N30-R). Every business utterance is stamped once at creation with its start time (first
partial or final arrival, whichever is earlier); the stamp lives on the utterance object (``SpokenUtterance``) and goes
with it through the normal, timeout, late-verdict and (round 35: stall) paths. The clear-yes check and every owner ring
compare it with the send time of our latest live-person question: only an utterance that started after the question
was sent can confirm. No session markers. Real time; the reviewer probe uses the real detector."""
import time
import unittest
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import deps
from plugin.line.engine.machine import CallSession, SpokenUtterance
from test_r16c_fallback_question import Q

GREETING = "Hello, this is Dana. How can I help?"
SHORT_WINDOW = {"confirm_reply_window_s": 0.05, "confirm_cooldown_s": 60.0}


def make(strict=True, quiet=0.0, **over):
    line = Line(reply=lambda d, t: ["UNAUTHORIZED MODEL TEXT"], quiet_before_speak_s=quiet, **over)
    if strict:
        req = line.request(mode="owner", title="Delta reservations", goal="Ask about the bag fee.",
                           fallback_question=Q, owner_ring_timeout_s=20, intro_names=["Delta"])
    else:
        req = line.request(mode="owner")
    line.approve(req["task_id"])
    created = wait_for(lambda: line.adapter.created and line.adapter.created[-1])
    session = wait_for(lambda: line.session(req["call_id"]) and line.session(req["call_id"]).provider_sid
                       and line.session(req["call_id"]))
    relay = line.connect(req, created)
    wait_for(lambda: session.setup_seen)
    return line, req, session, relay


def partial(relay, text):
    relay.send({"type": "prompt", "voicePrompt": text, "lang": "en-US", "last": False})


def questions(relay):
    return [t for t in relay.texts() if "live person?" in t]


def owner_calls(line):
    return [c for c in line.adapter.created if c["to"] == line.cfg.owner_number]


def kinds(line, req):
    return [e["kind"] for e in line.store.events(req["call_id"])]


def scripted(table):
    """A detector stub: business text -> (delay s, label). Anything else: unknown at once."""
    class Scripted:
        def __init__(self, ask_model=None):
            pass

        def update(self, utt, context=None):
            if getattr(utt, "speaker", "business") == "business" and utt.text in table:
                delay, label = table[utt.text]
                time.sleep(delay)
                return deps.Detection(label, "high", ("scripted",))
            return deps.Detection("unknown", "low", ("scripted",))
        feed = update
    return mock.patch.object(deps, "HoldTracker", Scripted)


def delayed_real(delay):
    """The real HoldTracker, with business utterances held back at the worker boundary (the reviewer's probe)."""
    real = deps.HoldTracker

    class Delayed(real):
        def update(self, utt, context=None):
            if getattr(utt, "speaker", "business") == "business":
                time.sleep(delay)
            return real.update(self, utt, context)
    return mock.patch.object(deps, "HoldTracker", Delayed)


class ReviewerProbeTests(unittest.TestCase):
    """N30-R inverted: partial "Yes" stalls, question sent, then the final "Yes" that completes the partial. With the
    detector slower than the 2.5 s reply budget, the late HUMAN verdict keeps the utterance's stamp and never rings."""

    def run_probe(self, delay):
        with delayed_real(delay):
            line, req, session, relay = make(quiet=1.2)
            self.addCleanup(line.close)
            self.assertEqual(session.cfg.reply_budget_s, 2.5)
            partial(relay, "Yes")
            wait_for(lambda: questions(relay), timeout=CallSession.STALL_AFTER_S + 8, what="the question")
            relay.prompt("Yes")  # the partial's own final
            time.sleep(delay + 2.5)
        return line, req, session, relay

    def test_N30R_a_delayed_late_human_verdict_never_rings(self):
        line, req, session, relay = self.run_probe(3.0)
        self.assertIn("detect_slow", kinds(line, req))
        self.assertEqual(owner_calls(line), [])
        self.assertFalse(session.human_confirmed or session.yes_confirmed)
        self.assertEqual(len(questions(relay)), 1)
        whys = [e["data"].get("why") for e in line.store.events(req["call_id"])]
        self.assertTrue({"the utterance started before our question", "asked for this utterance"} & set(whys), whys)

    def test_N30R_the_immediate_control_never_rings(self):
        line, req, session, relay = self.run_probe(0.0)
        self.assertEqual(owner_calls(line), [])
        self.assertFalse(session.human_confirmed or session.yes_confirmed)


class RouteTests(unittest.TestCase):
    """Each route: a yes that started before our question never rings; a yes that started after it rings."""

    def ask_while_yes_starts(self, table, strict=True, **over):
        """The greeting's detection is slow, so the timeout path asks the question at ~0.5 s; a partial "Yes." starts
        at 0.2 s, before the question was sent, and its final comes after."""
        line, req, session, relay = make(strict=strict, reply_budget_s=0.5, **over)
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        time.sleep(0.2)
        partial(relay, "Yes.")
        wait_for(lambda: questions(relay), timeout=4, what="the question")
        relay.prompt(table["final"])
        return line, req, session, relay

    def ask_then(self, text, strict=True, **over):
        line, req, session, relay = make(strict=strict, reply_budget_s=0.5, **over)
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        wait_for(lambda: questions(relay), timeout=4, what="the question")
        time.sleep(0.1)
        relay.prompt(text)  # starts after the question was sent
        return line, req, session, relay

    def never_rings(self, line, session, wait):
        time.sleep(wait)
        self.assertEqual(owner_calls(line), [])
        self.assertFalse(session.human_confirmed or session.yes_confirmed)

    def rings(self, line, req):
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        self.assertEqual(len(owner_calls(line)), 1)

    # normal route: a fast HUMAN verdict
    def test_R34_normal_route(self):
        table = {GREETING: (0.8, "unknown"), "Yes.": (0.0, "human"), "final": "Yes."}
        with scripted(table):
            line, req, session, relay = self.ask_while_yes_starts(table)
            self.never_rings(line, session, 1.5)
            self.assertIn("the utterance started before our question",
                          [e["data"].get("why") for e in line.store.events(req["call_id"])])
        with scripted(table):
            line, req, session, relay = self.ask_then("Yes.")
            self.rings(line, req)

    # timeout route: detection slower than the reply budget; the timeout path decides
    def test_R34_timeout_route(self):
        table = {GREETING: (0.8, "unknown"), "Yes.": (1.2, "human"), "final": "Yes."}
        with scripted(table):
            line, req, session, relay = self.ask_while_yes_starts(table)
            self.never_rings(line, session, 2.5)
            self.assertIn("detect_slow", kinds(line, req))
        with scripted(table):
            line, req, session, relay = self.ask_then("Yes.")
            self.rings(line, req)
            self.assertIn("detect_slow", kinds(line, req))

    # late-verdict route: an unrestricted call, where a late HUMAN verdict alone confirms. The reply window is short,
    # so the timeout path neither confirms nor re-asks (a re-ask would be a later question: the latest one counts)
    def test_R34_late_verdict_route(self):
        text = "This is Dana speaking."  # not a yes: only the late verdict can confirm
        table = {GREETING: (0.8, "unknown"), text: (1.2, "human"), "final": text}
        with scripted(table):
            line, req, session, relay = self.ask_while_yes_starts(table, strict=False, **SHORT_WINDOW)
            self.assertFalse(session._strict_confirmation())
            self.never_rings(line, session, 2.5)
            self.assertIn("human", [e["data"].get("label") for e in line.store.events(req["call_id"])
                                    if e["kind"] == "detect" and e["data"].get("late")])
        with scripted(table):
            line, req, session, relay = self.ask_then(text, strict=False, **SHORT_WINDOW)
            self.rings(line, req)
            self.assertIn("human", [e["data"].get("label") for e in line.store.events(req["call_id"])
                                    if e["kind"] == "detect" and e["data"].get("late")])

    # stall route (round 35, replacing promotion): the final carries the stamp of its earliest pending partial
    def test_R34_stall_route(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        partial(relay, "Yes.")  # before any question: the stall leads to the question
        wait_for(lambda: questions(relay), timeout=CallSession.STALL_AFTER_S + 3, what="the question")
        relay.prompt("Yes.")
        self.never_rings(line, session, 1.5)
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        wait_for(lambda: questions(relay), timeout=4)
        time.sleep(0.1)
        partial(relay, "Yes.")  # after the question: the partial decides nothing, its final decides
        wait_for(lambda: "partials_stalled" in kinds(line, req), timeout=CallSession.STALL_AFTER_S + 3)
        time.sleep(0.5)
        self.assertFalse(session.human_confirmed)
        relay.prompt("Yes.")
        self.rings(line, req)


class StampTests(unittest.TestCase):
    def test_R34_the_stamp_is_the_first_partial_or_the_final(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        seen = []
        orig = session._on_speech

        async def spy(raw, rx=None, *, utt=None):
            seen.append(utt)
            await orig(raw, rx, utt=utt)
        session._on_speech = spy
        t0 = time.monotonic()
        partial(relay, "Thanks")
        time.sleep(0.4)
        relay.prompt("Thanks for calling.")
        t1 = time.monotonic()
        time.sleep(0.4)
        relay.prompt("Hello?")  # no partial: the final's arrival
        wait_for(lambda: len(seen) == 2)
        self.assertIsInstance(seen[0], SpokenUtterance)
        self.assertLess(seen[0].started_at - t0, 0.2)  # the first partial, not the final
        self.assertGreater(seen[1].started_at, t1)
        with self.assertRaises(Exception):
            seen[0].started_at = 0.0  # frozen: the stamp cannot be changed on the way

    def test_R34_the_relay_cannot_supply_a_stamp(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        wait_for(lambda: questions(relay), timeout=4)
        relay.send({"type": "prompt", "voicePrompt": "Yes.", "lang": "en-US", "last": True,
                    "_started_at": -1e12})
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)  # an ordinary final, stamped on arrival

    def test_R36_an_abandoned_partial_is_cleared_only_by_a_final(self):
        # Round 36 (N32-R): no timer, no reset. A stale pending partial from before the question stamps the next
        # final, with or without a fresh partial, so that final cannot confirm; the final after it can.
        for fresh_partial in (False, True):
            line, req, session, relay = make()
            self.addCleanup(line.close)
            relay.prompt(GREETING)
            wait_for(lambda: questions(relay), timeout=4)
            session.utt_first_partial_at = time.monotonic() - 50.0  # a stale partial from before the question
            if fresh_partial:
                partial(relay, "Yes")
                time.sleep(0.1)
            relay.prompt("Yes.")
            time.sleep(1.5)
            self.assertEqual(owner_calls(line), [])
            relay.prompt("Yes.")  # the next final starts after the question
            self.rings(line, req)

    rings = RouteTests.rings

    def test_R34_no_session_markers(self):
        import inspect
        src = inspect.getsource(CallSession)
        self.assertNotIn("predates_question =", src)
        self.assertNotIn("provisional_now", src)
        self.assertNotIn("promoted", src)  # round 35: no promotion of any kind

    def test_R34_ring_owner_compares_the_confirming_stamp(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        wait_for(lambda: questions(relay), timeout=4)
        session.human_confirmed = session.yes_confirmed = True
        session.confirmed_utt = SpokenUtterance("Yes.", session.last_question_sent - 1.0)  # before the question
        self.assertFalse(line.svc.run(session.ring_owner("human_detected")))
        self.assertFalse(line.svc.run(session.ring_owner("agent_request")))
        self.assertIn("no utterance after our live-person question confirmed a person",
                      [e["data"].get("why") for e in line.store.events(req["call_id"])
                       if e["kind"] == "owner_ring_refused"])
        self.assertEqual(owner_calls(line), [])


if __name__ == "__main__":
    unittest.main()
