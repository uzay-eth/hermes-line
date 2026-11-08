"""Round 33: R1b delta 30 (N30, N31), the reviewer's probes inverted.

N30: partial text decides nothing; a final is never dropped and every decision that needs one (clear yes, ring,
phrase, unsafe-offer silence) comes from it. Round 35: promotion is gone. Stalled partials (no final for 2.5 s) only get
the fixed question from the engine (the "stall" helper below, formerly "promote"); a final carries the stamp of its
earliest pending partial, so a final whose partials began before our question is no reply to it.

N31: one global counter for the live-person question across every route, counted in the send path when the frame is
actually sent: on strict calls one every 20 s and 4 per call. Real time, real detector."""
import time
import unittest
from unittest import mock

from harness import Line, wait_for
from plugin.line.engine import assistant
from plugin.line.engine.machine import CallSession
from test_r16c_fallback_question import Q


async def _round43c_reply_rule_off(self, *args, **kwargs):
    """Round 43c (MIT-018 STEER-4): the reply rule (ask twice per party, then the owner judges) replaces the 20 s
    no-re-ask rule for replies to our question. This budget test checks the global question budget for everything
    the reply rule does not decide, so it runs with that rule switched off."""
    return False

GREETING = "Hello, this is Dana. How can I help?"
BOT = "I'm Delta's automated assistant."
CONNECTING = "Thank you. I'm an AI assistant calling for Sam. Please hold for just a moment while I connect Sam."


def make(quiet=1.2, **over):
    line = Line(reply=lambda d, t: ["UNAUTHORIZED MODEL TEXT"], quiet_before_speak_s=quiet, **over)
    req = line.request(mode="owner", title="Delta reservations", goal="Ask about the bag fee.", fallback_question=Q,
                       owner_ring_timeout_s=20, intro_names=["Delta", "Delta Air Lines"], ivr_category="baggage")
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


def kinds(line, req):
    return [e["kind"] for e in line.store.events(req["call_id"])]


def owner_calls(line):
    return [c for c in line.adapter.created if c["to"] == line.cfg.owner_number]


def skips(line, req):
    """Why a question was not asked: the global check, or the new-party route's own view of the same counter."""
    return [e["data"].get("why") for e in line.store.events(req["call_id"])
            if e["kind"] in ("question_budget_skip", "new_party_question_skipped")]


def stall(line, req, relay, text):
    """A partial with no final: after STALL_AFTER_S the engine notes the stall (and may ask the fixed question)."""
    before = kinds(line, req).count("partials_stalled")
    partial(relay, text)
    wait_for(lambda: kinds(line, req).count("partials_stalled") > before, timeout=CallSession.STALL_AFTER_S + 3,
             what="the stall")


class PromotedYesTests(unittest.TestCase):
    """Reviewer N30 table: question sent, a partial "Yes" stalls, then a correcting final. It never rings."""

    # Round 43c (MIT-018 STEER-4): "Yes, how can I help?" is now a clear yes (an explicit affirmation, any words after
    # it); it is tested to ring below. The others still never ring.
    CORRECTIONS = ("Yes, I'm not a live person.", "Yes, would you like to make a payment?",
                   "Yesterday I called about my bag.", "No, I am an automated assistant.")

    def asked(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        wait_for(lambda: questions(relay), timeout=6, what="the question")
        return line, req, session, relay

    def test_N30_a_stalled_partial_yes_with_a_contradicting_final_never_rings(self):
        for final in self.CORRECTIONS:
            with self.subTest(final=final):
                line, req, session, relay = self.asked()
                stall(line, req, relay, "Yes")
                self.assertFalse(session.human_confirmed)  # the partial "Yes" decided nothing
                self.assertFalse(session.yes_confirmed)
                self.assertNotIn("Yes", [t["text"] for t in line.store.transcript(req["call_id"])])
                relay.prompt(final)  # the real final: processed, never dropped
                time.sleep(2.0)
                self.assertNotIn("duplicate_final_dropped", kinds(line, req))
                self.assertIn(final, [t["text"] for t in line.store.transcript(req["call_id"])])
                self.assertEqual(owner_calls(line), [])
                self.assertFalse(session.human_confirmed)
                self.assertFalse(session.yes_confirmed)
                self.assertNotIn(CONNECTING, relay.texts())
                self.assertLessEqual(len(questions(relay)), 2)  # round 43c: at most the one re-ask, never a ring

    def test_R43c_a_stalled_partial_yes_then_yes_how_can_i_help_rings(self):
        line, req, session, relay = self.asked()
        stall(line, req, relay, "Yes")
        self.assertFalse(session.human_confirmed)  # the partial "Yes" alone still decides nothing
        relay.prompt("Yes, how can I help?")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)

    def test_N30_a_stalled_partial_yes_alone_never_confirms(self):
        line, req, session, relay = self.asked()
        stall(line, req, relay, "Yes")
        time.sleep(2.0)
        self.assertEqual(owner_calls(line), [])
        self.assertFalse(session.human_confirmed or session.yes_confirmed)
        self.assertEqual(relay.texts()[-1], questions(relay)[-1])  # nothing said after the question

    def test_N30_the_identical_final_still_decides_the_clear_yes(self):
        line, req, session, relay = self.asked()
        stall(line, req, relay, "Yes.")
        self.assertFalse(session.human_confirmed)
        relay.prompt("Yes.")  # the identical final: the clear yes is taken from it
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        self.assertTrue(session.yes_confirmed)
        self.assertEqual(len(owner_calls(line)), 1)

    def test_N30_a_yes_said_before_our_question_is_not_its_reply(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        stall(line, req, relay, "Yes")  # no question yet: the stall leads to it
        wait_for(lambda: questions(relay), timeout=6, what="the question")
        relay.prompt("Yes")  # the partial's own final, said before that question
        time.sleep(2.0)
        self.assertEqual(owner_calls(line), [])
        self.assertFalse(session.human_confirmed or session.yes_confirmed)
        self.assertEqual(len(questions(relay)), 1)  # and no second question for the same utterance
        self.assertIn("the utterance started before our question",  # round 34: its stamp predates the question
                      [e["data"].get("why") for e in line.store.events(req["call_id"])])
        relay.prompt("Yes.")  # a reply after the question: the ordinary path rings
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)


class UnsafeOfferTests(unittest.TestCase):
    """Reviewer N30 assistant counterpart: the final that extends the partial with an unsafe offer stays silent."""

    def run_case(self, part, final):
        with mock.patch.object(assistant, "MIN_SPACING_S", 0.5):  # spacing out of the way: the silence is the rule's
            line, req, session, relay = make()
            self.addCleanup(line.close)
            relay.prompt(BOT)
            relay.wait_text("Representative.", timeout=6)
            time.sleep(0.6)
            stall(line, req, relay, part)
            time.sleep(1.8)  # the partial got no phrase (at most the engine's fixed question)
            self.assertEqual(len(session.assistant_said), 1, session.assistant_said)
            relay.prompt(final)
            time.sleep(2.5)
        said = relay.texts()
        self.assertEqual(len(session.assistant_said), 1, session.assistant_said)
        self.assertFalse(any(t in said for t in ("Baggage question.", "Agent.", "Speak to an agent.")), said)
        self.assertLessEqual(len(questions(relay)), 1)  # round 35: the stall question, nothing else
        # Round 35: after the stall question the final may read as a possible new party (silence, no re-ask) rather
        # than as the bot (the unsafe-offer rule); either way the final gets no phrase.
        self.assertEqual([e for e in line.store.events(req["call_id"]) if e["kind"] == "ivr_action"][1:], [])
        self.assertEqual(owner_calls(line), [])

    def test_N30_payment_offer_after_a_stalled_prefix_is_silent(self):
        self.run_case("How can I help", "How can I help you make a payment?")

    def test_N30_callback_offer_after_a_stalled_prefix_is_silent(self):
        self.run_case("Would you like", "Would you like us to call you back?")


class GlobalBudgetTests(unittest.TestCase):
    """N31: one counter, counted when the frame is sent, every route (generic, stall, new party, re-ask)."""

    def test_N31_a_four_attempt_stimulus_sends_at_most_four_questions_counted_four(self):
        with mock.patch.object(CallSession, "NEW_PARTY_QUESTION_EVERY_S", 1.5), \
                mock.patch.object(CallSession, "STALL_AFTER_S", 0.6):
            # the reviewer's stimulus scaled down: each attempt comes after the reply window and the spacing
            line, req, session, relay = make(quiet=0.2, confirm_reply_window_s=1.0, confirm_cooldown_s=1.0)
            self.addCleanup(line.close)
            relay.prompt(BOT)
            relay.wait_text("Representative.", timeout=6)
            self.assertTrue(session._strict_confirmation())
            attempts = (("final", GREETING), ("partial", "Hello, how can I"), ("final", "My name is Alex. How can I help?"),
                        ("final", "Hello? Are you there?"), ("partial", "Hi, this is Sam"), ("final", GREETING),
                        ("final", "My name is Jo. How can I help?"), ("final", "Hello? Is anyone there?"),
                        ("final", "My name is Kim. How can I help?"))
            for kind, text in attempts:  # nine attempts, each past the patched spacing
                if kind == "final":
                    relay.prompt(text)
                else:
                    partial(relay, text)
                time.sleep(2.2)
            time.sleep(1.0)
        sent = questions(relay)
        self.assertLessEqual(len(sent), 4)
        self.assertEqual(len(sent), session.questions_sent)
        self.assertEqual(session.questions_sent, 4)
        self.assertEqual(session.new_party_questions, 4)  # the round-27 name reads the same global counter
        events = line.store.events(req["call_id"])
        self.assertEqual([e["data"]["count"] for e in events if e["kind"] == "live_person_question_sent"], [1, 2, 3, 4])
        self.assertIn("budget", skips(line, req))
        self.assertEqual(owner_calls(line), [])

    @mock.patch.object(CallSession, "_question_reply", _round43c_reply_rule_off)
    def test_N31_questions_on_a_strict_call_are_twenty_seconds_apart(self):
        line, req, session, relay = make(quiet=0.0)
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        relay.wait_text("live person?")
        relay.prompt("Hello?")  # an unclear reply: the re-ask route
        relay.prompt("My name is Alex. How can I help you?")
        time.sleep(1.0)
        self.assertEqual(len(questions(relay)), 1)
        self.assertEqual(session.questions_sent, 1)
        self.assertIn("spacing", skips(line, req))

    def test_N31_a_question_that_is_never_sent_spends_nothing(self):
        line, req, session, relay = make()
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        time.sleep(0.3)
        relay.prompt("Hello?")  # supersedes the first before it could go
        wait_for(lambda: questions(relay), timeout=6)
        time.sleep(0.5)
        self.assertEqual(session.questions_sent, 1)


class InboundAndLateTests(unittest.TestCase):
    def test_N30_no_stall_question_after_a_person_is_confirmed(self):
        line, req, session, relay = make(quiet=0.0)
        self.addCleanup(line.close)
        relay.prompt(GREETING)
        relay.wait_text("live person?")
        relay.prompt("Yes.")
        line.wait_state(req["call_id"], "ringing_owner", timeout=8)
        before = len(questions(relay))
        partial(relay, "Hello? Are you still")
        time.sleep(CallSession.STALL_AFTER_S + 0.8)
        self.assertNotIn("partials_stalled", kinds(line, req))
        self.assertEqual(len(questions(relay)), before)

    def test_N30_the_stall_path_has_no_route_to_a_person(self):
        import inspect
        src = inspect.getsource(CallSession._stall_watch)
        for name in ("_on_human", "_maybe_confirm", "_bot_call_human", "_assistant_reply", "_on_detection", "_detect",
                     "_transcript", "SpokenUtterance", "yes_confirmed", "human_confirmed =", "_info_only_speech",
                     "start_turn", "offer("):
            self.assertNotIn(name, src)


if __name__ == "__main__":
    unittest.main()
